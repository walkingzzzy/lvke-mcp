"""MCP-owned job repository with idempotency reservation (§6.5).

Status: intentionally reserved, not yet wired. Every one of the 169 public
tools registers ``task_support="forbidden"`` (see
:mod:`lvke_mcp.runtime.transport`), so no caller reaches this repository today.
It is kept as the infrastructure for ``task_support != "forbidden"`` rather
than deleted as dead code; decide its fate together with async task support,
not as part of a public-surface compression pass.

Job state machine: ``queued -> running -> complete | partial | failed | cancelled``.
A ``queued`` job may also be cancelled before it starts.

Idempotency reservation:
  - same key + same input hash -> the same terminal job record (replay).
  - same key + different input hash -> ``idempotency_conflict`` error.

Jobs are mutable state-machine records, so unlike the immutable
:class:`~lvke_mcp.runtime.storage.JSONArtifactStore` objects they are stored
directly under ``{data_root}/workspaces/{workspace_id}/jobs/{domain}/`` per
the §6.2 workspace layout.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable

from filelock import FileLock

from lvke_mcp.runtime.storage import require_safe_id, sha256_json, utc_now
from lvke_mcp.runtime.workspace import workspace_root

JOB_STATUSES = ("queued", "running", "complete", "partial", "failed", "cancelled")
_TERMINAL = frozenset({"complete", "partial", "failed", "cancelled"})

# queued -> running | cancelled ; running -> complete | partial | failed | cancelled
_ALLOWED: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"complete", "partial", "failed", "cancelled"}),
    "complete": frozenset(),
    "partial": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def _input_hash(idempotency_key: str, input_payload: Any) -> str:
    return sha256_json({"idempotency_key": idempotency_key, "input": input_payload})


class JobRepository:
    """Persist job state-machine records for one workspace + domain."""

    def __init__(self, workspace_id: str, *, domain: str = "jobs") -> None:
        self.workspace_id = require_safe_id(workspace_id, "workspace_id")
        self.domain = require_safe_id(domain, "domain")
        self._directory = workspace_root(workspace_id) / "jobs" / self.domain

    # ---- persistence ----------------------------------------------------
    def _path(self, job_id: str) -> Path:
        return self._directory / f"{job_id}.json"

    def _read(self, job_id: str) -> dict[str, Any] | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(loaded, dict) and str(loaded.get("workspace_id") or "") == self.workspace_id:
            return loaded
        return None

    def _write(self, record: dict[str, Any]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        target = self._path(str(record["job_id"]))
        with FileLock(str(target) + ".lock", timeout=30):
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

    def _find_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        if not self._directory.is_dir():
            return None
        for path in sorted(self._directory.glob("job_*.json")):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(loaded, dict)
                and str(loaded.get("workspace_id") or "") == self.workspace_id
                and str(loaded.get("idempotency_key") or "") == idempotency_key
            ):
                return loaded
        return None

    # ---- public API -----------------------------------------------------
    def reserve(
        self,
        idempotency_key: str,
        input_payload: Any,
        *,
        producer: str,
        job_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Reserve a job under an idempotency key.

        Returns ``(record, created)``.  A replay (same key + same input hash)
        returns the existing terminal record with ``created=False``; the same
        key with a different input hash raises ``idempotency_conflict``.
        """
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency_key_required")
        if len(key) > 200:
            raise ValueError("idempotency_key_too_long")
        current_hash = _input_hash(key, input_payload)
        existing = self._find_by_key(key)
        if existing is not None:
            if existing.get("input_hash") == current_hash:
                return existing, False
            raise ValueError("idempotency_conflict")
        record = {
            "job_id": require_safe_id(job_id, "job_id") if job_id else "job_" + uuid.uuid4().hex,
            "workspace_id": self.workspace_id,
            "domain": self.domain,
            "idempotency_key": key,
            "input_hash": current_hash,
            "input": input_payload,
            "producer": producer,
            "status": "queued",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self._write(record)
        return record, True

    def transition(
        self,
        job_id: str,
        *,
        status: str,
        note: str = "",
        result: Any | None = None,
    ) -> dict[str, Any]:
        """Advance a job to ``status``, enforcing the §6.5 state machine."""
        job_id = require_safe_id(job_id, "job_id")
        if status not in JOB_STATUSES:
            raise ValueError("invalid_job_status")
        with FileLock(str(self._path(job_id)) + ".lock", timeout=30):
            record = self._read(job_id)
            if record is None:
                raise ValueError("job_not_found")
            current = str(record.get("status") or "queued")
            if status not in _ALLOWED.get(current, frozenset()):
                raise ValueError(f"invalid_job_transition:{current}->{status}")
            record["status"] = status
            record["updated_at"] = utc_now()
            if note:
                record["note"] = note
            if result is not None:
                record["result"] = result
            self._write(record)
        return record

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._read(require_safe_id(job_id, "job_id"))

    def list(self) -> list[dict[str, Any]]:
        if not self._directory.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self._directory.glob("job_*.json")):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(loaded, dict) and str(loaded.get("workspace_id") or "") == self.workspace_id:
                records.append(loaded)
        return records

    def iter_statuses(self) -> Iterable[str]:
        return JOB_STATUSES
