"""MCP 工具响应的统一包装。

绿科约定（见 ``mcp-servers/README.md`` 共用约定第 1-2 条）：

- 成功响应使用 ``status=ok`` 与 ``success/business_success=true``。
- 业务校验失败使用 ``status=blocked``、``system_success=true``；真正的
  处理器/协议错误由 ``errors.py`` 返回 ``status=failed``。
- 两种响应都附加 ``agent-coordination.v1``，供 Codex 恢复阶段、对象与
  下一步，不改变原有 ``data/source`` 与 ``code/message`` 兼容字段。

任何 MCP 工具的返回值都应通过本模块的 ``ok`` / ``err`` 函数构造,
保证下游 hermes 的 ``_strip_credentials`` 中间件能正确脱敏并解析。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from lvke_mcp.runtime.coordination import build_coordination


def ok(data: Any, *, source: str) -> dict[str, Any]:
    """构造成功响应。

    Args:
        data: 工具实际返回的载荷（可为任意 JSON 可序列化对象）。
        source: 形如 ``"finance-calc.calc_irr"`` 的来源标识，用于审计。

    Returns:
        统一格式的成功响应字典。
    """

    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "success": True,
        "business_success": True,
        "system_success": True,
        "transport_success": True,
        "status": "ok",
        "data": data,
        "source": source,
        "resource_uris": [],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
        "trace_id": f"mcp_{uuid.uuid4().hex}",
        "started_at": now,
        "finished_at": now,
        "duration_ms": 0,
        "input_hash": None,
        "basis_hash": None,
        "lineage": {},
        "content_hash": None,
    }
    payload["coordination"] = build_coordination(
        payload, server_name=str(source).split(".", 1)[0]
    )
    return payload


def err(
    code: str,
    message: str,
    *,
    detail: str | Mapping[str, Any] | None = None,
    retryable: bool = False,
    field_errors: Mapping[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """构造失败响应。

    Args:
        code: ``<server>.<error_tag>`` 形式的错误码。
        message: 给最终用户看的可读提示（中文）。
        detail: 可选的 debug 信息（异常堆栈摘要、上下游错误等）。

    Returns:
        统一格式的失败响应字典。
    """

    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "success": False,
        "business_success": False,
        "system_success": True,
        "transport_success": True,
        "status": "blocked",
        "code": code,
        "message": message,
        "retryable": bool(retryable),
        "trace_id": trace_id or f"mcp_{uuid.uuid4().hex}",
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [],
        "started_at": now,
        "finished_at": now,
        "duration_ms": 0,
        "input_hash": None,
        "basis_hash": None,
        "lineage": {},
        "content_hash": None,
    }
    if field_errors:
        payload["field_errors"] = dict(field_errors)
    if detail is not None:
        payload["detail"] = detail
    payload["coordination"] = build_coordination(
        payload, server_name=str(code).split(".", 1)[0]
    )
    return payload
