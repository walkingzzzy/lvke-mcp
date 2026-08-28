"""package store、导出根与十三表列定义；含酒店/光伏两套必填列的原地扩展。"""

from __future__ import annotations

from pathlib import Path


from lvke_mcp.runtime.workspace import deliverable_dir
from lvke_mcp.runtime.storage import JSONArtifactStore, require_safe_id


PACKAGE_STORE = JSONArtifactStore(
    "asset-acquisition", "table_packages", "acquisition_tables_package", "table-packages"
)


def _export_root(workspace_id: str) -> Path:
    """收购表包 CSV/XLSX 落盘根，统一写到仓库 ``lvke产出/``。"""
    return deliverable_dir(
        require_safe_id(workspace_id, "workspace_id"),
        "asset-acquisition",
        "exports",
    )


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
    ("income_statement", "利润表"),
    ("balance_sheet", "资产负债表"),
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
    ("income_statement", "利润表"),
    ("balance_sheet", "资产负债表"),
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
        ("vat_wan", "增值税（万元）"), ("surtax_wan", "附加税（万元）"),
        ("loss_carryforward_wan", "亏损结转（万元）"),
    ),
    "income_statement": (
        ("year", "财务年度"),
        ("revenue_wan", "营业收入（万元）"),
        ("operating_cost_wan", "经营成本（万元）"),
        ("depreciation_wan", "折旧（万元）"),
        ("interest_wan", "利息（万元）"),
        ("income_tax_wan", "所得税（万元）"),
        ("net_profit_wan", "净利润（万元）"),
    ),
    "balance_sheet": (
        ("year", "财务年度"),
        ("cash_wan", "货币资金（万元）"),
        ("fixed_asset_net_wan", "固定资产净值（万元）"),
        ("total_assets_wan", "资产合计（万元）"),
        ("debt_wan", "有息负债（万元）"),
        ("equity_wan", "所有者权益（万元）"),
        ("total_liabilities_equity_wan", "负债和权益合计（万元）"),
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
