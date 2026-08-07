"""行完整性判定原语：item id、正数/非负、存货与周转分量、年份序列。"""

from __future__ import annotations

import hashlib
from typing import Any

from .base import (
    _canonical,
    _record,
    _rows,
)


def _stable_item_id(domain: str, row: dict[str, Any], index: int) -> str:
    explicit = str(row.get("item_id") or row.get("id") or "").strip()
    if explicit:
        return explicit
    identity = {
        "domain": domain,
        "name": row.get("name") or row.get("category") or "",
        "unit": row.get("unit") or "",
        "index": index,
    }
    return f"{domain[:8]}_{hashlib.sha256(_canonical(identity).encode('utf-8')).hexdigest()[:12]}"


def _rows_with_item_ids(domain: str, value: Any) -> list[dict[str, Any]]:
    rows = _rows(value)
    for index, row in enumerate(rows):
        row["item_id"] = _stable_item_id(domain, row, index)
    return rows


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _nonnegative_present(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        return float(value) >= 0
    except (TypeError, ValueError):
        return False


def _inventory_complete(value: Any) -> bool:
    row = _record(value)
    aliases = (
        ("raw", "raw_material", "materials", "原材料"),
        ("fuel", "energy", "燃料", "动力", "燃料及动力"),
        ("wip", "work_in_progress", "在产品"),
        ("finished", "finished_goods", "fg", "产成品"),
    )
    def complete_component(raw: Any) -> bool:
        if isinstance(raw, dict):
            base = raw.get("annual_base_wan")
            if base is None:
                base = raw.get("base_wan")
            return _positive(raw.get("days")) and _positive(base)
        return False

    return all(
        any(complete_component(row.get(key)) for key in group)
        for group in aliases
    )


def _turnover_component_complete(value: Any) -> bool:
    if isinstance(value, dict):
        base = value.get("annual_base_wan")
        if base is None:
            base = value.get("base_wan")
        return (
            _positive(value.get("days"))
            and _positive(base)
            and bool(str(value.get("base_source") or "").strip())
        )
    return _positive(value)


def _year_sequence_issues(
    rows: list[dict[str, Any]],
    *,
    label: str,
    expected_start: int | None = None,
    expected_end: int | None = None,
) -> list[str]:
    if not rows:
        return []
    years: list[int] = []
    issues: list[str] = []
    for row in rows:
        raw_year = row.get("year") if row.get("year") is not None else row.get("period")
        try:
            years.append(int(raw_year))
        except (TypeError, ValueError):
            issues.append(f"{label} 缺少有效 year")
    if len(years) != len(rows):
        return issues
    if len(set(years)) != len(years):
        issues.append(f"{label} 年份重复: {years}")
    ordered = sorted(years)
    if years != ordered:
        issues.append(f"{label} 年份未排序: {years}")
    if ordered and ordered != list(range(ordered[0], ordered[-1] + 1)):
        issues.append(f"{label} 年份不连续: {ordered}")
    if expected_start is not None and ordered and ordered[0] != expected_start:
        issues.append(f"{label} 必须从 year={expected_start} 开始")
    if expected_end is not None and ordered and ordered[-1] != expected_end:
        issues.append(f"{label} 必须结束于 year={expected_end}")
    return issues
