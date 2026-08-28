"""酒店月度收购模型引擎。"""

from __future__ import annotations

import calendar
from typing import Any

from lvke_mcp.domains.finance.calculations import payback_period

from lvke_mcp.domains.finance.spec import LATEST_SPEC_VERSION

from .base import (
    AcquisitionModelError,
    _number,
    _safe_irr,
    _series,
)

from .period_dates import (
    _add_months,
    _date_value,
    _month_end,
    _month_overlap_days,
    _month_start,
)

from .schedules import (
    _depreciation_schedule,
    _monthly_debt_schedule,
    _monthly_lease_income,
)


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
    depreciation = _depreciation_schedule(
        {**transaction, "depreciation_schedule": spec.get("depreciation_schedule")},
        years,
    )
    annual_depreciation = depreciation.get("annual_depreciation_wan") or [0.0] * years
    cost = spec.get("cost") or {}
    cost_items = cost.get("cost_items") if isinstance(cost, dict) else {}
    annual_owner_opex = _number((cost or {}).get("annual_owner_operating_cost_wan")) or sum(
        _number(value) for value in (cost_items or {}).values()
    )
    tax_cfg = spec.get("tax") or {}
    income_tax_rate = min(max(_number(tax_cfg.get("income_tax_rate")), 0.0), 1.0)
    vat_rate = min(max(_number(tax_cfg.get("vat_rate")), 0.0), 1.0)
    surtax_rate = min(max(_number(tax_cfg.get("surtax_rate")), 0.0), 1.0)
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
            "vat_wan": 0.0,
            "surtax_wan": 0.0,
            "loss_carryforward_wan": 0.0,
            "depreciation_wan": 0.0,
            "net_profit_wan": 0.0,
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
    loss_carryforward = 0.0
    cash = 0.0
    retained_earnings = 0.0
    cumulative_depreciation = 0.0
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
        taxable_raw = revenue - operating_cost - depreciation_month
        if taxable_raw < 0:
            loss_carryforward += -taxable_raw
            taxable = 0.0
        else:
            used = min(loss_carryforward, taxable_raw)
            loss_carryforward -= used
            taxable = taxable_raw - used
        tax = taxable * income_tax_rate
        vat = revenue * vat_rate
        surtax = vat * surtax_rate
        net_profit = taxable_raw - tax
        cfads = revenue - operating_cost - tax - surtax - maintenance_capex
        exit_cash = net_exit if index == exit_month else 0.0
        debt_row = debt_rows[index]
        project_cf = cfads + lease_adjustment + exit_cash
        equity_cf = project_cf - debt_row["debt_service_wan"]
        project_monthly.append(project_cf)
        equity_monthly.append(equity_cf)
        cash = round(cash + equity_cf, 2)
        cumulative_depreciation = round(cumulative_depreciation + depreciation_month, 2)
        retained_earnings = round(retained_earnings + net_profit, 2)
        fixed_asset_net = round(max(total_cost - cumulative_depreciation, 0.0), 2)
        debt_wan = round(float(debt_row.get("closing_principal_wan") or 0.0), 2)
        equity_wan = round(equity + retained_earnings, 2)
        total_assets = round(cash + fixed_asset_net, 2)
        total_le = round(debt_wan + equity_wan, 2)
        bucket = annual[model_year]
        for key, value in {
            "hotel_revenue_wan": hotel_revenue, "lease_revenue_wan": lease_revenue,
            "revenue_wan": revenue,
            "operating_cost_wan": operating_cost, "tax_wan": tax,
            "income_tax_wan": tax,
            "maintenance_capex_wan": maintenance_capex, "project_cf_wan": project_cf,
            "equity_cf_wan": equity_cf, "debt_service_wan": debt_row["debt_service_wan"],
            "interest_wan": debt_row["interest_wan"],
            "vat_wan": vat,
            "surtax_wan": surtax,
            "depreciation_wan": depreciation_month,
            "net_profit_wan": net_profit,
        }.items():
            bucket[key] += value
        bucket["loss_carryforward_wan"] = loss_carryforward
        bucket["year"] = model_year + 1
        bucket["cash_wan"] = cash
        bucket["fixed_asset_net_wan"] = fixed_asset_net
        bucket["total_assets_wan"] = total_assets
        bucket["debt_wan"] = debt_wan
        bucket["equity_wan"] = equity_wan
        bucket["total_liabilities_equity_wan"] = total_le
        monthly_rows.append({
            "month": index + 1, "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
            "active_days": owner_days, "hotel_days": hotel_days,
            "operating_mode": operating_mode, "hotel_revenue_wan": hotel_revenue,
            "hotel_cost_wan": hotel_cost, "lease_revenue_wan": lease_revenue,
            "lease_adjustment_wan": lease_adjustment,
            "operating_cost_wan": operating_cost,
            "tax_wan": tax, "income_tax_wan": tax,
            "vat_wan": vat, "surtax_wan": surtax,
            "loss_carryforward_wan": loss_carryforward,
            "interest_wan": debt_row["interest_wan"],
            "depreciation_wan": depreciation_month,
            "net_profit_wan": net_profit,
            "maintenance_capex_wan": maintenance_capex,
            "project_cf_wan": project_cf, "equity_cf_wan": equity_cf,
            "debt_service_wan": debt_row["debt_service_wan"], "dscr": cfads / debt_row["debt_service_wan"] if debt_row["debt_service_wan"] else None,
            "cash_wan": cash,
            "fixed_asset_net_wan": fixed_asset_net,
            "total_assets_wan": total_assets,
            "debt_wan": debt_wan,
            "equity_wan": equity_wan,
            "total_liabilities_equity_wan": total_le,
        })
        cursor = _add_months(cursor, 1)
    project_annual = [-total_cost, *[row["project_cf_wan"] for row in annual]]
    equity_annual = [-equity, *[row["equity_cf_wan"] for row in annual]]
    monthly_irr = _safe_irr(project_monthly)
    monthly_equity_irr = _safe_irr(equity_monthly)
    annual_irr = _safe_irr(project_annual) if monthly_irr is None else None
    annual_equity_irr = _safe_irr(equity_annual) if monthly_equity_irr is None else None
    monthly_rate = (1 + discount_rate) ** (1 / 12) - 1
    annual_dscr = [
        (row["project_cf_wan"] + row["debt_service_wan"]) / row["debt_service_wan"]
        if row["debt_service_wan"] else None
        for row in annual
    ]
    return {
        "available": True, "model_version": "acquisition_model.v3", "spec_version": LATEST_SPEC_VERSION,
        "scenario_id": scenario_id, "confirmation_status": spec.get("confirmation_status"),
        "validation_status": "calculated", "operating_mode": operating_mode,
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
            "project_irr_pct": (
                ((1 + monthly_irr) ** 12 - 1) * 100
                if monthly_irr is not None
                else annual_irr * 100 if annual_irr is not None else None
            ),
            "equity_irr_pct": (
                ((1 + monthly_equity_irr) ** 12 - 1) * 100
                if monthly_equity_irr is not None
                else annual_equity_irr * 100 if annual_equity_irr is not None else None
            ),
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
