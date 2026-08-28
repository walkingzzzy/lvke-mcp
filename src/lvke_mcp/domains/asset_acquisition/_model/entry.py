"""模型入口、情景应用与最高可接受价求解。"""

from __future__ import annotations

import copy
from typing import Any

from lvke_mcp.domains.finance.calculations import npv, payback_period

from lvke_mcp.domains.finance.spec import LATEST_SPEC_VERSION, validate

from .balance_sheet import roll_annual_balance_sheet
from .base import (
    AcquisitionModelError,
    INDEPENDENT_SCENARIO_FIELDS,
    _number,
    _safe_irr,
    _series,
)

from .hotel_lease import (
    calculate_hotel_operation,
    calculate_lease_portfolio,
)

from .monthly_engine import (
    _run_monthly_acquisition_model,
)

from .schedules import (
    _debt_schedule,
    _depreciation_schedule,
)

from .solar_engine import (
    _run_solar_acquisition_model,
)


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
    depreciation = _depreciation_schedule(
        {**transaction, "depreciation_schedule": spec.get("depreciation_schedule")},
        years,
    )
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
    net_profit = [
        owner_revenue[index] - owner_opex[index] - depreciation_values[index] - project_tax[index]
        for index in range(years)
    ]
    closing_debt = [
        float(row.get("closing_principal_wan") or 0.0) for row in debt_schedule
    ]
    annual_summary = roll_annual_balance_sheet(
        years=years,
        total_cost=total_acquisition_cost,
        opening_equity=equity,
        equity_cf=equity_cashflows[1:],
        net_profit=net_profit,
        depreciation=depreciation_values,
        closing_debt=closing_debt,
        year_meta=[
            {
                "year": index + 1,
                "year_index": index + 1,
                "revenue_wan": owner_revenue[index],
                "operating_cost_wan": owner_opex[index],
                "depreciation_wan": depreciation_values[index],
                "income_tax_wan": project_tax[index],
                "net_profit_wan": net_profit[index],
                "project_cf_wan": after_tax_operating[index],
                "equity_cf_wan": equity_cashflows[index + 1],
                "debt_service_wan": debt_schedule[index]["debt_service_wan"],
                "interest_wan": debt_schedule[index]["interest_wan"],
                "maintenance_capex_wan": maintenance[index],
            }
            for index in range(years)
        ],
    )
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
        "validation_status": "calculated",
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
        "annual_summary": annual_summary,
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
            *([] if depreciation["classes"] else ["未提供可折旧资产分类基础/年限，未臆造折旧；须补充证据或明确标记不适用"]),
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
            "rationale": str(metadata.get("rationale") or ""),
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
