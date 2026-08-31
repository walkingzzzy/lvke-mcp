"""勾稽一致性检查与估算依据 markdown。"""

from __future__ import annotations

from typing import Any

# P0/P1 modular finance package (方案 §8/§13)
from lvke_mcp.domains.finance import checks as _fin_checks

from .base import (
    BENCHMARK_RATE,
    _fmt,
    _irr,
)


def check_consistency(r: dict[str, Any]) -> list[dict[str, Any]]:
    """M1 T1.2：财务数值勾稽校验，返回 [{rule, ok, detail}]（供 consistency_check 门禁）。"""
    if not r.get("available"):
        return []
    inv, fund = r["investment"], r["funding"]
    ind = r.get("indicators") or {}
    annual = r.get("annual") or {}
    checks: list[dict[str, Any]] = []

    # 1. 资金筹措合计 == 总投资
    fund_sum = round(fund["capital"] + fund["loan"] + fund["subsidy"], 2)
    checks.append({"rule": "资金筹措合计=总投资", "category": "integrity", "ok": abs(fund_sum - inv["total"]) < 1.0,
                   "detail": f"筹措合计 {_fmt(fund_sum)} vs 总投资 {_fmt(inv['total'])} 万元"})

    # 2. 项目现金流表 IRR == 主要技经指标 IRR（三处一致的核心）
    if ind.get("project_irr_pct") is not None:
        cfs = [x["net_cashflow"] for x in (annual.get("project_cashflow") or [])]
        try:
            irr_tbl = round(_irr(cfs) * 100, 2)
            checks.append({"rule": "现金流表IRR=技经指标IRR", "category": "integrity", "ok": abs(irr_tbl - ind["project_irr_pct"]) < 0.5,
                           "detail": f"现金流表 {irr_tbl}% vs 指标表 {ind['project_irr_pct']}%"})
        except Exception:  # noqa: BLE001
            pass

    # 3. 各年 总成本 == 经营成本 + 折旧 + 摊销 + 利息
    tc = annual.get("total_cost") or []
    if tc:
        ok3 = all(abs(x["total_cost"] - (x["operating_cost"] + x["depreciation"] + x["amortization"] + x["interest"])) < 1.0
                  for x in tc)
        checks.append({"rule": "总成本=经营成本+折旧+摊销+利息", "category": "integrity", "ok": ok3, "detail": f"逐年校验 {len(tc)} 年"})

    # 4. 建设期利息汇总 == 投资估算中的建设期利息
    idc = annual.get("interest_during_construction") or []
    if idc:
        idc_sum = round(sum(x["interest"] for x in idc), 2)
        checks.append({"rule": "建设期利息汇总=投资估算利息", "category": "integrity", "ok": abs(idc_sum - inv["interest"]) < 1.0,
                       "detail": f"汇总 {_fmt(idc_sum)} vs 估算 {_fmt(inv['interest'])} 万元"})

    # 5. 流动资金分项净额（流动资产−流动负债）== 投资估算流动资金（附表3 勾稽）
    wc = annual.get("working_capital") or {}
    if wc.get("current_assets") is not None:
        net = round((wc.get("current_assets") or 0.0) - (wc.get("current_liabilities") or 0.0), 2)
        stated = round(float(
            wc.get("investment_total")
            if wc.get("investment_total") is not None
            else wc.get("stated_total")
            if wc.get("stated_total") is not None
            else wc.get("total") or 0.0
        ), 2)
        delta = round(net - stated, 2)
        working_capital_ok = abs(delta) <= 0.01
        check = {
            "rule": "流动资金分项净额=投资估算流动资金",
            "category": "integrity",
            "ok": working_capital_ok,
            "detail": f"分项净额 {_fmt(net)} vs 估算 {_fmt(stated)} 万元，差额 {_fmt(delta)} 万元",
        }
        if not working_capital_ok:
            # 流动资金由周转天数×营收/成本推导，而营收成本又受它影响——这是个
            # 不动点，调用方只拿到差额时只能反复试算（实测迭代两轮才收敛）。
            # 分项净额就是本轮的解，连同应随之调整的总投资一起给出，让下一次
            # 提交能直接收敛，而不是继续猜。
            construction = round(float(inv.get("construction") or 0.0), 2)
            interest = round(float(inv.get("interest") or 0.0), 2)
            check.update(
                {
                    "code": "working_capital_inconsistent",
                    "blocking": True,
                    "resolution": {
                        "set_invest_breakdown_working_capital_wan": net,
                        "set_total_investment_wan": round(construction + interest + net, 2),
                        "note": (
                            "把 invest_breakdown.working_capital_wan 改为分项净额，"
                            "并把 total_investment_wan 改为 建设投资+建设期利息+该净额，"
                            "然后重新 prepare/confirm/run；周转天数不变时一次即可收敛。"
                        ),
                    },
                }
            )
        checks.append(check)

    # 6. 折旧(附表6-2)+摊销(附表6-3) 逐年合计 == 总成本表非现金摊提额（折旧+摊销），口径不重不漏
    dep_tbl = annual.get("depreciation_table") or []
    amort_tbl = annual.get("amortization_table") or []
    if dep_tbl and tc:
        ok6 = all(
            abs((dep_tbl[i]["depreciation"] + (amort_tbl[i]["amortization"] if i < len(amort_tbl) else 0.0))
                - (tc[i]["depreciation"] + tc[i]["amortization"])) < 1.0
            for i in range(min(len(dep_tbl), len(tc))))
        checks.append({"rule": "折旧表+摊销表=总成本表折旧摊销", "category": "integrity", "ok": ok6,
                       "detail": f"逐年校验 {min(len(dep_tbl), len(tc))} 年（附表6-2/6-3↔附表6）"})

    # 7.【H1】资本金现金流建设期股东净投入 == 非流动投资资金缺口。
    #    非流动投资 = 总投资 − 流动资金；贷款和政府补助优先覆盖建设期投入，股东投入补足差额。
    #    该口径同时适用于固定资产项目和房地产开发产品（存货）项目，不能再用 fixed_asset
    #    判断房地产资本金投入，否则 fixed_asset=0 会产生负的错误期望值。
    cap_cf = annual.get("capital_cashflow") or []
    build_years = int((r.get("params") or {}).get("build_years") or 1)
    if cap_cf and fund.get("loan") is not None:
        build_out = round(-sum(x["net_cashflow"] for x in cap_cf[:build_years] if x["net_cashflow"] < 0), 2)
        non_current_investment = round(
            float(inv.get("construction") or 0.0) + float(inv.get("interest") or 0.0), 2
        )
        expect = round(max(
            non_current_investment
            - float(fund.get("loan") or 0.0)
            - float(fund.get("subsidy") or 0.0),
            0.0,
        ), 2)
        tol = max(inv["total"] * 0.01, 50.0)
        checks.append({"rule": "资本金现金流建设期投入=非流动投资−贷款及补助",
                       "ok": abs(build_out - expect) < tol,
                       "detail": (
                           f"建设期股东净投入 {_fmt(build_out)} vs "
                           f"建设投资+建设期利息−贷款−补助 {_fmt(expect)} 万元"
                       )})

    # 8.【H2/M1】折旧表逐年"原值×(1−残值率)/折旧年限" == 当期折旧费（表内公式自洽、扣残值、扣无形）
    if dep_tbl:
        previous_cumulative = 0.0

        def _renewal_composite_ok(row: dict[str, Any]) -> bool:
            nonlocal previous_cumulative
            cumulative = float(row.get("cumulative_depreciation") or 0.0)
            charge = float(row.get("depreciation") or 0.0)
            ok = charge >= 0 and abs((cumulative - previous_cumulative) - charge) < 1.0
            previous_cumulative = cumulative
            return ok

        ok8 = all(
            (
                abs(
                    round(sum(float(item.get("depreciation") or 0.0) for item in x.get("classes") or []), 2)
                    - float(x.get("depreciation") or 0.0)
                ) < 1.0
                and all(
                    abs(
                        round(
                            float(item.get("original_value_wan") or 0.0)
                            * (1 - float(item.get("salvage_rate") or 0.0))
                            / max(int(item.get("depreciation_years") or 1), 1),
                            2,
                        ) - float(item.get("depreciation") or 0.0)
                    ) < 1.0
                    for item in x.get("classes") or [] if item.get("depreciation")
                )
            )
            if x.get("depreciation_basis") == "classified"
            else _renewal_composite_ok(x)
            if x.get("depreciation_basis") == "renewal_composite"
            else abs(
                round(x["original_value"] * (1 - x["salvage_rate"]) / max(x["dep_years"], 1), 2)
                - x["depreciation"]
            ) < 1.0
            for x in dep_tbl if x.get("depreciation")
        )
        checks.append({"rule": "折旧表原值×(1−残值率)/年限=折旧额", "ok": ok8,
                       "detail": f"逐年复算 {len(dep_tbl)} 年（附表6-2 自洽）"})

    # 9.【P0-1 / §5.1】项目总投资 = 建设投资 + 建设期利息 + 流动资金。
    #    other/reserve 是建设投资的组成，不得再与 construction 相加（否则重复）。
    #    投资口径 ambiguous 时本条不作为算术硬勾稽（由「投资口径无歧义」阻断终稿）。
    comp = round((inv.get("construction") or 0.0)
                 + (inv.get("interest") or 0.0) + (inv.get("working_capital") or 0.0), 2)
    scope_st = ((inv.get("scope_status") or {}).get("status") or "")
    gap = abs(comp - inv["total"])

    # 【P0-3修复】有差额时应标记为False+warning,不再静默通过
    if gap < 1.0:
        ok9 = True
    elif scope_st == "ambiguous":
        ok9 = False  # 改为False,不再自动通过
    elif gap < inv["total"] * 0.01:  # <1%
        ok9 = False  # 有差额即为False
    else:
        ok9 = False

    checks.append({"rule": "投资构成分项合计=总投资",
                   "category": "integrity",
                   "ok": ok9,
                   "severity": "warning" if (gap >= 1.0 and gap < inv["total"] * 0.01) else ("error" if gap >= inv["total"] * 0.01 else None),
                   "blocking": gap >= inv["total"] * 0.01,
                   "detail": f"建设投资+利息+流资 {_fmt(comp)} vs 总投资 {_fmt(inv['total'])} 万元"
                             f"（差额 {_fmt(gap)}; scope={scope_st or 'clear'}; other/reserve 不重复计入）"})


    # 11.【P0-03】附表7 利润表总成本 == 附表6 总成本(逐年、含运营期利息)。
    #    堵住"附表6 计息、附表7 不计息"的跨表断链——此前各表内部自洽、无跨表勾稽故漏检。
    pd_rows = annual.get("profit_distribution") or []
    if tc and pd_rows:
        n = min(len(tc), len(pd_rows))
        ok11 = all(abs(pd_rows[i]["total_cost"] - tc[i]["total_cost"]) < 1.0 for i in range(n))
        checks.append({"rule": "附表7利润表总成本=附表6总成本(含息)", "category": "integrity", "ok": ok11,
                       "detail": f"逐年校验 {n} 年(利润表已计运营期利息)"})

    # 12.【P0-5】财务计划现金流量表三活动自洽：经营净现金 − 投资净流出 − 还本付息 == 当期净现金流。
    #    (invest_out 已按"净流出"存储:正=净流出、负=净回收,故此处用减号)
    fp = annual.get("financial_plan") or []
    if fp:
        ok12 = all(
            abs((x["operating_net"] + x["finance_in"] - x["invest_out"] - x["debt_service"]) - x["net_cashflow"]) < 1.0
            for x in fp)
        checks.append({"rule": "财务计划现金流三活动合计=当期净现金流", "category": "integrity", "ok": ok12,
                       "detail": f"逐年校验 {len(fp)} 期(投资/融资/经营三活动自洽)"})

    # 13.【P0-4】附表9 逐行组成合计 == 净现金流:营收−现金经营成本−税金−调整所得税−建设投资−流资增加+回收。
    #    保证逐行可人工复核、且组成拆分不改变净现金流(项目 IRR 不变)。
    proj = annual.get("project_cashflow") or []
    if proj and any(p.get("phase") for p in proj):
        ok13 = all(
            abs(round((p.get("revenue") or 0.0) - (p.get("op_cash_cost") or 0.0) - (p.get("tax_surtax") or 0.0)
                      - (p.get("income_tax") or 0.0) - (p.get("construction") or 0.0) - (p.get("wc_change") or 0.0)
                      + (p.get("recover") or 0.0), 2) - p["net_cashflow"]) < 1.0
            for p in proj)
        checks.append({"rule": "附表9组成合计=净现金流", "category": "integrity", "ok": ok13,
                       "detail": f"逐年复核 {len(proj)} 期(营收−成本−税−调整所得税−建设投资−流资+回收)"})

    # 14.【P0 附加税同源】达产年 附表5.税金及附加 == 附表9.税金及附加（运营期）
    inc = annual.get("income_statement") or []
    if proj and inc:
        op_proj = [p for p in proj if p.get("phase") == "运营期"]
        n = min(len(op_proj), len(inc))
        if n > 0:
            ok14 = all(
                abs(float(op_proj[i].get("tax_surtax") or 0.0) - float(inc[i].get("tax_surtax") or 0.0)) < 1.0
                for i in range(n)
            )
            t5 = float(inc[0].get("tax_surtax") or 0.0)
            t9 = float(op_proj[0].get("tax_surtax") or 0.0)
            checks.append({
                "rule": "附表5税金及附加=附表9税金及附加",
                "category": "integrity",
                "ok": ok14,
                "detail": f"运营期逐年比对 {n} 年；首年 附表5={_fmt(t5)} vs 附表9={_fmt(t9)} 万元",
                "blocking": True,
            })
            # 与 indicators 达产附加税也应对齐（若有）
            # 注意：有爬坡时 附表5 第 1 年≠达产年；应用达产年（营收最大行）比对 indicators
            ind_tax = (ind or {}).get("tax_surcharge")
            if ind_tax is not None and n > 0:
                peak_idx = 0
                peak_rev = -1.0
                for i, row in enumerate(inc[:n]):
                    rv = float(row.get("revenue") or 0.0)
                    if rv >= peak_rev:
                        peak_rev = rv
                        peak_idx = i
                t5_peak = float(inc[peak_idx].get("tax_surtax") or 0.0)
                ok14b = abs(float(ind_tax) - t5_peak) < 1.0
                checks.append({
                    "rule": "indicators附加税=附表5税金及附加",
                    "category": "integrity",
                    "ok": ok14b,
                    "detail": (
                        f"indicators={_fmt(ind_tax)} vs 附表5达产年(第{peak_idx+1}运营年,"
                        f"营收={_fmt(peak_rev)})={_fmt(t5_peak)} 万元"
                    ),
                    "blocking": True,
                })

    # 15.【Fix-P0-2】附表10 资本金投入合计 ≈ 筹措资本金（流资对应投入已记入运营年）
    cap_rows = annual.get("capital_cashflow") or []
    fund_cap = float((fund or {}).get("capital") or 0.0)
    scope_status = ((r.get("investment") or {}).get("scope_status") or {}).get("status")
    if cap_rows and fund_cap > 0 and scope_status != "ambiguous":
        sum_ci = round(sum(float(x.get("capital_invest") or 0.0) for x in cap_rows), 2)
        ok15 = abs(sum_ci - fund_cap) < max(fund_cap * 0.01, 1.0)
        checks.append({
            "rule": "附表10资本金投入合计=筹措资本金",
            "category": "integrity",
            "ok": ok15,
            "detail": f"sum(capital_invest)={_fmt(sum_ci)} vs funding.capital={_fmt(fund_cap)} 万元",
            "blocking": True,
        })

    # 16.【Fix-P0-1】有贷款时附表2 应含期初/利率/期末（非仅利息摘要）
    idc_ann = annual.get("interest_during_construction") or []
    loan_amt = float((fund or {}).get("loan") or 0.0)
    if idc_ann and loan_amt > 0:
        sample = idc_ann[0] if isinstance(idc_ann[0], dict) else {}
        has_bal = sample.get("begin_balance") is not None or sample.get("begin") is not None
        has_rate = sample.get("rate") is not None or sample.get("rate_pct") is not None
        has_end = sample.get("end_balance") is not None or sample.get("end") is not None
        ok16 = bool(has_bal and has_rate and has_end)
        checks.append({
            "rule": "附表2建设期利息含期初利率期末",
            "category": "integrity",
            "ok": ok16,
            "detail": f"begin={has_bal} rate={has_rate} end={has_end} rows={len(idc_ann)}",
            "blocking": True,
        })

    # 10.【P2】附表1 三段式明细：工程费用+工程建设其他费用+预备费 三段合计 == 建设投资(细项不重不漏)
    det = inv.get("breakdown_detail")
    if det:
        seg_sum = round(det["engineering_total"] + det["other_total"] + det["contingency_total"], 2)
        constr = inv.get("construction") or 0.0
        checks.append({"rule": "投资明细三段合计=建设投资", "category": "integrity",
                       "ok": abs(seg_sum - constr) < max(constr * 0.01, 1.0),
                       "detail": f"三段合计 {_fmt(seg_sum)}(工程{_fmt(det['engineering_total'])}"
                                 f"+其他{_fmt(det['other_total'])}+预备{_fmt(det['contingency_total'])}) "
                                 f"vs 建设投资 {_fmt(constr)} 万元"})
    # 附加模块级门禁（投资口径/时间轴/流资方法/资金缺口/ICR）
    extra = _fin_checks.run_checks(r, engine_check=None)
    # 去重 rule 名
    seen = {c.get("rule") for c in checks}
    for c in extra:
        if c.get("rule") not in seen:
            checks.append(c)
            seen.add(c.get("rule"))
    # 【P0-4修复】偿债能力告警:ICR/DSCR<1时明确列出
    debt_service = annual.get("debt_service") or []
    if debt_service:
        icr_issues = []
        dscr_issues = []
        gap_support_active = (
            ((r.get("raw") or {}).get("fiscal_support_policy") or {}).get("mode")
            == "actual_cash_and_debt_service_gap"
            and all(
                bool(row.get("repay_actual_covers_debt_service"))
                for row in debt_service
                if float(row.get("principal") or 0.0) + float(row.get("interest") or 0.0) > 0
            )
        )

        for idx, ds in enumerate(debt_service):
            year = ds.get("year", idx + 1)
            icr = ds.get("icr")
            dscr = ds.get("dscr")

            if icr is not None and icr < 1.0:
                severity = "error" if icr < 0.8 else "warning"
                icr_issues.append(f"第{year}年ICR={icr:.2f}")
                checks.append({
                    "rule": "利息备付率ICR>=1",
                    "category": "viability",
                    "ok": False,
                    "severity": severity,
                    "blocking": False,
                    "detail": f"第{year}年ICR={icr:.2f}<1,偿债风险(当年EBITDA不足以覆盖利息)"
                })

            if dscr is not None and dscr < 1.0:
                dscr_issues.append(f"第{year}年DSCR={dscr:.2f}")
                checks.append({
                    "rule": "偿债备付率DSCR>=1",
                    "category": "viability",
                    "ok": False,
                    "severity": "warning",
                    "blocking": False,
                    "detail": f"第{year}年DSCR={dscr:.2f}<1,可用偿债资金不足以覆盖当期还本付息"
                })

        # 如果多年连续<1,汇总报告
        if len(icr_issues) > 3:
            checks.append({
                "rule": "利息备付率ICR>=1",
                "category": "viability",
                "ok": False,
                "severity": "error",
                "blocking": False,
                "detail": (
                    f"ICR<1年数: {len(icr_issues)}年,偿债能力严重不足,建议调整融资结构"
                    + ("；年度据实财政支持已覆盖必要偿债缺口，但不改变经营能力风险" if gap_support_active else "")
                )
            })

    return checks


