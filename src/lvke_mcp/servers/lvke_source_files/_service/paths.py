"""Workspace-scoped staging paths, session locks and Resource URIs."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from filelock import FileLock

from lvke_mcp.runtime.storage import require_safe_id
from lvke_mcp.runtime.workspace import workspace_root

from .constants import DOMAIN


def _source_root(workspace_id: str) -> Path:
    return workspace_root(require_safe_id(workspace_id, "workspace_id")) / "source-files"


def _sessions_root(workspace_id: str) -> Path:
    root = _source_root(workspace_id) / "staging" / "mcp_sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_dir(workspace_id: str, upload_id: str) -> Path:
    return _sessions_root(workspace_id) / require_safe_id(upload_id, "upload_id")


def _manifest_path(workspace_id: str, upload_id: str) -> Path:
    return _session_dir(workspace_id, upload_id) / "manifest.json"


def _session_lock(workspace_id: str, upload_id: str) -> FileLock:
    directory = _session_dir(workspace_id, upload_id)
    directory.mkdir(parents=True, exist_ok=True)
    return FileLock(str(directory / "session.lock"), timeout=30)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as target:
            json.dump(value, target, ensure_ascii=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _file_uri(workspace_id: str, file_id: str) -> str:
    return f"lvke://{DOMAIN}/workspaces/{workspace_id}/files/{file_id}"


def _analysis_uri(workspace_id: str, file_id: str) -> str:
    return f"lvke://{DOMAIN}/workspaces/{workspace_id}/analyses/{file_id}"


def _job_uri(workspace_id: str, job_id: str) -> str:
    return f"lvke://{DOMAIN}/workspaces/{workspace_id}/parse-jobs/{job_id}"
