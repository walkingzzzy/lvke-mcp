"""年度投影：建设期利息、等额本金、年度表构造与财务计划。"""

from __future__ import annotations

from typing import Any, Optional

# P0/P1 modular finance package (方案 §8/§13)
from lvke_mcp.domains.finance import debt as _fin_debt
from lvke_mcp.domains.finance import statements as _fin_statements
from lvke_mcp.domains.finance import taxes as _fin_taxes
from lvke_mcp.domains.finance import working_capital as _fin_wc

from .base import (
    _cost_param,
    _f,
    _fmt,
    _irr,
)

from .tax import (
    _compute_vat_with_credit_carryover,
)


def _construction_interest(loan: float, rate: float, build_years: int,
                           draw_plan: Optional[list[float]] = None) -> list[dict]:
    """P1：建设期利息分年表（分年提款 + 半期计息）。

    通用可研口径：建设期借款一般假设年中支用，当年借款按半年计息，往年借款按全年计息。
      第 n 年利息 = (期初累计借款 + 当年借款/2) × 利率
    参照甲方模板 ``附表2!C8=C7*rate/2*上浮`` 的半期计息思想，但不硬编码利率/上浮，
    利率与提款计划均由参数传入；缺提款计划时按建设期均匀提款。

    返回逐年 ``{period, begin_balance, draw, rate, interest, end_balance}``。
    """
    rows: list[dict] = []
    if loan <= 0 or build_years <= 0:
        return rows
    if not draw_plan:
        per = round(loan / build_years, 2)
        draw_plan = [per] * (build_years - 1) + [round(loan - per * (build_years - 1), 2)]
    begin = 0.0
    for y in range(build_years):
        draw = draw_plan[y] if y < len(draw_plan) else 0.0
        interest = round((begin + draw / 2.0) * rate, 2)  # 半期计息：当年借款按半年
        end = round(begin + draw, 2)
        rows.append({"period": y + 1, "begin_balance": round(begin, 2), "draw": round(draw, 2),
                     "rate": rate, "interest": interest, "end_balance": end})
        begin = end
    return rows


def _equal_principal_debt(loan: float, years: int, rate: float, op_years: int,
                          *, method: str = "equal_principal", grace_years: int = 0,
                          balloon_pct: float = 0.3,
                          principal_schedule: list | None = None,
                          interest_schedule: list | None = None) -> list[dict]:
    """偿债计划（P1-4）：默认等额本金；支持等额本息/到期还本/气球/甲方还本序列。"""
    return _fin_debt.build_debt_schedule(
        loan, years, rate, op_years,
        method=method or "equal_principal",
        grace_years=grace_years or 0,
        balloon_pct=balloon_pct,
        principal_schedule=principal_schedule,
        interest_schedule=interest_schedule,
    )


