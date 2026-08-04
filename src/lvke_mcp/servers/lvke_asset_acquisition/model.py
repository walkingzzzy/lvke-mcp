"""Deterministic asset-acquisition and hotel/lease finance engine.

The module consumes ``finance_spec.v3`` only.  It deliberately keeps purchase
price, market rent, ADR, occupancy, leverage, interest, tenor, transaction tax,
maintenance capex, and exit value as independent scenario dimensions.
"""

from __future__ import annotations

import copy
import calendar
import math
from datetime import date, datetime
from typing import Any

from lvke_mcp.servers.finance_calc.calculations import irr, npv, payback_period

from lvke_mcp.servers.lvke_asset_acquisition.spec import LATEST_SPEC_VERSION, validate


INDEPENDENT_SCENARIO_FIELDS = {
    "transaction.purchase_price",
    "lease_portfolio.market_rent",
    "hotel_operation.adr",
    "hotel_operation.occupancy",
    "transaction.financing_ratio",
    "transaction.interest_rate",
    "transaction.tenor",
    "transaction.transaction_taxes",
    "hotel_operation.maintenance_capex",
    "transaction.exit_value",
    "solar_operation.tariff_yuan_per_kwh",
    "solar_operation.annual_generation_mwh",
    "solar_operation.utilization_hours",
    "solar_operation.annual_opex_wan",
    "solar_operation.maintenance_capex_wan",
    "solar_operation.curtailment_rate",
}


class AcquisitionModelError(ValueError):
    """Raised when a v3 acquisition spec cannot be deterministically solved."""


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _series(value: Any, years: int, *, default: float = 0.0) -> list[float]:
    if isinstance(value, (list, tuple)):
        values = [_number(item, default) for item in value]
        if not values:
            values = [default]
    else:
        values = [_number(value, default)]
    if len(values) < years:
        values.extend([values[-1]] * (years - len(values)))
    return values[:years]


def _safe_irr(cashflows: list[float]) -> float | None:
    try:
        return float(irr(cashflows))
    except (ValueError, OverflowError, ZeroDivisionError):
        return None


def calculate_hotel_operation(hotel: dict[str, Any], years: int) -> dict[str, Any]:
    rooms = _number(hotel.get("rooms"))
    days = int(_number(hotel.get("operating_days"), 365))
    adr = _series(hotel.get("adr"), years)
    occupancy = _series(hotel.get("occupancy"), years)
    food = _series(hotel.get("food_beverage_revenue"), years)
    meeting = _series(hotel.get("meeting_revenue"), years)
    other = _series(hotel.get("other_revenue"), years)
    payroll = _series(hotel.get("payroll"), years)
    utilities = _series(hotel.get("utilities"), years)
    consumables = _series(hotel.get("consumables"), years)
    capex = _series(hotel.get("maintenance_capex"), years)
    ota = _series(hotel.get("ota_commission"), years)
    target_cover = max(_number(hotel.get("target_rent_coverage"), 1.5), 1.0)

    rows: list[dict[str, Any]] = []
    for index in range(years):
        room_revenue = rooms * adr[index] * occupancy[index] * days / 10000.0
        ota_cost = room_revenue * ota[index] if 0 <= ota[index] <= 1 else ota[index]
        total_revenue = room_revenue + food[index] + meeting[index] + other[index]
        operating_cost = ota_cost + payroll[index] + utilities[index] + consumables[index]
        ebitdar = total_revenue - operating_cost
        rows.append({
            "year": index + 1,
            "rooms": rooms,
            "adr_yuan": adr[index],
            "occupancy": occupancy[index],
            "revpar_yuan": adr[index] * occupancy[index],
            "room_revenue_wan": room_revenue,
            "food_beverage_revenue_wan": food[index],
            "meeting_revenue_wan": meeting[index],
            "other_revenue_wan": other[index],
            "total_revenue_wan": total_revenue,
            "ota_commission_wan": ota_cost,
            "operating_cost_wan": operating_cost,
            "ebitdar_wan": ebitdar,
            "affordable_rent_wan": max(ebitdar / target_cover, 0.0),
            "maintenance_capex_wan": capex[index],
        })
    return {
        "available": bool(rooms > 0),
        "years": rows,
        "target_rent_coverage": target_cover,
    }


def _lease_annual_base(unit: dict[str, Any]) -> float:
    rent = _number(unit.get("base_rent_wan"))
    pricing = str(unit.get("pricing_unit") or "annual_total")
    area = _number(unit.get("area_sqm"))
    if pricing == "yuan_sqm_month":
        return area * rent * 12.0 / 10000.0
    if pricing == "wan_sqm_year":
        return area * rent
    if pricing == "monthly_total_wan":
        return rent * 12.0
    return rent


def _date(value: Any, default: date) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return default


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + max(int(months), 0)
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _escalation_count(on_date: date, first_escalation: date | None) -> int:
    if first_escalation is None or on_date < first_escalation:
        return 0
    years = on_date.year - first_escalation.year
    anniversary = date(
        on_date.year,
        first_escalation.month,
        min(first_escalation.day, calendar.monthrange(on_date.year, first_escalation.month)[1]),
    )
    return years + (1 if on_date >= anniversary else 0)


def _contract_rent_for_year(
    *, annual_base: float, start: date, end: date, year: int,
    escalation_rate: float, first_escalation: date | None, free_until: date,
) -> float:
    """Prorate one lease by actual active days and dated escalations."""

    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    active_start = max(start, year_start)
    active_end = min(end, year_end)
    if active_start > active_end:
        return 0.0
    days_in_year = 366 if calendar.isleap(year) else 365
    total = 0.0
    cursor = active_start
    while cursor <= active_end:
        if cursor >= free_until:
            total += (
                annual_base
                * ((1.0 + escalation_rate) ** _escalation_count(cursor, first_escalation))
                / days_in_year
            )
        cursor = date.fromordinal(cursor.toordinal() + 1)
    # Daily proration deliberately uses binary floats for speed; normalize the
    # immaterial accumulation residue so a full contractual year remains the
    # exact annual amount used by report/artifact consistency checks.
    return round(total, 10)


