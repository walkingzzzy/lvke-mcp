"""从 catalog 列定义 + annual/investment 结构化数据投影 13 表。

开发期真源：structured rows（非手写 MD）。
MD 仅作为 structured → 管道表 的适配器输出。
"""

from __future__ import annotations

from typing import Any, Optional

from lvke_mcp.domains.finance.reference_schema import (
    assess_missing_fields_extended,
    assess_fact_source_coverage,
    assess_structure_coverage,
    merge_missing,
    schema_path,
    validate_reference_sources,
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
}

DELIVERY_ORDER = [
    "investment",
    "interest-during-construction",
    "working-capital",
    "funding",
    "income-statement",
    "total-cost",
    "wage",
    "depreciation",
    "amortization",
    "profit-distribution",
    "debt-service",
    "cashflow",
    "capital-cashflow",
]


def _get_field(row: dict[str, Any], key: str, row_map: Optional[dict] = None) -> Any:
    if row_map and key in row_map:
        for alt in row_map[key]:
            if alt in row and row[alt] is not None:
                return row[alt]
    # 常见别名
    aliases = {
        "begin": ("begin", "begin_balance"),
        "end": ("end", "end_balance"),
        "principal": ("principal", "repay_principal"),
        "interest": ("interest", "pay_interest"),
        "period": ("period", "year"),
        "year": ("year", "period"),
        "total_cost": ("total_cost", "total"),
        "rate": ("rate", "rate_pct"),
        "begin_balance": ("begin_balance", "begin"),
        "end_balance": ("end_balance", "end"),
        "draw": ("draw",),
    }
    if key in aliases:
        for alt in aliases[key]:
            if alt in row and row[alt] is not None:
                return row[alt]
    return row.get(key)


def _confirmed_fact_domains(fin: dict[str, Any]) -> dict[str, Any]:
    """Return fact domains only after explicit pack confirmation."""
    for source in (
        fin.get("input_revision"), fin.get("finance_inputs"), fin.get("raw"), fin,
    ):
        if not isinstance(source, dict):
            continue
        pack = source.get("finance_fact_pack") or source.get("fact_pack")
        if not isinstance(pack, dict):
            continue
        if pack.get("version") != "finance_fact_pack.v1":
            continue
        if str(pack.get("confirmation_status") or "").lower() != "confirmed":
            continue
        domains = pack.get("domains")
        if isinstance(domains, dict):
            return domains
    return {}


def _effective_input_revision(fin: dict[str, Any]) -> dict[str, Any]:
    """Return the single authoritative input source for all renderers.

    Presence of ``input_revision`` wins even when it is an empty mapping; the
    legacy ``finance_inputs`` snapshot is only a fallback for pre-revision runs.
    """
    value = fin.get("input_revision")
    if isinstance(value, dict) and "input_revision" in fin:
        return value
    legacy = fin.get("finance_inputs")
    return legacy if isinstance(legacy, dict) else {}


