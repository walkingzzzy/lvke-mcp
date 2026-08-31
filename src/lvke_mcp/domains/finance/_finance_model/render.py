"""表格与摘要渲染、必需标记与 markdown 输出。"""

from __future__ import annotations

from typing import Any

# P0/P1 modular finance package (方案 §8/§13)

from .base import (
    _fmt,
    _fmt_rate_display,
)


def _render_tables(r: dict[str, Any]) -> dict[str, str]:
    inv = r["investment"]
    fund = r["funding"]
    ind = r.get("indicators") or {}
    tables: dict[str, str] = {}

    # 附表1 固定资产投资估算表（交付编号：晏批注附表1）。
    # 【P2】按引擎实际持有的投资分项展示：建设投资（若提供工程建设其他费用/预备费则拆分显示）、
    #   建设期利息、流动资金。注：更细的"建筑/设备/安装工程费"三段式需上游采集颗粒度投资输入，
    #   当前输入 schema 未提供；不从单一建设投资数凭空拆分（可研不得造数），故按已有颗粒度呈现。
    _detail = inv.get("breakdown_detail")
    _other = inv.get("other")
    _reserve = inv.get("reserve")
    rows = []
    if _detail and (_detail["engineering"] or _detail["other"] or _detail["contingency"]):
        # 最细档：完整三段式（工程费用 1.1 / 工程建设其他费用 1.2 / 预备费 1.3，各带细项）。
        _eng_t = _detail["engineering_total"]
        _oth_t = _detail["other_total"]
        _con_t = _detail["contingency_total"]
        rows.append(("1", "建设投资", inv["construction"]))
        if _detail["engineering"]:
            rows.append(("1.1", "　一、工程费用", _eng_t))
            for i, (label, val) in enumerate(_detail["engineering"], 1):
                rows.append((f"1.1.{i}", f"　　{label}", val))
        if _detail["other"]:
            rows.append(("1.2", "　二、工程建设其他费用", _oth_t))
            for i, (label, val) in enumerate(_detail["other"], 1):
                rows.append((f"1.2.{i}", f"　　{label}", val))
        if _detail["contingency"]:
            rows.append(("1.3", "　三、预备费", _con_t))
            for i, (label, val) in enumerate(_detail["contingency"], 1):
                rows.append((f"1.3.{i}", f"　　{label}", val))
    elif _other or _reserve:
        # 中档：仅有分段汇总(其他/预备费)，无细项 → 拆 3 行。
        _eng = round((inv["construction"] or 0.0) - (_other or 0.0) - (_reserve or 0.0), 2)
        rows.append(("1", "建设投资", inv["construction"]))
        rows.append(("1.1", "　工程费用（建安工程）", _eng))
        if _other:
            rows.append(("1.2", "　工程建设其他费用", _other))
        if _reserve:
            rows.append(("1.3", "　预备费", _reserve))
    else:
        # 降级档：仅单一建设投资数（不造数）。
        rows.append(("1", "建设投资", inv["construction"]))
    rows.append(("2", "建设期利息", inv["interest"]))
    rows.append(("3", "流动资金", inv["working_capital"]))
    rows.append(("", "项目总投资合计", inv["total"]))
    body = "\n".join(f"| {no} | {name} | {_fmt(v)} |" for no, name, v in rows)
    tables["investment"] = "| 序号 | 项目 | 金额（万元） |\n| --- | --- | --- |\n" + body

    # 附表 资金筹措
    frows = [
        ("1", "项目资本金（自筹）", fund["capital"], fund["capital_pct"]),
        ("2", "银行贷款", fund["loan"], fund["loan_pct"]),
    ]
    if fund["subsidy"]:
        frows.append(("3", "政府补助/专项债", fund["subsidy"], fund["subsidy_pct"]))
    frows.append(("", "合计", inv["total"], 100.0))
    fbody = "\n".join(f"| {no} | {name} | {_fmt(v)} | {_fmt(p)} |" for no, name, v, p in frows)
    tables["funding"] = "| 序号 | 资金来源 | 金额（万元） | 占比（%） |\n| --- | --- | --- | --- |\n" + fbody

    # 主要技术经济指标
    mrows = [("项目总投资", "万元", inv["total"]),
             ("其中：建设投资", "万元", inv["construction"]),
             ("　　　建设期利息", "万元", inv["interest"]),
             ("　　　流动资金", "万元", inv["working_capital"]),
             ("项目资本金", "万元", fund["capital"]),
             ("银行贷款", "万元", fund["loan"])]
    if ind:
        mrows += [
            ("达产年营业收入", "万元", ind.get("revenue")),
            ("达产年总成本费用", "万元", ind.get("op_cost")),
            ("达产年净利润", "万元", ind.get("net_profit")),
            ("项目投资财务内部收益率(IRR)", "%", ind.get("project_irr_pct")),
            (f"财务净现值(Ic={ind.get('benchmark_rate_pct')}%)", "万元", ind.get("npv_wan")),
            ("静态投资回收期", "年", ind.get("static_payback_years")),
            ("动态投资回收期", "年", ind.get("dynamic_payback_years")),
            ("盈亏平衡点", "%", ind.get("bep_pct")),
        ]
    mbody = "\n".join(f"| {name} | {unit} | {_fmt(v)} |" for name, unit, v in mrows)
    tables["indicators"] = "| 指标名称 | 单位 | 数值 |\n| --- | --- | --- |\n" + mbody
    # M1 T1.1：合并逐年联动附表（key 对齐 lvke_templates.catalog 的 template_id）
    tables.update(_render_annual_tables(r))
    # 别名：3 张基础表同时以 catalog template_id 暴露，供 appendix ready 判定
    tables["investment-estimation"] = tables["investment"]
    tables["financing"] = tables["funding"]
    tables["key-indicators"] = tables["indicators"]
    return tables