def calculate_lease_portfolio(portfolio: dict[str, Any], years: int) -> dict[str, Any]:
    units = portfolio.get("units") or []
    rows: list[dict[str, Any]] = []
    locked = [0.0] * years
    renewals = [0.0] * years
    cash_adjustments = [0.0] * years
    deposits_held = [0.0] * years
    guarantees = [0.0] * years
    base_year = int(_number(portfolio.get("base_year"), date.today().year))
    for unit in units if isinstance(units, list) else []:
        if not isinstance(unit, dict):
            continue
        base = _lease_annual_base(unit)
        escalation = _number(unit.get("escalation_rate"))
        vacancy = _number(unit.get("vacancy_rate"))
        bad_debt = _number(unit.get("bad_debt_rate"))
        free_months = max(min(int(_number(unit.get("rent_free_months"))), 12), 0)
        start = _date(unit.get("start_date"), date(base_year, 1, 1))
        end = _date(unit.get("end_date"), date(base_year + years - 1, 12, 31))
        if end < start:
            raise AcquisitionModelError(f"lease {unit.get('unit_id') or ''} end_date precedes start_date")
        escalation_date_raw = str(unit.get("escalation_date") or "").strip()
        first_escalation = (
            _date(escalation_date_raw, start)
            if escalation_date_raw
            else (_add_months(start, 12) if escalation > 0 else None)
        )
        free_until = _add_months(start, free_months)
        start_year = start.year
        end_year = end.year
        renewal_probability = _number(unit.get("renewal_probability"))
        deposit = _number(unit.get("deposit_wan"))
        guarantee = _number(unit.get("guarantee_wan"))
        upfront_cost = _number(unit.get("leasing_cost_wan")) + _number(unit.get("fitout_allowance_wan"))
        unit_values: list[float] = []
        renewal_values: list[float] = []
        unit_adjustments: list[float] = []
        for index in range(years):
            current_year = base_year + index
            in_contract = start_year <= current_year <= end_year
            gross = _contract_rent_for_year(
                annual_base=base, start=start, end=end, year=current_year,
                escalation_rate=escalation, first_escalation=first_escalation,
                free_until=free_until,
            ) if in_contract else 0.0
            net = gross * (1.0 - vacancy) * (1.0 - bad_debt)
            if in_contract:
                locked[index] += net
            renewal = 0.0
            if current_year > end_year and renewal_probability > 0:
                escalation_count = _escalation_count(date(current_year, 1, 1), first_escalation)
                renewal = (
                    base * ((1.0 + escalation) ** escalation_count)
                    * renewal_probability * (1.0 - vacancy) * (1.0 - bad_debt)
                )
                renewals[index] += renewal
            adjustment = 0.0
            if current_year == start_year and base_year <= start_year < base_year + years:
                adjustment += deposit - upfront_cost
            if current_year == end_year and base_year <= end_year < base_year + years:
                adjustment -= deposit
            cash_adjustments[index] += adjustment
            if start_year <= current_year <= end_year:
                deposits_held[index] += deposit
                guarantees[index] += guarantee
            unit_values.append(net)
            renewal_values.append(renewal)
            unit_adjustments.append(adjustment)
        rows.append({
            "unit_id": unit.get("unit_id"),
            "annual_rent_wan": unit_values,
            "renewal_expected_rent_wan": renewal_values,
            "cash_adjustments_wan": unit_adjustments,
            "contract_end_year": end_year,
            "payment_frequency": unit.get("payment_frequency") or "annual",
            "deposit_wan": deposit,
            "guarantee_wan": guarantee,
            "evidence_ids": list(unit.get("evidence_ids") or []),
        })
    # ``market_rent`` is the independently scenario-controlled stabilized
    # annual rent for the whole portfolio.  It fills only the part not locked
    # by contracts, so changing purchase price can never mutate rent and
    # changing market rent cannot rewrite signed contract cash flows.
    market = _series(portfolio.get("market_rent"), years) if portfolio.get("market_rent") is not None else [0.0] * years
    unlocked = [max(market[index] - locked[index], renewals[index], 0.0) for index in range(years)]
    annual = [locked[index] + unlocked[index] for index in range(years)]
    total = sum(annual)
    locked_total = sum(locked)
    return {
        "units": rows,
        "annual_rent_wan": annual,
        "locked_rent_wan": locked,
        "market_rent_wan": market,
        "renewal_expected_rent_wan": renewals,
        "unlocked_rent_wan": unlocked,
        "cash_adjustments_wan": cash_adjustments,
        "deposits_held_wan": deposits_held,
        "guarantees_wan": guarantees,
        "contract_income_ratio": locked_total / total if total else 0.0,
        "unlocked_income_ratio": 1.0 - (locked_total / total if total else 0.0),
        "lease_coverage_years": next((i for i, value in enumerate(locked, 1) if value <= 0), years + 1) - 1,
    }


def _debt_schedule(
    principal: float, rate: float, tenor: int, years: int,
    repayment: str = "equal_principal",
) -> list[dict[str, Any]]:
    tenor = max(min(int(tenor), years), 0)
    annual_principal = principal / tenor if tenor else 0.0
    method = str(repayment or "equal_principal").strip().lower()
    if method not in {"equal_principal", "equal_payment", "annuity", "bullet", "interest_only"}:
        raise AcquisitionModelError(f"unsupported repayment method: {repayment}")
    annuity_payment = 0.0
    if tenor and principal > 0:
        annuity_payment = (
            principal / tenor if rate == 0
            else principal * rate * ((1 + rate) ** tenor) / (((1 + rate) ** tenor) - 1)
        )
    outstanding = principal
    rows: list[dict[str, float]] = []
    for index in range(years):
        interest = outstanding * rate if index < tenor else 0.0
        principal_payment = 0.0
        if index < tenor:
            if method in {"bullet", "interest_only"}:
                principal_payment = outstanding if index == tenor - 1 else 0.0
            elif method in {"equal_payment", "annuity"}:
                principal_payment = min(max(annuity_payment - interest, 0.0), outstanding)
            else:
                principal_payment = min(annual_principal, outstanding)
        rows.append({
            "year": index + 1,
            "opening_principal_wan": outstanding,
            "interest_wan": interest,
            "principal_wan": principal_payment,
            "debt_service_wan": interest + principal_payment,
            "repayment_method": method,
        })
        outstanding = max(outstanding - principal_payment, 0.0)
    return rows


def _depreciation_schedule(transaction: dict[str, Any], years: int) -> dict[str, Any]:
    """Straight-line depreciation from explicit asset-class evidence only."""

    candidates = [
        item for item in (transaction.get("asset_scope") or [])
        if isinstance(item, dict) and item.get("included", True)
        and (_number(item.get("depreciable_basis_wan")) > 0 or _number(item.get("value_wan")) > 0)
    ]
    if not candidates and _number(transaction.get("depreciable_basis_wan")) > 0:
        candidates = [{
            "scope_id": "transaction-depreciable-basis",
            "depreciable_basis_wan": transaction.get("depreciable_basis_wan"),
            "depreciation_years": transaction.get("depreciation_years"),
            "residual_rate": transaction.get("residual_rate", 0.0),
            "depreciation_start_year": transaction.get("depreciation_start_year", 1),
        }]
    annual = [0.0] * years
    classes: list[dict[str, Any]] = []
    for item in candidates:
        basis = _number(item.get("depreciable_basis_wan"), _number(item.get("value_wan")))
        life = int(_number(item.get("depreciation_years")))
        if basis <= 0 or life <= 0:
            raise AcquisitionModelError(
                f"asset {item.get('scope_id') or ''} requires explicit depreciation_years"
            )
        residual_rate = min(max(_number(item.get("residual_rate")), 0.0), 1.0)
        start_year = max(int(_number(item.get("depreciation_start_year"), 1)), 1)
        charge = basis * (1.0 - residual_rate) / life
        values = [charge if start_year <= index + 1 < start_year + life else 0.0 for index in range(years)]
        annual = [annual[index] + values[index] for index in range(years)]
        classes.append({
            "scope_id": item.get("scope_id") or item.get("type") or "asset",
            "basis_wan": basis,
            "depreciation_years": life,
            "residual_rate": residual_rate,
            "annual_depreciation_wan": values,
        })
    return {"classes": classes, "annual_depreciation_wan": annual}


