"""财务结果完整性校验：正文和工件必须绑定可回读的一致 FinanceRun。

``run_id`` 只承担结果寻址、回读和 lineage。
"""

from __future__ import annotations

from typing import Any


def _load_binding(
    workspace_id: str,
) -> dict[str, Any]:
    """MCP 边界无持久化 finance_binding；绑定由调用方显式传 expected_run_id。"""
    return {}


def _assert_acquisition_publish_finance_binding(
    workspace_id: str,
    *,
    binding: dict[str, Any],
    run_id: str,
    strict: bool,
) -> dict[str, Any]:
    """Validate a FinanceSpec v3 acquisition release without legacy 13-table rules.

    Acquisition packs have their own Word/Excel/report-data consistency contract;
    applying the feasibility-project 13-sheet renderer here would reject a valid
    acquisition release for the wrong product contract.
    """

    from lvke_mcp.domains.asset_acquisition import backend as acquisition_service
    from lvke_mcp.domains.finance.spec import validate_for_formal

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def block(code: str, message: str, **details: Any) -> None:
        item: dict[str, Any] = {"code": code, "message": message}
        if details:
            item["details"] = details
        blockers.append(item)

    run = acquisition_service.get_run(workspace_id, run_id)
    if not run:
        block("finance_acquisition_run_not_found", "报告绑定的资产收购 run 不存在")
        return {
            "ok": False,
            "blockers": blockers,
            "warnings": warnings,
            "bound_run_id": run_id,
            "binding": binding,
            "artifact_id": None,
            "gate_type": "asset_acquisition",
            "validation_level": "incomplete",
        }

    if run.get("status") != "succeeded" or not run.get("consistency_ok"):
        block(
            "finance_run_consistency_failed",
            "资产收购 run 尚未成功完成内部勾稽",
            status=run.get("status"),
            consistency_ok=run.get("consistency_ok"),
        )
    spec_id = str(run.get("spec_id") or "")
    spec_row = (
        acquisition_service.get_spec(workspace_id, spec_id)
        if spec_id else {}
    )
    spec = spec_row.get("spec") if isinstance(spec_row, dict) else None
    formal_ok = False
    formal_errors: list[str] = []
    if isinstance(spec, dict):
        formal_ok, formal_errors = validate_for_formal(spec)
    if (
        not run.get("formal_spec_valid")
        or not formal_ok
        or not isinstance(spec, dict)
    ):
        block(
            "finance_formal_spec_invalid",
            "资产收购 run 未绑定通过正式校验的 FinanceSpec",
            errors=formal_errors or run.get("formal_spec_errors") or [],
        )
    elif (
        spec_row.get("spec_hash") != run.get("spec_hash")
        or acquisition_service._hash(spec) != run.get("spec_hash")  # noqa: SLF001
    ):
        block("finance_spec_binding_mismatch", "资产收购 run 的 Spec 快照或哈希已不一致")
    elif run.get("evidence_binding_hash"):
        current_evidence = acquisition_service.assess_spec_evidence(
            workspace_id,
            spec,
        )
        if (
            not current_evidence.get("formal_ok")
            or current_evidence.get("binding_hash") != run.get("evidence_binding_hash")
            or current_evidence.get("binding_version") != run.get("evidence_binding_version")
        ):
            block(
                "finance_evidence_binding_invalid",
                "资产收购 run 的证据绑定已失效或偏离运行快照",
                expected_binding_hash=run.get("evidence_binding_hash"),
                current_binding_hash=current_evidence.get("binding_hash"),
                evidence_status=current_evidence.get("status"),
                issues=[
                    *(current_evidence.get("invalid") or []),
                    *(current_evidence.get("missing") or []),
                    *(current_evidence.get("pending") or []),
                ],
            )
    if isinstance(spec, dict):
        try:
            from lvke_mcp.domains.asset_acquisition.backend import _bind_spec_evidence

            current_evidence = _bind_spec_evidence(workspace_id, spec)
        except Exception as exc:  # noqa: BLE001
            current_evidence = {
                "formal_ok": False,
                "status": "invalid",
                "binding_hash": "",
                "invalid": [{"code": "SOURCE_EVIDENCE_STATE_INVALID", "message": str(exc)}],
            }
        if (
            not current_evidence.get("formal_ok")
            or current_evidence.get("binding_hash") != run.get("evidence_binding_hash")
            or current_evidence.get("binding_version") != run.get("evidence_binding_version")
        ):
            block(
                "finance_evidence_binding_invalid",
                "资产收购 run 的服务端证据绑定无效或与运行快照不一致",
                expected_binding_hash=run.get("evidence_binding_hash"),
                current_binding_hash=current_evidence.get("binding_hash"),
                evidence_status=current_evidence.get("status"),
                issues=[
                    *(current_evidence.get("invalid") or []),
                    *(current_evidence.get("missing") or []),
                    *(current_evidence.get("pending") or []),
                ],
            )

    if isinstance(spec, dict):
        max_price_ok, max_price_error, max_price_details = acquisition_service._max_price_validation(  # noqa: SLF001
            run,
            spec,
        )
        if not max_price_ok:
            block(
                "finance_max_price_validation_failed",
                "最高可接受收购价未按确定性阈值完成求解",
                **({"gate_reason": max_price_error} | max_price_details),
            )

    open_blocking = [
        issue for issue in (run.get("issues") or [])
        if issue.get("blocking") and issue.get("status") == "open"
    ]
    if open_blocking:
        block(
            "finance_blocking_issues",
            f"资产收购 run 仍有 {len(open_blocking)} 个开放的阻断问题",
            issues=open_blocking,
        )

    succeeded = [
        artifact
        for artifact in acquisition_service.list_artifacts(
            workspace_id,
            limit=100,
        )
        if artifact.get("run_id") == run_id and artifact.get("status") == "succeeded"
    ]
    valid_artifacts: list[dict[str, Any]] = []
    artifact_failures: list[dict[str, Any]] = []
    for artifact in succeeded:
        reasons: list[str] = []
        checks = artifact.get("consistency_checks") or []
        report_data = artifact.get("report_data") or {}
        report_bindings = report_data.get("bindings") or {}
        if artifact.get("integrity_status") != "passed" or not artifact.get("ok", True):
            reasons.append("hash_integrity_failed")
        if artifact.get("numeric_consistency") != "passed":
            reasons.append("numeric_consistency_failed")
        if not checks or any(not check.get("passed") for check in checks):
            reasons.append("numeric_checks_incomplete")
        if artifact.get("spec_hash") != run.get("spec_hash"):
            reasons.append("spec_hash_mismatch")
        if str(artifact.get("fact_revision") or "") != spec_id:
            reasons.append("fact_revision_mismatch")
        if artifact.get("spec_snapshot_hash") != run.get("spec_snapshot_hash"):
            reasons.append("spec_snapshot_hash_mismatch")
        if artifact.get("evidence_binding_hash") != run.get("evidence_binding_hash"):
            reasons.append("evidence_binding_hash_mismatch")
        if artifact.get("evidence_binding_version") != run.get("evidence_binding_version"):
            reasons.append("evidence_binding_version_mismatch")
        if artifact.get("report_data_hash") != report_data.get("report_data_hash"):
            reasons.append("report_data_hash_mismatch")
        for field in (
            "run_id", "spec_hash", "input_hash", "model_version",
            "spec_snapshot_hash", "evidence_binding_hash", "evidence_binding_version",
        ):
            if report_bindings.get(field) != run.get(field):
                reasons.append(f"report_data_{field}_mismatch")
        if str(report_bindings.get("spec_id") or "") != spec_id:
            reasons.append("report_data_fact_revision_mismatch")
        if reasons:
            artifact_failures.append({
                "artifact_id": artifact.get("artifact_id"),
                "reasons": sorted(set(reasons)),
            })
        else:
            valid_artifacts.append(artifact)

    if not succeeded:
        block("finance_acquisition_artifact_missing", "该 run 尚无成功且完整的收购工件")
    elif not valid_artifacts:
        block(
            "finance_artifact_mismatch",
            "正式收购工件的哈希、绑定或数字一致性校验不完整",
            artifacts=artifact_failures,
        )

    valid_ids = sorted(
        str(item.get("artifact_id") or "")
        for item in valid_artifacts
        if item.get("artifact_id")
    )
    if len(valid_ids) > 1:
        block(
            "finance_acquisition_artifact_ambiguous",
            "该 run 对应多个完整工件，必须使用 artifact_id 直接寻址",
            artifact_ids=valid_ids,
        )
    bound_artifact_id = valid_ids[0] if len(valid_ids) == 1 else ""

    bound_tables_package_id = ""
    valid_tables_package: dict[str, Any] = {}
    if run.get("model_version") == "acquisition_model.v3":
        try:
            from lvke_mcp.domains.asset_acquisition.tables import PACKAGE_STORE

            table_records = [
                record
                for record in PACKAGE_STORE.list(workspace_id)
                if (record.get("payload") or {}).get("run_id") == run_id
                and ((record.get("payload") or {}).get("integrity") or {}).get("status") == "passed"
            ]
        except Exception as exc:  # noqa: BLE001
            table_records = []
            block(
                "finance_acquisition_tables_unverifiable",
                f"无法验证收购十三表 package：{type(exc).__name__}",
            )
        if not table_records:
            block(
                "finance_acquisition_tables_missing",
                "月度 v3 run 缺少通过完整性校验的收购十三表 package",
            )
        elif len(table_records) > 1:
            block(
                "finance_acquisition_tables_ambiguous",
                "该 run 对应多个完整十三表 package，必须使用 package_id 直接寻址",
                package_ids=sorted(str(item.get("object_id") or "") for item in table_records),
            )
        else:
            valid_tables_package = table_records[0]
            bound_tables_package_id = str(valid_tables_package.get("object_id") or "")
            table_payload = valid_tables_package.get("payload") or {}
            table_mismatches = [
                {"field": field, "expected": run.get(field), "actual": table_payload.get(field)}
                for field in (
                    "run_id", "spec_hash", "input_hash", "model_version",
                    "evidence_binding_hash",
                )
                if table_payload.get(field) != run.get(field)
            ]
            if table_mismatches:
                block(
                    "finance_acquisition_tables_mismatch",
                    "收购十三表与 run/spec/input/model/evidence 绑定不一致",
                    package_id=bound_tables_package_id,
                    mismatches=table_mismatches,
                )

    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "bound_run_id": run_id,
        "binding": binding,
        "artifact_id": bound_artifact_id or None,
        "acquisition_tables_package_id": bound_tables_package_id or None,
        "valid_artifact_ids": sorted(valid_ids),
        "gate_type": "asset_acquisition",
        "validation_level": "complete" if not blockers else "incomplete",
    }


