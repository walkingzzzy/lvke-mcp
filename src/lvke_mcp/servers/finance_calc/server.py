"""finance-calc MCP server 入口(stdio)。

启动方式:
    python -m lvke_mcp.servers.finance_calc.server

注册的 7 个工具:
    calc_irr / calc_npv / calc_xirr / calc_xnpv / calc_break_even /
    payback_period / sensitivity_analysis
"""

from __future__ import annotations

import math
from datetime import date

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.responses import err, ok
from lvke_mcp.runtime.stdio import StdioServer
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

SERVER_NAME = "finance-calc"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)


def _validate_cashflows(value) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    out: list[float] = []
    for x in value:
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            return None
        if math.isnan(float(x)) or math.isinf(float(x)):
            return None
        out.append(float(x))
    return out


def _validate_dates(value, expected_length: int) -> list[date] | None:
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


def _ok_with_warnings(data: dict, *, source: str, warnings: list[str]) -> dict:
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


def _tool_calc_irr(args: dict) -> dict:
    cfs = _validate_cashflows(args.get("cashflows"))
    if cfs is None:
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "cashflows 必须是数值数组,且至少 1 项",
        )
    guess = args.get("guess", 0.1)
    if not isinstance(guess, (int, float)) or isinstance(guess, bool):
        return err(f"{SERVER_NAME}.invalid_argument", "guess 必须是数字")
    try:
        changes = cashflow_sign_changes(cfs)
        roots = irr_roots(cfs) if changes >= 2 else []
        rate = (
            min(roots, key=lambda value: abs(value - float(guess)))
            if roots
            else irr(cfs, guess=float(guess))
        )
    except ValueError:
        logger.exception("IRR calculation has no solution")
        return err(f"{SERVER_NAME}.no_solution", "当前现金流不存在可用 IRR 解")
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
            "n_periods": len(cfs),
        },
        source=f"{SERVER_NAME}.calc_irr",
        warnings=warnings,
    )


def _tool_calc_npv(args: dict) -> dict:
    cfs = _validate_cashflows(args.get("cashflows"))
    if cfs is None:
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "cashflows 必须是数值数组,且至少 1 项",
        )
    rate = args.get("rate")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return err(f"{SERVER_NAME}.invalid_argument", "rate 必须是数字")
    try:
        value = npv(cfs, float(rate))
    except ValueError:
        logger.exception("NPV calculation rejected input")
        return err(f"{SERVER_NAME}.invalid_argument", "折现率或现金流参数无效")
    return ok(
        {
            "npv": value,
            "rate": float(rate),
            "n_periods": len(cfs),
        },
        source=f"{SERVER_NAME}.calc_npv",
    )


def _tool_calc_xnpv(args: dict) -> dict:
    cfs = _validate_cashflows(args.get("cashflows"))
    dates = _validate_dates(args.get("dates"), len(cfs or []))
    rate = args.get("rate")
    if cfs is None or dates is None or not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "cashflows、等长 ISO dates 与数字 rate 必填",
        )
    try:
        value = xnpv(cfs, dates, float(rate))
    except ValueError:
        return err(f"{SERVER_NAME}.invalid_argument", "日期、折现率或现金流参数无效")
    return ok(
        {
            "xnpv": value,
            "rate": float(rate),
            "dates": [item.isoformat() for item in dates],
            "date_basis": "actual/365",
            "n_periods": len(cfs),
        },
        source=f"{SERVER_NAME}.calc_xnpv",
    )


def _tool_calc_xirr(args: dict) -> dict:
    cfs = _validate_cashflows(args.get("cashflows"))
    dates = _validate_dates(args.get("dates"), len(cfs or []))
    guess = args.get("guess", 0.1)
    if cfs is None or dates is None or not isinstance(guess, (int, float)) or isinstance(guess, bool):
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "cashflows、等长 ISO dates 必填，guess 必须为数字",
        )
    if any(value < dates[0] for value in dates):
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "dates[0] 必须是最早日期",
        )
    try:
        rate = xirr(cfs, dates, guess=float(guess))
    except ValueError:
        return err(f"{SERVER_NAME}.no_solution", "当前日期现金流不存在可用 XIRR 解")
    changes = cashflow_sign_changes(cfs)
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
            "n_periods": len(cfs),
        },
        source=f"{SERVER_NAME}.calc_xirr",
        warnings=warnings,
    )