def _build_annual(r: dict[str, Any]) -> dict[str, Any]:
    """基于已算 indicators/operating 生成逐年结构化附表（达产恒定简化）。"""
    inv, fund, params = r["investment"], r["funding"], r["params"]
    ind = r.get("indicators") or {}
    op = r.get("operating") or {}
    build = int(params["build_years"])
    calc = int(params["calc_years"])
    op_years = max(calc - build, 1)
    loan = fund["loan"]
    rate = fund["loan_rate"]
    loan_years = int(fund["loan_years"])

    _method = (params.get("loan_repay_method") or (r.get("params") or {}).get("loan_repay_method")
               or ((r.get("raw") or {}).get("loan_repay_method")) or "equal_principal")
    _grace = int((r.get("params") or {}).get("loan_grace_years") or 0)
    _prin_sched = (r.get("raw") or {}).get("loan_principal_by_year") or (
        (r.get("params") or {}).get("loan_principal_by_year")
    )
    _int_sched = (r.get("raw") or {}).get("loan_interest_by_year")
    if _prin_sched:
        _method = "principal_schedule"
    debt = _equal_principal_debt(
        loan, loan_years, rate, op_years, method=str(_method), grace_years=_grace,
        balloon_pct=float((r.get("raw") or {}).get("loan_balloon_pct") or 0.3),
        principal_schedule=list(_prin_sched) if _prin_sched else None,
        interest_schedule=list(_int_sched) if _int_sched else None,
    )
    annual: dict[str, Any] = {"debt_service": debt}

    raw = r.get("raw") or {}
    if ind:  # 经营性项目
        def _annual_consumption_tax(index: int = 0) -> float:
            value = raw.get("consumption_tax_payable_wan")
            if isinstance(value, (list, tuple)):
                if not value:
                    return 0.0
                return max(float(value[min(index, len(value) - 1)] or 0.0), 0.0)
            return max(float(value or 0.0), 0.0)

        revenue = ind.get("revenue") or 0.0
        total_cost = ind.get("op_cost") or 0.0      # 现有口径：总成本费用（含折旧）
        dep_charge = ind.get("depreciation") or 0.0  # 现有单一非现金摊提额（折旧口径）
        tax_sur = ind.get("tax_surcharge") or 0.0
        vat_output = ind.get("vat_output") or 0.0    # PG5-a 附表5：销项税
        vat_input = ind.get("vat_input") or 0.0      # 进项税
        vat_payable = ind.get("vat_payable") or 0.0  # 应纳增值税
        op_cash_cost = round(total_cost - dep_charge, 2)   # 现金经营成本（不含折旧摊销）
        # BC-P2：逐年 P&L（spec 非 flat 时非空）。附表5收入/6总成本/7利润改按 pnl_by_year 逐年填，
        # 达产年与现状一致；pnl_by_year=None（flat/单点）时各年恒为达产值，字节级不变（老测试全绿）。
        pnl = (r.get("operating") or {}).get("pnl_by_year") or None

        # 【M1 修复】附表6-2/6-3 折旧、摊销独立取值（compute_financials 已按各自基数算好并透传），
        # 不再用 dep_charge − amort 反拆（反拆会让无形资产既进折旧基数又单独摊销，两表不自洽）。
        # 折旧+摊销 == dep_charge（非现金摊提合计），total_cost / 利润 / IRR 口径不变。
        intangible = raw.get("intangible_wan")
        amort_years = int(raw.get("amortization_years") or 10)
        if raw.get("dep_only") is not None:
            dep = round(raw.get("dep_only") or 0.0, 2)       # 附表6-2 折旧（基数已扣无形、扣残值）
            amort = round(raw.get("amort_only") or 0.0, 2)   # 附表6-3 摊销（无形/摊销年限）
        else:
            # 兜底（理论不走到：operating 块一定已透传）：退回原反拆逻辑
            amort = 0.0
            if intangible and intangible > 0 and amort_years > 0:
                amort = round(min(intangible / amort_years, dep_charge), 2)
            dep = round(dep_charge - amort, 2)

        # P0 附表6-1：工资及附加。优先 cost_items["工资及福利"]/raw.wage_wan，否则按经营成本占比估。
        # Fix-P1-1：键名已含「福利/附加」时视为总额含附加，内拆而非再 ×(1+r)。
        cost_items = raw.get("cost_items") or {}
        wage = raw.get("wage_wan")
        # Public contract defines wage_wan as the annual total including
        # welfare/surcharges.  Treating it as base salary and applying the
        # welfare rate again inflated table 6-1 and broke the cost-detail tie.
        wage_key_includes_welfare = wage is not None
        if wage is None and isinstance(cost_items, dict):
            for _k, _v in cost_items.items():
                _ks = str(_k)
                if "工资" in _ks or "薪" in _ks or "人工" in _ks:
                    wage = _f(_v)
                    if "福利" in _ks or "附加" in _ks:
                        wage_key_includes_welfare = True
                    break
        # BC-P3：工资占比/福利率/所得税率三级取值（spec.cost → config → 兜底=原硬编码 0.15/0.14/0.25）。
        _spec = r.get("spec")
        _wage_rate = _cost_param(_spec, "wage_rate", "cost")       # 原硬编码 0.15
        _welfare_rate = _cost_param(_spec, "welfare_rate", "cost")  # 原硬编码 0.14
        _income_tax_rate = _cost_param(_spec, "income_tax_rate", "tax")  # 原硬编码 0.25
        wage_estimated = False
        negative_cash_cost = op_cash_cost < -0.005
        if negative_cash_cost:
            r.setdefault("blocking_issues", []).append({
                "rule": "negative_operating_cost",
                "detail": (
                    "达产年总成本费用小于折旧/摊销，无法推导非负现金经营成本；"
                    "须显式补充 annual_operating_cost_wan、cost_items 或 operating_cost_by_year"
                ),
            })
        if wage is None:
            # BC-4a 房产：工资应按期间费用估，不得把开发成本结转额计入基数（否则虚高）。
            _wage_base = op_cash_cost
            if raw.get("property_inventory") and raw.get("period_opex") is not None:
                _wage_base = float(raw.get("period_opex") or 0.0)
            # 负现金成本由 run 固化门禁阻断；这里不继续扩散成负工资。
            wage = 0.0 if negative_cash_cost else round(_wage_base * _wage_rate, 2)
            wage_estimated = True
        wage = round(max(min(float(wage or 0.0), max(op_cash_cost, 0.0)), 0.0), 2)
        if wage_key_includes_welfare and not wage_estimated and _welfare_rate > 0:
            # 输入已是「工资及福利」总额：内拆，禁止 total 膨胀到 输入×(1+r)
            wage_total = wage
            wage = round(wage_total / (1.0 + float(_welfare_rate)), 2)
            welfare = round(wage_total - wage, 2)
        else:
            welfare = round(wage * float(_welfare_rate), 2)  # 职工福利/附加按工资占比简化

        income_rows, cost_rows, profit_rows = [], [], []
        wage_rows, dep_rows, amort_rows = [], [], []
        # 【M1 修复】折旧表原值用折旧基数（固定资产扣无形），使表内"原值×(1−残值率)/年限"复算 == 折旧额。
        # BC-4a 房产：无固定资产折旧，附表6-2 各年折旧=0、原值=0。
        _is_property_inv = bool(raw.get("property_inventory"))
        fixed_base = raw.get("dep_base")
        if fixed_base is None:
            fixed_base = 0.0 if _is_property_inv else (r["investment"].get("fixed_asset") or 0.0)
        salvage_rate = raw.get("salvage_rate") if raw.get("salvage_rate") is not None else (0.0 if _is_property_inv else 0.05)
        dep_years = int(raw.get("depreciation_years") or (0 if _is_property_inv else op_years))
        _classified_dep_rows = (
            list(raw.get("depreciation_class_schedule") or [])
            if isinstance(raw.get("depreciation_class_schedule"), list) else []
        )
        if _is_property_inv:
            dep = 0.0  # 强制：开发产品不折旧
        # 【P1-3】逐年增值税含留抵结转（方案 §6.5）：先按各年营收比例缩放销项/进项，再走留抵滚动
        #   账户——当期进项抵不完的部分结转下期，不逐年独立 max。无留抵结余时与旧 vat_payable 一致。
        _vat_out_seq: list[float] = []
        _vat_in_seq: list[float] = []
        for y in range(op_years):
            py = pnl[y] if (pnl and y < len(pnl)) else None
            _rev_v = py["revenue"] if py else revenue
            _ratio_v = (_rev_v / revenue) if revenue else 1.0
            _vat_out_seq.append(round(vat_output * _ratio_v, 2))
            _vat_in_seq.append(round(vat_input * _ratio_v, 2))
        _vat_series = _compute_vat_with_credit_carryover(_vat_out_seq, _vat_in_seq)
        _vat_had_credit = any(vr["credit_end"] > 0 for vr in _vat_series)
        for y in range(op_years):
            interest = debt[y]["interest"] if y < len(debt) else 0.0
            # 【P1-2】寿命内计提、期满归零（禁止静默延长寿命）；表内按年真实滚动。
            dep_y = (
                round(float(_classified_dep_rows[y].get("depreciation") or 0.0), 2)
                if y < len(_classified_dep_rows)
                else round(dep, 2) if (y + 1) <= max(dep_years, 0) else 0.0
            )
            amort_y = round(amort, 2) if (y + 1) <= max(amort_years, 0) else 0.0
            # BC-P2：spec 非 flat 时逐年取该年 P&L，否则用达产恒定值（现状口径不变）。
            py = pnl[y] if (pnl and y < len(pnl)) else None
            rev_y = py["revenue"] if py else revenue
            occ_y = py["op_cash_cost"] if py else op_cash_cost   # 该年现金经营成本
            tax_sur_y = py["tax_surcharge"] if py else tax_sur
            surtax_component_y = None
            # P1-3：若启用附加税以应纳增值税为基数（finance.surtax_on_vat=true），覆盖当年附加税
            if bool((r.get("raw") or {}).get("surtax_on_vat")) or bool((r.get("params") or {}).get("surtax_on_vat")):
                _vp = float((_vat_series[y]["payable"] if y < len(_vat_series) else vat_payable) or 0.0)
                # vat_add_rate 在简化路径是「营收附加率」；VAT 基数优先使用所在地法定分项。
                _policy = (r.get("raw") or {}).get("surtax_component_policy") or {}
                if _policy.get("mode") == "statutory_components":
                    surtax_component_y = _fin_taxes.surtax_components_from_tax_payable(
                        [_vp],
                        consumption_tax_by_year=[_annual_consumption_tax(y)],
                        urban_maintenance_rate=float(_policy["urban_maintenance_rate"]),
                        education_surcharge_rate=float(_policy["education_surcharge_rate"]),
                        local_education_surcharge_rate=float(_policy["local_education_surcharge_rate"]),
                    )[0]
                    tax_sur_y = surtax_component_y["total"]
                else:
                    _srate = float((r.get("raw") or {}).get("surtax_vat_rate") or 0.12)
                    tax_sur_y = round(max(_vp, 0.0) * _srate, 2)
            # 逐年表是附表和审查的唯一会计数字源。P&L 预投影可能使用
            # 平均折旧或旧税费口径，因此在分类折旧和法定附加税已解析后必须
            # 从同一年的收入、现金成本、摊提和税费重算，不得沿用旧 profit_before。
            tc_y = round(occ_y + dep_y + amort_y, 2)
            pb_y = round(rev_y - tc_y - tax_sur_y, 2)
            if py and float(py.get("profit_before") or 0.0) > 0:
                # 保留免税/减半等逐年有效税率，仅用新税前利润替换税基。
                tax_rate_y = max(
                    float(py.get("income_tax") or 0.0)
                    / float(py.get("profit_before") or 1.0),
                    0.0,
                )
            else:
                tax_rate_y = _income_tax_rate
            it_y = round(max(pb_y, 0.0) * tax_rate_y, 2)
            np_y = round(pb_y - it_y, 2)
            # 【P0-03 修复】附表7 利润表为融资后会计口径:利润总额须扣运营期利息(计入财务费用),
            #   据此重算实际所得税与会计净利润。indicators/附表9 项目投资现金流为融资前口径
            #   (利息不进融资前现金流),两套口径分离、互不污染——故此改动不影响 IRR/NPV/资本金IRR。
            #   有效税率优先按融资前该年实际税率(捕捉免税/减半优惠),缺 pnl 时用基准所得税率。
            eff_rate_y = (it_y / pb_y) if (py and pb_y > 0) else _income_tax_rate
            pb_acct = round(pb_y - interest, 2)                    # 会计利润总额(已扣运营期利息)
            it_acct = round(max(pb_acct, 0.0) * eff_rate_y, 2)     # 实际所得税(按会计利润)
            np_acct = round(pb_acct - it_acct, 2)                  # 会计净利润
            surplus_y = round(max(np_acct, 0.0) * 0.10, 2)         # 法定盈余公积(按会计净利润 10%)
            # 【P1-3】逐年增值税取留抵结转序列（当期销项先冲上期留抵再冲当期进项，抵不完结转下期）。
            _vrow = _vat_series[y] if y < len(_vat_series) else {"output": vat_output, "input_used": vat_input, "payable": vat_payable, "credit_end": 0.0}
            income_rows.append({
                "year": y + 1, "revenue": rev_y, "operating_cost": occ_y,
                "depreciation": dep_y, "tax_surtax": tax_sur_y,
                "vat_output": _vrow["output"], "vat_input": _vrow["input_used"],
                "vat_payable": _vrow["payable"], "vat_credit_end": _vrow["credit_end"],
                "consumption_tax_payable": (
                        surtax_component_y.get("consumption_tax_payable")
                        if surtax_component_y else _annual_consumption_tax(y)
                ),
                "surtax_tax_base": (
                    surtax_component_y.get("tax_base") if surtax_component_y else None
                ),
                "urban_maintenance_tax": (
                    surtax_component_y.get("urban_maintenance_tax")
                    if surtax_component_y else None
                ),
                "education_surcharge": (
                    surtax_component_y.get("education_surcharge")
                    if surtax_component_y else None
                ),
                "local_education_surcharge": (
                    surtax_component_y.get("local_education_surcharge")
                    if surtax_component_y else None
                ),
                "ebit": pb_y, "income_tax": it_y, "net_profit": np_y,
            })
            cost_rows.append({
                "year": y + 1, "operating_cost": occ_y, "depreciation": dep_y,
                "amortization": amort_y, "interest": interest,
                "total_cost": round(occ_y + dep_y + amort_y + interest, 2),
            })
            profit_rows.append({
                # total_cost 与附表6 同源:现金经营成本+折旧+摊销+利息。
                "year": y + 1, "revenue": rev_y,
                "total_cost": round(occ_y + dep_y + amort_y + interest, 2),
                "tax_surtax": tax_sur_y, "total_profit": pb_acct, "income_tax": it_acct,
                "net_profit": np_acct, "surplus_reserve": surplus_y,
                "undistributed": round(np_acct - surplus_y, 2),
            })
            # 附表6-1 工资及附加逐年
            wage_rows.append({"year": y + 1, "wage": wage, "welfare": welfare,
                              "total": round(wage + welfare, 2)})
            # 附表6-2 折旧逐年：寿命内计提，期满为 0（P1-2 不静默延长）。
            # 表内"原值×(1−残值率)/折旧年限"在计提年可复算 == 当期折旧费。
            if y < len(_classified_dep_rows):
                class_row = _classified_dep_rows[y]
                cumulative_dep = round(
                    float(class_row.get("cumulative_depreciation") or 0.0), 2
                )
                dep_rows.append({
                    "year": y + 1,
                    "original_value": round(fixed_base, 2),
                    "salvage_rate": salvage_rate,
                    "dep_years": dep_years,
                    "depreciation": dep_y,
                    "cumulative_depreciation": cumulative_dep,
                    "net_value": round(fixed_base - cumulative_dep, 2),
                    "depreciation_basis": "classified",
                    "classes": list(class_row.get("classes") or []),
                })
            else:
                cumulative_dep = round(sum(
                    dep if j < dep_years else 0.0 for j in range(y + 1)
                ), 2)
                cumulative_dep = min(cumulative_dep, round(fixed_base * (1 - salvage_rate), 2))
                dep_rows.append({"year": y + 1, "original_value": round(fixed_base, 2),
                                 "salvage_rate": salvage_rate, "dep_years": dep_years,
                                 "depreciation": dep_y, "cumulative_depreciation": cumulative_dep,
                                 "net_value": round(fixed_base - cumulative_dep, 2)})
            # 附表6-3 摊销逐年：寿命内计提，期满为 0。
            amort_rows.append({"year": y + 1, "base": round(intangible or 0.0, 2),
                               "amort_years": amort_years,
                               "amortization": amort_y})
            # DSCR = CFADS / 偿债额 = (会计净利润+折旧+摊销+利息) / (还本+付息)。
            # 会计净利润已扣息,加回利息还原为可用于偿债的现金流;原码用未扣息净利润→利息被加两次、DSCR 偏高。
            due = (debt[y]["principal"] + debt[y]["interest"]) if y < len(debt) else 0.0
            cogs_addback_y = float(py.get("inventory_cogs_addback") or 0.0) if py else 0.0
            avail = np_acct + dep_y + amort_y + interest + cogs_addback_y
            if y < len(debt):
                debt[y]["dscr"] = round(avail / due, 2) if due > 0 else None
                # ICR = EBITDA近似 / 利息（EBIT+折旧摊销）/ interest
                ebitda = round(pb_acct + interest + dep_y + amort_y, 2)
                debt[y]["icr"] = round(ebitda / interest, 2) if interest > 0 else None

                # 【P0-1修复】补充偿债资金来源分项(附表8展示用)
                # 来源 = 会计净利润 + 折旧 + 摊销(按制造业/经营类规则)
                debt[y]["repay_source_profit"] = round(np_acct, 2)
                debt[y]["repay_source_dep"] = round(dep_y, 2)
                debt[y]["repay_source_amort"] = round(amort_y, 2)
        annual["income_statement"] = income_rows
        annual["total_cost"] = cost_rows
        annual["profit_distribution"] = profit_rows
        annual["wage"] = wage_rows            # 附表6-1
        annual["depreciation_table"] = dep_rows   # 附表6-2
        annual["amortization_table"] = amort_rows  # 附表6-3
        # Confirmed repayment sources distinguish available capacity from the
        # amount actually consumed.  DSCR uses available capacity; actual use is
        # allocated pro-rata and must close to current debt service.
        source_facts = raw.get("debt_repay_sources") or []
        if isinstance(source_facts, list) and source_facts:
            def _source_kind(name: Any) -> str:
                text = str(name or "").lower()
                if "利润" in text or "profit" in text:
                    return "profit"
                if "折旧" in text or "depreciation" in text:
                    return "depreciation"
                if "摊销" in text or "amort" in text:
                    return "amortization"
                return ""

            for y, debt_row in enumerate(debt):
                bases = {
                    "profit": max(float((profit_rows[y] if y < len(profit_rows) else {}).get("distributable") or 0.0), 0.0),
                    "depreciation": max(float((dep_rows[y] if y < len(dep_rows) else {}).get("depreciation") or 0.0), 0.0),
                    "amortization": max(float((amort_rows[y] if y < len(amort_rows) else {}).get("amortization") or 0.0), 0.0),
                }
                available_parts = {key: 0.0 for key in bases}
                for fact in source_facts:
                    if not isinstance(fact, dict):
                        continue
                    kind = _source_kind(fact.get("name") or fact.get("source"))
                    if not kind:
                        continue
                    schedule = fact.get("annual_schedule_wan") or fact.get("schedule_wan")
                    if isinstance(schedule, list) and y < len(schedule):
                        value = float(schedule[y] or 0.0)
                    elif fact.get("annual_wan") is not None:
                        value = float(fact.get("annual_wan") or 0.0)
                    else:
                        share = float(fact.get("share") or 0.0)
                        share = share / 100.0 if abs(share) > 1.0 else share
                        value = bases[kind] * share
                    available_parts[kind] += max(value, 0.0)
                available = round(sum(available_parts.values()), 2)
                due = round(float(debt_row.get("principal") or 0.0) + float(debt_row.get("interest") or 0.0), 2)
                actual = round(min(available, due), 2)
                actual_parts = {key: 0.0 for key in available_parts}
                if available > 0 and actual > 0:
                    keys = list(actual_parts)
                    for key in keys[:-1]:
                        actual_parts[key] = round(actual * available_parts[key] / available, 2)
                    actual_parts[keys[-1]] = round(actual - sum(actual_parts[key] for key in keys[:-1]), 2)
                debt_row.update({
                    "repay_available_profit": round(available_parts["profit"], 2),
                    "repay_available_depreciation": round(available_parts["depreciation"], 2),
                    "repay_available_amortization": round(available_parts["amortization"], 2),
                    "repay_available": available,
                    "repay_actual_profit": actual_parts["profit"],
                    "repay_actual_depreciation": actual_parts["depreciation"],
                    "repay_actual_amortization": actual_parts["amortization"],
                    "repay_actual": actual,
                    "repay_surplus": round(available - actual, 2),
                    "repay_actual_covers_debt_service": abs(actual - due) <= 0.05,
                    "dscr": round(available / due, 2) if due > 0 else None,
                })
        if wage_estimated:
            r.setdefault("assumptions", []).append(
                f"工资及附加缺输入，按现金经营成本 15% 估为 {_fmt(wage)} 万元/年（附表6-1，可研简化）")
        if amort > 0:
            r.setdefault("assumptions", []).append(
                f"无形及其他资产摊销 {_fmt(amort)} 万元/年（附表6-3，自非现金摊提额中拆分，不改变总成本口径）")

    # 【P0-4】附表9 项目投资现金流：逐行组成(可人工复核),net 仍取 op.cashflows 保证项目 IRR 三处一致。
    #   运营期净现金流 = 营业收入 − 现金经营成本 − 税金及附加 − 调整所得税(融资前口径,与P0-03会计所得税分开)
    #                    − 流动资金增加(投产首年) + 流动资金回收+资产余值(末年);组成合计 == cfs[t](rule#13验证)。
    #   融资前口径:不含借款提款/还本/融资利息(利息影响仅体现在附表7会计利润与附表10资本金现金流)。
    cfs = list(op.get("cashflows") or [])
    inc = annual.get("income_statement") or []
    _wc = round(inv["working_capital"] or 0.0, 2)
    # 【P1-03】期末资产回收统一读 compute_financials 存的 terminal_recovery(残值+未折完账面净值),
    #   与现金流末年口径一致(rule#13 组成合计==净现金流)。缺失时(理论不走到)退回原 ×残值率。
    _salv = raw.get("terminal_recovery")
    if _salv is None:
        _salv_rate = raw.get("salvage_rate")
        _salv_rate = 0.05 if _salv_rate is None else _salv_rate
        _salv = round((inv.get("fixed_asset") or 0.0) * _salv_rate, 2)
    proj_rows, cum = [], 0.0
    for t, cf in enumerate(cfs):
        cum = round(cum + cf, 2)
        row: dict[str, Any] = {"year": t, "net_cashflow": round(cf, 2), "cumulative": cum}
        if t < build:
            row.update({"phase": "建设期", "revenue": 0.0, "op_cash_cost": 0.0,
                        "tax_surtax": 0.0, "income_tax": 0.0, "construction": round(-cf, 2),
                        "wc_change": 0.0, "recover": 0.0})
        else:
            j = t - build
            ir = inc[j] if j < len(inc) else {}
            wc_add = _wc if j == 0 else 0.0
            _wc_end = raw.get("terminal_wc_recovery")
            if _wc_end is None:
                _wc_end = _wc
            else:
                _wc_end = round(float(_wc_end or 0.0), 2)
            rec = round(float(_wc_end) + float(_salv or 0.0), 2) if j == op_years - 1 else 0.0
            # 附表9 融资前组成须与 op.cashflows 同源：达产简化用 indicators 平均摊提口径；
            # 收入表 ir 为会计/年表（寿命内真实折旧），可能与平均口径差 1–数万元。
            # 组成列优先 indicators，保证 rule#13 与项目 IRR 三处一致。
            _rev = float(
                ir.get("revenue")
                if ir.get("revenue") is not None
                else ((ind or {}).get("revenue") or 0.0)
            )
            _occ = float(ir.get("operating_cost") or 0.0)
            if bool(raw.get("property_inventory")):
                # 开发成本结转已在建设期支付，附表9项目现金流只列当期现金期间费用。
                _occ = float(raw.get("period_opex") or 0.0)
            if not _occ and ind:
                _occ = float((ind or {}).get("op_cost") or 0.0) - float((ind or {}).get("depreciation") or 0.0)
            # 【P0 附加税同源】优先用附表5 当年 tax_surtax，再退 indicators（已与增值税附加同源）
            _tax_s = float(
                ir.get("tax_surtax")
                if ir.get("tax_surtax") is not None
                else ((ind or {}).get("tax_surcharge") or 0.0)
            )
            # 调整所得税：优先附表5 融资前所得税；否则反推使组成恒等
            _net_op = float(cf)
            if j == 0:
                _net_op = round(_net_op + _wc, 2)  # 还原投产年流资投入
            if j == op_years - 1:
                # The terminal cash flow only includes the explicitly resolved
                # recovery amount.  A vendor workbook may intentionally set
                # working-capital recovery to zero, so subtracting the full
                # working-capital balance here breaks the table-9 composition.
                _net_op = round(_net_op - float(_wc_end) - float(_salv or 0.0), 2)
            if ir.get("income_tax") is not None:
                _adj_tax = round(float(ir.get("income_tax") or 0.0), 2)
                # 若用表内所得税，组成可能与 cf 有 1 元级差——以 net 为准微调附加税列不改；
                # 组成校验用反推所得税保持 rule#13（净现金流契约优先）
                _compose_tax = round(_rev - _occ - _tax_s - _net_op, 2)
                # 展示用融资前所得税；rule#13 用使组成恒等的值
                _adj_tax_display = _adj_tax
                _adj_tax = _compose_tax
            else:
                _adj_tax = round(_rev - _occ - _tax_s - _net_op, 2)
                _adj_tax_display = _adj_tax
            row.update({"phase": "运营期",
                        "revenue": round(_rev, 2),
                        "op_cash_cost": round(_occ, 2),
                        "tax_surtax": round(_tax_s, 2),
                        "income_tax": _adj_tax,  # 与净现金流恒等的调整所得税（rule#13）
                        "income_tax_financing_before": _adj_tax_display,  # 附表5 同源展示
                        "construction": 0.0, "wc_change": round(wc_add, 2), "recover": rec})
        proj_rows.append(row)
    annual["project_cashflow"] = proj_rows

    # 附表10按投资实际发生期展开：建设期资本金=建设支出-贷款提款；
    # 投产年资本金=当年流动资金增加。每年均可由组成项直接复算净现金流。
    _equity_inject_plan: list[float] = []
    _eq_raw = (raw.get("equity_inject_by_year") if isinstance(raw, dict) else None) or []
    if isinstance(_eq_raw, (list, tuple)) and any(float(x or 0) for x in _eq_raw):
        _equity_inject_plan = [round(float(x or 0.0), 2) for x in _eq_raw]
        if build > 0 and len(_equity_inject_plan) < build:
            _equity_inject_plan = _equity_inject_plan + [0.0] * (build - len(_equity_inject_plan))
    _loan_draw_plan: list[float] = []
    _loan_draws = (raw.get("loan_draw_by_year") if isinstance(raw, dict) else None) or []
    if not _loan_draws:
        _loan_draws = ((r.get("raw") or {}).get("loan_draw_by_year") or [])
    if isinstance(_loan_draws, (list, tuple)) and any(float(x or 0) for x in _loan_draws):
        _loan_draw_plan = [round(float(x or 0.0), 2) for x in _loan_draws]
        if build > 0 and len(_loan_draw_plan) < build:
            _loan_draw_plan = _loan_draw_plan + [0.0] * (build - len(_loan_draw_plan))
        elif build > 0 and len(_loan_draw_plan) > build:
            _loan_draw_plan = _loan_draw_plan[: build - 1] + [round(sum(_loan_draw_plan[build - 1 :]), 2)]
    loan_draw_per = round(loan / build, 2) if (loan > 0 and build > 0) else 0.0
    subsidy = float(fund.get("subsidy") or 0.0)
    if _equity_inject_plan:
        expected_build_equity = round(max(
            float(inv.get("construction") or 0.0)
            + float(inv.get("interest") or 0.0)
            - loan
            - subsidy,
            0.0,
        ), 2)
        planned_build_equity = round(sum(_equity_inject_plan), 2)
        excess = round(planned_build_equity - expected_build_equity, 2)
        # Some confirmed funding schedules report total project equity in the
        # construction-year row, including working capital that is not used
        # until operation starts. Rephase only the exactly explainable WC
        # amount; any other excess remains visible to consistency checks.
        if (
            excess > 0.01
            and abs(excess - float(inv.get("working_capital") or 0.0)) <= 0.01
        ):
            remaining = excess
            for index in range(len(_equity_inject_plan) - 1, -1, -1):
                reduction = min(_equity_inject_plan[index], remaining)
                _equity_inject_plan[index] = round(
                    _equity_inject_plan[index] - reduction,
                    2,
                )
                remaining = round(remaining - reduction, 2)
                if remaining <= 0:
                    break
            r.setdefault("assumptions", []).append(
                "资金计划建设期资本金含流动资金，已按实际使用时点重分期至投产年"
            )
    subsidy_draw_per = round(subsidy / build, 2) if (subsidy > 0 and build > 0) else 0.0
    capital_rows = []
    for t, cf in enumerate(cfs):
        if t < build:
            if _loan_draw_plan:
                draw = _loan_draw_plan[t] if t < len(_loan_draw_plan) else 0.0
            else:
                draw = loan_draw_per if t < build - 1 else round(loan - loan_draw_per * (build - 1), 2)
            subsidy_draw = (
                subsidy_draw_per
                if t < build - 1
                else round(subsidy - subsidy_draw_per * (build - 1), 2)
            )
            build_outlay = round(max(-float(cf), 0.0), 2)
            if _equity_inject_plan:
                capital_invest = float(_equity_inject_plan[t]) if t < len(_equity_inject_plan) else 0.0
            else:
                capital_invest = round(max(build_outlay - draw - subsidy_draw, 0.0), 2)
            adj = round(-capital_invest, 2)
            capital_rows.append({
                "year": t, "phase": "建设期",
                "capital_invest": capital_invest,
                "loan_draw": round(draw, 2),
                "subsidy_draw": round(subsidy_draw, 2),
                "revenue": 0.0,
                "recover_fixed": 0.0,
                "recover_wc": 0.0,
                "op_cash_cost": 0.0,
                "tax_surtax": 0.0,
                "income_tax": 0.0,
                # Compatibility only.  This is now the atomic cash-inflow total,
                # never the former opaque/net-style operating inflow.
                "op_inflow": 0.0, "principal": 0.0, "interest": 0.0,
                "cash_inflow": 0.0, "cash_outflow": round(-adj, 2),
                "net_cashflow": round(adj, 2),
            })
        else:
            y = t - build
            principal = round(debt[y]["principal"], 2) if y < len(debt) else 0.0
            interest = round(debt[y]["interest"], 2) if y < len(debt) else 0.0
            project_row = proj_rows[t] if t < len(proj_rows) else {}
            capital_invest = round(max(float(project_row.get("wc_change") or 0.0), 0.0), 2)
            revenue_y = round(float(project_row.get("revenue") or 0.0), 2)
            op_cash_cost_y = round(float(project_row.get("op_cash_cost") or 0.0), 2)
            tax_surtax_y = round(float(project_row.get("tax_surtax") or 0.0), 2)
            income_tax_y = round(float(project_row.get("income_tax") or 0.0), 2)
            is_terminal = y == op_years - 1
            recover_fixed = round(float(_salv or 0.0), 2) if is_terminal else 0.0
            terminal_wc = raw.get("terminal_wc_recovery")
            if terminal_wc is None:
                terminal_wc = _wc
            recover_wc = round(float(terminal_wc or 0.0), 2) if is_terminal else 0.0
            cash_inflow = round(revenue_y + recover_fixed + recover_wc, 2)
            cash_outflow = round(
                capital_invest + op_cash_cost_y + tax_surtax_y + income_tax_y
                + principal + interest,
                2,
            )
            adj = round(cash_inflow - cash_outflow, 2)
            capital_rows.append({
                "year": t, "phase": "运营期",
                "capital_invest": capital_invest, "loan_draw": 0.0,
                "subsidy_draw": 0.0,
                "revenue": revenue_y,
                "recover_fixed": recover_fixed,
                "recover_wc": recover_wc,
                "op_cash_cost": op_cash_cost_y,
                "tax_surtax": tax_surtax_y,
                "income_tax": income_tax_y,
                # Legacy alias only; formal consumers use cash_inflow/outflow
                # and the atomic components below.
                "op_inflow": round(cash_inflow - op_cash_cost_y - tax_surtax_y - income_tax_y, 2),
                "cash_inflow": cash_inflow,
                "cash_outflow": cash_outflow,
                "principal": principal,
                "interest": interest, "net_cashflow": round(adj, 2),
                "capital_invest_note": "流动资金增加对应的资本金投入" if capital_invest else "",
            })
    annual["capital_cashflow"] = capital_rows
    try:
        annual["capital_irr_pct"] = round(_irr([x["net_cashflow"] for x in capital_rows]) * 100, 2)
    except Exception:  # noqa: BLE001
        annual["capital_irr_pct"] = None

    # 建设期利息分年：优先用 P1 半期计息明细（raw.idc_rows），缺失时退简化均摊
    # Fix-P0-1：必须透传 begin_balance/rate/end_balance（勿用 begin/rate_pct/end 空键）
    idc_rows = (r.get("raw") or {}).get("idc_rows") or []
    if idc_rows:
        annual["interest_during_construction"] = [
            {
                "period": x.get("period"),
                "begin_balance": x.get("begin_balance", x.get("begin")),
                "draw": x.get("draw"),
                "rate": x.get("rate", x.get("rate_pct")),
                "interest": x.get("interest"),
                "end_balance": x.get("end_balance", x.get("end")),
                "calculation_basis": "half_year_average_balance",
            }
            for x in idc_rows
        ]
    else:
        # 降级路径也必须给出可复核滚动账户，不能只放一列利息。
        _loan_rate = float(
            raw.get("loan_rate")
            or (r.get("funding") or {}).get("loan_rate")
            or (r.get("finance_inputs") or {}).get("loan_rate")
            or 0.0
        )
        _draw_per = round(float(loan or 0.0) / max(build, 1), 2)
        _begin = 0.0
        _idc_total = float(inv.get("interest") or 0.0)
        _rows = []
        for y in range(build):
            _draw = _draw_per if y < build - 1 else round(float(loan or 0.0) - _draw_per * (build - 1), 2)
            _interest = round(_idc_total / max(build, 1), 2)
            if y == build - 1:
                _interest = round(_idc_total - sum(float(x["interest"]) for x in _rows), 2)
            _end = round(_begin + _draw, 2)
            _rows.append({
                "period": y + 1, "begin_balance": round(_begin, 2), "draw": _draw,
                "rate": _loan_rate, "interest": _interest, "end_balance": _end,
                "calculation_basis": "explicit_interest_schedule",
            })
            _begin = _end
        annual["interest_during_construction"] = _rows
    # 附表3 流动资金：优先周转天数法（P1-1）；否则汇总反解并标记 method=ratio_backsolve。
    wc_total = round(inv["working_capital"] or 0.0, 2)
    _rev_base = float((ind or {}).get("revenue") or 0.0)
    _cash_cost_base = 0.0
    if ind:
        _cash_cost_base = round(float(ind.get("op_cost") or 0.0) - float(ind.get("depreciation") or 0.0), 2)
    _wc_days = (raw or {}).get("wc_turnover_days")
    annual["working_capital"] = _fin_wc.build_working_capital(
        wc_total=wc_total,
        revenue=_rev_base,
        cash_cost=_cash_cost_base,
        wc_turnover_days=_wc_days,
        turnover=(raw or {}).get("wc_turnover") if isinstance((raw or {}).get("wc_turnover"), dict) else None,
    )
    # 投资估算流资总额与周转法数值显式并列；差异由 run 固化前
    # 的 consistency gate 阻断，禁止强制缩放或静默抹平。
    if annual["working_capital"].get("method") == "turnover_days":
        annual["working_capital"]["investment_total"] = wc_total
    else:
        # 反解路径：净额强制等于投资估算流资（勾稽不变）
        annual["working_capital"]["total"] = wc_total
        annual["working_capital"]["net_working_capital"] = wc_total

    # 【P0-5 / F13-P0-04】财务计划现金流量表(C03 控制表,不占 13 张交付编号)：
    #   逐年汇总 投资/融资/经营 三类活动现金流,输出期末现金、累计盈余资金与资金缺口,
    #   判断项目各期能否正常运营、资金链是否安全。仅经营性项目构造(非经营性走全生命周期资金平衡)。
    annual["financial_plan"] = _build_financial_plan(r, annual, debt)

    # 【P1-6】非经营性：全生命周期资金平衡表（控制表，不占 13 表编号）
    if not ind:
        fund = r.get("funding") or {}
        annual_opex = round(sum(
            float(value or 0.0) for value in ((raw or {}).get("cost_items") or {}).values()
        ), 2)
        annual["non_operating_balance"] = _fin_statements.non_operating_funding_balance(
            total_investment=float(inv.get("total") or 0.0),
            capital=float(fund.get("capital") or 0.0),
            loan=float(fund.get("loan") or 0.0),
            subsidy=float(fund.get("subsidy") or 0.0),
            annual_opex=annual_opex,
            annual_subsidy=float((raw or {}).get("annual_operating_subsidy_wan") or 0.0),
            calc_years=calc,
            build_years=build,
            debt_service=debt,
        )
        # 提升到 result 顶层供门禁（_build_annual 只返回 annual，由调用方合并）
    else:
        annual["non_operating_balance"] = None

    # 【P1-4】DSCR 已在下方循环附加；此处补 ICR（EBIT/利息）
    if ind and debt:
        # approximate EBIT ≈ 会计利润总额 + 利息 = (net+tax) + interest  or total_profit+interest
        pd = annual.get("profit_distribution") or []
        for i, drow in enumerate(debt):
            interest = float(drow.get("interest") or 0.0)
            if i < len(pd):
                # total_profit is after interest (accounting); EBIT ≈ total_profit + interest
                ebit = float(pd[i].get("total_profit") or 0.0) + interest
            else:
                ebit = None
            drow["icr"] = round(ebit / interest, 2) if (ebit is not None and interest > 0) else None

    return annual


