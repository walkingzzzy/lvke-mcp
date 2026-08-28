"""Deterministic advanced analysis derived from an immutable finance run."""

from __future__ import annotations

import math
import random
from typing import Any, Callable


MONTE_CARLO_FIELDS = {
    "revenue_scale",
    "operating_cost_scale",
    "construction_scale",
}


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _period_value(rows: list[dict[str, Any]], index: int, *keys: str) -> float:
    if index < 0 or index >= len(rows):
        return 0.0
    row = rows[index]
    for key in keys:
        if row.get(key) is not None:
            return _number(row[key])
    return 0.0


def build_balance_sheet_schedule(run: dict[str, Any]) -> dict[str, Any]:
    """Build a reproducible balance-sheet schedule without hiding residual equity."""

    annual = run.get("annual") if isinstance(run.get("annual"), dict) else {}
    plan = annual.get("financial_plan") if isinstance(annual.get("financial_plan"), list) else []
    if not plan:
        return {
            "available": False,
            "reason": "financial_plan_missing",
            "missing_inputs": ["annual.financial_plan"],
            "rows": [],
        }

    investment = run.get("investment") if isinstance(run.get("investment"), dict) else {}
    funding = run.get("funding") if isinstance(run.get("funding"), dict) else {}
    params = run.get("params") if isinstance(run.get("params"), dict) else {}
    raw = run.get("raw") if isinstance(run.get("raw"), dict) else {}
    build_years = max(int(_number(params.get("build_years")) or 1), 1)
    fixed_asset_gross = _number(investment.get("fixed_asset"))
    working_capital_total = _number(investment.get("working_capital"))
    capital_total = _number(funding.get("capital"))
    subsidy_total = _number(funding.get("subsidy"))
    loan_total = _number(funding.get("loan"))
    depreciation = annual.get("depreciation_table") if isinstance(annual.get("depreciation_table"), list) else []
    amortization = annual.get("amortization_table") if isinstance(annual.get("amortization_table"), list) else []
    profits = annual.get("profit_distribution") if isinstance(annual.get("profit_distribution"), list) else []
    income = annual.get("income_statement") if isinstance(annual.get("income_statement"), list) else []
    debt = annual.get("debt_service") if isinstance(annual.get("debt_service"), list) else []
    intangible_gross = max((_number(row.get("base")) for row in amortization), default=0.0)

    rows: list[dict[str, Any]] = []
    cumulative_profit = 0.0
    cumulative_amortization = 0.0
    cumulative_cip = 0.0
    cumulative_debt_draw = 0.0
    cumulative_capital = 0.0
    cumulative_subsidy = 0.0
    for index, plan_row in enumerate(plan):
        period = int(_number(plan_row.get("period")) or index + 1)
        construction_phase = index < build_years or plan_row.get("phase") == "建设期"
        operating_index = index - build_years
        final_period = index == len(plan) - 1

        raw_cash = (
            _number(plan_row.get("cash_end"))
            if plan_row.get("cash_end") is not None
            else _number(plan_row.get("cumulative"))
        )
        cash = max(raw_cash, 0.0)
        cash_deficit = max(-raw_cash, 0.0)
        if construction_phase:
            progress = min((index + 1) / build_years, 1.0)
            confirmed_schedule = plan_row.get("funding_plan_source") == "confirmed_annual_schedule"
            if confirmed_schedule:
                cumulative_cip += _number(plan_row.get("construction_investment"))
                cumulative_cip += _number(plan_row.get("construction_interest"))
                cumulative_debt_draw += _number(plan_row.get("loan_draw"))
                cumulative_capital += _number(plan_row.get("capital_own"))
                cumulative_subsidy += _number(plan_row.get("gov_subsidy"))
                construction_in_progress = cumulative_cip
                outstanding_debt = cumulative_debt_draw
                contributed_capital = cumulative_capital
                contributed_subsidy = cumulative_subsidy
            else:
                construction_in_progress = fixed_asset_gross * progress
                outstanding_debt = loan_total * progress
                contributed_capital = capital_total * progress
                contributed_subsidy = subsidy_total * progress
            net_fixed_assets = 0.0
            # CIP already contains the complete fixed-asset gross amount,
            # including the intangible portion split out on commissioning.
            net_intangible_assets = 0.0
            working_capital = 0.0
        else:
            construction_in_progress = 0.0
            dep_net = _period_value(depreciation, operating_index, "net_value")
            net_fixed_assets = dep_net if depreciation else fixed_asset_gross
            amortization_charge = _period_value(amortization, operating_index, "amortization")
            cumulative_amortization = min(
                intangible_gross,
                cumulative_amortization + amortization_charge,
            )
            net_intangible_assets = max(intangible_gross - cumulative_amortization, 0.0)
            working_capital = working_capital_total
            outstanding_debt = _period_value(debt, operating_index, "end", "closing_balance")
            contributed_capital = capital_total
            contributed_subsidy = subsidy_total
            cumulative_profit += _period_value(profits, operating_index, "net_profit")
            if final_period:
                # The financial plan includes terminal recovery in cash.
                fixed_recovery = _number(raw.get("terminal_recovery"))
                if fixed_recovery:
                    # Derecognize closing book value; only the disposal
                    # gain/loss changes retained earnings.
                    cumulative_profit += fixed_recovery - net_fixed_assets
                net_fixed_assets = 0.0
                working_capital = 0.0

        deferred_tax_asset = 0.0
        deferred_tax_liability = 0.0
        if not construction_phase:
            deferred_balance = _period_value(income, operating_index, "deferred_tax_liability")
            if deferred_balance >= 0:
                deferred_tax_liability = deferred_balance
            else:
                deferred_tax_asset = abs(deferred_balance)
        total_assets = (
            cash
            + construction_in_progress
            + net_fixed_assets
            + net_intangible_assets
            + working_capital
            + deferred_tax_asset
        )
        recorded_equity = contributed_capital + contributed_subsidy + cumulative_profit
        total_liabilities = outstanding_debt + cash_deficit + deferred_tax_liability
        calculated_equity_residual = total_assets - total_liabilities
        reconciliation_delta = calculated_equity_residual - recorded_equity
        row = {
            "period": period,
            "phase": "construction" if construction_phase else "operation",
            "cash_wan": round(cash, 2),
            "cash_deficit_wan": round(cash_deficit, 2),
            "construction_in_progress_wan": round(construction_in_progress, 2),
            "net_fixed_assets_wan": round(net_fixed_assets, 2),
            "net_intangible_assets_wan": round(net_intangible_assets, 2),
            "working_capital_wan": round(working_capital, 2),
            "deferred_tax_asset_wan": round(deferred_tax_asset, 2),
            "deferred_tax_liability_wan": round(deferred_tax_liability, 2),
            "total_assets_wan": round(total_assets, 2),
            "outstanding_debt_wan": round(outstanding_debt, 2),
            "total_liabilities_wan": round(total_liabilities, 2),
            "contributed_capital_wan": round(contributed_capital, 2),
            "subsidy_equity_wan": round(contributed_subsidy, 2),
            "cumulative_retained_earnings_wan": round(cumulative_profit, 2),
            "recorded_equity_wan": round(recorded_equity, 2),
            "calculated_equity_residual_wan": round(calculated_equity_residual, 2),
            "equity_reconciliation_delta_wan": round(reconciliation_delta, 2),
            "balance_ok": abs(total_assets - total_liabilities - calculated_equity_residual) <= 0.01,
            "equity_reconciliation_ok": abs(reconciliation_delta) <= 0.10,
        }
        rows.append(row)

    mismatches = [row["period"] for row in rows if not row["equity_reconciliation_ok"]]
    cash_deficit_periods = [row["period"] for row in rows if row["cash_deficit_wan"] > 0]
    return {
        "available": True,
        "method": "finance_run_schedule_reconciliation.v1",
        "currency_unit": "wan_yuan",
        "rows": rows,
        "balance_ok": all(row["balance_ok"] for row in rows),
        "equity_reconciliation_ok": not mismatches,
        "mismatch_periods": mismatches,
        "cash_deficit_periods": cash_deficit_periods,
        "formal_ready": not mismatches and not cash_deficit_periods,
        "formula_lineage": {
            "cash": "annual.financial_plan.cash_end|cumulative",
            "fixed_assets": "annual.depreciation_table.net_value",
            "intangible_assets": "annual.amortization_table.base-amortization",
            "working_capital": "investment.working_capital",
            "debt": "annual.debt_service.end",
            "retained_earnings": "cumulative annual.profit_distribution.net_profit",
            "terminal_disposal_gain": "raw.terminal_recovery-closing fixed-asset book value",
            "cash_deficit": "max(-annual.financial_plan.cash_end|cumulative,0)",
            "equity_residual": "total_assets-total_liabilities",
        },
    }


