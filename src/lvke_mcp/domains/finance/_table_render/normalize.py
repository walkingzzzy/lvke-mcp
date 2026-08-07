"""行归一化、参考行树挂载与渲染行契约。"""

from __future__ import annotations

from typing import Any


from .field_source import (
    _confirmed_fact_domains,
    _effective_input_revision,
    _repay_source_facts,
    _source_kind,
    _source_value,
)

from .primitives import (
    _column_values,
    _item_row_period_values,
    _number,
)


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