def _build_financial_plan(r: dict[str, Any], annual: dict[str, Any],
                          debt: list[dict]) -> list[dict[str, Any]]:
    """P0-5：财务计划现金流量表(投资/融资/经营三活动 + 期末现金 + 累计盈余 + 资金缺口)。

    口径说明(与既有附表自洽、不重复计数)：
    - 建设期：资金筹措假设按需到位,融资流入 = 投资流出(建设投资+建设期利息按分年),净现金流=0,
      不在此替 P0-1 投资口径歧义报假缺口(真正资金链压力在运营期还本付息 vs 经营现金流)。
    - 运营期经营活动净现金 = 会计净利润 + 折旧 + 摊销 + 利息(还原不含息经营现金流,与附表8 CFADS 同口径)。
    - 运营期投资活动 = −流动资金增加(投产首年) + 流动资金回收 + 资产余值回收(末年)。
    - 运营期融资活动 = −(当年还本 + 当年付息)。
    - 累计盈余资金逐年滚动;任一年期末现金<0 标记资金缺口年。
    """
    ind = r.get("indicators") or {}
    if not ind:  # 非经营性项目：不构造(按全生命周期资金平衡分析,另行处理)
        return []
    params = r.get("params") or {}
    inv = r.get("investment") or {}
    build = int(params.get("build_years") or 1)
    calc = int(params.get("calc_years") or 1)
    op_years = max(calc - build, 1)

    pd_rows = annual.get("profit_distribution") or []   # 附表7(会计口径,已扣息)
    tc_rows = annual.get("total_cost") or []            # 附表6(含利息)
    wc_total = round((inv.get("working_capital") or 0.0), 2)
    # 【P1-03】资产余值回收:与 compute_financials/附表9 口径一致(残值+未折完账面净值),读 terminal_recovery。
    salvage = (r.get("raw") or {}).get("terminal_recovery")
    if salvage is None:
        salvage_rate = (r.get("raw") or {}).get("salvage_rate")
        salvage_rate = 0.05 if salvage_rate is None else salvage_rate
        salvage = round((inv.get("fixed_asset") or 0.0) * salvage_rate, 2)

    rows: list[dict[str, Any]] = []
    cum = 0.0
    input_revision = r.get("input_revision") if isinstance(r.get("input_revision"), dict) else {}
    raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
    funding_schedule = (
        input_revision.get("funding_annual_schedule")
        or raw.get("funding_annual_schedule")
        or []
    )
    funding_by_year: dict[int, dict[str, Any]] = {}
    if isinstance(funding_schedule, list):
        for item in funding_schedule:
            if not isinstance(item, dict):
                continue
            try:
                year = int(item.get("year") or item.get("period"))
            except (TypeError, ValueError):
                continue
            if year not in funding_by_year:
                funding_by_year[year] = item
    atomic_funding = (
        set(funding_by_year) == set(range(1, build + 1))
        and all(
            row.get("construction_investment_wan") not in (None, "")
            and row.get("construction_interest_wan") not in (None, "")
            and row.get("working_capital_wan") not in (None, "")
            and row.get("capital_own_wan") not in (None, "")
            and row.get("loan_wan") not in (None, "")
            and row.get("gov_subsidy_wan") not in (None, "")
            for row in funding_by_year.values()
        )
    )
    # 建设期：全部筹资包含流动资金。非流动投资逐年支出，流动资金先形成现金储备，
    # 投产首年再转为营运资本；不得让已筹资金在财务计划中“消失”。房地产开发成本
    # 属存货而非固定资产，因此这里读取建设投资+建设期利息，不读取 fixed_asset。
    non_wc_investment = round(
        float(inv.get("construction") or 0.0) + float(inv.get("interest") or 0.0), 2
    )
    total_financing = round(non_wc_investment + wc_total, 2)
    for t in range(build):
        funding_row = funding_by_year.get(t + 1, {}) if atomic_funding else {}
        if atomic_funding:
            construction_use = round(float(funding_row.get("construction_investment_wan") or 0.0), 2)
            interest_use = round(float(funding_row.get("construction_interest_wan") or 0.0), 2)
            working_use = round(float(funding_row.get("working_capital_wan") or 0.0), 2)
            invest_out = round(construction_use + interest_use + working_use, 2)
            equity_in = round(float(funding_row.get("capital_own_wan") or 0.0), 2)
            loan_in = round(float(funding_row.get("loan_wan") or 0.0), 2)
            subsidy_in = round(float(funding_row.get("gov_subsidy_wan") or 0.0), 2)
            finance_in = round(equity_in + loan_in + subsidy_in, 2)
        else:
            construction_use = round(non_wc_investment / build, 2) if t < build - 1 else round(
                non_wc_investment - sum(row["construction_investment"] + row["construction_interest"] for row in rows), 2
            )
            interest_use = 0.0
            working_use = 0.0
            invest_out = construction_use
            finance_in = (
                round(total_financing / build, 2)
                if t < build - 1
                else round(total_financing - sum(row["finance_in"] for row in rows), 2)
            )
            equity_in = finance_in
            loan_in = 0.0
            subsidy_in = 0.0
        net = round(finance_in - invest_out, 2)
        cum = round(cum + net, 2)
        rows.append({
            "period": t + 1, "phase": "建设期",
            "finance_in": finance_in, "operating_net": 0.0,
            "invest_out": invest_out, "debt_service": 0.0,
            "net_cashflow": net, "cumulative": cum, "gap": False,
            "construction_investment": construction_use,
            "construction_interest": interest_use,
            "working_capital": working_use,
            "capital_own": equity_in,
            "loan_draw": loan_in,
            "gov_subsidy": subsidy_in,
            "funding_balance_ok": abs(invest_out - finance_in) <= 0.05,
            "funding_plan_source": "confirmed_annual_schedule" if atomic_funding else "estimate_fallback",
        })
    # 运营期
    for y in range(op_years):
        pdr = pd_rows[y] if y < len(pd_rows) else {}
        tcr = tc_rows[y] if y < len(tc_rows) else {}
        interest = round(tcr.get("interest") or 0.0, 2)
        net_profit = round(pdr.get("net_profit") or 0.0, 2)   # 会计净利润(已扣息)
        dep = round(tcr.get("depreciation") or 0.0, 2)
        amort = round(tcr.get("amortization") or 0.0, 2)
        # 经营活动净现金 = 会计净利润 + 非现金摊提 + 利息(还原不含息经营现金流)
        cogs_series = (r.get("raw") or {}).get("cogs_series") or []
        cogs_addback = float(cogs_series[y] or 0.0) if y < len(cogs_series) else 0.0
        op_net = round(net_profit + dep + amort + interest + cogs_addback, 2)
        # 投资活动：投产首年投入流动资金,末年回收流动资金+资产余值
        # With a v1 atomic funding plan, working capital is already an explicit
        # use in the construction/funding year and must not be paid a second time.
        invest_out = 0.0 if atomic_funding else (wc_total if y == 0 else 0.0)
        recover = round(wc_total + salvage, 2) if y == op_years - 1 else 0.0
        invest_net = round(recover - invest_out, 2)
        # 融资活动：还本 + 付息
        principal = round(debt[y]["principal"], 2) if y < len(debt) else 0.0
        ds = round(principal + interest, 2)
        net = round(op_net + invest_net - ds, 2)
        cum = round(cum + net, 2)
        rows.append({
            "period": build + y + 1, "phase": "运营期",
            "finance_in": 0.0, "operating_net": op_net,
            "invest_out": round(invest_out - recover, 2),  # 净投资流出(负=净回收)
            "debt_service": ds, "net_cashflow": net,
            "cumulative": cum, "gap": cum < 0,
        })
    return rows
