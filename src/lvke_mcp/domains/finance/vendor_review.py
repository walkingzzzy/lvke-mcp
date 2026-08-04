"""End-to-end orchestration for vendor workbook import and review."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Optional

from lvke_mcp.domains.finance.run_store import DEFAULT_TENANT_ID


def import_vendor_workbook_review(
    workspace_id: str,
    xlsx_path: str | Path,
    *,
    valuation_date: str = "",
    force_recompute: bool = False,
    cohort_xlsx_paths: Optional[list[str | Path]] = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Run M1-M6 inside one tenant-scoped finance audit partition."""

    return _import_vendor_workbook_review(
        workspace_id,
        xlsx_path,
        valuation_date=valuation_date,
        force_recompute=force_recompute,
        cohort_xlsx_paths=cohort_xlsx_paths,
        tenant_id=tenant_id,
    )


def _import_vendor_workbook_review(
    workspace_id: str,
    xlsx_path: str | Path,
    *,
    valuation_date: str,
    force_recompute: bool,
    cohort_xlsx_paths: Optional[list[str | Path]],
    tenant_id: str,
) -> dict[str, Any]:
    """MCP 单租户版：run_store.tenant_scope 为 no-op，直接执行。"""

    from lvke_mcp.domains.finance import (
        dual_track,
        reference_track,
        review_verdict,
        run_service,
        run_store,
        vendor_import,
        vendor_review_report,
    )

    if not workspace_id or not str(workspace_id).strip():
        raise ValueError("workspace_id 必填")
    reference_pack = vendor_import.build_reference_pack(xlsx_path)
    reference_record = run_store.record_vendor_reference(workspace_id, reference_pack)

    cohorts = []
    for cohort_path in cohort_xlsx_paths or []:
        candidate = Path(cohort_path).expanduser().resolve()
        if candidate == Path(xlsx_path).expanduser().resolve():
            continue
        cohorts.append(vendor_import.build_reference_pack(candidate))
    cleanup_findings = vendor_import.detect_cleanup_issues(
        reference_pack,
        cohort_reference_packs=cohorts,
    )
    finance_input = vendor_import.build_finance_input_from_vendor(reference_pack)
    finance_spec = vendor_import.build_vendor_finance_spec(reference_pack, finance_input)
    project_context = vendor_import.infer_vendor_project_context(reference_pack)
    effective_valuation_date = valuation_date or date.today().isoformat()

    run = run_service.run_workspace_finance_model(
        workspace_id,
        spec=finance_spec,
        input_revision=finance_input,
        input_revision_id=0,
        mode="review_candidate",
        force_recompute=force_recompute,
        record_audit=True,
        report_file=f"vendor_review/{reference_record['reference_id']}",
        section="甲方计算表导入复核",
        force_flat=False,
        allow_prepare_llm=False,
        valuation_date=effective_valuation_date,
        project_context=project_context,
        tenant_id=tenant_id,
    )
    comparison = (
        dual_track.compare_engine_to_reference(run, reference_pack)
        if run.get("available")
        else {"ok": False, "matched": [], "mismatched": [], "missing": [],
              "red_flags": [], "needs_explanation": [],
              "summary": {"matched": 0, "mismatched": 0, "missing": 0,
                          "red_flags": 0, "needs_explanation": 0}}
    )
    reference_replay = reference_track.replay_reference_track(reference_pack)
    amount_bridge = reference_track.build_corrected_track_bridge(run, reference_pack)
    verdict = review_verdict.build_verdict(run, cleanup_findings, comparison)
    issue_record = {"ok": False, "count": 0, "reason": "run_unavailable"}
    sheet_decisions: dict[str, Any] = {
        "formal_ok": False,
        "pending_sheets": list((reference_pack.get("sheets") or {}).keys()),
    }
    if run.get("run_id"):
        binding_record = run_store.bind_vendor_reference_run(
            workspace_id, reference_record["reference_id"], str(run["run_id"])
        )
        sheet_decisions = binding_record.get("sheet_decisions") or sheet_decisions

    # The sheet-decision blocker is part of the authoritative verdict.  Add it
    # before both persistence and Markdown rendering; adding it only to the
    # response projection afterwards made the saved review report incorrectly
    # appear free of this formal-delivery blocker.
    if (
        not sheet_decisions.get("formal_ok")
        and not any(
            issue.get("rule") == "vendor_sheet_decision_pending"
            for issue in verdict
        )
    ):
        verdict.append({
            "rule": "vendor_sheet_decision_pending",
            "severity": "high",
            "blocking": True,
            "detail": (
                "非空工作表尚未逐张完成 mapped/ignored 人工裁决："
                + "、".join(sheet_decisions.get("pending_sheets") or [])
            ),
        })

    if run.get("run_id"):
        issue_record = review_verdict.persist_verdict(
            workspace_id, str(run["run_id"]), verdict
        )
        # 参考轨复现状态（GOV-001）：超容差 red_flag → out_of_tolerance（阻断批准）；
        # 收敛 → converged；否则待人工复核。业务复核状态在人工裁决前保持 pending。
        red_flags = comparison.get("red_flags") or []
        needs_explanation = comparison.get("needs_explanation") or []
        if not sheet_decisions.get("formal_ok"):
            reference_status = "pending"
        elif red_flags:
            reference_status = "out_of_tolerance"
        elif needs_explanation:
            reference_status = "explain_pending"
        else:
            reference_status = "converged"
        run_store.set_reference_review_status(
            workspace_id, str(run["run_id"]), reference_status
        )
        run_store.set_business_review_status(
            workspace_id, str(run["run_id"]), "pending"
        )

    report_md = vendor_review_report.render_review_md(
        reference_pack, cleanup_findings, run, comparison, verdict,
        reference_replay=reference_replay, amount_bridge=amount_bridge,
    )
    report_path = vendor_review_report.write_review_md(
        vendor_review_report.default_report_path(workspace_id, reference_pack),
        report_md,
    )
    blocking = [issue for issue in verdict if issue.get("blocking")]
    source = reference_pack.get("source") or {}
    return {
        "ok": True,
        "available": bool(run.get("available")),
        "review_passed": not blocking and bool(run.get("available")),
        "workspace_id": workspace_id,
        "reference_id": reference_record["reference_id"],
        "reference_reused": bool(reference_record.get("reused")),
        "reference": {
            "source_type": "vendor_reference",
            "read_only": True,
            "reliability_grade": "C",
            "workbook_name": source.get("workbook_name"),
            "workbook_sha256": source.get("workbook_sha256"),
            "sheet_count": len(reference_pack.get("sheets") or {}),
            "mapped_sheet_count": sum(
                1 for mapping in (reference_pack.get("sheet_map") or {}).values()
                if mapping.get("mapped")
            ),
            "formula_status": reference_pack.get("formula_status"),
            "warnings": reference_pack.get("warnings") or [],
        },
        "run_id": run.get("run_id"),
        "calculation_status": run.get("calculation_status"),
        "missing_inputs": run.get("missing_inputs") or finance_input.get("_missing_inputs") or [],
        "valuation_date": effective_valuation_date,
        "indicators": run.get("indicators") or {},
        "capital_irr_pct": (run.get("annual") or {}).get("capital_irr_pct"),
        "table_manifest": run.get("table_manifest") or [],
        "cleanup_findings": cleanup_findings,
        "comparison": comparison,
        "reference_replay": reference_replay,
        "amount_bridge": amount_bridge,
        "sheet_decisions": sheet_decisions,
        "verdict": verdict,
        "blocking_issues": blocking,
        "issue_record": issue_record,
        "report_path": report_path,
    }
