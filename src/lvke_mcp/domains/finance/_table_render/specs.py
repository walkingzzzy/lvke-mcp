"""单元格格式化原语、十三表静态规格与交付顺序。"""

from __future__ import annotations

from typing import Any

from lvke_mcp.domains.finance._run_service.base import (
    DELIVERY_TABLE_KEYS,
    DELIVERY_TABLE_META,
)


def _fmt(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        if abs(v - round(v)) < 1e-9:
            return f"{int(round(v)):,}"
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _fmt_rate_pct(v: Any) -> str:
    """年利率：小数 0.045 →「4.50%」；已是百分数(>1)则原样加 %。"""
    if v is None or v == "":
        return ""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    # 引擎真源为小数利率；>1 视为已是百分数（兼容误写）
    pct = x * 100.0 if abs(x) <= 1.0 else x
    if abs(pct - round(pct)) < 1e-9:
        return f"{int(round(pct))}%"
    return f"{pct:.2f}%"


def _fmt_cell(col_key: str, v: Any) -> str:
    """按列键格式化；rate 类列输出 4.50% 而非 0.04。"""
    if col_key in ("rate", "rate_pct", "loan_rate") or col_key.endswith("_rate"):
        # salvage_rate 等也可能是小数；统一百分展示
        if col_key in ("rate", "rate_pct", "loan_rate", "salvage_rate"):
            return _fmt_rate_pct(v)
    return _fmt(v)


# 每张交付表：从 result 取数据的投影定义
# columns: (row_key, label) — row_key 对应 dict 字段
_TABLE_SPECS: dict[str, dict[str, Any]] = {
    "investment": {
        "delivery_no": "附表1",
        "title": "固定资产投资估算表",
        "builder": "investment",
    },
    "interest-during-construction": {
        "delivery_no": "附表2",
        "title": "建设期贷款利息表",
        "annual_key": "interest_during_construction",
        "columns": [
            ("period", "期间"),
            ("begin_balance", "期初借款余额"),
            ("draw", "当期提款"),
            ("rate", "年利率(%)"),
            ("interest", "当期利息"),
            ("end_balance", "期末借款余额"),
        ],
        "fallback_columns": [("period", "期间"), ("interest", "当期建设期利息")],
    },
    "working-capital": {
        "delivery_no": "附表3",
        "title": "流动资金估算表",
        "builder": "working_capital",
    },
    "funding": {
        "delivery_no": "附表4",
        "title": "投资使用计划与资金筹措表",
        "builder": "funding",
    },
    "income-statement": {
        "delivery_no": "附表5",
        "title": "营业收入、税金及附加和增值税估算表",
        "annual_key": "income_statement",
        "columns": [
            ("year", "运营年"),
            ("revenue", "营业收入"),
            ("operating_cost", "经营成本"),
            ("depreciation", "折旧"),
            ("tax_surtax", "销售税金及附加"),
            ("vat_output", "销项税额"),
            ("vat_input", "进项税额"),
            ("vat_payable", "应纳增值税"),
            ("income_tax", "调整所得税(融资前)"),
            ("net_profit", "融资前净利"),
        ],
        "footer": (
            "> 所得税/净利为**融资前**口径；融资后见附表7。"
            "税金及附加与附表9 同源（默认应纳增值税×附加率）。"
        ),
    },
    "total-cost": {
        "delivery_no": "附表6",
        "title": "总成本费用估算表",
        "annual_key": "total_cost",
        "columns": [
            ("year", "运营年"),
            ("operating_cost", "经营成本"),
            ("depreciation", "折旧费"),
            ("amortization", "摊销费"),
            ("interest", "利息支出"),
            ("total_cost", "总成本费用"),
        ],
        "row_map": {"total_cost": ("total_cost", "total")},
    },
    "wage": {
        "delivery_no": "附表6-1",
        "title": "工资及附加估算表",
        "annual_key": "wage",
        "columns": [
            ("year", "运营年"),
            ("wage", "工资"),
            ("welfare", "职工福利及附加"),
            ("total", "工资及附加合计"),
        ],
        "footer": (
            "> 结构展示：若成本项键名已含「福利/附加」，本表为内拆、合计不大于该项；"
            "现金经营成本以 cost_items 汇总为准，不重复加计福利。"
        ),
    },
    "depreciation": {
        "delivery_no": "附表6-2",
        "title": "固定资产折旧费估算表",
        "annual_key": "depreciation_table",
        "columns": [
            ("year", "运营年"),
            ("original_value", "固定资产原值"),
            ("salvage_rate", "残值率"),
            ("dep_years", "折旧年限"),
            ("depreciation", "当期折旧费"),
            ("cumulative_depreciation", "累计折旧"),
            ("net_value", "净值"),
        ],
    },
    "amortization": {
        "delivery_no": "附表6-3",
        "title": "无形资产及其他资产摊销估算表",
        "annual_key": "amortization_table",
        "columns": [
            ("year", "运营年"),
            ("base", "摊销基数"),
            ("amort_years", "摊销年限"),
            ("amortization", "当期摊销费"),
        ],
    },
    "profit-distribution": {
        "delivery_no": "附表7",
        "title": "利润与利润分配表",
        "annual_key": "profit_distribution",
        "columns": [
            ("year", "运营年"),
            ("revenue", "营业收入"),
            ("total_cost", "总成本费用"),
            ("tax_surtax", "税金及附加"),
            ("total_profit", "利润总额"),
            ("ebit", "息税前利润(EBIT)"),
            ("ebitda", "息税折旧摊销前利润(EBITDA)"),
            ("loss_offset", "弥补以前年度亏损"),
            ("taxable_income", "应纳税所得额"),
            ("income_tax", "所得税"),
            ("net_profit", "净利润"),
            ("begin_undistributed", "期初未分配利润"),
            ("available_distribution", "可供分配的利润"),
            ("surplus_reserve", "提取法定盈余公积金"),
            ("distributable", "可供投资者分配的利润"),
            ("arbitrary_reserve", "提取任意盈余公积金"),
            ("investor_distribution", "投资各方利润分配"),
            ("undistributed", "未分配利润"),
        ],
        "footer": (
            "> 融资后会计口径（总成本含运营期利息）。"
            "分配行树按可研简化：法定盈余公积=净利润×10%；未单独建模任意公积/投资方分配时列 0。"
        ),
    },
    "debt-service": {
        "delivery_no": "附表8",
        "title": "还款付息测算表",
        "annual_key": "debt_service",
        "columns": [
            ("year", "运营年"),
            ("begin", "期初借款余额"),
            ("draw", "本年借款"),
            ("rate", "年利率(%)"),
            ("principal", "当期还本"),
            ("interest", "当期付息"),
            ("debt_service", "当期还本付息"),
            ("end", "期末借款余额"),
            ("repay_source_profit", "偿债资金来源-利润"),
            ("repay_source_dep", "偿债资金来源-折旧费"),
            ("repay_source_amort", "偿债资金来源-摊销费"),
            ("dscr", "偿债备付率(DSCR)"),
            ("icr", "利息备付率(ICR)"),
        ],
    },
    "cashflow": {
        "delivery_no": "附表9",
        "title": "项目投资现金流量表",
        "annual_key": "project_cashflow",
        "columns": [
            ("year", "计算期(年)"),
            ("phase", "阶段"),
            ("revenue", "营业收入"),
            ("op_cash_cost", "经营成本"),
            ("tax_surtax", "税金及附加"),
            ("income_tax", "调整所得税"),
            ("construction", "建设投资(含建设期利息)"),
            ("wc_change", "流动资金增加"),
            ("recover", "回收(流资+余值)"),
            ("net_cashflow", "净现金流"),
            ("cumulative", "累计净现金流"),
        ],
        "footer": (
            "> 融资前项目投资现金流；调整所得税不含利息税盾。"
            "税金及附加与附表5 同源。"
        ),
    },
    "capital-cashflow": {
        "delivery_no": "附表10",
        "title": "项目资本金流量表",
        "annual_key": "capital_cashflow",
        "columns": [
            ("year", "计算期(年)"),
            ("phase", "阶段"),
            ("capital_invest", "资本金投入"),
            ("revenue", "营业收入"),
            ("recover_fixed", "回收固定资产余值"),
            ("recover_wc", "回收流动资金"),
            ("op_cash_cost", "经营成本"),
            ("tax_surtax", "税金及附加"),
            ("income_tax", "所得税"),
            ("cash_inflow", "现金流入"),
            ("cash_outflow", "现金流出"),
            ("op_inflow", "经营现金流入"),
            ("principal", "还本"),
            ("interest", "付息"),
            ("net_cashflow", "资本金净现金流"),
        ],
    },
    # 附表11。列键取自**运行时唯一的生产者** annual._build_financial_plan
    # （period/cumulative/gap…）。曾另有同语义第二份实现
    # statements.financial_plan_rows（year/cash_end/cum_surplus/funding_gap/
    # min_cash），照它写会让 5 列恒为 None（已实测踩过），且它还让 checks.py
    # 的缺口检查恒 ok=true —— 该死代码已删除，勿再按那套键名写。
    # 标签需同时满足冻结契约的 reference_columns 词元与 required_row_groups，
    # 改标签前先看 docs/reference_table_schema.json 的 financial-plan 条目。
    # gap 是布尔，按原值输出、不折算数字，免得 0/1 被下游当金额读。
    "financial-plan": {
        "delivery_no": "附表11",
        "title": "财务计划现金流量表",
        "annual_key": "financial_plan",
        "columns": [
            ("period", "计算期(年)"),
            ("phase", "阶段"),
            ("operating_net", "经营活动净现金流"),
            ("invest_out", "投资活动净流出"),
            ("finance_in", "融资活动净流入"),
            ("loan_draw", "其中：贷款提款"),
            ("capital_own", "其中：资本金投入"),
            ("gov_subsidy", "其中：政府补助"),
            ("debt_service", "还本付息"),
            ("net_cashflow", "净现金流量"),
            ("cumulative", "累计盈余资金"),
            ("gap", "是否存在资金缺口"),
        ],
    },
}


# 表号、标题和交付顺序只能由 run_service.base 的正式 manifest 决定。
for _key, _delivery_no, _title in DELIVERY_TABLE_META:
    _TABLE_SPECS[_key]["delivery_no"] = _delivery_no
    _TABLE_SPECS[_key]["title"] = _title

DELIVERY_ORDER = list(DELIVERY_TABLE_KEYS)
