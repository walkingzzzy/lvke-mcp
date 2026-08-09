"""审查启动、异步调度与恢复，以及 get_review。get_review 归此组是因为它在读取时会触发异步恢复（_resume_async_review_if_needed），属生命周期操作而非纯投影。"""

from __future__ import annotations

import threading
from typing import Any

from lvke_mcp.runtime.storage import require_safe_id, sha256_json, utc_now
from lvke_mcp.servers.lvke_deliverable_review.contracts import DEPLOYMENT_MODES
from lvke_mcp.servers.lvke_deliverable_review.store import STORE

from .base import (
    _ASYNC_LOCK,
    _ASYNC_THREADS,
    _blocked,
    _message,
    _next_actions,
    _ok,
    _review_envelope_status,
    _review_uri,
    _write,
)

from .events import (
    _project,
)

from .executor import (
    _run_review,
)

from .preparation import (
    _verified_preparation_record,
)


def _run_async_review(
    workspace_id: str,
    review_id: str,
    preparation_payload: dict[str, Any] | None,
    mode: str,
    preparation_integrity_reasons: list[str] | None = None,
) -> None:
    key = (workspace_id, review_id)
    try:
        _run_review(
            workspace_id,
            review_id,
            preparation_payload,
            mode,
            preparation_integrity_reasons,
        )
    finally:
        with _ASYNC_LOCK:
            if _ASYNC_THREADS.get(key) is threading.current_thread():
                _ASYNC_THREADS.pop(key, None)


def _schedule_async_review(
    workspace_id: str,
    review_id: str,
    preparation_payload: dict[str, Any] | None,
    mode: str,
    preparation_integrity_reasons: list[str] | None = None,
) -> bool:
    key = (workspace_id, review_id)
    with _ASYNC_LOCK:
        existing = _ASYNC_THREADS.get(key)
        if existing is not None and existing.is_alive():
            return False
        thread = threading.Thread(
            target=_run_async_review,
            args=(
                workspace_id,
                review_id,
                preparation_payload,
                mode,
                preparation_integrity_reasons,
            ),
            name=f"deliverable-review-{review_id[-8:]}",
            daemon=True,
        )
        _ASYNC_THREADS[key] = thread
        thread.start()
    return True


def _resume_async_review_if_needed(workspace_id: str, state: dict[str, Any]) -> bool:
    if (
        state.get("execution") != "async"
        or state.get("validation_complete")
        or state.get("invalidated")
    ):
        return False
    preparation_id = str(state.get("review_preparation_id") or "")
    preparation, integrity_reasons = _verified_preparation_record(
        workspace_id,
        preparation_id,
        expected_basis_hash=str(
            state.get("preparation_basis_hash") or ""
        ),
        expected_content_hash=str(
            state.get("preparation_content_hash") or ""
        ),
    )
    payload = (preparation or {}).get("payload")
    return _schedule_async_review(
        workspace_id,
        str(state.get("review_id") or ""),
        payload if isinstance(payload, dict) else None,
        str(state.get("mode") or "deep"),
        integrity_reasons,
    )


