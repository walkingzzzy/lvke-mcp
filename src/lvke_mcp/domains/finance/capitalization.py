"""Investment → asset capitalization mapping (方案 §5.1).

规范：
- 建设投资 = 工程费用 + 工程建设其他费用 + 预备费
- 建设期融资费用（建设期利息）资本化进固定资产（可研简化默认）
- 无形资产 / 其他资产可从用户输入单独给出，并从固定资产折旧基数中扣除
- 房产去化（property_inventory）开发产品按存货，不进固定资产折旧
"""

from __future__ import annotations

from typing import Any


def map_assets(
    *,
    construction: float,
    interest: float,
    intangible_wan: float = 0.0,
    other_assets_wan: float = 0.0,
    property_inventory: bool = False,
    capitalize_interest: bool = True,
) -> dict[str, Any]:
    """把投资分项映射为资产类别原值。

    Returns keys:
      fixed_asset_gross  固定资产原值（含资本化利息，未扣无形）
      fixed_asset_dep_base  折旧基数（扣无形/其他资产）
      intangible_original
      other_assets_original
      inventory_development  开发产品存货原值（房产）
      capitalized_interest
      method
    """
    construction = float(construction or 0.0)
    interest = float(interest or 0.0)
    intangible = max(float(intangible_wan or 0.0), 0.0)
    other_assets = max(float(other_assets_wan or 0.0), 0.0)
    cap_int = round(interest, 2) if capitalize_interest else 0.0

    if property_inventory:
        # 开发产品=存货；建设期利息仍可资本化进存货成本（可研简化：并入开发成本）
        inv = round(construction + cap_int, 2)
        return {
            "fixed_asset_gross": 0.0,
            "fixed_asset_dep_base": 0.0,
            "intangible_original": round(intangible, 2),
            "other_assets_original": round(other_assets, 2),
            "inventory_development": inv,
            "capitalized_interest": cap_int,
            "method": "property_inventory_cogs",
            "note": "房地产去化：开发成本按存货核算，不形成固定资产折旧基数",
        }

    gross = round(construction + cap_int, 2)
    # 无形/其他从折旧基数扣除，避免双重计入（M1）
    dep_base = round(max(gross - intangible - other_assets, 0.0), 2)
    return {
        "fixed_asset_gross": gross,
        "fixed_asset_dep_base": dep_base,
        "intangible_original": round(intangible, 2),
        "other_assets_original": round(other_assets, 2),
        "inventory_development": 0.0,
        "capitalized_interest": cap_int,
        "method": "fixed_asset_plus_intangible",
        "note": (
            "固定资产原值=建设投资+资本化建设期利息；"
            "折旧基数=固定资产原值−无形资产−其他资产"
        ),
    }
