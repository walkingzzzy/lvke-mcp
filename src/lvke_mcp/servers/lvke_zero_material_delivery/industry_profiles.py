"""Versioned first-wave industry semantics for zero-material estimates."""

from __future__ import annotations

from typing import Any

PROFILE_VERSION = "zero-material-industry-profiles.2026-08.v1"

# These profiles describe how a controlled assumption is interpreted. Numeric
# seeds remain owned by the deterministic finance scenario factory.
PROFILES: dict[str, dict[str, Any]] = {
    "tourism_catering": {
        "applicability": ["景区提升", "主题乐园", "度假酒店", "文旅演艺", "营地", "餐饮配套"],
        "revenue_model": "tourism",
        "revenue_drivers": ["annual_visitors", "spend_per_visitor", "visitor_ramp", "secondary_spend_rate"],
        "investment_structure": ["civil", "equipment", "installation", "other", "contingency", "working_capital"],
        "cost_structure": ["labor", "energy", "maintenance", "marketing", "materials_and_consumables"],
        "labor_rule": "按运营容量、开放时长、安全岗位与服务半径估算定员",
        "regional_adjustment": ["客源半径", "旅游季节性", "当地人工", "建设成本指数"],
        "sensitivity_variables": ["visitor_volume", "spend_per_visitor", "construction_investment", "operating_cost"],
    },
    "manufacturing": {
        "applicability": ["装备制造", "电子信息", "新材料", "汽车零部件", "食品及医药生产"],
        "revenue_model": "product_sales",
        "revenue_drivers": ["capacity", "capacity_utilization", "unit_price", "ramp"],
        "investment_structure": ["civil", "production_equipment", "installation", "other", "contingency", "working_capital"],
        "cost_structure": ["material", "fuel_power", "direct_labor", "maintenance", "management"],
        "labor_rule": "按产线班次、设备自动化率、辅助生产与管理比例估算定员",
        "regional_adjustment": ["工业用地与厂房造价", "能源价格", "物流条件", "当地人工"],
        "sensitivity_variables": ["product_price", "capacity_utilization", "material_cost", "construction_investment"],
    },
    "environment_utilities": {
        "applicability": ["供水", "污水处理", "固废处理", "光伏", "风电", "储能"],
        "revenue_model": "product_sales_or_gov_payment",
        "revenue_drivers": ["design_capacity", "utilization", "tariff_or_service_fee", "payment_ramp"],
        "investment_structure": ["civil", "process_equipment", "pipeline_or_grid", "installation", "other", "contingency"],
        "cost_structure": ["energy", "chemicals_or_consumables", "labor", "maintenance", "disposal_or_grid_fee"],
        "labor_rule": "按处理规模、站点数量、运行班次和法定值守岗位估算定员",
        "regional_adjustment": ["资源条件", "环保排放标准", "处理或上网价格", "建设成本指数"],
        "sensitivity_variables": ["utilization", "tariff", "energy_cost", "construction_investment"],
    },
    "park_infrastructure": {
        "applicability": ["产业园", "物流园", "市政道路", "城市更新", "智慧停车", "基础设施配套"],
        "revenue_model": "property_sales_or_gov_payment",
        "revenue_drivers": ["developable_area", "sale_or_rent_rate", "absorption", "government_payment"],
        "investment_structure": ["land_or_site", "civil", "municipal_support", "equipment", "other", "contingency"],
        "cost_structure": ["development_cost", "operations", "maintenance", "marketing", "management"],
        "labor_rule": "建设期按项目管理配置，运营期按可租售面积、设施数量和物业服务标准估算",
        "regional_adjustment": ["土地及征拆", "当地建安指数", "产业吸纳能力", "租售价格"],
        "sensitivity_variables": ["construction_investment", "absorption", "sale_or_rent_rate", "financing_cost"],
    },
    "urban_rail_transit": {
        "applicability": ["城市轨道交通", "地铁", "轻轨", "市域(郊)铁路", "有轨电车", "轨道交通延伸线"],
        "revenue_model": "gov_payment",
        "revenue_drivers": [
            "annual_passenger_trips",
            "average_fare_yuan",
            "ridership_ramp",
            "government_operating_subsidy",
        ],
        "investment_structure": [
            "civil_and_tunneling",
            "track_and_station",
            "vehicles",
            "electromechanical_and_signalling",
            "depot_and_parking",
            "land_and_relocation",
            "other",
            "contingency",
        ],
        "cost_structure": [
            "traction_power",
            "operating_labor",
            "vehicle_and_facility_maintenance",
            "station_operations",
            "management",
        ],
        "labor_rule": "按线路长度、车站数量、运营时长、行车间隔与法定值守岗位估算定员",
        "regional_adjustment": ["敷设方式与地质条件", "征地拆迁", "票价机制与财政补贴能力", "建安造价指数"],
        "sensitivity_variables": [
            "annual_passenger_trips",
            "average_fare_yuan",
            "construction_investment",
            "government_operating_subsidy",
        ],
    },
    "commercial_professional_services": {
        "applicability": ["商业综合服务", "咨询", "检验检测", "研发中心", "媒体广告", "专业服务平台"],
        "revenue_model": "product_sales",
        "revenue_drivers": ["service_volume", "average_fee", "utilization", "client_ramp"],
        "investment_structure": ["premises", "professional_equipment", "software_and_intangible", "other", "contingency", "working_capital"],
        "cost_structure": ["professional_labor", "premises", "technology", "marketing", "administration"],
        "labor_rule": "按项目或客户容量、专业人员产能、后台支持比例和管理跨度估算定员",
        "regional_adjustment": ["客户密度", "专业人工成本", "办公或实验场地成本", "服务定价"],
        "sensitivity_variables": ["service_volume", "average_fee", "professional_labor_cost", "utilization"],
    },
}


def get_profile(industry_code: str) -> dict[str, Any]:
    """Return a detached profile so callers cannot mutate module state."""

    profile = PROFILES.get(industry_code)
    if profile is None:
        raise ValueError(f"unsupported zero-material industry: {industry_code}")
    return {
        "profile_version": PROFILE_VERSION,
        "industry_code": industry_code,
        **{key: list(value) if isinstance(value, list) else value for key, value in profile.items()},
    }


__all__ = ["PROFILE_VERSION", "PROFILES", "get_profile"]