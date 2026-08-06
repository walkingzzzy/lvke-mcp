"""Deterministic low-level finance calculator shared by MCP adapters.

This module owns no MCP process or transport.  Both the retained legacy
``finance-calc`` wrapper and the aggregated finance-model entry can call the
same calculation functions without importing one MCP server from another.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Callable

from lvke_mcp.domains.finance.calculations import (
    PaybackResult,
    break_even_analysis,
    cashflow_sign_changes,
    irr,
    irr_roots,
    npv,
    payback_period,
    sensitivity_irr,
    xirr,
    xnpv,
)
from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.responses import err, ok

SOURCE_NAME = "finance-calc"
logger = get_logger(SOURCE_NAME)

_CASHFLOWS = {"type": "array", "items": {"type": "number"}}
CALCULATOR_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "irr": {
        "type": "object",
        "properties": {
            "cashflows": _CASHFLOWS,
            "guess": {"type": "number", "default": 0.1},
        },
        "required": ["cashflows"],
    },
    "npv": {
        "type": "object",
        "properties": {
            "cashflows": _CASHFLOWS,
            "rate": {"type": "number"},
        },
        "required": ["cashflows", "rate"],
    },
    "xirr": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cashflows": {
                "type": "array", "minItems": 2, "maxItems": 1000,
                "items": {"type": "number"},
            },
            "dates": {
                "type": "array", "minItems": 2, "maxItems": 1000,
                "items": {"type": "string", "format": "date"},
            },
            "guess": {"type": "number", "exclusiveMinimum": -1, "default": 0.1},
        },
        "required": ["cashflows", "dates"],
    },
    "xnpv": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cashflows": {
                "type": "array", "minItems": 1, "maxItems": 1000,
                "items": {"type": "number"},
            },
            "dates": {
                "type": "array", "minItems": 1, "maxItems": 1000,
                "items": {"type": "string", "format": "date"},
            },
            "rate": {"type": "number", "exclusiveMinimum": -1},
        },
        "required": ["cashflows", "dates", "rate"],
    },
    "break_even": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "fixed_cost_wan": {"type": "number", "minimum": 0},
            "unit_price_yuan": {"type": "number", "exclusiveMinimum": 0},
            "unit_variable_cost_yuan": {"type": "number", "minimum": 0},
            "expected_volume": {"type": "number", "exclusiveMinimum": 0},
        },
        "required": [
            "fixed_cost_wan", "unit_price_yuan",
            "unit_variable_cost_yuan", "expected_volume",
        ],
    },
    "payback_period": {
        "type": "object",
        "properties": {
            "cashflows": _CASHFLOWS,
            "rate": {"type": "number", "default": 0.0},
        },
        "required": ["cashflows"],
    },
    "sensitivity": {
        "type": "object",
        "properties": {
            "cashflows": _CASHFLOWS,
            "factors": {
                "type": "object",
                "minProperties": 1,
                "additionalProperties": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "years": {
                            "type": "array", "minItems": 1, "uniqueItems": True,
                            "items": {"type": "integer", "minimum": 0},
                        },
                        "value_per_year": {"type": "number"},
                    },
                    "required": ["years", "value_per_year"],
                },
            },
            "deltas": {
                "type": "array", "minItems": 1,
                "items": {"type": "number"},
                "default": [-0.2, -0.1, 0, 0.1, 0.2],
            },
        },
        "required": ["cashflows", "factors"],
    },
}


def _validate_cashflows(value: object) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    result: list[float] = []
    for item in value:
        if (
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
        ):
            return None
        result.append(float(item))
    return result


def _validate_dates(value: object, expected_length: int) -> list[date] | None:
    if not isinstance(value, list) or len(value) != expected_length:
        return None
    parsed: list[date] = []
    try:
        for item in value:
            if not isinstance(item, str):
                return None
            parsed.append(date.fromisoformat(item))
    except ValueError:
        return None
    return parsed


def _ok_with_warnings(
    data: dict[str, Any],
    *,
    source: str,
    warnings: list[str],
) -> dict[str, Any]:
    response = ok(data, source=source)
    response["warnings"] = warnings
    return response


def _validate_sensitivity_factors(
    value: object,
    cashflow_count: int,
) -> dict[str, dict[str, object]] | None:
    if not isinstance(value, dict) or not value:
        return None
    validated: dict[str, dict[str, object]] = {}
    for name, spec in value.items():
        if not isinstance(name, str) or not name or not isinstance(spec, dict):
            return None
        if set(spec) != {"years", "value_per_year"}:
            return None
        years = spec.get("years")
        value_per_year = spec.get("value_per_year")
        if (
            not isinstance(years, list)
            or not years
            or any(not isinstance(year, int) or isinstance(year, bool) for year in years)
            or len(set(years)) != len(years)
            or any(year < 0 or year >= cashflow_count for year in years)
            or not isinstance(value_per_year, (int, float))
            or isinstance(value_per_year, bool)
            or not math.isfinite(float(value_per_year))
        ):
            return None
        validated[name] = {
            "years": years,
            "value_per_year": float(value_per_year),
        }
    return validated


def calculate_irr(args: dict[str, Any]) -> dict[str, Any]:
    cashflows = _validate_cashflows(args.get("cashflows"))
    if cashflows is None:
        return err(
            f"{SOURCE_NAME}.invalid_argument",
            "cashflows 必须是数值数组,且至少 1 项",
        )
    guess = args.get("guess", 0.1)
    if not isinstance(guess, (int, float)) or isinstance(guess, bool):
        return err(f"{SOURCE_NAME}.invalid_argument", "guess 必须是数字")
    try:
        changes = cashflow_sign_changes(cashflows)
        roots = irr_roots(cashflows) if changes >= 2 else []
        rate = (
            min(roots, key=lambda value: abs(value - float(guess)))
            if roots
            else irr(cashflows, guess=float(guess))
        )
    except ValueError:
        logger.exception("IRR calculation has no solution")
        return err(f"{SOURCE_NAME}.no_solution", "当前现金流不存在可用 IRR 解")
    warnings = ["multiple_irr_detected_use_npv_or_mirr"] if changes >= 2 else []
    return _ok_with_warnings(
        {
            "irr": rate,
            "irr_percent": rate * 100,
            "irr_roots": roots or [rate],
            "irr_roots_percent": [value * 100 for value in (roots or [rate])],
            "multiple_irr": len(roots) > 1 or changes >= 2,
            "cashflow_sign_changes": changes,
            "warnings": warnings,
            "decision_basis": "npv_or_mirr_required" if changes >= 2 else "irr",
            "n_periods": len(cashflows),
        },
        source=f"{SOURCE_NAME}.calc_irr",
        warnings=warnings,
    )


def calculate_npv(args: dict[str, Any]) -> dict[str, Any]:
    cashflows = _validate_cashflows(args.get("cashflows"))
    rate = args.get("rate")
    if cashflows is None:
        return err(
            f"{SOURCE_NAME}.invalid_argument",
            "cashflows 必须是数值数组,且至少 1 项",
        )
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return err(f"{SOURCE_NAME}.invalid_argument", "rate 必须是数字")
    try:
        value = npv(cashflows, float(rate))
    except ValueError:
        logger.exception("NPV calculation rejected input")
        return err(f"{SOURCE_NAME}.invalid_argument", "折现率或现金流参数无效")
    return ok(
        {"npv": value, "rate": float(rate), "n_periods": len(cashflows)},
        source=f"{SOURCE_NAME}.calc_npv",
    )


def calculate_xnpv(args: dict[str, Any]) -> dict[str, Any]:
    cashflows = _validate_cashflows(args.get("cashflows"))
    dates = _validate_dates(args.get("dates"), len(cashflows or []))
    rate = args.get("rate")
    if (
        cashflows is None
        or dates is None
        or not isinstance(rate, (int, float))
        or isinstance(rate, bool)
    ):
        return err(
            f"{SOURCE_NAME}.invalid_argument",
            "cashflows、等长 ISO dates 与数字 rate 必填",
        )
    try:
        value = xnpv(cashflows, dates, float(rate))
    except ValueError:
        return err(f"{SOURCE_NAME}.invalid_argument", "日期、折现率或现金流参数无效")
    return ok(
        {
            "xnpv": value,
            "rate": float(rate),
            "dates": [item.isoformat() for item in dates],
            "date_basis": "actual/365",
            "n_periods": len(cashflows),
        },
        source=f"{SOURCE_NAME}.calc_xnpv",
    )


def calculate_xirr(args: dict[str, Any]) -> dict[str, Any]:
    cashflows = _validate_cashflows(args.get("cashflows"))
    dates = _validate_dates(args.get("dates"), len(cashflows or []))
    guess = args.get("guess", 0.1)
    if (
        cashflows is None
        or dates is None
        or not isinstance(guess, (int, float))
        or isinstance(guess, bool)
    ):
        return err(
            f"{SOURCE_NAME}.invalid_argument",
            "cashflows、等长 ISO dates 必填，guess 必须为数字",
        )
    if any(value < dates[0] for value in dates):
        return err(f"{SOURCE_NAME}.invalid_argument", "dates[0] 必须是最早日期")
    try:
        rate = xirr(cashflows, dates, guess=float(guess))
    except ValueError:
        return err(f"{SOURCE_NAME}.no_solution", "当前日期现金流不存在可用 XIRR 解")
    changes = cashflow_sign_changes(cashflows)
    warnings = ["multiple_xirr_possible_use_xnpv_for_decision"] if changes >= 2 else []
    return _ok_with_warnings(
        {
            "xirr": rate,
            "xirr_percent": rate * 100.0,
            "dates": [item.isoformat() for item in dates],
            "date_basis": "actual/365",
            "cashflow_sign_changes": changes,
            "multiple_xirr_possible": changes >= 2,
            "warnings": warnings,
            "decision_basis": "xnpv_required" if changes >= 2 else "xirr",
            "n_periods": len(cashflows),
        },
        source=f"{SOURCE_NAME}.calc_xirr",
        warnings=warnings,
    )


def calculate_break_even(args: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "fixed_cost_wan",
        "unit_price_yuan",
        "unit_variable_cost_yuan",
        "expected_volume",
    )
    if any(
        not isinstance(args.get(field), (int, float))
        or isinstance(args.get(field), bool)
        for field in fields
    ):
        return err(f"{SOURCE_NAME}.invalid_argument", "盈亏平衡四项数字输入均为必填")
    try:
        result = break_even_analysis(
            **{field: float(args[field]) for field in fields}
        )
    except ValueError:
        return err(
            f"{SOURCE_NAME}.invalid_argument",
            "盈亏平衡输入无效或单位贡献毛利不为正",
        )
    return ok(
        {**result, "currency_unit": "yuan", "fixed_cost_unit": "wan_yuan"},
        source=f"{SOURCE_NAME}.calc_break_even",
    )


def calculate_payback(args: dict[str, Any]) -> dict[str, Any]:
    cashflows = _validate_cashflows(args.get("cashflows"))
    if cashflows is None:
        return err(
            f"{SOURCE_NAME}.invalid_argument",
            "cashflows 必须是数值数组,且至少 1 项",
        )
    rate = args.get("rate", 0.0)
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return err(f"{SOURCE_NAME}.invalid_argument", "rate 必须是数字")
    try:
        result: PaybackResult = payback_period(cashflows, rate=float(rate))
    except ValueError:
        logger.exception("payback calculation rejected input")
        return err(f"{SOURCE_NAME}.invalid_argument", "回收期计算参数无效")
    return ok(
        {
            "static_years": result.static_years,
            "dynamic_years": result.dynamic_years,
            "cumulative_static": result.cumulative_static,
            "cumulative_discounted": result.cumulative_discounted,
            "rate": float(rate),
        },
        source=f"{SOURCE_NAME}.payback_period",
    )


def calculate_sensitivity(args: dict[str, Any]) -> dict[str, Any]:
    cashflows = _validate_cashflows(args.get("cashflows"))
    if cashflows is None:
        return err(
            f"{SOURCE_NAME}.invalid_argument",
            "cashflows 必须是数值数组,且至少 1 项",
        )
    factors = _validate_sensitivity_factors(args.get("factors"), len(cashflows))
    if factors is None:
        return err(
            f"{SOURCE_NAME}.invalid_argument",
            "factors 中每个因子必须包含非空且不重复的有效 years，以及有限数字 value_per_year",
        )
    deltas = args.get("deltas", [-0.2, -0.1, 0, 0.1, 0.2])
    if (
        not isinstance(deltas, list)
        or not deltas
        or not all(
            isinstance(delta, (int, float))
            and not isinstance(delta, bool)
            and math.isfinite(float(delta))
            for delta in deltas
        )
    ):
        return err(f"{SOURCE_NAME}.invalid_argument", "deltas 必须是非空有限数字数组")
    try:
        series = sensitivity_irr(cashflows, factors, deltas)
    except Exception:  # noqa: BLE001
        logger.exception("sensitivity scan failed")
        return err(f"{SOURCE_NAME}.internal_error", "敏感性扫描内部错误")
    elasticities: dict[str, float] = {}
    for name, points in series.items():
        positive = next(
            (point for point in points if abs(point["delta"] - 0.1) < 1e-6),
            None,
        )
        negative = next(
            (point for point in points if abs(point["delta"] + 0.1) < 1e-6),
            None,
        )
        base = next(
            (point for point in points if abs(point["delta"]) < 1e-6),
            None,
        )
        if positive and negative and base and not math.isnan(base["irr"]):
            average = (
                abs(positive["irr"] - base["irr"])
                + abs(negative["irr"] - base["irr"])
            ) / 2
            elasticities[name] = average / 0.1 if average > 0 else 0.0
    return ok(
        {
            "series": series,
            "elasticities": elasticities,
            "deltas": [float(delta) for delta in deltas],
        },
        source=f"{SOURCE_NAME}.sensitivity_analysis",
    )


CALCULATOR_HANDLERS: dict[
    str,
    Callable[[dict[str, Any]], dict[str, Any]],
] = {
    "irr": calculate_irr,
    "npv": calculate_npv,
    "xirr": calculate_xirr,
    "xnpv": calculate_xnpv,
    "break_even": calculate_break_even,
    "payback_period": calculate_payback,
    "sensitivity": calculate_sensitivity,
}


def calculate(operation: str, inputs: dict[str, Any]) -> dict[str, Any] | None:
    handler = CALCULATOR_HANDLERS.get(operation)
    return None if handler is None else handler(inputs)
