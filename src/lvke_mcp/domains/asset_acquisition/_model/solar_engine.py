"""光伏年度运营收购模型引擎。"""

from __future__ import annotations

from typing import Any

from lvke_mcp.domains.finance.calculations import npv, payback_period

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
    _month_start,
)

from .schedules import (
    _depreciation_schedule,
    _monthly_debt_schedule,
)


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
        "validation_status": "calculated", "calculation_granularity": "annual",
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
