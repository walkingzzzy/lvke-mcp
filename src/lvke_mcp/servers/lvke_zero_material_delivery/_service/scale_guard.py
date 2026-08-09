"""Pre-run project-scale reconciliation across intent, assumptions and spec.

50 公里、10 座车站的轨道线按通用单体项目种子（约 11.6 亿元）运行时，算术
全部自洽，十三表也能通过勾稽，因此 ``finance_status`` 会显示 ok——但业务尺度
明显错误。本模块在 FinanceRun 之前做四方对账，尺度明显不匹配时返回
``project_scale_inconsistent`` 并阻止建 run。

设计约束：
- 只报不改。区间用于提示，绝不用区间自动改写用户或证据给定的值。
- 只在**双方都有值**时比较；缺值是 missing_inputs 问题，不在本检查范围。
- 阈值宽松（数量级级别），只拦明显错配，不做精度审查。
"""

from __future__ import annotations

from typing import Any

# 城市轨道交通投资强度经验区间（亿元/公里）。仅用于提示，不用于改值。
# 下限对应地面/高架简易制式，上限对应全地下高造价段。
URBAN_RAIL_INVEST_INTENSITY = (3.0, 12.0)

# 通用尺度失配容忍倍数：实际值与按强度推算值相差超过该倍数即视为明显错配。
_SCALE_TOLERANCE = 3.0


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def check_project_scale(
    *,
    industry_code: str,
    explicit_inputs: dict[str, Any] | None,
    field_values: dict[str, Any],
) -> dict[str, Any]:
    """Return ``{"ok": bool, "issues": [...], "advisories": [...]}``.

    ``issues`` 非空即应阻断 FinanceRun；``advisories`` 只是提示。
    """

    issues: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    explicit = dict(explicit_inputs or {})

    def explicit_value(name: str) -> float | None:
        item = explicit.get(name)
        if isinstance(item, dict):
            return _number(item.get("value"))
        return _number(item)

    route_km = explicit_value("route_length_km")
    total_investment_wan = _number(field_values.get("total_investment_wan"))

    # 1. 线路长度 × 投资强度 vs 总投资（仅轨道类且两者都有值时）
    if str(industry_code) == "urban_rail_transit" and route_km and total_investment_wan:
        low_wan = route_km * URBAN_RAIL_INVEST_INTENSITY[0] * 10000.0
        high_wan = route_km * URBAN_RAIL_INVEST_INTENSITY[1] * 10000.0
        if total_investment_wan * _SCALE_TOLERANCE < low_wan:
            issues.append({
                "code": "project_scale_inconsistent",
                "field": "total_investment_wan",
                "detail": (
                    f"线路长度 {route_km} 公里，按 "
                    f"{URBAN_RAIL_INVEST_INTENSITY[0]}~{URBAN_RAIL_INVEST_INTENSITY[1]} 亿元/公里"
                    f"推算投资约 {low_wan / 10000:.0f}~{high_wan / 10000:.0f} 亿元，"
                    f"当前 {total_investment_wan / 10000:.2f} 亿元，明显偏小"
                ),
                "observed_wan": total_investment_wan,
                "expected_range_wan": [round(low_wan, 2), round(high_wan, 2)],
                "resolution": "以工可批复或可比项目证据显式提供总投资，不得沿用通用行业种子",
            })
        elif total_investment_wan > high_wan * _SCALE_TOLERANCE:
            issues.append({
                "code": "project_scale_inconsistent",
                "field": "total_investment_wan",
                "detail": (
                    f"线路长度 {route_km} 公里，当前投资 "
                    f"{total_investment_wan / 10000:.2f} 亿元明显偏大"
                ),
                "observed_wan": total_investment_wan,
                "expected_range_wan": [round(low_wan, 2), round(high_wan, 2)],
                "resolution": "核对总投资口径或线路长度",
            })
        elif not (low_wan <= total_investment_wan <= high_wan):
            advisories.append({
                "code": "invest_intensity_outside_reference",
                "detail": (
                    f"投资强度 {total_investment_wan / max(route_km, 1e-9) / 10000:.2f} 亿元/公里"
                    f"落在参考区间 {URBAN_RAIL_INVEST_INTENSITY[0]}~"
                    f"{URBAN_RAIL_INVEST_INTENSITY[1]} 之外，需证据说明"
                ),
            })

    # 2. 明确输入与最终取值是否一致（防止下游把明确值改写回种子）
    for name, item in explicit.items():
        if not isinstance(item, dict) or "value" not in item:
            continue
        stated = _number(item.get("value"))
        actual = _number(field_values.get(name))
        if stated is None or actual is None:
            continue
        if abs(stated - actual) > max(abs(stated) * 0.001, 1e-6):
            issues.append({
                "code": "explicit_input_overridden",
                "field": name,
                "detail": f"句子明确 {stated}，运行取值 {actual}",
                "resolution": "行业种子只能补缺，不得覆盖明确输入",
            })

    return {"ok": not issues, "issues": issues, "advisories": advisories}