def _tool_break_even(args: dict) -> dict:
    fields = (
        "fixed_cost_wan",
        "unit_price_yuan",
        "unit_variable_cost_yuan",
        "expected_volume",
    )
    if any(
        not isinstance(args.get(field), (int, float)) or isinstance(args.get(field), bool)
        for field in fields
    ):
        return err(f"{SERVER_NAME}.invalid_argument", "盈亏平衡四项数字输入均为必填")
    try:
        result = break_even_analysis(**{field: float(args[field]) for field in fields})
    except ValueError:
        return err(f"{SERVER_NAME}.invalid_argument", "盈亏平衡输入无效或单位贡献毛利不为正")
    return ok(
        {**result, "currency_unit": "yuan", "fixed_cost_unit": "wan_yuan"},
        source=f"{SERVER_NAME}.calc_break_even",
    )


def _tool_payback_period(args: dict) -> dict:
    cfs = _validate_cashflows(args.get("cashflows"))
    if cfs is None:
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "cashflows 必须是数值数组,且至少 1 项",
        )
    rate = args.get("rate", 0.0)
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return err(f"{SERVER_NAME}.invalid_argument", "rate 必须是数字")
    try:
        result: PaybackResult = payback_period(cfs, rate=float(rate))
    except ValueError:
        logger.exception("payback calculation rejected input")
        return err(f"{SERVER_NAME}.invalid_argument", "回收期计算参数无效")
    return ok(
        {
            "static_years": result.static_years,
            "dynamic_years": result.dynamic_years,
            "cumulative_static": result.cumulative_static,
            "cumulative_discounted": result.cumulative_discounted,
            "rate": float(rate),
        },
        source=f"{SERVER_NAME}.payback_period",
    )


