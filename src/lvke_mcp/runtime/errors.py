"""MCP 层标准错误码与脱敏错误构造（方案 §13 目标模块）。

从 ``official_server.py`` 收口两类错误出口，保证六个 Server 的错误
行为与脱敏口径只有一份实现：

- 工具级错误：``CallToolResult(isError=True)``，payload 只含
  ``success/code/message``，绝不携带堆栈、文件路径或环境信息
  （详情只经 stderr logger 记录）。
- 协议级错误：JSON-RPC error 响应行（Parse error / Invalid Request /
  Method not found 等），供 ``strict_stdio_server`` 直接写 stdout。

错误码常量直接复用官方 SDK ``mcp.types``，不自定义数值。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from mcp import types
from lvke_mcp.runtime.coordination import build_coordination

# 复用官方 SDK 的 JSON-RPC 标准错误码，集中在此再导出，
# 让调用方不必分别记忆 types 里的常量名。
PARSE_ERROR = types.PARSE_ERROR
INVALID_REQUEST = types.INVALID_REQUEST
METHOD_NOT_FOUND = types.METHOD_NOT_FOUND
INVALID_PARAMS = types.INVALID_PARAMS
INTERNAL_ERROR = types.INTERNAL_ERROR

# 对外可见的通用错误文案：只描述"发生了什么类别的失败"，不带实现细节。
UNHANDLED_MESSAGE = "工具执行失败"
INVALID_OUTPUT_TYPE_MESSAGE = "工具输出格式无效"
INVALID_OUTPUT_SCHEMA_MESSAGE = "工具输出未通过 outputSchema 校验"


def sanitized_error_payload(
    code: str,
    message: str,
    *,
    trace_id: str | None = None,
    server_name: str | None = None,
) -> dict[str, Any]:
    """构造脱敏、可关联日志的工具级错误 payload。"""

    payload = {
        "success": False,
        "business_success": False,
        "system_success": False,
        "transport_success": False,
        "status": "failed",
        "code": code,
        "message": message,
        "retryable": False,
        "trace_id": trace_id or f"mcp_{uuid.uuid4().hex}",
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": 0,
        "input_hash": None,
        "basis_hash": None,
        "lineage": {},
        "content_hash": None,
    }
    payload["coordination"] = build_coordination(payload, server_name=server_name)
    return payload


def error_call_result(
    code: str,
    message: str,
    *,
    trace_id: str | None = None,
    server_name: str | None = None,
) -> types.CallToolResult:
    """把脱敏 payload 包装为 ``CallToolResult(isError=True)``。"""

    payload = sanitized_error_payload(code, message, trace_id=trace_id, server_name=server_name)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False),
            )
        ],
        is_error=True,
    )


def protocol_error_response(
    code: int,
    message: str,
    request_id: str | int | None = None,
) -> dict[str, Any]:
    """构造 JSON-RPC 协议级 error 响应（stdout 单行输出用）。"""

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
