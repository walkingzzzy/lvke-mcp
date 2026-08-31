"""城市轨道交通财务输入的领域级完整性校验。"""

from __future__ import annotations

from typing import Any


VALID_PASSENGER_UNITS = frozenset({
    "人次",
    "万人次",
    "person_trips",
    "passenger_trips",
    "persons",
    "10k_person_trips",
    "ten_thousand_person_trips",
})


def revenue_input_complete(
    spec: dict[str, Any] | None,
    input_revision: dict[str, Any] | None,
) -> bool:
    """Return whether operating revenue has a complete deterministic driver."""

    revision = input_revision if isinstance(input_revision, dict) else {}
    annual = revision.get("annual_revenue_wan")
    if isinstance(annual, (int, float)) and annual > 0:
        return True
    revenue = _revenue_from_spec(spec)
    if not revenue:
        return False
    model = str(revenue.get("model") or "")
    if model == "product_sales":
        products = revenue.get("products")
        return isinstance(products, list) and bool(products) and all(
            isinstance(item, dict)
            and _positive(item.get("capacity"))
            and _positive(item.get("price_per_unit"))
            for item in products
        )
    if model == "property_sales":
        return _positive(revenue.get("saleable_area")) and _positive(
            revenue.get("price_per_sqm")
        )
    if model == "tourism":
        try:
            visitors = float(revenue.get("annual_visitors") or 0)
            spend = max(
                float(revenue.get("spend_per_visitor") or 0),
                float(revenue.get("ticket_price_yuan") or 0)
                + float(revenue.get("secondary_spend_yuan") or 0),
            )
        except (TypeError, ValueError):
            return False
        return visitors > 0 and spend > 0
    if model == "rail_transit":
        return (
            _positive(revenue.get("annual_passenger_trips"))
            and str(revenue.get("passenger_unit") or "").strip()
            in VALID_PASSENGER_UNITS
            and _positive(revenue.get("average_fare_yuan"))
            and bool(revenue.get("ridership_ramp") or revenue.get("ramp"))
        )
    if model == "flat":
        # flat 模型的驱动就是达产营收本身。此前没有 flat 分支，spec 侧写了
        # `revenue.annual_revenue_wan` 也会落到下面的 revenue_by_year 检查并
        # 返回 False，于是模型改用"投资额×30%"派生基线——一个与输入无关的数。
        # 这里显式承认 spec 侧的达产营收（含 flat 回退时保留的
        # `fixed_annual_revenue_wan`），与函数开头对 input_revision 的判断同源。
        if _positive(revenue.get("annual_revenue_wan")) or _positive(
            revenue.get("fixed_annual_revenue_wan")
        ):
            return True
    series = revision.get("revenue_by_year")
    return isinstance(series, list) and any(
        isinstance(value, (int, float)) and value > 0 for value in series
    )


def rail_transit_missing_inputs(
    spec: dict[str, Any] | None,
    input_revision: dict[str, Any] | None,
    *,
    build_period_months: Any = None,
) -> list[str]:
    """Return explicit rail inputs that may not use generic model defaults."""

    revenue = _revenue_from_spec(spec)
    if str(revenue.get("model") or "") != "rail_transit":
        return []
    revision = input_revision if isinstance(input_revision, dict) else {}
    missing: list[str] = []

    if not _positive(build_period_months or revision.get("build_period_months")):
        missing.append("build_period_months")
    for field in (
        "calc_period_years",
        "capital_own_ratio",
        "loan_ratio",
        "loan_rate",
        "loan_years",
        "discount_rate",
    ):
        if not _positive(revision.get(field)):
            missing.append(field)
    if not _positive(revenue.get("annual_passenger_trips")):
        missing.append("revenue.annual_passenger_trips")
    unit = str(revenue.get("passenger_unit") or "").strip()
    if unit not in VALID_PASSENGER_UNITS:
        missing.append("revenue.passenger_unit")
    if not _positive(revenue.get("average_fare_yuan")):
        missing.append("revenue.average_fare_yuan")
    if not (revenue.get("ridership_ramp") or revenue.get("ramp")):
        missing.append("revenue.ridership_ramp")
    costs = revision.get("cost_items")
    cost_series = revision.get("operating_cost_by_year") or revision.get("opex_by_year")
    if not (
        isinstance(costs, dict)
        and any(_positive(value) for value in costs.values())
    ) and not (
        isinstance(cost_series, list)
        and any(_positive(value) for value in cost_series)
    ):
        missing.append("cost_items_or_operating_cost_by_year")
    policy = revision.get("fiscal_support_policy")
    if not isinstance(policy, dict) or not str(policy.get("mode") or "").strip():
        missing.append("fiscal_support_policy")
    return missing


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _revenue_from_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    candidate = spec if isinstance(spec, dict) else {}
    revenue = candidate.get("revenue")
    if isinstance(revenue, dict):
        return revenue
    finance_inputs = candidate.get("finance_inputs")
    if isinstance(finance_inputs, dict) and isinstance(finance_inputs.get("revenue"), dict):
        return finance_inputs["revenue"]
    return {}