def _render_summary(r: dict[str, Any]) -> str:
    """供注入生成 prompt 的"确定性财务数据块"（要求 LLM 原样引用，不得改动）。"""
    inv, fund, ind = r["investment"], r["funding"], r.get("indicators") or {}
    lines = [
        "【以下为 finance-calc 确定性测算结果，必须原样引用，不得改动、不得写“详见第六章测算”】",
        f"- 项目总投资：{_fmt(inv['total'])} 万元（建设投资 {_fmt(inv['construction'])}、建设期利息 {_fmt(inv['interest'])}、流动资金 {_fmt(inv['working_capital'])}）",
        f"- 资金筹措：资本金/自筹 {_fmt(fund['capital'])} 万元（{_fmt(fund['capital_pct'])}%）、银行贷款 {_fmt(fund['loan'])} 万元（{_fmt(fund['loan_pct'])}%）"
        + (f"、政府补助 {_fmt(fund['subsidy'])} 万元" if fund['subsidy'] else "")
        + f"；贷款期限 {fund['loan_years']} 年、年利率 {fund['loan_rate']*100:.1f}%",
    ]
    if ind:
        lines += [
            f"- 达产年：营业收入 {_fmt(ind.get('revenue'))} 万元、总成本费用 {_fmt(ind.get('op_cost'))} 万元、净利润 {_fmt(ind.get('net_profit'))} 万元",
            f"- 财务指标：IRR {_fmt(ind.get('project_irr_pct'))}%、财务净现值(Ic={ind.get('benchmark_rate_pct')}%) {_fmt(ind.get('npv_wan'))} 万元、静态回收期 {_fmt(ind.get('static_payback_years'))} 年、动态回收期 {_fmt(ind.get('dynamic_payback_years'))} 年、盈亏平衡点 {_fmt(ind.get('bep_pct'))}%",
        ]
    else:
        lines.append("- 本项目为非经营性项目，不计算 IRR/NPV，按全生命周期资金平衡分析。")
    if r.get("assumptions"):
        lines.append("- 测算假设：" + "；".join(r["assumptions"]))
    return "\n".join(lines)


def _required_markers(r: dict[str, Any]) -> list[str]:
    m = ["项目总投资", "项目资本金", "银行贷款"]
    if r.get("indicators"):
        m += ["财务内部收益率", "财务净现值", "投资回收期", "盈亏平衡点"]
    return m


