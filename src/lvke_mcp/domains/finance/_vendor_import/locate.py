"""行列定位原语：行标签、期间序列、按标签取序列与合计。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

from .base import (
    _cell_parts,
    _norm,
    _to_float,
)


def _rows(sheet: dict[str, Any]) -> dict[int, dict[int, tuple[str, Any]]]:
    grouped: dict[int, dict[int, tuple[str, Any]]] = defaultdict(dict)
    for cell, value in (sheet.get("values") or {}).items():
        row, col = _cell_parts(cell)
        if row and col:
            grouped[row][col] = (cell, value)
    return grouped


def _row_label(row: dict[int, tuple[str, Any]], max_label_col: int = 4) -> str:
    return " / ".join(
        str(value).strip()
        for col, (_, value) in sorted(row.items())
        if col <= max_label_col and isinstance(value, str) and str(value).strip()
    )


def _longest_period_sequence(row: dict[int, tuple[str, Any]]) -> dict[int, int]:
    points = []
    for col, (_, value) in sorted(row.items()):
        number = _to_float(value)
        if number is None or abs(number - round(number)) > 1e-9:
            continue
        integer = int(round(number))
        if 0 <= integer <= 100:
            points.append((col, integer))
    best: list[tuple[int, int]] = []
    current: list[tuple[int, int]] = []
    for point in points:
        if current and (point[0] != current[-1][0] + 1 or point[1] != current[-1][1] + 1):
            if len(current) > len(best):
                best = current
            current = []
        current.append(point)
    if len(current) > len(best):
        best = current
    return dict(best) if len(best) >= 3 else {}


def _period_headers(sheet: dict[str, Any], before_row: int) -> dict[int, int]:
    best: dict[int, int] = {}
    for row_index, row in _rows(sheet).items():
        if row_index >= before_row:
            continue
        candidate = _longest_period_sequence(row)
        if len(candidate) > len(best):
            best = candidate
    return best


def _series_for_row(sheet: dict[str, Any], row_index: int) -> list[dict[str, Any]]:
    row = _rows(sheet).get(row_index) or {}
    headers = _period_headers(sheet, row_index)
    result = []
    for col, period in sorted(headers.items()):
        cell_value = row.get(col)
        if not cell_value:
            continue
        value = _to_float(cell_value[1])
        if value is None:
            continue
        result.append({"period": period, "cell": cell_value[0], "value": value})
    return result


def _find_row(
    sheet: Optional[dict[str, Any]],
    keywords: Iterable[str],
    *,
    exclude: Iterable[str] = (),
) -> tuple[int, str]:
    if not sheet:
        return 0, ""
    wanted = [_norm(x) for x in keywords]
    excluded = [_norm(x) for x in exclude]
    for row_index, row in sorted(_rows(sheet).items()):
        label = _row_label(row)
        normalized = _norm(label)
        if wanted and all(word in normalized for word in wanted) and not any(
            word in normalized for word in excluded
        ):
            return row_index, label
    return 0, ""


def _series_by_label(
    sheet: Optional[dict[str, Any]],
    keywords: Iterable[str],
    *,
    exclude: Iterable[str] = (),
) -> list[dict[str, Any]]:
    row_index, _ = _find_row(sheet, keywords, exclude=exclude)
    return _series_for_row(sheet, row_index) if row_index else []


def _total_for_row(sheet: dict[str, Any], row_index: int) -> Optional[float]:
    rows = _rows(sheet)
    row = rows.get(row_index) or {}
    total_columns: list[int] = []
    for candidate_row, cells in rows.items():
        if candidate_row >= row_index:
            continue
        for col, (_, value) in cells.items():
            if isinstance(value, str) and "合计" in _norm(value):
                total_columns.append(col)
    for col in sorted(set(total_columns), reverse=True):
        if col in row:
            value = _to_float(row[col][1])
            if value is not None:
                return value
    numbers = []
    for col, (_, raw) in row.items():
        if col <= 2:
            continue
        value = _to_float(raw)
        if value is not None:
            numbers.append(value)
    return max(numbers, key=lambda item: abs(item)) if numbers else None


def _total_by_label(
    sheet: Optional[dict[str, Any]],
    keywords: Iterable[str],
    *,
    exclude: Iterable[str] = (),
) -> Optional[float]:
    if not sheet:
        return None
    row_index, _ = _find_row(sheet, keywords, exclude=exclude)
    return _total_for_row(sheet, row_index) if row_index else None


def _first_numeric_on_row(sheet: Optional[dict[str, Any]], row_index: int) -> Optional[float]:
    if not sheet or not row_index:
        return None
    for col, (_, raw) in sorted((_rows(sheet).get(row_index) or {}).items()):
        if col < 3:
            continue
        value = _to_float(raw)
        if value is not None:
            return value
    return None


def _row_value(
    sheet: dict[str, Any], row_index: int, col_index: int,
) -> tuple[str, Any]:
    return (_rows(sheet).get(row_index) or {}).get(col_index, ("", None))


def _series_values(series: list[dict[str, Any]] | None) -> list[float]:
    """Flatten period series to a 0-based year vector (period 1 -> index 0).

    Sparse period maps (e.g. loan only in year 2) are preserved as zeros in
    earlier years — critical for non-uniform construction/loan phasing (B-2).
    """
    if not series:
        return []
    by_period: dict[int, float] = {}
    order: list[float] = []
    for item in series:
        if not isinstance(item, dict):
            continue
        value = _to_float(item.get("value"))
        if value is None:
            continue
        period = item.get("period")
        try:
            p = int(period) if period is not None else None
        except (TypeError, ValueError):
            p = None
        if p is not None and p >= 1:
            by_period[p] = round(float(value), 6)
        else:
            order.append(round(float(value), 6))
    if by_period:
        max_p = max(by_period)
        return [float(by_period.get(i, 0.0)) for i in range(1, max_p + 1)]
    return order
