"""Unified verdict rules for vendor-workbook financial reviews."""

from __future__ import annotations

from typing import Any


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _issue(
    rule: str,
    detail: str,
    *,
    severity: str = "warning",
    blocking: bool = False,
    source: str = "engine",
) -> dict[str, Any]:
    return {
        "rule": rule,
        "severity": severity,
        "blocking": bool(blocking),
        "ok": False,
        "status": "open",
        "source": source,
        "detail": detail,
    }


def build_verdict(
    run: dict[str, Any],
    cleanup_findings: list[dict[str, Any]],
    comparison: dict[str, Any],
) -> list[dict[str, Any]]:
    """Judge negative returns, debt insufficiency, cleanup issues and red flags."""
    verdict: list[dict[str, Any]] = []
    if not run or not run.get("available"):
        detail = str((run or {}).get("reason") or "甲方输入不足，无法由我方模型完成重算")
        verdict.append(_issue(
            "review_recalculation_unavailable",
            f"无法形成对外数字真源：{detail}",
            severity="high",
            blocking=True,
        ))
    else:
        indicators = run.get("indicators") or {}
        annual = run.get("annual") or {}
        project_irr = _number(indicators.get("project_irr_pct"))
        capital_irr = _number(annual.get("capital_irr_pct"))
        project_cashflows = [
            _number(value)
            for value in ((run.get("operating") or {}).get("cashflows") or [])
        ]
        project_cashflows = [value for value in project_cashflows if value is not None]
        capital_cashflows = [
            _number(row.get("net_cashflow"))
            for row in (annual.get("capital_cashflow") or [])
            if isinstance(row, dict)
        ]
        capital_cashflows = [value for value in capital_cashflows if value is not None]
        if project_irr is not None and project_irr < 0:
            verdict.append(_issue(
                "negative_project_irr",
                f"项目财务内部收益率为 {project_irr:.4f}%，项目层面亏损；如实呈现，不得粉饰",
                severity="high",
                blocking=True,
            ))
        elif project_irr is None and project_cashflows and max(project_cashflows) <= 0:
            verdict.append(_issue(
                "project_cashflow_never_positive",
                "项目全周期现金流均不为正，IRR不存在且投资无法回收；该状态比负IRR更严重",
                severity="high",
                blocking=True,
            ))
        if capital_irr is not None and capital_irr < 0:
            verdict.append(_issue(
                "negative_capital_irr",
                f"资本金财务内部收益率为 {capital_irr:.4f}%，股东层面亏损；如实呈现，不得粉饰",
                severity="high",
                blocking=True,
            ))
        elif capital_irr is None and capital_cashflows and max(capital_cashflows) <= 0:
            verdict.append(_issue(
                "capital_cashflow_never_positive",
                "资本金全周期现金流均不为正，资本金IRR不存在且股东投入无法回收",
                severity="high",
                blocking=True,
            ))

        debt_rows = annual.get("debt_service") or []
        for metric, label in (("icr", "ICR"), ("dscr", "DSCR")):
            insufficient = []
            for row in debt_rows:
                if not isinstance(row, dict):
                    continue
                value = _number(row.get(metric))
                if value is not None and value < 1.0:
                    insufficient.append((row.get("year"), value))
            if insufficient:
                rendered = "、".join(f"Y{year}={value:.4f}" for year, value in insufficient)
                verdict.append(_issue(
                    f"debt_service_{metric}_below_1",
                    f"{label}<1，偿债资金不足：{rendered}",
                    severity="high",
                    blocking=True,
                ))

    for finding in cleanup_findings or []:
        if not isinstance(finding, dict):
            continue
        code = str(finding.get("code") or "vendor_cleanup")
        locator = str(finding.get("locator") or "")
        detail = str(finding.get("detail") or "")
        suggestion = finding.get("engine_suggestion")
        suffix = f"；建议/引擎值={suggestion}" if suggestion not in (None, "") else ""
        verdict.append(_issue(
            f"vendor_cleanup_{code.lower()}",
            f"{locator}：{detail}{suffix}",
            severity=str(finding.get("severity") or ("high" if code in {"F1", "F2"} else "medium")),
            blocking=bool(finding.get("blocking", False)),
            source="vendor_reference",
        ))

    # 参考轨超容差必须形成 blocking finding，避免内部自洽但与甲方
    # 参考轨严重不符的 run 被标记为 validation passed。
    for item in comparison.get("red_flags") or []:
        if not isinstance(item, dict):
            continue
        deviation = item.get("deviation_pct")
        deviation_text = "基准为0" if deviation is None else f"偏差 {float(deviation):.2f}%"
        verdict.append(_issue(
            "reference_track_out_of_tolerance",
            (
                f"参考轨超容差（须裁决）：{item.get('locator')}：我方={item.get('engine_value')}，"
                f"甲方参考={item.get('ref_value')}，{deviation_text}"
            ),
            severity="high",
            blocking=True,
            source="dual_track",
        ))
    return verdict


def persist_verdict(
    workspace_id: str,
    run_id: str,
    verdict: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write verdict issues through the shared audit-db issue path."""
    from lvke_mcp.domains.finance import run_store

    return run_store.record_model_issues(workspace_id, run_id, verdict)
