"""Finance-basis reconciliation: working capital, investment, funding, revenue."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


from .assumptions import _field_values


def _sync_working_capital(
    finance: dict[str, Any],
    *,
    base_revenue: float,
    target_revenue: float,
) -> float:
    """Keep turnover detail and stated investment working capital in one lineage."""

    if base_revenue <= 0 or target_revenue < 0:
        return float(finance.get("invest_breakdown", {}).get("working_capital_wan") or 0.0)
    ratio = target_revenue / base_revenue
    turnover = finance.get("wc_turnover")
    breakdown = finance.get("invest_breakdown")
    if not isinstance(breakdown, dict):
        return 0.0
    working_capital = round(float(breakdown.get("working_capital_wan") or 0.0) * ratio, 2)
    breakdown["working_capital_wan"] = working_capital
    working_series = finance.get("working_capital_by_year")
    if isinstance(working_series, list):
        finance["working_capital_by_year"] = [
            round(float(value) * ratio, 2) if isinstance(value, (int, float)) else value
            for value in working_series
        ]
    from lvke_mcp.domains.finance.working_capital import estimate_from_turnover

    cost_items = finance.get("cost_items")
    cash_cost = (
        sum(float(value) for value in cost_items.values() if isinstance(value, (int, float)))
        if isinstance(cost_items, dict)
        else 0.0
    )
    computed = estimate_from_turnover(
        revenue=target_revenue,
        cash_cost=cash_cost,
        turnover=turnover if isinstance(turnover, dict) else {},
    )
    working_capital = round(float(computed.get("total") or working_capital), 2)
    breakdown["working_capital_wan"] = working_capital
    if isinstance(turnover, dict):
        turnover["self_funded_wan"] = working_capital
    return working_capital


def _scale_investment_breakdown(
    breakdown: dict[str, Any],
    *,
    base_construction: float,
    target_construction: float,
) -> None:
    if base_construction <= 0 or target_construction <= 0:
        return
    ratio = target_construction / base_construction
    for key in ("construction_wan", "other_wan", "reserve_wan"):
        if isinstance(breakdown.get(key), (int, float)):
            breakdown[key] = round(float(breakdown[key]) * ratio, 2)
    for key in ("construction_detail", "other_detail", "contingency_detail"):
        detail = breakdown.get(key)
        if isinstance(detail, dict):
            for name, value in detail.items():
                if isinstance(value, (int, float)):
                    detail[name] = round(float(value) * ratio, 2)
    items = breakdown.get("construction_items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("amount_wan"), (int, float)):
                item["amount_wan"] = round(float(item["amount_wan"]) * ratio, 2)
            if isinstance(item.get("indicator_yuan"), (int, float)):
                item["indicator_yuan"] = round(float(item["indicator_yuan"]) * ratio, 2)


def _reconcile_funding(
    finance: dict[str, Any],
    *,
    target_total: float,
    base_total: float,
    working_capital: float,
    build_months: int,
    values: dict[str, Any],
) -> None:
    from lvke_mcp.domains.finance.industry_scenario_factory import _funding

    breakdown = finance.get("invest_breakdown")
    if not isinstance(breakdown, dict):
        return
    construction = float(breakdown.get("construction_wan") or 0.0)
    if construction <= 0:
        return
    if abs(target_total - base_total) > 0.005:
        target_construction = max(target_total - working_capital, 0.0)
        for _ in range(8):
            projected_total, *_ = _funding(
                target_construction,
                working_capital,
                float(values.get("loan_ratio", finance.get("loan_ratio") or 0.0)),
                float(values.get("loan_rate", finance.get("loan_rate") or 0.0)),
                max(1, (build_months + 11) // 12),
                0.0,
            )
            correction = target_total - projected_total
            if abs(correction) <= 0.005:
                break
            target_construction = max(round(target_construction + correction, 2), 0.0)
        if target_construction <= 0:
            return
        _scale_investment_breakdown(
            breakdown,
            base_construction=construction,
            target_construction=target_construction,
        )
        construction = round(target_construction, 2)
    loan_ratio = float(values.get("loan_ratio", finance.get("loan_ratio") or 0.0))
    loan_rate = float(values.get("loan_rate", finance.get("loan_rate") or 0.0))
    build_years = max(1, (build_months + 11) // 12)
    subsidy_ratio = (
        float(finance.get("gov_subsidy_wan") or 0.0) / max(target_total, 1.0)
    )
    total, capital, loan, subsidy, interest = _funding(
        construction,
        working_capital,
        loan_ratio,
        loan_rate,
        build_years,
        subsidy_ratio,
    )
    finance.update(
        {
            "total_investment_wan": total,
            "capital_own_wan": capital,
            "loan_wan": loan,
            "gov_subsidy_wan": subsidy,
            "loan_ratio": loan_ratio,
        }
    )
    breakdown["interest_wan"] = interest


def _apply_revenue_target(spec: dict[str, Any], old: float, target: float) -> None:
    revenue = spec.get("revenue") if isinstance(spec.get("revenue"), dict) else {}
    if old <= 0 or target < 0:
        return
    ratio = target / old
    model = str(revenue.get("model") or "")
    if model == "tourism" and isinstance(revenue.get("annual_visitors"), (int, float)):
        revenue["annual_visitors"] = round(float(revenue["annual_visitors"]) * ratio, 6)
    elif model == "product_sales" and isinstance(revenue.get("products"), list):
        for product in revenue["products"]:
            if isinstance(product, dict) and isinstance(product.get("capacity"), (int, float)):
                product["capacity"] = round(float(product["capacity"]) * ratio, 6)
    elif model == "property_sales" and isinstance(revenue.get("saleable_area"), (int, float)):
        revenue["saleable_area"] = round(float(revenue["saleable_area"]) * ratio, 6)
    elif model == "gov_payment":
        revenue["annual_gov_payment_wan"] = target
    else:
        revenue["annual_revenue_wan"] = target


def _effective_revenue_target(spec: dict[str, Any], fallback: float) -> float:
    revenue = spec.get("revenue")
    if not isinstance(revenue, dict):
        return fallback
    try:
        from lvke_mcp.domains.finance import revenue_models

        expanded = revenue_models.expand({"revenue": revenue}, 20)
    except Exception:  # noqa: BLE001
        return fallback
    values = [
        float(item)
        for item in expanded.get("revenue_by_year") or []
        if isinstance(item, (int, float))
    ]
    return round(max(values), 2) if values else fallback


def _scenario_inputs(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios

    scenario_id = str(package["factory_scenario_id"])
    industry_code = scenario_id.split(".", 1)[0]
    scenario = next(
        item
        for item in build_industry_scenarios(industry_code)
        if item["scenario_id"] == scenario_id
    )
    spec = deepcopy(scenario["spec"])
    finance = deepcopy(scenario["finance"])
    values = _field_values(package)

    base_total = float(finance["total_investment_wan"])
    target_total = float(values.get("total_investment_wan", base_total))
    base_revenue = float(finance.get("annual_revenue_wan") or 0)
    base_effective_revenue = _effective_revenue_target(spec, base_revenue)
    target_revenue = float(values.get("annual_revenue_wan", base_revenue))
    _apply_revenue_target(spec, base_revenue, target_revenue)
    effective_revenue = _effective_revenue_target(spec, target_revenue)
    finance["annual_revenue_wan"] = effective_revenue

    if "loan_rate" in values:
        finance["loan_rate"] = float(values["loan_rate"])
    if "loan_ratio" in values:
        finance["loan_ratio"] = float(values["loan_ratio"])
    build_months = int(values.get("build_period_months", scenario["build_period_months"]))
    working_capital = _sync_working_capital(
        finance,
        base_revenue=base_effective_revenue,
        target_revenue=effective_revenue,
    )
    _reconcile_funding(
        finance,
        target_total=target_total,
        base_total=base_total,
        working_capital=working_capital,
        build_months=build_months,
        values=values,
    )
    operating_years = int(values.get("operating_period_years", 10))
    finance["calc_period_years"] = max(
        finance.get("loan_years", 1) + (build_months + 11) // 12,
        operating_years + (build_months + 11) // 12,
    )
    finance.update(
        {
            "industry": scenario["industry_label"],
            "invest_type": scenario["invest_type"],
            "build_period_months": build_months,
        }
    )
    spec.update(
        {
            "confirmation_status": "candidate",
            "source_hint": "zero_material_controlled_assumption",
            "selected_scenario_id": scenario_id,
            "assumptions": [
                "零材料受控假设，仅用于 estimate_preview",
                "计算口径冻结不代表项目事实已获证据支持",
            ],
            "field_sources": {
                field: {
                    "source": "controlled_assumption",
                    "source_ref": str(item.get("source_ref") or package.get("profile_version") or ""),
                    "confirmed": bool(item.get("confirmed")),
                }
                for item in package.get("fields") or []
                if isinstance(item, dict)
                for field in [str(item.get("name") or "")]
                if field
            },
        }
    )
    spec.pop("confirmed_by", None)
    context = {
        "scenario_id": scenario_id,
        "scenario": scenario,
        "build_period_months": build_months,
    }
    return spec, finance, context
