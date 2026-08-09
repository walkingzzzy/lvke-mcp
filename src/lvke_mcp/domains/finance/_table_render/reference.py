"""参考矩阵与参考期间表提升。"""

from __future__ import annotations

from typing import Any, Optional


from .builders import (
    _pack_rows,
)

from .field_source import (
    _confirmed_fact_domains,
    _effective_input_revision,
)

from .primitives import (
    _last_value,
    _period_value,
    _series,
    _sum_values,
)

from .specs import (
    _fmt_rate_pct,
)


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
            "interest", "net_cashflow", "fiscal_support", "renewal_capex",
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
        fiscal_support = [
            float(row.get("fiscal_support") or 0.0) if isinstance(row, dict) else 0.0
            for row in records
        ]
        renewal_capex = [
            float(row.get("renewal_capex") or 0.0) if isinstance(row, dict) else 0.0
            for row in records
        ]
        cash_inflow = [
            round(revenue[i] + fiscal_support[i] + fixed_recover[i] + wc_recover[i], 2)
            for i in range(len(records))
        ]
        cash_outflow = [
            round(
                capital_invest[i] + renewal_capex[i] + principal[i] + interest[i]
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
            ("1.2", "财政缺口支持", fiscal_support, "fiscal_support"),
            ("1.3", "回收固定资产余值", fixed_recover, "recover_fixed"),
            ("1.4", "回收流动资金", wc_recover, "recover_wc"),
            ("2", "现金流出", cash_outflow, "cash_outflow"),
            ("2.1", "项目资本金", capital_invest, "capital_invest"),
            ("2.2", "更新改造投资", renewal_capex, "renewal_capex"),
            ("2.3", "借款本金偿还", principal, "principal"),
            ("2.4", "借款利息支付", interest, "interest"),
            ("2.5", "经营成本", op_cash_cost, "op_cash_cost"),
            ("2.6", "税金及附加", tax_surtax, "tax_surtax"),
            ("2.7", "所得税", income_tax, "income_tax"),
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
