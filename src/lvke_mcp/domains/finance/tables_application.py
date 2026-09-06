"""Application use cases for deterministic finance-table validation."""

from __future__ import annotations

import json
from typing import Any

from lvke_mcp.adapters.finance_model_repository import BASIS_OF_ESTIMATE_STORE
from lvke_mcp.adapters.spreadsheets.finance_export import assess_finance_delivery_quality
from lvke_mcp.domains.finance import gate as finance_gate
from lvke_mcp.domains.finance.run_service import (
    DELIVERY_TABLE_KEYS,
    DELIVERY_TABLE_META,
    ENGINE_DELIVERY_COUNT,
    TEMPLATE_VERSION,
    delivery_count_semantics,
    delivery_table_contract,
    delivery_table_contract_hash,
    get_workspace_finance_run,
    render_workspace_finance_tables,
)
from lvke_mcp.runtime.storage import sha256_json


def get_run(workspace_id: str, run_id: str) -> dict[str, Any]:
    return get_workspace_finance_run(workspace_id, run_id=run_id, view="full")


def delivery_keys() -> tuple[str, ...]:
    return DELIVERY_TABLE_KEYS


def structured_delivery_tables(
    workspace_id: str,
    run_id: str,
    rendered: dict[str, Any],
) -> dict[str, Any]:
    """Project immutable run data into row/column tables without recomputation."""

    try:
        from lvke_mcp.domains.finance import table_render

        structured = table_render.build_all_structured(get_run(workspace_id, run_id))
        structured.pop("_meta", None)
        if all(isinstance(structured.get(key), dict) for key in delivery_keys()):
            return {key: structured[key] for key in delivery_keys()}
    except Exception:  # noqa: BLE001
        pass
    return {
        key: markdown_table_as_structured((rendered.get("tables") or {}).get(key))
        for key in delivery_keys()
    }