def assert_acquisition_report_finance_binding(
    workspace_id: str,
    *,
    run_id: str,
    package_id: str,
) -> dict[str, Any]:
    """Validate an acquisition run for a restricted report draft.

    This is deliberately weaker than the formal publish gate: preview and
    process-acceptance runs may feed the governed report chain, but they never
    become formal-release eligible through this check.
    """

    from lvke_mcp.domains.asset_acquisition import backend as acquisition_service
    from lvke_mcp.domains.asset_acquisition.tables import get_package_record

    blockers: list[dict[str, Any]] = []

    def block(code: str, message: str, **details: Any) -> None:
        item: dict[str, Any] = {"code": code, "message": message}
        if details:
            item["details"] = details
        blockers.append(item)

    run = acquisition_service.get_run(workspace_id, run_id)
    if not run:
        block("finance_acquisition_run_not_found", "报告绑定的资产收购 run 不存在")
    elif run.get("status") != "succeeded" or run.get("consistency_ok") is not True:
        block(
            "finance_run_consistency_failed",
            "资产收购 run 尚未成功完成内部勾稽",
            status=run.get("status"),
            consistency_ok=run.get("consistency_ok"),
        )

    package = get_package_record(workspace_id, package_id) if package_id else None
    payload = (package or {}).get("payload") or {}
    if package is None:
        block("acquisition_tables_package_required", "报告必须绑定资产收购十三表 package")
    else:
        if str(payload.get("run_id") or "") != run_id:
            block("acquisition_tables_run_mismatch", "资产收购十三表与报告 run 不一致")
        if (payload.get("integrity") or {}).get("status") != "passed":
            block("acquisition_tables_integrity_failed", "资产收购十三表完整性校验未通过")
        if run:
            mismatches = [
                {
                    "field": field,
                    "expected": run.get(field),
                    "actual": payload.get(field),
                }
                for field in (
                    "run_id", "spec_hash", "input_hash", "model_version",
                    "evidence_binding_hash",
                )
                if payload.get(field) != run.get(field)
            ]
            if mismatches:
                block(
                    "acquisition_tables_binding_mismatch",
                    "资产收购十三表与 run 的不可变绑定不一致",
                    mismatches=mismatches,
                )

    mode = str((run or {}).get("delivery_mode") or "")
    if mode not in {"estimate_preview", "process_acceptance"}:
        block(
            "acquisition_report_preview_mode_required",
            "受限报告校验仅适用于 estimate_preview 或 process_acceptance",
            delivery_mode=mode,
        )
    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": [{
            "code": "finance_acquisition_preview_only",
            "message": "当前报告仅可用于技术预览或过程验收，不具备正式发布资格",
        }],
        "bound_run_id": run_id,
        "acquisition_tables_package_id": package_id or None,
        "artifact_id": None,
        "gate_type": "asset_acquisition",
        "validation_level": "preview",
        "formal_release_eligible": False,
    }


