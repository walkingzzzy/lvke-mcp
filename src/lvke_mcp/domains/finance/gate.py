"""财务宿主门禁：财务正文/发布前必须绑定可用的模型 run。

对齐《财务模型与13表上下级关系及AI调用流程方案》：
- 无成功 run_id 不得写确定性财务结论 / 不得装配 13 表
- 正式发布（review_grade）必须绑定已批准且勾稽通过的 run
- 预览（estimate_preview）可用 draft run，但不得伪装评审级
"""

from __future__ import annotations

from typing import Any, Optional


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

    from lvke_mcp.servers.lvke_asset_acquisition import backend as acquisition_service
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
            "approved_run_id": None,
            "binding": binding,
            "artifact_id": binding.get("artifact_id"),
            "gate_type": "asset_acquisition",
            "assurance_level": "estimate_preview",
        }

    if run.get("status") != "succeeded" or not run.get("consistency_ok"):
        block(
            "approved_run_consistency_failed",
            "资产收购 run 尚未成功完成内部勾稽",
            status=run.get("status"),
            consistency_ok=run.get("consistency_ok"),
        )
    if run.get("review_status") != "approved" or not run.get("approved_by"):
        block("finance_run_not_approved", "资产收购 run 尚未完成批准")

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
                "finance_evidence_review_required",
                "资产收购 run 的证据绑定已失效、待复核或偏离批准快照",
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
            from lvke_mcp.servers.lvke_asset_acquisition.backend import _bind_spec_evidence

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
                "资产收购 run 的服务端证据绑定未批准、已失效或与运行快照不一致",
                expected_binding_hash=run.get("evidence_binding_hash"),
                current_binding_hash=current_evidence.get("binding_hash"),
                evidence_status=current_evidence.get("status"),
                issues=[
                    *(current_evidence.get("invalid") or []),
                    *(current_evidence.get("missing") or []),
                    *(current_evidence.get("pending") or []),
                ],
            )

    reference_review = run.get("reference_review") or {}
    if (
        run.get("reference_review_status") != "approved"
        or reference_review.get("status") != "approved"
        or not str(reference_review.get("actor") or "").strip()
        or not str(reference_review.get("reference_hash") or "").strip()
    ):
        block("finance_reference_review_required", "资产收购参考轨尚未完成可追溯批准")

    business_review = run.get("business_review") or {}
    if (
        run.get("business_review_status") != "approved"
        or business_review.get("status") != "approved"
        or not str(business_review.get("actor") or "").strip()
    ):
        block("finance_business_review_required", "资产收购业务差异尚未批准")

    if isinstance(spec, dict):
        max_price_ok, max_price_error, max_price_details = acquisition_service._max_price_gate(  # noqa: SLF001
            run, spec, require_business_decision=True,
        )
        if not max_price_ok:
            block(
                "finance_max_price_review_required",
                "最高可接受收购价尚未按正式阈值求解并完成业务批准",
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
        block("finance_acquisition_artifact_missing", "批准 run 尚无成功的正式收购工件")
    elif not valid_artifacts:
        block(
            "finance_artifact_mismatch",
            "正式收购工件的哈希、绑定或数字一致性校验不完整",
            artifacts=artifact_failures,
        )

    bound_tables_package_id = str(binding.get("acquisition_tables_package_id") or "")
    valid_tables_package: dict[str, Any] = {}
    if run.get("model_version") == "acquisition_model.v3":
        try:
            from lvke_mcp.servers.lvke_asset_acquisition.tables import get_package_record

            table_record = (
                get_package_record(workspace_id, bound_tables_package_id)
                if bound_tables_package_id
                else None
            )
        except Exception as exc:  # noqa: BLE001
            table_record = None
            block(
                "finance_acquisition_tables_unverifiable",
                f"无法验证收购十三表 package：{type(exc).__name__}",
            )
        if not bound_tables_package_id:
            block(
                "finance_acquisition_tables_missing",
                "月度 v3 正式发布必须绑定收购十三表 package",
            )
        elif table_record is None:
            block(
                "finance_acquisition_tables_not_found",
                "finance_binding 指向的收购十三表 package 不存在",
                package_id=bound_tables_package_id,
            )
        else:
            table_payload = table_record.get("payload") or {}
            integrity = table_payload.get("integrity") or {}
            table_mismatches = [
                {"field": field, "expected": run.get(field), "actual": table_payload.get(field)}
                for field in (
                    "run_id", "spec_hash", "input_hash", "model_version",
                    "evidence_binding_hash",
                )
                if table_payload.get(field) != run.get(field)
            ]
            if (
                integrity.get("status") != "passed"
                or int(integrity.get("required_table_count") or 0) != 13
                or int(integrity.get("manifest_count") or 0) != 13
                or table_mismatches
            ):
                block(
                    "finance_acquisition_tables_mismatch",
                    "收购十三表完整性或 run/spec/input/model/evidence 绑定不一致",
                    package_id=bound_tables_package_id,
                    integrity=integrity,
                    mismatches=table_mismatches,
                )
            else:
                valid_tables_package = table_record

    bound_artifact_id = str(binding.get("artifact_id") or "")
    valid_ids = {str(item.get("artifact_id") or "") for item in valid_artifacts}
    if not bound_artifact_id:
        item = {
            "code": "finance_artifact_binding_missing",
            "message": "finance_binding 未绑定成功的正式收购工件",
        }
        (blockers if strict else warnings).append(item)
    elif bound_artifact_id not in valid_ids:
        block(
            "finance_artifact_binding_stale",
            "finance_binding 指向的工件不存在、未成功或完整性校验失败",
            artifact_id=bound_artifact_id,
        )

    expected_binding = {
        "finance_run_id": run_id,
        "spec_hash": run.get("spec_hash"),
        "spec_id": spec_id,
        "fact_revision": spec_id,
        "spec_snapshot_hash": run.get("spec_snapshot_hash"),
        "evidence_binding_version": run.get("evidence_binding_version"),
        "evidence_binding_hash": run.get("evidence_binding_hash"),
        "input_hash": run.get("input_hash"),
        "model_version": run.get("model_version"),
        "review_status": "approved",
        "binding_kind": "asset_acquisition",
    }
    if run.get("model_version") == "acquisition_model.v3":
        expected_binding.update({
            "acquisition_tables_package_id": bound_tables_package_id,
            "acquisition_tables_basis_hash": valid_tables_package.get("basis_hash"),
        })
    bound_artifact = next(
        (
            item for item in valid_artifacts
            if str(item.get("artifact_id") or "") == bound_artifact_id
        ),
        {},
    )
    if bound_artifact:
        expected_binding.update({
            "artifact_status": "succeeded",
            "template_version": bound_artifact.get("template_version"),
            "report_data_hash": bound_artifact.get("report_data_hash"),
        })
    binding_mismatches = [
        {"field": field, "expected": expected, "actual": binding.get(field)}
        for field, expected in expected_binding.items()
        if binding.get(field) != expected
    ]
    if binding_mismatches:
        block(
            "finance_binding_stale",
            "正式报告绑定的 run、Spec 或事实版本与批准 run 不一致",
            mismatches=binding_mismatches,
        )

    approved = run_id if run.get("review_status") == "approved" else None
    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "bound_run_id": run_id,
        "approved_run_id": approved,
        "binding": binding,
        "artifact_id": bound_artifact_id or None,
        "acquisition_tables_package_id": bound_tables_package_id or None,
        "valid_artifact_ids": sorted(valid_ids),
        "gate_type": "asset_acquisition",
        "assurance_level": "review_grade" if not blockers else "estimate_preview",
    }


