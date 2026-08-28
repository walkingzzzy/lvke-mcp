from __future__ import annotations

from typing import Any


CANONICAL_STATUSES = frozenset({
    "ok", "accepted", "partial", "empty", "missing_inputs", "blocked",
    "incomplete", "failed", "upstream_failure",
})
SUCCESSFUL_TERMINAL_STATUSES = frozenset({
    "applied", "released", "completed", "done", "cancelled",
})
ACTIVE_STATUSES = frozenset({"pending", "queued", "running", "started", "processing"})
COMPLETED_STATUSES = frozenset({"ok", "partial", "empty"})
SUCCESSFUL_STATUSES = frozenset({*COMPLETED_STATUSES, "accepted"})


def normalize_operation_outcome(result: dict[str, Any], *, server_name: str) -> dict[str, Any]:
    """Project domain status without conflating operation, quality and viability."""

    payload = dict(result)
    raw_status = str(
        payload.get("status")
        or ("failed" if payload.get("success") is False else "ok")
    ).strip().lower()
    if raw_status in CANONICAL_STATUSES:
        status = raw_status
    elif raw_status in SUCCESSFUL_TERMINAL_STATUSES:
        status = "ok"
        payload.setdefault("domain_status", raw_status)
    elif raw_status in ACTIVE_STATUSES:
        status = "accepted"
        payload.setdefault("task_status", raw_status)
    else:
        status = "blocked" if payload.get("success") is False else "ok"
        payload.setdefault("domain_status", raw_status)

    system_success = bool(payload.get(
        "system_success",
        payload.get("transport_success", status != "failed"),
    )) and status != "failed"
    operation_success = status in SUCCESSFUL_STATUSES
    payload.update({
        "status": status,
        "success": operation_success,
        "business_success": operation_success,
        "system_success": system_success,
        "transport_success": system_success,
        "completed": status in COMPLETED_STATUSES,
        "outcome": status,
    })
    if status == "empty":
        payload.setdefault("result_available", False)
    elif status in {"ok", "partial"}:
        payload.setdefault("result_available", True)
    if not operation_success:
        payload.setdefault("code", f"{server_name}.{status}")
        payload.setdefault("message", {
            "missing_inputs": "缺少完成业务所需输入",
            "blocked": "当前操作无法执行",
            "incomplete": "业务操作尚未完成",
            "failed": "工具执行失败",
            "upstream_failure": "上游服务未能提供结果",
        }.get(status, "业务操作未成功完成"))
    return payload