"""研究事件游标编解码与事件分页读取。"""

from __future__ import annotations

from __future__ import annotations

import base64
import json
from typing import Any


from lvke_mcp.adapters.research_repository import AGENT_SESSION_STORE, EVENT_STORE
from lvke_mcp.runtime.storage import canonical_json

from .base import (
    _failure,
)


def _encode_event_cursor(created_at: str, event_id: str) -> str:
    raw = canonical_json({"created_at": created_at, "event_id": event_id}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

def _decode_event_cursor(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode((cursor + padding).encode("ascii")))
        created_at = str(payload.get("created_at") or "")
        event_id = str(payload.get("event_id") or "")
        if not created_at or not event_id:
            raise ValueError
        return created_at, event_id
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("event_cursor_invalid") from exc

def list_events(
    workspace_id: str,
    task_id: str,
    *,
    after_cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    if AGENT_SESSION_STORE.get(workspace_id, task_id) is None:
        return _failure("task_not_found", "未找到 Agent DR 会话")
    records = [
        record
        for record in EVENT_STORE.list(workspace_id)
        if str((record.get("payload") or {}).get("task_id") or "") == task_id
    ]
    records.sort(key=lambda record: (str(record.get("created_at") or ""), str(record.get("object_id") or "")))
    if after_cursor:
        try:
            marker = _decode_event_cursor(after_cursor)
        except ValueError:
            return _failure("event_cursor_invalid", "事件游标无效")
        records = [
            record
            for record in records
            if (str(record.get("created_at") or ""), str(record.get("object_id") or "")) > marker
        ]
    bounded = max(1, min(int(limit), 200))
    page = records[:bounded]
    has_more = len(records) > bounded
    next_cursor = (
        _encode_event_cursor(str(page[-1].get("created_at") or ""), str(page[-1].get("object_id") or ""))
        if page
        else after_cursor or None
    )
    return {
        "success": True,
        "status": "ok",
        "task_id": task_id,
        "events": [record.get("payload") or {} for record in page],
        "next_cursor": next_cursor,
        "has_more": has_more,
        "resource_uris": [str(record.get("resource_uri") or "") for record in page],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }
