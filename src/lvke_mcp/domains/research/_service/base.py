"""研究域共享基座：状态集合、过渡锁、幂等窗口、事件追加与失败信封。"""

from __future__ import annotations

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from filelock import FileLock
from lvke_mcp.runtime.workspace import workspace_root

from lvke_mcp.adapters.research_repository import EVENT_STORE, IDEMPOTENCY_STORE


_TERMINAL = {"done", "partial", "needs_clarification", "blocked", "failed", "failed_report", "cancelled"}
_CONTINUABLE = {"partial", "needs_clarification", "blocked", "failed_report"}

# bundle 只固化 artifacts/ 目录真实存在的产物；checkpoint 单独经回退探测。
_BUNDLE_ARTIFACTS = ("report", "sources", "evidence", "extracts", "citation_audit", "quality")

@contextmanager
def _agent_transition_guard(workspace_id: str, task_id: str):
    directory = workspace_root(workspace_id) / "mcp_objects" / "deep-research" / "agent-locks"
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / f"{task_id}.lock"), timeout=30):
        yield

def _idempotency_ttl_seconds() -> int:
    try:
        return max(60, min(int(os.getenv("LVKE_MCP_IDEMPOTENCY_TTL_SECONDS", "86400")), 604800))
    except ValueError:
        return 86400


def _active_idempotency_record(
    workspace_id: str,
    key_hash: str,
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    records = sorted(
        IDEMPOTENCY_STORE.list(workspace_id),
        key=lambda record: str(record.get("created_at") or ""),
        reverse=True,
    )
    for record in records:
        saved = record.get("payload") or {}
        if saved.get("operation") != "dr_start" or saved.get("key_hash") != key_hash:
            continue
        try:
            expires_at = datetime.fromisoformat(str(saved.get("expires_at") or ""))
        except ValueError:
            continue
        if expires_at > now:
            return record
    return None

def _append_event(
    workspace_id: str,
    task_id: str,
    event_type: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Persist structured lifecycle state without model reasoning or hidden traces."""

    payload = {
        "task_id": task_id,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    linked_ids = [task_id]
    for key in (
        "session_id",
        "plan_revision_id",
        "proposal_id",
        "checkpoint_id",
        "new_task_id",
        "transition_id",
        "research_package_id",
        "base_plan_revision_id",
    ):
        if data.get(key):
            linked_ids.append(str(data[key]))
    linked_ids.extend(str(item) for item in data.get("source_ids") or [])
    return EVENT_STORE.put(
        workspace_id,
        payload,
        producer=f"lvke-deep-research.{event_type}",
        status="ok",
        source_ids=linked_ids,
        basis=payload,
    )

def _normalize_profile(value: str) -> str:
    return "deep_standard" if value == "deep" else value if value in {"quick", "deep_assist", "deep_standard", "deep_max"} else "deep_standard"


def _failure(code: str, message: str) -> dict[str, Any]:
    # Some DR output schemas require task-specific fields on success.  Keep
    # this business block as a failure envelope; OfficialStdioServer exposes
    # blocked results to MCP clients with ``isError=false``.
    return {"success": False, "status": "blocked", "code": code, "message": message, "resource_uris": [], "warnings": [], "blockers": [code], "next_actions": []}
