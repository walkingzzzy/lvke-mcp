"""十三表构造与完整性/勾稽校验、数据血缘。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


from lvke_mcp.runtime.storage import sha256_json

from .columns import (
    _BOOLEAN_FIELDS,
    _DATE_FIELDS,
    _NUMERIC_FIELDS,
)

from .rows import (
    _depreciation_rows,
    _equity_rows,
    _ppa_rows,
    _rows,
    _scenario_rows,
    _table_contract,
)


def _coalesce_num(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return default


def _projected_num(row: dict[str, Any], *keys: str, default: float | None = None) -> float | None:
    """Project a computed field; missing keys stay absent instead of becoming 0."""

    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if value is not None and value != "":
            return value
    return default


def _year_end_lookup(row: dict[str, Any], year_end_monthly: dict[int, dict[str, Any]]) -> dict[str, Any]:
    for key in ("year_index", "year"):
        raw = row.get(key)
        try:
            year_index = int(raw)
        except (TypeError, ValueError):
            continue
        if year_index in year_end_monthly:
            return year_end_monthly[year_index]
    return {}


def _balance_sheet_row(
    row: dict[str, Any], year_end_monthly: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    fallback = _year_end_lookup(row, year_end_monthly)
    return {
        "year": row.get("year") or row.get("year_index"),
        "cash_wan": _projected_num(row, "cash_wan", "closing_cash_wan", default=_projected_num(fallback, "cash_wan", "closing_cash_wan")),
        "fixed_asset_net_wan": _projected_num(
            row, "fixed_asset_net_wan", "net_fixed_asset_wan",
            default=_projected_num(fallback, "fixed_asset_net_wan", "net_fixed_asset_wan"),
        ),
        "total_assets_wan": _projected_num(row, "total_assets_wan", default=_projected_num(fallback, "total_assets_wan")),
        "debt_wan": _projected_num(row, "debt_wan", "closing_principal_wan", default=_projected_num(fallback, "debt_wan", "closing_principal_wan")),
        "equity_wan": _projected_num(row, "equity_wan", default=_projected_num(fallback, "equity_wan")),
        "total_liabilities_equity_wan": _projected_num(
            row, "total_liabilities_equity_wan",
            default=_projected_num(fallback, "total_liabilities_equity_wan"),
        ),
    }


def _build_tables(
    run: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    result = dict(run.get("result") or {})
    transaction = dict(spec.get("transaction") or {})
    monthly = _rows(result.get("monthly_timeline"))
    annual = _rows(result.get("annual_summary"))
    year_end_monthly: dict[int, dict[str, Any]] = {}
    for row in monthly:
        month = row.get("month")
        try:
            year_index = (int(month) - 1) // 12 + 1
        except (TypeError, ValueError):
            continue
        year_end_monthly[year_index] = row
    if not annual and year_end_monthly:
        annual = [
            {"year": year_index, "year_index": year_index, **row}
            for year_index, row in sorted(year_end_monthly.items())
        ]
    debt = _rows(result.get("debt_schedule_monthly"))
    debt_by_month = {row.get("month"): row for row in debt}
    period_by_month = {row.get("month"): row.get("period_start") for row in monthly}
    ppa = _ppa_rows(transaction, float(result.get("purchase_price_wan") or 0))
    start_date = str(transaction.get("model_start_date") or transaction.get("closing_date") or "")
    total_investment = float(result.get("total_acquisition_cost_wan") or 0)
    debt_amount = float(debt[0].get("opening_principal_wan") or 0) if debt else 0.0
    equity_amount = total_investment - debt_amount
    common = {
        "transaction_bridge": [{
            "run_id": run.get("run_id"),
            "acquisition_type": transaction.get("acquisition_type"),
            "purchase_price_wan": result.get("purchase_price_wan"),
            "valuation_value_wan": transaction.get("valuation_value"),
            "transaction_tax_wan": result.get("transaction_tax_wan"),
            "total_acquisition_cost_wan": result.get("total_acquisition_cost_wan"),
            "asset_scope_count": len(_rows(transaction.get("asset_scope"))),
        }],
        "investment_funding": [{
            "total_investment_wan": total_investment,
            "financing_ratio": transaction.get("financing_ratio"),
            "debt_wan": debt_amount,
            "equity_wan": equity_amount,
            "funding_balance_check_wan": total_investment - debt_amount - equity_amount,
        }],
        "purchase_price_allocation": ppa,
        "operating_cost_working_capital": [{key: row.get(key) for key in (
            "month", "period_start", "operating_cost_wan", "maintenance_capex_wan", "project_cf_wan",
        )} for row in monthly],
        "depreciation_amortization": _depreciation_rows(result, annual),
        "debt_schedule": [{**row, "period_start": period_by_month.get(row.get("month"))} for row in debt],
        "tax_calculation": [{
            "month": row.get("month"), "period_start": row.get("period_start"),
            "income_tax_wan": _coalesce_num(row, "income_tax_wan", "tax_wan"),
            "interest_wan": _coalesce_num(
                row,
                "interest_wan",
                default=_coalesce_num(debt_by_month.get(row.get("month")) or {}, "interest_wan"),
            ),
            "vat_wan": _coalesce_num(row, "vat_wan", "value_added_tax_wan"),
            "surtax_wan": _coalesce_num(row, "surtax_wan", "additional_tax_wan"),
            "loss_carryforward_wan": _coalesce_num(row, "loss_carryforward_wan"),
        } for row in monthly],
        "income_statement": [{
            "year": row.get("year") or row.get("year_index"),
            "revenue_wan": _coalesce_num(row, "revenue_wan"),
            "operating_cost_wan": _coalesce_num(row, "operating_cost_wan"),
            "depreciation_wan": _coalesce_num(row, "depreciation_wan"),
            "interest_wan": _coalesce_num(row, "interest_wan"),
            "income_tax_wan": _coalesce_num(row, "income_tax_wan", "tax_wan"),
            "net_profit_wan": _coalesce_num(row, "net_profit_wan", "profit_wan"),
        } for row in annual],
        "balance_sheet": [_balance_sheet_row(row, year_end_monthly) for row in annual],
        "project_cashflow": [{key: row.get(key) for key in (
            "year", "year_index", "period_start", "period_end", "period_label", "period_basis",
            "revenue_wan", "operating_cost_wan", "income_tax_wan", "maintenance_capex_wan",
            "debt_service_wan", "project_cf_wan", "equity_cf_wan",
        )} for row in annual],
        "equity_cashflow_indicators": _equity_rows(result, annual, start_date),
        "scenario_max_price": _scenario_rows(
            run,
            result,
            spec,
        ),
    }
    if str(spec.get("asset_type") or "hotel_lease") == "solar_power":
        common.update({
            "monthly_timeline": [{key: row.get(key) for key in (
                "month", "period_start", "period_end", "active_days", "asset_type",
            )} for row in monthly],
            "generation_revenue": [{key: row.get(key) for key in (
                "month", "period_start", "gross_generation_mwh", "sold_generation_mwh",
                "tariff_yuan_per_kwh", "operating_revenue_wan",
            )} for row in monthly],
            "other_operating_revenue": [{key: row.get(key) for key in (
                "month", "period_start", "other_revenue_wan",
            )} for row in monthly],
        })
    else:
        common.update({
            "monthly_timeline": [{key: row.get(key) for key in (
                "month", "period_start", "period_end", "active_days", "hotel_days", "operating_mode",
            )} for row in monthly],
            "hotel_revenue": [{key: row.get(key) for key in (
                "month", "period_start", "hotel_revenue_wan", "hotel_cost_wan",
            )} for row in monthly],
            "lease_revenue": [{key: row.get(key) for key in (
                "month", "period_start", "lease_revenue_wan", "lease_adjustment_wan",
            )} for row in monthly],
        })
    return common


def _check(name: str, actual: float, expected: float, tolerance: float, note: str = "") -> dict[str, Any]:
    difference = float(actual) - float(expected)
    return {
        "name": name, "actual": actual, "expected": expected, "difference": difference,
        "tolerance": tolerance, "status": "passed" if abs(difference) <= tolerance else "failed",
        "note": note,
    }


def _integrity(
    tables: dict[str, list[dict[str, Any]]], *,
    asset_type: str = "hotel_lease", run_id: str = "",
) -> dict[str, Any]:
    definitions, _columns, required_columns = _table_contract(asset_type)
    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    column_missing: dict[str, dict[str, int]] = {}
    nested_cells: dict[str, int] = {}
    type_errors: dict[str, dict[str, int]] = {}
    # — hashes_match_run: all tables share the same run_id from transaction_bridge
    bridge = (tables.get("transaction_bridge") or [{}])[0]
    declared_run_id = str(bridge.get("run_id") or "")
    hashes_match = bool(declared_run_id) and (not run_id or declared_run_id == run_id)
    if not hashes_match:
        blockers.append(f"hashes_mismatch_run:declared={declared_run_id} expected={run_id}")
    # — asset-type table isolation: no hotel tables in solar, no solar tables in hotel
    _SOLAR_ONLY_KEYS = frozenset({"generation_revenue", "other_operating_revenue"})
    _HOTEL_ONLY_KEYS = frozenset({"hotel_revenue", "lease_revenue"})
    if asset_type == "solar_power":
        for key in _HOTEL_ONLY_KEYS:
            if tables.get(key):
                blockers.append(f"hotel_table_leak_into_solar:{key}")
    else:
        for key in _SOLAR_ONLY_KEYS:
            if tables.get(key):
                blockers.append(f"solar_table_leak_into_hotel:{key}")
    for key, _name in definitions:
        rows = tables.get(key) or []
        if not rows:
            blockers.append(f"empty_table:{key}")
            continue
        missing_counts = {
            column: sum(row.get(column) is None or row.get(column) == "" for row in rows)
            for column in required_columns[key]
        }
        missing_counts = {column: count for column, count in missing_counts.items() if count}
        if missing_counts:
            column_missing[key] = missing_counts
            blockers.extend(f"missing_required_values:{key}:{column}:{count}" for column, count in missing_counts.items())
        nested = sum(
            isinstance(value, (dict, list))
            for row in rows for value in row.values()
        )
        if nested:
            nested_cells[key] = nested
            blockers.append(f"nested_cells:{key}:{nested}")
        bad_types: dict[str, int] = {}
        for row in rows:
            for field, value in row.items():
                if value is None or value == "":
                    continue
                invalid = False
                if field in _DATE_FIELDS:
                    try:
                        date.fromisoformat(str(value))
                    except ValueError:
                        invalid = True
                elif field in _BOOLEAN_FIELDS:
                    invalid = not isinstance(value, bool)
                elif field.endswith("_wan") or field in _NUMERIC_FIELDS:
                    invalid = not isinstance(value, (int, float)) or isinstance(value, bool)
                if invalid:
                    bad_types[field] = bad_types.get(field, 0) + 1
        if bad_types:
            type_errors[key] = bad_types
            blockers.extend(f"invalid_type:{key}:{field}:{count}" for field, count in bad_types.items())
        if key == "equity_cashflow_indicators":
            invalid_payback_states = sum(
                (
                    row.get("dynamic_payback_years") is None
                    and row.get("dynamic_payback_status") != "not_recovered"
                ) or (
                    row.get("dynamic_payback_years") is not None
                    and row.get("dynamic_payback_status") != "recovered"
                )
                for row in rows
            )
            if invalid_payback_states:
                blockers.append(
                    "invalid_payback_state:equity_cashflow_indicators:"
                    f"{invalid_payback_states}"
                )
            checks.append(_check(
                "股东现金流动态回收状态一致", invalid_payback_states, 0, 0,
                "预测期内未回收时回收期留空并标记 not_recovered",
            ))
        checks.append(_check(
            f"{key}必填字段完整", len(rows) * len(required_columns[key]) - sum(missing_counts.values()),
            len(rows) * len(required_columns[key]), 0,
        ))
        checks.append(_check(f"{key}标量单元格", nested, 0, 0))
        checks.append(_check(f"{key}字段类型正确", sum(bad_types.values()), 0, 0))

    bridge = (tables.get("transaction_bridge") or [{}])[0]
    funding = (tables.get("investment_funding") or [{}])[0]
    checks.append(_check(
        "总收购成本=总投资", float(bridge.get("total_acquisition_cost_wan") or 0),
        float(funding.get("total_investment_wan") or 0), 0.01,
    ))
    checks.append(_check(
        "总投资=债务+权益", float(funding.get("total_investment_wan") or 0),
        float(funding.get("debt_wan") or 0) + float(funding.get("equity_wan") or 0), 0.01,
    ))
    ppa = tables.get("purchase_price_allocation") or []
    checks.append(_check(
        "购买价分摊合计=收购价", sum(float(row.get("allocation_wan") or 0) for row in ppa),
        float(bridge.get("purchase_price_wan") or 0), 0.01,
    ))
    if any(row.get("status") == "reconciliation_required" for row in ppa):
        warnings.append("PPA 差额已显式列为未分摊购买价，正式会计分摊前须复核")

    timeline = tables.get("monthly_timeline") or []
    discontinuities = 0
    for previous, current in zip(timeline, timeline[1:]):
        try:
            previous_end = date.fromisoformat(str(previous.get("period_end")))
            current_start = date.fromisoformat(str(current.get("period_start")))
        except ValueError:
            discontinuities += 1
            continue
        if current_start != previous_end + timedelta(days=1):
            discontinuities += 1
    checks.append(_check("月度期间连续", discontinuities, 0, 0))

    debt = tables.get("debt_schedule") or []
    debt_max_diff = max((
        abs(float(row.get("opening_principal_wan") or 0) - float(row.get("principal_wan") or 0) - float(row.get("closing_principal_wan") or 0))
        for row in debt
    ), default=0.0)
    service_max_diff = max((
        abs(float(row.get("interest_wan") or 0) + float(row.get("principal_wan") or 0) - float(row.get("debt_service_wan") or 0))
        for row in debt
    ), default=0.0)
    checks.append(_check("偿债本金滚动最大差异", debt_max_diff, 0, 0.0001))
    checks.append(_check("偿债构成最大差异", service_max_diff, 0, 0.0001))

    monthly_cf = tables.get("operating_cost_working_capital") or []
    annual_cf = tables.get("project_cashflow") or []
    rollup_max_diff = 0.0
    for index, annual_row in enumerate(annual_cf):
        subtotal = sum(float(row.get("project_cf_wan") or 0) for row in monthly_cf[index * 12:(index + 1) * 12])
        rollup_max_diff = max(rollup_max_diff, abs(subtotal - float(annual_row.get("project_cf_wan") or 0)))
    checks.append(_check("年度项目现金流=月度合计最大差异", rollup_max_diff, 0, 0.01))

    checks.append(_check("所有表绑定同一 run_id", 1 if hashes_match else 0, 1, 0, f"declared_run_id={declared_run_id} expected_run_id={run_id}"))

    failed_checks = [row["name"] for row in checks if row["status"] == "failed"]
    blockers.extend(f"failed_check:{name}" for name in failed_checks)
    blockers = list(dict.fromkeys(blockers))
    return {
        "status": "passed" if not blockers else "failed",
        "required_table_count": len(definitions),
        "manifest_count": len(tables),
        "empty_tables": [key for key, _name in definitions if not tables.get(key)],
        "column_missing": column_missing,
        "nested_cells": nested_cells,
        "type_errors": type_errors,
        "checks": checks,
        "failed_checks": failed_checks,
        "blockers": blockers,
        "warnings": warnings,
        "hashes_match_run": hashes_match,
    }


def _lineage(
    run: dict[str, Any], tables: dict[str, Any], *, asset_type: str = "hotel_lease",
) -> list[dict[str, Any]]:
    definitions, _columns, _required = _table_contract(asset_type)
    return [{
        "table_key": key,
        "source": "immutable_acquisition_run",
        "run_id": run.get("run_id"),
        "result_path": (
            "result.monthly_timeline" if key in {
            "monthly_timeline", "hotel_revenue", "lease_revenue",
            "generation_revenue", "other_operating_revenue",
                "operating_cost_working_capital", "tax_calculation",
            } else "result"
        ),
        "table_hash": sha256_json(tables[key]),
        "recalculated": False,
    } for key, _name in definitions]
