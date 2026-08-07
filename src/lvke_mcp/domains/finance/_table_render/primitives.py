"""行列原语：期间取值、序列、求和与末值。"""

from __future__ import annotations

from typing import Any, Optional



def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _item_row_period_values(body: dict[str, Any], item_label: str) -> list[Any]:
    """Read period values from an item-row layout by item label."""
    columns = list(body.get("columns") or [])
    rows = list(body.get("rows") or [])
    keys = [
        str(column.get("key") or "")
        for column in columns
        if isinstance(column, dict)
    ]
    if "item" not in keys:
        return []
    item_index = keys.index("item")
    period_indices = [i for i, key in enumerate(keys) if key.startswith("period_")]
    if not period_indices:
        # Fallback to amount/total single-value layouts.
        for amount_key in ("amount", "total"):
            if amount_key in keys:
                amount_index = keys.index(amount_key)
                for row in rows:
                    if (
                        isinstance(row, (list, tuple))
                        and item_index < len(row)
                        and str(row[item_index] or "").strip() == item_label
                        and amount_index < len(row)
                    ):
                        return [row[amount_index]]
        return []
    for row in rows:
        if not isinstance(row, (list, tuple)) or item_index >= len(row):
            continue
        if str(row[item_index] or "").strip() != item_label:
            continue
        return [
            row[i] if i < len(row) else None
            for i in period_indices
        ]
    return []


def _column_values(body: dict[str, Any], key: str) -> list[Any]:
    columns = list(body.get("columns") or [])
    rows = list(body.get("rows") or [])
    keys = [
        str(column.get("key") or "")
        for column in columns
        if isinstance(column, dict)
    ]
    if key in keys:
        index = keys.index(key)
        return [
            row[index] if isinstance(row, (list, tuple)) and index < len(row) else None
            for row in rows
        ]
    # Promoted item-row layout: map engine field → item label via reference_row_fields.
    fields = list(body.get("reference_row_fields") or [])
    if key in fields:
        field_index = fields.index(key)
        item_index = keys.index("item") if "item" in keys else -1
        period_indices = [i for i, col in enumerate(keys) if col.startswith("period_")]
        if item_index >= 0 and period_indices and field_index < len(rows):
            # Rows are in the same order as reference_row_fields.
            row = rows[field_index]
            if isinstance(row, (list, tuple)):
                return [row[i] if i < len(row) else None for i in period_indices]
        # Fallback: engine matrix if still available.
    if body.get("engine_columns"):
        columns = list(body.get("engine_columns") or [])
        rows = list(body.get("engine_rows") or [])
        keys = [
            str(column.get("key") or "")
            for column in columns
            if isinstance(column, dict)
        ]
        if key in keys:
            index = keys.index(key)
            return [
                row[index] if isinstance(row, (list, tuple)) and index < len(row) else None
                for row in rows
            ]
    return []


def _period_value(record: dict[str, Any], index: int) -> Any:
    value = record.get("year")
    if value is None:
        value = record.get("period")
    return value if value is not None else index + 1


def _series(records: list[dict[str, Any]], field: str) -> list[Any]:
    return [record.get(field) for record in records]


def _sum_values(values: list[Any]) -> Optional[float]:
    numeric = [_number(value) for value in values if value not in (None, "")]
    clean = [value for value in numeric if value is not None]
    return round(sum(clean), 2) if clean else None


def _last_value(values: list[Any]) -> Any:
    for value in reversed(values):
        if value not in (None, ""):
            return value
    return None
