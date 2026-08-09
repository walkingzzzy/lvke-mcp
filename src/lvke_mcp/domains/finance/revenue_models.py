"""收入模型展开器（B 层核心，解决 H1 单点收入 / H2 达产恒定）。

把 FinanceSpec 的收入选型展开成**逐年营收 + 逐年可变成本**序列，喂回引擎。
五种模型：
- ``product_sales``：多产品×单价×产量×逐年达产率曲线（制造/加工）
- ``property_sales``：可售面积×售价×逐年去化率（房地产）
- ``tourism``：客流×客单价×逐年爬坡（文旅）
- ``gov_payment``：政府付费/可用性付费（污水 PPP、特许经营等）
- ``flat``：单点法（向后兼容现状，ramp 为空即全程达产）

纯函数、无 IO；``ramp`` 为空时回退全程达产（等价现状），保证向后兼容。
"""

from __future__ import annotations

from typing import Any


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_wan(price: float, unit: str) -> float:
    """单价换算为万元。unit="wan" 直接返回，否则按元→万元。"""
    return price if unit == "wan" else price / 10000.0


def _unit_scale(unit: str) -> float:
    """产量计量单位的换算系数：万-单位（万箱/万吨/万件…）→ 10000，否则 1。

    可研中产量常以「万箱/万吨」表达而单价以「元」表达，两者相乘需 ×10000 才是万元。
    revenue(万元) = _to_wan(单价) × 产量 × _unit_scale(产量单位)。
    """
    u = str(unit or "").strip()
    return 10000.0 if u.startswith("万") else 1.0


def _ramp(seq: list[float], op_years: int) -> list[float]:
    """把爬坡曲线补齐/截断到 op_years：不足则末值填满，超出则截断。空则全程达产。"""
    if not seq:
        return [1.0] * op_years
    out = [_f(x, 0.0) for x in seq[:op_years]]
    if out:
        out += [out[-1]] * (op_years - len(out))
    else:
        out = [1.0] * op_years
    return out


def expand(spec: dict[str, Any], op_years: int) -> dict[str, Any]:
    """把 RevenueSpec 展开为逐年序列。返回 {revenue_by_year[], var_cost_by_year[], model, note}。

    op_years：运营期年数（= calc_years - build_years，与 compute_financials 一致）。
    达产恒定回退：ramp 为空时按全程达产（等价现状），保证向后兼容。
    """
    op_years = max(int(op_years or 0), 1)
    rev = (spec or {}).get("revenue") or {}
    model = rev.get("model", "flat")
    if model == "product_sales":
        return _product_sales(rev, op_years)
    if model == "property_sales":
        return _property_sales(rev, op_years)
    if model == "tourism":
        return _tourism(rev, op_years)
    if model == "gov_payment":
        return _gov_payment(rev, op_years)
    if model == "rail_transit":
        return _rail_transit(rev, op_years)
    if model == "hotel_mixed":  # 【恒立专用】混合经营模型
        return _hotel_mixed(rev, op_years)
    if model in {"lease_portfolio", "inventory_sales"}:
        return _scheduled(rev, op_years, model)
    return _flat(rev, op_years)


def _product_sales(rev: dict[str, Any], op_years: int) -> dict[str, Any]:
    revenue_by_year = [0.0] * op_years
    var_by_year = [0.0] * op_years
    products = rev.get("products") or []
    for p in products:
        if not isinstance(p, dict):
            continue
        full = (_to_wan(_f(p.get("price_per_unit")), p.get("price_unit", "yuan"))
                * _f(p.get("capacity")) * _unit_scale(p.get("unit", "")))
        ramp = _ramp(p.get("ramp") or [], op_years)
        vc_rate = _f(p.get("var_cost_rate"))
        for y in range(op_years):
            ry = round(full * ramp[y], 2)
            revenue_by_year[y] = round(revenue_by_year[y] + ry, 2)
            var_by_year[y] = round(var_by_year[y] + ry * vc_rate, 2)
    return {"revenue_by_year": revenue_by_year, "var_cost_by_year": var_by_year,
            "model": "product_sales",
            "note": f"产销法：{len(products)} 个产品×单价×产量×达产率曲线"}