def assert_publish_finance_binding(
    workspace_id: str,
    *,
    expected_run_id: str = "",
    strict: bool = True,
) -> dict[str, Any]:
    """校验报告绑定的 FinanceRun 及其表格工件完整性。

    ``strict`` 只决定缺少显式绑定时返回 blocker 还是 warning。
    """
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    binding: dict[str, Any] = {}
    actual_bound = ""
    bound = str(expected_run_id or "").strip()
    bound_tables_package_id = ""
    bound_xlsx_uri = ""
    bound_xlsx_hash = ""

    # FinanceSpec v3 acquisition runs publish their own acquisition report
    # pack.  Return before touching audit_db/table_render/table_pack so the
    # legacy feasibility-project 13-sheet contract remains unchanged.
    if bound.startswith("acqrun_"):
        result = _assert_acquisition_publish_finance_binding(
            workspace_id,
            binding=binding,
            run_id=bound,
            strict=strict,
        )
        if blockers:
            result = dict(result)
            result["ok"] = False
            result["blockers"] = [*blockers, *(result.get("blockers") or [])]
            result["actual_bound_run_id"] = actual_bound or None
        return result

    run_view: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}
    if not bound:
        item = {
            "code": "finance_binding_missing",
            "message": "报告必须明确绑定 finance_run_id",
        }
        (blockers if strict else warnings).append(item)
    else:
        try:
            from lvke_mcp.domains.finance import run_store, table_render
            from lvke_mcp.domains.finance.model_manifest import (
                DEFAULT_SPEC_SCHEMA_VERSION,
                manifest_from_dict,
            )

            run_view = run_store.load_run(workspace_id, bound) or {}
            snapshot = run_store.load_result_snapshot(workspace_id, bound) or {}
        except Exception as exc:  # noqa: BLE001
            blockers.append({
                "code": "finance_run_unverifiable",
                "message": f"无法读取绑定的 FinanceRun：{type(exc).__name__}",
            })
            run_view = {}
            snapshot = {}

        if not run_view or not snapshot:
            blockers.append({
                "code": "finance_run_not_found",
                "message": "绑定的 FinanceRun 不存在或缺少结果快照",
                "details": {"run_id": bound},
            })
        elif str(run_view.get("workspace_id") or "") != str(workspace_id):
            blockers.append({
                "code": "finance_run_workspace_mismatch",
                "message": "绑定的 FinanceRun 不属于当前工作区",
            })
        else:
            if not run_view.get("consistency_ok"):
                blockers.append({
                    "code": "finance_run_consistency_failed",
                    "message": "绑定的 FinanceRun 勾稽未通过",
                })
            manifest_data = snapshot.get("model_manifest") or {}
            if not manifest_data:
                blockers.append({
                    "code": "finance_manifest_missing",
                    "message": "绑定的 FinanceRun 缺少完整 ModelManifest",
                })
            else:
                manifest = manifest_from_dict(manifest_data)
                if not snapshot.get("valuation_date"):
                    blockers.append({
                        "code": "finance_valuation_date_missing",
                        "message": "绑定的 FinanceRun 缺少 valuation_date",
                    })
                if manifest.spec_schema_version != DEFAULT_SPEC_SCHEMA_VERSION:
                    blockers.append({
                        "code": "finance_spec_schema_version_stale",
                        "message": (
                            "绑定的 FinanceRun 使用了过期 FinanceSpec schema："
                            f"{manifest.spec_schema_version}，当前要求 {DEFAULT_SPEC_SCHEMA_VERSION}"
                        ),
                    })
                manifest_errors = manifest.validate(as_of=snapshot.get("valuation_date"))
                if manifest_errors:
                    blockers.append({
                        "code": "finance_manifest_invalid",
                        "message": f"绑定的 FinanceRun ModelManifest 无效：{manifest_errors}",
                    })
                if snapshot.get("manifest_hash") != manifest.hash:
                    blockers.append({
                        "code": "finance_manifest_hash_mismatch",
                        "message": "manifest_hash 与 ModelManifest 内容不一致",
                    })

            quality = table_render.build_all_structured(snapshot).get("_meta") or {}
            if not quality.get("reference_structure_ready"):
                blockers.append({
                    "code": "finance_reference_structure_incomplete",
                    "message": (
                        "财务附表不完整："
                        f"有效 {quality.get('effective_table_count', 0)}/"
                        f"{quality.get('required_table_count', 13)} 张"
                    ),
                })
            from lvke_mcp.adapters.finance_tables_repository import (
                PACKAGE_STORE,
                xlsx_path_from_uri,
            )
            from lvke_mcp.domains.finance.run_service import DELIVERY_TABLE_KEYS

            expected_table_ids = set(DELIVERY_TABLE_KEYS)
            package_failures: list[dict[str, Any]] = []
            valid_packages: list[tuple[dict[str, Any], Any, str]] = []
            matching_packages = [
                record
                for record in PACKAGE_STORE.list(workspace_id)
                if str((record.get("payload") or {}).get("run_id") or "") == bound
            ]
            for package_record in matching_packages:
                package_payload = package_record.get("payload") or {}
                package_id = str(package_record.get("object_id") or "")
                reasons: list[str] = []
                manifest_rows = [
                    item
                    for item in (package_payload.get("table_manifest") or [])
                    if isinstance(item, dict)
                ]
                manifest_ids = {
                    str(item.get("table_id") or "") for item in manifest_rows
                }
                manifest_run_ids = {
                    str(item.get("run_id") or "") for item in manifest_rows
                }
                tables = (
                    package_payload.get("tables")
                    if isinstance(package_payload.get("tables"), dict)
                    else {}
                )
                validation = (
                    package_payload.get("validation")
                    if isinstance(package_payload.get("validation"), dict)
                    else {}
                )
                technical = (
                    validation.get("technical_validation")
                    if isinstance(validation.get("technical_validation"), dict)
                    else {}
                )
                if len(manifest_rows) != len(expected_table_ids) or manifest_ids != expected_table_ids:
                    reasons.append("thirteen_table_manifest_incomplete")
                if manifest_run_ids != {bound}:
                    reasons.append("table_manifest_run_mismatch")
                if set(tables) != expected_table_ids:
                    reasons.append("thirteen_table_payload_incomplete")
                if not bool(technical.get("valid", validation.get("valid"))):
                    reasons.append("technical_validation_failed")
                xlsx_uri = str(package_record.get("resource_uri") or "") + "/xlsx"
                xlsx_path = xlsx_path_from_uri(xlsx_uri)
                if xlsx_path is None:
                    reasons.append("xlsx_resource_missing")
                else:
                    from lvke_mcp.adapters.spreadsheets.finance_export import (
                        assess_finance_delivery_quality,
                    )

                    delivery_quality = (
                        assess_finance_delivery_quality(snapshot).get("delivery_quality") or {}
                    )
                    if not delivery_quality.get("validation_complete"):
                        reasons.append("xlsx_semantic_validation_failed")
                if reasons:
                    package_failures.append({
                        "package_id": package_id,
                        "reasons": reasons,
                    })
                else:
                    valid_packages.append((package_record, xlsx_path, xlsx_uri))
            if not matching_packages:
                blockers.append({
                    "code": "finance_tables_package_missing",
                    "message": "绑定的 FinanceRun 尚未生成真实 FinanceTablesPackage",
                })
            elif not valid_packages:
                blockers.append({
                    "code": "finance_tables_package_invalid",
                    "message": "FinanceTablesPackage 未同时满足同一 run、13 表和 XLSX Resource 要求",
                    "details": {"packages": package_failures},
                })
            else:
                import hashlib

                package_record, xlsx_path, bound_xlsx_uri = max(
                    valid_packages,
                    key=lambda item: str(item[0].get("created_at") or ""),
                )
                bound_tables_package_id = str(package_record.get("object_id") or "")
                bound_xlsx_hash = "sha256:" + hashlib.sha256(
                    xlsx_path.read_bytes()
                ).hexdigest()

            open_blocking = [
                item for item in (run_view.get("issues") or [])
                if item.get("status") == "open" and item.get("blocking")
            ]
            if open_blocking:
                blockers.append({
                    "code": "finance_blocking_issues",
                    "message": f"绑定的 FinanceRun 仍有 {len(open_blocking)} 个一致性阻断问题",
                })

    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "bound_run_id": bound or None,
        "actual_bound_run_id": actual_bound or None,
        "finance_tables_package_id": bound_tables_package_id or None,
        "xlsx_resource_uri": bound_xlsx_uri or None,
        "xlsx_hash": bound_xlsx_hash or None,
        "binding": binding,
        "validation_level": "complete" if not blockers else "incomplete",
    }