def _date_value(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_end(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _month_overlap_days(start: date, end: date, active_from: date, active_to: date | None = None) -> int:
    left = max(start, active_from)
    right = min(end, active_to) if active_to else end
    return max((right - left).days + 1, 0)


def _monthly_lease_income(
    portfolio: dict[str, Any], month_start: date, month_end: date,
) -> tuple[float, float]:
    """Return contractual rent and one-off leasing cash adjustment for one month.

    A unit without a valid contract period contributes no confirmed rent.  This
    deliberately prevents a lapsed bar/gym lease from silently continuing.
    """

    rent = 0.0
    adjustment = 0.0
    for unit in portfolio.get("units") or []:
        if not isinstance(unit, dict):
            continue
        start = _date_value(unit.get("start_date"))
        end = _date_value(unit.get("end_date"))
        if start is None or end is None or end < start:
            continue
        active_days = _month_overlap_days(month_start, month_end, start, end)
        if not active_days:
            continue
        annual = _lease_annual_base(unit)
        escalation = max(_number(unit.get("escalation_rate")), 0.0)
        escalation_date = _date_value(unit.get("escalation_date")) or _add_months(start, 12)
        increments = 0
        cursor = escalation_date
        while cursor <= month_end:
            increments += 1
            cursor = _add_months(cursor, 12)
        effective_annual = annual * ((1.0 + escalation) ** increments)
        rent += effective_annual * active_days / (366.0 if calendar.isleap(month_start.year) else 365.0)
        if start.year == month_start.year and start.month == month_start.month:
            adjustment -= _number(unit.get("leasing_cost_wan")) + _number(unit.get("fitout_allowance_wan"))
    return rent, adjustment


def _monthly_debt_schedule(
    debt: float, annual_rate: float, tenor_years: int, months: int, repayment: str,
) -> list[dict[str, float]]:
    balance = max(debt, 0.0)
    monthly_rate = max(annual_rate, 0.0) / 12.0
    tenor_months = max(tenor_years * 12, 0)
    rows: list[dict[str, float]] = []
    payment = 0.0
    if repayment in {"equal_payment", "annuity"} and tenor_months:
        payment = (
            balance / tenor_months
            if monthly_rate == 0
            else balance * monthly_rate * (1 + monthly_rate) ** tenor_months / ((1 + monthly_rate) ** tenor_months - 1)
        )
    for index in range(months):
        opening = balance
        interest = opening * monthly_rate if index < tenor_months else 0.0
        principal = 0.0
        if index < tenor_months:
            if repayment == "equal_principal":
                principal = debt / tenor_months
            elif repayment in {"equal_payment", "annuity"}:
                principal = max(payment - interest, 0.0)
            elif repayment == "bullet":
                principal = opening if index == tenor_months - 1 else 0.0
            elif repayment == "interest_only":
                principal = opening if index == tenor_months - 1 else 0.0
        principal = min(principal, opening)
        balance = max(opening - principal, 0.0)
        rows.append({
            "month": index + 1, "opening_principal_wan": opening,
            "interest_wan": interest, "principal_wan": principal,
            "debt_service_wan": interest + principal, "closing_principal_wan": balance,
        })
    return rows


def _run_monthly_acquisition_model(
    spec: dict[str, Any], *, discount_rate: float, scenario_id: str,
) -> dict[str, Any]:
    """Monthly mixed hotel/lease acquisition model with annual projections.

    This is intentionally an additive v3 path: old annual specifications keep
    using the v2 algorithm below.  The monthly path has no implied lease
    renewal and never converts hotel affordability into owner revenue.
    """

    transaction = dict(spec.get("transaction") or {})
    hotel = dict(spec.get("hotel_operation") or {})
    portfolio = dict(spec.get("lease_portfolio") or {})
    start = _date_value(transaction.get("model_start_date") or transaction.get("closing_date"))
    opening = _date_value(transaction.get("opening_date") or transaction.get("hotel_opening_date"))
    if start is None:
        raise AcquisitionModelError("monthly acquisition requires transaction.model_start_date or closing_date")
    operating_mode = str(transaction.get("operating_mode") or spec.get("operating_mode") or "")
    if operating_mode not in {"owner_lessor", "mixed_owner_operator"}:
        raise AcquisitionModelError("monthly acquisition requires operating_mode owner_lessor or mixed_owner_operator")
    if operating_mode == "mixed_owner_operator" and opening is None:
        raise AcquisitionModelError("mixed_owner_operator requires transaction.opening_date")
    years = max(
        int(_number(transaction.get("exit_year"), 0)),
        int(_number(transaction.get("tenor"), 0)),
        int(_number(portfolio.get("projection_years"), 0)),
        1,
    )
    months = years * 12
    purchase_price = _number(transaction.get("purchase_price"))
    taxes = transaction.get("transaction_taxes") or {}
    transaction_tax = sum(_number(value) for value in taxes.values()) if isinstance(taxes, dict) else _number(taxes)
    closing_cost = _number(transaction.get("closing_costs"))
    fitout_capex = _number(transaction.get("fitout_capex_wan"))
    total_cost = purchase_price + transaction_tax + closing_cost + fitout_capex
    financing_ratio = min(max(_number(transaction.get("financing_ratio")), 0.0), 1.0)
    debt = total_cost * financing_ratio
    equity = total_cost - debt
    repayment = str(transaction.get("repayment") or "equal_principal")
    if repayment not in {"equal_principal", "equal_payment", "annuity", "bullet", "interest_only"}:
        raise AcquisitionModelError(f"unsupported repayment method: {repayment}")
    debt_rows = _monthly_debt_schedule(
        debt, _number(transaction.get("interest_rate")), int(_number(transaction.get("tenor"))), months, repayment,
    )
    depreciation = _depreciation_schedule(transaction, years)
    annual_depreciation = depreciation.get("annual_depreciation_wan") or [0.0] * years
    cost = spec.get("cost") or {}
    cost_items = cost.get("cost_items") if isinstance(cost, dict) else {}
    annual_owner_opex = _number((cost or {}).get("annual_owner_operating_cost_wan")) or sum(
        _number(value) for value in (cost_items or {}).values()
    )
    income_tax_rate = min(max(_number((spec.get("tax") or {}).get("income_tax_rate")), 0.0), 1.0)
    exit_year = int(_number(transaction.get("exit_year"), years))
    exit_month = min(max(exit_year * 12 - 1, 0), months - 1)
    exit_value = _number(transaction.get("exit_value"))
    exit_cost = _number(transaction.get("exit_cost_wan")) + exit_value * min(max(_number(transaction.get("exit_cost_rate")), 0.0), 1.0)
    exit_tax = _number(transaction.get("exit_tax_wan")) + exit_value * min(max(_number(transaction.get("exit_tax_rate")), 0.0), 1.0)
    net_exit = max(exit_value - exit_cost - exit_tax, 0.0)
    monthly_rows: list[dict[str, Any]] = []
    project_monthly = [-total_cost]
    equity_monthly = [-equity]
    annual: list[dict[str, Any]] = []
    for year_index in range(years):
        year_start = _add_months(_month_start(start), year_index * 12)
        year_end = _month_end(_add_months(year_start, 11))
        annual.append({
            "year": year_end.year,
            "year_index": year_index + 1,
            "period_start": year_start.isoformat(),
            "period_end": year_end.isoformat(),
            "period_label": (
                str(year_end.year)
                if year_start.month == 1 and year_end.month == 12
                else f"{year_start:%Y-%m}/{year_end:%Y-%m}"
            ),
            "period_basis": "calendar_year" if year_start.month == 1 else "rolling_model_year",
            "hotel_revenue_wan": 0.0,
            "lease_revenue_wan": 0.0,
            "revenue_wan": 0.0,
            "operating_cost_wan": 0.0,
            "tax_wan": 0.0,
            "income_tax_wan": 0.0,
            "maintenance_capex_wan": 0.0,
            "project_cf_wan": 0.0,
            "equity_cf_wan": 0.0,
            "debt_service_wan": 0.0,
            "interest_wan": 0.0,
        })
    room_adr = _series(hotel.get("adr"), years)
    occupancy = _series(hotel.get("occupancy"), years)
    food = _series(hotel.get("food_beverage_revenue"), years)
    meeting = _series(hotel.get("meeting_revenue"), years)
    other = _series(hotel.get("other_revenue"), years)
    payroll = _series(hotel.get("payroll"), years)
    utilities = _series(hotel.get("utilities"), years)
    consumables = _series(hotel.get("consumables"), years)
    maintenance = _series(hotel.get("maintenance_capex"), years)
    ota = _series(hotel.get("ota_commission"), years)
    rooms = _number(hotel.get("rooms"))
    cursor = _month_start(start)
    for index in range(months):
        period_start = cursor
        period_end = _month_end(cursor)
        model_year = index // 12
        days = (period_end - period_start).days + 1
        owner_days = _month_overlap_days(period_start, period_end, start)
        hotel_days = _month_overlap_days(period_start, period_end, opening) if opening else 0
        lease_revenue, lease_adjustment = _monthly_lease_income(portfolio, period_start, period_end)
        hotel_revenue = 0.0
        hotel_cost = 0.0
        maintenance_capex = 0.0
        if operating_mode == "mixed_owner_operator" and hotel_days:
            room_revenue = rooms * room_adr[model_year] * occupancy[model_year] * hotel_days / 10000.0
            ancillary = (food[model_year] + meeting[model_year] + other[model_year]) * hotel_days / (366.0 if calendar.isleap(period_start.year) else 365.0)
            hotel_revenue = room_revenue + ancillary
            ota_cost = room_revenue * ota[model_year] if 0 <= ota[model_year] <= 1 else ota[model_year] * hotel_days / days
            hotel_cost = ota_cost + (payroll[model_year] + utilities[model_year] + consumables[model_year]) * hotel_days / (366.0 if calendar.isleap(period_start.year) else 365.0)
            maintenance_capex = maintenance[model_year] * hotel_days / (366.0 if calendar.isleap(period_start.year) else 365.0)
        owner_cost = annual_owner_opex * owner_days / (366.0 if calendar.isleap(period_start.year) else 365.0)
        revenue = lease_revenue + (hotel_revenue if operating_mode == "mixed_owner_operator" else 0.0)
        operating_cost = owner_cost + (hotel_cost if operating_mode == "mixed_owner_operator" else 0.0)
        depreciation_month = _number(annual_depreciation[model_year] if model_year < len(annual_depreciation) else 0.0) / 12.0
        taxable = max(revenue - operating_cost - depreciation_month, 0.0)
        tax = taxable * income_tax_rate
        cfads = revenue - operating_cost - tax - maintenance_capex
        exit_cash = net_exit if index == exit_month else 0.0
        debt_row = debt_rows[index]
        project_cf = cfads + lease_adjustment + exit_cash
        equity_cf = project_cf - debt_row["debt_service_wan"]
        project_monthly.append(project_cf)
        equity_monthly.append(equity_cf)
        bucket = annual[model_year]
        for key, value in {
            "hotel_revenue_wan": hotel_revenue, "lease_revenue_wan": lease_revenue,
            "revenue_wan": revenue,
            "operating_cost_wan": operating_cost, "tax_wan": tax,
            "income_tax_wan": tax,
            "maintenance_capex_wan": maintenance_capex, "project_cf_wan": project_cf,
            "equity_cf_wan": equity_cf, "debt_service_wan": debt_row["debt_service_wan"],
            "interest_wan": debt_row["interest_wan"],
        }.items():
            bucket[key] += value
        monthly_rows.append({
            "month": index + 1, "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
            "active_days": owner_days, "hotel_days": hotel_days,
            "operating_mode": operating_mode, "hotel_revenue_wan": hotel_revenue,
            "hotel_cost_wan": hotel_cost, "lease_revenue_wan": lease_revenue,
            "lease_adjustment_wan": lease_adjustment,
            "operating_cost_wan": operating_cost,
            "tax_wan": tax, "income_tax_wan": tax,
            "interest_wan": debt_row["interest_wan"],
            "maintenance_capex_wan": maintenance_capex,
            "project_cf_wan": project_cf, "equity_cf_wan": equity_cf,
            "debt_service_wan": debt_row["debt_service_wan"], "dscr": cfads / debt_row["debt_service_wan"] if debt_row["debt_service_wan"] else None,
        })
        cursor = _add_months(cursor, 1)
    project_annual = [-total_cost, *[row["project_cf_wan"] for row in annual]]
    equity_annual = [-equity, *[row["equity_cf_wan"] for row in annual]]
    monthly_irr = _safe_irr(project_monthly)
    monthly_equity_irr = _safe_irr(equity_monthly)
    monthly_rate = (1 + discount_rate) ** (1 / 12) - 1
    annual_dscr = [
        (row["project_cf_wan"] + row["debt_service_wan"]) / row["debt_service_wan"]
        if row["debt_service_wan"] else None
        for row in annual
    ]
    return {
        "available": True, "model_version": "acquisition_model.v3", "spec_version": LATEST_SPEC_VERSION,
        "scenario_id": scenario_id, "confirmation_status": spec.get("confirmation_status"),
        "business_review_status": "pending", "operating_mode": operating_mode,
        "calculation_granularity": "monthly", "purchase_price_wan": purchase_price,
        "transaction_tax_wan": transaction_tax, "total_acquisition_cost_wan": total_cost,
        "monthly_timeline": monthly_rows, "annual_summary": annual,
        "project_cashflows_monthly_wan": project_monthly, "equity_cashflows_monthly_wan": equity_monthly,
        "project_cashflows_wan": project_annual, "equity_cashflows_wan": equity_annual,
        "debt_schedule_monthly": debt_rows, "debt_schedule": {"monthly": debt_rows},
        "depreciation_schedule": depreciation,
        "hotel_operation": {"available": operating_mode == "mixed_owner_operator", "monthly": monthly_rows},
        "lease_portfolio": {"monthly": monthly_rows},
        "owner_revenue_wan": [row["lease_revenue_wan"] if operating_mode == "owner_lessor" else row["hotel_revenue_wan"] + row["lease_revenue_wan"] for row in annual],
        "owner_operating_cost_wan": [row["operating_cost_wan"] for row in annual],
        "project_cfads_wan": [row["project_cf_wan"] + row["debt_service_wan"] for row in annual],
        "net_exit_value_wan": net_exit,
        "indicators": {
            "project_irr_pct": ((1 + monthly_irr) ** 12 - 1) * 100 if monthly_irr is not None else None,
            "equity_irr_pct": ((1 + monthly_equity_irr) ** 12 - 1) * 100 if monthly_equity_irr is not None else None,
            "npv_wan": sum(value / ((1 + monthly_rate) ** index) for index, value in enumerate(project_monthly)),
            "static_payback_years": payback_period(project_annual, rate=discount_rate).static_years,
            "dynamic_payback_years": payback_period(project_annual, rate=discount_rate).dynamic_years,
            "minimum_dscr": min((value for value in annual_dscr if value is not None), default=None),
            "minimum_monthly_dscr": min((row["dscr"] for row in monthly_rows if row["dscr"] is not None), default=None),
            "minimum_icr": min((row["project_cf_wan"] / row["interest_wan"] for row in annual if row["interest_wan"] > 0), default=None),
        },
        "assumptions": [
            "月度自然月计算；交割、开业与租约起止按实际有效天数计量",
            "租约到期后不默认续租；无有效合同的配套租赁不计入确认收入",
            "mixed_owner_operator 合并酒店自营与配套租赁收入，不将酒店可承受租金计作业主收入",
        ],
    }


def _run_solar_acquisition_model(
    spec: dict[str, Any], *, discount_rate: float, scenario_id: str,
) -> dict[str, Any]:
    """Run an operating solar-plant acquisition without hotel/lease proxies.

    Solar operations are calculated annually from generation and tariff.  A
    monthly bridge is emitted only so the existing debt/table package can keep
    a single auditable period contract; it never introduces hotel assumptions.
    """

    transaction = dict(spec.get("transaction") or {})
    solar = dict(spec.get("solar_operation") or {})
    start = _date_value(transaction.get("model_start_date") or transaction.get("closing_date"))
    if start is None:
        raise AcquisitionModelError("solar acquisition requires transaction.model_start_date or closing_date")
    capacity = _number(solar.get("installed_capacity_mw"))
    generation_input = _number(solar.get("annual_generation_mwh"))
    utilization_hours = _number(solar.get("utilization_hours"))
    base_generation = generation_input or capacity * utilization_hours
    tariff = _number(solar.get("tariff_yuan_per_kwh"))
    if capacity <= 0 or base_generation <= 0 or tariff <= 0:
        raise AcquisitionModelError(
            "solar acquisition requires positive installed capacity, generation/utilization hours and tariff"
        )
    remaining_years = int(_number(solar.get("remaining_operating_years")))
    if remaining_years <= 0:
        raise AcquisitionModelError("solar acquisition requires remaining_operating_years > 0")
    exit_year = int(_number(transaction.get("exit_year"), remaining_years))
    tenor = int(_number(transaction.get("tenor"), 0))
    years = max(min(max(exit_year, tenor, 1), remaining_years), 1)
    months = years * 12

    purchase_price = _number(transaction.get("purchase_price"))
    taxes = transaction.get("transaction_taxes") or {}
    transaction_tax = sum(_number(value) for value in taxes.values()) if isinstance(taxes, dict) else _number(taxes)
    closing_cost = _number(transaction.get("closing_costs"))
    total_cost = purchase_price + transaction_tax + closing_cost
    financing_ratio = min(max(_number(transaction.get("financing_ratio")), 0.0), 1.0)
    debt = total_cost * financing_ratio
    equity = total_cost - debt
    repayment = str(transaction.get("repayment") or "equal_principal")
    if repayment not in {"equal_principal", "equal_payment", "annuity", "bullet", "interest_only"}:
        raise AcquisitionModelError(f"unsupported repayment method: {repayment}")
    debt_rows = _monthly_debt_schedule(
        debt, _number(transaction.get("interest_rate")), tenor, months, repayment,
    )
    depreciation = _depreciation_schedule(transaction, years)
    depreciation_values = depreciation.get("annual_depreciation_wan") or [0.0] * years
    degradation = min(max(_number(solar.get("degradation_rate")), 0.0), 1.0)
    curtailment = min(max(_number(solar.get("curtailment_rate")), 0.0), 1.0)
    opex = _series(solar.get("annual_opex_wan"), years)
    maintenance = _series(solar.get("maintenance_capex_wan"), years)
    income_tax_rate = min(max(_number((spec.get("tax") or {}).get("income_tax_rate")), 0.0), 1.0)
    exit_value = _number(transaction.get("exit_value"))
    exit_cost = _number(transaction.get("exit_cost_wan")) + exit_value * min(
        max(_number(transaction.get("exit_cost_rate")), 0.0), 1.0,
    )
    exit_tax = _number(transaction.get("exit_tax_wan")) + exit_value * min(
        max(_number(transaction.get("exit_tax_rate")), 0.0), 1.0,
    )
    net_exit = max(exit_value - exit_cost - exit_tax, 0.0)

    annual: list[dict[str, Any]] = []
    monthly_rows: list[dict[str, Any]] = []
    project_annual = [-total_cost]
    equity_annual = [-equity]
    project_tax: list[float] = []
    equity_tax: list[float] = []
    dscr_values: list[float | None] = []
    cursor = _month_start(start)
    for index in range(years):
        gross_generation = base_generation * ((1.0 - degradation) ** index)
        sold_generation = gross_generation * (1.0 - curtailment)
        revenue = sold_generation * 1000.0 * tariff / 10000.0
        depreciation_amount = _number(depreciation_values[index] if index < len(depreciation_values) else 0.0)
        annual_interest = sum(row["interest_wan"] for row in debt_rows[index * 12:(index + 1) * 12])
        annual_service = sum(row["debt_service_wan"] for row in debt_rows[index * 12:(index + 1) * 12])
        tax = max(revenue - opex[index] - depreciation_amount, 0.0) * income_tax_rate
        tax_after_interest = max(revenue - opex[index] - depreciation_amount - annual_interest, 0.0) * income_tax_rate
        cfads = revenue - opex[index] - tax_after_interest - maintenance[index]
        exit_cash = net_exit if index + 1 == exit_year else 0.0
        project_cf = revenue - opex[index] - tax - maintenance[index] + exit_cash
        equity_cf = cfads + exit_cash - annual_service
        project_annual.append(project_cf)
        equity_annual.append(equity_cf)
        project_tax.append(tax)
        equity_tax.append(tax_after_interest)
        dscr_values.append(cfads / annual_service if annual_service else None)
        year_start = _add_months(cursor, index * 12)
        year_end = _month_end(_add_months(year_start, 11))
        annual.append({
            "year": year_end.year, "year_index": index + 1,
            "period_start": year_start.isoformat(), "period_end": year_end.isoformat(),
            "period_label": str(year_end.year) if year_start.month == 1 else f"{year_start:%Y-%m}/{year_end:%Y-%m}",
            "period_basis": "calendar_year" if year_start.month == 1 else "rolling_model_year",
            "gross_generation_mwh": gross_generation, "sold_generation_mwh": sold_generation,
            "tariff_yuan_per_kwh": tariff, "revenue_wan": revenue,
            "operating_cost_wan": opex[index], "income_tax_wan": tax,
            "maintenance_capex_wan": maintenance[index], "debt_service_wan": annual_service,
            "interest_wan": annual_interest, "project_cf_wan": project_cf, "equity_cf_wan": equity_cf,
        })
        for month_offset, debt_row in enumerate(debt_rows[index * 12:(index + 1) * 12]):
            period_start = _add_months(year_start, month_offset)
            period_end = _month_end(period_start)
            monthly_cf = project_cf / 12.0
            monthly_cfads = cfads / 12.0
            monthly_rows.append({
                "month": index * 12 + month_offset + 1,
                "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
                "active_days": (period_end - period_start).days + 1,
                "asset_type": "solar_power", "gross_generation_mwh": gross_generation / 12.0,
                "sold_generation_mwh": sold_generation / 12.0, "tariff_yuan_per_kwh": tariff,
                "operating_revenue_wan": revenue / 12.0, "other_revenue_wan": 0.0,
                "operating_cost_wan": opex[index] / 12.0,
                "maintenance_capex_wan": maintenance[index] / 12.0,
                "tax_wan": tax / 12.0, "income_tax_wan": tax / 12.0,
                "interest_wan": debt_row["interest_wan"], "project_cf_wan": monthly_cf,
                "equity_cf_wan": equity_cf / 12.0, "debt_service_wan": debt_row["debt_service_wan"],
                "dscr": monthly_cfads / debt_row["debt_service_wan"] if debt_row["debt_service_wan"] else None,
            })
    project_irr = _safe_irr(project_annual)
    equity_irr = _safe_irr(equity_annual)
    payback = payback_period(project_annual, rate=discount_rate)
    monthly_dscr = [row["dscr"] for row in monthly_rows if row.get("dscr") is not None]
    return {
        "available": True, "model_version": "acquisition_model.solar.v1",
        "asset_type": "solar_power", "spec_version": LATEST_SPEC_VERSION,
        "scenario_id": scenario_id, "confirmation_status": spec.get("confirmation_status"),
        "business_review_status": "pending", "calculation_granularity": "annual",
        "purchase_price_wan": purchase_price, "transaction_tax_wan": transaction_tax,
        "total_acquisition_cost_wan": total_cost, "monthly_timeline": monthly_rows,
        "annual_summary": annual, "project_cashflows_wan": project_annual,
        "equity_cashflows_wan": equity_annual, "debt_schedule_monthly": debt_rows,
        "debt_schedule": {"monthly": debt_rows}, "depreciation_schedule": depreciation,
        "tax_schedule": {"income_tax_rate": income_tax_rate, "project_income_tax_wan": project_tax, "equity_income_tax_wan": equity_tax},
        "solar_operation": {
            "installed_capacity_mw": capacity, "base_generation_mwh": base_generation,
            "tariff_yuan_per_kwh": tariff, "curtailment_rate": curtailment,
            "degradation_rate": degradation, "years": annual,
        },
        "owner_revenue_wan": [row["revenue_wan"] for row in annual],
        "owner_operating_cost_wan": [row["operating_cost_wan"] for row in annual],
        "project_cfads_wan": [
            row["revenue_wan"] - row["operating_cost_wan"] - equity_tax[index] - row["maintenance_capex_wan"]
            for index, row in enumerate(annual)
        ],
        "net_exit_value_wan": net_exit,
        "indicators": {
            "project_irr_pct": project_irr * 100 if project_irr is not None else None,
            "equity_irr_pct": equity_irr * 100 if equity_irr is not None else None,
            "npv_wan": npv(project_annual, discount_rate),
            "static_payback_years": payback.static_years, "dynamic_payback_years": payback.dynamic_years,
            "minimum_dscr": min((value for value in dscr_values if value is not None), default=None),
            "minimum_monthly_dscr": min(monthly_dscr, default=None),
            "minimum_icr": min((
                (row["revenue_wan"] - row["operating_cost_wan"]) / row["interest_wan"]
                for row in annual if row["interest_wan"] > 0
            ), default=None),
        },
        "assumptions": [
            "发电量按明确基准发电量、逐年衰减率和限电率计算",
            "售电收入按上网电量×含税电价计算，全部运营驱动来自光伏运营输入",
            "月度明细为年度光伏运营结果的等额期间桥接，债务按月独立计算",
        ],
    }


def run_acquisition_model(
    spec: dict[str, Any], *, discount_rate: float = 0.08, scenario_id: str = "base",
) -> dict[str, Any]:
    ok, errors = validate(spec)
    if spec.get("version") != LATEST_SPEC_VERSION:
        errors.append(f"asset acquisition requires {LATEST_SPEC_VERSION}")
    transaction_candidate = spec.get("transaction") or {}
    if not isinstance(transaction_candidate, dict) or _number(transaction_candidate.get("purchase_price")) <= 0:
        errors.append("asset acquisition candidate requires transaction.purchase_price > 0")
    asset_type = str(spec.get("asset_type") or "hotel_lease")
    if asset_type == "solar_power":
        if not ok or errors:
            raise AcquisitionModelError("; ".join(dict.fromkeys(errors)))
        return _run_solar_acquisition_model(
            spec, discount_rate=discount_rate, scenario_id=scenario_id,
        )
    hotel_candidate = spec.get("hotel_operation") or {}
    portfolio_candidate = spec.get("lease_portfolio") or {}

    def any_positive(value: Any) -> bool:
        values = value if isinstance(value, (list, tuple)) else [value]
        return any(_number(item) > 0 for item in values)

    hotel_income = (
        isinstance(hotel_candidate, dict)
        and _number(hotel_candidate.get("rooms")) > 0
        and any_positive(hotel_candidate.get("adr"))
        and any_positive(hotel_candidate.get("occupancy"))
    )
    lease_income = False
    if isinstance(portfolio_candidate, dict):
        lease_income = any_positive(portfolio_candidate.get("market_rent")) or any(
            isinstance(unit, dict) and _number(unit.get("base_rent_wan")) > 0
            for unit in (portfolio_candidate.get("units") or [])
        )
    if not (hotel_income or lease_income):
        errors.append("asset acquisition candidate requires hotel or lease income drivers")
    if not ok or errors:
        raise AcquisitionModelError("; ".join(dict.fromkeys(errors)))

    if str((spec.get("transaction") or {}).get("calculation_granularity") or spec.get("calculation_granularity") or "").lower() == "monthly":
        return _run_monthly_acquisition_model(spec, discount_rate=discount_rate, scenario_id=scenario_id)

    transaction = dict(spec.get("transaction") or {})
    years = max(
        int(_number(transaction.get("exit_year"), 0)),
        int(_number(transaction.get("tenor"), 0)),
        int(_number((spec.get("lease_portfolio") or {}).get("projection_years"), 10)),
        1,
    )
    purchase_price = _number(transaction.get("purchase_price"))
    taxes = transaction.get("transaction_taxes") or {}
    transaction_tax = sum(_number(value) for value in taxes.values()) if isinstance(taxes, dict) else _number(taxes)
    closing_cost = _number(transaction.get("closing_costs"))
    total_acquisition_cost = purchase_price + transaction_tax + closing_cost

    hotel = calculate_hotel_operation(dict(spec.get("hotel_operation") or {}), years)
    leases = calculate_lease_portfolio(dict(spec.get("lease_portfolio") or {}), years)
    hotel_rows = hotel.get("years") or []
    lease_rent = leases.get("annual_rent_wan") or [0.0] * years
    lease_adjustments = leases.get("cash_adjustments_wan") or [0.0] * years
    affordable_rent = [row.get("affordable_rent_wan", 0.0) for row in hotel_rows]
    maintenance = [row.get("maintenance_capex_wan", 0.0) for row in hotel_rows]

    # Asset-owner cash flow uses contractual rent when leases exist.  With no
    # lease evidence it uses the operator-affordability result as a candidate,
    # visibly marked for business review instead of silently treating it as fact.
    uses_contract_rent = any(abs(value) > 1e-12 for value in lease_rent)
    owner_revenue = [
        lease_rent[index] if uses_contract_rent else affordable_rent[index]
        for index in range(years)
    ]
    cost = spec.get("cost") or {}
    cost_items = cost.get("cost_items") if isinstance(cost, dict) else {}
    annual_owner_opex = sum(_number(value) for value in (cost_items or {}).values())
    owner_opex = _series(
        (cost or {}).get("annual_owner_operating_cost_wan", annual_owner_opex)
        if isinstance(cost, dict) else annual_owner_opex,
        years,
    )
    depreciation = _depreciation_schedule(transaction, years)
    depreciation_values = depreciation["annual_depreciation_wan"]
    tax_spec = spec.get("tax") or {}
    income_tax_rate = min(max(_number((tax_spec or {}).get("income_tax_rate"), 0.0), 0.0), 1.0)
    tax_holiday_years = max(int(_number((tax_spec or {}).get("tax_holiday_years"), 0)), 0)
    tax_half_years = max(int(_number((tax_spec or {}).get("tax_half_years"), 0)), 0)

    def effective_tax_rate(index: int) -> float:
        if index < tax_holiday_years:
            return 0.0
        if index < tax_holiday_years + tax_half_years:
            return income_tax_rate / 2.0
        return income_tax_rate

    project_tax: list[float] = []
    project_cfads: list[float] = []
    pre_tax_operating: list[float] = []
    after_tax_operating: list[float] = []
    for index in range(years):
        ebitda = owner_revenue[index] - owner_opex[index]
        taxable = max(ebitda - depreciation_values[index], 0.0)
        tax = taxable * effective_tax_rate(index)
        cfads = ebitda - tax - maintenance[index]
        project_tax.append(tax)
        project_cfads.append(cfads)
        pre_tax_operating.append(ebitda - maintenance[index] + lease_adjustments[index])
        after_tax_operating.append(cfads + lease_adjustments[index])
    exit_year = int(_number(transaction.get("exit_year"), years))
    exit_value = _number(transaction.get("exit_value"))
    exit_cost = _number(transaction.get("exit_cost_wan")) + exit_value * min(
        max(_number(transaction.get("exit_cost_rate")), 0.0), 1.0,
    )
    exit_tax = _number(transaction.get("exit_tax_wan")) + exit_value * min(
        max(_number(transaction.get("exit_tax_rate")), 0.0), 1.0,
    )
    net_exit_value = max(exit_value - exit_cost - exit_tax, 0.0)
    if 1 <= exit_year <= years:
        pre_tax_operating[exit_year - 1] += max(exit_value - exit_cost, 0.0)
        after_tax_operating[exit_year - 1] += net_exit_value

    financing_ratio = _number(transaction.get("financing_ratio"))
    debt = total_acquisition_cost * financing_ratio
    equity = total_acquisition_cost - debt
    rate = _number(transaction.get("interest_rate"))
    tenor = int(_number(transaction.get("tenor"), 0))
    repayment_requested = str(transaction.get("repayment") or "equal_principal")
    supported_repayments = {"equal_principal", "equal_payment", "annuity", "bullet", "interest_only"}
    unresolved_repayment = repayment_requested not in supported_repayments
    if unresolved_repayment and spec.get("confirmation_status") == "confirmed":
        raise AcquisitionModelError(f"unsupported repayment method: {repayment_requested}")
    # Candidate runs remain calculable for comparison, but the provisional
    # assumption is explicit and validate_for_formal keeps the run unapprovable.
    repayment = "equal_principal" if unresolved_repayment else repayment_requested
    debt_schedule = _debt_schedule(debt, rate, tenor, years, repayment)
    project_pre_tax_cashflows = [-total_acquisition_cost, *pre_tax_operating]
    project_cashflows = [-total_acquisition_cost, *after_tax_operating]
    equity_cashflows = [-equity]
    equity_tax: list[float] = []
    dscr_values: list[float | None] = []
    icr_values: list[float | None] = []
    for index, row in enumerate(debt_schedule):
        service = row["debt_service_wan"]
        interest = row["interest_wan"]
        ebitda = owner_revenue[index] - owner_opex[index]
        taxable = max(ebitda - depreciation_values[index] - interest, 0.0)
        tax = taxable * effective_tax_rate(index)
        equity_tax.append(tax)
        recurring_cfads = ebitda - tax - maintenance[index]
        exit_cash = net_exit_value if index + 1 == exit_year else 0.0
        equity_cashflows.append(
            recurring_cfads + lease_adjustments[index] + exit_cash - service
        )
        dscr_values.append(recurring_cfads / service if service > 0 else None)
        icr_values.append(ebitda / interest if interest > 0 else None)

    project_irr = _safe_irr(project_cashflows)
    equity_irr = _safe_irr(equity_cashflows)
    payback = payback_period(project_cashflows, rate=discount_rate)
    rent_coverages = []
    for index, rent in enumerate(lease_rent):
        ebitdar = _number((hotel_rows[index] if index < len(hotel_rows) else {}).get("ebitdar_wan"))
        rent_coverages.append(ebitdar / rent if rent > 0 else None)
    exit_npv = net_exit_value / ((1 + discount_rate) ** exit_year) if exit_year else 0.0
    total_positive_npv = sum(
        max(value, 0.0) / ((1 + discount_rate) ** index)
        for index, value in enumerate(project_cashflows)
    )
    return {
        "available": True,
        "model_version": "acquisition_model.v2",
        "spec_version": LATEST_SPEC_VERSION,
        "scenario_id": scenario_id,
        "confirmation_status": spec.get("confirmation_status"),
        # Confirmation proves who accepted the input snapshot; it does not prove
        # the resulting tax/lease/transaction conclusion is business-valid.
        "business_review_status": "pending",
        "purchase_price_wan": purchase_price,
        "transaction_tax_wan": transaction_tax,
        "total_acquisition_cost_wan": total_acquisition_cost,
        "hotel_operation": hotel,
        "lease_portfolio": leases,
        "depreciation_schedule": depreciation,
        "tax_schedule": {
            "income_tax_rate": income_tax_rate,
            "tax_holiday_years": tax_holiday_years,
            "tax_half_years": tax_half_years,
            "project_income_tax_wan": project_tax,
            "equity_income_tax_wan": equity_tax,
        },
        "debt_schedule": debt_schedule,
        "project_pre_tax_cashflows_wan": project_pre_tax_cashflows,
        "project_cashflows_wan": project_cashflows,
        "equity_cashflows_wan": equity_cashflows,
        "owner_revenue_wan": owner_revenue,
        "owner_operating_cost_wan": owner_opex,
        "project_cfads_wan": project_cfads,
        "net_exit_value_wan": net_exit_value,
        "indicators": {
            "project_irr_pct": project_irr * 100 if project_irr is not None else None,
            "equity_irr_pct": equity_irr * 100 if equity_irr is not None else None,
            "npv_wan": npv(project_cashflows, discount_rate),
            "static_payback_years": payback.static_years,
            "dynamic_payback_years": payback.dynamic_years,
            "minimum_dscr": min((value for value in dscr_values if value is not None), default=None),
            "minimum_icr": min((value for value in icr_values if value is not None), default=None),
            "minimum_tenant_rent_coverage": min((value for value in rent_coverages if value is not None), default=None),
            "lease_coverage_years": leases.get("lease_coverage_years"),
            "contract_income_ratio": leases.get("contract_income_ratio"),
            "unlocked_income_ratio": leases.get("unlocked_income_ratio"),
            "maintenance_capex_coverage": (
                sum(project_cfads) / sum(maintenance) if sum(maintenance) > 0 else None
            ),
            "exit_value_npv_ratio": exit_npv / total_positive_npv if total_positive_npv > 0 else None,
        },
        "assumptions": [
            "收购价、租金、ADR、入住率、融资、税费、维修资本开支和退出价值按独立维度计算",
            *([] if not unresolved_repayment else [f"还款方式 {repayment_requested} 尚未裁决，candidate暂按equal_principal试算"]),
            *([] if depreciation["classes"] else ["未提供可折旧资产分类基础/年限，未臆造折旧；正式批准前须补证据或裁决不适用"]),
            *([] if uses_contract_rent else ["缺合同租金时，以承租人可支付租金作为candidate，必须业务复核"]),
        ],
    }


def apply_scenario(spec: dict[str, Any], changes: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply independent scenario changes and return a before/after ledger."""

    unknown = sorted(set(changes) - INDEPENDENT_SCENARIO_FIELDS)
    if unknown:
        raise AcquisitionModelError(f"unsupported or coupled scenario fields: {unknown}")
    out = copy.deepcopy(spec)
    ledger: list[dict[str, Any]] = []
    for path, change in sorted(changes.items()):
        section, field = path.split(".", 1)
        container = out.setdefault(section, {})
        if not isinstance(container, dict):
            raise AcquisitionModelError(f"scenario target is not an object: {section}")
        metadata = change if isinstance(change, dict) and "value" in change else {}
        after = copy.deepcopy(metadata.get("value") if metadata else change)
        before = copy.deepcopy(container.get(field))
        container[field] = after
        ledger.append({
            "field": path, "before": before, "after": copy.deepcopy(after),
            "source": str(metadata.get("source") or ""),
            "proposed_by": str(metadata.get("proposed_by") or ""),
            "approved_by": str(metadata.get("approved_by") or ""),
            "approval_reason": str(metadata.get("approval_reason") or ""),
            "result_impact": copy.deepcopy(metadata.get("result_impact")),
        })
    return out, ledger


def solve_max_acquisition_price(
    spec: dict[str, Any], *, target_irr: float = 0.08, min_dscr: float | None = None,
    lower: float = 0.0, upper: float | None = None, tolerance_wan: float = 0.01,
    max_iterations: int = 100,
) -> dict[str, Any]:
    transaction = dict(spec.get("transaction") or {})
    current = _number(transaction.get("purchase_price"))
    high = _number(upper, max(current * 3.0, 1.0)) if upper is not None else max(current * 3.0, 1.0)
    low = max(_number(lower), 0.0)
    iterations = 0
    best: dict[str, Any] | None = None

    def evaluate(price: float) -> tuple[bool, dict[str, Any]]:
        candidate = copy.deepcopy(spec)
        candidate.setdefault("transaction", {})["purchase_price"] = max(price, 1e-9)
        result = run_acquisition_model(candidate, scenario_id="max_price_solver")
        project_irr_pct = result["indicators"].get("project_irr_pct")
        dscr = result["indicators"].get("minimum_dscr")
        meets_irr = project_irr_pct is not None and project_irr_pct / 100.0 >= target_irr
        meets_dscr = min_dscr is None or (dscr is not None and dscr >= min_dscr)
        return bool(meets_irr and meets_dscr), result

    # When the caller did not impose an upper bound, expand until a failing
    # price brackets the feasible region.  The old implementation silently
    # returned ``3 * current price`` even when that price still met the target.
    bracketed = True
    bounded_by_upper = upper is not None
    high_feasible, high_result = evaluate(high)
    if upper is None:
        expansions = 0
        while high_feasible and expansions < 32:
            low = high
            best = high_result
            high *= 2.0
            high_feasible, high_result = evaluate(high)
            expansions += 1
        bracketed = not high_feasible
        if not bracketed:
            return {
                "converged": False, "feasible": True, "bracketed": False,
                "max_acquisition_price_wan": low, "target_irr": target_irr,
                "min_dscr": min_dscr, "solve_interval_wan": [low, high],
                "iterations": iterations, "tolerance_wan": tolerance_wan,
                "scenario_id": "max_price_solver", "bounded_by_upper": False,
                "reason": "feasible_region_not_bracketed",
                "indicators_at_solution": (best or {}).get("indicators") or {},
            }
    elif high_feasible:
        best = high_result
        return {
            "converged": True, "feasible": True, "bracketed": False,
            "max_acquisition_price_wan": high, "target_irr": target_irr,
            "min_dscr": min_dscr, "solve_interval_wan": [low, high],
            "iterations": iterations, "tolerance_wan": tolerance_wan,
            "scenario_id": "max_price_solver", "bounded_by_upper": True,
            "reason": "caller_upper_bound_is_feasible",
            "indicators_at_solution": high_result.get("indicators") or {},
        }
    while iterations < max_iterations and high - low > tolerance_wan:
        iterations += 1
        mid = (low + high) / 2.0
        feasible, result = evaluate(mid)
        if feasible:
            low = mid
            best = result
        else:
            high = mid
    return {
        "converged": best is not None and high - low <= tolerance_wan,
        "feasible": best is not None,
        "bracketed": bracketed,
        "max_acquisition_price_wan": low,
        "target_irr": target_irr,
        "min_dscr": min_dscr,
        "solve_interval_wan": [low, high],
        "iterations": iterations,
        "tolerance_wan": tolerance_wan,
        "scenario_id": "max_price_solver",
        "bounded_by_upper": bounded_by_upper,
        "reason": "converged" if best is not None and high - low <= tolerance_wan else "no_feasible_price_in_interval",
        "indicators_at_solution": (best or {}).get("indicators") or {},
    }