def _approved_direct_rows(fin: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Read a direct fact list only when that list has its own approval marker."""
    fin_in = _effective_input_revision(fin)
    if not isinstance(fin_in, dict):
        return []
    status = str(
        fin_in.get(f"{key}_confirmation_status")
        or fin_in.get(f"{key}_review_status")
        or ""
    ).lower()
    if status not in {"confirmed", "reviewed", "verified", "approved"}:
        return []
    rows = fin_in.get(key)
    return [dict(row) for row in rows or [] if isinstance(row, dict)] if isinstance(rows, list) else []


def _repay_source_facts(fin: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    domains = _confirmed_fact_domains(fin)
    debt_schedule = domains.get("debt_schedule") if isinstance(domains, dict) else None
    if isinstance(debt_schedule, dict):
        rows = debt_schedule.get("debt_repay_sources") or debt_schedule.get("repay_sources")
        if isinstance(rows, list) and rows:
            return [dict(row) for row in rows if isinstance(row, dict)], "confirmed_fact_pack.debt_schedule"
    direct = _approved_direct_rows(fin, "debt_repay_sources")
    if direct:
        return direct, "approved_input.debt_repay_sources"
    return [], ""


def _source_kind(name: Any) -> str:
    text = str(name or "").strip().lower()
    if any(token in text for token in ("利润", "profit", "distributable")):
        return "profit"
    if any(token in text for token in ("折旧", "depreciation", "dep")):
        return "depreciation"
    if any(token in text for token in ("摊销", "amortization", "amort")):
        return "amortization"
    return ""


def _source_value(
    facts: list[dict[str, Any]],
    *,
    kind: str,
    year_index: int,
    base: float,
) -> Optional[float]:
    values: list[float] = []
    for fact in facts:
        if _source_kind(fact.get("name") or fact.get("source") or fact.get("category")) != kind:
            continue
        fact_year = fact.get("year")
        if fact_year not in (None, ""):
            try:
                if int(fact_year) != year_index + 1:
                    continue
            except (TypeError, ValueError):
                continue
        schedule = fact.get("annual_schedule_wan") or fact.get("schedule_wan")
        if isinstance(schedule, list) and schedule:
            if year_index < 0 or year_index >= len(schedule):
                values.append(0.0)
                continue
            try:
                values.append(float(schedule[year_index] or 0.0))
            except (TypeError, ValueError):
                pass
            continue
        amount = fact.get("annual_wan")
        if amount is None:
            amount = fact.get("amount_wan")
        if amount is not None:
            try:
                values.append(float(amount))
            except (TypeError, ValueError):
                pass
            continue
        share = fact.get("share")
        if share is not None:
            try:
                ratio = float(share)
                ratio = ratio / 100.0 if abs(ratio) > 1.0 else ratio
                # share is a claim on capacity, not a hard allocation that can
                # zero-out when base is temporarily 0. Keep a non-null series so
                # structure checks can still see confirmed sources; coverage uses
                # max(base*share, debt need) at render time when possible.
                values.append(base * ratio)
            except (TypeError, ValueError):
                pass
    if not values:
        return None
    return round(sum(values), 2)


def _normalize_rows(key: str, rows: list, fin: dict) -> list[dict]:
    """归一化各表行字段，便于统一投影。"""
    out: list[dict] = []
    if key == "debt-service":
        loan_rate = (
            (fin.get("raw") or {}).get("loan_rate")
            or (fin.get("funding") or {}).get("loan_rate")
            or (fin.get("finance_inputs") or {}).get("loan_rate")
            or 0.0
        )
        profit_rows = (fin.get("annual") or {}).get("profit_distribution") or []
        dep_rows = (fin.get("annual") or {}).get("depreciation_table") or []
        amort_rows = (fin.get("annual") or {}).get("amortization_table") or []
        source_facts, _source_provenance = _repay_source_facts(fin)
        debt_domain = _confirmed_fact_domains(fin).get("debt_schedule") or {}
        allocation_method = str(
            debt_domain.get("repayment_allocation_method")
            or debt_domain.get("allocation_method")
            or "pro_rata"
        )
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            year = r.get("year") or r.get("period")
            try:
                y_idx = int(year) - 1 if year is not None else -1
            except (TypeError, ValueError):
                y_idx = -1
            profit_base = 0.0
            dep_base = 0.0
            amort_base = 0.0
            if 0 <= y_idx < len(profit_rows) and isinstance(profit_rows[y_idx], dict):
                prow = profit_rows[y_idx]
                base = prow.get("distributable")
                if base is None:
                    np_ = float(prow.get("net_profit") or 0.0)
                    surplus = float(prow.get("surplus_reserve") or 0.0)
                    base = max(np_ - surplus, 0.0)
                profit_base = float(base or 0.0)
            if 0 <= y_idx < len(dep_rows) and isinstance(dep_rows[y_idx], dict):
                dep_base = float(dep_rows[y_idx].get("depreciation") or 0.0)
            if 0 <= y_idx < len(amort_rows) and isinstance(amort_rows[y_idx], dict):
                amort_base = float(amort_rows[y_idx].get("amortization") or 0.0)
            principal = r.get("principal") if r.get("principal") is not None else r.get("repay_principal")
            interest = r.get("interest") if r.get("interest") is not None else r.get("pay_interest")
            debt_service = round(float(principal or 0.0) + float(interest or 0.0), 2)
            profit_src = _source_value(
                source_facts, kind="profit", year_index=y_idx, base=profit_base,
            )
            dep_src = _source_value(
                source_facts, kind="depreciation", year_index=y_idx, base=dep_base,
            )
            amort_src = _source_value(
                source_facts, kind="amortization", year_index=y_idx, base=amort_base,
            )
            available_parts = [
                max(float(profit_src or 0.0), 0.0),
                max(float(dep_src or 0.0), 0.0),
                max(float(amort_src or 0.0), 0.0),
            ]
            available_total = round(sum(available_parts), 2)
            principal_due = max(float(principal or 0.0), 0.0)
            actual_total = round(min(principal_due, available_total), 2)
            actual_parts = [0.0, 0.0, 0.0]
            if allocation_method == "pro_rata" and available_total > 0 and actual_total > 0:
                actual_parts[0] = round(actual_total * available_parts[0] / available_total, 2)
                actual_parts[1] = round(actual_total * available_parts[1] / available_total, 2)
                actual_parts[2] = round(actual_total - actual_parts[0] - actual_parts[1], 2)
            elif actual_total > 0:
                remaining = actual_total
                for index, value in enumerate(available_parts):
                    actual_parts[index] = round(min(value, remaining), 2)
                    remaining = round(remaining - actual_parts[index], 2)
            # If confirmed sources use share/annual amounts that still leave a
            # coverage gap, keep values but structure gate will fail closed.
            # When annual_wan is provided, base is ignored and values stay fixed.
            out.append({
                "year": year,
                "begin": r.get("begin") if r.get("begin") is not None else r.get("begin_balance"),
                "draw": r.get("draw") if r.get("draw") is not None else r.get("loan_draw"),
                "rate": r.get("rate") if r.get("rate") is not None else loan_rate,
                "principal": principal,
                "interest": interest,
                "debt_service": debt_service,
                "end": r.get("end") if r.get("end") is not None else r.get("end_balance"),
                "repay_source_profit": profit_src,
                "repay_source_dep": dep_src,
                "repay_source_amort": amort_src,
                "repay_available": available_total,
                "repay_actual": actual_total,
                "repay_actual_profit": actual_parts[0],
                "repay_actual_dep": actual_parts[1],
                "repay_actual_amort": actual_parts[2],
                "repay_surplus": round(available_total - actual_total, 2),
                "repay_allocation_method": allocation_method,
                "repay_actual_covers_principal": abs(actual_total - principal_due) <= 0.05,
                "dscr": (
                    round((available_total + float(interest or 0.0)) / debt_service, 2)
                    if debt_service > 0 else None
                ),
                "icr": r.get("icr"),
            })
        return out
    if key == "profit-distribution":
        cost_rows = (fin.get("annual") or {}).get("total_cost") or []
        fin_in = _effective_input_revision(fin)
        if not isinstance(fin_in, dict):
            fin_in = {}
        raw = fin.get("raw") if isinstance(fin.get("raw"), dict) else {}
        dist = (
            fin_in.get("distribution_policy")
            or raw.get("distribution_policy")
            or {}
        )
        if not isinstance(dist, dict):
            dist = {}
        domains = {}
        pack = fin_in.get("finance_fact_pack") or raw.get("finance_fact_pack") or {}
        if isinstance(pack, dict) and isinstance(pack.get("domains"), dict):
            domains = pack.get("domains") or {}
            if isinstance(domains.get("distribution_policy"), dict) and not dist:
                dist = domains.get("distribution_policy") or {}
        opening_undistributed = 0.0
        for index, r in enumerate(rows or []):
            if not isinstance(r, dict):
                continue
            np_ = float(r.get("net_profit") or 0.0)
            surplus = r.get("surplus_reserve")
            if surplus is None:
                surplus = round(max(np_, 0.0) * 0.10, 2)
            loss_offset = float(r.get("loss_offset") or r.get("loss_used") or 0.0)
            total_profit = float(r.get("total_profit") or 0.0)
            taxable = r.get("taxable_income")
            if taxable is None:
                taxable = round(total_profit - loss_offset, 2)
            distributable = r.get("distributable")
            if distributable is None:
                distributable = round(max(np_ - float(surplus or 0.0), 0.0), 2)
            # Explicit policy: confirmed zero vs rate vs missing.
            arbitrary = r.get("arbitrary_reserve")
            if arbitrary is None:
                if dist.get("arbitrary_reserve_confirmed_zero") is True:
                    arbitrary = 0.0
                elif dist.get("arbitrary_reserve_rate") is not None:
                    try:
                        arbitrary = round(
                            max(float(distributable or 0.0), 0.0)
                            * float(dist.get("arbitrary_reserve_rate") or 0.0),
                            2,
                        )
                    except (TypeError, ValueError):
                        arbitrary = None
                elif dist.get("arbitrary_reserve_wan") is not None:
                    try:
                        arbitrary = float(dist.get("arbitrary_reserve_wan") or 0.0)
                    except (TypeError, ValueError):
                        arbitrary = None
            investor = r.get("investor_distribution")
            if investor is None:
                if dist.get("investor_distribution_confirmed_zero") is True:
                    investor = 0.0
                elif dist.get("investor_distribution_rate") is not None:
                    try:
                        base = max(
                            float(distributable or 0.0) - float(arbitrary or 0.0),
                            0.0,
                        )
                        investor = round(
                            base * float(dist.get("investor_distribution_rate") or 0.0),
                            2,
                        )
                    except (TypeError, ValueError):
                        investor = None
                elif dist.get("investor_distribution_wan") is not None:
                    try:
                        investor = float(dist.get("investor_distribution_wan") or 0.0)
                    except (TypeError, ValueError):
                        investor = None
            undistributed = r.get("undistributed")
            if undistributed is None:
                undistributed = round(
                    np_
                    - float(surplus or 0.0)
                    - float(arbitrary or 0.0)
                    - float(investor or 0.0),
                    2,
                )
            cost_row = cost_rows[index] if index < len(cost_rows) and isinstance(cost_rows[index], dict) else {}
            interest = float(cost_row.get("interest") or 0.0)
            depreciation = float(cost_row.get("depreciation") or 0.0)
            amortization = float(cost_row.get("amortization") or 0.0)
            ebit = r.get("ebit")
            if ebit is None:
                ebit = round(total_profit + interest, 2)
            ebitda = r.get("ebitda")
            if ebitda is None:
                ebitda = round(float(ebit or 0.0) + depreciation + amortization, 2)
            available_distribution = round(opening_undistributed + np_, 2)
            out.append({
                **r,
                "ebit": ebit,
                "ebitda": ebitda,
                "loss_offset": loss_offset,
                "taxable_income": taxable,
                "begin_undistributed": opening_undistributed,
                "available_distribution": available_distribution,
                "surplus_reserve": surplus,
                "distributable": distributable,
                "arbitrary_reserve": arbitrary,
                "investor_distribution": investor,
                "undistributed": undistributed,
            })
            opening_undistributed = float(undistributed or 0.0)
        return out
    if key == "interest-during-construction":
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            out.append({
                "period": r.get("period") or r.get("year"),
                "begin_balance": r.get("begin_balance") if r.get("begin_balance") is not None else r.get("begin"),
                "draw": r.get("draw"),
                "rate": r.get("rate") if r.get("rate") is not None else r.get("rate_pct"),
                "interest": r.get("interest"),
                "end_balance": r.get("end_balance") if r.get("end_balance") is not None else r.get("end"),
            })
        return out
    if key == "total-cost":
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            d = dict(r)
            if d.get("total_cost") is None and d.get("total") is not None:
                d["total_cost"] = d["total"]
            out.append(d)
        return out
    if key == "wage":
        fin_in = _effective_input_revision(fin)
        if not isinstance(fin_in, dict):
            fin_in = {}
        raw = fin.get("raw") or {}
        fact_domains = _confirmed_fact_domains(fin)
        staff = (
            fin_in.get("staff_detail")
            or fin_in.get("wage_detail")
            or fin_in.get("labor_plan")
            or raw.get("staff_detail")
            or fact_domains.get("staff_detail")
            or []
        )
        # 有定员明细时，在逐年合计行上附加 staff_categories 供结构门禁/导出使用
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            d = dict(r)
            if isinstance(staff, list) and staff:
                d["staff_categories"] = staff
            out.append(d)
        return out
    if key == "depreciation":
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            out.append(dict(r))
        return out
    return [r for r in (rows or []) if isinstance(r, dict)]


def _attach_reference_row_trees(key: str, body: dict[str, Any], fin: dict[str, Any]) -> dict[str, Any]:
    """在 structured 表上挂甲方参考行树（有输入才展开，不造数）。

    这不是 Excel 公式同构全量重写，而是把参考表语义行树固化到 JSON/导出元数据，
    供门禁与可读包展示；缺料时 row_tree 标记 incomplete。
    """
    fin_in = _effective_input_revision(fin)
    if not isinstance(fin_in, dict):
        fin_in = {}
    raw = fin.get("raw") or {}

    if key == "income-statement":
        products = body.get("product_tree") or []
        tree = ["营业收入"]
        if products:
            for p in products:
                if not isinstance(p, dict):
                    continue
                name = str(p.get("name") or "产品")
                tree.extend([f"  {name}", "    单价", "    数量/爬坡"])
            tree.append("营业收入合计")
        else:
            tree.append("  （flat/单点收入，无分产品树）")
        tree.extend([
            "销项税额",
            "营业税金与附加",
            "  城市维护建设税",
            "  教育费附加",
            "  地方教育附加",
            "进项税额",
            "应纳增值税",
        ])
        body["row_tree"] = tree
        body["row_tree_complete"] = bool(products)
        return body

    if key == "total-cost":
        cost_items = fin_in.get("cost_items") or raw.get("cost_items") or {}
        tree = ["生产负荷（%）"]
        if isinstance(cost_items, dict) and cost_items:
            for name in cost_items.keys():
                tree.append(str(name))
            body["cost_item_tree"] = [
                {"name": str(k), "amount_wan": v} for k, v in cost_items.items()
            ]
        else:
            tree.append("经营成本（默认成本率路径，无明细树）")
        tree.extend([
            "经营成本小计",
            "折旧费",
            "摊销费",
            "财务费用",
            "  长期贷款利息",
            "总成本费用",
            "  固定成本",
            "  变动成本",
        ])
        body["row_tree"] = tree
        body["row_tree_complete"] = isinstance(cost_items, dict) and len(cost_items) >= 3
        return body

    if key == "wage":
        fact_domains = _confirmed_fact_domains(fin)
        staff = (
            fin_in.get("staff_detail")
            or fin_in.get("wage_detail")
            or raw.get("staff_detail")
            or fact_domains.get("staff_detail")
            or []
        )
        tree = ["劳动定员"]
        if isinstance(staff, list) and staff:
            for row in staff:
                if not isinstance(row, dict):
                    continue
                cat = row.get("category") or row.get("name") or "人员"
                tree.append(f"  {cat}：人数×人均年工资")
            body["staff_detail"] = staff
        else:
            tree.append("  （仅有工资/福利合计，无定员明细）")
        tree.extend(["工资额", "福利费", "合计"])
        body["row_tree"] = tree
        body["row_tree_complete"] = isinstance(staff, list) and bool(staff)
        return body

    if key == "depreciation":
        fact_domains = _confirmed_fact_domains(fin)
        classes: list = []
        dep_rows = (fin.get("annual") or {}).get("depreciation_table") or []
        if isinstance(dep_rows, list):
            for row in dep_rows:
                if isinstance(row, dict) and row.get("classes"):
                    classes = list(row.get("classes") or [])
                    break
        if not classes:
            classes = (
                raw.get("depreciation_classes")
                or raw.get("asset_classes")
                or fin_in.get("depreciation_classes")
                or fin_in.get("asset_classes")
                or fact_domains.get("asset_classes")
                or []
            )
        tree = []
        if isinstance(classes, list) and classes:
            for c in classes:
                if isinstance(c, dict):
                    name = c.get("name") or c.get("label") or "资产类别"
                else:
                    name = str(c)
                tree.extend([str(name), "  原值", "  当期折旧费", "  净值"])
            body["asset_classes"] = classes
        else:
            tree = ["固定资产（综合原值）", "  原值", "  当期折旧费", "  累计折旧", "  净值"]
        body["row_tree"] = tree
        body["row_tree_complete"] = isinstance(classes, list) and len(classes) >= 2
        return body

    if key == "profit-distribution":
        body["row_tree"] = [
            "营业收入", "营业税金及附加", "总成本费用", "补贴收入",
            "利润总额", "弥补以前年度亏损", "应纳税所得额", "所得税", "净利润",
            "期初未分配利润", "可供分配的利润", "提取法定盈余公积金",
            "可供投资者分配的利润", "提取任意盈余公积金", "投资各方利润分配", "未分配利润",
        ]
        required = {
            "total_profit", "ebit", "ebitda", "loss_offset", "taxable_income",
            "net_profit", "begin_undistributed", "available_distribution",
            "surplus_reserve", "distributable", "arbitrary_reserve",
            "investor_distribution", "undistributed",
        }
        keys = {
            str(c.get("key") or "")
            for c in list(body.get("columns") or []) + list(body.get("engine_columns") or [])
            if isinstance(c, dict)
        }
        # Field presence ≠ distribution policy confirmed. Track policy separately.
        body["row_tree_complete"] = required.issubset(keys) and bool(body.get("rows"))
        # Field presence ≠ distribution policy complete.
        arb = _column_values(body, "arbitrary_reserve")
        inv_dist = _column_values(body, "investor_distribution")
        fin_in = _effective_input_revision(fin)
        if not isinstance(fin_in, dict):
            fin_in = {}
        raw = fin.get("raw") if isinstance(fin.get("raw"), dict) else {}
        dist = fin_in.get("distribution_policy") or raw.get("distribution_policy") or {}
        pack = fin_in.get("finance_fact_pack") or raw.get("finance_fact_pack") or {}
        if isinstance(pack, dict) and isinstance((pack.get("domains") or {}).get("distribution_policy"), dict):
            dist = dist or (pack.get("domains") or {}).get("distribution_policy") or {}
        policy_present = bool(dist) and (
            dist.get("arbitrary_reserve_confirmed_zero") is True
            or dist.get("investor_distribution_confirmed_zero") is True
            or dist.get("arbitrary_reserve_rate") is not None
            or dist.get("investor_distribution_rate") is not None
            or dist.get("arbitrary_reserve_wan") is not None
            or dist.get("investor_distribution_wan") is not None
            or (
                bool(arb) and bool(inv_dist)
                and any(_number(v) is not None for v in arb)
                and any(_number(v) is not None for v in inv_dist)
            )
        )
        body["distribution_policy_confirmed"] = policy_present
        body["derived_policy_fields"] = {
            "loss_offset": "explicit value or deterministic zero",
            "surplus_reserve": "deterministic 10% legal-reserve formula",
            "ebit_ebitda": "derived from profit and depreciation/amortization schedules",
            "arbitrary_reserve": "project fact; blank unless supplied",
            "investor_distribution": "project fact; blank unless supplied",
        }
        return body

    if key == "debt-service":
        source_facts, provenance = _repay_source_facts(fin)
        body["row_tree"] = [
            "期初借款余额", "本年借款", "当期还本付息", "还本", "付息", "期末借款余额",
            "可用于偿债的资金来源",
            "  可供投资者分配的利润",
            "  折旧费",
            "  摊销费",
            "偿债后剩余资金",
            "利息备付率", "偿债备付率",
        ]
        kinds = {
            _source_kind(row.get("name") or row.get("source") or row.get("category"))
            for row in source_facts
        }
        complete = {"profit", "depreciation", "amortization"}.issubset(kinds)
        body["repay_sources"] = source_facts
        body["repay_source_provenance"] = provenance
        body["repay_sources_confirmed"] = bool(source_facts and provenance)
        body["row_tree_complete"] = complete and bool(body.get("rows"))
        return body

    if key == "working-capital":
        inv_detail = body.get("inventory_detail") or {}
        tree = [
            "流动资产", "  应收账款", "  存货",
        ]
        if inv_detail:
            for k in inv_detail:
                tree.append(f"    {k}")
        else:
            tree.append("    （存货未分项）")
        tree.extend([
            "  现金", "流动负债", "  应付账款", "流动资金",
            "铺底流动资金", "当期增加额", "流动资金来源",
        ])
        body["row_tree"] = tree
        body["row_tree_complete"] = bool(inv_detail) and body.get("method") != "ratio_backsolve"
        return body

    return body


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _item_row_period_values(body: dict[str, Any], item_label: str) -> list[Any]:
    """Read period values from an item-row layout by item label."""
    columns = list(body.get("columns") or [])
    rows = list(body.get("rows") or [])
    keys = [
        str(column.get("key") or "")
        for column in columns
        if isinstance(column, dict)
    ]
    if "item" not in keys:
        return []
    item_index = keys.index("item")
    period_indices = [i for i, key in enumerate(keys) if key.startswith("period_")]
    if not period_indices:
        # Fallback to amount/total single-value layouts.
        for amount_key in ("amount", "total"):
            if amount_key in keys:
                amount_index = keys.index(amount_key)
                for row in rows:
                    if (
                        isinstance(row, (list, tuple))
                        and item_index < len(row)
                        and str(row[item_index] or "").strip() == item_label
                        and amount_index < len(row)
                    ):
                        return [row[amount_index]]
        return []
    for row in rows:
        if not isinstance(row, (list, tuple)) or item_index >= len(row):
            continue
        if str(row[item_index] or "").strip() != item_label:
            continue
        return [
            row[i] if i < len(row) else None
            for i in period_indices
        ]
    return []


def _column_values(body: dict[str, Any], key: str) -> list[Any]:
    columns = list(body.get("columns") or [])
    rows = list(body.get("rows") or [])
    keys = [
        str(column.get("key") or "")
        for column in columns
        if isinstance(column, dict)
    ]
    if key in keys:
        index = keys.index(key)
        return [
            row[index] if isinstance(row, (list, tuple)) and index < len(row) else None
            for row in rows
        ]
    # Promoted item-row layout: map engine field → item label via reference_row_fields.
    fields = list(body.get("reference_row_fields") or [])
    if key in fields:
        field_index = fields.index(key)
        item_index = keys.index("item") if "item" in keys else -1
        period_indices = [i for i, col in enumerate(keys) if col.startswith("period_")]
        if item_index >= 0 and period_indices and field_index < len(rows):
            # Rows are in the same order as reference_row_fields.
            row = rows[field_index]
            if isinstance(row, (list, tuple)):
                return [row[i] if i < len(row) else None for i in period_indices]
        # Fallback: engine matrix if still available.
    if body.get("engine_columns"):
        columns = list(body.get("engine_columns") or [])
        rows = list(body.get("engine_rows") or [])
        keys = [
            str(column.get("key") or "")
            for column in columns
            if isinstance(column, dict)
        ]
        if key in keys:
            index = keys.index(key)
            return [
                row[index] if isinstance(row, (list, tuple)) and index < len(row) else None
                for row in rows
            ]
    return []


def _renderer_row_contract(key: str, body: dict[str, Any]) -> dict[str, Any]:
    """Enforce renderer-owned row contracts in addition to schema checks.

    These checks prevent a column heading or an arbitrary formula from turning a
    summary schedule into reference grade.  They intentionally cover the four
    tables that previously passed with aggregate values only.
    """
    checks: dict[str, bool] = {}
    gaps: list[str] = []

    if key == "working-capital":
        columns = [
            str(column.get("key") or "")
            for column in body.get("columns") or []
            if isinstance(column, dict)
        ]
        item_index = columns.index("item") if "item" in columns else -1
        days_index = columns.index("days") if "days" in columns else -1
        amount_index = columns.index("amount") if "amount" in columns else -1
        by_label: dict[str, list[Any]] = {}
        for row in body.get("rows") or []:
            if (
                isinstance(row, (list, tuple)) and item_index >= 0
                and len(row) > item_index
            ):
                by_label[str(row[item_index] or "").strip()] = list(row)
        inventory_labels = ("原材料", "燃料及动力", "在产品", "产成品")
        explicit_component_detail = body.get("inventory_detail") or {}
        explicit_components_complete = bool(
            body.get("inventory_components_complete")
            and isinstance(explicit_component_detail, dict)
            and all(
                isinstance(explicit_component_detail.get(component), dict)
                and explicit_component_detail[component].get("complete") is True
                for component in ("raw", "fuel", "wip", "finished")
            )
        )
        checks["inventory_component_values"] = explicit_components_complete or all(
            label in by_label
            and days_index >= 0 and amount_index >= 0
            and len(by_label[label]) > max(days_index, amount_index)
            and (_number(by_label[label][days_index]) or 0.0) > 0
            and (_number(by_label[label][amount_index]) or 0.0) > 0
            for label in inventory_labels
        )
        source_values = []
        for label in ("短期借款", "企业自筹流动资金"):
            row = by_label.get(label) or []
            source_values.append(
                _number(row[amount_index])
                if amount_index >= 0 and len(row) > amount_index else None
            )
        total_row = by_label.get("流动资金") or []
        total_value = (
            _number(total_row[amount_index])
            if amount_index >= 0 and len(total_row) > amount_index else None
        )
        # Prefer investment-stated WC when present: sources fund the investment
        # requirement. Turnover net may differ when no force-scale is applied.
        investment_total = _number(body.get("investment_total"))
        source_sum = None
        if all(value is not None and value >= 0 for value in source_values):
            source_sum = sum(float(value or 0.0) for value in source_values)
        source_row = by_label.get("流动资金来源") or []
        source_row_total = (
            _number(source_row[amount_index])
            if amount_index >= 0 and len(source_row) > amount_index else None
        )
        # Sources may fund either the turnover net or the investment WC requirement.
        closure_targets = [
            t for t in (total_value, investment_total, source_row_total) if t is not None
        ]
        checks["working_capital_sources"] = (
            source_sum is not None
            and (
                (bool(closure_targets) and any(abs(source_sum - float(t)) <= 0.5 for t in closure_targets))
                or (
                    # Explicit short loan + self funded both present and non-negative:
                    # still require a positive total target when available.
                    source_sum >= 0 and total_value is None and investment_total is None
                )
            )
        )
        if not checks["inventory_component_values"]:
            gaps.append("存货四分项缺周转天数或可复算金额")
        if not checks["working_capital_sources"]:
            gaps.append("流动资金来源未以短期借款+企业自筹明确闭合")

    elif key == "income-statement":
        checks["tax_component_policy"] = bool(body.get("tax_component_policy_confirmed"))
        if not checks["tax_component_policy"]:
            gaps.append("城建税/教育费附加/地方教育附加缺明确政策口径")

    elif key == "total-cost":
        # Name-based fixed/variable guessing cannot reach reference; require
        # explicit project cost_behavior confirmation.
        checks["cost_behavior_confirmed"] = bool(body.get("cost_behavior_confirmed"))
        if not checks["cost_behavior_confirmed"]:
            gaps.append("固定/变动成本分类未确认（cost_behavior）；名称猜测仅估算，不得升 reference")

    elif key == "wage":
        staff = body.get("staff_detail") or []
        complete = 0
        for row in staff if isinstance(staff, list) else []:
            if not isinstance(row, dict):
                continue
            headcount = row.get("headcount") if row.get("headcount") is not None else row.get("人数")
            wage = row.get("avg_wage_yuan") if row.get("avg_wage_yuan") is not None else row.get("人均年工资")
            if str(row.get("category") or row.get("name") or "").strip() and (
                (_number(headcount) or 0.0) > 0 and (_number(wage) or 0.0) > 0
            ):
                complete += 1
        checks["staff_category_facts"] = complete >= 1
        if not checks["staff_category_facts"]:
            gaps.append("工资表缺可复算的人员类别×人数×人均年工资")

    elif key == "depreciation":
        classes = body.get("asset_classes") or []
        complete = 0
        for row in classes if isinstance(classes, list) else []:
            if not isinstance(row, dict):
                continue
            original = next((row.get(field) for field in (
                "original_value_wan", "original_wan", "original_value", "amount_wan",
            ) if row.get(field) is not None), None)
            years = next((row.get(field) for field in (
                "depreciation_years", "dep_years", "years", "life",
            ) if row.get(field) is not None), None)
            if str(row.get("name") or row.get("label") or "").strip() and (
                (_number(original) or 0.0) > 0 and (_number(years) or 0.0) > 0
            ):
                complete += 1
        checks["asset_class_facts"] = complete >= 2
        if not checks["asset_class_facts"]:
            gaps.append("折旧表缺至少 2 类资产的原值×年限明细")

    elif key == "profit-distribution":
        checks["reference_row_tree"] = bool(body.get("row_tree_complete"))
        derived_fields = (
            "total_profit", "ebit", "ebitda", "loss_offset", "taxable_income",
            "net_profit", "begin_undistributed", "available_distribution",
            "surplus_reserve", "distributable", "undistributed",
        )
        checks["derived_rows_populated"] = bool(body.get("rows")) and all(
            values and all(_number(value) is not None for value in values)
            for values in (_column_values(body, field) for field in derived_fields)
        )
        checks["derived_policy_disclosed"] = bool(body.get("derived_policy_fields"))
        # Field presence ≠ distribution policy complete.
        checks["distribution_policy_confirmed"] = bool(body.get("distribution_policy_confirmed"))
        if not checks["reference_row_tree"]:
            gaps.append("利润表未完整展示 EBIT/EBITDA/亏损弥补/公积金/分配行")
        if not checks["derived_rows_populated"]:
            gaps.append("利润表关键行无法从确定性结果复算")
        if not checks["distribution_policy_confirmed"]:
            # Non-blocking disclosure gap: structure may still pass, but policy is not claimed complete.
            body.setdefault("notes", [])
            if isinstance(body.get("notes"), list):
                note = "任意盈余公积金/投资各方分配未确认；行名齐全≠政策完整"
                if note not in body["notes"]:
                    body["notes"].append(note)

    elif key == "debt-service":
        facts = body.get("repay_sources") or []
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for row in facts if isinstance(facts, list) else []:
            if not isinstance(row, dict):
                continue
            kind = _source_kind(row.get("name") or row.get("source") or row.get("category"))
            if kind:
                by_kind.setdefault(kind, []).append(row)

        def has_basis(rows: list[dict[str, Any]]) -> bool:
            return any(
                row.get(field) is not None
                for row in rows
                for field in ("share", "annual_wan", "amount_wan", "annual_schedule_wan", "schedule_wan")
            )

        checks["confirmed_repay_sources"] = bool(body.get("repay_sources_confirmed")) and all(
            has_basis(by_kind.get(kind) or [])
            for kind in ("profit", "depreciation", "amortization")
        )
        source_fields = ("repay_source_profit", "repay_source_dep", "repay_source_amort")
        checks["repay_source_values"] = bool(body.get("rows")) and all(
            values and all(_number(value) is not None for value in values)
            for values in (_column_values(body, field) for field in source_fields)
        )
        # 模板中的利润、折旧和摊销用于偿还本金；利息已计入经营损益。
        principal_vals = _column_values(body, "principal")
        profit_vals = _column_values(body, "repay_source_profit")
        dep_vals = _column_values(body, "repay_source_dep")
        amort_vals = _column_values(body, "repay_source_amort")
        repay_closed = bool(principal_vals) and bool(profit_vals) and bool(dep_vals) and bool(amort_vals)
        if repay_closed:
            for idx, need in enumerate(principal_vals):
                need_n = _number(need)
                if need_n is None:
                    repay_closed = False
                    break
                supply = (
                    float(_number(profit_vals[idx]) or 0.0)
                    + float(_number(dep_vals[idx]) or 0.0)
                    + float(_number(amort_vals[idx]) or 0.0)
                ) if idx < len(profit_vals) and idx < len(dep_vals) and idx < len(amort_vals) else 0.0
                # 允许来源合计 ≥ 偿债额（可有余量）；不足则缺口。
                if supply + 0.5 < float(need_n):
                    repay_closed = False
                    break
        checks["repay_source_covers_principal"] = repay_closed
        # Parent/child identity on available funds. Prefer item-row layout; fall
        # back to engine field series so pre/post promotion both work.
        available_vals = (
            _item_row_period_values(body, "偿债资金来源（可用）")
            or _item_row_period_values(body, "可用于偿债的资金来源")
            or _column_values(body, "repay_available")
        )
        surplus_vals = (
            _item_row_period_values(body, "偿债后剩余资金")
            or _column_values(body, "repay_surplus")
        )
        profit_item = _item_row_period_values(body, "可供投资者分配的利润") or profit_vals
        dep_item = _item_row_period_values(body, "折旧费") or dep_vals
        amort_item = _item_row_period_values(body, "摊销费") or amort_vals
        principal_item = _item_row_period_values(body, "还本") or principal_vals
        actual_vals = _item_row_period_values(body, "实际用于偿债的资金") or _column_values(body, "repay_actual")
        actual_profit = _item_row_period_values(body, "实际使用利润") or _column_values(body, "repay_actual_profit")
        actual_dep = _item_row_period_values(body, "实际使用折旧") or _column_values(body, "repay_actual_dep")
        actual_amort = _item_row_period_values(body, "实际使用摊销") or _column_values(body, "repay_actual_amort")
        if not available_vals and profit_item and dep_item and amort_item:
            n = min(len(profit_item), len(dep_item), len(amort_item))
            available_vals = [
                round(
                    float(_number(profit_item[i]) or 0.0)
                    + float(_number(dep_item[i]) or 0.0)
                    + float(_number(amort_item[i]) or 0.0),
                    2,
                )
                for i in range(n)
            ]
        parent_child_ok = bool(available_vals) and bool(profit_item) and bool(dep_item) and bool(amort_item)
        if parent_child_ok:
            for idx in range(len(available_vals)):
                avail = _number(available_vals[idx])
                if avail is None or idx >= len(profit_item) or idx >= len(dep_item) or idx >= len(amort_item):
                    parent_child_ok = False
                    break
                comp = (
                    float(_number(profit_item[idx]) or 0.0)
                    + float(_number(dep_item[idx]) or 0.0)
                    + float(_number(amort_item[idx]) or 0.0)
                )
                if abs(float(avail) - comp) > 0.05:
                    parent_child_ok = False
                    break
                if not all(idx < len(values) for values in (actual_vals, actual_profit, actual_dep, actual_amort, principal_item)):
                    parent_child_ok = False
                    break
                actual_n = float(_number(actual_vals[idx]) or 0.0)
                actual_comp = sum(float(_number(values[idx]) or 0.0) for values in (actual_profit, actual_dep, actual_amort))
                need_n = float(_number(principal_item[idx]) or 0.0)
                if abs(actual_n - actual_comp) > 0.05 or abs(actual_n - need_n) > 0.05:
                    parent_child_ok = False
                    break
                if surplus_vals and idx < len(surplus_vals):
                    surplus_n = _number(surplus_vals[idx])
                    if surplus_n is not None:
                        if abs(float(surplus_n) - (float(avail) - actual_n)) > 0.05:
                            parent_child_ok = False
                            break
        checks["repay_source_parent_child_closed"] = parent_child_ok
        if not checks["confirmed_repay_sources"]:
            gaps.append("偿债资金来源未绑定 confirmed fact_pack 明细；禁止默认 75% 伪造")
        if not checks["repay_source_values"]:
            gaps.append("偿债资金来源的利润/折旧/摊销年度金额不完整")
        if not checks["repay_source_covers_principal"]:
            gaps.append("可用偿债资金合计不足以覆盖各年还本额")
        if not checks["repay_source_parent_child_closed"]:
            gaps.append("偿债来源父子不勾稽：可用/实际/剩余三组父子恒等式失败")

    elif key == "funding":
        # Uses/sources year balance and fact-plan source are required for reference.
        checks["funding_balance_ok"] = body.get("funding_balance_ok") is True or (
            body.get("funding_plan_source") == "proportional_spread_fallback"
            and body.get("grade") != "reference"
        )
        # Proportional fallback cannot be formal/reference truth.
        if body.get("funding_plan_source") == "proportional_spread_fallback":
            checks["funding_fact_plan"] = False
            gaps.append("资金计划为比例摊分回退，非事实包分年真源")
        else:
            checks["funding_fact_plan"] = True
        if body.get("funding_balance_ok") is False:
            checks["funding_balance_ok"] = False
            gaps.append(
                f"资金用途与来源不闭合 uses={body.get('uses_total')} sources={body.get('sources_total')}"
            )

    if not checks:
        return {"ok": True, "coverage": 1.0, "checks": {}, "gaps": []}
    passed = sum(1 for value in checks.values() if value)
    coverage = round(passed / len(checks), 4)
    return {
        "ok": coverage >= 0.999 and not gaps,
        "coverage": coverage,
        "checks": checks,
        "gaps": gaps,
    }


def _build_investment(fin: dict) -> dict[str, Any]:
    inv = fin.get("investment") or {}
    detail = inv.get("breakdown_detail") or {}
    fin_in = _effective_input_revision(fin)
    if not isinstance(fin_in, dict):
        fin_in = {}
    raw = fin.get("raw") or {}
    raw_bd = fin_in.get("invest_breakdown") or {}
    if not raw_bd and isinstance(raw, dict):
        raw_bd = raw.get("invest_breakdown") or {}
    if not isinstance(raw_bd, dict):
        raw_bd = {}
    construction_items = raw_bd.get("construction_items") or []
    if detail:
        rows: list[dict[str, Any]] = []
        total = float(inv.get("total") or 0.0)

        def add_group(no: str, name: str, items: list[Any], amount_key: str) -> None:
            group_total = float(detail.get(amount_key) or 0.0)
            rows.append({
                "no": no, "name": name, "unit": "", "quantity": None,
                "indicator": None, "civil": None, "equipment": None,
                "installation": None, "other": None, "total": group_total,
                "pct": round(group_total / total * 100, 2) if total else None,
            })
            for idx, item in enumerate(items or [], start=1):
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    label, amount = item[0], item[1]
                elif isinstance(item, dict):
                    label = item.get("name") or item.get("label") or f"明细{idx}"
                    amount = item.get("amount") or item.get("amount_wan")
                else:
                    continue
                amount = float(amount or 0.0)
                bucket = "other"
                text = str(label)
                if "建筑" in text or "土建" in text:
                    bucket = "civil"
                elif "设备" in text:
                    bucket = "equipment"
                elif "安装" in text:
                    bucket = "installation"
                row = {
                    "no": f"{no}.{idx}", "name": label, "unit": "万元",
                    "quantity": None, "indicator": None, "civil": None,
                    "equipment": None, "installation": None, "other": None,
                    "total": amount,
                    "pct": round(amount / total * 100, 2) if total else None,
                }
                row[bucket] = amount
                rows.append(row)

        if construction_items:
            normalized_items = []
            for item in construction_items:
                if not isinstance(item, dict):
                    continue
                quantity = item.get("quantity")
                indicator = item.get("indicator_yuan") if item.get("indicator_yuan") is not None else item.get("indicator")
                amount = item.get("amount_wan")
                if amount is None and quantity is not None and indicator is not None:
                    amount = round(float(quantity) * float(indicator) / 10000.0, 2)
                normalized_items.append({**item, "amount_wan": amount})
            group_total = round(sum(float(x.get("amount_wan") or 0.0) for x in normalized_items), 2)
            rows.append({"no": "1", "name": "工程费用", "unit": "", "quantity": None,
                         "indicator": None, "civil": None, "equipment": None,
                         "installation": None, "other": None, "total": group_total,
                         "pct": round(group_total / total * 100, 2) if total else None})
            for idx, item in enumerate(normalized_items, start=1):
                category = str(item.get("category") or "other")
                bucket = category if category in {"civil", "equipment", "installation", "other"} else "other"
                amount = float(item.get("amount_wan") or 0.0)
                row = {"no": f"1.{idx}", "name": item.get("name") or f"工程明细{idx}",
                       "unit": item.get("unit") or "", "quantity": item.get("quantity"),
                       "indicator": item.get("indicator_yuan") if item.get("indicator_yuan") is not None else item.get("indicator"),
                       "civil": None, "equipment": None, "installation": None, "other": None,
                       "total": amount, "pct": round(amount / total * 100, 2) if total else None}
                row[bucket] = amount
                rows.append(row)
        else:
            add_group("1", "工程费用", detail.get("engineering") or [], "engineering_total")
        add_group("2", "工程建设其他费用", detail.get("other") or [], "other_total")
        add_group("3", "预备费", detail.get("contingency") or [], "contingency_total")
        engineering_total = float(detail.get("engineering_total") or 0.0)
        if construction_items:
            engineering_total = round(sum(
                float(item.get("amount_wan") or 0.0)
                for item in normalized_items
            ), 2)
        one_to_three = round(
            engineering_total
            + float(detail.get("other_total") or 0.0)
            + float(detail.get("contingency_total") or 0.0),
            2,
        )
        interest = float(inv.get("interest") or 0.0)
        fixed_asset_total = round(one_to_three + interest, 2)
        summary_rows = (
            ("4", "一至三项合计", one_to_three),
            ("5", "建设期贷款利息", interest),
            ("6", "固定资产投资合计", fixed_asset_total),
            ("7", "流动资金", inv.get("working_capital")),
        )
        for no, name, amount in summary_rows:
            value = float(amount or 0.0)
            rows.append({
                "no": no, "name": name, "unit": "万元", "quantity": None,
                "indicator": None, "civil": None, "equipment": None,
                "installation": None, "other": value, "total": value,
                "pct": round(value / total * 100, 2) if total else None,
            })
        rows.append({
            "no": "", "name": "项目总投资合计", "unit": "万元",
            "quantity": None, "indicator": None, "civil": None,
            "equipment": None, "installation": None, "other": None,
            "total": total, "pct": 100.0,
        })
        cols = [
            ("no", "序号"), ("name", "工程或费用名称"), ("unit", "计量单位"),
            ("quantity", "工程量"), ("indicator", "估算指标（元）"),
            ("civil", "建筑工程"), ("equipment", "设备原价"),
            ("installation", "运杂安装工程费"), ("other", "其它费用"),
            ("total", "合计"), ("pct", "比例(%)"),
        ]
        return _pack_rows(
            rows, cols, source="investment.breakdown_detail",
            notes=["明细来自用户输入；未提供工程量/指标时保持空白，不反向造数"],
            extra={"layout_mode": "item_rows_component_columns"},
            # reference_structure 由 build_all_structured 统一裁决，禁止默认 True
        )
    rows = [
        {"no": "1", "name": "建设投资", "amount": inv.get("construction")},
        {"no": "2", "name": "建设期利息", "amount": inv.get("interest")},
        {"no": "3", "name": "流动资金", "amount": inv.get("working_capital")},
        {"no": "", "name": "项目总投资合计", "amount": inv.get("total")},
    ]
    cols = [("no", "序号"), ("name", "项目"), ("amount", "金额（万元）")]
    return _pack_rows(rows, cols, source="investment", notes=["摘要级：无明细时不拆建筑/设备/安装"])


def _build_funding(fin: dict) -> dict[str, Any]:
    fund = fin.get("funding") or {}
    inv = fin.get("investment") or {}
    build_years = int((fin.get("params") or {}).get("build_years") or 0)
    raw = fin.get("raw") if isinstance(fin.get("raw"), dict) else {}
    # input_revision (projected / latest) always wins over stale finance_inputs.
    fin_in = _effective_input_revision(fin)
    if not isinstance(fin_in, dict):
        fin_in = {}
    schedule = (
        fin_in.get("funding_annual_schedule")
        or raw.get("funding_annual_schedule")
        or []
    )
    if not isinstance(schedule, list):
        schedule = []

    def _year_key(row: Any) -> int:
        if not isinstance(row, dict):
            return 10**9
        try:
            return int(row.get("year") or row.get("period") or 10**9)
        except (TypeError, ValueError):
            return 10**9

    if schedule and any(isinstance(r, dict) and r.get("year") not in (None, "") for r in schedule):
        schedule = sorted(schedule, key=_year_key)

    equity_plan = fin_in.get("equity_inject_by_year") or raw.get("equity_inject_by_year") or []
    loan_plan = fin_in.get("loan_draw_by_year") or raw.get("loan_draw_by_year") or []
    construction_plan = (
        fin_in.get("construction_investment_by_year")
        or raw.get("construction_investment_by_year")
        or []
    )
    interest_plan = (
        fin_in.get("construction_interest_by_year")
        or raw.get("construction_interest_by_year")
        or []
    )
    outlay_plan = (
        fin_in.get("construction_outlay_by_year")
        or raw.get("construction_outlay_by_year")
        or []
    )
    has_fact_schedule = bool(schedule) or any(float(x or 0) for x in equity_plan or []) or any(
        float(x or 0) for x in loan_plan or []
    ) or any(float(x or 0) for x in outlay_plan or []) or any(
        float(x or 0) for x in construction_plan or []
    )
    if build_years > 0:
        year_keys = [f"year_{i + 1}" for i in range(build_years)]
        cols = [("no", "序号"), ("name", "项目")] + [
            (key, f"建设期第{i + 1}年") for i, key in enumerate(year_keys)
        ] + [("amount", "合计")]

        def from_list(values: Any, fallback_total: Any = None) -> dict[str, float]:
            vals = [0.0] * build_years
            if isinstance(values, list) and values:
                for i in range(build_years):
                    if i < len(values):
                        try:
                            vals[i] = round(float(values[i] or 0.0), 2)
                        except (TypeError, ValueError):
                            vals[i] = 0.0
            elif fallback_total not in (None, ""):
                total = float(fallback_total or 0.0)
                if build_years > 0:
                    per = round(total / build_years, 2)
                    vals = [per] * build_years
                    if vals:
                        vals[-1] = round(total - sum(vals[:-1]), 2)
            return {key: vals[i] for i, key in enumerate(year_keys)}

        def from_schedule(field: str, fallback_total: Any = None) -> dict[str, float]:
            if schedule:
                by_year = {
                    _year_key(row): row for row in schedule
                    if isinstance(row, dict) and 1 <= _year_key(row) <= build_years
                }
                values = []
                for i in range(1, build_years + 1):
                    row = by_year.get(i, {})
                    values.append(row.get(field) if row.get(field) is not None else 0.0)
                return from_list(values, fallback_total)
            return from_list([], fallback_total)

        if has_fact_schedule:
            interest = (
                from_list(interest_plan)
                if interest_plan else from_schedule("construction_interest_wan")
            )

            has_atomic_construction = any(
                isinstance(row, dict)
                and row.get("construction_investment_wan") not in (None, "")
                for row in schedule
            )
            if construction_plan:
                construction = from_list(construction_plan, inv.get("construction"))
            elif has_atomic_construction:
                construction = from_schedule("construction_investment_wan", inv.get("construction"))
            elif outlay_plan or any(
                isinstance(row, dict) and row.get("construction_outlay_wan") not in (None, "")
                for row in schedule
            ):
                # Legacy draft/reference only.  v1 formal plans must provide
                # construction_investment_wan and construction_interest_wan.
                outlay = from_list(outlay_plan) if outlay_plan else from_schedule("construction_outlay_wan")
                construction = {
                    key: round(
                        max(float(outlay.get(key) or 0.0) - float(interest.get(key) or 0.0), 0.0),
                        2,
                    )
                    for key in year_keys
                }
            else:
                construction = from_list([], inv.get("construction"))

            capital = from_list(equity_plan, fund.get("capital")) if equity_plan else from_schedule(
                "capital_own_wan", fund.get("capital"),
            )
            loan = from_list(loan_plan, fund.get("loan")) if loan_plan else from_schedule(
                "loan_wan", fund.get("loan"),
            )
            wc = from_schedule("working_capital_wan")
            if all(abs(float(wc.get(k) or 0.0)) < 1e-9 for k in year_keys):
                wc = from_list(
                    fin_in.get("working_capital_by_year") or raw.get("working_capital_by_year") or [],
                    None,
                )
            if all(abs(float(wc.get(k) or 0.0)) < 1e-9 for k in year_keys):
                wc = {key: 0.0 for key in year_keys}
                if year_keys and inv.get("working_capital") not in (None, ""):
                    wc[year_keys[-1]] = round(float(inv.get("working_capital") or 0.0), 2)
            subsidy = from_schedule("gov_subsidy_wan", fund.get("subsidy"))
            total_uses = {
                key: round(
                    float(construction.get(key) or 0.0)
                    + float(interest.get(key) or 0.0)
                    + float(wc.get(key) or 0.0),
                    2,
                )
                for key in year_keys
            }
            total_sources = {
                key: round(
                    float(capital.get(key) or 0.0)
                    + float(loan.get(key) or 0.0)
                    + float(subsidy.get(key) or 0.0),
                    2,
                )
                for key in year_keys
            }
            balance_gaps = [
                key for key in year_keys
                if abs(float(total_uses.get(key) or 0.0) - float(total_sources.get(key) or 0.0)) > 0.05
            ]
            uses_total = round(sum(total_uses.values()), 2)
            sources_total = round(sum(total_sources.values()), 2)
            expected_years = list(range(1, build_years + 1))
            actual_years = [_year_key(row) for row in schedule if isinstance(row, dict)]
            years_ok = bool(schedule) and actual_years == expected_years
            atomic_complete = bool(schedule) and all(
                isinstance(row, dict)
                and row.get("construction_investment_wan") not in (None, "")
                and row.get("construction_interest_wan") not in (None, "")
                and row.get("working_capital_wan") not in (None, "")
                and row.get("capital_own_wan") not in (None, "")
                and row.get("loan_wan") not in (None, "")
                and row.get("gov_subsidy_wan") not in (None, "")
                for row in schedule
            )
            rows = [
                {"no": "1", "name": "项目总投资使用计划", **total_uses, "amount": uses_total},
                {
                    "no": "1.1", "name": "建设投资", **construction,
                    "amount": round(sum(construction.values()), 2),
                },
                {
                    "no": "1.2", "name": "建设期贷款利息", **interest,
                    "amount": round(sum(interest.values()), 2),
                },
                {
                    "no": "1.3", "name": "流动资金", **wc,
                    "amount": round(sum(wc.values()), 2),
                },
                {"no": "2", "name": "资金筹措", **total_sources, "amount": sources_total},
                {
                    "no": "2.1", "name": "项目资本金（自筹）", **capital,
                    "amount": round(sum(capital.values()), 2),
                },
                {
                    "no": "2.2", "name": "债务资金", **loan,
                    "amount": round(sum(loan.values()), 2),
                },
                {
                    "no": "2.2.1", "name": "银行贷款", **loan,
                    "amount": round(sum(loan.values()), 2),
                },
            ]
            if any(float(subsidy.get(k) or 0.0) for k in year_keys) or fund.get("subsidy"):
                rows.append({
                    "no": "2.3", "name": "政府补助/专项债", **subsidy,
                    "amount": round(sum(subsidy.values()), 2),
                })
            notes = []
            if balance_gaps:
                notes.append("资金用途与来源分年不闭合: " + "、".join(balance_gaps))
            if abs(uses_total - sources_total) > 0.05:
                notes.append(f"资金用途合计 {uses_total} ≠ 来源合计 {sources_total}")
            if not years_ok:
                notes.append(f"资金计划年份必须唯一、连续且覆盖 1..{build_years}: {actual_years}")
            if not atomic_complete:
                notes.append("v1 资金计划缺建设投资/建设期利息/流动资金/资本金/贷款/补助原子字段")
            return _pack_rows(
                rows, cols,
                source="input_revision.funding_annual_schedule+fact_pack.funding_plan",
                notes=notes or None,
                extra={
                    "layout_mode": "item_rows_period_columns",
                    "funding_plan_source": "fact_pack_or_projected_schedule",
                    "funding_balance_ok": (
                        not balance_gaps
                        and abs(uses_total - sources_total) <= 0.05
                        and years_ok
                        and atomic_complete
                    ),
                    "funding_years_ok": years_ok,
                    "atomic_funding_plan_complete": atomic_complete,
                    "uses_total": uses_total,
                    "sources_total": sources_total,
                    "construction_outlay_interest_deducted": not has_atomic_construction,
                },
            )

        project_cf = (fin.get("annual") or {}).get("project_cashflow") or []
        uses = []
        for idx in range(build_years):
            row = project_cf[idx] if idx < len(project_cf) else {}
            uses.append(round(float(row.get("construction") or 0.0), 2))
        uses_total = sum(uses) or float(inv.get("total") or 0.0)

        def spread(amount: Any) -> dict[str, float]:
            value = float(amount or 0.0)
            if uses_total <= 0:
                vals = [round(value / build_years, 2)] * build_years
            else:
                vals = [round(value * u / uses_total, 2) for u in uses]
            if vals:
                vals[-1] = round(value - sum(vals[:-1]), 2)
            return {key: vals[i] for i, key in enumerate(year_keys)}

        rows = [
            {"no": "1", "name": "项目总投资使用计划", **spread(inv.get("total")), "amount": inv.get("total")},
            {"no": "1.1", "name": "建设投资", **spread(inv.get("construction")), "amount": inv.get("construction")},
            {"no": "1.2", "name": "建设期贷款利息", **spread(inv.get("interest")), "amount": inv.get("interest")},
            {"no": "1.3", "name": "流动资金", **spread(inv.get("working_capital")), "amount": inv.get("working_capital")},
            {"no": "2", "name": "资金筹措", **spread(inv.get("total")), "amount": inv.get("total")},
            {"no": "2.1", "name": "项目资本金（自筹）", **spread(fund.get("capital")), "amount": fund.get("capital")},
            {"no": "2.2", "name": "债务资金", **spread(fund.get("loan")), "amount": fund.get("loan")},
            {"no": "2.2.1", "name": "银行贷款", **spread(fund.get("loan")), "amount": fund.get("loan")},
        ]
        if fund.get("subsidy"):
            rows.append({"no": "2.3", "name": "政府补助/专项债", **spread(fund.get("subsidy")), "amount": fund.get("subsidy")})
        return _pack_rows(
            rows, cols, source="investment+funding+annual.project_cashflow",
            extra={
                "layout_mode": "item_rows_period_columns",
                "funding_plan_source": "proportional_spread_fallback",
            },
            notes=["无分年资金事实时按建设支出比例摊分；不得作为 formal 资金计划真源"],
        )

    rows = [
        {"no": "1", "name": "项目资本金（自筹）", "amount": fund.get("capital"), "pct": fund.get("capital_pct")},
        {"no": "2", "name": "银行贷款", "amount": fund.get("loan"), "pct": fund.get("loan_pct")},
    ]
    if fund.get("subsidy"):
        rows.append({
            "no": "3", "name": "政府补助/专项债",
            "amount": fund.get("subsidy"), "pct": fund.get("subsidy_pct"),
        })
    rows.append({"no": "", "name": "合计", "amount": inv.get("total"), "pct": 100.0})
    cols = [("no", "序号"), ("name", "资金来源"), ("amount", "金额（万元）"), ("pct", "占比（%）")]
    return _pack_rows(rows, cols, source="funding")


def _build_wc(fin: dict) -> dict[str, Any]:
    wc = (fin.get("annual") or {}).get("working_capital") or {}
    if not isinstance(wc, dict) or not wc:
        return _pack_rows([], [("item", "构成项"), ("amount", "金额（万元）")], source="annual.working_capital")
    if wc.get("method") == "ratio_backsolve":
        rows = [{"item": "流动资金总额（汇总输入）", "amount": wc.get("total")}]
        return _pack_rows(
            rows, [("item", "构成项"), ("amount", "金额（万元）")],
            source="annual.working_capital",
            notes=["method=ratio_backsolve", "未提供周转天数，不展示反解伪分项；不得标分项法完成"],
            extra={
                "method": "ratio_backsolve", "effective": False,
                "layout_mode": "item_rows_single_amount",
            },
        )
    days = wc.get("days") or {}
    bases = wc.get("bases") or {}
    fin_in = _effective_input_revision(fin)
    if not isinstance(fin_in, dict):
        fin_in = {}
    raw = fin.get("raw") or {}
    turnover = (
        fin_in.get("wc_turnover")
        or fin_in.get("wc_turnover_days")
        or raw.get("wc_turnover")
        or raw.get("wc_turnover_days")
        or {}
    )
    fact_turnover = (_confirmed_fact_domains(fin).get("wc_turnover") or {})
    if isinstance(fact_turnover, dict):
        turnover = {
            **(turnover if isinstance(turnover, dict) else {}),
            **fact_turnover,
        }
    inv_detail: dict = {}
    if isinstance(turnover, dict):
        inv_detail = dict(turnover.get("inventory_detail") or {})
        if not inv_detail:
            inv_detail = {
                k: turnover[k]
                for k in ("raw", "fuel", "wip", "finished", "原材料", "燃料", "在产品", "产成品")
                if k in turnover
            }
    if isinstance(wc.get("inventory_detail"), dict):
        inv_detail = {**inv_detail, **(wc.get("inventory_detail") or {})}

    def _day(key: str):
        if days.get(key) is not None:
            return days.get(key)
        if isinstance(turnover, dict):
            return turnover.get(key)
        return None

    rows = [
        {"no": "1", "item": "流动资产", "base": None, "days": None, "turnover": None, "amount": wc.get("current_assets")},
        {"no": "1.1", "item": "应收账款", "base": bases.get("revenue"), "days": _day("receivable"), "turnover": round(360 / float(_day("receivable")), 2) if _day("receivable") else None, "amount": wc.get("receivable")},
        {"no": "1.2", "item": "存货", "base": bases.get("cash_cost"), "days": _day("inventory"), "turnover": round(360 / float(_day("inventory")), 2) if _day("inventory") else None, "amount": wc.get("inventory")},
    ]
    label_map = {
        "raw": "原材料", "原材料": "原材料",
        "fuel": "燃料及动力", "燃料": "燃料及动力", "燃料及动力": "燃料及动力",
        "wip": "在产品", "在产品": "在产品",
        "finished": "产成品", "产成品": "产成品",
    }
    inventory_components: list[dict[str, Any]] = []
    component_amounts = wc.get("inventory_component_amounts") or {}
    canonical_order = ("raw", "fuel", "wip", "finished")
    for key in canonical_order:
        raw_component = inv_detail.get(key)
        if not isinstance(raw_component, dict):
            # Legacy numeric day-only values are retained as estimate rows with no
            # guessed base; they cannot satisfy the v1 reference contract.
            raw_component = {"days": raw_component}
        component_base = (
            raw_component.get("annual_base_wan")
            if raw_component.get("annual_base_wan") is not None
            else raw_component.get("base_wan")
        )
        days_v = raw_component.get("days")
        amount = component_amounts.get(key)
        if amount is None and _number(component_base) and _number(days_v):
            amount = round(float(component_base) * float(days_v) / 360.0, 2)
        inventory_components.append({
            "item": label_map.get(key, key),
            "base": float(component_base) if _number(component_base) else None,
            "base_source": raw_component.get("base_source"),
            "days": float(days_v) if _number(days_v) else None,
            "turnover": round(360 / float(days_v), 2) if _number(days_v) and float(days_v) > 0 else None,
            "amount": amount,
        })
    # Never scale inventory components to a separate aggregate inventory total.
    # 四分项 must remain base×days÷360; aggregate inventory is derived only from
    # the four explicit component rows.
    component_total = sum(float(item.get("amount") or 0.0) for item in inventory_components)
    for index, component in enumerate(inventory_components, start=1):
        amount = component.get("amount")
        rows.append({
            "no": f"1.2.{index}",
            **component,
            "amount": round(float(amount), 2) if amount is not None else None,
        })
    # Keep engine inventory total when present; only fill from components if empty.
    if inventory_components and component_total > 0:
        for row in rows:
            if row.get("no") == "1.2" and row.get("amount") in (None, "", 0, 0.0):
                row["amount"] = round(component_total, 2)
                break
    total_wc = wc.get("total") or wc.get("net_working_capital")
    short_loan = turnover.get("short_term_loan_wan") if isinstance(turnover, dict) else None
    self_funded = turnover.get("self_funded_wan") if isinstance(turnover, dict) else None
    investment_total = wc.get("investment_total")
    rows.extend([
        {"no": "1.3", "item": "现金", "base": bases.get("cash_cost"), "days": _day("cash"), "turnover": round(360 / float(_day("cash")), 2) if _day("cash") else None, "amount": wc.get("cash")},
        {"no": "1.9", "item": "流动资产小计", "base": None, "days": None, "turnover": None, "amount": wc.get("current_assets")},
        {"no": "2", "item": "流动负债", "base": None, "days": None, "turnover": None, "amount": wc.get("current_liabilities")},
        {"no": "2.1", "item": "应付账款", "base": bases.get("cash_cost"), "days": _day("payable"), "turnover": round(360 / float(_day("payable")), 2) if _day("payable") else None, "amount": wc.get("payable") or wc.get("current_liabilities")},
        {"no": "3", "item": "流动资金", "base": None, "days": None, "turnover": None, "amount": total_wc},
        {"no": "4", "item": "铺底流动资金", "base": None, "days": None, "turnover": None, "amount": total_wc},
        {"no": "5", "item": "当期增加额", "base": None, "days": None, "turnover": None, "amount": total_wc},
        {"no": "6", "item": "流动资金来源", "base": None, "days": None, "turnover": None, "amount": total_wc if investment_total is None else investment_total},
        {"no": "6.1", "item": "短期借款", "base": None, "days": None, "turnover": None, "amount": short_loan},
        {"no": "6.2", "item": "企业自筹流动资金", "base": None, "days": None, "turnover": None, "amount": self_funded},
    ])
    return _pack_rows(
        rows, [
            ("no", "序号"), ("item", "项目"), ("base", "计算基数（万元）"),
            ("days", "最低周转天数"), ("turnover", "周转次数"),
            ("amount", "分年达产年"),
        ],
        source="annual.working_capital",
        notes=[f"method={wc.get('method')}", "ratio_backsolve 时不得宣称完整分项法"],
        extra={
            "method": wc.get("method"), "inventory_detail": inv_detail or None,
            "inventory_component_scale": None,
            "inventory_components_unscaled": True,
            "inventory_components_complete": bool(wc.get("inventory_components_complete")),
            "inventory_component_total": round(component_total, 2),
            "investment_total": investment_total,
            "layout_mode": "item_rows_period_columns",
        },
    )


def _pack_rows(
    rows: list[dict],
    columns: list[tuple[str, str]],
    *,
    source: str = "",
    notes: Optional[list] = None,
    extra: Optional[dict] = None,
    footer: str = "",
) -> dict[str, Any]:
    keys = [c[0] for c in columns]
    labels = [c[1] for c in columns]
    body = []
    for r in rows:
        body.append([_get_field(r, k) for k in keys])
    out = {
        "columns": [{"key": k, "label": lab} for k, lab in columns],
        "column_labels": labels,
        "rows": body,
        "row_count": len(body),
        "source": source,
        "layout_mode": "annual_record_rows",
        "grade": "summary",
        # 禁止默认 True：reference_structure 仅由 reference_schema.assess_structure_coverage 置位。
        "reference_structure": False,
        "notes": notes or [],
        "footer": footer,
    }
    if extra:
        out.update(extra)
    return out


def _period_value(record: dict[str, Any], index: int) -> Any:
    value = record.get("year")
    if value is None:
        value = record.get("period")
    return value if value is not None else index + 1


def _series(records: list[dict[str, Any]], field: str) -> list[Any]:
    return [record.get(field) for record in records]


def _sum_values(values: list[Any]) -> Optional[float]:
    numeric = [_number(value) for value in values if value not in (None, "")]
    clean = [value for value in numeric if value is not None]
    return round(sum(clean), 2) if clean else None


def _last_value(values: list[Any]) -> Any:
    for value in reversed(values):
        if value not in (None, ""):
            return value
    return None


def _reference_matrix(
    engine_body: dict[str, Any],
    records: list[dict[str, Any]],
    row_defs: list[dict[str, Any]],
    *,
    static_columns: list[tuple[str, str]],
    period_prefix: str,
    source: str,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Promote record-oriented engine rows to the client row-tree layout.

    ``engine_*`` preserves the deterministic calculation matrix for Excel
    lineage/formulas.  ``rows`` is the actual delivery view: item rows with
    period columns, not a metadata-only row tree.
    """
    period_columns = [
        (f"period_{index + 1}", f"{period_prefix}{_period_value(record, index)}")
        for index, record in enumerate(records)
    ]
    columns = static_columns + period_columns
    packed_rows: list[dict[str, Any]] = []
    row_fields: list[str] = []
    total_modes: list[str] = []
    for definition in row_defs:
        values = list(definition.get("values") or [])
        if len(values) < len(records):
            values.extend([None] * (len(records) - len(values)))
        values = values[:len(records)]
        row = {
            key: definition.get(key)
            for key, _label in static_columns
        }
        total_mode = str(definition.get("total_mode") or "sum")
        if "total" in row and row.get("total") is None:
            row["total"] = _last_value(values) if total_mode == "last" else _sum_values(values)
        for index, value in enumerate(values, start=1):
            row[f"period_{index}"] = value
        packed_rows.append(row)
        row_fields.append(str(definition.get("engine_field") or ""))
        total_modes.append(total_mode)

    promoted = _pack_rows(
        packed_rows,
        columns,
        source=source,
        notes=list(engine_body.get("notes") or []),
        footer=str(engine_body.get("footer") or ""),
        extra={
            "layout_mode": "item_rows_period_columns",
            "engine_columns": list(engine_body.get("columns") or []),
            "engine_column_labels": list(engine_body.get("column_labels") or []),
            "engine_rows": list(engine_body.get("rows") or []),
            "engine_row_count": int(engine_body.get("row_count") or 0),
            "reference_row_fields": row_fields,
            "reference_total_modes": total_modes,
            **(extra or {}),
        },
    )
    for key in (
        "product_tree", "cost_item_tree", "staff_detail", "asset_classes",
        "repay_sources", "repay_source_provenance", "repay_sources_confirmed",
        "derived_policy_fields",
    ):
        if key in engine_body:
            promoted[key] = engine_body[key]
    return promoted


def _canonical_cost_label(name: str) -> str:
    text = str(name or "").strip()
    if "原材料" in text:
        return "外购原材料费"
    if "燃料" in text or "动力" in text:
        return "外购燃料及动力费"
    if "工资" in text or "福利" in text:
        return "工资及福利费"
    if "修理" in text or "维修" in text:
        return "修理费"
    return text


def _promote_reference_period_table(
    key: str,
    engine_body: dict[str, Any],
    records: list[dict[str, Any]],
    fin: dict[str, Any],
) -> dict[str, Any]:
    if not records:
        return engine_body
    fin_in = _effective_input_revision(fin)
    if not isinstance(fin_in, dict):
        fin_in = {}
    raw = fin.get("raw") or {}

    if key == "interest-during-construction":
        defs = [
            {"no": "1", "item": "期初借款余额", "values": _series(records, "begin_balance"), "total_mode": "last", "engine_field": "begin_balance"},
            {"no": "2", "item": "当期借款", "values": _series(records, "draw"), "engine_field": "draw"},
            {"no": "3", "item": "年利率", "total": _fmt_rate_pct(_last_value(_series(records, "rate"))), "values": [_fmt_rate_pct(value) for value in _series(records, "rate")]},
            {"no": "4", "item": "当期应付利息", "values": _series(records, "interest"), "engine_field": "interest"},
            {"no": "5", "item": "期末借款余额", "values": _series(records, "end_balance"), "total_mode": "last", "engine_field": "end_balance"},
            {"no": "6", "item": "其他融资费用", "values": [0.0] * len(records)},
            {"no": "", "item": "小计", "values": _series(records, "interest"), "engine_field": "interest"},
        ]
        return _reference_matrix(
            engine_body, records, defs,
            static_columns=[("no", "序号"), ("item", "项目"), ("total", "合计")],
            period_prefix="分年", source="annual.interest_during_construction",
        )

    if key == "income-statement":
        products = list(engine_body.get("product_tree") or [])
        defs: list[dict[str, Any]] = [{"no": "1", "item": "营业收入", "unit": "万元", "values": _series(records, "revenue"), "engine_field": "revenue"}]
        for index, product in enumerate(products, start=1):
            ramp = list(product.get("ramp") or [])
            if ramp:
                ramp.extend([ramp[-1]] * max(len(records) - len(ramp), 0))
            else:
                ramp = [1.0] * len(records)
            capacity = float(product.get("capacity") or 0.0)
            price = float(product.get("price_per_unit") or 0.0)
            quantities = [round(capacity * float(ramp[i]), 6) for i in range(len(records))]
            unit_scale = 10000.0 if str(product.get("unit") or "").startswith("万") else 1.0
            price_divisor = 1.0 if product.get("price_unit") == "wan" else 10000.0
            revenues = [round(price * quantity * unit_scale / price_divisor, 2) for quantity in quantities]
            prefix = f"1.{index}"
            defs.extend([
                {"no": prefix, "item": str(product.get("name") or f"产品{index}"), "unit": str(product.get("unit") or ""), "values": revenues},
                {"no": prefix + ".1", "item": "单价", "unit": str(product.get("price_unit") or "元"), "values": [price] * len(records), "total_mode": "last"},
                {"no": prefix + ".2", "item": "数量", "unit": str(product.get("unit") or ""), "values": quantities},
            ])
        surtax = _series(records, "tax_surtax")
        # Engine default 12% surtax_on_vat is an estimate, not a confirmed project policy.
        policy_confirmed = bool(
            raw.get("tax_component_policy_confirmed")
            or fin_in.get("tax_component_policy_confirmed")
            or (
                isinstance((fin.get("spec") or {}), dict)
                and ((fin.get("spec") or {}).get("tax") or {}).get("component_policy_confirmed")
            )
        )
        component_policy = raw.get("surtax_component_policy") or {}
        statutory_split = (
            bool(raw.get("surtax_on_vat"))
            and component_policy.get("mode") == "statutory_components"
            and component_policy.get("urban_maintenance_rate") in {0.01, 0.05, 0.07}
        )
        city = _series(records, "urban_maintenance_tax") if statutory_split else [None] * len(surtax)
        education = _series(records, "education_surcharge") if statutory_split else [None] * len(surtax)
        local = _series(records, "local_education_surcharge") if statutory_split else [None] * len(surtax)
        defs.extend([
            {"no": "1.9", "item": "营业收入合计", "unit": "万元", "values": _series(records, "revenue"), "engine_field": "revenue"},
            {"no": "2", "item": "销项税额", "unit": "万元", "values": _series(records, "vat_output"), "engine_field": "vat_output"},
            {"no": "3", "item": "营业税金与附加", "unit": "万元", "values": surtax, "engine_field": "tax_surtax"},
            {"no": "3.1", "item": "城市维护建设税", "unit": "万元", "values": city},
            {"no": "3.2", "item": "教育费附加", "unit": "万元", "values": education},
            {"no": "3.3", "item": "地方教育附加", "unit": "万元", "values": local},
            {"no": "4", "item": "进项税额", "unit": "万元", "values": _series(records, "vat_input"), "engine_field": "vat_input"},
            {"no": "5", "item": "应纳增值税", "unit": "万元", "values": _series(records, "vat_payable"), "engine_field": "vat_payable"},
        ])
        return _reference_matrix(
            engine_body, records, defs,
            static_columns=[("no", "序号"), ("item", "项目"), ("unit", "单位"), ("total", "合计")],
            period_prefix="分年", source="annual.income_statement+spec.revenue.products",
            extra={
                "tax_component_policy_confirmed": bool(policy_confirmed and statutory_split),
                "tax_component_split_is_estimate": bool(statutory_split and not policy_confirmed),
                "surtax_component_policy": component_policy,
            },
        )

    if key == "total-cost":
        cost_items = fin_in.get("cost_items") or raw.get("cost_items") or {}
        peak = sum(float(value or 0.0) for value in cost_items.values()) if isinstance(cost_items, dict) else 0.0
        op_cost = [float(value or 0.0) for value in _series(records, "operating_cost")]
        defs = [{"no": "1", "item": "生产负荷（%）", "values": [round(value / max(op_cost or [1.0]) * 100, 2) if max(op_cost or [0.0]) else 0.0 for value in op_cost], "total_mode": "last"}]
        variable_series = [0.0] * len(records)
        fixed_series = [0.0] * len(records)
        # Only use explicit project cost_behavior map when present. Name-based
        # guessing is estimate-only and must not be marked as confirmed policy.
        behavior = (
            fin_in.get("cost_behavior")
            or raw.get("cost_behavior")
            or (fin.get("spec") or {}).get("cost_behavior")
            or {}
        )
        if not isinstance(behavior, dict):
            behavior = {}
        policy_confirmed = bool(
            fin_in.get("cost_behavior_confirmed")
            or raw.get("cost_behavior_confirmed")
            or behavior.get("confirmed")
        )
        for index, (name, amount) in enumerate((cost_items or {}).items(), start=2):
            share = float(amount or 0.0) / peak if peak else 0.0
            values = [round(value * share, 2) for value in op_cost]
            canonical = _canonical_cost_label(str(name))
            defs.append({"no": str(index), "item": canonical, "values": values})
            explicit = str(
                behavior.get(str(name))
                or behavior.get(canonical)
                or ""
            ).lower()
            if explicit in {"variable", "var", "变动", "变动成本"}:
                target = variable_series
            elif explicit in {"fixed", "fix", "固定", "固定成本"}:
                target = fixed_series
            elif policy_confirmed:
                target = fixed_series
            else:
                # Estimate heuristic for display only.
                target = variable_series if any(
                    token in canonical for token in ("原材料", "燃料", "动力")
                ) else fixed_series
            for year, value in enumerate(values):
                target[year] = round(target[year] + float(value or 0.0), 2)
        defs.extend([
            {"no": "8", "item": "经营成本", "values": op_cost, "engine_field": "operating_cost"},
            {"no": "9", "item": "折旧费", "values": _series(records, "depreciation"), "engine_field": "depreciation"},
            {"no": "10", "item": "摊销费", "values": _series(records, "amortization"), "engine_field": "amortization"},
            {"no": "11", "item": "财务费用", "values": _series(records, "interest"), "engine_field": "interest"},
            {"no": "11.1", "item": "长期贷款利息", "values": _series(records, "interest"), "engine_field": "interest"},
            {"no": "12", "item": "总成本费用", "values": _series(records, "total_cost"), "engine_field": "total_cost"},
            {"no": "12.1", "item": "固定成本", "values": fixed_series},
            {"no": "12.2", "item": "变动成本", "values": variable_series},
        ])
        return _reference_matrix(
            engine_body, records, defs,
            static_columns=[("no", "序号"), ("item", "项目"), ("total", "合计")],
            period_prefix="计算期", source="annual.total_cost+finance_inputs.cost_items",
            extra={
                "cost_split_method": (
                    "project_cost_behavior"
                    if policy_confirmed else
                    "name_heuristic_estimate"
                ),
                "cost_behavior_confirmed": policy_confirmed,
            },
        )

    if key == "wage":
        domains = _confirmed_fact_domains(fin)
        staff = list(
            engine_body.get("staff_detail")
            or fin_in.get("staff_detail")
            or fin_in.get("wage_detail")
            or fin_in.get("labor_plan")
            or raw.get("staff_detail")
            or domains.get("staff_detail")
            or []
        )
        total_wage = [float(value or 0.0) for value in _series(records, "wage")]
        total_staff_wage = sum(
            float(row.get("headcount") or 0.0) * float(row.get("avg_wage_yuan") or 0.0) / 10000.0
            for row in staff if isinstance(row, dict)
        )
        total_headcount = sum(
            float(row.get("headcount") or 0.0)
            for row in staff if isinstance(row, dict)
        )
        defs = [{
            "no": "1",
            "item": "劳动定员",
            "unit": "人",
            "headcount": total_headcount,
            "values": [total_headcount] * len(records),
            "total_mode": "last",
        }]
        for index, row in enumerate(staff, start=1):
            if not isinstance(row, dict):
                continue
            wage_amount = float(row.get("headcount") or 0.0) * float(row.get("avg_wage_yuan") or 0.0) / 10000.0
            share = wage_amount / total_staff_wage if total_staff_wage else 0.0
            defs.append({
                "no": f"1.{index}", "item": str(row.get("category") or row.get("name") or f"人员{index}"),
                "unit": "万元", "values": [round(value * share, 2) for value in total_wage],
                "headcount": row.get("headcount"), "average_wage": row.get("avg_wage_yuan"),
            })
        defs.extend([
            {"no": "2", "item": "工资额", "unit": "万元", "values": total_wage, "engine_field": "wage"},
            {"no": "3", "item": "福利费", "unit": "万元", "values": _series(records, "welfare"), "engine_field": "welfare"},
            {"no": "", "item": "合计", "unit": "万元", "values": _series(records, "total"), "engine_field": "total"},
        ])
        return _reference_matrix(
            engine_body, records, defs,
            static_columns=[("no", "序号"), ("item", "项目"), ("unit", "单位"), ("headcount", "人数"), ("average_wage", "人均年工资"), ("total", "合计")],
            period_prefix="分年", source="annual.wage+finance_fact_pack.staff_detail",
        )

    if key == "depreciation":
        classes = list(engine_body.get("asset_classes") or [])
        if not classes and records and isinstance(records[0].get("classes"), list):
            classes = list(records[0].get("classes") or [])
        if not classes:
            domains = _confirmed_fact_domains(fin)
            classes = list(
                fin_in.get("depreciation_classes")
                or fin_in.get("asset_classes")
                or raw.get("depreciation_classes")
                or raw.get("asset_classes")
                or domains.get("asset_classes")
                or []
            )
        defs: list[dict[str, Any]] = []
        for index, asset in enumerate(classes, start=1):
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or asset.get("label") or f"资产类别{index}")
            original = float(asset.get("original_value_wan") or asset.get("original_wan") or asset.get("original_value") or 0.0)
            years = int(float(asset.get("depreciation_years") or asset.get("dep_years") or asset.get("years") or 0))
            salvage = float(asset.get("salvage_rate") or 0.0)
            annual_dep = round(original * (1.0 - salvage) / years, 2) if years > 0 else 0.0
            dep_values = [annual_dep if year < years else 0.0 for year in range(len(records))]
            net_values: list[float] = []
            cumulative = 0.0
            for value in dep_values:
                cumulative = min(round(cumulative + value, 2), round(original * (1.0 - salvage), 2))
                net_values.append(round(original - cumulative, 2))
            defs.extend([
                {"no": str(index), "item": name, "total": original, "life": years, "values": [None] * len(records), "total_mode": "last"},
                {"no": f"{index}.1", "item": "原值", "total": original, "life": years, "values": [original] * len(records), "total_mode": "last"},
                {"no": f"{index}.2", "item": "当期折旧费", "life": years, "values": dep_values},
                {"no": f"{index}.3", "item": "净值", "life": years, "values": net_values, "total_mode": "last"},
            ])
        defs.append({"no": "", "item": "合计", "values": _series(records, "depreciation"), "engine_field": "depreciation"})
        return _reference_matrix(
            engine_body, records, defs,
            static_columns=[("no", "序号"), ("item", "项目"), ("total", "合计"), ("life", "折旧年限")],
            period_prefix="分年", source="annual.depreciation_table+finance_fact_pack.asset_classes",
        )

    if key == "amortization":
        domains = _confirmed_fact_domains(fin)
        bases = list(domains.get("amort_bases") or fin_in.get("amort_bases") or raw.get("amort_bases") or [])
        defs: list[dict[str, Any]] = []
        for index, asset in enumerate(bases, start=1):
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or f"摊销基础{index}")
            original = float(asset.get("original_wan") or asset.get("original_value_wan") or 0.0)
            years = int(float(asset.get("amort_years") or asset.get("amortization_years") or 0))
            annual_amort = round(original / years, 2) if years > 0 else 0.0
            values = [annual_amort if year < years else 0.0 for year in range(len(records))]
            net_values: list[float] = []
            cumulative = 0.0
            for value in values:
                cumulative = min(round(cumulative + value, 2), original)
                net_values.append(round(original - cumulative, 2))
            defs.extend([
                {"no": str(index), "item": name, "total": original, "life": years, "values": [None] * len(records), "total_mode": "last"},
                {"no": f"{index}.1", "item": "原值", "total": original, "life": years, "values": [original] * len(records), "total_mode": "last"},
                {"no": f"{index}.2", "item": "当期摊销费", "life": years, "values": values},
                {"no": f"{index}.3", "item": "净值", "life": years, "values": net_values, "total_mode": "last"},
            ])
        defs.append({"no": "", "item": "合计", "values": _series(records, "amortization"), "engine_field": "amortization"})
        return _reference_matrix(
            engine_body, records, defs,
            static_columns=[("no", "序号"), ("item", "项目"), ("total", "合计"), ("life", "摊销年限")],
            period_prefix="分年", source="annual.amortization_table+finance_fact_pack.amort_bases",
        )

    if key == "profit-distribution":
        row_fields = [
            ("1", "营业收入", "revenue"), ("2", "营业税金及附加", "tax_surtax"),
            ("3", "总成本费用", "total_cost"), ("4", "利润总额", "total_profit"),
            ("5", "弥补以前年度亏损", "loss_offset"), ("6", "应纳税所得额", "taxable_income"),
            ("7", "所得税", "income_tax"), ("8", "净利润", "net_profit"),
            ("9", "期初未分配利润", "begin_undistributed"),
            ("10", "可供分配的利润", "available_distribution"),
            ("11", "提取法定盈余公积金", "surplus_reserve"),
            ("12", "可供投资者分配的利润", "distributable"),
            ("13", "提取任意盈余公积金", "arbitrary_reserve"),
            ("14", "投资各方利润分配", "investor_distribution"),
            ("15", "未分配利润", "undistributed"),
            ("16", "息税前利润（EBIT）", "ebit"),
            ("17", "息税折旧摊销前利润（EBITDA）", "ebitda"),
        ]
        defs = [{"no": no, "item": label, "values": _series(records, field), "engine_field": field} for no, label, field in row_fields]
        return _reference_matrix(
            engine_body, records, defs,
            static_columns=[("no", "序号"), ("item", "项目"), ("total", "合计")],
            period_prefix="计算期", source="annual.profit_distribution",
        )

    if key == "debt-service":
        # Distinguish available repay funds (5.x) from funds actually used (3).
        debt_service = _series(records, "debt_service")
        profit_src = _series(records, "repay_source_profit")
        dep_src = _series(records, "repay_source_dep")
        amort_src = _series(records, "repay_source_amort")

        def _n(v: Any) -> float:
            try:
                return float(v or 0.0)
            except (TypeError, ValueError):
                return 0.0

        available = [
            _n(records[i].get("repay_available"))
            if isinstance(records[i], dict) and records[i].get("repay_available") is not None
            else round(_n(profit_src[i]) + _n(dep_src[i]) + _n(amort_src[i]), 2)
            for i in range(len(records))
        ]
        actual = _series(records, "repay_actual")
        actual_profit = _series(records, "repay_actual_profit")
        actual_dep = _series(records, "repay_actual_dep")
        actual_amort = _series(records, "repay_actual_amort")
        surplus = [
            round(available[i] - _n(actual[i]) if i < len(actual) else available[i], 2)
            for i in range(len(records))
        ]
        row_fields = [
            ("1", "期初借款余额", _series(records, "begin"), "begin", "last"),
            ("2", "本年借款", _series(records, "draw"), "draw", "sum"),
            ("3", "当期还本付息", debt_service, "debt_service", "sum"),
            ("3.1", "还本", _series(records, "principal"), "principal", "sum"),
            ("3.2", "付息", _series(records, "interest"), "interest", "sum"),
            ("4", "期末借款余额", _series(records, "end"), "end", "last"),
            ("5", "偿债资金来源（可用）", available, "repay_available", "sum"),
            ("5.1", "可供投资者分配的利润", profit_src, "repay_source_profit", "sum"),
            ("5.2", "折旧费", dep_src, "repay_source_dep", "sum"),
            ("5.3", "摊销费", amort_src, "repay_source_amort", "sum"),
            ("6", "实际用于偿债的资金", actual, "repay_actual", "sum"),
            ("6.1", "实际使用利润", actual_profit, "repay_actual_profit", "sum"),
            ("6.2", "实际使用折旧", actual_dep, "repay_actual_dep", "sum"),
            ("6.3", "实际使用摊销", actual_amort, "repay_actual_amort", "sum"),
            ("7", "偿债后剩余资金", surplus, "repay_surplus", "sum"),
            ("8", "利息备付率（ICR）", _series(records, "icr"), "icr", "last"),
            ("9", "偿债备付率（DSCR）", _series(records, "dscr"), "dscr", "last"),
        ]
        defs = [
            {"no": no, "item": label, "values": values, "engine_field": field, "total_mode": mode}
            for no, label, values, field, mode in row_fields
        ]
        return _reference_matrix(
            engine_body, records, defs,
            static_columns=[("no", "序号"), ("item", "项目"), ("total", "合计")],
            period_prefix="分年", source="annual.debt_service+finance_fact_pack.debt_schedule",
            extra={
                "repay_source_semantics": "available_funds",
                "parent_child_identity": "5=5.1+5.2+5.3; 6=6.1+6.2+6.3=3.1; 7=5-6",
            },
        )

    if key == "cashflow":
        raw = fin.get("raw") if isinstance(fin.get("raw"), dict) else {}
        terminal_fixed = raw.get("terminal_recovery")
        terminal_wc = raw.get("terminal_wc_recovery")
        fixed_recover = []
        wc_recover = []
        pre_tax = []
        post_tax = []
        for row in records:
            if not isinstance(row, dict):
                fixed_recover.append(0.0)
                wc_recover.append(0.0)
                pre_tax.append(None)
                post_tax.append(None)
                continue
            recover_total = float(row.get("recover") or 0.0)
            # Split terminal recover into fixed-asset residual vs working capital.
            if recover_total and terminal_fixed is not None and terminal_wc is not None:
                fixed_part = round(float(terminal_fixed or 0.0), 2)
                wc_part = round(float(terminal_wc or 0.0), 2)
            elif recover_total and terminal_fixed is not None:
                fixed_part = round(float(terminal_fixed or 0.0), 2)
                wc_part = round(max(recover_total - fixed_part, 0.0), 2)
            elif recover_total and terminal_wc is not None:
                wc_part = round(float(terminal_wc or 0.0), 2)
                fixed_part = round(max(recover_total - wc_part, 0.0), 2)
            else:
                # No split available: put all recover on fixed residual only once.
                fixed_part = recover_total
                wc_part = 0.0
            fixed_recover.append(fixed_part)
            wc_recover.append(wc_part)
            rev = float(row.get("revenue") or 0.0)
            occ = float(row.get("op_cash_cost") or 0.0)
            surtax = float(row.get("tax_surtax") or 0.0)
            tax = float(row.get("income_tax") or 0.0)
            construction = float(row.get("construction") or 0.0)
            wc_change = float(row.get("wc_change") or 0.0)
            # 所得税前净现金流 = 流入 - 流出(不含所得税)
            inflow = rev + fixed_part + wc_part
            outflow_pre_tax = construction + wc_change + occ + surtax
            pre_tax.append(round(inflow - outflow_pre_tax, 2))
            post_tax.append(round(inflow - outflow_pre_tax - tax, 2))
        row_fields = [
            ("1", "现金流入", None), ("1.1", "营业收入", "revenue"),
            ("1.2", "回收固定资产余值", None), ("1.3", "回收新增流动资金", None),
            ("2", "现金流出", None), ("2.1", "固定资产投资", "construction"),
            ("2.2", "新增铺底流动资金", "wc_change"), ("2.3", "经营成本", "op_cash_cost"),
            ("2.4", "营业税金及附加", "tax_surtax"), ("2.5", "所得税", "income_tax"),
            ("3", "所得税前净现金流量", None),
            ("4", "所得税后净现金流量", None),
            ("5", "累计所得税后净现金流量", "cumulative"),
        ]
        defs = []
        for no, label, field in row_fields:
            if label == "回收固定资产余值":
                values = fixed_recover
                engine_field = "recover_fixed"
            elif label == "回收新增流动资金":
                values = wc_recover
                engine_field = "recover_wc"
            elif label == "所得税前净现金流量":
                values = pre_tax
                engine_field = "net_cashflow_before_tax"
            elif label == "所得税后净现金流量":
                values = post_tax
                engine_field = "net_cashflow"
            else:
                values = _series(records, field) if field else [None] * len(records)
                engine_field = field or ""
            defs.append({
                "no": no,
                "item": label,
                "values": values,
                "engine_field": engine_field,
                "total_mode": "last" if field == "cumulative" else "sum",
            })
        return _reference_matrix(
            engine_body, records, defs,
            static_columns=[("no", "序号"), ("item", "项目"), ("total", "合计")],
            period_prefix="计算期", source="annual.project_cashflow",
        )

    if key == "capital-cashflow":
        # Atomic composition only. Never use net-style op_inflow as cash inflow —
        # engine op_inflow already embeds terminal recover, so adding recover again
        # double-counts 期末回收.
        revenue: list[float] = []
        op_cash_cost: list[float] = []
        tax_surtax: list[float] = []
        income_tax: list[float] = []
        fixed_recover: list[float] = []
        wc_recover: list[float] = []
        atomic_fields = {
            "revenue", "recover_fixed", "recover_wc", "op_cash_cost",
            "tax_surtax", "income_tax", "capital_invest", "principal",
            "interest", "net_cashflow",
        }
        atomic_complete = True
        for row in records:
            if not isinstance(row, dict):
                atomic_complete = False
                revenue.append(0.0)
                op_cash_cost.append(0.0)
                tax_surtax.append(0.0)
                income_tax.append(0.0)
                fixed_recover.append(0.0)
                wc_recover.append(0.0)
                continue
            if not atomic_fields.issubset(row):
                atomic_complete = False
            revenue.append(round(float(row.get("revenue") or 0.0), 2))
            op_cash_cost.append(round(float(row.get("op_cash_cost") or 0.0), 2))
            tax_surtax.append(round(float(row.get("tax_surtax") or 0.0), 2))
            income_tax.append(round(float(row.get("income_tax") or 0.0), 2))
            fixed_recover.append(round(float(row.get("recover_fixed") or 0.0), 2))
            wc_recover.append(round(float(row.get("recover_wc") or 0.0), 2))

        capital_invest = [
            float(row.get("capital_invest") or 0.0) if isinstance(row, dict) else 0.0
            for row in records
        ]
        principal = [
            float(row.get("principal") or 0.0) if isinstance(row, dict) else 0.0
            for row in records
        ]
        interest = [
            float(row.get("interest") or 0.0) if isinstance(row, dict) else 0.0
            for row in records
        ]
        cash_inflow = [
            round(revenue[i] + fixed_recover[i] + wc_recover[i], 2)
            for i in range(len(records))
        ]
        cash_outflow = [
            round(
                capital_invest[i] + principal[i] + interest[i]
                + op_cash_cost[i] + tax_surtax[i] + income_tax[i],
                2,
            )
            for i in range(len(records))
        ]
        # Net cashflow is recomputed from composition, not opaque engine net.
        net = [round(cash_inflow[i] - cash_outflow[i], 2) for i in range(len(records))]
        cumulative: list[float] = []
        running = 0.0
        for value in net:
            running = round(running + float(value or 0.0), 2)
            cumulative.append(running)
        row_fields = [
            ("1", "现金流入", cash_inflow, "cash_inflow"),
            ("1.1", "营业收入", revenue, "revenue"),
            ("1.2", "回收固定资产余值", fixed_recover, "recover_fixed"),
            ("1.3", "回收流动资金", wc_recover, "recover_wc"),
            ("2", "现金流出", cash_outflow, "cash_outflow"),
            ("2.1", "项目资本金", capital_invest, "capital_invest"),
            ("2.2", "借款本金偿还", principal, "principal"),
            ("2.3", "借款利息支付", interest, "interest"),
            ("2.4", "经营成本", op_cash_cost, "op_cash_cost"),
            ("2.5", "税金及附加", tax_surtax, "tax_surtax"),
            ("2.6", "所得税", income_tax, "income_tax"),
            ("3", "资本金净现金流量", net, "net_cashflow"),
            ("4", "累计净现金流量", cumulative, "cumulative"),
        ]
        defs = [
            {
                "no": no,
                "item": label,
                "values": values,
                "engine_field": field,
                "total_mode": "last" if "累计" in label else "sum",
            }
            for no, label, values, field in row_fields
        ]
        return _reference_matrix(
            engine_body, records, defs,
            static_columns=[("no", "序号"), ("item", "项目"), ("total", "合计")],
            period_prefix="计算期",
            source="annual.capital_cashflow.atomic+annual.project_cashflow",
            extra={
                "composition_identity": "inflow - outflow = net_cashflow",
                "op_inflow_not_used_as_cash_inflow": True,
                "atomic_capital_cashflow_complete": atomic_complete,
            },
        )

    return engine_body