def validate_distribution_manifest(distributions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, distribution in enumerate(distributions):
        path = f"/distributions/{index}"
        field = str(distribution.get("field") or "")
        kind = str(distribution.get("distribution") or "")
        if field not in MONTE_CARLO_FIELDS:
            errors.append({"path": f"{path}/field", "code": "field_not_allowed"})
        if field in seen:
            errors.append({"path": f"{path}/field", "code": "duplicate_field"})
        seen.add(field)
        if kind == "uniform":
            low, high = _number(distribution.get("low")), _number(distribution.get("high"))
            if low <= 0 or high < low:
                errors.append({"path": path, "code": "invalid_uniform_bounds"})
        elif kind == "triangular":
            low = _number(distribution.get("low"))
            mode = _number(distribution.get("mode"))
            high = _number(distribution.get("high"))
            if low <= 0 or not low <= mode <= high:
                errors.append({"path": path, "code": "invalid_triangular_bounds"})
        elif kind == "normal":
            mean = _number(distribution.get("mean"))
            stddev = _number(distribution.get("stddev"))
            low = _number(distribution.get("low"))
            high = _number(distribution.get("high"))
            if stddev <= 0 or low <= 0 or high < low or not low <= mean <= high:
                errors.append({"path": path, "code": "invalid_normal_bounds"})
        else:
            errors.append({"path": f"{path}/distribution", "code": "unknown_distribution"})
    return errors


def _sample_distribution(rng: random.Random, value: dict[str, Any]) -> float:
    kind = value["distribution"]
    low, high = float(value["low"]), float(value["high"])
    if kind == "uniform":
        return rng.uniform(low, high)
    if kind == "triangular":
        return rng.triangular(low, high, float(value["mode"]))
    mean, stddev = float(value["mean"]), float(value["stddev"])
    for _ in range(10_000):
        candidate = rng.normalvariate(mean, stddev)
        if low <= candidate <= high:
            return candidate
    return min(max(mean, low), high)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def run_monte_carlo(
    *,
    distributions: list[dict[str, Any]],
    sample_count: int,
    seed: int,
    rerun: Callable[[dict[str, float]], dict[str, Any] | None],
) -> dict[str, Any]:
    """Run deterministic in-memory samples and retain only aggregate results."""

    errors = validate_distribution_manifest(distributions)
    if errors:
        return {"available": False, "field_errors": errors}
    rng = random.Random(seed)
    irr_values: list[float] = []
    npv_values: list[float] = []
    failures: dict[str, int] = {}
    for _ in range(sample_count):
        scales = {
            value["field"]: _sample_distribution(rng, value)
            for value in distributions
        }
        result = rerun(scales)
        if not isinstance(result, dict) or not result.get("available"):
            failures["model_unavailable"] = failures.get("model_unavailable", 0) + 1
            continue
        indicators = result.get("indicators") if isinstance(result.get("indicators"), dict) else {}
        irr = indicators.get("project_irr_pct")
        npv = indicators.get("npv_wan")
        if not isinstance(irr, (int, float)) or not isinstance(npv, (int, float)):
            failures["indicator_unavailable"] = failures.get("indicator_unavailable", 0) + 1
            continue
        irr_values.append(float(irr))
        npv_values.append(float(npv))

    def summary(values: list[float]) -> dict[str, float | None]:
        return {
            "p5": round(percentile(values, 0.05), 6) if values else None,
            "p50": round(percentile(values, 0.50), 6) if values else None,
            "p95": round(percentile(values, 0.95), 6) if values else None,
        }

    return {
        "available": bool(irr_values),
        "sample_count": sample_count,
        "successful_sample_count": len(irr_values),
        "failed_sample_count": sample_count - len(irr_values),
        "failure_categories": failures,
        "seed": seed,
        "project_irr_pct": summary(irr_values),
        "npv_wan": summary(npv_values),
        "samples_persisted": False,
    }
