"""Shared English-to-Chinese industry aliases for parameter matching.

Used by:
- domains/finance/scale_infer.py (infer_build_scale, infer_headcount)
- domains/finance/env_templates.py (resolve_env_template, env_profile)
- servers/lvke_project_planning/_lifecycle/build_scale.py (get_industry_constraints)

All three resolve industry parameters from YAML with Chinese keys but need to
support English industry_code inputs from ProjectContext.
"""

# English tokens → Chinese YAML key
INDUSTRY_ALIASES: dict[str, str] = {
    "hotel": "酒店",
    "hospitality": "酒店",
    "energy": "能源",
    "power": "能源",
    "solar": "能源",
    "photovoltaic": "能源",
    "pv": "能源",
    "光伏": "能源",
    "wind": "能源",
    "storage": "能源",
    "cultural": "文旅",
    "tourism": "文旅",
    "travel": "文旅",
    "real_estate": "房地产",
    "real estate": "房地产",
    "property": "房地产",
    "housing": "房地产",
    "infrastructure": "基础设施",
    "municipal": "基础设施",
    "public_service": "公共服务",
    "public service": "公共服务",
    "government": "公共服务",
    "manufacturing": "制造",
    "manufacture": "制造",
    "logistics": "仓储物流",
    "warehouse": "仓储物流",
    "agriculture": "农业",
    "agri": "农业",
    "farming": "农业",
    "chemical": "化工",
    "chemicals": "化工",
    "electronics": "电子",
    "electronic": "电子",
    "machinery": "机械",
    "mechanical": "机械",
    "mineral": "矿产加工",
    "mineral_processing": "矿产加工",
    "mining": "矿产加工",
    "ore": "矿产加工",
}


def normalize_industry(industry: str) -> str:
    """Normalize English industry_code to Chinese YAML key.

    Returns the normalized key if a match is found, otherwise returns the
    original input (lowercase). Callers then match against YAML keys.
    """
    ind = str(industry or "").strip().lower()
    # Prefer the most specific token so composite codes such as
    # ``warehouse_storage`` resolve to 仓储物流 instead of 能源.
    for en_token, cn_key in sorted(
        INDUSTRY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if en_token in ind:
            return cn_key
    return ind
