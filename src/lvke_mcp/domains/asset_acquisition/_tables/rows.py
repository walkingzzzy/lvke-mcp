"""行构造：表契约、标量拼接、PPA/折旧/权益行与情景行。"""

from __future__ import annotations

from typing import Any


from lvke_mcp.domains.asset_acquisition import backend as acquisition_service
from lvke_mcp.runtime.storage import sha256_json

from .columns import (
    REQUIRED_COLUMNS,
    SOLAR_REQUIRED_COLUMNS,
    SOLAR_TABLE_COLUMNS,
    SOLAR_TABLE_DEFINITIONS,
    TABLE_COLUMNS,
    TABLE_DEFINITIONS,
)


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _table_contract(asset_type: str) -> tuple[
    tuple[tuple[str, str], ...],
    dict[str, tuple[tuple[str, str], ...]],
    dict[str, tuple[str, ...]],
]:
    if asset_type == "solar_power":
        return SOLAR_TABLE_DEFINITIONS, SOLAR_TABLE_COLUMNS, SOLAR_REQUIRED_COLUMNS
    return TABLE_DEFINITIONS, TABLE_COLUMNS, REQUIRED_COLUMNS


def _join_scalar(value: Any) -> str:
    if not isinstance(value, list):
        return "" if value is None else str(value)
    labels: list[str] = []
    for item in value:
        if isinstance(item, dict):
            label = item.get("code") or item.get("field") or item.get("message")
            labels.append(str(label or ""))
        else:
            labels.append(str(item))
    return ";".join(label for label in labels if label)


