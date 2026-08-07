"""纯函数计算器工具与算子映射。"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from lvke_mcp.domains.finance.calculator_service import (
    CALCULATOR_INPUT_SCHEMAS,
    calculate as calculate_finance_operation,
)

from .envelope import (
    _err_env,
)

from .schemas import (
    SERVER_NAME,
)


_CALCULATOR_TOOL_BY_OPERATION = {
    "irr": "calc_irr",
    "npv": "calc_npv",
    "xirr": "calc_xirr",
    "xnpv": "calc_xnpv",
    "break_even": "calc_break_even",
    "payback_period": "payback_period",
    "sensitivity": "sensitivity_analysis",
}


def _tool_finance_calculate(args: dict[str, Any]) -> dict[str, Any]:
    """Route to the existing deterministic finance-calc implementation."""

    operation = str(args.get("operation") or "")
    input_schema = CALCULATOR_INPUT_SCHEMAS.get(operation)
    if input_schema is None:
        return _err_env(
            f"{SERVER_NAME}.calculator_operation_invalid",
            "未知确定性财务计算操作",
        )
    inputs = args.get("inputs") if isinstance(args.get("inputs"), dict) else {}
    error = next(Draft202012Validator(input_schema).iter_errors(inputs), None)
    if error is not None:
        path = ".".join(str(item) for item in error.absolute_path) or "<root>"
        return _err_env(
            f"{SERVER_NAME}.calculator_input_invalid",
            f"finance_calculate.{operation} 入参无效：{path}: {error.message}",
        )
    result = calculate_finance_operation(operation, inputs)
    if result is None:  # Defensive: schema/handler registries must stay aligned.
        return _err_env(
            f"{SERVER_NAME}.calculator_operation_invalid",
            "未知确定性财务计算操作",
        )
    return result