def finance_tables_markdown(r: dict[str, Any]) -> str:
    """把附表拼成可直接嵌入正文的 Markdown（财务章尾部/附表区）。

    【2026-07-12】主路径改为 catalog 风格 structured 投影 → MD 适配器；
    不再以手写管道表为唯一实现。失败时回退旧 _render 结果中的 tables 字符串。
    """
    if not r.get("available"):
        return ""
    try:
        from lvke_mcp.domains.finance import table_render

        pack = table_render.build_all_structured(r)
        md = table_render.finance_tables_markdown_from_structured(pack, r)
        if md and md.strip():
            return md
    except Exception:  # noqa: BLE001
        pass
    # 回退：使用已渲染的 result['tables'] 字符串
    t = r.get("tables") or {}
    seq = [
        ("附表1 固定资产投资估算表（万元）", "investment"),
        ("附表2 建设期贷款利息表（万元）", "interest-during-construction"),
        ("附表3 流动资金估算表（万元）", "working-capital"),
        ("附表4 投资使用计划与资金筹措表（万元）", "funding"),
        ("附表5 营业收入、税金及附加和增值税估算表（万元）", "income-statement"),
        ("附表6 总成本费用估算表（万元）", "total-cost"),
        ("附表6-1 工资及附加估算表（万元）", "wage"),
        ("附表6-2 固定资产折旧费估算表（万元）", "depreciation"),
        ("附表6-3 无形资产及其他资产摊销估算表（万元）", "amortization"),
        ("附表7 利润与利润分配表（万元）", "profit-distribution"),
        ("附表8 还款付息测算表（万元）", "debt-service"),
        ("附表9 项目投资现金流量表（万元）", "cashflow"),
        ("附表10 项目资本金流量表（万元）", "capital-cashflow"),
        # 附表11 已是正式交付表（见 _run_service.base.DELIVERY_TABLE_META），
        # 故从 display_seq（仅展示、不占编号）移入 seq。留在 display_seq 里会让
        # markdown 审计副本称它"控制表 C03"、而 XLSX/CSV 称"附表11"——同一张表
        # 两个名字，审查方无从对应大纲条款。
        ("附表11 财务计划现金流量表（万元）", "financial-plan"),
    ]
    display_seq = [
        ("附表（展示）主要技术经济指标表", "indicators"),
        ("附表（展示）单因素敏感性分析表", "sensitivity"),
    ]
    parts: list[str] = []
    for title, key in seq + display_seq:
        md = t.get(key)
        if md:
            parts.append(f"\n\n**{title}**\n\n{md}")
    sc = r.get("scenarios") or {}
    if sc.get("base"):
        parts.append(
            "\n\n**情景分析**\n\n"
            f"- 基准：IRR {_fmt(sc['base'].get('irr_pct'))}%\n"
            f"- 乐观：IRR {_fmt((sc.get('bull') or {}).get('irr_pct'))}%\n"
            f"- 悲观：IRR {_fmt((sc.get('bear') or {}).get('irr_pct'))}%"
        )
    body = "".join(parts)
    return body