def _driver_scalar(value: Any) -> Any:
    """Project a driver input to one deterministic display value."""
    if isinstance(value, dict):
        for field in ("monthly_values", "annual_values"):
            candidate = value.get(field)
            if isinstance(candidate, list):
                return candidate[0] if candidate else None
            if candidate is not None:
                return candidate
        return None
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _ppa_rows(transaction: dict[str, Any], purchase_price: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in _rows(transaction.get("asset_scope")):
        row = dict(source)
        allocation = row.get("allocation_wan")
        if allocation is None:
            allocation = row.get("value_wan", row.get("depreciable_basis_wan", 0))
        row.update({
            "allocation_wan": float(allocation or 0),
            "evidence_ids": _join_scalar(row.get("evidence_ids")),
            "conflicts": _join_scalar(row.get("conflicts")),
        })
        rows.append(row)
    allocated = sum(float(row.get("allocation_wan") or 0) for row in rows)
    difference = float(purchase_price or 0) - allocated
    if abs(difference) > 0.01:
        rows.append({
            "scope_id": "unallocated-purchase-price",
            "type": "unallocated",
            "included": True,
            "status": "reconciliation_required",
            "area_sqm": None,
            "accounting_treatment": "unallocated_purchase_price",
            "allocation_wan": difference,
            "depreciable_basis_wan": 0.0,
            "depreciation_years": None,
            "residual_rate": None,
            "evidence_ids": "",
            "conflicts": "ppa_unallocated",
            "resolution": "收购价与已分摊资产之间的差额；正式会计分摊前须复核商誉、土地或其他资产",
        })
    return rows


def _depreciation_rows(result: dict[str, Any], annual: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    schedule = dict(result.get("depreciation_schedule") or {})
    for asset in _rows(schedule.get("classes")):
        for index, amount in enumerate(asset.get("annual_depreciation_wan") or []):
            period = annual[index] if index < len(annual) else {}
            output.append({
                "scope_id": asset.get("scope_id"),
                "year_index": index + 1,
                "period_start": period.get("period_start"),
                "period_end": period.get("period_end"),
                "period_label": period.get("period_label"),
                "basis_wan": asset.get("basis_wan"),
                "depreciation_years": asset.get("depreciation_years"),
                "residual_rate": asset.get("residual_rate"),
                "annual_depreciation_wan": amount,
            })
    return output


def _equity_rows(result: dict[str, Any], annual: list[dict[str, Any]], start_date: str) -> list[dict[str, Any]]:
    project = list(result.get("project_cashflows_wan") or [])
    equity = list(result.get("equity_cashflows_wan") or [])
    indicators = dict(result.get("indicators") or {})
    dynamic_payback = indicators.get("dynamic_payback_years")
    dynamic_payback_status = "recovered" if dynamic_payback is not None else "not_recovered"
    output: list[dict[str, Any]] = []
    for index in range(max(len(project), len(equity))):
        period = annual[index - 1] if index > 0 and index - 1 < len(annual) else {}
        output.append({
            "cashflow_index": index,
            "period_start": start_date if index == 0 else period.get("period_start"),
            "period_end": start_date if index == 0 else period.get("period_end"),
            "period_label": "收购时点" if index == 0 else period.get("period_label"),
            "project_cashflow_wan": project[index] if index < len(project) else None,
            "equity_cashflow_wan": equity[index] if index < len(equity) else None,
            **indicators,
            "dynamic_payback_status": dynamic_payback_status,
        })
    return output


def _scenario_row(
    scenario_id: str, scenario_kind: str, result: dict[str, Any], *,
    changes: dict[str, Any] | None = None, result_hash: str = "",
) -> dict[str, Any]:
    changes = changes or {}
    indicators = dict(result.get("indicators") or {})
    dynamic_payback = indicators.get("dynamic_payback_years")
    occupancy = _driver_scalar(result.get("occupancy"))
    adr = _driver_scalar(result.get("adr"))
    return {
        "scenario_id": scenario_id,
        "scenario_kind": scenario_kind,
        "changed_fields": ";".join(sorted(changes)),
        "adr": _driver_scalar(changes.get("hotel_operation.adr", adr)),
        "occupancy": _driver_scalar(changes.get("hotel_operation.occupancy", occupancy)),
        "tariff_yuan_per_kwh": changes.get(
            "solar_operation.tariff_yuan_per_kwh", result.get("tariff_yuan_per_kwh")
        ),
        "annual_generation_mwh": changes.get(
            "solar_operation.annual_generation_mwh", result.get("annual_generation_mwh")
        ),
        "annual_opex_wan": changes.get(
            "solar_operation.annual_opex_wan", result.get("annual_opex_wan")
        ),
        "purchase_price_wan": result.get("purchase_price_wan"),
        "financing_ratio": result.get("financing_ratio"),
        "project_irr_pct": indicators.get("project_irr_pct"),
        "equity_irr_pct": indicators.get("equity_irr_pct"),
        "npv_wan": indicators.get("npv_wan"),
        "static_payback_years": indicators.get("static_payback_years"),
        "dynamic_payback_years": indicators.get("dynamic_payback_years"),
        "dynamic_payback_status": "recovered" if dynamic_payback is not None else "not_recovered",
        "minimum_dscr": indicators.get("minimum_dscr"),
        "minimum_monthly_dscr": indicators.get("minimum_monthly_dscr"),
        "minimum_icr": indicators.get("minimum_icr"),
        "target_irr": None,
        "maximum_acceptable_price_wan": None,
        "converged": None,
        "feasible": None,
        "bounded_by_upper": None,
        "result_hash": result_hash,
    }


def _scenario_rows(
    run: dict[str, Any],
    result: dict[str, Any],
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    run_id = str(run.get("run_id") or "")
    transaction = dict(spec.get("transaction") or {})
    solar = dict(spec.get("solar_operation") or {})
    monthly = _rows(result.get("monthly_timeline"))
    first_month = monthly[0] if monthly else {}
    base = _scenario_row(
        f"{run_id}:{result.get('scenario_id') or 'base'}", "base",
        {
            "purchase_price_wan": result.get("purchase_price_wan"),
            "financing_ratio": transaction.get("financing_ratio"),
            "adr": first_month.get("adr"),
            "occupancy": first_month.get(
                "occupancy", (spec.get("hotel_operation") or {}).get("occupancy")
            ),
            "tariff_yuan_per_kwh": solar.get("tariff_yuan_per_kwh"),
            "annual_generation_mwh": solar.get("annual_generation_mwh"),
            "annual_opex_wan": solar.get("annual_opex_wan"),
            "indicators": result.get("indicators") or {},
        },
        result_hash=sha256_json(result),
    )
    output = [base]
    for summary in acquisition_service.list_scenario_matrices(
        str(run.get("workspace_id") or ""),
        run_id,
    ):
        matrix_id = str(summary.get("matrix_id") or "")
        matrix = acquisition_service.get_scenario_matrix(
            str(run.get("workspace_id") or ""),
            run_id,
            matrix_id,
        )
        for source in _rows(matrix.get("rows")):
            output.append(_scenario_row(
                f"{matrix_id}:{source.get('scenario_id')}", "matrix",
                {
                    "purchase_price_wan": source.get("purchase_price_wan"),
                    "financing_ratio": source.get("financing_ratio"),
                    "occupancy": source.get("occupancy"),
                    "tariff_yuan_per_kwh": source.get("tariff_yuan_per_kwh"),
                    "annual_generation_mwh": source.get("annual_generation_mwh"),
                    "annual_opex_wan": source.get("annual_opex_wan"),
                    "indicators": source.get("indicators") or {},
                },
                changes=dict(source.get("changes") or {}),
                result_hash=str(source.get("result_hash") or ""),
            ))
    max_price = dict(run.get("max_acquisition_price_analysis") or {})
    solved = dict(max_price.get("result") or {})
    if solved:
        indicators = dict(solved.get("indicators_at_solution") or {})
        output.append({
            **_scenario_row(
                f"{run_id}:max-price", "max_price",
                {
                    "purchase_price_wan": solved.get("max_acquisition_price_wan"),
                    "financing_ratio": transaction.get("financing_ratio"),
                    "indicators": indicators,
                },
                result_hash=str(max_price.get("analysis_hash") or ""),
            ),
            "target_irr": solved.get("target_irr"),
            "maximum_acceptable_price_wan": solved.get("max_acquisition_price_wan"),
            "converged": solved.get("converged"),
            "feasible": solved.get("feasible"),
            "bounded_by_upper": solved.get("bounded_by_upper"),
        })
    return output