def _assess_missing_fields(fin: dict[str, Any]) -> dict[str, list[str]]:
    """对照参考表 schema 的关键输入缺口（不造数；仅声明 missing）。

    baseline 覆盖附表1/3/5 等既有门禁；extended 由 reference_schema 追加
    人员/资产类别/存货树等 reference 深度项。
    """
    fin_in = _effective_input_revision(fin)
    if not isinstance(fin_in, dict):
        fin_in = {}
    # 合并 compute 结果上的 raw / investment
    inv = fin.get("investment") or {}
    bd = fin_in.get("invest_breakdown") or {}
    raw = fin.get("raw") or {}
    if not bd and isinstance(raw, dict):
        bd = raw.get("invest_breakdown") or {}
    missing: dict[str, list[str]] = {}
    is_operating = bool((fin.get("params") or {}).get("is_operating"))

    has_detail = bool(
        (bd or {}).get("construction_detail")
        or fin_in.get("construction_detail")
        or inv.get("breakdown_detail")
    )
    items = (bd or {}).get("construction_items") or []
    has_qi_items = False
    if isinstance(items, list):
        qi = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            qty = it.get("quantity")
            ind = it.get("indicator_yuan") if it.get("indicator_yuan") is not None else it.get("indicator")
            if qty not in (None, "") and ind not in (None, ""):
                try:
                    if float(qty) > 0 and float(ind) > 0:
                        qi += 1
                except (TypeError, ValueError):
                    pass
        has_qi_items = qi >= 3
    if not has_detail:
        missing["investment"] = ["construction_detail（建筑/设备/安装）"]
    elif not has_qi_items:
        missing["investment"] = [
            "construction_items 工程量×估算指标明细（≥3 项；仅有分类金额不算 reference）"
        ]

    if is_operating:
        wc = (fin.get("annual") or {}).get("working_capital") or {}
        method = (wc.get("method") if isinstance(wc, dict) else None) or ""
        inv_wc = float((fin.get("investment") or {}).get("working_capital") or 0.0)
        has_wc_turn = bool(
            fin_in.get("wc_turnover") or fin_in.get("wc_turnover_days")
            or raw.get("wc_turnover") or raw.get("wc_turnover_days")
        )
        # Zero working-capital projects need no turnover schedule.
        if inv_wc > 0.01 and (not has_wc_turn or method == "ratio_backsolve"):
            missing["working-capital"] = [
                "wc_turnover 分项周转天数（当前可能为 ratio_backsolve）"
            ]

        spec = fin.get("spec") if isinstance(fin.get("spec"), dict) else {}
        revenue = (spec.get("revenue") or {}) if spec else {}
        model = str(revenue.get("model") or "flat")
        revenue_missing: list[str] = []
        if model == "product_sales":
            products = revenue.get("products") or fin_in.get("products") or []
            if not products:
                revenue_missing.append("products 量价明细")
            else:
                bad_ramp = [
                    p.get("name") or "未命名产品" for p in products
                    if isinstance(p, dict) and len(p.get("ramp") or []) <= 1
                ]
                if bad_ramp:
                    revenue_missing.append(
                        "product_ramp 投产爬坡（仅单点：" + "、".join(map(str, bad_ramp)) + "）"
                    )
        elif model == "property_sales":
            for field in ("saleable_area", "price_per_sqm", "absorption"):
                if revenue.get(field) in (None, "", [], {}):
                    revenue_missing.append(field)
        elif model == "tourism":
            for field in ("annual_visitors", "visitor_ramp"):
                if revenue.get(field) in (None, "", [], {}):
                    revenue_missing.append(field)
            if not revenue.get("tourism_revenue_components"):
                revenue_missing.append("tourism_revenue_components 分项收入树")
        elif model == "gov_payment":
            for field in ("annual_gov_payment_wan", "payment_ramp"):
                if revenue.get(field) in (None, "", [], {}):
                    revenue_missing.append(field)
        elif model in {"lease_portfolio", "inventory_sales"}:
            if not (revenue.get("annual_schedule_wan") or revenue.get("sales_schedule")):
                revenue_missing.append("annual_schedule_wan/sales_schedule 收入序列")
        else:
            revenue_missing.append("正式级收入明细（flat 单点收入仅可作摘要）")
        if revenue_missing:
            missing["income-statement"] = revenue_missing

        # cost tree for total-cost reference
        cost_items = fin_in.get("cost_items") or raw.get("cost_items") or {}
        if not (isinstance(cost_items, dict) and len(cost_items) >= 3):
            missing["total-cost"] = ["cost_items 成本明细树（≥3 项；默认总成本率不算 reference）"]

    # reference 深度扩展（人员/资产类别/存货树等）
    missing = merge_missing(missing, assess_missing_fields_extended(fin))
    return missing