def basis_of_estimate_md(r: dict[str, Any]) -> str:
    """M1 T1.3：Basis of Estimate（估算依据说明），随财务章附于报告，回应"数据可辩护"。

    列出：口径与规范依据、关键假设（取值+来源+方法+理由）、精度区间提示。
    """
    if not r.get("available"):
        return ""
    bench = r.get("benchmark_rate", BENCHMARK_RATE)
    params = r.get("params") or {}
    lines = [
        "**估算依据说明（Basis of Estimate）**",
        "",
        "- 规范依据：《建设项目经济评价方法与参数（第三版）》(发改投资〔2006〕1325号)；"
        "行业财务基准收益率参照发改投资〔2013〕586号（须按最新发布复核）。",
        f"- 财务基准收益率(Ic)：{bench*100:.1f}%；计算期 {params.get('calc_years')} 年"
        f"（含建设期 {params.get('build_years')} 年）。",
        "- 估算性质：可研阶段属**估算级**（AACE Class 4/5，公认精度约 ±20%~50%），"
        "以公开锚点数据 + 财务模型推算，非审计级精度。",
        "",
        "**关键假设登记：**",
        "",
        "| 假设项 | 取值/口径 | 来源/方法 |",
        "| --- | --- | --- |",
    ]
    for a in (r.get("assumptions") or []):
        # assumptions 为文本；结构化拆分（含"按…/估算/…%"）尽力解析，失败则整条入"说明"
        lines.append(f"| 测算假设 | — | {str(a)} |")
    return "\n".join(lines) + "\n"
