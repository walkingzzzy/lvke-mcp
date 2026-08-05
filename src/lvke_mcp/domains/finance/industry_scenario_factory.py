"""Deterministic China 13-industry finance scenario matrix.

The factory is deliberately data-driven and contains no randomness.  It
builds six project archetypes for each of the thirteen industry groups and
five operating/financing variants for each archetype (13 * 6 * 5 = 390).

These are synthetic test cases, not project evidence.  Every case therefore
carries an explicit synthetic/C-grade evidence boundary even though its
``FinanceSpec`` is confirmed for deterministic replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MATRIX_VERSION = "finance_industry_matrix.2026-07-13.v1"


@dataclass(frozen=True)
class Archetype:
    code: str
    name: str
    model: str | None = None
    scale: float = 1.0
    operating: bool = True
    scope_note: str = ""


@dataclass(frozen=True)
class Industry:
    code: str
    label: str
    profile_version: str
    default_model: str
    construction_wan: float
    revenue_wan: float
    cash_cost_rate: float
    vat_rate: float
    build_months: int
    operating_years: int
    intangible_rate: float
    unit: str
    archetypes: tuple[Archetype, ...]


@dataclass(frozen=True)
class Variant:
    code: str
    label: str
    project_scale: float
    revenue_factor: float
    cost_factor: float
    debt_ratio: float
    loan_rate: float
    ramp: tuple[float, ...]
    absorption: tuple[float, ...]
    repayment: str
    grace_years: int = 0
    risk_case: bool = False


def _a(
    code: str,
    name: str,
    *,
    model: str | None = None,
    scale: float = 1.0,
    operating: bool = True,
    scope_note: str = "",
) -> Archetype:
    return Archetype(code, name, model, scale, operating, scope_note)


INDUSTRIES: tuple[Industry, ...] = (
    Industry(
        "agriculture_food", "农业与食品", "agriculture_food.v1", "product_sales",
        12000, 8500, 0.62, 0.09, 12, 12, 0.03, "万吨",
        (
            _a("grain_processing", "粮食精深加工", scale=1.00),
            _a("facility_vegetables", "设施蔬菜基地", scale=0.72),
            _a("livestock", "规模化畜牧养殖", scale=1.18),
            _a("aquaculture", "生态水产养殖", scale=0.86),
            _a("food_processing", "食品加工产业园", scale=1.42),
            _a("cold_chain_kitchen", "农产品冷链与中央厨房", scale=1.08),
        ),
    ),
    Industry(
        "energy_utilities", "能源与公用事业", "energy_utilities.v1", "product_sales",
        50000, 12000, 0.38, 0.13, 24, 18, 0.01, "万千瓦时",
        (
            _a("distributed_pv", "分布式光伏电站", scale=0.72),
            _a("onshore_wind", "陆上风电场", scale=1.35),
            _a("energy_storage", "电网侧储能电站", scale=0.92),
            _a("biomass", "生物质热电联产", scale=1.12),
            _a("water_supply", "城乡供水一体化", model="gov_payment", scale=0.88),
            _a("sewage", "污水处理特许经营", model="gov_payment", scale=0.82),
        ),
    ),
    Industry(
        "manufacturing", "制造业", "manufacturing.v1", "product_sales",
        28000, 18000, 0.60, 0.13, 18, 12, 0.04, "万件",
        (
            _a("equipment", "高端装备制造基地", scale=1.20),
            _a("electronics", "电子信息产品工厂", scale=0.86),
            _a("chemical_material", "化工新材料生产线", scale=1.35),
            _a("auto_parts", "汽车零部件工厂", scale=1.05),
            _a("textile", "绿色纺织工厂", scale=0.76),
            _a("medical_device", "医药与医疗器械工厂", scale=0.96),
        ),
    ),
    Industry(
        "construction_real_estate", "建筑与房地产", "construction_real_estate.v1",
        "property_sales", 90000, 60000, 0.09, 0.09, 24, 8, 0.01, "万平方米",
        (
            _a("residential", "住宅开发项目", scale=1.00),
            _a("industrial_park", "产业园区开发", scale=1.18),
            _a("commercial_complex", "商业综合体", scale=1.42),
            _a("urban_renewal", "城市更新项目", scale=1.25),
            _a("prefab_epc", "装配式建筑与EPC基地", model="product_sales", scale=0.65),
            _a("affordable_rental", "保障性租赁住房", model="gov_payment", scale=0.82),
        ),
    ),
    Industry(
        "transport_logistics", "交通与物流", "transport_logistics.v1", "product_sales",
        60000, 14000, 0.43, 0.09, 24, 18, 0.01, "万吨",
        (
            _a("toll_road", "收费公路改扩建", model="gov_payment", scale=1.45),
            _a("port_terminal", "港口码头工程", scale=1.35),
            _a("logistics_park", "综合物流园", scale=0.88),
            _a("cold_chain", "冷链物流中心", scale=0.76),
            _a("smart_parking", "城市智慧停车", scale=0.48),
            _a("charging_network", "新能源汽车充换电网络", scale=0.62),
        ),
    ),
    Industry(
        "retail_ecommerce", "零售与电商", "retail_ecommerce.v1", "product_sales",
        18000, 24000, 0.70, 0.13, 12, 10, 0.05, "万单",
        (
            _a("supermarket", "区域连锁超市", scale=0.86),
            _a("ecommerce_platform", "垂直电商平台", scale=0.74),
            _a("warehouse_retail", "仓储会员零售", scale=1.18),
            _a("community_retail", "社区零售网络", scale=0.68),
            _a("cross_border", "跨境电商中心", scale=1.05),
            _a("wholesale_market", "专业批发市场", scale=1.28),
        ),
    ),
    Industry(
        "tourism_catering", "旅游与餐饮", "tourism_catering.v1", "tourism",
        40000, 12000, 0.50, 0.06, 24, 18, 0.03, "万人次",
        (
            _a("scenic_area", "山水景区提升", scale=1.00),
            _a("resort_hotel", "度假酒店", scale=0.82),
            _a("restaurant_chain", "餐饮连锁中心", scale=0.52),
            _a("cultural_show", "文旅演艺项目", scale=0.64),
            _a("campground", "户外营地集群", scale=0.46),
            _a("theme_park", "主题乐园", scale=1.42),
        ),
    ),
    Industry(
        "finance", "金融服务", "finance.v1", "product_sales",
        15000, 9000, 0.52, 0.06, 12, 10, 0.18, "万笔",
        (
            _a("microloan", "小额贷款服务平台", scale=0.88,
               scope_note="仅验证项目投资与服务费现金流，不替代金融机构资产负债和监管资本模型"),
            _a("leasing", "融资租赁运营平台", scale=1.18,
               scope_note="仅验证项目投资与手续费现金流，不替代租赁资产信用风险模型"),
            _a("factoring", "供应链保理平台", scale=1.02,
               scope_note="仅验证项目投资与手续费现金流，不替代应收资产迁徙和减值模型"),
            _a("insurance_broker", "保险经纪服务平台", scale=0.62),
            _a("fund_management", "基金管理运营平台", scale=0.72),
            _a("fintech", "金融科技服务平台", scale=0.78),
        ),
    ),
    Industry(
        "software_internet", "软件与互联网", "software_internet.v1", "product_sales",
        22000, 16000, 0.46, 0.06, 12, 10, 0.35, "万户",
        (
            _a("saas", "企业级SaaS平台", scale=0.72),
            _a("cloud_datacenter", "云计算与数据中心", scale=1.42),
            _a("ai_platform", "行业人工智能平台", scale=0.96),
            _a("industrial_internet", "工业互联网平台", scale=1.08),
            _a("cybersecurity", "网络安全服务平台", scale=0.82),
            _a("digital_content", "数字内容平台", scale=0.68),
        ),
    ),
    Industry(
        "healthcare_pharma_eldercare", "医疗医药与养老", "healthcare_pharma_eldercare.v1",
        "product_sales", 50000, 22000, 0.55, 0.06, 24, 15, 0.06, "万人次",
        (
            _a("general_hospital", "综合医院", scale=1.35),
            _a("specialty_clinic", "专科医疗中心", scale=0.68),
            _a("pharma_plant", "药品生产基地", scale=1.08),
            _a("device_plant", "医疗器械生产基地", scale=0.92),
            _a("eldercare", "医养结合养老中心", scale=0.86),
            _a("health_management", "健康管理中心", scale=0.58),
        ),
    ),
    Industry(
        "education_hr", "教育与人力资源", "education_hr.v1", "product_sales",
        25000, 10000, 0.54, 0.06, 18, 12, 0.10, "万人",
        (
            _a("vocational_school", "职业学校扩建", model="gov_payment", scale=1.08),
            _a("skills_training", "职业技能培训中心", scale=0.62),
            _a("hr_service", "人力资源服务产业园", scale=0.74),
            _a("childcare", "普惠托育中心", model="gov_payment", scale=0.58),
            _a("online_education", "在线教育平台", scale=0.48),
            _a("research_transfer", "科研成果转化中心", scale=0.82),
        ),
    ),
    Industry(
        "government_public_service", "政府与公共服务", "government_public_service.v1",
        "gov_payment", 70000, 20000, 0.45, 0.06, 24, 18, 0.02, "项",
        (
            _a("municipal_road", "市政道路工程", scale=1.10, operating=False,
               scope_note="非经营性项目仅做全生命周期资金平衡，不强算IRR"),
            _a("sewage_ppp", "污水处理PPP", scale=0.82),
            _a("waste_treatment", "生活垃圾处理", scale=0.88),
            _a("cultural_center", "公共文化中心", scale=0.72, operating=False,
               scope_note="非经营性项目仅做全生命周期资金平衡，不强算IRR"),
            _a("affordable_housing", "保障性住房", scale=1.18),
            _a("government_data", "政务数据平台", scale=0.54),
        ),
    ),
    Industry(
        "professional_research_media", "专业服务、科研与媒体",
        "professional_research_media.v1", "product_sales",
        18000, 12000, 0.52, 0.06, 12, 10, 0.15, "万项目",
        (
            _a("consulting", "工程咨询服务平台", scale=0.62),
            _a("testing_lab", "检验检测实验室", scale=0.86),
            _a("rd_center", "产业研发中心", model="gov_payment", scale=1.08),
            _a("media", "融媒体中心", scale=0.72),
            _a("advertising", "数字广告平台", scale=0.58),
            _a("legal_accounting", "法律与会计共享服务中心", scale=0.66),
        ),
    ),
)


VARIANTS: tuple[Variant, ...] = (
    Variant(
        "base", "基准参考", 1.00, 1.00, 1.00, 0.45, 0.042,
        (0.60, 0.80, 1.00), (0.15, 0.25, 0.30, 0.20, 0.10),
        "equal_principal",
    ),
    Variant(
        "small_low_debt", "小型民营低负债", 0.45, 1.03, 1.04, 0.20, 0.038,
        (0.65, 0.85, 1.00), (0.12, 0.23, 0.30, 0.22, 0.13),
        "equal_principal",
    ),
    Variant(
        "large_high_leverage", "大型高杠杆", 2.20, 1.00, 0.98, 0.72, 0.052,
        (0.50, 0.72, 0.90, 1.00), (0.10, 0.20, 0.30, 0.25, 0.15),
        "equal_installment", grace_years=1,
    ),
    Variant(
        "slow_ramp", "慢爬坡", 1.00, 1.00, 1.03, 0.50, 0.047,
        (0.30, 0.50, 0.70, 0.85, 1.00),
        (0.08, 0.12, 0.18, 0.22, 0.20, 0.12, 0.08),
        "equal_principal", grace_years=1,
    ),
    Variant(
        "downside_stress", "下行情景压力", 1.12, 0.55, 1.15, 0.65, 0.061,
        (0.20, 0.35, 0.50, 0.60, 0.65),
        (0.04, 0.07, 0.10, 0.12, 0.12, 0.10, 0.08),
        "balloon", grace_years=1, risk_case=True,
    ),
)


def list_industries() -> list[dict[str, Any]]:
    """Return the canonical thirteen group catalogue without mutable objects."""
    return [
        {
            "code": item.code,
            "label": item.label,
            "profile_version": item.profile_version,
            "archetype_count": len(item.archetypes),
            "scenario_count": len(item.archetypes) * len(VARIANTS),
        }
        for item in INDUSTRIES
    ]


def _round_split(total: float, weights: tuple[float, ...]) -> list[float]:
    values = [round(total * weight, 2) for weight in weights[:-1]]
    values.append(round(total - sum(values), 2))
    return values


def _investment_detail(construction: float) -> dict[str, Any]:
    engineering, other, contingency = _round_split(construction, (0.84, 0.11, 0.05))
    civil, equipment, installation = _round_split(engineering, (0.43, 0.49, 0.08))
    land, management, design, consulting, supervision, bidding, test_run = _round_split(
        other, (0.42, 0.16, 0.12, 0.09, 0.09, 0.06, 0.06)
    )
    basic, price = _round_split(contingency, (0.80, 0.20))
    return {
        "construction_wan": round(construction, 2),
        "other_wan": other,
        "reserve_wan": contingency,
        "construction_detail": {
            "civil_wan": civil,
            "equipment_wan": equipment,
            "installation_wan": installation,
        },
        "construction_items": [
            {
                "name": "建筑工程",
                "category": "civil",
                "unit": "项",
                "quantity": 1,
                "indicator_yuan": round(civil * 10000.0, 2),
                "amount_wan": civil,
            },
            {
                "name": "设备及工器具购置",
                "category": "equipment",
                "unit": "项",
                "quantity": 1,
                "indicator_yuan": round(equipment * 10000.0, 2),
                "amount_wan": equipment,
            },
            {
                "name": "安装工程",
                "category": "installation",
                "unit": "项",
                "quantity": 1,
                "indicator_yuan": round(installation * 10000.0, 2),
                "amount_wan": installation,
            },
        ],
        "other_detail": {
            "land_wan": land,
            "management_wan": management,
            "design_wan": design,
            "consulting_wan": consulting,
            "supervision_wan": supervision,
            "bidding_wan": bidding,
            "test_run_wan": test_run,
        },
        "contingency_detail": {"basic_wan": basic, "price_wan": price},
    }


def _construction_interest(loan: float, rate: float, build_years: int) -> float:
    if loan <= 0:
        return 0.0
    per = round(loan / build_years, 2)
    draws = [per] * (build_years - 1) + [round(loan - per * (build_years - 1), 2)]
    begin = 0.0
    interest = 0.0
    for draw in draws:
        interest += round((begin + draw / 2.0) * rate, 2)
        begin = round(begin + draw, 2)
    return round(interest, 2)


def _funding(
    construction: float,
    working_capital: float,
    debt_ratio: float,
    loan_rate: float,
    build_years: int,
    subsidy_ratio: float,
) -> tuple[float, float, float, float, float]:
    total = round(construction + working_capital, 2)
    for _ in range(20):
        loan = round(total * debt_ratio, 2)
        interest = _construction_interest(loan, loan_rate, build_years)
        next_total = round(construction + working_capital + interest, 2)
        if next_total == total:
            break
        total = next_total
    loan = round(total * debt_ratio, 2)
    interest = _construction_interest(loan, loan_rate, build_years)
    total = round(construction + working_capital + interest, 2)
    subsidy = round(total * subsidy_ratio, 2)
    capital = round(total - loan - subsidy, 2)
    return total, capital, loan, subsidy, interest


def _turnover(industry: Industry, model: str) -> dict[str, float]:
    if model == "property_sales":
        return {"receivable": 12, "inventory": 90, "cash": 8, "payable": 45}
    if industry.code in {"retail_ecommerce", "agriculture_food"}:
        return {"receivable": 18, "inventory": 52, "cash": 8, "payable": 32}
    if industry.code in {"software_internet", "professional_research_media", "finance"}:
        return {"receivable": 55, "inventory": 5, "cash": 12, "payable": 18}
    return {"receivable": 35, "inventory": 38, "cash": 10, "payable": 25}


def _working_capital(revenue: float, cash_cost: float, turnover: dict[str, float]) -> float:
    # Match the formal table contract: each displayed component is quantized
    # to 0.01 万元 before deriving the net working-capital amount.
    receivable = round(revenue * turnover["receivable"] / 360.0, 2)
    inventory = round(cash_cost * turnover["inventory"] / 360.0, 2)
    cash = round(cash_cost * turnover["cash"] / 360.0, 2)
    payable = round(cash_cost * turnover["payable"] / 360.0, 2)
    return round(max(receivable + inventory + cash - payable, 0.0), 2)


def _cost_items(total: float, industry: Industry, model: str) -> dict[str, float]:
    if model == "property_sales":
        labels = ("销售费用", "管理费用", "物业与招商费用", "其他期间费用")
        weights = (0.38, 0.30, 0.20, 0.12)
    elif industry.code in {"software_internet", "finance", "professional_research_media"}:
        labels = ("人员薪酬", "云资源与外购服务", "市场销售费用", "管理及合规费用")
        weights = (0.46, 0.20, 0.19, 0.15)
    elif industry.code in {"tourism_catering", "healthcare_pharma_eldercare", "education_hr"}:
        labels = ("人员薪酬", "材料与耗材", "能源与运维", "市场及管理费用")
        weights = (0.38, 0.28, 0.19, 0.15)
    else:
        labels = ("原材料及外购服务", "燃料动力", "工资及福利", "维修维护", "管理销售及其他")
        weights = (0.48, 0.13, 0.17, 0.09, 0.13)
    return dict(zip(labels, _round_split(total, weights), strict=True))


def _revenue_spec(
    industry: Industry,
    archetype: Archetype,
    variant: Variant,
    model: str,
    peak_revenue: float,
) -> dict[str, Any]:
    if model == "property_sales":
        peak_absorption = max(variant.absorption)
        total_sales = round(peak_revenue / peak_absorption, 2)
        area = round(max(8.0, industry.construction_wan * archetype.scale / 5000.0), 2) * 10000
        price = round(total_sales * 10000.0 / area, 2)
        return {
            "model": model,
            "saleable_area": area,
            "price_per_sqm": price,
            "absorption": list(variant.absorption),
            "annual_revenue_wan": peak_revenue,
        }
    if model == "tourism":
        visitors = round(650000 * archetype.scale * variant.project_scale, 0)
        max_ramp = max(variant.ramp)
        spend = round(peak_revenue * 10000.0 / max(visitors * max_ramp, 1.0), 2)
        return {
            "model": model,
            "annual_visitors": visitors,
            "visitor_unit": "人次",
            "spend_per_visitor": spend,
            "visitor_ramp": list(variant.ramp),
            "tourism_revenue_components": [
                {
                    "name": "门票及基础服务收入",
                    "basis": "per_visitor",
                    "price_per_visitor_yuan": round(spend * 0.68, 2),
                    "participation_rate": 1.0,
                    "ramp": list(variant.ramp),
                },
                {
                    "name": "二次消费收入",
                    "basis": "per_visitor",
                    "price_per_visitor_yuan": round(spend - round(spend * 0.68, 2), 2),
                    "participation_rate": 1.0,
                    "ramp": list(variant.ramp),
                },
            ],
            "annual_revenue_wan": peak_revenue,
        }
    if model == "gov_payment":
        full_payment = round(peak_revenue / max(variant.ramp), 2)
        return {
            "model": model,
            "annual_gov_payment_wan": full_payment,
            "annual_revenue_wan": peak_revenue,
            "payment_ramp": list(variant.ramp),
            "vat_refund_rate": 0.70 if archetype.code in {"sewage", "sewage_ppp"} else 0.0,
        }
    price = 100.0 if industry.code != "finance" else 1000.0
    primary_revenue, secondary_revenue = _round_split(peak_revenue, (0.68, 0.32))
    max_ramp = max(variant.ramp)
    products = []
    for name, revenue in (
        (archetype.name + "主产品/服务", primary_revenue),
        (archetype.name + "配套产品/服务", secondary_revenue),
    ):
        products.append({
            "name": name,
            "unit": industry.unit,
            "price_per_unit": price,
            "price_unit": "yuan",
            "capacity": round(revenue / (price * max_ramp), 6),
            "ramp": list(variant.ramp),
            "var_cost_rate": min(max(industry.cash_cost_rate * 0.72, 0.18), 0.78),
        })
    return {"model": model, "products": products, "annual_revenue_wan": peak_revenue}


def build_scenario(industry: Industry, archetype: Archetype, variant: Variant) -> dict[str, Any]:
    model = archetype.model or industry.default_model
    scenario_id = f"{industry.code}.{archetype.code}.{variant.code}"
    scale = archetype.scale * variant.project_scale
    construction = round(industry.construction_wan * scale, 2)
    peak_revenue = round(industry.revenue_wan * scale * variant.revenue_factor, 2)
    if model == "property_sales":
        # Base property economics use a 30% peak annual absorption.  Variant
        # absorption must change realised revenue; do not inflate saleable
        # value merely because a downside case has a lower peak sell-through.
        peak_revenue = round(
            peak_revenue * max(variant.absorption) / max(VARIANTS[0].absorption), 2
        )
    operating = archetype.operating
    if not operating:
        peak_revenue = 0.0

    # For property, cost_items are period expenses; development cost is
    # separately recognised by the kernel as inventory COGS.
    base_cash_cost = round(
        industry.revenue_wan * scale * industry.cash_cost_rate * variant.cost_factor,
        2,
    )
    if model == "property_sales":
        base_cash_cost = round(industry.revenue_wan * scale * 0.09 * variant.cost_factor, 2)
        wc_cost_base = round(
            base_cash_cost + construction * max(variant.absorption), 2
        )
    else:
        wc_cost_base = base_cash_cost
    if not operating:
        base_cash_cost = round(construction * 0.012, 2)
        wc_cost_base = 0.0

    turnover = _turnover(industry, model)
    working_revenue = peak_revenue
    if operating:
        from lvke_mcp.domains.finance import revenue_models

        preview_revenue = _revenue_spec(
            industry,
            archetype,
            variant,
            model,
            peak_revenue or 1.0,
        )
        expanded = revenue_models.expand(
            {"revenue": preview_revenue},
            industry.operating_years,
        )
        working_revenue = max(
            [float(item or 0.0) for item in (expanded.get("revenue_by_year") or [])]
            or [peak_revenue]
        )
    if operating:
        from lvke_mcp.domains.finance.working_capital import estimate_from_turnover

        inventory_bases = _round_split(wc_cost_base, (0.45, 0.15, 0.20, 0.20))
        turnover = {
            **turnover,
            "inventory_detail": {
                key: {
                    "days": turnover["inventory"],
                    "annual_base_wan": base,
                    "base_source": f"cost_items:{key}",
                }
                for key, base in zip(
                    ("raw", "fuel", "wip", "finished"), inventory_bases, strict=True,
                )
            },
            "short_term_loan_wan": 0.0,
        }
        working = float(
            estimate_from_turnover(
                revenue=working_revenue,
                cash_cost=wc_cost_base,
                turnover=turnover,
            )["total"]
        )
        turnover["self_funded_wan"] = working
    else:
        working = 0.0
    build_months = max(12, int(round(industry.build_months * (1.15 if variant.code == "slow_ramp" else 1.0))))
    build_years = (build_months + 11) // 12
    if not operating:
        subsidy_ratio = {
            "base": 0.55,
            "small_low_debt": 0.70,
            "large_high_leverage": 0.25,
            "slow_ramp": 0.50,
            "downside_stress": 0.35,
        }[variant.code]
        debt_ratio = min(variant.debt_ratio, max(0.0, 0.90 - subsidy_ratio))
    else:
        subsidy_ratio = 0.10 if industry.code == "government_public_service" else 0.0
        debt_ratio = variant.debt_ratio
    total, capital, loan, subsidy, interest = _funding(
        construction, working, debt_ratio, variant.loan_rate, build_years, subsidy_ratio
    )
    detail = _investment_detail(construction)
    detail.update({"interest_wan": interest, "working_capital_wan": working})

    operating_years = industry.operating_years
    calc_years = build_years + operating_years
    loan_years = min(max(5, round(operating_years * 0.58)), operating_years)
    intangible = round(construction * industry.intangible_rate, 2)
    if model == "property_sales":
        intangible = round(construction * 0.005, 2)

    cost_items = _cost_items(base_cash_cost, industry, model)
    annual_operating_subsidy = 0.0
    if not operating:
        from lvke_mcp.domains.finance.debt import build_debt_schedule

        debt_schedule = build_debt_schedule(
            loan,
            loan_years,
            variant.loan_rate,
            operating_years,
            method=variant.repayment,
            grace_years=variant.grace_years,
            balloon_pct=0.45 if variant.repayment == "balloon" else 0.30,
        )
        peak_debt_service = max((
            float(row.get("principal") or 0.0) + float(row.get("interest") or 0.0)
            for row in debt_schedule
        ), default=0.0)
        coverage_factor = 0.45 if variant.risk_case else (0.85 if variant.code == "slow_ramp" else 1.0)
        annual_operating_subsidy = round(
            (base_cash_cost + peak_debt_service) * coverage_factor, 2
        )
    revenue = _revenue_spec(industry, archetype, variant, model, peak_revenue or 1.0)
    if not operating:
        revenue = {
            "model": "gov_payment",
            "annual_gov_payment_wan": 1.0,
            "annual_revenue_wan": 1.0,
            "payment_ramp": list(variant.ramp),
        }

    finance = {
        "total_investment_wan": total,
        "invest_breakdown": detail,
        "capital_own_wan": capital,
        "loan_wan": loan,
        "gov_subsidy_wan": subsidy,
        "loan_rate": variant.loan_rate,
        "loan_years": loan_years,
        "loan_repay_method": variant.repayment,
        "loan_grace_years": variant.grace_years,
        "loan_balloon_pct": 0.45 if variant.repayment == "balloon" else 0.30,
        "calc_period_years": calc_years,
        "is_operating": operating,
        "annual_revenue_wan": peak_revenue,
        "annual_operating_subsidy_wan": annual_operating_subsidy,
        "cost_items": cost_items,
        "wage_wan": next((value for key, value in cost_items.items() if "工资" in key or "薪酬" in key), 0.0),
        "intangible_assets_wan": intangible,
        "depreciation_years": max(10, min(20, operating_years + 2)),
        "amortization_years": min(10, operating_years),
        "salvage_rate": 0.0 if model == "property_sales" else 0.05,
        "wc_turnover": turnover,
        "vat_rate": industry.vat_rate,
        "vat_input_rate": min(industry.vat_rate, 0.10),
        "income_tax_rate": 0.25,
        "surtax_on_vat": True,
        "surtax_vat_rate": 0.12,
        "input_sources": {
            "total_investment_wan": {
                "evidence_level": "C",
                "source_ref": f"synthetic_matrix:{scenario_id}",
                "note": "确定性合成测试输入，不是实际项目证据",
            },
            "annual_revenue_wan": {
                "evidence_level": "C",
                "source_ref": f"synthetic_matrix:{scenario_id}",
                "note": "行业场景压力测试假设，不得作为真实项目输入",
            },
        },
    }
    spec = {
        "version": "finance_spec.v2",
        "industry": industry.label,
        "invest_type": "government" if industry.code == "government_public_service" else "enterprise",
        "policy_version": "cn_tax_policy.2026-01",
        "industry_profile_version": industry.profile_version,
        "selected_scenario_id": scenario_id,
        "confirmation_status": "confirmed",
        "source_hint": "synthetic_quality_matrix",
        "revenue": revenue,
        "cost": {
            "cost_items": cost_items,
            "cost_policy": "user_items",
            "salvage_rate": finance["salvage_rate"],
        },
        "tax": {
            "income_tax_rate": 0.25,
            "vat_rate": industry.vat_rate,
            "vat_input_rate": finance["vat_input_rate"],
            "surtax_rate": 0.01,
        },
        "assumptions": [
            "本场景仅用于财务模型确定性质量测试",
            "所有输入均为行业化合成假设，证据等级C，不代表真实项目",
        ],
        "field_sources": {
            "revenue": {
                "source": "synthetic_quality_matrix",
                "source_ref": scenario_id,
                "confirmed": True,
            }
        },
    }
    model_scope = "non_operating_funding_balance" if not operating else "project_investment_finance"
    if industry.code == "finance":
        model_scope = "financial_service_project_only"

    return {
        "matrix_version": MATRIX_VERSION,
        "scenario_id": scenario_id,
        "industry_code": industry.code,
        "industry_label": industry.label,
        "industry_profile_version": industry.profile_version,
        "archetype_id": archetype.code,
        "archetype_name": archetype.name,
        "variant_id": variant.code,
        "variant_name": variant.label,
        "project_name": f"{archetype.name}—{variant.label}财务测试场景",
        "invest_type": spec["invest_type"],
        "build_period_months": build_months,
        "finance": finance,
        "spec": spec,
        "expectations": {
            "risk_case": variant.risk_case,
            "must_surface_risk": variant.risk_case,
            "must_have_delivery_13": operating,
            "expected_economic_class": (
                "viable_reference" if variant.code == "base"
                else ("distress" if variant.risk_case else "exploratory")
            ),
            "model_scope": model_scope,
            "scope_disclosure": archetype.scope_note,
            "synthetic_only": True,
        },
        "evidence": {
            "kind": "deterministic_synthetic_scenario",
            "grade": "C",
            "production_claim_allowed": False,
            "boundary": "覆盖与算术验证证据，不是项目可行性或生产成效证据",
        },
    }


def build_industry_scenarios(industry_code: str) -> list[dict[str, Any]]:
    matches = [item for item in INDUSTRIES if item.code == industry_code]
    if not matches:
        raise ValueError(f"unknown industry code: {industry_code}")
    industry = matches[0]
    return [
        build_scenario(industry, archetype, variant)
        for archetype in industry.archetypes
        for variant in VARIANTS
    ]


def build_all_scenarios() -> list[dict[str, Any]]:
    """Build the complete, stable 390-case matrix in catalogue order."""
    return [
        build_scenario(industry, archetype, variant)
        for industry in INDUSTRIES
        for archetype in industry.archetypes
        for variant in VARIANTS
    ]


def compute_scenario(
    scenario: dict[str, Any], *, with_analysis: bool = True,
) -> dict[str, Any]:
    """Run one factory scenario through the production finance kernel."""
    from lvke_mcp.domains.finance import finance_model

    result = finance_model.compute_financials(
        scenario["finance"],
        invest_type=scenario["invest_type"],
        build_period_months=scenario["build_period_months"],
        industry=scenario["industry_label"],
        spec=scenario["spec"],
        _with_analysis=with_analysis,
    )
    if isinstance(result, dict):
        result["scenario_id"] = scenario["scenario_id"]
        result["finance_inputs"] = scenario["finance"]
    return result


__all__ = [
    "INDUSTRIES",
    "MATRIX_VERSION",
    "VARIANTS",
    "build_all_scenarios",
    "build_industry_scenarios",
    "build_scenario",
    "compute_scenario",
    "list_industries",
]
