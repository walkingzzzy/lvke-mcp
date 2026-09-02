"""Consistency / release checks facade (P0-6)."""

from __future__ import annotations

from typing import Any, Callable, Optional


def run_checks(
    result: dict[str, Any],
    *,
    engine_check: Optional[Callable[[dict[str, Any]], list[dict[str, Any]]]] = None,
) -> list[dict[str, Any]]:
    """Merge engine check_consistency with module-level release checks."""
    checks: list[dict[str, Any]] = []
    if engine_check is not None:
        try:
            checks.extend(engine_check(result) or [])
        except Exception:  # noqa: BLE001
            checks.append({"rule": "engine_check_consistency", "ok": False, "detail": "引擎勾稽执行失败"})

    inv = result.get("investment") or {}
    scope = inv.get("scope_status") or {}
    if scope.get("status") == "ambiguous":
        checks.append(
            {
                "rule": "投资口径无歧义",
                "category": "delivery",
                "ok": False,
                "detail": scope.get("reason") or "LEGACY_INVESTMENT_SCOPE_AMBIGUOUS",
                "blocking": True,
            }
        )
    elif scope.get("status") == "clear":
        checks.append({"rule": "投资口径无歧义", "category": "delivery", "ok": True, "detail": scope.get("reason") or "clear"})

    # Timeline present
    tl = result.get("timeline") or {}
    if tl:
        ok_tl = int(tl.get("calc_years") or 0) == int((result.get("params") or {}).get("calc_years") or 0)
        checks.append(
            {
                "rule": "统一时间轴期间数=计算期",
                "category": "integrity",
                "ok": ok_tl,
                "detail": f"timeline.calc_years={tl.get('calc_years')} params={((result.get('params') or {}).get('calc_years'))}",
            }
        )

    # WC method honesty
    wc = (result.get("annual") or {}).get("working_capital") or {}
    if wc.get("method") == "ratio_backsolve":
        checks.append(
            {
                "rule": "流动资金分项为业务周转驱动",
                "category": "integrity",
                "ok": False,
                "detail": "当前为汇总反解分项（ratio_backsolve），不得标分项法完成",
                "blocking": False,
            }
        )
    elif wc.get("method") == "turnover_days":
        checks.append(
            {
                "rule": "流动资金分项为业务周转驱动",
                "category": "integrity",
                "ok": True,
                "detail": f"turnover_days overall={((wc.get('days') or {}).get('overall'))}",
            }
        )

    # Funding gap any year
    # 键名以运行时真正的生产者 annual._build_financial_plan 为准（它写 "gap"）。
    # 曾误读同语义死代码 statements.financial_plan_rows 的 "funding_gap"，
    # 导致本检查恒 ok=true（真缺口年也报「缺口年数 0/N」）。死代码已删除。
    fp = (result.get("annual") or {}).get("financial_plan") or []
    if fp:
        gaps = [x for x in fp if x.get("gap")]
        checks.append(
            {
                "rule": "财务计划无资金缺口年",
                "category": "viability",
                "ok": len(gaps) == 0,
                "detail": f"缺口年数 {len(gaps)}/{len(fp)}",
                "blocking": False,
            }
        )

    # 附表4（资金筹措）↔ 附表11（财务计划）建设期融资口径。
    # 这两张交付表对同一口径各自成文，此前无任何一条勾稽比对它们：曾出现
    # 附表4 资本金 11200/贷款 16800，而附表11 资本金 26200/贷款 0，两表
    # 同时"自洽"、单表校验均 valid=true、整包 22 条勾稽全部沉默。
    _fund = result.get("funding") or {}
    _build_rows = [row for row in (fp or []) if str(row.get("phase") or "") == "建设期"]
    if _build_rows and _fund:
        plan_equity = round(sum(float(row.get("capital_own") or 0.0) for row in _build_rows), 2)
        plan_loan = round(sum(float(row.get("loan_draw") or 0.0) for row in _build_rows), 2)
        fund_equity = round(float(_fund.get("capital") or 0.0), 2)
        fund_loan = round(float(_fund.get("loan") or 0.0), 2)
        equity_ok = abs(plan_equity - fund_equity) <= 0.05
        loan_ok = abs(plan_loan - fund_loan) <= 0.05
        checks.append(
            {
                "rule": "附表11建设期融资结构=附表4资金筹措",
                "category": "integrity",
                "ok": equity_ok and loan_ok,
                "detail": (
                    f"资本金 附表11={plan_equity:,.2f} vs 附表4={fund_equity:,.2f}；"
                    f"贷款 附表11={plan_loan:,.2f} vs 附表4={fund_loan:,.2f} 万元"
                ),
                "blocking": True,
            }
        )

    # Non-operating balance
    nob = result.get("non_operating_balance") or {}
    if nob:
        checks.append(
            {
                "rule": "非经营性项目生命周期资金平衡",
                "category": "viability",
                "ok": bool(nob.get("balanced")),
                "detail": f"lifecycle_gap={nob.get('lifecycle_gap')}",
                "blocking": False,
            }
        )

    # Debt ICR when present
    ds = (result.get("annual") or {}).get("debt_service") or []
    if ds and any(x.get("icr") is not None for x in ds):
        weak = [x for x in ds if x.get("icr") is not None and x["icr"] < 1.0]
        checks.append(
            {
                "rule": "利息备付率ICR>=1",
                "category": "viability",
                "ok": len(weak) == 0,
                "detail": f"ICR<1 年数 {len(weak)}",
                "blocking": False,
            }
        )

    return checks


def release_blockers(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rules that must pass for final publish (方案 §9.4)."""
    blockers = []
    for c in checks:
        if c.get("ok"):
            continue
        if c.get("blocking") or c.get("rule") in {
            "资金筹措合计=总投资",
            "现金流表IRR=技经指标IRR",
            "附表7利润表总成本=附表6总成本(含息)",
            "附表9组成合计=净现金流",
            "投资口径无歧义",
            "附表11建设期融资结构=附表4资金筹措",
        }:
            blockers.append(c)
    return blockers