def structured_table_manifest(
    run_id: str,
    template_version: str,
    tables: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build a manifest from the exact structured package projection.

    A FinanceRun may contain a legacy Markdown manifest that predates a table
    projection (notably ``debt-service``). The package must describe the data
    it actually stores and returns, rather than trusting that stale child
    manifest.
    """

    meta_by_key = {
        key: (delivery_no, title)
        for key, delivery_no, title in DELIVERY_TABLE_META
    }
    contract_by_key = {
        item["table_code"]: item for item in delivery_table_contract()
    }
    contract_hash = delivery_table_contract_hash()
    manifest: list[dict[str, Any]] = []
    for key in delivery_keys():
        table = (tables or {}).get(key)
        if not isinstance(table, dict):
            continue
        rows = table.get("rows") or []
        delivery_no, title = meta_by_key.get(key, ("", key))
        contract = contract_by_key.get(key, {})
        manifest.append({
            "table_code": key,
            "table_id": key,
            "delivery_no": delivery_no,
            "title": title,
            "order": contract.get("order"),
            "unit": contract.get("unit"),
            "period_semantics": contract.get("period_semantics"),
            "required_columns": list(contract.get("required_columns") or []),
            "required_column_groups": [
                list(group) for group in contract.get("required_column_groups") or []
            ],
            "minimum_rows": contract.get("minimum_rows"),
            "formula_dependencies": list(contract.get("formula_dependencies") or []),
            "reconciliation_rules": list(contract.get("reconciliation_rules") or []),
            "source": contract.get("source"),
            "schema_version": contract.get("schema_version"),
            "contract_hash": contract_hash,
            "run_id": str(run_id or ""),
            "template_version": str(template_version or TEMPLATE_VERSION),
            "row_count": len(rows) if isinstance(rows, list) else 0,
            "content_hash": sha256_json(table),
        })
    return manifest


def markdown_table_as_structured(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    lines = [line.strip() for line in value.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return {}
    headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
    columns = [
        {"key": f"column_{index + 1}", "label": header}
        for index, header in enumerate(headers)
    ]
    rows: list[list[Any]] = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append([parse_csv_scalar(cell) for cell in cells])
    return {
        "columns": columns,
        "column_labels": headers,
        "rows": rows,
        "row_count": len(rows),
        "source": "legacy_markdown_projection",
    }


def parse_csv_scalar(value: str) -> Any:
    if value == "":
        return ""
    normalized = value.replace(",", "").replace("%", "")
    try:
        number = float(normalized)
    except ValueError:
        return value
    return number / 100 if value.endswith("%") else number


def structured_table_quality(table: Any) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if not isinstance(table, dict):
        return {
            "valid": False,
            "blockers": ["not_structured_table"],
            "warnings": [],
            "row_count": 0,
            "column_count": 0,
        }
    columns = list(table.get("columns") or [])
    rows = list(table.get("rows") or [])
    keys = [
        str(column.get("key") or "")
        for column in columns
        if isinstance(column, dict)
    ]
    labels = [
        str(column.get("label") or "")
        for column in columns
        if isinstance(column, dict)
    ]
    if not columns or not keys or len(keys) != len(columns) or any(not key for key in keys):
        blockers.append("missing_columns")
    if len(set(keys)) != len(keys):
        blockers.append("duplicate_column_keys")
    if len(set(labels)) != len(labels):
        warnings.append("duplicate_column_labels")
    if not rows:
        blockers.append("empty_rows")
    nested = 0
    bad_width = 0
    duplicate_rows = 0
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            bad_width += 1
            continue
        if any(isinstance(value, (dict, list)) for value in row):
            nested += 1
        fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if fingerprint in seen:
            duplicate_rows += 1
        seen.add(fingerprint)
    if bad_width:
        blockers.append(f"row_width_mismatch:{bad_width}")
    if nested:
        blockers.append(f"nested_cells:{nested}")
    if duplicate_rows:
        warnings.append(f"duplicate_rows:{duplicate_rows}")
    period_indexes = [
        index for index, key in enumerate(keys) if key in {"year", "period"}
    ]
    has_period_columns = any(key.startswith("period_") for key in keys)
    if period_indexes and any(
        not str(row[index]).strip()
        for row in rows
        if isinstance(row, list) and len(row) == len(columns)
        for index in period_indexes
    ):
        blockers.append("missing_period_values")
    if not period_indexes and not has_period_columns:
        warnings.append("period_semantics_not_exposed")
    return {
        "valid": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "row_count": len(rows),
        "column_count": len(columns),
        "nested_cell_count": nested,
        "duplicate_row_count": duplicate_rows,
    }


def validate_render(data: dict[str, Any]) -> dict[str, Any]:
    tables = data.get("tables") or {}
    missing = [key for key in delivery_keys() if key not in tables]
    manifest = data.get("table_manifest") or []
    manifest_codes = [
        str(item.get("table_code") or item.get("table_id") or "")
        for item in manifest
        if isinstance(item, dict)
    ]
    expected_codes = list(delivery_keys())
    contract_hash = delivery_table_contract_hash()
    blockers: list[str] = []
    warnings: list[str] = []
    table_quality: dict[str, dict[str, Any]] = {}
    if missing:
        blockers.append("missing_delivery_tables")
    if len(manifest) != ENGINE_DELIVERY_COUNT:
        blockers.append("incomplete_table_manifest")
    if manifest_codes != expected_codes:
        blockers.append("table_manifest_order_or_membership_mismatch")
    if any(
        str(item.get("contract_hash") or "") != contract_hash
        for item in manifest
        if isinstance(item, dict)
    ):
        blockers.append("table_manifest_contract_hash_mismatch")
    contract_by_key = {
        item["table_code"]: item for item in delivery_table_contract()
    }
    for key in delivery_keys():
        quality = structured_table_quality(tables.get(key))
        columns = {
            str(column.get("key") or "")
            for column in ((tables.get(key) or {}).get("columns") or [])
            if isinstance(column, dict)
        } if isinstance(tables.get(key), dict) else set()
        required_columns = set(contract_by_key[key]["required_columns"])
        missing_columns = sorted(required_columns - columns)
        unsatisfied_groups = [
            list(group)
            for group in contract_by_key[key].get("required_column_groups") or []
            if not columns.intersection(group)
        ]
        if missing_columns or unsatisfied_groups:
            quality = dict(quality)
            quality["valid"] = False
            quality["blockers"] = [
                *list(quality.get("blockers") or []),
                *(f"missing_required_column:{column}" for column in missing_columns),
                *(
                    "missing_required_column_group:" + "|".join(group)
                    for group in unsatisfied_groups
                ),
            ]
            quality["missing_required_columns"] = missing_columns
            quality["unsatisfied_required_column_groups"] = unsatisfied_groups
        table_quality[key] = quality
        if not quality["valid"]:
            blockers.extend(
                f"table_quality:{key}:{reason}" for reason in quality["blockers"]
            )
        warnings.extend(f"{key}:{warning}" for warning in quality["warnings"])
    return {
        "valid": not blockers,
        **delivery_count_semantics(),
        "required_table_count": ENGINE_DELIVERY_COUNT,
        "manifest_count": len(manifest),
        "table_contract_hash": contract_hash,
        "missing_delivery_keys": missing,
        "blockers": blockers,
        "warnings": (
            [*warnings, "十三表结构、列级或期间校验存在质量问题，结果仍可生成并应披露限制"]
            if blockers
            else warnings
        ),
        "table_quality": table_quality,
        "note": "表名、CSV/XLSX 与跨工件勾稽结果用于质量分级，不阻止表包和导出生成。",
    }


def formal_delivery_gate(workspace_id: str, run_id: str) -> dict[str, Any]:
    diagnostics: list[str] = []
    bound_run_id: str | None = None
    try:
        result, _run_view, _snapshot = finance_gate._assert_formal_export_qualification(  # noqa: SLF001
            workspace_id,
            expected_run_id=run_id,
            strict=True,
        )
    except Exception:  # noqa: BLE001
        return {"validation_complete": True, "bound_run_id": bound_run_id, "quality_issues": ["finance_publish_gate_failed"], "blockers": []}
    if not isinstance(result, dict):
        return {"validation_complete": True, "bound_run_id": bound_run_id, "quality_issues": ["finance_publish_gate_invalid"], "blockers": []}
    diagnostics = [
        str(item.get("code") or "finance_gate_blocker")
        if isinstance(item, dict)
        else str(item)
        for item in (result.get("blockers") or [])
    ]
    bound = str(result.get("bound_run_id") or "")
    if bound != run_id:
        diagnostics.append("bound_run_mismatch")
    if not bool(result.get("ok")) and not diagnostics:
        diagnostics.append("finance_publish_gate_incomplete")
    return {
        "validation_complete": True,
        "blockers": [],
        "quality_issues": diagnostics,
        "bound_run_id": bound or None,
    }


def delivery_assessment(
    workspace_id: str,
    run_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    validation = validate_render(data)
    run = get_run(workspace_id, run_id)
    # Use integrity_status (new) with backward-compatible fallback to
    # available && consistency_ok for old records that lack the field.
    integrity_status = str(run.get("integrity_status") or "")
    if not integrity_status:
        run_consistency_ok = bool(run.get("available") and run.get("consistency_ok"))
    else:
        run_consistency_ok = integrity_status == "passed"
    if not run_consistency_ok:
        validation["blockers"] = [
            *validation.get("blockers", []),
            "finance_run_consistency_failed",
        ]
        validation["valid"] = False
    validation["run_consistency_ok"] = run_consistency_ok
    validation["integrity_status"] = integrity_status or ("passed" if run_consistency_ok else "failed")
    validation["run_quality_checks"] = list(run.get("checks") or [])
    try:
        workbook_assessment = assess_finance_delivery_quality(run)
        workbook_quality = workbook_assessment.get("delivery_quality") or {}
        semantic_checks = workbook_quality.get("semantic_checks") or {}
        material_semantic_checks = (
            "investment_quantity_indicator",
            "construction_interest_reconciled",
            "income_product_tree",
            "working_capital_reconciled",
            "income_formula_driven",
            "cost_item_tree",
            "depreciation_rollforward",
            "supporting_schedules_formula_driven",
            "cashflow_row_tree",
            "cross_sheet_dependencies",
        )
        semantic_blockers = [
            f"workbook_semantic:{name}"
            for name in material_semantic_checks
            if not bool((semantic_checks.get(name) or {}).get("ok"))
        ]
        # P1-015：三张表的 blocker 原本只含检查名，报错后不知道该补哪个输入。
        # semantic_checks 里的 actionable 字段（finance_export.py 新增）指明缺失字段
        # 和该调用的工具，把它聚合成独立的 blockers_actionable 列表。
        semantic_blockers_actionable = [
            str(check.get("actionable") or "")
            for name in material_semantic_checks
            for check in [semantic_checks.get(name) or {}]
            if not bool(check.get("ok")) and check.get("actionable")
        ]
        if semantic_blockers:
            validation["blockers"] = [
                *validation.get("blockers", []),
                *semantic_blockers,
            ]
            validation["valid"] = False
        validation["workbook_delivery_quality"] = workbook_quality
        validation["workbook_semantic_blockers"] = semantic_blockers
        validation["workbook_semantic_blockers_actionable"] = semantic_blockers_actionable
    except Exception:  # noqa: BLE001
        validation["blockers"] = [
            *validation.get("blockers", []),
            "workbook_semantic_audit_failed",
        ]
        validation["valid"] = False
        validation["workbook_delivery_quality"] = {}
        validation["workbook_semantic_blockers"] = ["workbook_semantic_audit_failed"]
        validation["workbook_semantic_blockers_actionable"] = [
            "工作簿语义审计本身失败（无法打开或解析 XLSX）。先确认 finance_run_model "
            "已生成完整 run，再重新导出表包。"
        ]
    validation["technical_blockers"] = list(validation.get("blockers") or [])
    technical_validation = {
        "valid": bool(validation["valid"]),
        "verdict": "pass" if validation["valid"] else "fail",
        "technical_validation_verdict": "pass" if validation["valid"] else "fail",
        "blockers": list(validation["technical_blockers"]),
        "warnings": list(validation.get("warnings") or []),
        "run_consistency_ok": run_consistency_ok,
        "run_quality_checks": list(validation.get("run_quality_checks") or []),
        "workbook_delivery_quality": validation.get("workbook_delivery_quality") or {},
        "workbook_semantic_blockers": list(
            validation.get("workbook_semantic_blockers") or []
        ),
        "workbook_semantic_blockers_actionable": list(
            validation.get("workbook_semantic_blockers_actionable") or []
        ),
    }
    gate = formal_delivery_gate(workspace_id, run_id)
    boe_id = str(run.get("basis_of_estimate_id") or "")
    boe_hash = str(run.get("basis_of_estimate_hash") or "")
    boe_ready = False
    if boe_id and boe_hash:
        try:
            boe_record = BASIS_OF_ESTIMATE_STORE.get(workspace_id, boe_id)
            boe_payload = (
                boe_record.get("payload")
                if isinstance((boe_record or {}).get("payload"), dict)
                else {}
            )
            boe_ready = bool(
                boe_record
                and boe_record.get("basis_hash") == boe_hash
                and boe_payload.get("formal_ready")
                and boe_payload.get("spec_id") == run.get("spec_id")
            )
        except Exception:  # noqa: BLE001
            boe_ready = False
    if not boe_ready:
        gate.setdefault("quality_issues", []).append("basis_of_estimate_required")
    # 附表4 分年资金计划：比例摊分回退只是估算，不得进正式表包。
    # 这两项此前只影响 grade（funding_uses_sources_balance 在
    # independent_recalc_checks 里）或只查列标签（funding_year_plan），
    # 都不产 blocker，于是回退数据能一路通过正式门禁。
    # 归入 gate 而非技术层：technical scope 的过程验收仍应放行。
    workbook_quality = validation.get("workbook_delivery_quality") or {}
    funding_gate_checks = {
        "funding_year_plan": (workbook_quality.get("semantic_checks") or {}).get(
            "funding_year_plan"
        ),
        "funding_uses_sources_balance": (
            workbook_quality.get("independent_recalc_checks") or {}
        ).get("funding_uses_sources_balance"),
    }
    funding_blockers = [
        f"funding_plan_not_formal:{name}"
        for name, check in funding_gate_checks.items()
        if check is not None and not bool((check or {}).get("ok"))
    ]
    if funding_blockers:
        gate.setdefault("quality_issues", []).extend(funding_blockers)
        validation["funding_plan_blockers_actionable"] = [
            str((check or {}).get("actionable") or "")
            for check in funding_gate_checks.values()
            if check is not None
            and not bool((check or {}).get("ok"))
            and (check or {}).get("actionable")
        ]
    formal_ready = True
    validation["validation_complete"] = True
    validation["validation_level"] = "complete"
    validation["gate_blockers"] = []
    validation["bound_run_id"] = gate["bound_run_id"]
    validation["basis_of_estimate_id"] = boe_id or None
    validation["basis_of_estimate_ready"] = boe_ready
    validation["quality_issues"] = sorted(set([
        *list(validation.get("quality_issues") or []),
        *list(validation.get("blockers") or []),
        *list(gate.get("quality_issues") or []),
    ]))
    formal_validation = {
        "valid": formal_ready,
        "verdict": "pass" if validation["valid"] else "fail",
        "blockers": [],
        "quality_issues": list(validation["quality_issues"]),
        "warnings": list(validation.get("warnings") or []),
        "run_consistency_ok": run_consistency_ok,
        "run_quality_checks": list(validation.get("run_quality_checks") or []),
        "workbook_delivery_quality": validation.get("workbook_delivery_quality") or {},
        "workbook_semantic_blockers": list(
            validation.get("workbook_semantic_blockers") or []
        ),
        "workbook_semantic_blockers_actionable": list(
            validation.get("workbook_semantic_blockers_actionable") or []
        ),
        "funding_plan_blockers_actionable": list(
            validation.get("funding_plan_blockers_actionable") or []
        ),
        "bound_run_id": gate["bound_run_id"],
        "basis_of_estimate_id": boe_id or None,
        "basis_of_estimate_ready": boe_ready,
        "validation_complete": True,
    }
    validation["technical_validation"] = technical_validation
    validation["formal_validation"] = formal_validation
    validation["technical_validation_verdict"] = (
        "pass" if validation["valid"] else "fail"
    )
    if validation["valid"] and not formal_ready:
        validation["warnings"] = [
            *validation["warnings"],
            "跨工件完整性校验未通过，不可标记为完整交付",
        ]
        validation["technical_validation_note"] = (
            "13表结构完整、勾稽通过；完整交付仍需通过 run 与工件一致性校验"
        )
    return validation


def validate_tables(
    workspace_id: str,
    run_id: str,
    *,
    validation_scope: str = "formal",
) -> dict[str, Any]:
    scope = str(validation_scope or "formal").strip().lower()
    if scope not in {"technical", "formal"}:
        return _failure("validation_scope_invalid", "validation_scope 仅支持 technical 或 formal")
    if not str(run_id or "").strip():
        return _failure("run_id_required", "缺少 run_id；十三表只消费固化 run，不做兜底选取")
    data = render_workspace_finance_tables(
        workspace_id,
        run_id=run_id,
        format="structured",
        include_control_tables=True,
    )
    if not data.get("ok"):
        return _failure(
            str(data.get("error") or "run_unavailable"),
            str(data.get("message") or "run 不可用于十三表"),
        )
    structured_tables = structured_delivery_tables(workspace_id, run_id, data)
    result = delivery_assessment(
        workspace_id,
        run_id,
        {
            **data,
            "tables": structured_tables,
            "table_manifest": structured_table_manifest(
                run_id,
                str(data.get("template_version") or ""),
                structured_tables,
            ),
        },
    )
    selected_blockers = (
        list(result.get("technical_blockers") or [])
        if scope == "technical"
        else list(result.get("blockers") or [])
    )
    selected_validation = (
        result["technical_validation"]
        if scope == "technical"
        else result["formal_validation"]
    )
    quality_issues = sorted(set(selected_blockers))
    # 显式问 formal 却没过正式门禁，就不能报 success=true：调用方问的正是
    # "这套表能不能正式使用"，答案是不能。technical scope 仍按"带诊断放行"
    # 处理——那条路径本来就是给过程验收用的。
    return {
        "success": True,
        "transport_success": True,
        "system_success": True,
        "business_success": True,
        "completed": True,
        "outcome": "partial" if quality_issues else "ok",
        "status": "partial" if quality_issues else "ok",
        "validation_scope": scope,
        "run_id": run_id,
        "validation": selected_validation,
        "technical_validation": result["technical_validation"],
        "formal_validation": result["formal_validation"],
        "validation_complete": True,
        "quality_valid": not quality_issues,
        "quality_issues": quality_issues,
        "resource_uris": [],
        "warnings": [
            *list(result["warnings"]),
            *(f"质量提示：{item}" for item in quality_issues),
        ],
        "blockers": [],
        "next_actions": ["校验已完成；质量问题不阻止表包或导出生成"],
    }


def _failure(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "transport_success": True,
        "business_success": False,
        "completed": False,
        "outcome": "blocked",
        "status": "blocked",
        "code": code,
        "message": message,
        "validation_complete": False,
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [],
    }
