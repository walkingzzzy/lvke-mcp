"""Statement builders helpers (P0-4/P0-5/P1-6)."""

from __future__ import annotations

from typing import Any, Optional


def project_cashflow_rows(
    *,
    build_years: int,
    op_years: int,
    construction_interest_annual: list[float],
    op_rows: list[dict[str, Any]],
    working_capital: float,
    terminal_recovery: float,
) -> list[dict[str, Any]]:
    """附表9 项目投资现金流逐行（融资前，不含借款/还本/融资利息）。"""
    rows: list[dict[str, Any]] = []
    cum = 0.0
    for t in range(build_years):
        constr = float(construction_interest_annual[t] if t < len(construction_interest_annual) else 0.0)
        net = -constr
        cum = round(cum + net, 2)
        rows.append(
            {
                "year": t,
                "phase": "建设期",
                "revenue": 0.0,
                "op_cash_cost": 0.0,
                "tax_surtax": 0.0,
                "income_tax": 0.0,
                "construction": round(constr, 2),
                "wc_change": 0.0,
                "recover": 0.0,
                "net_cashflow": round(net, 2),
                "cum_cashflow": cum,
            }
        )
    for j in range(op_years):
        ir = op_rows[j] if j < len(op_rows) else {}
        rev = float(ir.get("revenue") or 0.0)
        occ = float(ir.get("op_cash_cost") or 0.0)
        tax = float(ir.get("tax_surtax") or ir.get("tax_surcharge") or 0.0)
        itax = float(ir.get("income_tax") or 0.0)
        wc = float(working_capital or 0.0) if j == 0 else 0.0
        rec = 0.0
        if j == op_years - 1:
            rec = float(working_capital or 0.0) + float(terminal_recovery or 0.0)
        net = round(rev - occ - tax - itax - wc + rec, 2)
        cum = round(cum + net, 2)
        rows.append(
            {
                "year": build_years + j,
                "phase": "运营期",
                "revenue": rev,
                "op_cash_cost": occ,
                "tax_surtax": tax,
                "income_tax": itax,
                "construction": 0.0,
                "wc_change": wc,
                "recover": rec,
                "net_cashflow": net,
                "cum_cashflow": cum,
            }
        )
    return rows


def capital_cashflow_rows(
    *,
    build_years: int,
    op_years: int,
    capital_outlays: list[float],
    loan_draws: list[float],
    op_inflows: list[float],
    debt_rows: list[dict[str, Any]],
    actual_tax_by_year: Optional[list[float]] = None,
) -> list[dict[str, Any]]:
    """附表10 资本金现金流：独立组成（资本金投入/经营流入/还本/付息），非仅净额变换。

    capital_outlays: 建设期各年资本金实际投入（正数表示股东出资额）
    loan_draws: 建设期贷款提款
    op_inflows: 运营期经营净流入（会计口径经营现金，通常=净利润+折旧摊销，或传入税后经营现金）
    """
    rows: list[dict[str, Any]] = []
    for t in range(build_years):
        cap = float(capital_outlays[t] if t < len(capital_outlays) else 0.0)
        draw = float(loan_draws[t] if t < len(loan_draws) else 0.0)
        # 股东现金流：−资本金投入（贷款提款不计入股东流入表的「经营」，仅减少应出资本金）
        net = round(-cap, 2)
        rows.append(
            {
                "year": t,
                "phase": "建设期",
                "capital_invest": round(cap, 2),
                "loan_draw": round(draw, 2),
                "op_inflow": 0.0,
                "principal": 0.0,
                "interest": 0.0,
                "actual_income_tax": 0.0,
                "net_cashflow": net,
            }
        )
    for j in range(op_years):
        d = debt_rows[j] if j < len(debt_rows) else {}
        principal = float(d.get("principal") or 0.0)
        interest = float(d.get("interest") or 0.0)
        op_in = float(op_inflows[j] if j < len(op_inflows) else 0.0)
        atax = float(actual_tax_by_year[j]) if actual_tax_by_year and j < len(actual_tax_by_year) else 0.0
        net = round(op_in - principal - interest, 2)
        rows.append(
            {
                "year": build_years + j,
                "phase": "运营期",
                "capital_invest": 0.0,
                "loan_draw": 0.0,
                "op_inflow": op_in,
                "principal": principal,
                "interest": interest,
                "actual_income_tax": atax,
                "net_cashflow": net,
            }
        )
    return rows


