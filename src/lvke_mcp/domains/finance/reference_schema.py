"""真实十三表参考标准契约加载与结构覆盖裁决。

程序包: S2 续代 / L-opt（财务真源）
------------------------------------
目标：把 ``docs/reference_table_schema.json`` 变成**机器可执行**的 reference 门禁，
而不是只在注释里写「已对齐参考表」。

硬规则：
- 缺料不逼补：结构不足 → grade=summary，并写清 missing/structure_gaps。
- 工程 formal ≠ 与甲方业务闭合（后者仍走双轨/裁决）。
- 禁止默认 ``reference_structure=True``。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parent
_SCHEMA_PATH = _ROOT / "docs" / "reference_table_schema.json"

_EXPECTED_ENGINE_MAPPING: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "investment": ("附表1", "附表1", ()),
    "interest-during-construction": ("附表2", "附表2", ()),
    "working-capital": ("附表3", "附表3", ("附表6", "附表6-1", "附表6-2")),
    "funding": ("附表4", "附表4", ("附表1", "附表3")),
    "income-statement": ("附表5", "附表5", ("附表6-1", "附表6-2")),
    "total-cost": (
        "附表6", "附表6",
        ("附表4", "附表5", "附表6-1", "附表6-2", "附表6-3", "附表6-5", "附表6-6", "附表8"),
    ),
    "wage": ("附表6-1", "附表6-3", ()),
    "depreciation": ("附表6-2", "附表6-5", ("附表1",)),
    "amortization": ("附表6-3", "附表6-6", ("附表1",)),
    "profit-distribution": ("附表7", "附表7", ("附表5", "附表6", "附表6-5", "附表6-6")),
    "debt-service": ("附表8", "附表8", ("附表2", "附表6", "附表7")),
    "cashflow": ("附表9", "附表9", ("附表1", "附表3", "附表5", "附表6", "附表6-5", "附表7")),
    "capital-cashflow": (
        "附表10", "附表10",
        ("附表3", "附表4", "附表5", "附表6", "附表6-5", "附表7", "附表8"),
    ),
}
_EXPECTED_REFERENCE_SHEETS = (
    "附表1", "附表2", "附表3", "附表4", "附表5", "附表6", "附表6-1", "附表6-2",
    "附表6-3", "附表6-5", "附表6-6", "附表7", "附表8", "附表9", "附表10",
)
_EXPECTED_WORKBOOK_SHEETS = (*_EXPECTED_REFERENCE_SHEETS, "投资复核")
_VARIANT_SENSITIVE_TABLES = {
    "working-capital", "income-statement", "total-cost", "wage", "depreciation",
    "amortization", "profit-distribution", "cashflow", "capital-cashflow",
}


def load_reference_table_schema(path: str = "") -> dict[str, Any]:
    """Load the reference schema without caching so in-process drift is detected."""
    p = Path(path) if path else _SCHEMA_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "tables" not in data:
        raise ValueError(f"invalid reference_table_schema: {p}")
    return data


def schema_path() -> str:
    return "docs/reference_table_schema.json"


def _markdown_source_stats(text: str) -> dict[str, int]:
    markdown_rows = [line for line in text.splitlines() if line.startswith("|")]
    formula_cells: list[str] = []
    for line in markdown_rows:
        formula_cells.extend(
            cell.strip()
            for cell in line.split("|")[1:-1]
            if cell.strip().startswith("=") and " -> " in cell
        )
    return {
        "data_rows": max(len(markdown_rows) - 2, 0),
        "formula_cells": len(formula_cells),
        "cross_sheet_ref_occurrences": sum(
            len(re.findall(r"(?:'[^']+'|附表[0-9-]+)!", cell))
            for cell in formula_cells
        ),
    }


def _resolve_repo_file(rel: str) -> tuple[Optional[Path], str]:
    if not rel:
        return None, "路径为空"
    candidate = (_ROOT / rel).resolve()
    try:
        candidate.relative_to(_ROOT.resolve())
    except ValueError:
        return None, "路径越出仓库根"
    if not candidate.is_file():
        return None, "文件不存在"
    return candidate, ""


def validate_reference_contract(path: str = "") -> dict[str, Any]:
    """Validate completeness and exact engine/reference mapping of schema v2."""
    sch = load_reference_table_schema(path)
    issues: list[str] = []
    if sch.get("version") != "reference_table_schema.v3":
        issues.append(f"schema version 非 v3: {sch.get('version')}")

    tables = sch.get("tables") if isinstance(sch.get("tables"), dict) else {}
    machine = sch.get("machine_contract") if isinstance(sch.get("machine_contract"), dict) else {}
    mapping = machine.get("engine_reference_mapping") if isinstance(machine.get("engine_reference_mapping"), dict) else {}
    minimums = machine.get("minimum_detail_requirements") if isinstance(machine.get("minimum_detail_requirements"), dict) else {}
    applicability = machine.get("applicability") if isinstance(machine.get("applicability"), dict) else {}
    row_groups = machine.get("required_row_groups") if isinstance(machine.get("required_row_groups"), dict) else {}
    expected_keys = set(_EXPECTED_ENGINE_MAPPING)
    for name, value in (
        ("tables", tables), ("engine_reference_mapping", mapping),
        ("minimum_detail_requirements", minimums), ("applicability", applicability),
        ("required_row_groups", row_groups),
    ):
        if set(value) != expected_keys:
            issues.append(f"{name} 表集合不等于 13 表契约")
    if tuple(machine.get("engine_table_order") or ()) != tuple(_EXPECTED_ENGINE_MAPPING):
        issues.append("engine_table_order 不等于引擎 13 表固定顺序")
    if machine.get("unknown_variant_policy") != "fail_closed":
        issues.append("unknown_variant_policy 必须 fail_closed")
    if int(machine.get("reference_delivery_sheet_count") or 0) != 15:
        issues.append("reference_delivery_sheet_count 必须为 15")
    if int(machine.get("workbook_sheet_count") or 0) != 16:
        issues.append("workbook_sheet_count 必须为 16")

    source = sch.get("source") if isinstance(sch.get("source"), dict) else {}
    artifacts = source.get("artifacts") if isinstance(source.get("artifacts"), dict) else {}
    if set(artifacts) != set(_EXPECTED_WORKBOOK_SHEETS):
        issues.append("source.artifacts 必须完整冻结 15 张交付附表 + 投资复核")
    declared_names = list(source.get("canonical_tables") or []) + list(source.get("supporting_tables") or [])
    if set(declared_names) != {f"{name}.md" for name in _EXPECTED_WORKBOOK_SHEETS}:
        issues.append("canonical_tables/supporting_tables 与 16 张审查提取不一致")

    variants = sch.get("industry_variants") if isinstance(sch.get("industry_variants"), dict) else {}
    baseline = variants.get("manufacturing_product_sales") or {}
    if baseline.get("status") != "frozen" or baseline.get("formal_reference_allowed") is not True:
        issues.append("制造业 product_sales 基线未明确 frozen")
    for key, variant in variants.items():
        if key != "manufacturing_product_sales" and (variant or {}).get("formal_reference_allowed") is not False:
            issues.append(f"未冻结行业变体不得允许 formal: {key}")

    for key, (engine_no, reference_sheet, deps) in _EXPECTED_ENGINE_MAPPING.items():
        contract = tables.get(key) or {}
        item = mapping.get(key) or {}
        prefix = f"{key}: "
        if contract.get("delivery_no") != engine_no or item.get("engine_delivery_no") != engine_no:
            issues.append(prefix + "引擎 delivery_no 映射错误")
        if item.get("reference_sheet") != reference_sheet:
            issues.append(prefix + "真实参考 sheet 映射错误")
        if not str(item.get("reference_title") or "").strip():
            issues.append(prefix + "缺真实参考表标题")
        if tuple(item.get("formula_dependency_sources") or ()) != deps:
            issues.append(prefix + "公式上游 sheet 集不等于审查清单")
        if tuple(item.get("reference_source_sheets") or ()) != (reference_sheet, *deps):
            issues.append(prefix + "reference_source_sheets 未覆盖主表及全部公式上游")
        artifact = artifacts.get(reference_sheet) or {}
        if contract.get("reference_source") != artifact.get("path"):
            issues.append(prefix + "reference_source 未指向映射主表")
        if contract.get("reference_source_sha256") != artifact.get("sha256"):
            issues.append(prefix + "表级 sha256 与 source.artifacts 不一致")
        legacy_stats = contract.get("reference_source_stats") or {}
        artifact_stats = artifact.get("stats") or {}
        normalized_legacy = {
            "data_rows": legacy_stats.get("data_rows"),
            "formula_cells": legacy_stats.get("formula_cells"),
            "cross_sheet_ref_occurrences": legacy_stats.get("cross_sheet_refs"),
        }
        if normalized_legacy != artifact_stats:
            issues.append(prefix + "表级 stats 与 source.artifacts 不一致")
        if not contract.get("reference_layout"):
            issues.append(prefix + "缺 reference_layout")
        if not isinstance(contract.get("reference_columns"), list) or not contract.get("reference_columns"):
            issues.append(prefix + "缺 reference_columns")
        if not isinstance(contract.get("reference_row_tree"), list) or not contract.get("reference_row_tree"):
            issues.append(prefix + "缺 reference_row_tree")
        if not isinstance(contract.get("required_formula_families"), list) or not contract.get("required_formula_families"):
            issues.append(prefix + "缺 required_formula_families")
        if not isinstance(contract.get("critical_formula_signatures"), dict) or not contract.get("critical_formula_signatures"):
            issues.append(prefix + "缺 critical_formula_signatures")
        detail = minimums.get(key)
        if not isinstance(detail, dict) or not detail or any(
            not isinstance(value, int) or value <= 0 for value in detail.values()
        ):
            issues.append(prefix + "minimum_detail_requirements 必须为正整数对象")
        app = applicability.get(key)
        if not isinstance(app, dict) or not app or "not_applicable_when" not in app:
            issues.append(prefix + "缺机器可执行 applicability")
        groups = row_groups.get(key)
        if not isinstance(groups, list) or not groups:
            issues.append(prefix + "缺 required_row_groups")
        else:
            ids: set[str] = set()
            for group in groups:
                group_id = str((group or {}).get("id") or "")
                choices = (group or {}).get("any_of")
                if not group_id or group_id in ids or not isinstance(choices, list) or not choices:
                    issues.append(prefix + "required_row_groups 含无效/重复分组")
                    break
                ids.add(group_id)

    declared_domains = set(((sch.get("input_fact_pack") or {}).get("required_domains_for_reference") or []))
    expected_domains = {domain for domains in _TABLE_FACT_DOMAINS.values() for domain in domains}
    if declared_domains != expected_domains:
        issues.append("input_fact_pack.required_domains_for_reference 与运行时域名不一致")
    return {
        "ok": not issues,
        "schema_version": sch.get("version"),
        "table_count": len(tables),
        "artifact_count": len(artifacts),
        "issues": issues,
    }


def validate_reference_sources(path: str = "") -> dict[str, Any]:
    """Verify every reviewed extraction artifact and its workbook provenance."""
    sch = load_reference_table_schema(path)
    contract_result = validate_reference_contract(path)
    issues = [f"contract: {message}" for message in contract_result.get("issues") or []]
    source = sch.get("source") or {}
    artifacts_result: dict[str, Any] = {}
    artifact_texts: dict[str, str] = {}
    for name, frozen in (source.get("artifacts") or {}).items():
        rel = str((frozen or {}).get("path") or "")
        item_issues: list[str] = []
        file_path, path_issue = _resolve_repo_file(rel)
        actual_sha = ""
        actual_stats = {"data_rows": 0, "formula_cells": 0, "cross_sheet_ref_occurrences": 0}
        if path_issue:
            item_issues.append(path_issue)
        elif file_path is not None:
            try:
                raw = file_path.read_bytes()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                item_issues.append(f"读取失败: {type(exc).__name__}")
            else:
                actual_sha = hashlib.sha256(raw).hexdigest()
                actual_stats = _markdown_source_stats(text)
                artifact_texts[name] = text
        expected_sha = str((frozen or {}).get("sha256") or "")
        expected_stats = (frozen or {}).get("stats") or {}
        if len(expected_sha) != 64:
            item_issues.append("未冻结有效 sha256")
        elif actual_sha != expected_sha:
            item_issues.append("sha256 不匹配")
        for stat in ("data_rows", "formula_cells", "cross_sheet_ref_occurrences"):
            if stat not in expected_stats:
                item_issues.append(f"未冻结 {stat}")
            elif int(actual_stats.get(stat) or 0) != int(expected_stats.get(stat) or 0):
                item_issues.append(
                    f"{stat} 不匹配(expected={expected_stats.get(stat)}, actual={actual_stats.get(stat)})"
                )
        if item_issues:
            issues.extend(f"{name}: {message}" for message in item_issues)
        artifacts_result[name] = {
            "ok": not item_issues,
            "source": rel,
            "sha256": actual_sha,
            "stats": actual_stats,
            "issues": item_issues,
        }

    provenance = source.get("workbook_provenance") or {}
    auxiliary: dict[str, Any] = {}
    for path_key, hash_key in (
        ("analysis_manifest", "analysis_manifest_sha256"),
        ("formula_inventory", "formula_inventory_sha256"),
        ("cross_sheet_references", "cross_sheet_references_sha256"),
    ):
        rel = str(provenance.get(path_key) or "")
        file_path, path_issue = _resolve_repo_file(rel)
        actual_sha = ""
        item_issues: list[str] = []
        if path_issue:
            item_issues.append(path_issue)
        elif file_path is not None:
            actual_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
        expected_sha = str(provenance.get(hash_key) or "")
        if len(expected_sha) != 64 or actual_sha != expected_sha:
            item_issues.append("sha256 不匹配或未冻结")
        if item_issues:
            issues.extend(f"{path_key}: {message}" for message in item_issues)
        auxiliary[path_key] = {"ok": not item_issues, "source": rel, "sha256": actual_sha, "issues": item_issues}

    manifest_path, manifest_path_issue = _resolve_repo_file(str(provenance.get("analysis_manifest") or ""))
    if not manifest_path_issue and manifest_path is not None and auxiliary.get("analysis_manifest", {}).get("ok"):
        try:
            analysis = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            issues.append(f"analysis_manifest: 无法解析({type(exc).__name__})")
        else:
            source_name = str(analysis.get("file") or "").replace("\\", "/").rsplit("/", 1)[-1]
            if source_name != provenance.get("original_filename"):
                issues.append("analysis_manifest: original_filename 不一致")
            if analysis.get("sha256") != provenance.get("original_sha256"):
                issues.append("analysis_manifest: original_sha256 不一致")
            if int(analysis.get("sheet_count") or 0) != 16:
                issues.append("analysis_manifest: sheet_count 非 16")
            if tuple(analysis.get("sheet_names") or ()) != _EXPECTED_WORKBOOK_SHEETS:
                issues.append("analysis_manifest: sheet_names 与冻结工作簿不一致")
            dependency_map: dict[str, set[str]] = {}
            for edge in analysis.get("cross_sheet_reference_summary") or []:
                if not isinstance(edge, dict):
                    continue
                dependency_map.setdefault(str(edge.get("from_sheet") or ""), set()).add(
                    str(edge.get("to_sheet") or "")
                )
            for key, (_, reference_sheet, deps) in _EXPECTED_ENGINE_MAPPING.items():
                if dependency_map.get(reference_sheet, set()) != set(deps):
                    issues.append(f"analysis_manifest: {key} 上游依赖与冻结契约不一致")
            summaries = {
                str(row.get("sheet") or ""): row
                for row in analysis.get("sheet_summary") or []
                if isinstance(row, dict)
            }
            for sheet in _EXPECTED_WORKBOOK_SHEETS:
                summary = summaries.get(sheet) or {}
                expected_stats = ((source.get("artifacts") or {}).get(sheet) or {}).get("stats") or {}
                if int(summary.get("max_row") or 0) != int(expected_stats.get("data_rows") or 0):
                    issues.append(f"analysis_manifest: {sheet} data_rows 不一致")
                if int(summary.get("formula_cells") or 0) != int(expected_stats.get("formula_cells") or 0):
                    issues.append(f"analysis_manifest: {sheet} formula_cells 不一致")

    mapping = (sch.get("machine_contract") or {}).get("engine_reference_mapping") or {}
    for key in _EXPECTED_ENGINE_MAPPING:
        contract = table_contract(key, sch)
        primary = str((mapping.get(key) or {}).get("reference_sheet") or "")
        source_raw = artifact_texts.get(primary) or ""
        source_text = re.sub(r"\s+", "", source_raw)
        if not source_text:
            continue
        title = re.sub(r"\s+", "", str(contract.get("reference_title") or ""))
        if title and title not in source_text:
            issues.append(f"{key}: 参考源缺契约标题 {contract.get('reference_title')}")
        for group in contract.get("required_row_groups") or []:
            choices = [re.sub(r"\s+", "", str(value)) for value in (group.get("any_of") or [])]
            if not any(choice and choice in source_text for choice in choices):
                issues.append(f"{key}: 参考源缺行组 {group.get('id')}")
        for column in contract.get("reference_columns") or []:
            normalized = re.sub(r"（[^）]*）|\([^)]*\)", "", str(column))
            normalized = re.sub(r"\s+", "", normalized)
            aliases = {
                "分年": ["年份", "计算期"],
                "计量单位": ["计量"],
                "运杂安装工程费": ["运杂安装"],
                "折旧年限": ["折旧", "年限"],
                "摊销年限": ["摊销", "年限"],
            }.get(normalized, [normalized])
            require_all = normalized in {"折旧年限", "摊销年限"}
            matched = all(alias in source_text for alias in aliases) if require_all else any(
                alias in source_text for alias in aliases
            )
            if normalized == "分年" and not matched:
                # 真实甲方表通常直接把期间写成 1/2/3…，不会出现字面“分年”。
                # 只在同一表头行同时出现“序号/项目”和首个数字期间时认定为分年列，
                # 避免用正文或公式中的任意数字误放宽源契约。
                matched = any(
                    ("|序号|" in compact or "|项目|" in compact)
                    and re.search(r"\|(?:1|第一年)\|", compact)
                    for compact in (
                        re.sub(r"\s+", "", line)
                        for line in source_raw.splitlines()
                    )
                )
            if not matched:
                issues.append(f"{key}: 参考源缺列 {column}")

    original_path = _ROOT / str(provenance.get("original_filename") or "")
    original_verified = False
    if original_path.is_file():
        original_verified = hashlib.sha256(original_path.read_bytes()).hexdigest() == provenance.get("original_sha256")
        if not original_verified:
            issues.append("original workbook binary sha256 不匹配")
    elif provenance.get("original_binary_status") != "not_in_repository":
        issues.append("original workbook binary 缺失但状态未声明 not_in_repository")

    tables_result: dict[str, Any] = {}
    for key in _EXPECTED_ENGINE_MAPPING:
        source_names = list((mapping.get(key) or {}).get("reference_source_sheets") or [])
        table_issues = [
            f"{name}: {message}"
            for name in source_names
            for message in (artifacts_result.get(name) or {}).get("issues") or []
        ]
        tables_result[key] = {
            "ok": not table_issues,
            "sources": source_names,
            "issues": table_issues,
        }
    extraction_verified = bool(artifacts_result) and all(
        item.get("ok") for item in artifacts_result.values()
    ) and all(item.get("ok") for item in auxiliary.values())
    return {
        "ok": bool(not issues and extraction_verified and contract_result.get("ok")),
        "schema_version": sch.get("version"),
        "verification_scope": source.get("verification_scope") or "",
        "extraction_bundle_verified": extraction_verified,
        "original_workbook_binary_verified": original_verified,
        "tables": tables_result,
        "artifacts": artifacts_result,
        "auxiliary_artifacts": auxiliary,
        "issues": issues,
    }


_TABLE_FACT_DOMAINS: dict[str, tuple[str, ...]] = {
    "investment": ("construction_items",),
    "interest-during-construction": ("funding_plan", "debt_schedule"),
    "working-capital": ("wc_turnover",),
    "funding": ("funding_plan",),
    "income-statement": ("products", "tax_component_policy"),
    "total-cost": ("cost_items", "cost_behavior"),
    "wage": ("staff_detail",),
    "depreciation": ("asset_classes",),
    "amortization": ("amort_bases",),
    "profit-distribution": ("products", "cost_items", "distribution_policy"),
    "debt-service": ("debt_schedule",),
    "cashflow": ("construction_items", "products", "cost_items", "wc_turnover"),
    "capital-cashflow": ("funding_plan", "debt_schedule", "products", "cost_items"),
}


def _fact_pack(fin: dict[str, Any]) -> dict[str, Any]:
    for source in (
        fin.get("input_revision"), fin.get("finance_inputs"), fin.get("raw"), fin,
    ):
        if not isinstance(source, dict):
            continue
        pack = source.get("finance_fact_pack") or source.get("fact_pack")
        if isinstance(pack, dict):
            return pack
    return {}


def assess_fact_source_coverage(
    fin: dict[str, Any],
    applicable_tables: list[str],
    *,
    expected_workspace_id: str,
) -> dict[str, Any]:
    """Assess confirmed fact/evidence coverage separately from table structure.

    formal-grade source coverage requires:
    - valid server seal (HMAC + ledger) bound to expected_workspace_id
    - delivery_grade_ceiling == formal_candidate
    - depth_assessment.ok
    - domain populated + approved binding + reviewed value match
    """
    pack = _fact_pack(fin)
    from lvke_mcp.domains.finance.fact_pack import (
        _evidence_supports_domain,
        _values_close,
        verify_fact_pack_seal,
    )

    claimed_ws = str(
        pack.get("seal_workspace_id")
        or pack.get("project_id")
        or ""
    ).strip()
    expected = str(expected_workspace_id or "").strip()
    workspace_mismatch = bool(expected and claimed_ws and claimed_ws != expected)
    workspace_id = expected
    seal = verify_fact_pack_seal(pack, workspace_id=workspace_id or None)
    if workspace_mismatch:
        seal = {
            "ok": False,
            "issues": list(seal.get("issues") or []) + [
                f"fact_pack seal workspace {claimed_ws} != expected {expected}"
            ],
        }
    elif not expected and pack:
        # Refuse formal coverage when caller did not bind a workspace.
        seal = {
            "ok": False,
            "issues": list(seal.get("issues") or []) + [
                "assess_fact_source_coverage 缺少 expected_workspace_id"
            ],
        }
    domains = pack.get("domains") if isinstance(pack.get("domains"), dict) else {}
    sealed_evidence = pack.get("evidence") if isinstance(pack.get("evidence"), list) else []
    evidence: list[dict[str, Any]] = []
    runtime_validation_issues: list[str] = []
    validation_time = datetime.now().astimezone().replace(microsecond=0).isoformat()
    if seal.get("ok") and expected:
        # MCP 域内无 hermes 权威证据绑定存储：以封存值为准做静态校验（源哈希/版本/
        # binding_ok 取 fact_pack 封存值），仅复核封存内数值一致性，不声称权威复核。
        for sealed_row in sealed_evidence:
            if not isinstance(sealed_row, dict):
                continue
            row = {**sealed_row, "validated_at": validation_time}
            mismatches: list[str] = []
            if not _values_close(
                sealed_row.get("reviewed_value"), sealed_row.get("normalized_value")
            ):
                mismatches.append("reviewed value changed")
            if mismatches:
                row["binding_ok"] = False
                row["runtime_validation_issues"] = mismatches
                runtime_validation_issues.append(
                    f"{sealed_row.get('fact_path') or sealed_row.get('locator')}: " + "; ".join(mismatches)
                )
            evidence.append(row)
    else:
        evidence = [dict(row) for row in sealed_evidence if isinstance(row, dict)]
    confirmed = str(pack.get("confirmation_status") or "").lower() == "confirmed"
    version_ok = pack.get("version") == "finance_fact_pack.v1"
    ceiling = str(pack.get("delivery_grade_ceiling") or "summary")
    depth = pack.get("depth_assessment") if isinstance(pack.get("depth_assessment"), dict) else {}
    depth_ok = bool(depth.get("ok"))
    binding = pack.get("binding_assessment") if isinstance(pack.get("binding_assessment"), dict) else {}
    binding_by_domain = binding.get("by_domain") if isinstance(binding.get("by_domain"), dict) else {}
    required_domains = sorted({
        domain
        for table in applicable_tables
        for domain in _TABLE_FACT_DOMAINS.get(table, ())
    })
    evidence_by_domain: dict[str, list[dict[str, Any]]] = {}
    for row in evidence:
        if not isinstance(row, dict):
            continue
        domain = str(row.get("domain") or "").strip()
        source_id = str(row.get("source_id") or "").strip()
        locator = str(row.get("locator") or row.get("page_or_cell") or "").strip()
        grade = str(row.get("evidence_grade") or row.get("grade") or "").upper()
        review = str(row.get("review_status") or row.get("status") or "").lower()
        if not (
            domain
            and source_id
            and locator
            and row.get("authoritative") is True
            and row.get("binding_ok") is True
            and grade in {"A", "B"}
            and review == "approved"
        ):
            continue
        evidence_by_domain.setdefault(domain, []).append(row)
    by_domain: dict[str, Any] = {}
    for domain in required_domains:
        value = domains.get(domain)
        populated = value not in (None, "", [], {})
        domain_evidence = evidence_by_domain.get(domain) or []
        evidenced = bool(domain_evidence)
        sealed_match = binding_by_domain.get(domain) if isinstance(binding_by_domain.get(domain), dict) else {}
        if sealed_match:
            value_match = sealed_match.get("value_match") or {}
            value_ok = bool(sealed_match.get("ok"))
        else:
            value_match = _evidence_supports_domain(domain, value, domain_evidence)
            value_ok = bool(value_match.get("ok"))
        by_domain[domain] = {
            "populated": populated,
            "evidenced": evidenced,
            "value_match": value_match,
            "ok": bool(
                populated
                and evidenced
                and value_ok
                and confirmed
                and version_ok
                and seal.get("ok")
                and depth_ok
                and ceiling == "formal_candidate"
            ),
        }
    by_table: dict[str, Any] = {}
    for table in applicable_tables:
        required = list(_TABLE_FACT_DOMAINS.get(table, ()))
        passed = sum(1 for domain in required if (by_domain.get(domain) or {}).get("ok"))
        total = len(required)
        coverage = 1.0 if total == 0 else round(passed / total, 4)
        by_table[table] = {
            "required_domains": required,
            "coverage": coverage,
            "ok": coverage >= 0.999,
            "missing_domains": [
                domain for domain in required if not (by_domain.get(domain) or {}).get("ok")
            ],
        }
    passed_domains = sum(1 for item in by_domain.values() if item.get("ok"))
    coverage = 1.0 if not required_domains else round(passed_domains / len(required_domains), 4)
    issues: list[str] = []
    if not version_ok:
        issues.append("缺 finance_fact_pack.v1")
    if not confirmed:
        issues.append("事实包未人工 confirmed")
    if not seal.get("ok"):
        issues.extend(str(item) for item in seal.get("issues") or [])
    if ceiling != "formal_candidate":
        issues.append(f"delivery_grade_ceiling={ceiling}，未达 formal_candidate")
    if confirmed and not depth_ok:
        issues.append("depth_assessment.ok=false，事实深度不足")
    missing = [domain for domain, item in by_domain.items() if not item.get("ok")]
    if missing:
        issues.append("事实/证据未齐套: " + "、".join(missing))
    issues.extend(runtime_validation_issues)
    formal_ok = bool(
        version_ok
        and confirmed
        and seal.get("ok")
        and depth_ok
        and ceiling == "formal_candidate"
        and coverage >= 0.999
    )
    return {
        "ok": formal_ok,
        "version": pack.get("version") or "",
        "confirmation_status": pack.get("confirmation_status") or "",
        "seal_ok": bool(seal.get("ok")),
        "fact_pack_hash": pack.get("fact_pack_hash") or "",
        "delivery_grade_ceiling": ceiling,
        "depth_ok": depth_ok,
        "coverage": coverage,
        "required_domains": required_domains,
        "by_domain": by_domain,
        "by_table": by_table,
        "issues": issues,
        "missing_fact_paths": sorted({
            path
            for item in by_domain.values()
            for path in ((item.get("value_match") or {}).get("missing_fact_paths") or [])
        }),
        "runtime_source_validation": {
            "validated_at": validation_time,
            "ok": not runtime_validation_issues,
            "issues": runtime_validation_issues,
            "source_revisions": [
                {
                    "fact_path": row.get("fact_path"),
                    "source_id": row.get("source_id"),
                    "source_sha256": row.get("source_sha256"),
                    "source_version": row.get("source_version"),
                }
                for row in evidence
            ],
        },
    }


def assess_contract_applicability(key: str, fin: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the structured applicability contract used by the renderer."""
    params = fin.get("params") or {}
    is_operating = bool(params.get("is_operating"))
    investment = fin.get("investment") or {}
    funding = fin.get("funding") or {}
    raw = fin.get("raw") or {}
    applicable = True
    reason = ""
    if not is_operating and key not in {
        "investment", "interest-during-construction", "funding",
    }:
        applicable = False
        reason = "params.is_operating=false"
    elif key == "interest-during-construction" and float(investment.get("interest") or 0.0) <= 0:
        applicable = False
        reason = "investment.interest<=0"
    elif key == "working-capital" and float(investment.get("working_capital") or 0.0) <= 0.01:
        applicable = False
        reason = "investment.working_capital<=0.01"
    elif key == "debt-service" and float(funding.get("loan") or 0.0) <= 0:
        applicable = False
        reason = "funding.loan<=0"
    elif key == "depreciation" and bool(raw.get("property_inventory")):
        applicable = False
        reason = "raw.property_inventory=true"
    elif key == "amortization":
        asset_map = investment.get("asset_map") or {}
        if float(asset_map.get("intangible_original") or 0.0) <= 0:
            applicable = False
            reason = "investment.asset_map.intangible_original<=0"
    return {
        "applicable": applicable,
        "reason": reason,
        "contract": table_contract(key).get("applicability") or {},
    }


