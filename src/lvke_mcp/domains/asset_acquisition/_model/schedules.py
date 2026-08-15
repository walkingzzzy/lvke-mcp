"""年度与月度的债务、折旧与租金收入计划表。"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any



from .base import (
    AcquisitionModelError,
    _number,
)

from .hotel_lease import (
    _lease_annual_base,
)

from .period_dates import (
    _add_months,
    _date_value,
    _month_overlap_days,
)


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
    # Confirmed acquisition specs may carry the PPA/depreciation classification
    # at the spec root. Normalize that contract into the model's transaction
    # schedule instead of silently emitting an all-zero table when asset_scope
    # is intentionally reserved for transaction-boundary evidence.
    classified = transaction.get("depreciation_schedule")
    if not candidates and isinstance(classified, dict):
        for index, item in enumerate(classified.get("classes") or []):
            if not isinstance(item, dict):
                continue
            original = item.get("original_value_wan", item.get("basis_wan"))
            life = item.get("useful_life_years", item.get("depreciation_years"))
            candidate = {
                "scope_id": item.get("scope_id") or item.get("name") or f"classified-asset-{index + 1}",
                "depreciable_basis_wan": original,
                "depreciation_years": life,
                "residual_rate": item.get("residual_rate", item.get("salvage_rate", 0.0)),
                "depreciation_start_year": item.get("depreciation_start_year", 1),
                "included": True,
            }
            if _number(original) > 0 and int(_number(life)) > 0:
                candidates.append(candidate)
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