def verify_narrative_numbers(
    workspace_id: str,
    text: str,
    *,
    run_id: str = "",
    tolerance: float = 0.05,
) -> dict[str, Any]:
    """从正文抽取关键财务数字，与指定/绑定 run 的结果比对（粗粒度）。

    不替代人工审；用于发布前程序校验。无法解析的数字不阻断，仅报告 unmapped。
    """
    import re

    from lvke_mcp.domains.finance import run_store, run_service

    rid = run_id or str(
        (_load_binding(workspace_id) or {}).get("finance_run_id") or ""
    )
    if not rid:
        latest = run_store.latest_run(workspace_id) or {}
        rid = str(latest.get("run_id") or "")
    if not rid:
        return {
            "ok": False,
            "run_id": None,
            "matches": [],
            "mismatches": [],
            "message": "无 run 可校验",
        }

    if rid.startswith("acqrun_"):
        from lvke_mcp.domains.asset_acquisition import backend as acquisition_service

        run = acquisition_service.get_run(workspace_id, rid)
        if not run:
            return {
                "ok": False, "run_id": rid, "matches": [], "mismatches": [],
                "unmapped": [], "message": "资产收购 run 不存在",
            }
        result = run.get("result") or {}
        indicators = result.get("indicators") or {}
        acquisition_mapping = [
            ("purchase_price", result.get("purchase_price_wan"), r"(?:收购价|购买价)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*万"),
            ("total_acquisition_cost", result.get("total_acquisition_cost_wan"), r"(?:收购总成本|总收购成本|总投资)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*万"),
            ("project_irr", indicators.get("project_irr_pct"), r"(?:项目内部收益率|项目IRR|财务内部收益率)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            ("equity_irr", indicators.get("equity_irr_pct"), r"(?:股东内部收益率|股东IRR|资本金内部收益率)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            ("npv", indicators.get("npv_wan"), r"(?:净现值|NPV)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*万"),
            ("minimum_dscr", indicators.get("minimum_dscr"), r"(?:最低DSCR|最低偿债备付率)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)"),
        ]
        matches: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []
        unmapped: list[dict[str, Any]] = []
        for code, expected, pattern in acquisition_mapping:
            match = re.search(pattern, text or "")
            if not match:
                continue
            if expected is None:
                unmapped.append({
                    "element": code, "found": match.group(1),
                    "reason": "run_missing_expected_value",
                })
                continue
            expected_number = float(expected)
            found = float(match.group(1))
            if code in {"project_irr", "equity_irr"}:
                if expected_number > 1 and found <= 1:
                    found *= 100
                if expected_number <= 1 and found > 1:
                    expected_number *= 100
            same = abs(found - expected_number) <= max(
                tolerance, abs(expected_number) * 0.01,
            )
            item = {
                "element": code, "expected": expected_number,
                "found": found, "ok": same,
            }
            (matches if same else mismatches).append(item)
        return {
            "ok": not mismatches and not unmapped,
            "run_id": rid,
            "matches": matches,
            "mismatches": mismatches,
            "unmapped": unmapped,
        }

    view = run_service.get_workspace_finance_run(workspace_id, run_id=rid, view="summary")
    ind = view.get("indicators") or {}
    inv = view.get("investment") or {}
    # 兼容 snapshot / 审计重建键名
    fund = view.get("funding") or {}
    mapping = [
        ("total_investment", inv.get("total") or inv.get("total_investment"), r"(?:总投资|项目总投资)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*万"),
        ("capital", fund.get("capital") or fund.get("equity_capital"), r"(?:项目资本金|自有资金|资本金)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*万"),
        ("loan", fund.get("loan"), r"(?:银行贷款|贷款金额|贷款)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*万"),
        ("revenue", ind.get("revenue") or ind.get("annual_revenue"), r"(?:年营业收入|营业收入)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*万"),
        ("net_profit", ind.get("net_profit") or ind.get("annual_net_profit"), r"(?:年净利润|净利润)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*万"),
        ("project_irr", ind.get("project_irr_pct") or ind.get("project_irr"), r"(?:内部收益率|IRR|财务内部收益率)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
        ("npv", ind.get("npv_wan") or ind.get("npv"), r"(?:净现值|NPV)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*万"),
        ("static_payback", ind.get("static_payback_years") or ind.get("static_payback"), r"(?:静态投资回收期|(?<!动态)投资回收期)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*年"),
        ("dynamic_payback", ind.get("dynamic_payback_years") or ind.get("dynamic_payback"), r"(?:动态投资回收期)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*年"),
        ("capital_irr", ind.get("capital_irr_pct") or ind.get("capital_irr"), r"(?:资本金内部收益率|资本金IRR)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
        ("bep", ind.get("bep_pct") or ind.get("bep"), r"(?:盈亏平衡点|BEP)\s*[为:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
    ]
    matches: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    for code, exp, pattern in mapping:
        if exp is None:
            m = re.search(pattern, text or "")
            if m:
                unmapped.append({"element": code, "found": m.group(1), "reason": "run_missing_expected_value"})
            continue
        try:
            exp_f = float(exp)
        except (TypeError, ValueError):
            continue
        m = re.search(pattern, text or "")
        if not m:
            continue
        try:
            got = float(m.group(1))
        except (TypeError, ValueError):
            continue
        # IRR 可能是 16.8 或 0.168；若 expected>1 且 got<=1，放大 got
        if code == "project_irr":
            if exp_f > 1 and got <= 1:
                got *= 100
            if exp_f <= 1 and got > 1:
                exp_f *= 100
        ok = abs(got - exp_f) <= max(tolerance, abs(exp_f) * 0.01)
        item = {"element": code, "expected": exp_f, "found": got, "ok": ok}
        (matches if ok else mismatches).append(item)

    return {
        "ok": not mismatches and not unmapped,
        "run_id": rid,
        "matches": matches,
        "mismatches": mismatches,
        "unmapped": unmapped,
        "checked": len(matches) + len(mismatches),
    }
