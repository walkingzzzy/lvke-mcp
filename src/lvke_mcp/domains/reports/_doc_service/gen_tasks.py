"""生成任务快照的存取与列举。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional



from .paths import (
    _read_json,
    _workspace_root,
    _write_json,
)


def _gen_task_path(workspace_id: str) -> Path:
    return _workspace_root(workspace_id) / "gen_task.json"


def _gen_task_snapshot_path(
    workspace_id: str,
    task_id: str,
) -> Path:
    """Return the durable per-task snapshot path for a generated task id."""
    normalized = str(task_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", normalized):
        raise ValueError("invalid report generation task id")
    return _workspace_root(workspace_id) / "gen_tasks" / f"{normalized}.json"


def _gen_task_is_latest(candidate: dict[str, Any], current: Any) -> bool:
    """Whether ``candidate`` may replace the legacy latest-task snapshot."""
    if not isinstance(current, dict):
        return True
    candidate_id = str(candidate.get("task_id") or "")
    current_id = str(current.get("task_id") or "")
    if candidate_id and candidate_id == current_id:
        return True
    try:
        candidate_created = float(candidate.get("created_at") or 0)
        current_created = float(current.get("created_at") or 0)
    except (TypeError, ValueError):
        return True
    return candidate_created >= current_created


def save_gen_task(workspace_id: str, task: dict[str, Any]) -> None:
    """持久化生成任务快照：按 task_id 落单文件 + 维护 legacy latest 单例。"""
    task_id = str(task.get("task_id") or "").strip()
    if task_id:
        _write_json(
            _gen_task_snapshot_path(workspace_id, task_id),
            task,
        )
    latest_path = _gen_task_path(workspace_id)
    if not task_id or _gen_task_is_latest(task, _read_json(latest_path, None)):
        _write_json(latest_path, task)


def load_gen_task(
    workspace_id: str,
    task_id: str = "",
) -> Optional[dict[str, Any]]:
    """Load a task by id, or the legacy latest snapshot when id is omitted.

    A task-id lookup falls back to a matching legacy singleton so snapshots
    written before per-task history was introduced remain addressable.
    """
    normalized = str(task_id or "").strip()
    if normalized:
        try:
            data = _read_json(
                _gen_task_snapshot_path(workspace_id, normalized),
                None,
            )
        except ValueError:
            return None
        if isinstance(data, dict):
            return data
        legacy = _read_json(_gen_task_path(workspace_id), None)
        if isinstance(legacy, dict) and str(legacy.get("task_id") or "") == normalized:
            return legacy
        return None
    data = _read_json(_gen_task_path(workspace_id), None)
    if isinstance(data, dict):
        return data
    rows = list_gen_tasks(workspace_id, limit=1)
    return rows[0] if rows else None


def list_gen_tasks(
    workspace_id: str,
    *,
    owner_user_id: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List durable report-generation snapshots newest first.

    The legacy singleton is included when it has not yet been migrated into
    ``gen_tasks``.  Optional owner filtering prevents a user's implicit
    "latest" lookup from adopting another user's task in a shared workspace.
    """

    rows: dict[str, dict[str, Any]] = {}
    base_directory = _workspace_root(workspace_id) / "gen_tasks"
    if base_directory.is_dir():
        for path in base_directory.glob("*.json"):
            data = _read_json(path, None)
            if isinstance(data, dict) and data.get("task_id"):
                rows.setdefault(str(data["task_id"]), data)
    legacy = _read_json(_gen_task_path(workspace_id), None)
    if isinstance(legacy, dict) and legacy.get("task_id"):
        rows.setdefault(str(legacy["task_id"]), legacy)
    owner = str(owner_user_id or "").strip()
    values = [
        row for row in rows.values()
        if not owner or str(row.get("owner_user_id") or "").strip() == owner
    ]

    def sort_key(row: dict[str, Any]) -> tuple[float, str]:
        try:
            created = float(row.get("created_at") or 0)
        except (TypeError, ValueError):
            created = 0.0
        return created, str(row.get("task_id") or "")

    values.sort(key=sort_key, reverse=True)
    return values[: max(1, min(int(limit or 100), 500))]
