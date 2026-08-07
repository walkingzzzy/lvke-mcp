"""工作区路径、state 读写与幂等/历史/issue 记录。"""

from __future__ import annotations

import copy
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from lvke_mcp.runtime.workspace import data_root, deliverable_dir, workspace_root

from .base import (
    _LOCK,
    _idempotency_ttl_seconds,
    _now,
)


def _root(
    workspace_id: str,
) -> Path:
    """收购域**运行时状态**根（state.json / state.lock），留在 data_root。

    工件输出不走这里，见 :func:`_artifacts_root`：状态是可重建的本地缓存，
    工件是要随仓库留存与签审的交付物，两者分开存放。
    """
    return workspace_root(workspace_id) / "finance_acquisition"


def _artifacts_root(
    workspace_id: str,
) -> Path:
    """收购域交付工件根（报告 MD/DOCX、财务模型 XLSX、附件索引）。

    统一落到仓库 ``lvke产出/{ws}/asset-acquisition/artifacts``，与通用可研的
    十三表和研报同一口径。
    """
    return deliverable_dir(workspace_id, "asset-acquisition", "artifacts")


def _state_path(
    workspace_id: str,
) -> Path:
    return _root(workspace_id) / "state.json"


@contextmanager
def _state_guard(
    workspace_id: str,
):
    lock_path = _root(workspace_id) / "state.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, FileLock(str(lock_path), timeout=30):
        yield


def _load(
    workspace_id: str,
) -> dict[str, Any]:
    try:
        raw = json.loads(
            _state_path(workspace_id).read_text(
                encoding="utf-8"
            )
        )
    except FileNotFoundError:
        raw = {}
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"acquisition state is corrupt for workspace {workspace_id}") from exc
    return {
        "version": "acquisition_store.v2",
        "specs": dict(raw.get("specs") or {}),
        "runs": dict(raw.get("runs") or {}),
        "idempotency": dict(raw.get("idempotency") or {}),
        "artifacts": dict(raw.get("artifacts") or {}),
        "scenario_matrices": dict(raw.get("scenario_matrices") or {}),
    }


def _save(
    workspace_id: str,
    state: dict[str, Any],
) -> None:
    path = _state_path(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"state.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as target:
            json.dump(state, target, ensure_ascii=False, indent=2, default=str)
            target.flush()
            os.fsync(target.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _idempotency_record(scope: str, body_hash: str, **resource: Any) -> dict[str, Any]:
    created = datetime.now(timezone.utc)
    expires = datetime.fromtimestamp(
        created.timestamp() + _idempotency_ttl_seconds(), tz=timezone.utc,
    )
    return {
        "scope": scope,
        "body_hash": body_hash,
        "request_body_hash": body_hash,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        **resource,
    }


def _active_idempotency_record(
    records: dict[str, Any], scope: str,
) -> dict[str, Any] | None:
    record = records.get(scope)
    if not isinstance(record, dict):
        return None
    expires_at = str(record.get("expires_at") or "")
    if not expires_at:
        return record
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        records.pop(scope, None)
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        records.pop(scope, None)
        return None
    return record


def _history_event(status: str, *, request_id: str = "", **details: Any) -> dict[str, Any]:
    return {
        "status": status, "at": _now(),
        "request_id": request_id, "details": details,
    }


def _close_issue(run: dict[str, Any], code: str, *, reason: str) -> None:
    for issue in run.get("issues") or []:
        if issue.get("code") == code and issue.get("status") == "open":
            issue.update({"status": "closed", "closed_at": _now(), "resolution": reason})


def _open_issue(run: dict[str, Any], code: str, detail: str) -> None:
    existing = next(
        (issue for issue in run.get("issues") or [] if issue.get("code") == code and issue.get("status") == "open"),
        None,
    )
    if existing:
        existing.update({"detail": detail, "updated_at": _now()})
        return
    run.setdefault("issues", []).append({
        "code": code, "blocking": True, "status": "open", "detail": detail, "created_at": _now(),
    })


def _migration_binding(spec: dict[str, Any]) -> dict[str, Any]:
    trace = copy.deepcopy(spec.get("migration_trace") or {})
    if not isinstance(trace, dict):
        trace = {}
    return {
        "source_spec_version": trace.get("source_spec_version") or spec.get("version"),
        "migration_trace": trace,
        "migration_steps": copy.deepcopy(trace.get("steps") or []),
    }


def _workspace_ids() -> list[str]:
    root = data_root() / "workspaces"
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and str(path.name).strip()
    )
