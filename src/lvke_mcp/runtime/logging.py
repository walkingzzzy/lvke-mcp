"""MCP server 的日志工具。

stdio MCP server 的 stdout 必须**只**输出 JSON-RPC 报文,任何调试 / 业务
日志都应通过 stderr 输出。本模块封装一个 ``get_logger`` 入口,默认配置:

- 输出到 ``stderr``
- 行内带 ``[mcp-<server>] LEVEL`` 前缀,方便上游聚合
- 默认 ``INFO`` 等级,可通过环境变量 ``LVKE_MCP_LOG_LEVEL`` 调整
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Final

_DEFAULT_LEVEL: Final[str] = os.environ.get("LVKE_MCP_LOG_LEVEL", "INFO").upper()


def get_logger(server_name: str) -> logging.Logger:
    """返回带统一前缀的 logger。

    Args:
        server_name: 形如 ``"finance-calc"`` 的 server 短名。

    Returns:
        已配置好 stderr handler 的 :class:`logging.Logger` 实例。
    """

    logger = logging.getLogger(f"lvke.mcp.{server_name}")
    if logger.handlers:
        return logger

    # Windows stdio 上 stderr 默认 codec 是 cp936 / GBK,无法承载中文日志;
    # 强制 reconfigure 为 UTF-8 与子进程 PYTHONIOENCODING 对齐,避免上游
    # MCP SDK 读 stderr 时 UnicodeDecodeError(0xb0...) 阻断 test_connection。
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    handler = logging.StreamHandler(stream=sys.stderr)
    fmt = logging.Formatter(
        fmt=f"[mcp-{server_name}] %(levelname)s %(message)s",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, _DEFAULT_LEVEL, logging.INFO))
    logger.propagate = False
    return logger
