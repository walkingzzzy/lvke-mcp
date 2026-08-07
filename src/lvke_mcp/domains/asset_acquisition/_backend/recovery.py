"""中断任务恢复与已发布工件补齐。"""

from __future__ import annotations

import copy
import json
from typing import Any



from .artifacts import (
    _bind_succeeded_artifact,
    execute_queued_artifact,
)

from .base import (
    _RECOVERY_POOL,
    _now,
)

from .runs import (
    execute_queued_run,
    get_run,
)

from .store import (
    _artifacts_root,
    _history_event,
    _load,
    _save,
    _state_guard,
    _state_path,
    _workspace_ids,
)

from .xlsx import (
    _file_hash,
)


def _recover_published_artifact(
    workspace_id: str, artifact_id: str, row: dict[str, Any],
) -> tuple[bool, str]:
    """Recover a pack atomically published just before a process crash."""

    directory = _artifacts_root(workspace_id) / artifact_id
    index_path = directory / "附件索引.json"
    if not index_path.is_file():
        return False, "not_published"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False, "invalid_index"
    if (
        index.get("artifact_id") != artifact_id
        or index.get("run_id") != row.get("run_id")
    ):
        return False, "binding_mismatch"
    consistency = index.get("numeric_consistency") or {}
    if not isinstance(consistency, dict) or consistency.get("status") != "passed":
        return False, "numeric_consistency_failed"
    files = list(index.get("files") or [])
    for item in files:
        path = directory / str(item.get("name") or "")
        if not path.is_file() or _file_hash(path) != item.get("sha256"):
            return False, "file_hash_mismatch"
    files.append({
        "name": index_path.name,
        "size_bytes": index_path.stat().st_size,
        "sha256": _file_hash(index_path),
    })
    row.update({
        "ok": True, "status": "succeeded", "progress": 100,
        "directory": str(directory), "files": files,
        "numeric_consistency": "passed",
        "report_data_hash": index.get("report_data_hash"),
        "consistency_checks": list(consistency.get("checks") or []),
        "recovered_from_published_artifact": True,
        "updated_at": _now(),
    })
    row.setdefault("state_history", []).append(_history_event(
        "artifact_generated",
        request_id=str(row.get("request_id") or ""),
        artifact_id=artifact_id,
        recovery=True,
    ))
    return True, "recovered"


def recover_incomplete_acquisition_tasks(
    submit_run: Any | None = None, submit_artifact: Any | None = None,
) -> dict[str, Any]:
    """Requeue durable acquisition runs/artifacts left by a process restart."""

    run_submitter = submit_run or (
        lambda workspace_id, run_id: _RECOVERY_POOL.submit(
            execute_queued_run,
            workspace_id,
            run_id,
        )
    )
    artifact_submitter = submit_artifact or (
        lambda workspace_id, artifact_id: _RECOVERY_POOL.submit(
            execute_queued_artifact,
            workspace_id,
            artifact_id,
        )
    )
    runs: list[tuple[str, str]] = []
    artifacts: list[tuple[str, str]] = []
    recovered_bindings: list[tuple[str, str]] = []
    recovered_artifacts = 0
    failed_artifacts = 0
    for workspace_id in _workspace_ids():
        if not _state_path(workspace_id).is_file():
            continue
        with _state_guard(workspace_id):
            state = _load(workspace_id)
            changed = False
            for run_id, row in state["runs"].items():
                if row.get("status") not in {"queued", "running"}:
                    continue
                row.update({
                    "status": "queued", "progress": 0, "updated_at": _now(),
                    "recovered_at": _now(),
                    "recovery_count": int(row.get("recovery_count") or 0) + 1,
                })
                row.setdefault("state_history", []).append(_history_event(
                    "queued", request_id=str(row.get("request_id") or ""), recovery=True,
                ))
                runs.append((workspace_id, run_id))
                changed = True
            for artifact_id, row in state["artifacts"].items():
                if row.get("status") not in {"queued", "running"}:
                    continue
                recovered, reason = _recover_published_artifact(
                    workspace_id,
                    artifact_id,
                    row,
                )
                if recovered:
                    recovered_artifacts += 1
                    recovered_bindings.append((workspace_id, artifact_id))
                    changed = True
                    continue
                if reason != "not_published":
                    row.update({
                        "ok": False, "status": "failed", "progress": 100,
                        "numeric_consistency": "failed", "updated_at": _now(),
                        "error": {
                            "code": "ARTIFACT_RECOVERY_FAILED", "message": reason,
                            "retryable": False,
                        },
                    })
                    failed_artifacts += 1
                    changed = True
                    continue
                row.update({
                    "status": "queued", "progress": 0, "updated_at": _now(),
                    "recovered_at": _now(),
                    "recovery_count": int(row.get("recovery_count") or 0) + 1,
                })
                artifacts.append((workspace_id, artifact_id))
                changed = True
            if changed:
                _save(workspace_id, state)
    for workspace_id, artifact_id in recovered_bindings:
        artifact = dict(
            _load(workspace_id)["artifacts"].get(artifact_id)
            or {}
        )
        run = get_run(
            workspace_id,
            str(artifact.get("run_id") or ""),
        )
        if run:
            with _state_guard(workspace_id):
                state = _load(workspace_id)
                stored_run = state["runs"].get(str(artifact.get("run_id") or ""))
                if stored_run:
                    stored_run["lifecycle_status"] = "artifact_generated"
                    stored_run.setdefault("state_history", []).append(_history_event(
                        "artifact_generated",
                        request_id=str(artifact.get("request_id") or ""),
                        artifact_id=artifact_id,
                        recovery=True,
                    ))
                    _save(workspace_id, state)
                    run = copy.deepcopy(stored_run)
        bound = _bind_succeeded_artifact(
            workspace_id,
            run,
            artifact,
        ) if run else {
            "ok": False, "error": "ARTIFACT_BINDING_FAILED",
        }
        if not bound.get("ok"):
            with _state_guard(workspace_id):
                state = _load(workspace_id)
                stored = state["artifacts"].get(artifact_id)
                if stored:
                    stored.update({
                        "ok": False, "status": "failed",
                        "error": {
                            "code": "ARTIFACT_BINDING_FAILED",
                            "message": "recovered artifact finance binding failed",
                            "retryable": False,
                        },
                        "updated_at": _now(),
                    })
                    recovered_artifacts -= 1
                    failed_artifacts += 1
                    _save(workspace_id, state)
    for workspace_id, run_id in runs:
        run_submitter(workspace_id, run_id)
    for workspace_id, artifact_id in artifacts:
        artifact_submitter(workspace_id, artifact_id)
    return {
        "runs_requeued": len(runs),
        "artifacts_requeued": len(artifacts),
        "artifacts_recovered": recovered_artifacts,
        "artifacts_failed": failed_artifacts,
    }
