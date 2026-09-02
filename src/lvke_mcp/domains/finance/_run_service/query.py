"""run 读取与审计视图。"""

from __future__ import annotations

from typing import Any


from .base import (
    TEMPLATE_VERSION,
    _ensure_workspace,
    _read_workspace_req,
    _table_manifest,
)


def get_workspace_finance_run(
    workspace_id: str,
    *,
    run_id: str = "",
    view: str = "summary",
) -> dict[str, Any]:
    """纯查询：不重算、默认不写库。

    view:
    - summary: 指标 + 状态
    - full: 完整 result 快照（若有）
    - tables: 仅 tables
    - checks: 勾稽 / issues
    """
    _ensure_workspace(workspace_id)
    from lvke_mcp.domains.finance import run_store

    if run_id:
        audit_view = run_store.load_run(workspace_id, run_id) or {}
    else:
        audit_view = run_store.latest_run(workspace_id) or {}

    if not audit_view:
        # 无 run：返回输入与 unavailable 提示（不触发计算）
        _meta, req, finance_raw = _read_workspace_req(workspace_id)
        return {
            "ok": True,
            "available": False,
            "workspace_id": workspace_id,
            "run_id": None,
            "reason": "no_finance_run",
            "message": "尚无财务模型运行记录；请 POST /finance-runs 或调用 finance_run_model",
            "finance_inputs": dict(finance_raw or {}),
            "calculation_status": "none",
            "assurance_level": "none",
        }

    rid = audit_view.get("run_id")
    snapshot = run_store.load_result_snapshot(workspace_id, rid) if rid else None
    base: dict[str, Any] = {
        "ok": True,
        "workspace_id": workspace_id,
        "run_id": rid,
        "model_version": audit_view.get("model_version"),
        "template_version": audit_view.get("template_version") or TEMPLATE_VERSION,
        "spec_id": audit_view.get("spec_id"),
        "spec_hash": audit_view.get("spec_hash"),
        "input_hash": audit_view.get("input_hash"),
        "input_revision_id": audit_view.get("input_revision"),
        "idempotency_key": audit_view.get("idempotency_key"),
        "table_bundle_hash": audit_view.get("table_bundle_hash"),
        "consistency_ok": bool(audit_view.get("consistency_ok")),
        "available": True,
    }

    if snapshot and isinstance(snapshot, dict):
        # 合并快照（快照优先数值，base 保留审计元数据）
        merged = dict(snapshot)
        merged.update({k: v for k, v in base.items() if v is not None})
        merged["available"] = bool(snapshot.get("available", True))
        if view == "tables":
            return {
                "ok": True,
                "run_id": rid,
                "tables": snapshot.get("tables") or {},
                "table_manifest": _table_manifest(snapshot, rid),
                "table_bundle_hash": base.get("table_bundle_hash"),
            }
        if view == "checks":
            consistency = audit_view.get("consistency") or []
            blocking_issues = list(audit_view.get("blocking_issues") or [])
            if not blocking_issues:
                blocking_issues = [
                    {
                        "rule": str(item.get("rule") or item.get("code") or "finance_consistency_failed"),
                        "detail": str(item.get("detail") or ""),
                        "blocking": True,
                    }
                    for item in consistency
                    if isinstance(item, dict)
                    and not item.get("ok")
                    and bool(item.get("blocking", True))
                ]
            return {
                "ok": True,
                "run_id": rid,
                "consistency_ok": base["consistency_ok"],
                "consistency": consistency,
                "blocking_issues": blocking_issues,
                "issues": audit_view.get("issues") or [],
                "checks": snapshot.get("checks") or [],
            }
        if view == "summary":
            snapshot_meta = snapshot.get("project_metadata") if isinstance(snapshot.get("project_metadata"), dict) else {}
            return {
                **base,
                "indicators": snapshot.get("indicators") or {},
                "investment": snapshot.get("investment") or {},
                "funding": snapshot.get("funding") or {},
                "summary_md": snapshot.get("summary_md") or "",
                "missing_inputs": snapshot.get("missing_inputs") or [],
                "table_manifest": _table_manifest(snapshot, rid),
                "assurance_level": snapshot.get("assurance_level") or "estimate_preview",
                "calculation_status": "computed",
                "project_metadata": snapshot_meta,
                "project_type": snapshot.get("project_type") or snapshot_meta.get("project_type"),
                "industry": snapshot.get("industry") or snapshot_meta.get("industry") or audit_view.get("industry"),
                "valuation_date": snapshot.get("valuation_date") or snapshot_meta.get("valuation_date") or audit_view.get("valuation_date"),
                "currency": snapshot.get("currency") or snapshot_meta.get("currency"),
                "amount_unit": snapshot.get("amount_unit") or snapshot_meta.get("amount_unit"),
                "tax_basis": snapshot.get("tax_basis") or snapshot_meta.get("tax_basis"),
                "forecast_period": snapshot.get("forecast_period") or snapshot_meta.get("forecast_period"),
            }
        # full
        merged["audit"] = {
            "issues": audit_view.get("issues") or [],
            "consistency": audit_view.get("consistency") or [],
            "report_mappings": audit_view.get("report_mappings") or [],
        }
        # Do not preserve a pre-allocation manifest with empty child run_ids.
        merged["table_manifest"] = _table_manifest(merged, rid)
        return merged

    # 无快照：仅返回审计摘要
    if view == "checks":
        consistency = audit_view.get("consistency") or []
        blocking_issues = list(audit_view.get("blocking_issues") or [])
        if not blocking_issues:
            blocking_issues = [
                {
                    "rule": str(item.get("rule") or item.get("code") or "finance_consistency_failed"),
                    "detail": str(item.get("detail") or ""),
                    "blocking": True,
                }
                for item in consistency
                if isinstance(item, dict)
                and not item.get("ok")
                and bool(item.get("blocking", True))
            ]
        return {
            "ok": True,
            "run_id": rid,
            "consistency_ok": base["consistency_ok"],
            "consistency": consistency,
            "blocking_issues": blocking_issues,
            "issues": audit_view.get("issues") or [],
        }
    # 从 results 重建粗指标
    indicators = {}
    investment = {}
    funding = {}
    for r in audit_view.get("results") or []:
        code = r.get("element_code")
        val = r.get("value")
        if code in {"project_irr", "npv", "static_payback", "dynamic_payback",
                    "annual_revenue", "annual_net_profit", "annual_total_cost", "bep"}:
            indicators[code] = val
        elif code in {"total_investment", "construction_investment", "working_capital",
                      "interest_during_construction", "fixed_asset"}:
            investment[code] = val
        elif code in {"equity_capital", "loan", "subsidy"}:
            funding[code] = val
    return {
        **base,
        "indicators": indicators,
        "investment": investment,
        "funding": funding,
        "snapshot_missing": True,
        "message": "历史 run 无完整 result 快照；请重新 POST /finance-runs 生成可重放包",
    }
