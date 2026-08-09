"""缺项评估、表适用性与十三表结构化编排入口。"""

from __future__ import annotations

from typing import Any

from lvke_mcp.domains.finance.reference_schema import (
    assess_missing_fields_extended,
    assess_fact_source_coverage,
    assess_structure_coverage,
    merge_missing,
    schema_path,
    validate_reference_sources,
)

from .builders import (
    _build_funding,
    _build_investment,
    _build_wc,
    _pack_rows,
)

from .field_source import (
    _effective_input_revision,
)

from .normalize import (
    _attach_reference_row_trees,
    _normalize_rows,
    _renderer_row_contract,
)

from .reference import (
    _promote_reference_period_table,
)

from .specs import (
    DELIVERY_ORDER,
    _TABLE_SPECS,
)


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
        elif model == "rail_transit":
            for field in ("annual_passenger_trips", "average_fare_yuan"):
                if revenue.get(field) in (None, "", [], {}):
                    revenue_missing.append(field)
            if not (revenue.get("ridership_ramp") or revenue.get("ramp")):
                revenue_missing.append("ridership_ramp 客流爬坡")
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
                    elif model == "rail_transit":
                        # 票务/非票/财政支持三分，逐项保留量价与爬坡，
                        # 使附表5 的三类收入各自可复算、票价敏感性可算。
                        from lvke_mcp.domains.finance.revenue_models import (
                            rail_non_fare_rate,
                        )

                        trips = float(revenue_spec.get("annual_passenger_trips") or 0.0)
                        trips_unit = str(revenue_spec.get("passenger_unit") or "万人次")
                        fare = revenue_spec.get("average_fare_yuan")
                        ridership_ramp = list(
                            revenue_spec.get("ridership_ramp")
                            or revenue_spec.get("ramp")
                            or []
                        )
                        non_fare_rate = rail_non_fare_rate(revenue_spec)
                        body["product_tree"] = [{
                            "name": "票务收入",
                            "unit": trips_unit,
                            "price_per_unit": fare,
                            "price_unit": "yuan",
                            "capacity": trips,
                            "ramp": ridership_ramp,
                            "var_cost_rate": 0.0,
                            "revenue_model": model,
                            "revenue_category": "farebox",
                        }, {
                            # 非票收入绑定票务情景比例：单位取"倍票务收入"，
                            # 量=票务达产收入、价=比例，公式仍是量×价×爬坡。
                            "name": f"非票收入（票务×{non_fare_rate:.0%}）",
                            "unit": trips_unit,
                            "price_per_unit": (
                                float(fare) * non_fare_rate if fare is not None else None
                            ),
                            "price_unit": "yuan",
                            "capacity": trips,
                            "ramp": ridership_ramp,
                            "var_cost_rate": 0.0,
                            "revenue_model": model,
                            "revenue_category": "non_fare",
                            "non_fare_revenue_rate": non_fare_rate,
                            "non_fare_scenario": str(
                                revenue_spec.get("non_fare_scenario") or "base"
                            ),
                        }]
                        support = float(
                            revenue_spec.get("annual_fiscal_support_wan") or 0.0
                        )
                        if support > 0:
                            body["product_tree"].append({
                                "name": "财政支持（运营补贴）",
                                "unit": "年",
                                "price_per_unit": support,
                                "price_unit": "wan",
                                "capacity": 1.0,
                                "ramp": list(
                                    revenue_spec.get("fiscal_support_ramp") or []
                                ),
                                "var_cost_rate": 0.0,
                                "revenue_model": model,
                                "revenue_category": "fiscal_support",
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
