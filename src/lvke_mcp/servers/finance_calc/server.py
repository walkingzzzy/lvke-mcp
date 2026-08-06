"""Compatibility MCP wrapper for the internal deterministic calculator.

The public process is no longer registered in user configuration.  Keeping
this thin wrapper for one migration version allows old parity checks and local
callers to reach the same internal functions used by ``finance_calculate``.
"""

from __future__ import annotations

from lvke_mcp.domains.finance.calculator_service import (
    CALCULATOR_HANDLERS,
    CALCULATOR_INPUT_SCHEMAS,
)
from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.stdio import StdioServer

SERVER_NAME = "finance-calc"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)

_TOOLS = (
    (
        "calc_irr",
        "irr",
        "计算项目内部收益率 IRR。输入逐年现金流，第 0 年起且投资为负。",
    ),
    (
        "calc_npv",
        "npv",
        "计算项目净现值 NPV。需要折现率与逐年现金流。",
    ),
    (
        "calc_xirr",
        "xirr",
        "按显式 ISO 日期和 Actual/365 口径计算 XIRR。",
    ),
    (
        "calc_xnpv",
        "xnpv",
        "按显式 ISO 日期和 Actual/365 口径计算 XNPV。",
    ),
    (
        "calc_break_even",
        "break_even",
        "计算量价盈亏平衡点与安全裕度。",
    ),
    (
        "payback_period",
        "payback_period",
        "计算静态和动态投资回收期。",
    ),
    (
        "sensitivity_analysis",
        "sensitivity",
        "对 IRR 做单因素敏感性扫描并返回弹性系数。",
    ),
)


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    for tool_name, operation, description in _TOOLS:
        server.register_tool(
            name=tool_name,
            description=description,
            input_schema=CALCULATOR_INPUT_SCHEMAS[operation],
            handler=CALCULATOR_HANDLERS[operation],
        )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
