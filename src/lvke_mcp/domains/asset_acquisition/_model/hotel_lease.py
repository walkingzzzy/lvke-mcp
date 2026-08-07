"""酒店经营与租约组合：年度租金基数、递增与合同租金。"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any



from .base import (
    AcquisitionModelError,
    _number,
    _series,
)

from .period_dates import (
    _add_months,
    _date,
)


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