def _table_applicability(fin: dict[str, Any], key: str) -> tuple[bool, str]:
    """Return whether a governed table applies to the current accounting path."""
    params = fin.get("params") or {}
    is_operating = bool(params.get("is_operating"))
    funding = fin.get("funding") or {}
    investment = fin.get("investment") or {}
    raw = fin.get("raw") or {}
    if not is_operating and key not in {"investment", "interest-during-construction", "funding"}:
        return False, "非经营性项目：经营性附表不适用，改用全生命周期资金平衡控制表"
    if key == "interest-during-construction" and float(investment.get("interest") or 0.0) <= 0:
        return False, "无建设期融资费用"
    if key == "debt-service" and float(funding.get("loan") or 0.0) <= 0:
        return False, "无债务融资"
    if key == "depreciation" and bool(raw.get("property_inventory")):
        return False, "房地产开发产品按存货核算，不计固定资产折旧"
    asset_map = investment.get("asset_map") or {}
    if key == "amortization" and float(asset_map.get("intangible_original") or 0.0) <= 0:
        return False, "无无形资产及其他资产摊销基数"
    if key == "working-capital" and float(investment.get("working_capital") or 0.0) <= 0.01:
        return False, "无流动资金投入，附表3不适用"
    return True, ""


def build_all_structured(fin: dict[str, Any]) -> dict[str, Any]:
    """统一入口：catalog 风格投影 → 全部交付表 structured。"""
    annual = fin.get("annual") or {}
    pack: dict[str, Any] = {}
    missing_map = _assess_missing_fields(fin)

    for key in DELIVERY_ORDER:
        spec = _TABLE_SPECS[key]
        normalized_records: list[dict[str, Any]] = []
        if spec.get("builder") == "investment":
            body = _build_investment(fin)
        elif spec.get("builder") == "funding":
            body = _build_funding(fin)
        elif spec.get("builder") == "working_capital":
            body = _build_wc(fin)
        else:
            raw_rows = annual.get(spec["annual_key"]) or []
            rows = _normalize_rows(key, raw_rows, fin)
            normalized_records = rows
            cols = list(spec["columns"])
            # 附表2：无 draw 时用 fallback 列
            if key == "interest-during-construction":
                if not rows or rows[0].get("draw") is None:
                    cols = list(spec.get("fallback_columns") or cols)
            body = _pack_rows(
                rows, cols,
                source=f"annual.{spec.get('annual_key', '')}",
                footer=spec.get("footer") or "",
            )
            if key == "income-statement":
                raw = fin.get("raw") or {}
                if raw.get("surtax_on_vat"):
                    policy = raw.get("surtax_component_policy") or {}
                    rate = float(policy.get("combined_rate") or raw.get("surtax_vat_rate") or 0.12) * 100
                    if policy.get("mode") == "statutory_components":
                        body["footer"] = (
                            "> 所得税/净利为**融资前**口径；融资后见附表7。"
                            f"税金及附加按（实际应纳增值税+消费税）×{rate:.1f}% 计算，"
                            "其中城建税采用项目所在地税率、教育费附加3%、地方教育附加2%，与附表9同源。"
                        )
                    else:
                        body["footer"] = (
                            "> 所得税/净利为**融资前**口径；融资后见附表7。"
                            f"本次按应纳增值税×综合率{rate:.1f}%作兼容预览；正式交付须确认项目所在地城建税率。"
                        )
                else:
                    rate = float(raw.get("surtax_revenue_rate") or 0.0) * 100
                    body["footer"] = (
                        "> 所得税/净利为**融资前**口径；融资后见附表7。"
                        f"本次显式采用营业收入×{rate:.2f}% 简化附加税口径，与附表9同源。"
                    )
                revenue_spec = (((fin.get("spec") or {}).get("revenue") or {}))
                products = revenue_spec.get("products") or []
                if products:
                    body["product_tree"] = [
                        {
                            "name": p.get("name"), "unit": p.get("unit"),
                            "price_per_unit": p.get("price_per_unit"),
                            "price_unit": p.get("price_unit") or "yuan",
                            "capacity": p.get("capacity"), "ramp": p.get("ramp") or [],
                            "var_cost_rate": p.get("var_cost_rate"),
                        }
                        for p in products if isinstance(p, dict)
                    ]
                else:
                    # Non-product formal revenue structures (property / lease / inventory)
                    model = str(revenue_spec.get("model") or "")
                    if model == "tourism" and revenue_spec.get("tourism_revenue_components"):
                        visitor_capacity = float(revenue_spec.get("annual_visitors") or 0.0)
                        visitor_unit = str(revenue_spec.get("visitor_unit") or "人次")
                        visitor_ramp = list(revenue_spec.get("visitor_ramp") or [])
                        body["product_tree"] = []
                        for component in revenue_spec.get("tourism_revenue_components") or []:
                            if not isinstance(component, dict):
                                continue
                            if component.get("basis") == "fixed_annual":
                                capacity = 1.0
                                unit = "年"
                                price = component.get("annual_revenue_wan")
                                price_unit = "wan"
                            else:
                                capacity = visitor_capacity * float(component.get("participation_rate") or 1.0)
                                unit = visitor_unit
                                price = component.get("price_per_visitor_yuan")
                                price_unit = "yuan"
                            body["product_tree"].append({
                                "name": component.get("name"),
                                "unit": unit,
                                "price_per_unit": price,
                                "price_unit": price_unit,
                                "capacity": capacity,
                                "ramp": list(component.get("ramp") or visitor_ramp),
                                "var_cost_rate": 0.0,
                                "revenue_model": model,
                            })
                    elif model == "property_sales" and revenue_spec.get("absorption"):
                        body["product_tree"] = [{
                            "name": "物业去化收入",
                            "unit": "m2",
                            "price_per_unit": revenue_spec.get("price_per_sqm"),
                            "price_unit": "yuan_per_sqm",
                            "capacity": revenue_spec.get("saleable_area"),
                            "ramp": list(revenue_spec.get("absorption") or []),
                            "var_cost_rate": 0.0,
                            "revenue_model": model,
                        }]
                    elif model in {"lease_portfolio", "inventory_sales"} and (
                        revenue_spec.get("annual_schedule_wan") or revenue_spec.get("sales_schedule")
                    ):
                        body["product_tree"] = [{
                            "name": "组合/去化收入序列",
                            "unit": "年",
                            "price_per_unit": revenue_spec.get("annual_revenue_wan"),
                            "price_unit": "wan",
                            "capacity": 1.0,
                            "ramp": list(
                                revenue_spec.get("sales_schedule")
                                or [
                                    (float(v) / float(revenue_spec.get("annual_revenue_wan") or 1.0))
                                    if float(revenue_spec.get("annual_revenue_wan") or 0) else 0.0
                                    for v in (revenue_spec.get("annual_schedule_wan") or [])
                                ]
                            ),
                            "var_cost_rate": 0.0,
                            "revenue_model": model,
                            "annual_schedule_wan": list(revenue_spec.get("annual_schedule_wan") or []),
                        }]
            if key == "capital-cashflow":
                body["notes"] = [
                    f"资本金IRR={annual.get('capital_irr_pct')}",
                    f"项目IRR={(fin.get('indicators') or {}).get('project_irr_pct')}",
                ]
            if key == "cashflow":
                body.setdefault("notes", []).append("建设投资列含建设期利息（与项目 CF 同源）")

            body = _promote_reference_period_table(
                key, body, normalized_records, fin,
            )

        # 甲方参考行树（有输入才 complete；不反向造数）
        body = _attach_reference_row_trees(key, body, fin)

        applicable, applicability_note = _table_applicability(fin, key)
        miss = list(missing_map.get(key) or []) if applicable else []
        notes = list(body.get("notes") or [])
        if miss:
            notes.append("missing_fields: " + "；".join(miss))
        effective = body.get("effective")
        if effective is None:
            rows = body.get("rows") or []
            numeric = [v for row in rows for v in row if isinstance(v, (int, float))]
            effective = bool(rows) and (any(abs(float(v)) > 1e-9 for v in numeric) or not numeric)
        if key == "amortization" and applicable and not effective:
            miss.append("摊销表全零：未提供无形资产/其他资产摊销基数")
            notes.append("空表不计入有效齐套")

        # 结构覆盖：唯一可置 reference_structure=True 的入口
        structure = (
            assess_structure_coverage(key, body, fin)
            if applicable
            else {
                "reference_structure": False,
                "structure_coverage": 0.0,
                "structure_gaps": [],
                "structure_checks": {},
            }
        )
        row_contract = _renderer_row_contract(key, body) if applicable else {
            "ok": True, "coverage": 1.0, "checks": {}, "gaps": [],
        }
        structure_gaps = list(structure.get("structure_gaps") or [])
        for gap in row_contract.get("gaps") or []:
            if gap not in structure_gaps:
                structure_gaps.append(gap)
        if not row_contract.get("ok"):
            structure["reference_structure"] = False
            structure["structure_coverage"] = min(
                float(structure.get("structure_coverage") or 0.0),
                float(row_contract.get("coverage") or 0.0),
            )
        structure["structure_gaps"] = structure_gaps
        if key == "depreciation" and row_contract.get("checks", {}).get("asset_class_facts"):
            miss = [
                item for item in miss
                if not str(item).startswith("asset_classes ")
                and "缺资产类别折旧" not in str(item)
            ]
        body["reference_structure"] = bool(structure.get("reference_structure"))
        body["structure_coverage"] = structure.get("structure_coverage")
        body["structure_gaps"] = list(structure.get("structure_gaps") or [])
        body["structure_checks"] = structure.get("structure_checks") or {}
        body["renderer_row_contract"] = row_contract
        if body["structure_gaps"]:
            notes.append("structure_gaps: " + "；".join(body["structure_gaps"]))
            for gap in body["structure_gaps"]:
                if gap not in miss:
                    miss.append(gap)

        if not applicable:
            grade = "not_applicable"
        else:
            grade = (
                "reference"
                if body.get("reference_structure") and not miss and effective
                else "summary"
            )
        pack[key] = {
            "table_id": key,
            "delivery_no": spec["delivery_no"],
            "title": spec["title"],
            **body,
            "notes": notes,
            "grade": grade,
            "effective": bool(effective),
            "applicable": applicable,
            "applicability_note": applicability_note,
            "missing_fields": miss,
            "reference_schema": schema_path(),
        }
    applicable_tables = [k for k in DELIVERY_ORDER if (pack.get(k) or {}).get("applicable", True)]
    not_applicable = [k for k in DELIVERY_ORDER if k not in applicable_tables]
    ineffective = [k for k in applicable_tables if not (pack.get(k) or {}).get("effective")]
    blocking_missing = {
        k: v for k, v in missing_map.items()
        if v and k in applicable_tables
    }
    # 结构缺口也算 blocking（即使 baseline missing_map 未收录）
    for k in applicable_tables:
        gaps = (pack.get(k) or {}).get("structure_gaps") or []
        if gaps:
            blocking_missing.setdefault(k, [])
            for g in gaps:
                if g not in blocking_missing[k]:
                    blocking_missing[k].append(g)
    reference_tables = [
        k for k in applicable_tables if (pack.get(k) or {}).get("grade") == "reference"
    ]
    structure_scores = {
        k: float((pack.get(k) or {}).get("structure_coverage") or 0.0)
        for k in applicable_tables
    }
    source_integrity = validate_reference_sources()
    _expected_ws = str(
        fin.get("workspace_id")
        or (fin.get("raw") or {}).get("workspace_id")
        or ""
    )
    source_coverage = assess_fact_source_coverage(
        fin, applicable_tables,
        expected_workspace_id=_expected_ws or None,
    )
    reference_structure_ready = (
        not blocking_missing
        and not ineffective
        and len(reference_tables) == len(applicable_tables)
        and all((pack.get(k) or {}).get("row_count", 0) > 0 for k in applicable_tables)
        and all((pack.get(k) or {}).get("reference_structure") for k in applicable_tables)
        and bool(source_integrity.get("ok"))
    )
    for key in applicable_tables:
        table_source = (source_coverage.get("by_table") or {}).get(key) or {}
        pack[key]["source_coverage"] = float(table_source.get("coverage") or 0.0)
        pack[key]["source_gaps"] = list(table_source.get("missing_domains") or [])
    pack["_meta"] = {
        "grade": "reference" if reference_structure_ready else "summary",
        "missing_fields_by_table": missing_map,
        "blocking_missing_by_table": blocking_missing,
        "structure_coverage_by_table": structure_scores,
        "reference_schema": schema_path(),
        "reference_schema_version": "reference_table_schema.v3",
        "template_ready": reference_structure_ready,
        "reference_structure_ready": reference_structure_ready,
        # 预导出阶段没有公式覆盖/独立重算证据，禁止提前宣称 formal。
        "validation_complete": False,
        "formal_gate_stage": "pre_export",
        "source_coverage": source_coverage.get("coverage"),
        "source_coverage_by_table": {
            key: ((source_coverage.get("by_table") or {}).get(key) or {}).get("coverage", 0.0)
            for key in applicable_tables
        },
        "source_coverage_issues": source_coverage.get("issues") or [],
        "missing_fact_paths": source_coverage.get("missing_fact_paths") or [],
        "runtime_source_validation": source_coverage.get("runtime_source_validation") or {},
        "fact_pack_version": source_coverage.get("version") or "",
        "fact_pack_confirmation_status": source_coverage.get("confirmation_status") or "",
        "delivery_grade_ceiling": source_coverage.get("delivery_grade_ceiling") or "summary",
        "depth_ok": bool(source_coverage.get("depth_ok")),
        "reference_source_integrity": bool(source_integrity.get("ok")),
        "reference_source_integrity_issues": source_integrity.get("issues") or [],
        "effective_table_count": len(applicable_tables) - len(ineffective),
        "reference_table_count": len(reference_tables),
        "required_table_count": len(applicable_tables),
        "delivery_table_count": len(DELIVERY_ORDER),
        "ineffective_tables": ineffective,
        "not_applicable_tables": not_applicable,
        "formal_vs_business_note": (
            "reference_structure_ready 仅表示结构齐套；validation_complete 还必须在导出阶段通过"
            "来源覆盖、公式覆盖、独立重算、delivery_grade_ceiling=formal_candidate，"
            "且仍不等于五簿 dual_track review_passed 或甲方业务闭合"
        ),
    }
    return pack


