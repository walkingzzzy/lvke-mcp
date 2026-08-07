"""投资、资金筹措与营运资金三表 builder。"""

from __future__ import annotations

from typing import Any, Optional


from .field_source import (
    _confirmed_fact_domains,
    _effective_input_revision,
    _get_field,
)

from .primitives import (
    _number,
)


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
