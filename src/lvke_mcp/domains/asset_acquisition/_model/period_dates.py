"""日期原语：解析、月份加减、月首月末与月度重叠天数。不叫 calendar，避免与标准库同名。"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Any




def _date(value: Any, default: date) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return default


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + max(int(months), 0)
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _date_value(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_end(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _month_overlap_days(start: date, end: date, active_from: date, active_to: date | None = None) -> int:
    left = max(start, active_from)
    right = min(end, active_to) if active_to else end
    return max((right - left).days + 1, 0)