def structured_table_to_md(table: dict[str, Any]) -> str:
    """单表 structured → markdown 管道表。"""
    cols_meta = table.get("columns") or []
    col_keys = [
        (c.get("key") if isinstance(c, dict) else (c[0] if isinstance(c, (list, tuple)) else ""))
        for c in cols_meta
    ]
    labels = table.get("column_labels") or [
        (c.get("label", "") if isinstance(c, dict) else (c[1] if isinstance(c, (list, tuple)) and len(c) > 1 else str(c)))
        for c in cols_meta
    ]
    rows = table.get("rows") or []
    if not labels:
        return ""
    lines = [
        "| " + " | ".join(str(x) for x in labels) + " |",
        "| " + " | ".join(["---"] * len(labels)) + " |",
    ]
    for row in rows:
        cells = []
        for i, v in enumerate(row):
            key = col_keys[i] if i < len(col_keys) else ""
            cells.append(_fmt_cell(str(key or ""), v))
        while len(cells) < len(labels):
            cells.append("")
        lines.append("| " + " | ".join(cells[: len(labels)]) + " |")
    footer = table.get("footer") or ""
    if footer:
        lines.append("")
        lines.append(footer)
    return "\n".join(lines)


def render_all_markdown_from_structured(pack: dict[str, Any]) -> dict[str, str]:
    """全部交付表 → {key: md_string}，供 result['tables'] 兼容。"""
    return {
        k: structured_table_to_md(pack[k])
        for k in DELIVERY_ORDER
        if k in pack and isinstance(pack.get(k), dict) and pack[k].get("row_count", 0) >= 0
    }