def _property_sales(rev: dict[str, Any], op_years: int) -> dict[str, Any]:
    total = _to_wan(_f(rev.get("price_per_sqm")), "yuan") * _f(rev.get("saleable_area"))
    # 去化率是「当年销售占总可售的比例」；不足运营年数时用 0 补齐（不得用末值填充，否则重复卖）。
    raw_abs = rev.get("absorption") or []
    if raw_abs:
        absorb = [_f(x, 0.0) for x in list(raw_abs)[:op_years]]
        absorb += [0.0] * (op_years - len(absorb))
    else:
        # 无去化曲线：按运营期均分（可研简化，合计 100%）
        absorb = [round(1.0 / op_years, 6)] * op_years
        absorb[-1] = round(1.0 - sum(absorb[:-1]), 6)
    # 逐年确认收入
    revenue_by_year = [round(total * a, 2) for a in absorb]
    # 开发成本结转比例：供引擎按「建设投资 × 当年去化率」结转销售成本（建标〔2000〕205 号口径）。
    # 此处不持有投资金额，只回传 absorption 序列；COGS 在 finance_model 用 construction 计算。
    return {
        "revenue_by_year": revenue_by_year,
        "var_cost_by_year": [0.0] * op_years,
        "absorption": absorb,
        "model": "property_sales",
        "cost_side": "inventory_cogs",  # 引擎识别：开发产品=存货，不折旧、销售成本随去化结转
        "note": "去化法：可售面积×售价×逐年去化率（成本侧：开发成本随去化结转，不折旧）",
    }


def _tourism(rev: dict[str, Any], op_years: int) -> dict[str, Any]:
    # 客流常以「万人次」表达、客单价以「元」表达；visitor_unit 缺省按人次（含"万"则 ×10000）。
    rev, _errors = normalize_tourism_revenue(rev)
    ramp = _ramp(rev.get("visitor_ramp") or [], op_years)
    visitors = _f(rev.get("annual_visitors")) * _unit_scale(rev.get("visitor_unit", ""))
    components = rev.get("tourism_revenue_components") or []
    if isinstance(components, list) and components:
        revenue_by_year = [0.0] * op_years
        for component in components:
            if not isinstance(component, dict):
                continue
            component_ramp = _ramp(component.get("ramp") or ramp, op_years)
            if component.get("basis") == "fixed_annual":
                full = max(_f(component.get("annual_revenue_wan")), 0.0)
            else:
                participation = max(0.0, min(_f(component.get("participation_rate"), 1.0), 1.0))
                full = _to_wan(_f(component.get("price_per_visitor_yuan")), "yuan") * visitors * participation
            revenue_by_year = [
                round(value + full * component_ramp[index], 2)
                for index, value in enumerate(revenue_by_year)
            ]
    else:
        full = _to_wan(_f(rev.get("spend_per_visitor")), "yuan") * visitors
        revenue_by_year = [round(full * r, 2) for r in ramp]
    return {"revenue_by_year": revenue_by_year, "var_cost_by_year": [0.0] * op_years,
            "model": "tourism",
            "note": "客流法：分收入组件×客流/年度金额×爬坡"}