def _render_annual_tables(r: dict[str, Any]) -> dict[str, str]:
    """把逐年结构化附表渲染为 Markdown，key 对齐 lvke_templates.catalog 的 template_id。"""
    a = r.get("annual") or {}
    t: dict[str, str] = {}

    ds = a.get("debt_service") or []
    if ds and any(x.get("interest") for x in ds):
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['begin'])} | {_fmt(x['principal'])} | {_fmt(x['interest'])} | {_fmt(x['end'])} | {_fmt(x.get('dscr'))} | {_fmt(x.get('icr'))} |"
            for x in ds)
        t["debt-service"] = ("| 运营年 | 期初借款余额 | 当期还本 | 当期付息 | 期末借款余额 | 偿债备付率(DSCR) | 利息备付率(ICR) |\n"
                             "| --- | --- | --- | --- | --- | --- | --- |\n" + body)

    inc = a.get("income_statement") or []
    if inc:
        # PG5-a：附表5 增列增值税三列（销项/进项/应纳），价外税不进净利润列。
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['revenue'])} | {_fmt(x['operating_cost'])} | {_fmt(x['depreciation'])} | "
            f"{_fmt(x['tax_surtax'])} | {_fmt(x.get('vat_output'))} | {_fmt(x.get('vat_input'))} | {_fmt(x.get('vat_payable'))} | "
            f"{_fmt(x['income_tax'])} | {_fmt(x['net_profit'])} |"
            for x in inc)
        t["income-statement"] = (
            "| 运营年 | 营业收入 | 经营成本 | 折旧 | 销售税金及附加 | 销项税 | 进项税 | 应纳增值税 | 调整所得税(融资前) | 融资前净利 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + body
            + "\n\n> 本表所得税/净利为**融资前**口径（不含利息税盾）；融资后会计利润见附表7。"
            "销售税金及附加与附表9 同源（默认应纳增值税×附加率）。")

    tc = a.get("total_cost") or []
    if tc:
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['operating_cost'])} | {_fmt(x['depreciation'])} | {_fmt(x['amortization'])} | {_fmt(x['interest'])} | {_fmt(x['total_cost'])} |"
            for x in tc)
        t["total-cost"] = ("| 运营年 | 经营成本 | 折旧费 | 摊销费 | 利息支出 | 总成本费用 |\n"
                           "| --- | --- | --- | --- | --- | --- |\n" + body)

    # 附表6-1 工资及附加估算表
    wg = a.get("wage") or []
    if wg:
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['wage'])} | {_fmt(x['welfare'])} | {_fmt(x['total'])} |"
            for x in wg)
        t["wage"] = ("| 运营年 | 工资 | 职工福利及附加 | 工资及附加合计 |\n"
                     "| --- | --- | --- | --- |\n" + body)

    # 附表6-2 固定资产折旧费估算表
    dp = a.get("depreciation_table") or []
    if dp:
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['original_value'])} | {_fmt(x['salvage_rate'])} | {x['dep_years']} | {_fmt(x['depreciation'])} |"
            for x in dp)
        t["depreciation"] = ("| 运营年 | 固定资产原值 | 残值率 | 折旧年限 | 当期折旧费 |\n"
                             "| --- | --- | --- | --- | --- |\n" + body)

    # 附表6-3 无形及其他资产摊销估算表
    am = a.get("amortization_table") or []
    if am:
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['base'])} | {x['amort_years']} | {_fmt(x['amortization'])} |"
            for x in am)
        t["amortization"] = ("| 运营年 | 摊销基数 | 摊销年限 | 当期摊销费 |\n"
                             "| --- | --- | --- | --- |\n" + body)

    pd = a.get("profit_distribution") or []
    if pd:
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['revenue'])} | {_fmt(x['total_cost'])} | {_fmt(x['total_profit'])} | {_fmt(x['income_tax'])} | {_fmt(x['net_profit'])} | {_fmt(x['undistributed'])} |"
            for x in pd)
        t["profit-distribution"] = ("| 运营年 | 营业收入 | 总成本费用 | 利润总额 | 所得税 | 净利润 | 未分配利润 |\n"
                                    "| --- | --- | --- | --- | --- | --- | --- |\n" + body)

    proj = a.get("project_cashflow") or []
    if proj:
        if any(x.get("phase") for x in proj):  # P0-4：逐行组成(营收/成本/税/调整所得税/建设投资/流资/回收)
            body = "\n".join(
                f"| {x['year']} | {x.get('phase','')} | {_fmt(x.get('revenue'))} | {_fmt(x.get('op_cash_cost'))} | "
                f"{_fmt(x.get('tax_surtax'))} | {_fmt(x.get('income_tax'))} | {_fmt(x.get('construction'))} | "
                f"{_fmt(x.get('wc_change'))} | {_fmt(x.get('recover'))} | {_fmt(x['net_cashflow'])} | {_fmt(x['cumulative'])} |"
                for x in proj)
            t["cashflow"] = (
                "| 计算期(年) | 阶段 | 营业收入 | 经营成本 | 税金及附加 | 调整所得税 | 建设投资 | 流动资金增加 | 回收(流资+余值) | 净现金流 | 累计净现金流 |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + body
                + "\n\n> 调整所得税为融资前口径(不含利息税盾);现金流不含借款/还本/融资利息,评价项目融资前盈利能力。")
        else:
            body = "\n".join(f"| {x['year']} | {_fmt(x['net_cashflow'])} | {_fmt(x['cumulative'])} |" for x in proj)
            t["cashflow"] = ("| 计算期(年) | 净现金流 | 累计净现金流 |\n| --- | --- | --- |\n" + body)

    cap = a.get("capital_cashflow") or []
    if cap:
        cap_irr = a.get("capital_irr_pct")
        note = f"\n\n> 资本金财务内部收益率(IRR)：{_fmt(cap_irr)}%" if cap_irr is not None else ""
        if any(x.get("phase") for x in cap):  # P0-4：逐行组成(资本金投入/经营现金流入/还本/付息)
            body = "\n".join(
                f"| {x['year']} | {x.get('phase','')} | {_fmt(x.get('capital_invest'))} | {_fmt(x.get('op_inflow'))} | "
                f"{_fmt(x.get('principal'))} | {_fmt(x.get('interest'))} | {_fmt(x['net_cashflow'])} |"
                for x in cap)
            t["capital-cashflow"] = (
                "| 计算期(年) | 阶段 | 资本金投入 | 经营现金流入 | 还本 | 付息 | 资本金净现金流 |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n" + body + note)
        else:
            body = "\n".join(f"| {x['year']} | {_fmt(x['net_cashflow'])} |" for x in cap)
            t["capital-cashflow"] = ("| 计算期(年) | 资本金净现金流 |\n| --- | --- |\n" + body + note)

    idc = a.get("interest_during_construction") or []
    if idc and any(x.get("interest") for x in idc):
        # P1：若含分年提款/期初余额/利率（半期计息明细），输出完整列；否则退简表。
        if any(("draw" in x or "rate" in x or "begin_balance" in x) for x in idc):
            body = "\n".join(
                f"| 建设期第{x['period']}年 | {_fmt(x.get('begin_balance', x.get('begin')))} | {_fmt(x.get('draw'))} | "
                f"{_fmt_rate_display(x.get('rate') if x.get('rate') is not None else x.get('rate_pct'))} | "
                f"{_fmt(x['interest'])} | "
                f"{_fmt(x.get('end_balance', x.get('end')))} |"
                for x in idc)
            t["interest-during-construction"] = (
                "| 期间 | 期初借款余额 | 当期提款 | 年利率(%) | 当期利息 | 期末借款余额 |\n"
                "| --- | --- | --- | --- | --- | --- |\n" + body
                + "\n\n> 当年提款按半年计息（提款额×利率/2），既有余额按全年计息。")
        else:
            body = "\n".join(f"| 建设期第{x['period']}年 | {_fmt(x['interest'])} |" for x in idc)
            t["interest-during-construction"] = ("| 期间 | 当期建设期利息 |\n| --- | --- |\n" + body)

    sens = r.get("sensitivity") or {}
    if sens.get("revenue"):
        deltas = sens.get("deltas") or []
        header = "| 因子 | " + " | ".join(f"{int(d*100):+d}%" if d else "基准" for d in deltas) + " |"
        sep = "| --- " * (len(deltas) + 1) + "|"
        rows = []
        for key, label in (("revenue", "营业收入"), ("op_cost", "经营成本"), ("construction", "建设投资")):
            series = sens.get(key) or []
            cells = " | ".join(_fmt(p.get("irr_pct")) for p in series)
            rows.append(f"| {label} | {cells} |")
        t["sensitivity"] = f"{header}\n{sep}\n" + "\n".join(rows) + "\n\n> 表内为项目财务内部收益率(IRR, %)对各因子变动的敏感性。"

    # 【P0-5】财务计划现金流量表（正式交付表附表11）：投资/融资/经营三活动 + 期末现金 + 累计盈余 + 缺口
    fp = a.get("financial_plan") or []
    if fp:
        body = "\n".join(
            f"| {x['period']} | {x['phase']} | {_fmt(x['finance_in'])} | {_fmt(x['operating_net'])} | "
            f"{_fmt(x['invest_out'])} | {_fmt(x['debt_service'])} | {_fmt(x['net_cashflow'])} | "
            f"{_fmt(x['cumulative'])} | {'⚠️缺口' if x['gap'] else '—'} |"
            for x in fp)
        min_cum = min((x["cumulative"] for x in fp), default=0.0)
        gap_years = [x["period"] for x in fp if x["gap"]]
        note = (f"\n\n> 最低累计盈余资金 {_fmt(min_cum)} 万元；"
                + (f"**资金缺口年份：第 {'、'.join(map(str, gap_years))} 期(累计现金为负,需接续融资)**"
                   if gap_years else "各期累计现金均为正,资金链可持续。"))
        t["financial-plan"] = (
            "| 计算期(年) | 阶段 | 融资流入 | 经营活动净现金 | 投资活动净流出 | 还本付息 | 当期净现金流 | 累计盈余资金 | 资金缺口 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + body + note)

    # 附表3 流动资金估算表（分项：应收/存货/现金 − 应付 = 新增流动资金）
    wc = a.get("working_capital") or {}
    # Zero working capital is still a valid, reviewable appendix (not a missing
    # appendix).  Render the zero rows so every available run keeps the complete
    # 13-table delivery contract, including property projects without working cash.
    if wc:
        rows = [
            ("应收账款", wc.get("receivable")),
            ("存货", wc.get("inventory")),
            ("现金", wc.get("cash")),
            ("流动资产小计", wc.get("current_assets")),
            ("减：应付账款", wc.get("payable")),
        ]
        body = "\n".join(f"| {name} | {_fmt(v)} |" for name, v in rows)
        t["working-capital"] = ("| 流动资金构成项 | 金额（万元） |\n| --- | --- |\n"
                                + body + f"\n| **新增流动资金合计** | **{_fmt(wc.get('total'))}** |")

    return t
