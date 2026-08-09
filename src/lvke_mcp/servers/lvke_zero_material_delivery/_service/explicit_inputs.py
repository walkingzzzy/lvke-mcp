"""Deterministic extraction of explicitly stated project parameters.

零材料链此前只保存原句，句子里写明的"50公里、10站、2028至2032年"不会进入
假设包，行业种子因此把明确参数一起覆盖掉（建设期回落到 24 个月）。

本模块只做确定性抽取：命中固定模式才产出值，绝不猜测。抽到的值按
``sentence_explicit_input`` 优先级固化到 DeliveryIntent，行业矩阵只能补缺、
不能覆盖。识别到疑似数字但无法映射到已知字段时，登记为 unmapped 并由调用
方以 ``explicit_input_unmapped`` 显式暴露，不静默丢弃。
"""

from __future__ import annotations

import re
from typing import Any

SOURCE_SENTENCE = "sentence_explicit_input"

# 每条规则：字段名、正则、单位、把匹配文本换算成目标单位的函数。
_NUMBER = r"(\d+(?:\.\d+)?)"


def _f(value: str) -> float:
    return float(value)


_RULES: tuple[tuple[str, str, str, Any], ...] = (
    ("route_length_km", rf"{_NUMBER}\s*(?:公里|千米|km|KM|Km)", "公里", _f),
    ("station_count", rf"{_NUMBER}\s*(?:座|个)?\s*(?:车站|站点|站)(?!间)", "座", lambda v: int(float(v))),
    ("design_speed_kmh", rf"(?:时速|设计速度|速度目标)?\s*{_NUMBER}\s*(?:km/h|公里/小时|千米/小时)", "km/h", _f),
    ("total_investment_wan", rf"{_NUMBER}\s*亿元", "万元", lambda v: float(v) * 10000.0),
    ("operating_period_years", rf"(?:运营期|运营)\s*{_NUMBER}\s*年", "年", lambda v: int(float(v))),
)

# 建设期区间：2028至2032年 → 60 个月（含首尾年）。
_YEAR_SPAN = re.compile(
    r"(20\d{2})\s*(?:年)?\s*(?:至|到|-|—|~|－)\s*(20\d{2})\s*年?"
)

# 疑似量纲数字：用于发现"写了但没被任何规则接住"的明确输入。
_CANDIDATE = re.compile(
    rf"{_NUMBER}\s*(公里|千米|km|座|个|站|亿元|万元|年|km/h|%|台|辆|编组)"
)


def extract_explicit_inputs(sentence: str) -> dict[str, Any]:
    """Return ``{"fields": {...}, "unmapped": [...]}`` for one sentence.

    ``fields`` 中每项含 value/unit/source/raw，便于下游追溯到原文。
    """

    text = str(sentence or "")
    fields: dict[str, dict[str, Any]] = {}
    consumed: list[tuple[int, int]] = []

    span = _YEAR_SPAN.search(text)
    if span:
        start_year, end_year = int(span.group(1)), int(span.group(2))
        if end_year >= start_year:
            months = (end_year - start_year + 1) * 12
            fields["build_period_months"] = {
                "value": months,
                "unit": "月",
                "source": SOURCE_SENTENCE,
                "raw": span.group(0),
                "derivation": f"{start_year}-{end_year} 含首尾年共 {end_year - start_year + 1} 年",
            }
            fields["construction_start_year"] = {
                "value": start_year, "unit": "年", "source": SOURCE_SENTENCE, "raw": span.group(0),
            }
            fields["construction_end_year"] = {
                "value": end_year, "unit": "年", "source": SOURCE_SENTENCE, "raw": span.group(0),
            }
            consumed.append(span.span())

    for name, pattern, unit, convert in _RULES:
        match = re.search(pattern, text)
        if not match:
            continue
        if any(start <= match.start() < end for start, end in consumed):
            continue
        try:
            value = convert(match.group(1))
        except (TypeError, ValueError):
            continue
        fields[name] = {
            "value": value,
            "unit": unit,
            "source": SOURCE_SENTENCE,
            "raw": match.group(0),
        }
        consumed.append(match.span())

    unmapped: list[dict[str, str]] = []
    for match in _CANDIDATE.finditer(text):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        unmapped.append({"raw": match.group(0), "position": str(match.start())})

    return {"fields": fields, "unmapped": unmapped}
