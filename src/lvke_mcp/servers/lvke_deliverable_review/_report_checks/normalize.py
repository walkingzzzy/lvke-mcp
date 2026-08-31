"""数值与单位归一化、语义判定与近似比较原语。"""

from __future__ import annotations

from typing import Any


from .patterns import (
    _METRIC_PATTERNS,
    _PERIOD_PATTERN,
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _canonical_unit(unit: str) -> str:
    raw = str(unit or "").strip()
    if raw in {"%", "％"}:
        return "%"
    if raw in {"平方米", "平方", "㎡"}:
        return "㎡"
    if raw in {"公里", "千米", "km", "KM"}:
        return "公里"
    if raw in {"万吨", "吨"}:
        return "吨"
    if raw in {"万吨/年", "吨/年"}:
        return "吨/年"
    if raw in {"亿元", "万元", "元"}:
        return "万元"
    return raw


def _canonical_value(value: float, unit: str) -> float:
    if unit == "亿元":
        return value * 10000.0
    if unit == "元":
        return value / 10000.0
    if unit in {"万吨", "万吨/年"}:
        return value * 10000.0
    return value


def _semantic(text: str) -> str:
    for name, pattern in _METRIC_PATTERNS:
        if pattern.search(text):
            return name
    return ""


def _metric_unit_compatible(metric: str, unit: str) -> bool:
    canonical = _canonical_unit(unit)
    if metric in {
        "total_investment", "construction_investment", "working_capital",
        "capital", "debt", "revenue", "operating_cost", "total_cost",
        "net_profit", "profit", "npv", "price", "construction_interest",
        "income_tax", "depreciation", "annual_use", "engineering_cost", "civil_cost",
        "equipment_cost", "installation_cost", "other_investment_cost",
        "contingency", "wage_cost", "maintenance_cost", "utility_cost",
        "insurance_cost", "marketing_cost", "lease_cost", "management_cost",
        "salary_cost", "welfare_cost", "raw_material_cost",
    }:
        return canonical == "万元"
    if metric in {"project_irr", "capital_irr", "discount_rate", "bep"}:
        return canonical == "%"
    if metric in {"payback", "static_payback", "dynamic_payback"}:
        return canonical in {"年", "月", "个月"}
    if metric in {"dscr", "icr"}:
        return canonical in {"", "倍"}  # 无量纲或"倍"，不接受 %
    if metric == "room_count":
        return canonical == "间"
    if metric == "area":
        return canonical == "㎡"
    if metric == "capacity":
        return canonical in {"吨", "吨/年"}
    if metric == "market_radius":
        return canonical == "公里"
    return True


def _semantic_near(text: str, start: int, end: int, unit: str) -> str:
    candidates: list[tuple[int, int, int, str]] = []
    # Only punctuation is a reliable clause boundary here.  Chinese
    # conjunctions are also part of canonical metric names (for example
    # ``设备及工器具购置费``); treating ``及/和/与`` as separators truncates
    # that label and can bind its amount to the following metric instead.
    #
    # 顿号必须算边界：中文成本/投资明细就是用它并列的（"主要原材料 46,968.00
    # 万元、水电能源 1,600.00 万元"）。此前顿号不是边界，于是"水电能源"这个
    # 标签能跨过顿号绑到前一项的 46,968 上，批量产出"utility_cost 无法在 run
    # 中复现"这类假 P0 —— 引擎数字是对的，错的是标签配对。
    #
    # 括号是**有方向**的：左括号只能当 clause 的起点、右括号只能当终点。若把
    # 两者都当双向边界，"工程费用 54,000.00 万元（建筑工程费 18,000.00、…）"
    # 里括注内的分项会把括号外的"工程费用"当成自己的标签。
    clause_markers = ("，", ",", "；", ";", "。", "、", "：", ":")
    open_markers = clause_markers + ("（", "(")
    close_markers = clause_markers + ("）", ")")
    clause_start = max(
        (text.rfind(marker, 0, start) for marker in open_markers),
        default=-1,
    ) + 1
    following = [
        position for marker in close_markers
        if (position := text.find(marker, end)) >= 0
    ]
    clause_end = min(following) if following else len(text)
    for order, (name, pattern) in enumerate(_METRIC_PATTERNS):
        if not _metric_unit_compatible(name, unit):
            continue
        for match in pattern.finditer(text):
            if match.start() < clause_start or match.end() > clause_end:
                continue
            if match.end() <= start:
                distance = start - match.end()
                direction = 0
            elif match.start() >= end:
                distance = match.start() - end
                direction = 1
                # 财务句式通常是“指标+数值”。后置关键词可作回退，但
                # 不应抢走前一分句已明确标注的数值。“8%折现率”
                # 这种紧邻后置标签则应优先。
                if distance > 3:
                    distance += 24
            else:
                distance = 0
                direction = 0
            candidates.append((distance, direction, order, name))
    return min(candidates)[3] if candidates else ""


def _period_near(text: str, start: int, end: int) -> str:
    candidates: list[tuple[int, int, str]] = []
    for match in _PERIOD_PATTERN.finditer(text):
        # Do not treat the claim's own duration (for example 8.04年) as its period.
        if match.start() < end and match.end() > start:
            continue
        preceding = match.end() <= start
        distance = start - match.end() if preceding else match.start() - end
        candidates.append((distance + (0 if preceding else 12), 0 if preceding else 1, match.group(0)))
    return min(candidates, default=(0, 0, ""))[2]


def _flatten_numbers(
    value: Any, path: str = "root", output: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if output is None:
        output = []
    if isinstance(value, dict):
        for key, child in value.items():
            _flatten_numbers(child, f"{path}.{key}", output)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _flatten_numbers(child, f"{path}[{index}]", output)
    else:
        numeric = _number(value)
        if numeric is not None:
            output.append({"path": path, "value": numeric})
    return output


def _within_tolerance(actual: float, expected: float, *, metric: str = "") -> bool:
    tolerance = max(0.01, abs(expected) * (0.005 if metric in {"project_irr", "capital_irr"} else 1e-6))
    return abs(actual - expected) <= tolerance
