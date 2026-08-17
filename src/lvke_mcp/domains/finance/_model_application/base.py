"""服务名、日志、workspace/URI 原语、字符串工具与幂等/信封基座。"""

from __future__ import annotations

from typing import Any
import os
import uuid
from datetime import datetime, timedelta, timezone

from lvke_mcp.adapters.finance_model_repository import BASIS_OF_ESTIMATE_STORE, IDEMPOTENCY_STORE
from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.responses import err, ok


SERVER_NAME = "lvke-finance-model"


logger = get_logger(SERVER_NAME)


def _workspace_id(args: dict[str, Any]) -> str | None:
    value = args.get("workspace_id")
    return str(value).strip() if value is not None and str(value).strip() else None


def _run_uri(workspace_id: str, run_id: str | None) -> str | None:
    if not run_id:
        return None
    return f"lvke://finance-model/workspaces/{workspace_id}/runs/{run_id}"


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _unique_strings(value: Any) -> list[str]:
    return list(dict.fromkeys(_str_list(value)))


def _active_idempotency_record(
    workspace_id: str,
    key_hash: str,
    operation: str,
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    records = sorted(
        IDEMPOTENCY_STORE.list(workspace_id),
        key=lambda record: str(record.get("created_at") or ""),
        reverse=True,
    )
    for record in records:
        payload = record.get("payload") or {}
        if payload.get("operation") != operation or payload.get("key_hash") != key_hash:
            continue
        try:
            expires_at = datetime.fromisoformat(str(payload.get("expires_at") or ""))
        except ValueError:
            continue
        if expires_at > now:
            return record
    return None


def _expires_at() -> str:
    try:
        ttl = max(
            60,
            min(int(os.getenv("LVKE_MCP_IDEMPOTENCY_TTL_SECONDS", "86400")), 604800),
        )
    except ValueError:
        ttl = 86400
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()


def _latest_formal_boe(workspace_id: str, spec_id: str) -> dict[str, Any] | None:
    matches = [
        record
        for record in BASIS_OF_ESTIMATE_STORE.list(workspace_id)
        if (record.get("payload") or {}).get("spec_id") == spec_id
        and bool((record.get("payload") or {}).get("formal_ready"))
    ]
    return max(
        matches,
        key=lambda record: str(record.get("created_at") or ""),
        default=None,
    )


def _blocking_rules(data: dict[str, Any]) -> list[str]:
    return [
        str(issue["rule"])
        for issue in data.get("blocking_issues") or []
        if isinstance(issue, dict) and issue.get("rule")
    ]


def _blocked_run(code: str, action: str, spec_id: str) -> dict[str, Any]:
    return _ok_env(
        {"available": False, "error": code, "spec_id": spec_id},
        source=f"{SERVER_NAME}.finance_run_model",
        status="blocked",
        blockers=[code],
        next_actions=[action],
        run_id=None,
        spec_id=spec_id or None,
        missing_inputs=[],
    )


def _missing_run(field: str, spec_id: str) -> dict[str, Any]:
    return _ok_env(
        {
            "available": False,
            "error": "missing_inputs",
            "missing_inputs": [field],
            "spec_id": spec_id,
        },
        source=f"{SERVER_NAME}.finance_run_model",
        status="missing_inputs",
        blockers=[f"缺少必要输入：{field}"],
        next_actions=["重新 prepare 并确认包含必要输入的 FinanceSpec"],
        run_id=None,
        spec_id=spec_id or None,
        missing_inputs=[field],
    )


def _finalize(
    payload: dict[str, Any],
    *,
    status: str,
    resource_uris: list | tuple = (),
    warnings: list | tuple = (),
    blockers: list | tuple = (),
    next_actions: list | tuple = (),
    deprecated: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    payload["status"] = status
    payload["resource_uris"] = [str(uri) for uri in resource_uris if uri]
    payload["warnings"] = [str(warning) for warning in warnings if warning]
    payload["blockers"] = [str(blocker) for blocker in blockers if blocker]
    payload["next_actions"] = [str(action) for action in next_actions if action]
    if deprecated:
        payload["deprecated"] = True
    payload.update(extra)
    return payload


def _ok_env(data: Any, *, source: str, status: str, **extra: Any) -> dict[str, Any]:
    payload = _finalize(ok(data, source=source), status=status, **extra)
    if status == "partial":
        payload.update(
            {
                "success": True,
                "transport_success": True,
                "business_success": True,
                "completed": True,
                "outcome": status,
                "code": f"{SERVER_NAME}.partial",
                "message": "已生成结果并保留质量诊断",
            }
        )
    elif status in {"missing_inputs", "blocked", "failed"}:
        raw_code = (
            data.get("error") or data.get("reason") or status
            if isinstance(data, dict)
            else status
        )
        raw_message = (
            data.get("message") or raw_code
            if isinstance(data, dict)
            else raw_code
        )
        payload.update(
            {
                "success": False,
                "transport_success": True,
                "business_success": False,
                "completed": False,
                "outcome": status,
                "code": f"{SERVER_NAME}.{raw_code}",
                "message": str(raw_message),
            }
        )
    return payload


def _err_env(
    code: str,
    message: str,
    *,
    detail: Any = None,
    status: str = "failed",
    trace_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    del detail
    env = _finalize(
        err(code, message, trace_id=trace_id),
        status=status,
        **extra,
    )
    if not env["blockers"]:
        env["blockers"] = [message]
    return env


def _exception_env(
    log_message: str,
    code: str,
    message: str,
    *,
    status: str = "failed",
    **extra: Any,
) -> dict[str, Any]:
    trace_id = f"mcp_{uuid.uuid4().hex}"
    logger.exception("%s trace_id=%s", log_message, trace_id)
    return _err_env(
        code,
        message,
        status=status,
        trace_id=trace_id,
        **extra,
    )
