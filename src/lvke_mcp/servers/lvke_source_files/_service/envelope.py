"""Deterministic response envelopes for the source-files MCP adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lvke_mcp.adapters import source_files_repository as source_api
from lvke_mcp.runtime.coordination import build_coordination


def _envelope(
    *,
    success: bool,
    status: str,
    code: str = "",
    message: str = "",
    resource_uris: list[str] | None = None,
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    next_actions: list[str] | None = None,
    retryable: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": success,
        "business_success": success,
        "system_success": True,
        "transport_success": True,
        "status": status,
        "resource_uris": resource_uris or [],
        "warnings": warnings or [],
        "blockers": blockers or [],
        "next_actions": next_actions or [],
        **extra,
    }
    if code:
        payload["code"] = code.lower()
    if message:
        payload["message"] = message
    if retryable:
        payload["retryable"] = True
    payload["coordination"] = build_coordination(
        payload, server_name="lvke-source-files"
    )
    return payload


def _blocked(
    code: str,
    message: str,
    *,
    next_actions: list[str] | None = None,
    retryable: bool = False,
    field_errors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _envelope(
        success=False,
        status="blocked",
        code=code,
        message=message,
        blockers=[code.lower()],
        next_actions=next_actions,
        retryable=retryable,
        **({"field_errors": field_errors} if field_errors else {}),
    )


def _from_source_exception(exc: source_api.SourceFileError) -> dict[str, Any]:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = str(detail.get("code") or "source_operation_failed").lower()
    return _blocked(
        code,
        str(detail.get("message") or "原始资料操作失败"),
        retryable=bool(detail.get("retryable")),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
