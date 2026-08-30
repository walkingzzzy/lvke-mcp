"""Deterministic monthly driver and operating-calendar resolution."""

from __future__ import annotations

import calendar
import math
from datetime import date, timedelta
from typing import Any

from .base import AcquisitionModelError
from .period_dates import _add_months, _month_end, _month_start


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise AcquisitionModelError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AcquisitionModelError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise AcquisitionModelError(f"{field} must be a finite number")
    return result


def _nonnegative_sequence(value: Any, *, length: int, field: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise AcquisitionModelError(f"{field} must contain exactly {length} ordered values")
    values = [_finite_number(item, field=f"{field}[{index}]") for index, item in enumerate(value)]
    if any(item < 0 for item in values):
        raise AcquisitionModelError(f"{field} values must be non-negative")
    return values


def _annual_values(value: Any, *, years: int, field: str, default: float) -> list[float]:
    if value is None:
        values = [default]
    elif isinstance(value, (list, tuple)):
        if not value:
            raise AcquisitionModelError(f"{field} must not be empty")
        values = [_finite_number(item, field=f"{field}[{index}]") for index, item in enumerate(value)]
    else:
        values = [_finite_number(value, field=field)]
    if len(values) not in {1, years}:
        raise AcquisitionModelError(f"{field} must contain one value or exactly {years} annual values")
    if any(item < 0 for item in values):
        raise AcquisitionModelError(f"{field} values must be non-negative")
    return values * years if len(values) == 1 else values


def _periods(start: date, months: int) -> list[dict[str, Any]]:
    cursor = _month_start(start)
    result: list[dict[str, Any]] = []
    for index in range(months):
        period_start = _add_months(cursor, index)
        period_end = _month_end(period_start)
        result.append({
            "month": index + 1,
            "period_start": period_start,
            "period_end": period_end,
            "calendar_days": (period_end - period_start).days + 1,
        })
    return result


def _weekday_count(period_start: date, period_end: date) -> int:
    return sum(
        1
        for offset in range((period_end - period_start).days + 1)
        if (period_start + timedelta(days=offset)).weekday() < 5
    )


def resolve_operating_calendar(
    start: date,
    months: int,
    config: Any,
    *,
    legacy_operating_days: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve one ordered, bounded calendar row for every model month."""

    expected = _periods(start, months)
    years = months // 12
    cfg = dict(config or {}) if isinstance(config, dict) else {}
    basis = str(cfg.get("basis") or ("operating_days" if legacy_operating_days is not None else "calendar_days"))
    if basis not in {"calendar_days", "operating_days", "workdays"}:
        raise AcquisitionModelError("hotel_operation.operating_calendar.basis is invalid")
    rows = [{
        **row,
        "workdays": _weekday_count(row["period_start"], row["period_end"]),
        "operating_days": float(row["calendar_days"]),
    } for row in expected]

    supplied_periods = cfg.get("periods")
    supplied_monthly = cfg.get("monthly_days")
    source = "calendar_default"
    if supplied_monthly is not None:
        selected = _nonnegative_sequence(
            supplied_monthly,
            length=months,
            field="hotel_operation.operating_calendar.monthly_days",
        )
        source = "explicit_monthly_days"
    elif supplied_periods is not None:
        if not isinstance(supplied_periods, list) or len(supplied_periods) != months:
            raise AcquisitionModelError(
                f"hotel_operation.operating_calendar.periods must contain exactly {months} ordered periods"
            )
        selected = []
        selected_field = basis
        for index, (item, expected_row) in enumerate(zip(supplied_periods, expected)):
            if not isinstance(item, dict):
                raise AcquisitionModelError(f"operating_calendar.periods[{index}] must be an object")
            if str(item.get("period_start") or "") != expected_row["period_start"].isoformat():
                raise AcquisitionModelError("operating_calendar periods must be continuous and ordered")
            if item.get("period_end") is not None and str(item["period_end"]) != expected_row["period_end"].isoformat():
                raise AcquisitionModelError("operating_calendar period_end does not match the model month")
            for field in ("operating_days", "workdays"):
                if field in item:
                    rows[index][field] = _finite_number(item[field], field=f"operating_calendar.periods[{index}].{field}")
            raw_selected = expected_row["calendar_days"] if selected_field == "calendar_days" else item.get(selected_field)
            if raw_selected is None:
                raise AcquisitionModelError(f"operating_calendar.periods[{index}].{selected_field} is required")
            selected.append(_finite_number(raw_selected, field=f"operating_calendar.periods[{index}].{selected_field}"))
        source = "explicit_periods"
    elif legacy_operating_days is not None:
        annual = _annual_values(
            legacy_operating_days,
            years=years,
            field="hotel_operation.operating_days",
            default=365.0,
        )
        selected = []
        for year_index in range(years):
            year_rows = rows[year_index * 12:(year_index + 1) * 12]
            denominator = sum(row["calendar_days"] for row in year_rows)
            if annual[year_index] > denominator:
                raise AcquisitionModelError("hotel_operation.operating_days exceeds model-year calendar days")
            selected.extend(annual[year_index] * row["calendar_days"] / denominator for row in year_rows)
        source = "legacy_annual_operating_days"
    else:
        selected = [float(row["calendar_days"]) for row in rows]

    for index, (row, value) in enumerate(zip(rows, selected)):
        if value < 0 or value > row["calendar_days"]:
            raise AcquisitionModelError(f"operating calendar month {index + 1} days are outside calendar bounds")
        if row["workdays"] < 0 or row["workdays"] > row["calendar_days"]:
            raise AcquisitionModelError(f"operating calendar month {index + 1} workdays are outside calendar bounds")
        if row["operating_days"] < 0 or row["operating_days"] > row["calendar_days"]:
            raise AcquisitionModelError(f"operating calendar month {index + 1} operating_days are outside calendar bounds")
        row[basis] = value
        row["selected_days"] = value
        row["basis"] = basis
        row["period_start"] = row["period_start"].isoformat()
        row["period_end"] = row["period_end"].isoformat()

    manifest = {
        "basis": basis,
        "source": source,
        "month_count": months,
        "period_start": rows[0]["period_start"] if rows else None,
        "period_end": rows[-1]["period_end"] if rows else None,
    }
    return rows, manifest


def resolve_monthly_driver(
    name: str,
    raw: Any,
    *,
    months: int,
    periods: list[dict[str, Any]],
    kind: str,
    default: float = 0.0,
    maximum: float | None = None,
) -> tuple[list[float], dict[str, Any]]:
    """Resolve explicit monthly, seasonal annual, or legacy annual input."""

    if kind not in {"level", "annual_total"}:
        raise AcquisitionModelError(f"unsupported monthly driver kind: {kind}")
    years = months // 12
    cfg = dict(raw) if isinstance(raw, dict) else None
    if cfg is not None and "monthly_values" in cfg:
        values = _nonnegative_sequence(cfg["monthly_values"], length=months, field=f"{name}.monthly_values")
        source = "explicit_monthly"
        annual_targets = None
    else:
        annual_raw = cfg.get("annual_values") if cfg is not None else raw
        annual_targets = _annual_values(annual_raw, years=years, field=f"{name}.annual_values", default=default)
        factors_raw = cfg.get("seasonal_factors") if cfg is not None else None
        if factors_raw is not None:
            if not isinstance(factors_raw, (list, tuple)) or len(factors_raw) not in {12, months}:
                raise AcquisitionModelError(f"{name}.seasonal_factors must contain 12 or exactly {months} values")
            base_factors = [_finite_number(item, field=f"{name}.seasonal_factors[{index}]") for index, item in enumerate(factors_raw)]
            if any(item < 0 for item in base_factors):
                raise AcquisitionModelError(f"{name}.seasonal_factors values must be non-negative")
            factors = base_factors * years if len(base_factors) == 12 else base_factors
            values = []
            for year_index in range(years):
                year_factors = factors[year_index * 12:(year_index + 1) * 12]
                factor_sum = sum(year_factors)
                if factor_sum <= 0:
                    raise AcquisitionModelError(f"{name}.seasonal_factors must have a positive sum in every model year")
                if kind == "level":
                    if abs(factor_sum - 12.0) > 1e-9:
                        raise AcquisitionModelError(f"{name}.seasonal_factors must sum to 12 for a level driver")
                    values.extend(annual_targets[year_index] * item for item in year_factors)
                else:
                    values.extend(annual_targets[year_index] * item / factor_sum for item in year_factors)
            source = "seasonal_annual"
        else:
            values = []
            for year_index in range(years):
                if kind == "level":
                    values.extend([annual_targets[year_index]] * 12)
                else:
                    year_periods = periods[year_index * 12:(year_index + 1) * 12]
                    denominator = sum(float(item["calendar_days"]) for item in year_periods)
                    values.extend(
                        annual_targets[year_index] * float(item["calendar_days"]) / denominator
                        for item in year_periods
                    )
            source = "deterministic_annual_compatibility"

    if maximum is not None and any(item > maximum for item in values):
        raise AcquisitionModelError(f"{name} values must not exceed {maximum}")
    annual_resolved: list[float] = []
    for year_index in range(years):
        chunk = values[year_index * 12:(year_index + 1) * 12]
        annual_resolved.append(sum(chunk) if kind == "annual_total" else sum(chunk) / 12.0)
    manifest = {
        "driver": name,
        "kind": kind,
        "source": source,
        "month_count": months,
        "annual_input": annual_targets,
        "annual_resolved": annual_resolved,
        "reconciled": annual_targets is None or all(
            abs(actual - expected) <= 1e-8
            for actual, expected in zip(annual_resolved, annual_targets)
        ),
    }
    if not manifest["reconciled"]:
        raise AcquisitionModelError(f"{name} annual-to-monthly reconciliation failed")
    return values, manifest

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "AcquisitionModelError",
    "Any",
    "_add_months",
    "_annual_values",
    "_finite_number",
    "_month_end",
    "_month_start",
    "_nonnegative_sequence",
    "_periods",
    "_weekday_count",
    "calendar",
    "date",
    "math",
    "resolve_monthly_driver",
    "resolve_operating_calendar",
    "timedelta",
]