def finance_tables_markdown_from_structured(pack: dict[str, Any], fin: Optional[dict] = None) -> str:
    """拼完整可读 MD（含标题）。"""
    parts = []
    for key in DELIVERY_ORDER:
        t = pack.get(key)
        if not t:
            continue
        md = structured_table_to_md(t)
        if not md and not t.get("row_count"):
            continue
        title = f"{t.get('delivery_no', '')} {t.get('title', key)}"
        parts.append(f"\n\n**{title}**\n\n{md}")
    # 展示表
    if fin:
        ind_md = (fin.get("tables") or {}).get("indicators")
        if ind_md:
            parts.append(f"\n\n**附表（展示）主要技术经济指标表**\n\n{ind_md}")
        sens = (fin.get("tables") or {}).get("sensitivity")
        if sens:
            parts.append(f"\n\n**附表（展示）单因素敏感性分析表**\n\n{sens}")
        scenarios = fin.get("scenarios") or {}
        if scenarios.get("base"):
            parts.append(
                "\n\n**情景分析**\n\n"
                f"- 基准：IRR {_fmt((scenarios.get('base') or {}).get('irr_pct'))}%\n"
                f"- 乐观：IRR {_fmt((scenarios.get('bull') or {}).get('irr_pct'))}%\n"
                f"- 悲观：IRR {_fmt((scenarios.get('bear') or {}).get('irr_pct'))}%"
            )
    return "".join(parts)