def normalize_tourism_revenue(
    revenue: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Normalize tourism aliases into one auditable revenue component tree."""

    rev = dict(revenue or {})
    if str(rev.get("model") or "") != "tourism":
        return rev, []
    errors: list[dict[str, str]] = []
    raw_components = rev.get("tourism_revenue_components") or []
    components = [dict(item) for item in raw_components if isinstance(item, dict)]
    if raw_components and len(components) != len(raw_components):
        errors.append({
            "path": "/revenue/tourism_revenue_components",
            "code": "revenue_component_invalid",
            "message": "文旅收入组件必须全部为对象",
        })

    for index, component in enumerate(components):
        basis = str(component.get("basis") or "")
        if not str(component.get("name") or "").strip():
            errors.append({
                "path": f"/revenue/tourism_revenue_components/{index}/name",
                "code": "revenue_component_invalid",
                "message": "收入组件名称必填",
            })
        if basis == "per_visitor":
            if component.get("price_per_visitor_yuan") is None:
                errors.append({
                    "path": (
                        f"/revenue/tourism_revenue_components/{index}"
                        "/price_per_visitor_yuan"
                    ),
                    "code": "revenue_component_invalid",
                    "message": "逐客流收入组件必须提供人均单价",
                })
        elif basis == "fixed_annual":
            if component.get("annual_revenue_wan") is None:
                errors.append({
                    "path": (
                        f"/revenue/tourism_revenue_components/{index}"
                        "/annual_revenue_wan"
                    ),
                    "code": "revenue_component_invalid",
                    "message": "固定年度收入组件必须提供年度收入",
                })
        else:
            errors.append({
                "path": f"/revenue/tourism_revenue_components/{index}/basis",
                "code": "revenue_component_invalid",
                "message": "收入组件 basis 必须为 per_visitor 或 fixed_annual",
            })

    def alias_value(name: str) -> float | None:
        if rev.get(name) is None:
            return None
        try:
            value = float(rev[name])
        except (TypeError, ValueError):
            errors.append({
                "path": f"/revenue/{name}",
                "code": "revenue_component_invalid",
                "message": "收入别名必须为非负数",
            })
            return None
        if value < 0:
            errors.append({
                "path": f"/revenue/{name}",
                "code": "revenue_component_invalid",
                "message": "收入别名必须为非负数",
            })
            return None
        return value

    ticket = alias_value("ticket_price_yuan")
    secondary = alias_value("secondary_spend_yuan")
    fixed = alias_value("fixed_annual_revenue_wan")
    other = alias_value("other_revenue_wan")
    if fixed is not None and other is not None and abs(fixed - other) > 1e-9:
        errors.append({
            "path": "/revenue/other_revenue_wan",
            "code": "revenue_component_conflict",
            "message": "other_revenue_wan 与 fixed_annual_revenue_wan 不一致",
        })
    fixed_alias = fixed if fixed is not None else other

    explicit_per_visitor = sum(
        max(_f(item.get("price_per_visitor_yuan")), 0.0)
        * max(0.0, min(_f(item.get("participation_rate"), 1.0), 1.0))
        for item in components
        if item.get("basis") == "per_visitor"
    )
    explicit_fixed = sum(
        max(_f(item.get("annual_revenue_wan")), 0.0)
        for item in components
        if item.get("basis") == "fixed_annual"
    )
    alias_per_visitor = sum(value for value in (ticket, secondary) if value is not None)
    aliases_present = any(value is not None for value in (ticket, secondary, fixed_alias))

    if components and (ticket is not None or secondary is not None):
        if abs(explicit_per_visitor - alias_per_visitor) > 1e-9:
            errors.append({
                "path": "/revenue/tourism_revenue_components",
                "code": "revenue_component_conflict",
                "message": "逐客流收入组件与门票/二次消费别名合计不一致",
            })
    if components and fixed_alias is not None and abs(explicit_fixed - fixed_alias) > 1e-9:
        errors.append({
            "path": "/revenue/tourism_revenue_components",
            "code": "revenue_component_conflict",
            "message": "固定年度收入组件与其他收入别名不一致",
        })

    if not components and aliases_present:
        inherited_ramp = list(rev.get("visitor_ramp") or [])
        if ticket is not None:
            components.append({
                "name": "门票收入", "basis": "per_visitor",
                "price_per_visitor_yuan": ticket, "participation_rate": 1.0,
                "ramp": inherited_ramp,
            })
        if secondary is not None:
            components.append({
                "name": "二次消费", "basis": "per_visitor",
                "price_per_visitor_yuan": secondary, "participation_rate": 1.0,
                "ramp": inherited_ramp,
            })
        if fixed_alias is not None:
            components.append({
                "name": "其他固定收入", "basis": "fixed_annual",
                "annual_revenue_wan": fixed_alias, "ramp": inherited_ramp,
            })
        explicit_per_visitor = alias_per_visitor

    if not components and rev.get("spend_per_visitor") is None:
        errors.append({
            "path": "/revenue/tourism_revenue_components",
            "code": "revenue_component_missing",
            "message": "文旅收入必须提供产品树、汇总客单价或兼容收入别名",
        })

    if components:
        spend = rev.get("spend_per_visitor")
        if spend is not None:
            try:
                spend_value = float(spend)
            except (TypeError, ValueError):
                spend_value = -1.0
            if spend_value < 0 or abs(spend_value - explicit_per_visitor) > 1e-9:
                errors.append({
                    "path": "/revenue/spend_per_visitor",
                    "code": "revenue_component_conflict",
                    "message": "spend_per_visitor 与逐客流组件加权合计不一致",
                })
        rev["spend_per_visitor"] = explicit_per_visitor
        rev["tourism_revenue_components"] = components
    return rev, errors


def _gov_payment(rev: dict[str, Any], op_years: int) -> dict[str, Any]:
    """政府付费 / 可用性付费（污水 PPP、特许经营等）。

    参数：
    - annual_gov_payment_wan：达产/满负荷年政府付费（万元，缺省回落 annual_revenue_wan）
    - payment_ramp：逐年付费系数（空则全程 1.0）
    - vat_refund_rate：增值税即征即退比例（0~1，如污水 70% 退税填 0.7；仅作标记回传，引擎落地现金）
    - fiscal_subsidy_wan：年财政运营补贴（万元，叠加到营收；农业/公用事业常见）
    """
    annual = _f(rev.get("annual_gov_payment_wan"))
    if annual <= 0:
        annual = _f(rev.get("annual_revenue_wan"))
    ramp = _ramp(rev.get("payment_ramp") or rev.get("ramp") or [], op_years)
    subsidy = max(_f(rev.get("fiscal_subsidy_wan")), 0.0)
    revenue_by_year = [round(annual * r + subsidy, 2) for r in ramp]
    vat_refund = _f(rev.get("vat_refund_rate"))
    if vat_refund < 0:
        vat_refund = 0.0
    if vat_refund > 1:
        vat_refund = 1.0
    note_parts = ["政府付费/可用性付费"]
    if subsidy > 0:
        note_parts.append(f"年财政补贴 {subsidy:g} 万元")
    if vat_refund > 0:
        note_parts.append(f"增值税退税比例 {vat_refund:.0%}")
    return {
        "revenue_by_year": revenue_by_year,
        "var_cost_by_year": [0.0] * op_years,
        "model": "gov_payment",
        "vat_refund_rate": vat_refund,
        "fiscal_subsidy_wan": subsidy,
        "note": "：".join(note_parts) if len(note_parts) > 1 else note_parts[0],
    }


_NON_FARE_SCENARIO_RATES = {"low": 0.05, "base": 0.10, "high": 0.15}


def rail_non_fare_rate(rev: dict[str, Any]) -> float:
    """Resolve the non-fare share of farebox revenue from an explicit scenario.

    非票收入（广告、商业、通信管道等）在轨道交通里按票务收入的固定比例情景估算，
    行业惯例给 5%/10%/15% 三档。显式 ``non_fare_revenue_rate`` 优先，其次按
    ``non_fare_scenario`` 取档；两者都缺则回落 base 档，绝不静默取 0。
    """

    explicit = rev.get("non_fare_revenue_rate")
    if explicit is not None:
        return max(0.0, min(_f(explicit), 1.0))
    scenario = str(rev.get("non_fare_scenario") or "base").strip().lower()
    return _NON_FARE_SCENARIO_RATES.get(scenario, _NON_FARE_SCENARIO_RATES["base"])


def _rail_transit(rev: dict[str, Any], op_years: int) -> dict[str, Any]:
    """城市轨道交通：票务/非票/财政支持三分收入。

    参数：
    - annual_passenger_trips：达产年客运量（万人次，单位由 passenger_unit 决定）
    - average_fare_yuan：平均清分票价（元/人次，线网清分后归本线的票款）
    - ridership_ramp：逐年客流爬坡系数
    - non_fare_scenario / non_fare_revenue_rate：非票收入占票务收入比例（5%/10%/15%）
    - annual_fiscal_support_wan / fiscal_support_ramp：财政支持（运营补贴），单列不混入票价

    三类收入必须各自可追溯：票务=客运量×清分票价×爬坡；非票=票务×比例；
    财政支持独立成项。绝不把补贴摊进票价冒充票款收入，也不把票款和补贴合并成
    单一"营业收入"，否则运营期票价敏感性无法计算。
    """

    trips_unit = str(rev.get("passenger_unit") or "").strip()
    unit_scales = {
        "人次": 1.0,
        "person_trips": 1.0,
        "passenger_trips": 1.0,
        "persons": 1.0,
        "万人次": 10000.0,
        "10k_person_trips": 10000.0,
        "ten_thousand_person_trips": 10000.0,
    }
    if trips_unit not in unit_scales:
        raise ValueError("rail_transit.passenger_unit 必须显式为 人次 或 万人次")
    trips = max(_f(rev.get("annual_passenger_trips")), 0.0) * unit_scales[trips_unit]
    fare = max(_f(rev.get("average_fare_yuan")), 0.0)
    ramp = _ramp(rev.get("ridership_ramp") or rev.get("ramp") or [], op_years)
    # 票务收入（万元）= 客运量（人次）× 清分票价（元/人次）÷ 10000
    farebox_full = _to_wan(fare, "yuan") * trips
    multipliers = _ramp(rev.get("fare_multiplier_by_year") or [], op_years)
    if any(value < 0 for value in multipliers):
        raise ValueError("rail_transit.fare_multiplier_by_year 不得为负")
    farebox_by_year = [
        round(farebox_full * ramp[index] * multipliers[index], 2)
        for index in range(op_years)
    ]

    non_fare_rate = rail_non_fare_rate(rev)
    non_fare_by_year = [round(value * non_fare_rate, 2) for value in farebox_by_year]

    support_full = max(_f(rev.get("annual_fiscal_support_wan")), 0.0)
    support_ramp = _ramp(rev.get("fiscal_support_ramp") or [], op_years)
    support_by_year = [round(support_full * r, 2) for r in support_ramp]

    revenue_by_year = [
        round(farebox_by_year[y] + non_fare_by_year[y] + support_by_year[y], 2)
        for y in range(op_years)
    ]
    note_parts = [
        "轨道交通三分收入：票务=客运量×清分票价×爬坡",
        f"非票={non_fare_rate:.0%}×票务",
    ]
    if support_full > 0:
        note_parts.append(f"财政支持达产年 {support_full:g} 万元（单列）")
    return {
        "revenue_by_year": revenue_by_year,
        "var_cost_by_year": [0.0] * op_years,
        "model": "rail_transit",
        "farebox_by_year": farebox_by_year,
        "non_fare_by_year": non_fare_by_year,
        "fiscal_support_by_year": support_by_year,
        "non_fare_revenue_rate": non_fare_rate,
        "annual_passenger_trips_persons": trips,
        "average_fare_yuan": fare,
        "ridership_ramp": ramp,
        "fare_multiplier_by_year": multipliers,
        "note": "；".join(note_parts),
    }


def _flat(rev: dict[str, Any], op_years: int) -> dict[str, Any]:
    r = _f(rev.get("annual_revenue_wan"))
    return {"revenue_by_year": [r] * op_years, "var_cost_by_year": [0.0] * op_years,
            "model": "flat",
            "note": "单点法（向后兼容现状）"}


def _scheduled(rev: dict[str, Any], op_years: int, model: str) -> dict[str, Any]:
    raw = [_f(value) for value in (rev.get("annual_schedule_wan") or [])]
    schedule = raw[:op_years] + [0.0] * max(op_years - len(raw), 0)
    total = sum(schedule)
    # Inventory sales consume a finite stock. Expose the realized share of
    # the stock so the finance kernel can recognize inventory COGS and recover
    # the remaining working capital without treating sales as recurring rent.
    absorption = (
        [round(value / total, 12) for value in schedule]
        if model == "inventory_sales" and total > 0
        else None
    )
    return {
        "revenue_by_year": schedule,
        "var_cost_by_year": [0.0] * op_years,
        "model": model,
        "cost_side": "inventory_cogs" if model == "inventory_sales" else "lease_operation",
        **({"absorption": absorption} if absorption is not None else {}),
        "note": (
            "存量销售逐年去化计划（未售部分不得重复确认收入）"
            if model == "inventory_sales"
            else "租赁组合逐年合同/市场租金计划"
        ),
    }


def _hotel_mixed(rev: dict[str, Any], op_years: int) -> dict[str, Any]:
    """【恒立专用】酒店混合经营模型：客房自营 + 配套出租。

    参数：
    - self_operation.room_count：客房数量
    - self_operation.adr：平均房价ADR（元/间夜）
    - self_operation.occupancy_ramp：逐年入住率序列（0~1，空则全程首年值）
    - rental.supermarket：超市年租金（万元）
    - rental.pub：清吧年租金（万元）
    - rental.gym：健身房年租金（万元）
    - meeting_revenue：会议/餐饮年收入（万元，可选）
    """
    # 自营线：客房
    self_op = rev.get("self_operation") or {}
    room_count = _f(self_op.get("room_count"))
    adr = _f(self_op.get("adr"))
    occupancy_base = _f(self_op.get("occupancy_y1")) or _f(self_op.get("occupancy")) or 0.6
    occupancy_ramp = _ramp(self_op.get("occupancy_ramp") or [], op_years)

    # 如果ramp为空,使用occupancy_base作为全程值
    if not self_op.get("occupancy_ramp"):
        occupancy_ramp = [occupancy_base] * op_years

    # 客房收入 = 房间数 × 365 × ADR × 入住率 / 10000
    room_revenue_by_year = [
        round(room_count * 365 * adr * occupancy_ramp[y] / 10000, 2)
        for y in range(op_years)
    ]

    # 出租线：租金收入（固定）
    rental = rev.get("rental") or {}
    rental_total = (
        _f(rental.get("supermarket"))
        + _f(rental.get("pub"))
        + _f(rental.get("gym"))
    )

    # 会议/餐饮收入（可选）
    meeting = _f(rev.get("meeting_revenue"))

    # 混合收入合计
    revenue_by_year = [
        round(room_revenue_by_year[y] + rental_total + meeting, 2)
        for y in range(op_years)
    ]

    return {
        "revenue_by_year": revenue_by_year,
        "var_cost_by_year": [0.0] * op_years,  # 酒店成本在cost_items处理
        "model": "hotel_mixed",
        "note": f"酒店混合经营：{int(room_count)}间客房自营(ADR {int(adr)}元) + 租金{rental_total:.2f}万/年",
        "breakdown": {
            "room_revenue_by_year": room_revenue_by_year,
            "rental_revenue_by_year": [rental_total] * op_years,
            "meeting_revenue_by_year": [meeting] * op_years if meeting > 0 else None,
        },
    }
