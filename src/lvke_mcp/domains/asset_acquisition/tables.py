"""Immutable, auditable table packages for acquisition model runs."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from lvke_mcp.runtime.workspace import workspace_root
from lvke_mcp.domains.asset_acquisition import backend as acquisition_service
from lvke_mcp.domains.reports import artifacts as report_artifacts
from lvke_mcp.runtime.storage import (
    JSONArtifactStore,
    require_safe_id,
    sha256_json,
)

PACKAGE_STORE = JSONArtifactStore(
    "asset-acquisition", "table_packages", "acquisition_tables_package", "table-packages"
)


def _export_root(workspace_id: str) -> Path:
    base = (
        workspace_root(require_safe_id(workspace_id, "workspace_id"))
        / "mcp_objects"
        / "asset-acquisition"
    )
    return base

TABLE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("transaction_bridge", "收购范围与交易桥接"),
    ("investment_funding", "总投资与资金筹措"),
    ("purchase_price_allocation", "购买价分摊"),
    ("monthly_timeline", "月度交割、改造与开业时间轴"),
    ("hotel_revenue", "酒店经营收入"),
    ("lease_revenue", "配套租赁收入"),
    ("operating_cost_working_capital", "经营成本与营运资金"),
    ("depreciation_amortization", "折旧与土地使用权摊销"),
    ("debt_schedule", "偿债计划"),
    ("tax_calculation", "税费测算"),
    ("project_cashflow", "项目现金流"),
    ("equity_cashflow_indicators", "股东现金流与指标"),
    ("scenario_max_price", "情景敏感性与最高收购价"),
)

SOLAR_TABLE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("transaction_bridge", "收购范围与交易桥接"),
    ("investment_funding", "总投资与资金筹措"),
    ("purchase_price_allocation", "购买价分摊"),
    ("monthly_timeline", "光伏运营期间桥接"),
    ("generation_revenue", "发电量与售电收入"),
    ("other_operating_revenue", "其他运营收入"),
    ("operating_cost_working_capital", "经营成本与营运资金"),
    ("depreciation_amortization", "折旧与摊销"),
    ("debt_schedule", "偿债计划"),
    ("tax_calculation", "税费测算"),
    ("project_cashflow", "项目现金流"),
    ("equity_cashflow_indicators", "股东现金流与指标"),
    ("scenario_max_price", "情景敏感性与最高收购价"),
)

TABLE_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "transaction_bridge": (
        ("run_id", "运行ID"), ("acquisition_type", "收购类型"),
        ("purchase_price_wan", "收购价（万元）"), ("valuation_value_wan", "估值（万元）"),
        ("transaction_tax_wan", "交易税费（万元）"),
        ("total_acquisition_cost_wan", "总收购成本（万元）"),
        ("asset_scope_count", "资产范围数量"),
    ),
    "investment_funding": (
        ("total_investment_wan", "总投资（万元）"), ("financing_ratio", "融资比例"),
        ("debt_wan", "债务资金（万元）"), ("equity_wan", "权益资金（万元）"),
        ("funding_balance_check_wan", "资金平衡差异（万元）"),
    ),
    "purchase_price_allocation": (
        ("scope_id", "资产范围ID"), ("type", "资产类型"), ("included", "是否纳入"),
        ("status", "确认状态"), ("area_sqm", "面积（平方米）"),
        ("accounting_treatment", "会计处理"), ("allocation_wan", "分摊金额（万元）"),
        ("depreciable_basis_wan", "可折旧基础（万元）"),
        ("depreciation_years", "折旧年限（年）"), ("residual_rate", "残值率"),
        ("evidence_ids", "证据ID"), ("conflicts", "冲突说明"), ("resolution", "处理结论"),
    ),
    "monthly_timeline": (
        ("month", "月序号"), ("period_start", "期间开始日期"), ("period_end", "期间结束日期"),
        ("active_days", "有效天数"), ("hotel_days", "酒店运营天数"),
        ("operating_mode", "运营模式"),
    ),
    "hotel_revenue": (
        ("month", "月序号"), ("period_start", "期间开始日期"),
        ("hotel_revenue_wan", "酒店收入（万元）"), ("hotel_cost_wan", "酒店成本（万元）"),
    ),
    "lease_revenue": (
        ("month", "月序号"), ("period_start", "期间开始日期"),
        ("lease_revenue_wan", "租赁收入（万元）"),
        ("lease_adjustment_wan", "租赁调整（万元）"),
    ),
    "operating_cost_working_capital": (
        ("month", "月序号"), ("period_start", "期间开始日期"),
        ("operating_cost_wan", "经营成本（万元）"),
        ("maintenance_capex_wan", "维护性资本开支（万元）"),
        ("project_cf_wan", "项目现金流（万元）"),
    ),
    "depreciation_amortization": (
        ("scope_id", "资产范围ID"), ("year_index", "预测年度序号"),
        ("period_start", "期间开始日期"), ("period_end", "期间结束日期"),
        ("period_label", "财务期间"), ("basis_wan", "折旧基础（万元）"),
        ("depreciation_years", "折旧年限（年）"), ("residual_rate", "残值率"),
        ("annual_depreciation_wan", "年度折旧（万元）"),
    ),
    "debt_schedule": (
        ("month", "月序号"), ("period_start", "期间开始日期"),
        ("opening_principal_wan", "期初本金（万元）"), ("interest_wan", "利息（万元）"),
        ("principal_wan", "偿还本金（万元）"), ("debt_service_wan", "偿债额（万元）"),
        ("closing_principal_wan", "期末本金（万元）"),
    ),
    "tax_calculation": (
        ("month", "月序号"), ("period_start", "期间开始日期"),
        ("income_tax_wan", "所得税（万元）"), ("interest_wan", "利息（万元）"),
    ),
    "project_cashflow": (
        ("year", "财务年度"), ("year_index", "预测年度序号"),
        ("period_start", "期间开始日期"), ("period_end", "期间结束日期"),
        ("period_label", "财务期间"), ("period_basis", "期间口径"),
        ("revenue_wan", "收入（万元）"), ("operating_cost_wan", "经营成本（万元）"),
        ("income_tax_wan", "所得税（万元）"),
        ("maintenance_capex_wan", "维护性资本开支（万元）"),
        ("debt_service_wan", "偿债额（万元）"), ("project_cf_wan", "项目现金流（万元）"),
        ("equity_cf_wan", "股东现金流（万元）"),
    ),
    "equity_cashflow_indicators": (
        ("cashflow_index", "现金流序号"), ("period_start", "期间开始日期"),
        ("period_end", "期间结束日期"), ("period_label", "财务期间"),
        ("project_cashflow_wan", "项目现金流（万元）"),
        ("equity_cashflow_wan", "股东现金流（万元）"),
        ("project_irr_pct", "项目IRR（%）"), ("equity_irr_pct", "股东IRR（%）"),
        ("npv_wan", "净现值（万元）"), ("static_payback_years", "静态回收期（年）"),
        ("dynamic_payback_years", "动态回收期（年）"),
        ("dynamic_payback_status", "动态回收状态"),
        ("minimum_dscr", "最低年度DSCR（倍）"),
        ("minimum_monthly_dscr", "最低月度DSCR（倍）"), ("minimum_icr", "最低ICR（倍）"),
    ),
    "scenario_max_price": (
        ("scenario_id", "全局情景ID"), ("scenario_kind", "情景类型"),
        ("changed_fields", "变更字段"), ("adr", "ADR（元）"),
        ("occupancy", "入住率"), ("purchase_price_wan", "收购价（万元）"),
        ("financing_ratio", "融资比例"), ("project_irr_pct", "项目IRR（%）"),
        ("equity_irr_pct", "股东IRR（%）"), ("npv_wan", "净现值（万元）"),
        ("static_payback_years", "静态回收期（年）"),
        ("dynamic_payback_years", "动态回收期（年）"),
        ("dynamic_payback_status", "动态回收状态"),
        ("minimum_dscr", "最低年度DSCR（倍）"),
        ("minimum_monthly_dscr", "最低月度DSCR（倍）"), ("minimum_icr", "最低ICR（倍）"),
        ("target_irr", "目标IRR"), ("maximum_acceptable_price_wan", "最高收购价（万元）"),
        ("converged", "是否收敛"), ("feasible", "是否可行"),
        ("bounded_by_upper", "是否受上界约束"), ("result_hash", "结果哈希"),
    ),
}

SOLAR_TABLE_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    **{key: value for key, value in TABLE_COLUMNS.items() if key not in {
        "monthly_timeline", "hotel_revenue", "lease_revenue", "scenario_max_price",
    }},
    "monthly_timeline": (
        ("month", "月序号"), ("period_start", "期间开始日期"),
        ("period_end", "期间结束日期"), ("active_days", "有效天数"),
        ("asset_type", "资产类型"),
    ),
    "generation_revenue": (
        ("month", "月序号"), ("period_start", "期间开始日期"),
        ("gross_generation_mwh", "理论发电量（MWh）"),
        ("sold_generation_mwh", "上网电量（MWh）"),
        ("tariff_yuan_per_kwh", "上网电价（元/kWh）"),
        ("operating_revenue_wan", "售电收入（万元）"),
    ),
    "other_operating_revenue": (
        ("month", "月序号"), ("period_start", "期间开始日期"),
        ("other_revenue_wan", "其他运营收入（万元）"),
    ),
    "scenario_max_price": (
        ("scenario_id", "全局情景ID"), ("scenario_kind", "情景类型"),
        ("changed_fields", "变更字段"), ("tariff_yuan_per_kwh", "上网电价（元/kWh）"),
        ("annual_generation_mwh", "年发电量（MWh）"),
        ("annual_opex_wan", "年运维费（万元）"),
        ("purchase_price_wan", "收购价（万元）"),
        ("financing_ratio", "融资比例"), ("project_irr_pct", "项目IRR（%）"),
        ("equity_irr_pct", "股东IRR（%）"), ("npv_wan", "净现值（万元）"),
        ("static_payback_years", "静态回收期（年）"),
        ("dynamic_payback_years", "动态回收期（年）"),
        ("dynamic_payback_status", "动态回收状态"),
        ("minimum_dscr", "最低年度DSCR（倍）"),
        ("minimum_monthly_dscr", "最低月度DSCR（倍）"), ("minimum_icr", "最低ICR（倍）"),
        ("target_irr", "目标IRR"), ("maximum_acceptable_price_wan", "最高收购价（万元）"),
        ("converged", "是否收敛"), ("feasible", "是否可行"),
        ("bounded_by_upper", "是否受上界约束"), ("result_hash", "结果哈希"),
    ),
}

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    key: tuple(field for field, _label in columns)
    for key, columns in TABLE_COLUMNS.items()
}
# Evidence/audit annotations may legitimately be blank; the financial values may not.
REQUIRED_COLUMNS["purchase_price_allocation"] = (
    "scope_id", "type", "included", "status", "accounting_treatment", "allocation_wan",
)
REQUIRED_COLUMNS["equity_cashflow_indicators"] = tuple(
    field for field, _label in TABLE_COLUMNS["equity_cashflow_indicators"]
    if field != "dynamic_payback_years"
)
REQUIRED_COLUMNS["scenario_max_price"] = (
    "scenario_id", "scenario_kind", "purchase_price_wan", "project_irr_pct",
    "equity_irr_pct", "npv_wan",
)
SOLAR_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    key: tuple(field for field, _label in columns)
    for key, columns in SOLAR_TABLE_COLUMNS.items()
}
SOLAR_REQUIRED_COLUMNS["purchase_price_allocation"] = REQUIRED_COLUMNS["purchase_price_allocation"]
SOLAR_REQUIRED_COLUMNS["equity_cashflow_indicators"] = REQUIRED_COLUMNS["equity_cashflow_indicators"]
SOLAR_REQUIRED_COLUMNS["scenario_max_price"] = REQUIRED_COLUMNS["scenario_max_price"]
_DATE_FIELDS = {"period_start", "period_end"}
_NUMERIC_FIELDS = {
    "month", "year", "year_index", "cashflow_index", "active_days", "hotel_days", "area_sqm",
    "financing_ratio", "residual_rate", "depreciation_years", "project_irr_pct", "equity_irr_pct",
    "static_payback_years", "dynamic_payback_years", "minimum_dscr", "minimum_monthly_dscr",
    "minimum_icr", "target_irr", "adr", "occupancy", "gross_generation_mwh",
    "sold_generation_mwh", "tariff_yuan_per_kwh", "annual_generation_mwh", "annual_opex_wan",
}
_BOOLEAN_FIELDS = {"included", "converged", "feasible", "bounded_by_upper"}


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
    occupancy = result.get("occupancy")
    if isinstance(occupancy, list):
        occupancy = occupancy[0] if occupancy else None
    return {
        "scenario_id": scenario_id,
        "scenario_kind": scenario_kind,
        "changed_fields": ";".join(sorted(changes)),
        "adr": changes.get("hotel_operation.adr"),
        "occupancy": changes.get("hotel_operation.occupancy", occupancy),
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
    base = _scenario_row(
        f"{run_id}:{result.get('scenario_id') or 'base'}", "base",
        {
            "purchase_price_wan": result.get("purchase_price_wan"),
            "financing_ratio": transaction.get("financing_ratio"),
            "occupancy": (spec.get("hotel_operation") or {}).get("occupancy"),
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


def _build_tables(
    run: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    result = dict(run.get("result") or {})
    transaction = dict(spec.get("transaction") or {})
    monthly = _rows(result.get("monthly_timeline"))
    annual = _rows(result.get("annual_summary"))
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
            "income_tax_wan": row.get("income_tax_wan", row.get("tax_wan")),
            "interest_wan": row.get("interest_wan", (debt_by_month.get(row.get("month")) or {}).get("interest_wan")),
        } for row in monthly],
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
    tables: dict[str, list[dict[str, Any]]], *, asset_type: str = "hotel_lease",
) -> dict[str, Any]:
    definitions, _columns, required_columns = _table_contract(asset_type)
    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    column_missing: dict[str, dict[str, int]] = {}
    nested_cells: dict[str, int] = {}
    type_errors: dict[str, dict[str, int]] = {}
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
        "hashes_match_run": True,
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


def render(
    workspace_id: str,
    run_id: str,
) -> dict[str, Any]:
    run = acquisition_service.get_run(
        workspace_id,
        run_id,
    )
    if not run:
        return _blocked("RUN_NOT_FOUND", "资产收购 run 不存在")
    if run.get("status") != "succeeded" or not run.get("available"):
        return _blocked("RUN_NOT_READY", "资产收购 run 尚未固化成功")
    if run.get("model_version") not in {"acquisition_model.v3", "acquisition_model.solar.v1"}:
        return _blocked("ACQUISITION_MODEL_UNSUPPORTED", "收购十三表不支持该模型版本")
    spec_row = acquisition_service.get_spec(
        workspace_id,
        str(run.get("spec_id") or ""),
    )
    spec = spec_row.get("spec") if isinstance(spec_row, dict) else None
    if not isinstance(spec, dict) or spec_row.get("spec_hash") != run.get("spec_hash"):
        return _blocked("RUN_SPEC_MISMATCH", "run 与不可变 Spec 快照不一致")
    asset_type = str(spec.get("asset_type") or "hotel_lease")
    definitions, columns, _required = _table_contract(asset_type)
    tables = _build_tables(run, spec)
    integrity = _integrity(tables, asset_type=asset_type)
    manifest = [{
        "index": index, "key": key, "name": name, "row_count": len(tables[key]),
        "column_count": len(columns[key]), "table_hash": sha256_json(tables[key]),
        "missing_required": integrity["column_missing"].get(key, {}),
        "nested_cell_count": integrity["nested_cells"].get(key, 0),
    } for index, (key, name) in enumerate(definitions, 1)]
    basis = {
        "run_id": run_id, "spec_id": run.get("spec_id"), "spec_hash": run.get("spec_hash"),
        "input_hash": run.get("input_hash"), "model_version": run.get("model_version"),
        "asset_type": asset_type,
        "evidence_binding_hash": run.get("evidence_binding_hash"),
    }
    payload = {
        **basis,
        "package_schema": "acquisition_tables_package.v2",
        "table_manifest": manifest,
        "formula_lineage": _lineage(run, tables, asset_type=asset_type),
        "tables": tables,
        "integrity": integrity,
        "evidence_policy": str(run.get("evidence_policy") or "formal_evidence"),
        "project_fact_certified": bool(run.get("project_fact_certified", False)),
        "reconstruction_records": list(run.get("reconstruction_records") or []),
        "reconstructed_source_ids": list(run.get("reconstructed_source_ids") or []),
        "unresolved_inputs": list(run.get("unresolved_inputs") or []),
        "release_limitations": list(run.get("release_limitations") or []),
    }
    record = PACKAGE_STORE.put(
        workspace_id, payload,
        producer="lvke-asset-acquisition.acquisition_render_tables",
        status="ok" if integrity["status"] == "passed" else "partial",
        source_ids=[run_id, str(run.get("spec_id") or "")],
        basis=basis, schema_version="acquisition_tables_package.v2",
    )
    if integrity["status"] == "passed":
        _bind_package(
            workspace_id,
            run,
            record,
        )
    return _result(record)


def _bind_package(
    workspace_id: str,
    run: dict[str, Any],
    record: dict[str, Any],
) -> None:
    current = report_artifacts.load(
        workspace_id,
        "finance_binding",
        {},
    ) or {}
    fin = {key: value for key, value in current.items() if key not in {"workspace_id", "finance_run_id", "section", "bound_at"}}
    fin.update({
        "binding_kind": "asset_acquisition",
        "acquisition_tables_package_id": record["object_id"],
        "acquisition_tables_basis_hash": record["basis_hash"],
    })
    report_artifacts.bind_finance_run(
        workspace_id,
        str(run.get("run_id") or ""),
        section="asset_acquisition_tables",
        fin=fin,
    )


def _package(
    workspace_id: str,
    package_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    record = PACKAGE_STORE.get(workspace_id, package_id)
    return record, dict((record or {}).get("payload") or {})


def _ensure_exportable(payload: dict[str, Any]) -> dict[str, Any] | None:
    if (payload.get("integrity") or {}).get("status") != "passed":
        return _blocked("TABLE_PACKAGE_INCOMPLETE", "收购十三表列级完整性或勾稽校验未通过")
    return None


def _export_cell(field: str, value: Any) -> Any:
    if field == "dynamic_payback_status":
        return {"recovered": "已回收", "not_recovered": "未回收"}.get(value, value)
    return "" if value is None else value


def export_csv(
    workspace_id: str,
    package_id: str,
) -> dict[str, Any]:
    record, payload = _package(
        workspace_id,
        package_id,
    )
    if record is None:
        return _blocked("TABLE_PACKAGE_NOT_FOUND", "未找到收购十三表 package")
    blocked = _ensure_exportable(payload)
    if blocked is not None:
        return blocked
    definitions, columns_by_key, _required = _table_contract(
        str(payload.get("asset_type") or "hotel_lease")
    )
    directory = (
        _export_root(workspace_id)
        / "csv"
        / require_safe_id(package_id, "package_id")
    )
    directory.mkdir(parents=True, exist_ok=True)
    uris: list[str] = []
    hashes: dict[str, str] = {}
    for key, _name in definitions:
        target = directory / f"{key}.csv"
        columns = columns_by_key[key]
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow([label for _field, label in columns])
            for row in _rows((payload.get("tables") or {}).get(key)):
                writer.writerow([_export_cell(field, row.get(field)) for field, _label in columns])
        hashes[key] = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        uris.append(
            PACKAGE_STORE.uri(workspace_id, package_id)
            + f"/csv/{key}"
        )
    result = _result(record)
    result.update({"csv_resource_uris": uris, "csv_hashes": hashes, "resource_uris": [*result["resource_uris"], *uris]})
    return result


def export_xlsx(
    workspace_id: str,
    package_id: str,
) -> dict[str, Any]:
    record, payload = _package(
        workspace_id,
        package_id,
    )
    if record is None:
        return _blocked("TABLE_PACKAGE_NOT_FOUND", "未找到收购十三表 package")
    blocked = _ensure_exportable(payload)
    if blocked is not None:
        return blocked
    definitions, columns_by_key, _required = _table_contract(
        str(payload.get("asset_type") or "hotel_lease")
    )
    directory = _export_root(workspace_id) / "xlsx"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{require_safe_id(package_id, 'package_id')}.xlsx"
    workbook = Workbook()
    workbook.remove(workbook.active)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    thin_gray = Side(style="thin", color="D9E2F3")

    for index, (key, name) in enumerate(definitions, 1):
        sheet = workbook.create_sheet(title=f"{index:02d}-{name}"[:31])
        columns = columns_by_key[key]
        sheet.append([label for _field, label in columns])
        for row in _rows((payload.get("tables") or {}).get(key)):
            sheet.append([_export_cell(field, row.get(field)) for field, _label in columns])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        for column_index, (field, _label) in enumerate(columns, 1):
            if field.endswith("_wan"):
                for cell in sheet.iter_cols(min_col=column_index, max_col=column_index, min_row=2):
                    for item in cell:
                        item.number_format = '#,##0.00;[Red](#,##0.00);-'
            elif field in {"financing_ratio", "residual_rate", "occupancy", "target_irr"}:
                for cell in sheet.iter_cols(min_col=column_index, max_col=column_index, min_row=2):
                    for item in cell:
                        item.number_format = '0.0%'
        for column_cells in sheet.columns:
            maximum = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(maximum + 2, 10), 32)

    for sheet in workbook.worksheets:
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(bottom=thin_gray)
        sheet.row_dimensions[1].height = 24
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="center")
        for column_cells in sheet.columns:
            maximum = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(maximum + 2, 10), 36)
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.save(target)
    digest = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    uri = PACKAGE_STORE.uri(workspace_id, package_id) + "/xlsx"
    result = _result(record)
    result.update({"xlsx_resource_uri": uri, "xlsx_hash": digest, "resource_uris": [*result["resource_uris"], uri]})
    return result


def get_package(
    workspace_id: str,
    package_id: str,
) -> dict[str, Any]:
    record = PACKAGE_STORE.get(workspace_id, package_id)
    return _blocked("TABLE_PACKAGE_NOT_FOUND", "未找到收购十三表 package") if record is None else _result(record)


def get_package_record(
    workspace_id: str,
    package_id: str,
) -> dict[str, Any] | None:
    """Return the immutable record for a table package."""

    return PACKAGE_STORE.get(workspace_id, package_id)


def resolve_resource(
    uri: str,
) -> tuple[str | bytes, str] | None:
    record = PACKAGE_STORE.resolve_uri(uri)
    if record is not None:
        return json.dumps(record, ensure_ascii=False, indent=2), "application/json"
    prefix = "lvke://asset-acquisition/workspaces/"
    if not uri.startswith(prefix):
        return None
    parts = uri[len(prefix):].split("/")
    try:
        workspace_id = require_safe_id(parts[0], "workspace_id")
        if len(parts) == 4 and parts[1] == "table-packages" and parts[3] == "xlsx":
            package_id = require_safe_id(parts[2], "package_id")
            target = _export_root(workspace_id) / "xlsx" / f"{package_id}.xlsx"
            return (target.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") if target.is_file() else None
        if len(parts) == 5 and parts[1] == "table-packages" and parts[3] == "csv":
            package_id = require_safe_id(parts[2], "package_id")
            key = require_safe_id(parts[4], "table_key")
            package = PACKAGE_STORE.get(workspace_id, package_id)
            payload = dict((package or {}).get("payload") or {})
            definitions, _columns, _required = _table_contract(
                str(payload.get("asset_type") or "hotel_lease")
            )
            if key not in dict(definitions):
                return None
            target = _export_root(workspace_id) / "csv" / package_id / f"{key}.csv"
            return (target.read_bytes(), "text/csv; charset=utf-8") if target.is_file() else None
    except (ValueError, IndexError):
        return None
    return None


def _result(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") or {}
    integrity = payload.get("integrity") or {}
    blockers = list(integrity.get("blockers") or [])
    warnings = list(integrity.get("warnings") or [])
    return {
        "success": True,
        "status": "ok" if integrity.get("status") == "passed" else "partial",
        "object_id": record["object_id"],
        "acquisition_tables_package_id": record["object_id"],
        "run_id": payload.get("run_id"),
        "spec_hash": payload.get("spec_hash"),
        "input_hash": payload.get("input_hash"),
        "model_version": payload.get("model_version"),
        "evidence_binding_hash": payload.get("evidence_binding_hash"),
        "table_manifest": payload.get("table_manifest") or [],
        "formula_lineage": payload.get("formula_lineage") or [],
        "integrity": integrity,
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": blockers,
        "next_actions": [] if blockers else ["使用 package_id 导出 CSV/XLSX 或绑定资产收购报告"],
    }


def _failure(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "status": "failed", "code": code, "message": message, "resource_uris": [], "warnings": [], "blockers": [code], "next_actions": []}


def _blocked(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False, "transport_success": True,
        "business_success": False, "completed": False, "outcome": "blocked",
        "status": "blocked", "code": code, "message": message,
        "resource_uris": [], "warnings": [], "blockers": [code], "next_actions": [],
    }