def assert_publish_finance_binding(
    workspace_id: str,
    *,
    expected_run_id: str = "",
    strict: bool = True,
) -> dict[str, Any]:
    """正式发布门禁：正文绑定 run 必须存在且等于最新 approved run。

    Returns:
        {
          ok, blockers: [{code, message}], warnings: [...],
          bound_run_id, approved_run_id, binding
        }
    """
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    binding = _load_binding(workspace_id)
    actual_bound = str(binding.get("finance_run_id") or "") if isinstance(binding, dict) else ""
    expected = str(expected_run_id or "").strip()
    bound = expected or actual_bound
    if expected and actual_bound != expected:
        blockers.append({
            "code": "finance_binding_revision_mismatch",
            "message": "报告修订绑定的财务 run 与工作区当前绑定不一致，拒绝静默切换",
            "details": {
                "expected_run_id": expected,
                "actual_run_id": actual_bound or None,
            },
        })

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

    approved_id = ""
    approved_view: dict[str, Any] = {}
    try:
        from lvke_mcp.domains.finance import run_store

        approved_view = run_store.get_approved_run(workspace_id) or {}
        approved_id = str(approved_view.get("run_id") or "")
        au = {"has_run": bool(run_store.latest_run(workspace_id))}
    except Exception:  # noqa: BLE001
        au = {}
        approved_view = {}

    if not au.get("has_run"):
        blockers.append({
            "code": "audit_no_run",
            "message": "无财务审计 run，终稿数字不可追溯",
        })
    if not approved_id:
        blockers.append({
            "code": "finance_run_not_approved",
            "message": "财务测算尚未批准，终稿不得发布",
        })
    else:
        if not bound:
            msg = {
                "code": "finance_binding_missing",
                "message": "报告未绑定 finance_run_id；须在生成财务章时绑定已批准 run",
            }
            (blockers if strict else warnings).append(msg)
        elif bound != approved_id:
            blockers.append({
                "code": "finance_binding_stale",
                "message": (
                    f"正文绑定 run ({bound}) 与当前已批准 run ({approved_id}) 不一致，"
                    "须用批准 run 重新装配正文/13 表后发布"
                ),
            })
        # approved 自身勾稽
        if approved_view and not approved_view.get("consistency_ok"):
            blockers.append({
                "code": "approved_run_consistency_failed",
                "message": "已批准 run 勾稽未通过，禁止发布",
            })
        try:
            from lvke_mcp.domains.finance import run_store, table_render
            from lvke_mcp.domains.finance.model_manifest import (
                DEFAULT_SPEC_SCHEMA_VERSION,
                manifest_from_dict,
            )

            snapshot = run_store.load_result_snapshot(workspace_id, approved_id) or {}

            manifest_data = snapshot.get("model_manifest") or {}
            if not manifest_data:
                blockers.append({
                    "code": "finance_manifest_missing",
                    "message": "已批准 run 缺少完整 ModelManifest，无法证明模型、政策、行业和门禁版本",
                })
            else:
                manifest = manifest_from_dict(manifest_data)
                if not snapshot.get("valuation_date"):
                    blockers.append({
                        "code": "finance_valuation_date_missing",
                        "message": "已批准 run 缺少 valuation_date，无法复现政策有效期与审计口径",
                    })
                if manifest.spec_schema_version != DEFAULT_SPEC_SCHEMA_VERSION:
                    blockers.append({
                        "code": "finance_spec_schema_version_stale",
                        "message": (
                            "已批准 run 的 FinanceSpec schema 版本过旧："
                            f"{manifest.spec_schema_version}，当前要求 {DEFAULT_SPEC_SCHEMA_VERSION}"
                        ),
                    })
                manifest_errors = manifest.validate(as_of=snapshot.get("valuation_date"))
                if manifest_errors:
                    blockers.append({
                        "code": "finance_manifest_invalid",
                        "message": f"已批准 run 的 ModelManifest 无效：{manifest_errors}",
                    })
                if snapshot.get("manifest_hash") != manifest.hash:
                    blockers.append({
                        "code": "finance_manifest_hash_mismatch",
                        "message": "已批准 run 的 manifest_hash 与 ModelManifest 内容不一致",
                    })
            quality = (table_render.build_all_structured(snapshot).get("_meta") or {}) if snapshot else {}
            if not quality.get("formal_delivery_ready"):
                blockers.append({
                    "code": "finance_formal_delivery_incomplete",
                    "message": (
                        "财务附表尚未达到正式交付条件："
                        f"有效 {quality.get('effective_table_count', 0)}/{quality.get('required_table_count', 13)} 张；"
                        f"缺口={quality.get('missing_fields_by_table') or {}}；"
                        f"无效表={quality.get('ineffective_tables') or []}"
                    ),
                })
            from lvke_mcp.domains.finance import table_pack

            xlsx = table_pack.default_artifact_dir(
                workspace_id,
                approved_id,
            ) / "财务专业附表.xlsx"
            if not xlsx.is_file():
                blockers.append({
                    "code": "finance_xlsx_missing",
                    "message": "已批准 run 缺少财务专业附表.xlsx，正式发布必须生成 13-sheet Excel",
                })
            evidence_path = table_pack.default_artifact_dir(
                workspace_id,
                approved_id,
            ) / "evidence.json"
            if evidence_path.is_file():
                import json

                delivery_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                if not delivery_evidence.get("formal_delivery_ready"):
                    blockers.append({
                        "code": "finance_excel_semantic_checks_failed",
                        "message": (
                            "Excel 深度表格审查未通过："
                            f"{delivery_evidence.get('semantic_blockers') or []}"
                        ),
                    })
            elif xlsx.is_file():
                blockers.append({
                    "code": "finance_delivery_evidence_missing",
                    "message": "存在 Excel 但缺少 evidence.json，无法证明深度表格审查通过",
                })
        except Exception as exc:  # noqa: BLE001
            blockers.append({
                "code": "finance_formal_delivery_unverifiable",
                "message": f"无法验证财务正式交付完整性：{type(exc).__name__}",
            })
        open_blocking = [
            i for i in (approved_view.get("issues") or [])
            if i.get("status") == "open" and i.get("blocking")
        ]
        if open_blocking:
            blockers.append({
                "code": "finance_blocking_issues",
                "message": f"已批准 run 仍有 {len(open_blocking)} 个未关闭阻断问题",
            })

    # 批准 run 被 supersede 的情况（get_approved 不会返回 superseded，但 binding 可能旧）
    if bound and approved_id and bound == approved_id:
        try:
            from lvke_mcp.domains.finance import run_store

            view = run_store.load_run(workspace_id, bound) or {}
            if (view.get("review_status") or "") == "superseded":
                blockers.append({
                    "code": "finance_run_superseded",
                    "message": "绑定 run 已因输入变更过期(superseded)，须重新测算并批准",
                })
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "bound_run_id": bound or None,
        "actual_bound_run_id": actual_bound or None,
        "approved_run_id": approved_id or None,
        "binding": binding,
        "assurance_level": "review_grade" if (not blockers and approved_id) else "estimate_preview",
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
        from lvke_mcp.servers.lvke_asset_acquisition import backend as acquisition_service

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
