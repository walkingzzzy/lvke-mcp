"""Controlled unit dictionary and canonical unit comparison.

Exact, auditable conversions only.  The dictionary is opt-in and never uses
fuzzy unit inference; every applied rule is returned with its basis.
"""

from __future__ import annotations

import re
from typing import Any

from lvke_mcp.domains.research.quantitative import _normalize_unit

CONTROLLED_UNIT_RULES: dict[str, tuple[str, float, str]] = {
    "元": ("万元", 0.0001, "受控单位字典：1万元=10000元"),
    "千元": ("万元", 0.1, "受控单位字典：1万元=10千元"),
    "万元": ("万元", 1.0, "受控单位字典：单位恒等"),
    "百万元": ("万元", 100.0, "受控单位字典：1百万元=100万元"),
    "亿元": ("万元", 10000.0, "受控单位字典：1亿元=10000万元"),
    "kW": ("MW", 0.001, "受控SI单位字典：1MW=1000kW"),
    "MW": ("MW", 1.0, "受控SI单位字典：单位恒等"),
    "GW": ("MW", 1000.0, "受控SI单位字典：1GW=1000MW"),
    "%": ("%", 1.0, "受控比例单位字典：百分数恒等"),
    "倍": ("倍", 1.0, "受控倍数单位字典：单位恒等"),
}

# Canonical unit forms for Gate 3 comparison (source wording vs expected_unit).
_UNIT_CANON = {
    "千瓦时": "kwh", "兆瓦时": "mwh", "吉瓦时": "gwh", "度": "kwh",
    "千瓦": "kw", "兆瓦": "mw", "吉瓦": "gw",
    "平米": "平方米", "㎡": "平方米", "m²": "平方米", "m2": "平方米",
    "平方公里": "平方公里",
    "ha": "公顷", "公顷": "公顷",
    "标煤": "吨标煤", "吨标煤": "吨标煤",
    "公里": "km", "千米": "km",
}


def controlled_unit_rules() -> list[dict[str, Any]]:
    return [
        {
            "source_unit": source,
            "target_unit": target,
            "factor": factor,
            "conversion_basis": basis,
        }
        for source, (target, factor, basis) in CONTROLLED_UNIT_RULES.items()
    ]


def _canon_unit(unit: str) -> str:
    """Canonicalise a possibly-composite unit for Gate 3 equality.

    ``元/千瓦时`` and ``元/kWh`` must compare equal; ``千瓦`` and ``kW`` too.
    Each side of a ``/`` is normalised independently, so numerator and
    denominator aliases both resolve.
    """

    parts = re.split(r"[/／]", str(unit or "").strip())
    canon: list[str] = []
    for part in parts:
        token = _normalize_unit(part).lower()
        canon.append(_UNIT_CANON.get(token, token))
    return "/".join(canon)