def start(args: dict[str, Any]) -> dict[str, Any]:
    def execute(workspace_id: str) -> dict[str, Any]:
        preparation_id = str(args.get("review_preparation_id") or "")
        mode = str(args.get("mode") or "quick")
        execution = str(args.get("execution") or ("async" if mode == "deep" else "sync"))
        deployment_mode = str(args.get("deployment_mode") or "enforced")
        if mode not in {"quick", "deep"}:
            return _blocked("review_mode_invalid", "mode 必须为 quick 或 deep")
        if execution not in {"sync", "async"} or (mode == "quick" and execution == "async"):
            return _blocked("review_execution_invalid", "快速审查必须同步；深度审查支持同步或异步")
        if deployment_mode not in DEPLOYMENT_MODES:
            return _blocked(
                "review_deployment_mode_invalid",
                "deployment_mode 必须为 enforced 或 shadow",
            )
        start_identity = {
            "operation": "review_start",
            "idempotency_key": str(args.get("idempotency_key") or ""),
        }
        start_key_hash = sha256_json(start_identity)
        start_request_hash = sha256_json(args)
        review_id = (
            "review_"
            + start_key_hash.removeprefix("sha256:")[:32]
        )
        existing_events = STORE.events(workspace_id, review_id)
        if existing_events:
            first = existing_events[0]
            existing_created = first.get("payload") or {}
            if (
                first.get("event_type") != "review_created"
                or existing_created.get("start_key_hash") != start_key_hash
                or existing_created.get("start_request_hash") != start_request_hash
                or existing_created.get("review_preparation_id") != preparation_id
            ):
                # Raising keeps _write from caching the conflicting request
                # over the recoverable operation recorded in the event log.
                raise ValueError("idempotency_key_conflict")
        engine_terminal = any(
            event.get("event_type") in {"review_completed", "review_failed"}
            for event in existing_events
        )
        preparation: dict[str, Any] | None = None
        payload: dict[str, Any] | None = None
        preparation_integrity_reasons: list[str] = []
        if not engine_terminal:
            preparation, preparation_integrity_reasons = (
                _verified_preparation_record(
                    workspace_id,
                    preparation_id,

                    expected_basis_hash=str(
                        (existing_created if existing_events else {}).get(
                            "preparation_basis_hash"
                        )
                        or ""
                    ),
                    expected_content_hash=str(
                        (existing_created if existing_events else {}).get(
                            "preparation_content_hash"
                        )
                        or ""
                    ),
                )
            )
            candidate_payload = (preparation or {}).get("payload")
            payload = (
                candidate_payload
                if isinstance(candidate_payload, dict) else None
            )
        if not existing_events:
            if preparation is None or payload is None:
                if preparation_integrity_reasons and not set(
                    preparation_integrity_reasons
                ).intersection({
                    "preparation_record_unavailable",
                    "preparation_owner_mismatch",
                }):
                    return _blocked(
                        "preparation_integrity_failed",
                        "审查准备对象完整性校验失败",
                        integrity_reasons=preparation_integrity_reasons,
                    )
                return _blocked("preparation_not_found", _message("preparation_not_found"))
            created = {
                "review_preparation_id": preparation_id,
                "preparation_basis_hash": preparation.get("basis_hash"),
                "preparation_content_hash": preparation.get("content_hash"),
                "start_key_hash": start_key_hash,
                "start_request_hash": start_request_hash,
                "target": payload.get("target"),
                "target_spec": payload.get("target_spec"),
                "bindings": payload.get("bindings"),
                "upstream_snapshot": payload.get("upstream_snapshot"),
                "rule_pack": payload.get("rule_pack"),
                "project_context": payload.get("project_context"),
                "standards": payload.get("standards"),
                "legacy_gate_snapshot": payload.get("legacy_gate_snapshot"),
                "evidence_metadata": payload.get("evidence_metadata"),
                "mode": mode,
                "execution": execution,
                "deployment_mode": deployment_mode,
                "engine_version": payload.get("engine_version"),
                "recalculation_environment_version": payload.get("recalculation_environment_version"),
                "created_at": utc_now(),
            }
            STORE.append(workspace_id, review_id, "review_created", created)
        if execution == "sync" and not engine_terminal:
            _run_review(
                workspace_id,
                review_id,
                payload,
                mode,
                preparation_integrity_reasons,
            )
        elif execution == "async" and not engine_terminal:
            _schedule_async_review(
                workspace_id,
                review_id,
                payload,
                mode,
                preparation_integrity_reasons,
            )
        current = _project(workspace_id, review_id, check_freshness=False)
        response_status = (
            "accepted" if execution == "async"
            else _review_envelope_status(current)
        )
        return _ok(
            status=response_status, review_id=review_id,
            review_status=current.get("review_status"), overall_verdict=current.get("overall_verdict"),
            technical_verdict=current.get("technical_verdict"),
            release_verdict=current.get("release_verdict"),
            deployment_mode=current.get("deployment_mode"),
            shadow_comparison=current.get("shadow_comparison") or {},
            validation_status=current.get("validation_status"),
            validation_complete=bool(current.get("validation_complete")),
            resource_uris=[_review_uri(workspace_id, review_id)],
            blockers=current.get("blockers") or [], warnings=current.get("warnings") or [],
            next_actions=["调用 review_get 查询深度校验进度"] if execution == "async" else ["处理 findings 或导出不可变校验结果"],
        )
    return _write("review_start", args, execute)


def get_review(args: dict[str, Any] | str, review_id: str = "") -> dict[str, Any]:
    if isinstance(args, str):
        workspace_id = args
    else:
        workspace_id = str(args.get("workspace_id") or "")
        review_id = str(args.get("review_id") or "")
    try:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        state = _project(workspace_id, review_id)
    except ValueError as exc:
        code = "review_not_found" if str(exc) in {"review_not_found", "invalid review_id"} else str(exc)
        return _blocked(code, _message(code))
    _resume_async_review_if_needed(workspace_id, state)
    return _ok(
        status=_review_envelope_status(state),
        review=state, review_id=state["review_id"], review_status=state["review_status"],
        validation_status=state["validation_status"],
        validation_complete=state["validation_complete"],
        overall_verdict=state["overall_verdict"],
        technical_verdict=state.get("technical_verdict"),
        release_verdict=state.get("release_verdict"),
        deployment_mode=state.get("deployment_mode"),
        shadow_comparison=state.get("shadow_comparison") or {},
        finding_counts=state["finding_counts"],
        active_finding_counts=state["active_finding_counts"], coverage=state.get("coverage") or {},
        blockers=state.get("blockers") or [], warnings=state.get("warnings") or [],
        resource_uris=[_review_uri(workspace_id, review_id)],
        next_actions=_next_actions(state),
    )