def assess_reference_variant(
    fin: dict[str, Any],
    *,
    schema: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Resolve the frozen reference variant; unknown/non-frozen variants fail closed."""
    params = fin.get("params") or {}
    if params.get("is_operating") is False:
        variant_id = "non_operating"
        revenue_model = ""
    else:
        spec = fin.get("spec") if isinstance(fin.get("spec"), dict) else {}
        revenue = spec.get("revenue") if isinstance(spec.get("revenue"), dict) else {}
        revenue_model = str(revenue.get("model") or "").strip()
        if not revenue_model:
            for source in (fin.get("finance_inputs"), fin.get("input_revision"), fin.get("raw"), fin):
                if not isinstance(source, dict):
                    continue
                candidate = source.get("revenue_model")
                if not candidate and isinstance(source.get("revenue"), dict):
                    candidate = (source.get("revenue") or {}).get("model")
                if candidate:
                    revenue_model = str(candidate).strip()
                    break
        variant_id = ""
        variants = (schema or load_reference_table_schema()).get("industry_variants") or {}
        for name, variant in variants.items():
            if revenue_model and revenue_model in list((variant or {}).get("revenue_models") or []):
                variant_id = name
                break
        if not variant_id and not revenue_model:
            variant_id = "flat_estimate"
            revenue_model = "flat"
    variants = (schema or load_reference_table_schema()).get("industry_variants") or {}
    variant = variants.get(variant_id) or {}
    ok = variant.get("status") == "frozen" and variant.get("formal_reference_allowed") is True
    return {
        "ok": ok,
        "variant_id": variant_id or "unknown",
        "revenue_model": revenue_model,
        "status": variant.get("status") or "unknown",
        "formal_reference_allowed": bool(variant.get("formal_reference_allowed")),
        "reason": variant.get("reason") or variant.get("reference_scope") or "未冻结行业变体",
    }


def table_contract(key: str, schema: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    sch = schema or load_reference_table_schema()
    contract = dict((sch.get("tables") or {}).get(key) or {})
    machine = sch.get("machine_contract") or {}
    mapping = dict((machine.get("engine_reference_mapping") or {}).get(key) or {})
    contract.update(mapping)
    contract["min_detail_items"] = dict(
        (machine.get("minimum_detail_requirements") or {}).get(key) or {}
    )
    contract["applicability"] = dict((machine.get("applicability") or {}).get(key) or {})
    contract["required_row_groups"] = list(
        (machine.get("required_row_groups") or {}).get(key) or []
    )
    return contract


def _labels(body: dict[str, Any]) -> list[str]:
    raw = body.get("column_labels") or []
    out: list[str] = []
    for item in raw:
        out.append(str(item or ""))
    for col in body.get("columns") or []:
        if isinstance(col, dict):
            lab = str(col.get("label") or "")
            if lab and lab not in out:
                out.append(lab)
    return out


def _row_texts(body: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for row in body.get("rows") or []:
        if not isinstance(row, (list, tuple)):
            continue
        for cell in row:
            if cell is None or cell == "":
                continue
            texts.append(str(cell))
    for note in body.get("notes") or []:
        texts.append(str(note))
    return texts


def _has_any_label(labels: list[str], needles: list[str]) -> bool:
    joined = " ".join(labels)
    return any(n in joined for n in needles if n)


def _has_any_text(texts: list[str], needles: list[str]) -> bool:
    joined = " ".join(texts)
    return any(n in joined for n in needles if n)


def _has_all_groups(texts: list[str], groups: list[list[str]]) -> bool:
    joined = " ".join(texts)
    return all(any(token in joined for token in group if token) for group in groups)


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _inventory_keys_complete(value: dict[str, Any]) -> bool:
    keys = {str(key).strip().lower() for key in value}
    groups = (
        {"raw", "raw_material", "materials", "原材料"},
        {"fuel", "energy", "燃料", "动力", "燃料及动力"},
        {"wip", "work_in_progress", "在产品"},
        {"finished", "finished_goods", "fg", "产成品"},
    )
    return all(bool(keys & aliases) for aliases in groups)


def _count_construction_items(fin: dict[str, Any]) -> tuple[int, int]:
    """Return (item_count, qty_indicator_count)."""
    fin_in = fin.get("input_revision") or fin.get("finance_inputs") or {}
    if not isinstance(fin_in, dict):
        fin_in = {}
    raw = fin.get("raw") or {}
    bd = fin_in.get("invest_breakdown") or {}
    if not isinstance(bd, dict):
        bd = {}
    if not bd and isinstance(raw, dict):
        bd = raw.get("invest_breakdown") or {}
    if not isinstance(bd, dict):
        bd = {}
    items = bd.get("construction_items") or []
    if not isinstance(items, list):
        items = []
    n = 0
    qi = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        n += 1
        qty = it.get("quantity")
        ind = it.get("indicator_yuan") if it.get("indicator_yuan") is not None else it.get("indicator")
        if qty not in (None, "") and ind not in (None, ""):
            try:
                if float(qty) > 0 and float(ind) > 0:
                    qi += 1
            except (TypeError, ValueError):
                pass
    return n, qi


def _wc_inventory_detail(fin: dict[str, Any]) -> bool:
    fin_in = fin.get("input_revision") or fin.get("finance_inputs") or {}
    if not isinstance(fin_in, dict):
        fin_in = {}
    raw = fin.get("raw") or {}
    for src in (fin_in, raw, fin):
        if not isinstance(src, dict):
            continue
        days = src.get("wc_turnover_days") or src.get("wc_turnover") or {}
        if not isinstance(days, dict):
            continue
        # full inventory tree: raw / fuel / wip / finished (or materials/energy aliases)
        if _inventory_keys_complete(days):
            return True
        inv = days.get("inventory_detail") or days.get("存货明细")
        if isinstance(inv, dict) and _inventory_keys_complete(inv):
            return True
    wc = ((fin.get("annual") or {}).get("working_capital") or {})
    if isinstance(wc, dict):
        inv = wc.get("inventory_detail") or wc.get("components")
        if isinstance(inv, dict) and _inventory_keys_complete(inv):
            return True
        days = wc.get("days") or {}
        if isinstance(days, dict) and _inventory_keys_complete(days):
            return True
    return False


def _staff_detail_ok(fin: dict[str, Any]) -> bool:
    fin_in = fin.get("input_revision") or fin.get("finance_inputs") or {}
    if not isinstance(fin_in, dict):
        fin_in = {}
    raw = fin.get("raw") or {}
    for src in (fin_in, raw):
        if not isinstance(src, dict):
            continue
        staff = src.get("staff_detail") or src.get("wage_detail") or src.get("labor_plan")
        if isinstance(staff, list) and staff:
            # each row ideally headcount + avg wage
            good = 0
            for row in staff:
                if not isinstance(row, dict):
                    continue
                head = row.get("headcount") if row.get("headcount") is not None else row.get("人数")
                avg = (
                    row.get("avg_wage_yuan")
                    if row.get("avg_wage_yuan") is not None
                    else row.get("人均年工资")
                )
                if _positive_number(head) and _positive_number(avg):
                    good += 1
            if good >= 1:
                return True
        if _positive_number(src.get("headcount")) and _positive_number(src.get("avg_wage_yuan")):
            return True
    return False


def _asset_classes_ok(fin: dict[str, Any], body: dict[str, Any]) -> bool:
    def valid_classes(classes: Any) -> bool:
        if not isinstance(classes, list):
            return False
        valid = 0
        for item in classes:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("label") or "").strip()
            original = next(
                (item.get(field) for field in (
                    "original_value_wan", "original_wan", "original_value",
                    "original", "amount_wan", "base_wan",
                ) if item.get(field) is not None),
                None,
            )
            years = next(
                (item.get(field) for field in ("dep_years", "depreciation_years", "years", "life") if item.get(field) is not None),
                None,
            )
            if name and _positive_number(original) and _positive_number(years):
                valid += 1
        return valid >= 2

    if valid_classes(body.get("asset_classes")):
        return True
    annual = fin.get("annual") or {}
    dep_rows = annual.get("depreciation_table") or []
    if isinstance(dep_rows, list):
        for row in dep_rows:
            if isinstance(row, dict) and row.get("classes"):
                classes = row.get("classes") or []
                if valid_classes(classes):
                    return True
    raw = fin.get("raw") or {}
    if isinstance(raw, dict):
        classes = raw.get("depreciation_classes") or raw.get("asset_classes")
        if valid_classes(classes):
            return True
    fin_in = fin.get("input_revision") or fin.get("finance_inputs") or {}
    if isinstance(fin_in, dict):
        classes = fin_in.get("asset_classes") or fin_in.get("depreciation_classes")
        if valid_classes(classes):
            return True
    texts = _row_texts(body)
    return _has_all_groups(texts, [["房屋", "建筑物"], ["机器设备"]])


def _amortization_classes_ok(fin: dict[str, Any], body: dict[str, Any]) -> bool:
    pack = _fact_pack(fin)
    domains = pack.get("domains") if isinstance(pack.get("domains"), dict) else {}
    candidates: list[Any] = [domains.get("amort_bases")]
    for source in (fin.get("finance_inputs"), fin.get("input_revision"), fin.get("raw")):
        if isinstance(source, dict):
            candidates.append(source.get("amort_bases") or source.get("amortization_classes"))
    for classes in candidates:
        if not isinstance(classes, list):
            continue
        valid = 0
        for item in classes:
            if not isinstance(item, dict):
                continue
            base = next(
                (item.get(field) for field in ("original_wan", "base_wan", "amount_wan", "original_value") if item.get(field) is not None),
                None,
            )
            years = next(
                (item.get(field) for field in ("amort_years", "years", "life") if item.get(field) is not None),
                None,
            )
            if str(item.get("name") or item.get("label") or "").strip() and _positive_number(base) and _positive_number(years):
                valid += 1
        if valid >= 2:
            return True
    return _has_all_groups(_row_texts(body), [["土地使用权"], ["其他资产"]])


def _profit_structure_ok(body: dict[str, Any]) -> bool:
    labels = _labels(body)
    texts = _row_texts(body) + labels
    return _has_all_groups(
        texts,
        [
            ["弥补以前年度亏损"], ["提取法定盈余公积金"],
            ["可供分配的利润"], ["未分配利润"],
            ["息税前利润", "EBIT"], ["息税折旧摊销前利润", "EBITDA"],
        ],
    )


def _debt_repay_source_ok(body: dict[str, Any], fin: dict[str, Any]) -> bool:
    labels = _labels(body)
    texts = _row_texts(body) + labels
    if _has_all_groups(
        texts,
        [
            [
                "偿债资金来源",
                "偿还借款本金的资金来源",
                "可用于偿债的资金来源",
            ],
            ["可供投资者分配的利润"],
            ["折旧费"],
            ["摊销费"],
        ],
    ):
        return True
    # Structured extras: list of source facts with name fields.
    repay_sources = body.get("repay_sources")
    if isinstance(repay_sources, list):
        names = [
            str(row.get("name") or row.get("source") or row.get("category") or "")
            for row in repay_sources
            if isinstance(row, dict)
        ]
        if _has_all_groups(
            names,
            [["利润"], ["折旧"], ["摊销"]],
        ):
            return True
    if isinstance(repay_sources, dict) and _has_all_groups(
        [str(key) for key in repay_sources],
        [["利润"], ["折旧"], ["摊销"]],
    ):
        return True
    fin_in = fin.get("input_revision") or fin.get("finance_inputs") or {}
    if isinstance(fin_in, dict):
        source_input = fin_in.get("debt_repay_sources")
        if isinstance(source_input, list):
            names = [
                str(row.get("name") or row.get("source") or row.get("category") or "")
                for row in source_input
                if isinstance(row, dict)
            ]
            if _has_all_groups(names, [["利润"], ["折旧"], ["摊销"]]):
                return True
        if isinstance(source_input, dict) and _has_all_groups(
            [str(key) for key in source_input],
            [["profit", "利润"], ["depreciation", "折旧"], ["amortization", "摊销"]],
        ):
            return True
        pack = fin_in.get("finance_fact_pack") if isinstance(fin_in.get("finance_fact_pack"), dict) else {}
        debt = ((pack.get("domains") or {}).get("debt_schedule") or {}) if isinstance(pack, dict) else {}
        if isinstance(debt, dict):
            rows = debt.get("debt_repay_sources") or debt.get("repay_sources") or []
            if isinstance(rows, list):
                names = [
                    str(row.get("name") or row.get("source") or row.get("category") or "")
                    for row in rows
                    if isinstance(row, dict)
                ]
                if _has_all_groups(names, [["利润"], ["折旧"], ["摊销"]]):
                    return True
    return False


def assess_structure_coverage(
    key: str,
    body: dict[str, Any],
    fin: dict[str, Any],
    *,
    schema: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return structure coverage verdict for one delivery table.

    ``reference_structure`` is True only when machine checks pass the frozen
    contract — never by default.
    """
    sch = schema or load_reference_table_schema()
    contract = table_contract(key, sch)
    labels = _labels(body)
    texts = _row_texts(body)
    gaps: list[str] = []
    checks: dict[str, bool] = {}

    if not contract:
        return {
            "reference_structure": False,
            "structure_coverage": 0.0,
            "structure_gaps": [f"schema 未定义表 {key}"],
            "structure_checks": {},
            "grade_hint": "summary",
        }

    # Column coverage when reference_columns declared.
    # Skip hard column matching when table relies on engine summary columns
    # but has dedicated structure_requirements (e.g. 附表2 rollforward).
    ref_cols = list(contract.get("reference_columns") or [])
    skip_col_match = key in {
        "interest-during-construction",  # engine uses 期初/提款/利息/期末 列
        "funding",  # year plan / uses tree checked separately
    }
    if ref_cols and not skip_col_match:
        hit = 0
        for c in ref_cols:
            token = str(c).replace("（元）", "").replace("(%)", "").replace("（", "(").split("(")[0].strip()
            if any(token and token in lab for lab in labels):
                hit += 1
        ratio = hit / max(len(ref_cols), 1)
        checks["reference_columns"] = ratio >= 0.999
        if not checks["reference_columns"]:
            gaps.append(f"参考列覆盖不足({hit}/{len(ref_cols)})")
    else:
        checks["reference_columns"] = True

    expected_layout = str(contract.get("reference_layout") or "item_rows_period_columns")
    actual_layout = str(body.get("layout_mode") or "")
    checks["reference_layout"] = actual_layout == expected_layout
    if not checks["reference_layout"]:
        gaps.append(
            f"布局未达到参考行树(expected={expected_layout}, actual={actual_layout or 'missing'})"
        )

    applicability = assess_contract_applicability(key, fin)
    checks["contract_applicable"] = bool(applicability.get("applicable"))
    if not checks["contract_applicable"]:
        gaps.append(f"表不适用却进入结构裁决({applicability.get('reason') or 'unknown'})")

    if key in _VARIANT_SENSITIVE_TABLES:
        variant = assess_reference_variant(fin, schema=sch)
        checks["industry_variant_frozen"] = bool(variant.get("ok"))
        if not checks["industry_variant_frozen"]:
            gaps.append(
                "行业变体未冻结"
                f"(variant={variant.get('variant_id')}, model={variant.get('revenue_model') or 'unknown'})"
            )

    structure_texts = texts + labels
    for group in contract.get("required_row_groups") or []:
        group_id = str((group or {}).get("id") or "unknown")
        choices = [str(value) for value in ((group or {}).get("any_of") or []) if str(value)]
        passed = _has_any_text(structure_texts, choices)
        checks[f"row_group:{group_id}"] = passed
        if not passed:
            gaps.append(f"缺参考行组 {group_id}（{'/'.join(choices)}）")

    # Table-specific hard structure
    if key == "investment":
        n_items, n_qi = _count_construction_items(fin)
        min_items = int((contract.get("min_detail_items") or {}).get("construction_items") or 3)
        min_qi = int((contract.get("min_detail_items") or {}).get("quantity_indicator_pairs") or 3)
        # also accept rendered rows that already show quantity+indicator
        qi_rows = 0
        for row in body.get("rows") or []:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            # builder order: no,name,unit,quantity,indicator,...
            try:
                q = row[3]
                ind = row[4]
                if q not in (None, "") and ind not in (None, "") and float(q) > 0 and float(ind) > 0:
                    qi_rows += 1
            except (TypeError, ValueError, IndexError):
                pass
        checks["construction_items"] = max(n_items, qi_rows) >= min_items
        checks["quantity_indicator"] = max(n_qi, qi_rows) >= min_qi
        if not checks["construction_items"]:
            gaps.append(f"工程明细不足（需≥{min_items}项，当前{max(n_items, qi_rows)}）")
        if not checks["quantity_indicator"]:
            gaps.append(f"工程量×估算指标成对不足（需≥{min_qi}，当前{max(n_qi, qi_rows)}）")
        # category tree
        tree_ok = _has_any_text(texts, ["工程费用", "工程建设其它", "工程建设其他", "预备费"])
        checks["category_tree"] = tree_ok or max(n_items, qi_rows) >= min_items
        if not checks["category_tree"]:
            gaps.append("缺工程费用/其他费用/预备费类别树")

    elif key == "working-capital":
        method = body.get("method") or ((fin.get("annual") or {}).get("working_capital") or {}).get("method")
        checks["not_ratio_backsolve"] = method != "ratio_backsolve"
        if not checks["not_ratio_backsolve"]:
            gaps.append("method=ratio_backsolve，不得标周转分项法完成")
        rendered_inventory_complete = _has_all_groups(
            texts,
            [["原材料"], ["燃料", "动力"], ["在产品"], ["产成品"]],
        )
        body_inventory = body.get("inventory_detail")
        body_inventory_complete = (
            isinstance(body_inventory, dict)
            and _inventory_keys_complete(body_inventory)
        )
        inv_ok = _wc_inventory_detail(fin) or body_inventory_complete or rendered_inventory_complete
        checks["inventory_tree"] = inv_ok
        if not inv_ok:
            gaps.append("存货未展开为原材料/燃料/在产品/产成品")
        checks["turnover_days_shown"] = _has_any_label(labels, ["周转", "天数"]) or _has_any_text(
            texts, ["周转"]
        )
        if not checks["turnover_days_shown"]:
            gaps.append("未展示周转天数/次数")

    elif key == "income-statement":
        products = body.get("product_tree") or []
        min_products = int((contract.get("min_detail_items") or {}).get("products") or 1)
        valid_products = [
            product
            for product in products
            if isinstance(product, dict)
            and str(product.get("name") or "").strip()
            and _positive_number(product.get("price_per_unit"))
            and _positive_number(product.get("capacity"))
        ] if isinstance(products, list) else []
        checks["product_tree"] = len(valid_products) >= min_products
        if not checks["product_tree"]:
            gaps.append("缺分产品量价 product_tree")
        ramp_ok = False
        if isinstance(valid_products, list):
            for p in valid_products:
                if isinstance(p, dict) and len(p.get("ramp") or []) > 1:
                    ramp_ok = True
                    break
        checks["ramp_or_schedule"] = ramp_ok
        if not ramp_ok:
            gaps.append("产品爬坡/去化序列不足（仅单点）")
        tax_ok = _has_any_label(labels, ["销项", "进项", "增值税", "税金及附加"]) or _has_any_text(
            texts, ["销项", "增值税"]
        )
        checks["tax_breakdown"] = tax_ok
        if not tax_ok:
            gaps.append("税费分项列不足")

    elif key == "total-cost":
        fin_in = fin.get("input_revision") or fin.get("finance_inputs") or {}
        cost_items = {}
        if isinstance(fin_in, dict):
            cost_items = fin_in.get("cost_items") or {}
        raw = fin.get("raw") or {}
        if not cost_items and isinstance(raw, dict):
            cost_items = raw.get("cost_items") or {}
        checks["cost_item_tree"] = isinstance(cost_items, dict) and sum(
            1 for value in cost_items.values() if _positive_number(value)
        ) >= int((contract.get("min_detail_items") or {}).get("cost_items") or 3)
        if not checks["cost_item_tree"]:
            gaps.append("缺 cost_items 成本明细树（≥3 项）")
        # fixed/variable split is reference-level but not always available
        checks["fixed_variable_optional"] = True

    elif key == "wage":
        staff_ok = _staff_detail_ok(fin) or _has_any_text(texts, ["劳动定员", "人数", "人均年工资"])
        checks["staff_detail"] = staff_ok
        if not staff_ok:
            gaps.append("缺劳动定员/人数×人均年工资明细（仅有合计不算 reference）")
        checks["welfare_split"] = _has_any_label(labels, ["福利"]) or _has_any_text(texts, ["福利"])
        if not checks["welfare_split"]:
            gaps.append("缺福利费分列")

    elif key == "depreciation":
        class_ok = _asset_classes_ok(fin, body)
        checks["asset_classes"] = class_ok
        if not class_ok:
            gaps.append("缺资产类别折旧（房屋/设备等），综合原值合计不算 reference")
        checks["rollforward"] = _has_any_label(labels, ["累计折旧", "净值"]) or _has_all_groups(
            texts, [["当期折旧费", "累计折旧"], ["净值"]]
        )
        if not checks["rollforward"]:
            gaps.append("缺累计折旧/净值滚动列")

    elif key == "amortization":
        # multi base preferred; single intangible base is intermediate but still
        # accepted as structure if base>0 and years present
        rows = body.get("rows") or []
        effective_rows = 0
        column_keys = [
            str(column.get("key") or "")
            for column in body.get("columns") or []
            if isinstance(column, dict)
        ]
        total_index = column_keys.index("total") if "total" in column_keys else -1
        for row in rows:
            if not isinstance(row, (list, tuple)):
                continue
            try:
                base = row[total_index] if total_index >= 0 and len(row) > total_index else None
                if base not in (None, "") and abs(float(base)) > 1e-9:
                    effective_rows += 1
            except (TypeError, ValueError):
                pass
        checks["has_base"] = effective_rows > 0
        if not checks["has_base"]:
            gaps.append("摊销基数为空")
        checks["amortization_classes"] = _amortization_classes_ok(fin, body)
        if not checks["amortization_classes"]:
            gaps.append("缺土地使用权/其他资产两类摊销基数与年限")

    elif key == "profit-distribution":
        checks["distribution_tree"] = _profit_structure_ok(body)
        if not checks["distribution_tree"]:
            gaps.append("利润分配行树不足（弥补亏损/盈余公积/可供分配/未分配）")

    elif key == "debt-service":
        checks["repay_sources"] = _debt_repay_source_ok(body, fin)
        if not checks["repay_sources"]:
            gaps.append("缺偿债资金来源（利润/折旧/摊销）分项")
        checks["coverage_ratios"] = _has_all_groups(
            labels + texts,
            [["利息备付率", "ICR"], ["偿债备付率", "DSCR"]],
        )
        if not checks["coverage_ratios"]:
            gaps.append("缺 DSCR/ICR 覆盖率列")

    elif key == "funding":
        year_plan = any(str(x).startswith("建设期第") for x in labels) or any("分年" in x for x in labels)
        checks["year_plan"] = year_plan or bool(body.get("reference_structure"))
        # if build_years==0/1, single year still ok with uses tree
        params = fin.get("params") or {}
        build_years = int(params.get("build_years") or 0)
        if build_years <= 1:
            checks["year_plan"] = True
        if not checks["year_plan"]:
            gaps.append("缺建设期分年使用计划列")
        tree_ok = _has_any_text(texts, ["总投资", "资金筹措", "资本金", "贷款"])
        checks["uses_sources_tree"] = tree_ok
        if not tree_ok:
            gaps.append("缺总投资/资金筹措行树")

    elif key == "interest-during-construction":
        need_cols = ["期初", "提款", "利息", "期末"]
        hit = sum(
            1 for n in need_cols
            if any(n in lab for lab in labels) or any(n in text for text in texts)
        )
        checks["idc_rollforward"] = hit >= 3
        if not checks["idc_rollforward"]:
            gaps.append("建设期利息滚动列不足（期初/提款/利息/期末）")

    elif key in {"cashflow", "capital-cashflow"}:
        tree_ok = _has_any_text(texts, ["现金流入", "现金流出"]) or _has_any_label(
            labels, ["营业收入", "经营成本", "建设投资", "资本金", "还本", "付息"]
        )
        checks["cf_components"] = tree_ok
        if not tree_ok:
            gaps.append("现金流组成项不足")
        # capital needs debt service linkage conceptually; structure only checks labels
        if key == "capital-cashflow":
            checks["capital_debt_legs"] = _has_all_groups(
                labels + texts,
                [["还本", "借款本金偿还"], ["付息", "借款利息支付"], ["资本金"]],
            )
            if not checks["capital_debt_legs"]:
                gaps.append("资本金现金流缺还本/付息/资本金投入列")

    else:
        checks["baseline"] = True

    hard_checks = {
        k: v for k, v in checks.items()
        if not k.endswith("_optional") and not k.endswith("_preferred")
    }
    passed = sum(1 for v in hard_checks.values() if v)
    total = max(len(hard_checks), 1)
    coverage = round(passed / total, 4)
    reference_structure = coverage >= 0.999 and not gaps
    # allow tiny float noise: all hard checks true
    if all(hard_checks.values()):
        reference_structure = True
        gaps = []

    return {
        "reference_structure": bool(reference_structure),
        "structure_coverage": coverage if not reference_structure else 1.0,
        "structure_gaps": gaps,
        "structure_checks": checks,
        "grade_hint": "reference" if reference_structure else "summary",
        "contract_delivery_no": contract.get("delivery_no"),
        "contract_title": contract.get("title"),
        "engine_vs_reference_note": contract.get("engine_vs_reference_note")
        or contract.get("engine_sheet_mapping_note")
        or "",
    }


def assess_missing_fields_extended(
    fin: dict[str, Any],
    *,
    schema: Optional[dict[str, Any]] = None,
) -> dict[str, list[str]]:
    """Extend baseline missing_fields with reference-depth inputs.

    Only declares missing for **template/reference** depth; summary path may still compute.
    """
    sch = schema or load_reference_table_schema()
    missing: dict[str, list[str]] = {}
    params = fin.get("params") or {}
    is_operating = bool(params.get("is_operating", True))
    funding = fin.get("funding") or {}
    investment = fin.get("investment") or {}

    # investment depth
    n_items, n_qi = _count_construction_items(fin)
    inv_contract = table_contract("investment", sch)
    min_items = int((inv_contract.get("min_detail_items") or {}).get("construction_items") or 3)
    min_qi = int((inv_contract.get("min_detail_items") or {}).get("quantity_indicator_pairs") or 3)
    inv_miss: list[str] = []
    if n_items < min_items:
        inv_miss.append(f"construction_items 明细（需≥{min_items} 项工程量×指标）")
    elif n_qi < min_qi:
        inv_miss.append(f"construction_items 中 quantity×indicator 成对（需≥{min_qi}）")
    if inv_miss:
        missing["investment"] = inv_miss

    if is_operating:
        wc_total = float(investment.get("working_capital") or 0.0)
        if wc_total > 0.01:
            wc_missing: list[str] = []
            if not _wc_inventory_detail(fin):
                wc_missing.append(
                    "wc_turnover 存货分项（原材料/燃料/在产品/产成品周转天数）"
                )
            fact_domains = (_fact_pack(fin).get("domains") or {}) if _fact_pack(fin) else {}
            fin_in_wc = fin.get("input_revision") or fin.get("finance_inputs") or {}
            turnover = (
                (fin_in_wc.get("wc_turnover") if isinstance(fin_in_wc, dict) else None)
                or ((fin.get("raw") or {}).get("wc_turnover"))
                or {}
            )
            fact_turnover = (
                fact_domains.get("wc_turnover")
                if isinstance(fact_domains, dict) else None
            )
            if isinstance(fact_turnover, dict):
                turnover = {
                    **(turnover if isinstance(turnover, dict) else {}),
                    **fact_turnover,
                }
            if not (
                isinstance(turnover, dict)
                and turnover.get("short_term_loan_wan") not in (None, "")
                and turnover.get("self_funded_wan") not in (None, "")
            ):
                wc_missing.append("流动资金来源（短期借款+企业自筹）")
            if wc_missing:
                missing["working-capital"] = wc_missing
        if not _staff_detail_ok(fin):
            missing["wage"] = ["staff_detail 劳动定员（人数×人均年工资，可多类）"]
        if not _asset_classes_ok(fin, {}):
            # only if depreciation applies
            raw = fin.get("raw") or {}
            if not raw.get("property_inventory"):
                missing["depreciation"] = ["asset_classes 资产类别原值与折旧年限（≥2 类）"]

        # income products already handled by table_render baseline for flat
        # profit/debt structure are renderer concerns primarily; still declare input gaps
        fin_in = fin.get("input_revision") or fin.get("finance_inputs") or {}
        if not isinstance(fin_in, dict):
            fin_in = {}
        if float(funding.get("loan") or 0.0) > 0 and not fin_in.get("debt_repay_sources"):
            # not strictly required as input if renderer derives sources; skip input missing
            pass

    return missing


def merge_missing(
    base: dict[str, list[str]],
    extra: dict[str, list[str]],
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {k: list(v) for k, v in (base or {}).items()}
    for k, vals in (extra or {}).items():
        cur = out.setdefault(k, [])
        for v in vals or []:
            if v not in cur:
                cur.append(v)
    return out