def financial_plan_rows(
    *,
    build_years: int,
    op_years: int,
    invest_out_build: list[float],
    finance_in_build: list[float],
    op_operating_net: list[float],
    wc_change_op: list[float],
    terminal_recover: float,
    debt_rows: list[dict[str, Any]],
    min_cash: float = 0.0,
) -> list[dict[str, Any]]:
    """财务计划现金流量表（投资/融资/经营 + 期末现金 + 缺口）。"""
    rows: list[dict[str, Any]] = []
    cash = 0.0
    cum = 0.0
    for t in range(build_years):
        inv = float(invest_out_build[t] if t < len(invest_out_build) else 0.0)
        fin = float(finance_in_build[t] if t < len(finance_in_build) else 0.0)
        # 建设期：融资按需到位
        if fin <= 0 and inv > 0:
            fin = inv
        net = round(fin - inv, 2)
        cash = round(cash + net, 2)
        cum = round(cum + net, 2)
        gap = cash < min_cash
        rows.append(
            {
                "year": t,
                "phase": "建设期",
                "operating_net": 0.0,
                "invest_out": round(inv, 2),
                "finance_in": round(fin, 2),
                "debt_service": 0.0,
                "net_cashflow": net,
                "cash_end": cash,
                "cum_surplus": cum,
                "funding_gap": gap,
                "min_cash": min_cash,
            }
        )
    for j in range(op_years):
        d = debt_rows[j] if j < len(debt_rows) else {}
        ds = round(float(d.get("principal") or 0.0) + float(d.get("interest") or 0.0), 2)
        opn = float(op_operating_net[j] if j < len(op_operating_net) else 0.0)
        wcc = float(wc_change_op[j] if j < len(wc_change_op) else 0.0)
        rec = float(terminal_recover or 0.0) if j == op_years - 1 else 0.0
        # invest_out 正=净流出；回收为负流出
        invest_out = round(wcc - rec, 2)
        finance_in = 0.0
        net = round(opn + finance_in - invest_out - ds, 2)
        cash = round(cash + net, 2)
        cum = round(cum + net, 2)
        gap = cash < min_cash
        rows.append(
            {
                "year": build_years + j,
                "phase": "运营期",
                "operating_net": round(opn, 2),
                "invest_out": invest_out,
                "finance_in": finance_in,
                "debt_service": ds,
                "net_cashflow": net,
                "cash_end": cash,
                "cum_surplus": cum,
                "funding_gap": gap,
                "min_cash": min_cash,
            }
        )
    return rows


def non_operating_funding_balance(
    *,
    total_investment: float,
    capital: float,
    loan: float,
    subsidy: float,
    annual_opex: float = 0.0,
    annual_subsidy: float = 0.0,
    calc_years: int = 1,
    build_years: int = 1,
    debt_service: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """非经营性项目全生命周期资金平衡（P1-6）。"""
    total_investment = float(total_investment or 0.0)
    sources = {
        "capital": float(capital or 0.0),
        "loan": float(loan or 0.0),
        "subsidy": float(subsidy or 0.0),
        "annual_subsidy_total": round(float(annual_subsidy or 0.0) * max(int(calc_years) - int(build_years), 0), 2),
    }
    sources_total = round(sum(sources.values()), 2)
    opex_total = round(float(annual_opex or 0.0) * max(int(calc_years) - int(build_years), 0), 2)
    debt_rows = debt_service or []
    debt_service_total = round(sum(
        float(row.get("principal") or 0.0) + float(row.get("interest") or 0.0)
        for row in debt_rows
    ), 2)
    uses = {
        "investment": total_investment,
        "opex_total": opex_total,
        "debt_service_total": debt_service_total,
    }
    uses_total = round(sum(uses.values()), 2)
    gap = round(uses_total - sources_total, 2)
    yearly = []
    upfront_sources = round(
        float(capital or 0.0) + float(loan or 0.0) + float(subsidy or 0.0), 2
    )
    for t in range(max(int(calc_years), 1)):
        if t < build_years:
            use = round(total_investment / max(build_years, 1), 2)
            src = round(upfront_sources / max(build_years, 1), 2) if upfront_sources else 0.0
        else:
            debt_index = t - build_years
            debt_due = 0.0
            if debt_index < len(debt_rows):
                debt_due = (
                    float(debt_rows[debt_index].get("principal") or 0.0)
                    + float(debt_rows[debt_index].get("interest") or 0.0)
                )
            use = float(annual_opex or 0.0) + debt_due
            src = float(annual_subsidy or 0.0)
        yearly.append(
            {
                "year": t,
                "phase": "建设期" if t < build_years else "运营期",
                "source": round(src, 2),
                "use": round(use, 2),
                "gap": round(use - src, 2),
            }
        )
    summary_md = (
        f"资金来源合计 {sources_total:.2f} 万元；资金运用合计 {uses_total:.2f} 万元；"
        f"生命周期缺口 {gap:.2f} 万元；"
        f"{'基本平衡' if gap <= 1.0 else '存在资金缺口，需开源节流或调整筹资'}。"
    )
    return {
        "sources": sources,
        "uses": uses,
        "sources_total": sources_total,
        "uses_total": uses_total,
        "lifecycle_gap": gap,
        "balanced": gap <= 1.0,
        "yearly": yearly,
        "summary_md": summary_md,
    }
