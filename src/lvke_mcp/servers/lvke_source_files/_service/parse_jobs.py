"""Parse job status, retry and cancel over immutable attempt records."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from lvke_mcp.adapters import source_files_repository as source_api

from .envelope import _blocked, _envelope, _from_source_exception, _now
from .paths import _job_uri


def parse_status(
    workspace_id: str,
    job_id: str,
) -> dict[str, Any]:
    try:
        job, _record = source_api._require_parse_job_from_state(  # noqa: SLF001
            source_api._load_state(workspace_id),  # noqa: SLF001
            workspace_id,
            job_id,
            "mcp",
        )
    except source_api.SourceFileError as exc:
        return _from_source_exception(exc)
    public = source_api._public_parse_job(job)  # noqa: SLF001
    state = str(public.get("status") or "failed")
    success = state in {"queued", "running", "succeeded"}
    status = "ok" if success else "partial" if state == "partial" else "blocked"
    return _envelope(
        success=success,
        status=status,
        code="source_parse_incomplete" if not success else "",
        blockers=[] if state == "partial" else (["source_parse_incomplete"] if not success else []),
        resource_uris=[_job_uri(workspace_id, job_id)],
        task_status=state,
        parse_job=public,
    )


def parse_retry(
    workspace_id: str,
    job_id: str,
    *,
    idempotency_key: str,
    parse_immediately: bool = True,
) -> dict[str, Any]:
    request_hash = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    scope = f"mcp-retry:{job_id}:{idempotency_key}"
    try:
        with source_api._state_guard(workspace_id):  # noqa: SLF001
            state = source_api._load_state(workspace_id)  # noqa: SLF001
            old, file_record = source_api._require_parse_job_from_state(  # noqa: SLF001
                state, workspace_id, job_id, "mcp"
            )
            prior = source_api._active_idempotency_record(  # noqa: SLF001
                state["idempotency"], scope
            )
            if prior:
                if prior.get("request_hash") != request_hash:
                    return _blocked("idempotency_conflict", "同一幂等键已用于不同解析重试")
                new_id = str(prior.get("job_id") or "")
                new_job, _ = source_api._require_parse_job_from_state(  # noqa: SLF001
                    state, workspace_id, new_id, "mcp"
                )
                public = source_api._public_parse_job(new_job)  # noqa: SLF001
                return _envelope(
                    success=True,
                    status="ok",
                    resource_uris=[_job_uri(workspace_id, new_id)],
                    parse_job_id=new_id,
                    parse_job=public,
                    idempotent_replay=True,
                )
            if old.get("status") not in {"failed", "partial", "cancelled"}:
                return _blocked("parse_retry_not_allowed", "只有 failed/partial/cancelled 任务可以重试")
            if file_record.get("parse_job_id") != job_id:
                return _blocked("parse_retry_not_allowed", "解析任务已被更新尝试取代")
            new_id = f"job_{uuid.uuid4().hex}"
            job = {
                "job_id": new_id,
                "file_id": old["file_id"],
                "workspace_id": workspace_id,
                "status": "queued",
                "progress": 0,
                "attempt": int(old.get("attempt") or 1) + 1,
                "created_at": _now().isoformat(),
                "retry_of": job_id,
            }
            state["jobs"][new_id] = job
            file_record.update(
                {
                    "parse_job_id": new_id,
                    "status": "queued",
                    "extract_status": "queued",
                    "ocr_status": "pending",
                    "deterministic_status": "pending",
                    "updated_at": _now().isoformat(),
                }
            )
            state["idempotency"][scope] = source_api._idempotency_record(  # noqa: SLF001
                scope,
                request_hash,
                job_id=new_id,
                file_id=str(old["file_id"]),
            )
            source_api._save_state(workspace_id, state)  # noqa: SLF001
        if parse_immediately:
            source_api.parse_source_file(workspace_id, new_id)
        return parse_status(workspace_id, new_id) | {
            "parse_job_id": new_id,
            "idempotent_replay": False,
        }
    except source_api.SourceFileError as exc:
        return _from_source_exception(exc)


def parse_cancel(
    workspace_id: str,
    job_id: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    scope = f"mcp-cancel:{job_id}:{idempotency_key}"
    try:
        with source_api._state_guard(workspace_id):  # noqa: SLF001
            state = source_api._load_state(workspace_id)  # noqa: SLF001
            job, record = source_api._require_parse_job_from_state(  # noqa: SLF001
                state, workspace_id, job_id, "mcp"
            )
            prior = source_api._active_idempotency_record(  # noqa: SLF001
                state["idempotency"], scope
            )
            if prior:
                return _envelope(
                    success=True,
                    status="ok",
                    resource_uris=[_job_uri(workspace_id, job_id)],
                    task_status="cancelled",
                    idempotent_replay=True,
                )
            if job.get("status") not in {"queued", "running", "cancelled"}:
                return _blocked("parse_cancel_not_allowed", "终态解析任务不能取消")
            job.update(
                {
                    "status": "cancelled",
                    "progress": 100,
                    "finished_at": _now().isoformat(),
                    "worker_token": "",
                }
            )
            if record.get("parse_job_id") == job_id:
                record.update(
                    {
                        "status": "cancelled",
                        "extract_status": "cancelled",
                        "updated_at": _now().isoformat(),
                    }
                )
            state["idempotency"][scope] = source_api._idempotency_record(  # noqa: SLF001
                scope, hashlib.sha256(job_id.encode("utf-8")).hexdigest(), job_id=job_id
            )
            source_api._save_state(workspace_id, state)  # noqa: SLF001
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[_job_uri(workspace_id, job_id)],
            task_status="cancelled",
            idempotent_replay=False,
        )
    except source_api.SourceFileError as exc:
        return _from_source_exception(exc)