def _tool_sensitivity(args: dict) -> dict:
    cfs = _validate_cashflows(args.get("cashflows"))
    if cfs is None:
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "cashflows 必须是数值数组,且至少 1 项",
        )
    factors = _validate_sensitivity_factors(args.get("factors"), len(cfs))
    if factors is None:
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "factors 中每个因子必须包含非空且不重复的有效 years，以及有限数字 value_per_year",
        )
    deltas = args.get("deltas", [-0.2, -0.1, 0, 0.1, 0.2])
    if not isinstance(deltas, list) or not deltas or not all(
        isinstance(d, (int, float))
        and not isinstance(d, bool)
        and math.isfinite(float(d))
        for d in deltas
    ):
        return err(f"{SERVER_NAME}.invalid_argument", "deltas 必须是非空有限数字数组")
    try:
        series = sensitivity_irr(cfs, factors, deltas)
    except Exception:  # noqa: BLE001
        logger.exception("sensitivity scan failed")
        return err(
            f"{SERVER_NAME}.internal_error",
            "敏感性扫描内部错误",
        )
    # 计算简单的"最敏感因子"
    elasticities: dict[str, float] = {}
    for name, pts in series.items():
        # IRR 弹性: ΔIRR / Δ 因子,取 ±10% 处的均值绝对值
        pos = next((p for p in pts if abs(p["delta"] - 0.1) < 1e-6), None)
        neg = next((p for p in pts if abs(p["delta"] + 0.1) < 1e-6), None)
        base = next((p for p in pts if abs(p["delta"]) < 1e-6), None)
        if pos and neg and base and not math.isnan(base["irr"]):
            avg = (abs(pos["irr"] - base["irr"]) + abs(neg["irr"] - base["irr"])) / 2
            elasticities[name] = avg / 0.1 if avg > 0 else 0.0
    return ok(
        {
            "series": series,
            "elasticities": elasticities,
            "deltas": [float(d) for d in deltas],
        },
        source=f"{SERVER_NAME}.sensitivity_analysis",
    )


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="calc_irr",
        description="计算项目内部收益率 IRR。输入逐年现金流(第 0 年起,投资为负)。",
        input_schema={
            "type": "object",
            "properties": {
                "cashflows": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "第 0 年起的逐年净现金流(万元等单位自定,但全表统一)",
                },
                "guess": {
                    "type": "number",
                    "description": "迭代初值,默认 0.1",
                    "default": 0.1,
                },
            },
            "required": ["cashflows"],
        },
        handler=_tool_calc_irr,
    )
    server.register_tool(
        name="calc_npv",
        description="计算项目净现值 NPV。需要折现率与逐年现金流。",
        input_schema={
            "type": "object",
            "properties": {
                "cashflows": {
                    "type": "array",
                    "items": {"type": "number"},
                },
                "rate": {
                    "type": "number",
                    "description": "折现率(小数,如 0.08 表示 8%)",
                },
            },
            "required": ["cashflows", "rate"],
        },
        handler=_tool_calc_npv,
    )
    server.register_tool(
        name="calc_xirr",
        description="按显式 ISO 日期和 Actual/365 口径计算 XIRR；多次变号时返回决策预警。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "cashflows": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 1000,
                    "items": {"type": "number"},
                },
                "dates": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 1000,
                    "items": {"type": "string", "format": "date"},
                    "description": "与 cashflows 等长的 ISO 8601 日期，首项必须是最早日期",
                },
                "guess": {"type": "number", "exclusiveMinimum": -1, "default": 0.1},
            },
            "required": ["cashflows", "dates"],
        },
        handler=_tool_calc_xirr,
    )
    server.register_tool(
        name="calc_xnpv",
        description="按显式 ISO 日期和 Actual/365 口径计算 XNPV。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "cashflows": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1000,
                    "items": {"type": "number"},
                },
                "dates": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1000,
                    "items": {"type": "string", "format": "date"},
                    "description": "与 cashflows 等长的 ISO 8601 日期，首项必须是最早日期",
                },
                "rate": {"type": "number", "exclusiveMinimum": -1},
            },
            "required": ["cashflows", "dates", "rate"],
        },
        handler=_tool_calc_xnpv,
    )
    server.register_tool(
        name="calc_break_even",
        description="按固定成本、单位售价、单位变动成本和预计销量计算量价盈亏平衡点与安全裕度。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "fixed_cost_wan": {
                    "type": "number",
                    "minimum": 0,
                    "description": "达产年固定成本，单位万元",
                },
                "unit_price_yuan": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "单位售价，单位元/业务量单位",
                },
                "unit_variable_cost_yuan": {
                    "type": "number",
                    "minimum": 0,
                    "description": "单位变动成本，单位元/业务量单位",
                },
                "expected_volume": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "达产年预计业务量",
                },
            },
            "required": [
                "fixed_cost_wan",
                "unit_price_yuan",
                "unit_variable_cost_yuan",
                "expected_volume",
            ],
        },
        handler=_tool_break_even,
    )
    server.register_tool(
        name="payback_period",
        description="计算静态/动态投资回收期。rate=0 时动态回收期 = 静态。",
        input_schema={
            "type": "object",
            "properties": {
                "cashflows": {"type": "array", "items": {"type": "number"}},
                "rate": {"type": "number", "default": 0.0},
            },
            "required": ["cashflows"],
        },
        handler=_tool_payback_period,
    )
    server.register_tool(
        name="sensitivity_analysis",
        description=(
            "对 IRR 做单因素敏感性扫描。factors 描述哪些年份的现金流受哪个因子线性影响。"
            "返回每因子的 IRR 序列 + 弹性系数。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "cashflows": {"type": "array", "items": {"type": "number"}},
                "factors": {
                    "type": "object",
                    "minProperties": 1,
                    "additionalProperties": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "years": {
                                "type": "array",
                                "minItems": 1,
                                "uniqueItems": True,
                                "items": {"type": "integer", "minimum": 0},
                            },
                            "value_per_year": {"type": "number"},
                        },
                        "required": ["years", "value_per_year"],
                    },
                    "description": (
                        "形如 {<factor_name>: {years: [int], value_per_year: number}}。"
                        "years 必须落在 cashflows 下标范围内且不得重复。"
                        "浮动 +δ 时,years 指定的年份现金流 += δ * value_per_year"
                    ),
                },
                "deltas": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "number"},
                    "default": [-0.2, -0.1, 0, 0.1, 0.2],
                },
            },
            "required": ["cashflows", "factors"],
        },
        handler=_tool_sensitivity,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
