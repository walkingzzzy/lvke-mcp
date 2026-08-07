"""run 生命周期：创建、入队、执行与读取列举。"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from typing import Any


from lvke_mcp.domains.asset_acquisition.model import AcquisitionModelError, run_acquisition_model
from lvke_mcp.domains.finance.spec import LATEST_SPEC_VERSION, validate, validate_for_formal

from .base import (
    _LOG,
    _RUN_EXECUTION_FAILURE_MESSAGE,
    _RUN_VALIDATION_FAILURE_MESSAGE,
    _SOURCE_EVIDENCE_FAILURE_MESSAGE,
    _hash,
    _now,
)

from .evidence import (
    _bind_spec_evidence,
    _evidence_blocking_issues,
    _evidence_error_strings,
    _formal_assessment,
    _is_estimate_preview_spec,
    _is_process_acceptance_spec,
)

from .specs import (
    save_spec,
)

from .store import (
    _active_idempotency_record,
    _history_event,
    _idempotency_record,
    _load,
    _migration_binding,
    _save,
    _state_guard,
)


def _is_selected_scenario(spec: Mapping[str, Any], scenario_id: str) -> bool:
    """Only run the scenario whose assumptions are already selected in the Spec."""

    selected = str(spec.get("selected_scenario_id") or "base").strip()
    requested = str(scenario_id or "base").strip()
    return bool(selected and requested and requested == selected)


def create_run(
    workspace_id: str, spec: dict[str, Any], *, discount_rate: float = 0.08,
    scenario_id: str = "base", idempotency_key: str = "", request_id: str = "",
    scenario_change_ledger: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not _is_selected_scenario(spec, scenario_id):
        return {"ok": False, "error": "SCENARIO_NOT_FOUND"}
    estimate_preview = _is_estimate_preview_spec(spec)
    process_acceptance = _is_process_acceptance_spec(spec)
    schema_ok, schema_errors = (
        validate(spec) if estimate_preview or process_acceptance else validate_for_formal(spec)
    )
    evidence_binding = _bind_spec_evidence(
        workspace_id,
        spec,
    )
    if not schema_ok:
        return {"ok": False, "error": "SPEC_VALIDATION_FAILED", "details": list(schema_errors)}
    if not estimate_preview and not process_acceptance and not evidence_binding.get("formal_ok"):
        return {
            "ok": False,
            "error": "EVIDENCE_REVIEW_REQUIRED",
            "evidence_status": evidence_binding.get("status"),
        }
    request_id = request_id or f"req_{uuid.uuid4().hex}"
    saved_spec = save_spec(
        workspace_id, spec, request_id=request_id, trusted_confirmation=True,
    )
    if not saved_spec.get("ok"):
        return saved_spec
    spec = copy.deepcopy(saved_spec.get("spec") or {})
    evidence_binding = copy.deepcopy(saved_spec.get("evidence_binding") or {})
    body = {"spec": spec, "discount_rate": discount_rate, "scenario_id": scenario_id}
    body_hash = _hash(body)
    with _state_guard(workspace_id):
        state = _load(workspace_id)
        scope = f"run:{idempotency_key}" if idempotency_key else ""
        prior = _active_idempotency_record(state["idempotency"], scope) if scope else None
        if prior:
            if prior["body_hash"] != body_hash:
                return {"ok": False, "error": "IDEMPOTENCY_CONFLICT", "resource_id": prior["run_id"]}
            existing = state["runs"].get(prior["run_id"])
            if not existing:
                raise RuntimeError("run idempotency record points to a missing run")
            return {**existing, "idempotent_replay": True}
        result = run_acquisition_model(spec, discount_rate=discount_rate, scenario_id=scenario_id)
        run_id = f"acqrun_{uuid.uuid4().hex}"
        schema_formal_ok, schema_errors = validate_for_formal(spec)
        formal_ok, formal_errors, evidence_binding = _formal_assessment(
            workspace_id,
            spec,
            evidence_binding=evidence_binding,
        )
        created_at = _now()
        issues = [
            {"code": "SPEC_VALIDATION_FAILED", "blocking": True, "status": "open", "detail": item, "created_at": created_at}
            for item in schema_errors
        ]
        issues.extend(_evidence_blocking_issues(evidence_binding, created_at=created_at))
        scenario_ledger = list(scenario_change_ledger or [])
        invalid_scenario_sources = [
            index for index, item in enumerate(scenario_ledger)
            if not isinstance(item, Mapping)
            or not str(item.get("source") or "").strip()
        ]
        if invalid_scenario_sources:
            issues.append({
                "code": "SCENARIO_SOURCE_REQUIRED", "blocking": True, "status": "open",
                "detail": "情景调整缺少可追溯来源", "rows": invalid_scenario_sources,
                "created_at": created_at,
            })
        row = {
            "ok": True, "available": True, "run_id": run_id, "workspace_id": workspace_id,
            "status": "succeeded",
            "lifecycle_status": "validated" if not issues else "validation_failed",
            "delivery_mode": (
                "estimate_preview" if estimate_preview else (
                    "process_acceptance" if process_acceptance else "formal_candidate"
                )
            ),
            "model_version": result["model_version"],
            "spec_version": LATEST_SPEC_VERSION, "spec_hash": _hash(spec), "input_hash": body_hash,
            "spec_id": saved_spec.get("spec_id"),
            "spec_snapshot_hash": saved_spec.get("snapshot_hash"),
            "evidence_binding_version": evidence_binding.get("binding_version"),
            "evidence_binding_hash": evidence_binding.get("binding_hash"),
            "evidence_status": evidence_binding.get("status"),
            "evidence_formal_ok": bool(evidence_binding.get("formal_ok")),
            "evidence_binding": evidence_binding,
            "scenario_id": scenario_id, "scenario_change_ledger": scenario_ledger,
            "discount_rate": discount_rate, "result": result, "consistency_ok": True,
            "validation_status": "passed" if not issues else "failed",
            "formal_spec_valid": bool(schema_formal_ok and formal_ok),
            "process_acceptance_valid": bool(process_acceptance and schema_ok),
            "formal_spec_errors": formal_errors, "request_id": request_id,
            "created_at": created_at,
            "evidence_policy": str(spec.get("evidence_policy") or "formal_evidence"),
            "project_fact_certified": bool(spec.get(
                "project_fact_certified",
                str(spec.get("evidence_policy") or "") != "source_reconstructed",
            )),
            "reconstruction_records": copy.deepcopy(spec.get("reconstruction_records") or []),
            "reconstructed_source_ids": copy.deepcopy(spec.get("reconstructed_source_ids") or []),
            "unresolved_inputs": copy.deepcopy(spec.get("unresolved_inputs") or []),
            "release_limitations": copy.deepcopy(spec.get("release_limitations") or []),
            "business_decision_status": str(spec.get("business_decision_status") or "not_selected"),
            **_migration_binding(spec),
            "issues": issues,
            "state_history": [
                _history_event("validated_spec", request_id=request_id),
                _history_event("running", request_id=request_id),
                _history_event("calculated", request_id=request_id),
                _history_event("internally_consistent", request_id=request_id),
                _history_event(
                    "validated" if not issues else "validation_failed",
                    request_id=request_id,
                ),
            ],
        }
        state["runs"][run_id] = row
        if scope:
            state["idempotency"][scope] = _idempotency_record(
                scope, body_hash, run_id=run_id,
            )
        _save(workspace_id, state)
    return row


def enqueue_run(
    workspace_id: str, spec: dict[str, Any], *, discount_rate: float = 0.08,
    scenario_id: str = "base", idempotency_key: str = "", request_id: str = "",
    scenario_change_ledger: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist a pollable queued run before any model calculation starts."""

    if not _is_selected_scenario(spec, scenario_id):
        return {"ok": False, "error": "SCENARIO_NOT_FOUND"}
    schema_ok, schema_errors = validate_for_formal(spec)
    evidence_binding = _bind_spec_evidence(
        workspace_id,
        spec,
    )
    if not schema_ok:
        return {"ok": False, "error": "SPEC_VALIDATION_FAILED", "details": list(schema_errors)}
    if not evidence_binding.get("formal_ok"):
        return {
            "ok": False,
            "error": "EVIDENCE_REVIEW_REQUIRED",
            "evidence_status": evidence_binding.get("status"),
        }
    request_id = request_id or f"req_{uuid.uuid4().hex}"
    saved_spec = save_spec(
        workspace_id, spec, request_id=request_id, trusted_confirmation=True,
    )
    if not saved_spec.get("ok"):
        return saved_spec
    spec = copy.deepcopy(saved_spec.get("spec") or {})
    evidence_binding = copy.deepcopy(saved_spec.get("evidence_binding") or {})
    body = {"spec": spec, "discount_rate": discount_rate, "scenario_id": scenario_id}
    body_hash = _hash(body)
    schema_formal_ok, _schema_errors = validate_for_formal(spec)
    formal_ok, formal_errors, evidence_binding = _formal_assessment(
        workspace_id,
        spec,
        evidence_binding=evidence_binding,
    )
    with _state_guard(workspace_id):
        state = _load(workspace_id)
        scope = f"run:{idempotency_key}" if idempotency_key else ""
        prior = _active_idempotency_record(state["idempotency"], scope) if scope else None
        if prior:
            if prior.get("body_hash") != body_hash:
                return {"ok": False, "error": "IDEMPOTENCY_CONFLICT", "resource_id": prior.get("run_id", "")}
            existing = state["runs"].get(prior.get("run_id"))
            if not existing:
                raise RuntimeError("run idempotency record points to a missing run")
            return {**existing, "idempotent_replay": True}
        run_id = f"acqrun_{uuid.uuid4().hex}"
        created_at = _now()
        row = {
            "ok": True, "available": False, "run_id": run_id, "workspace_id": workspace_id,
            "status": "queued", "progress": 0, "lifecycle_status": "validated_spec",
            "spec_version": LATEST_SPEC_VERSION, "spec_hash": _hash(spec), "input_hash": body_hash,
            "spec_id": saved_spec.get("spec_id"), "scenario_id": scenario_id,
            "spec_snapshot_hash": saved_spec.get("snapshot_hash"),
            "evidence_binding_version": evidence_binding.get("binding_version"),
            "evidence_binding_hash": evidence_binding.get("binding_hash"),
            "evidence_status": evidence_binding.get("status"),
            "evidence_formal_ok": bool(evidence_binding.get("formal_ok")),
            "evidence_binding": evidence_binding,
            "scenario_change_ledger": list(scenario_change_ledger or []), "discount_rate": discount_rate,
            "validation_status": "pending",
            "formal_spec_valid": bool(schema_formal_ok and formal_ok),
            "formal_spec_errors": formal_errors,
            "request_id": request_id, "created_at": created_at, "updated_at": created_at,
            "issues": [],
            "evidence_policy": str(spec.get("evidence_policy") or "formal_evidence"),
            "project_fact_certified": bool(spec.get("project_fact_certified", str(spec.get("evidence_policy") or "") != "source_reconstructed")),
            "reconstruction_records": copy.deepcopy(spec.get("reconstruction_records") or []),
            "reconstructed_source_ids": copy.deepcopy(spec.get("reconstructed_source_ids") or []),
            "unresolved_inputs": copy.deepcopy(spec.get("unresolved_inputs") or []),
            "release_limitations": copy.deepcopy(spec.get("release_limitations") or []),
            "business_decision_status": str(spec.get("business_decision_status") or "not_selected"),
            **_migration_binding(spec),
            "state_history": [
                _history_event("validated_spec", request_id=request_id),
                _history_event("queued", request_id=request_id),
            ],
        }
        state["runs"][run_id] = row
        if scope:
            state["idempotency"][scope] = _idempotency_record(
                scope, body_hash, run_id=run_id,
            )
        _save(workspace_id, state)
        return row


def execute_queued_run(
    workspace_id: str,
    run_id: str,
) -> None:
    """Execute one durable queued run and converge it to succeeded/failed."""

    with _state_guard(workspace_id):
        state = _load(workspace_id)
        run = state["runs"].get(run_id)
        if not run or run.get("status") in {"succeeded", "failed", "cancelled"}:
            return
        spec_row = state["specs"].get(str(run.get("spec_id") or "")) or {}
        spec = spec_row.get("spec")
        if not isinstance(spec, dict):
            run.update({
                "status": "failed", "progress": 100, "updated_at": _now(),
                "error": {"code": "SPEC_VALIDATION_FAILED", "message": "spec snapshot missing", "retryable": False},
            })
            _save(workspace_id, state)
            return
        run.update({"status": "running", "progress": 10, "lifecycle_status": "running", "updated_at": _now()})
        run.setdefault("state_history", []).append(_history_event(
            "running", request_id=str(run.get("request_id") or ""),
        ))
        discount_rate = float(run.get("discount_rate") or 0.08)
        scenario_id = str(run.get("scenario_id") or "base")
        _save(workspace_id, state)
    try:
        current_evidence = _bind_spec_evidence(
            workspace_id,
            spec,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "asset acquisition evidence binding failed; error_type=%s",
            type(exc).__name__,
        )
        current_evidence = {
            "formal_ok": False,
            "status": "invalid",
            "binding_version": "",
            "binding_hash": "",
            "bindings": [],
            "missing": [],
            "pending": [],
            "invalid": [{
                "source_path": "$",
                "code": "SOURCE_EVIDENCE_STATE_INVALID",
                "message": _SOURCE_EVIDENCE_FAILURE_MESSAGE,
            }],
        }
    evidence_hash_matches = bool(
        current_evidence.get("binding_hash") == run.get("evidence_binding_hash")
        and current_evidence.get("binding_version") == run.get("evidence_binding_version")
    )
    evidence_snapshot_matches = bool(evidence_hash_matches and current_evidence.get("formal_ok"))
    try:
        result = run_acquisition_model(spec, discount_rate=discount_rate, scenario_id=scenario_id)
    except Exception as exc:  # noqa: BLE001
        code = "SPEC_VALIDATION_FAILED" if isinstance(exc, AcquisitionModelError) else "RUN_FAILED"
        safe_message = (
            _RUN_VALIDATION_FAILURE_MESSAGE
            if code == "SPEC_VALIDATION_FAILED"
            else _RUN_EXECUTION_FAILURE_MESSAGE
        )
        _LOG.warning(
            "asset acquisition run failed; run_id=%s error_type=%s code=%s",
            run_id,
            type(exc).__name__,
            code,
        )
        with _state_guard(workspace_id):
            state = _load(workspace_id)
            run = state["runs"].get(run_id)
            if run and run.get("status") != "cancelled":
                run.update({
                    "status": "failed", "progress": 100, "lifecycle_status": "failed", "updated_at": _now(),
                    "error": {"code": code, "message": safe_message, "retryable": False},
                })
                run.setdefault("state_history", []).append(_history_event(
                    "failed", request_id=str(run.get("request_id") or ""), error_code=code,
                ))
                _save(workspace_id, state)
        return

    with _state_guard(workspace_id):
        state = _load(workspace_id)
        run = state["runs"].get(run_id)
        if not run or run.get("status") == "cancelled":
            return
        created_at = str(run.get("created_at") or _now())
        schema_formal_ok, schema_errors = validate_for_formal(spec)
        formal_errors = [*schema_errors, *_evidence_error_strings(current_evidence)]
        if not evidence_hash_matches:
            formal_errors.append(
                "finance_spec.v3 证据绑定快照已变化或不再满足正式复核条件"
            )
        issues = [
            {"code": "SPEC_VALIDATION_FAILED", "blocking": True, "status": "open", "detail": item, "created_at": created_at}
            for item in schema_errors
        ]
        issues.extend(_evidence_blocking_issues(current_evidence, created_at=created_at))
        if not evidence_hash_matches:
            issues.append({
                "code": "EVIDENCE_BINDING_STALE",
                "blocking": True,
                "status": "open",
                "detail": (
                    "运行排队后证据绑定状态发生变化；必须保存新Spec修订并重新运行。"
                    f" snapshot={run.get('evidence_binding_hash')} current={current_evidence.get('binding_hash')}"
                ),
                "created_at": created_at,
            })
        scenario_ledger = list(run.get("scenario_change_ledger") or [])
        invalid_scenario_sources = [
            index for index, item in enumerate(scenario_ledger)
            if not isinstance(item, Mapping)
            or not str(item.get("source") or "").strip()
        ]
        if invalid_scenario_sources:
            issues.append({
                "code": "SCENARIO_SOURCE_REQUIRED", "blocking": True, "status": "open",
                "detail": "情景调整缺少可追溯来源", "rows": invalid_scenario_sources,
                "created_at": created_at,
            })
        run.update({
            "available": True, "status": "succeeded", "progress": 100,
            "lifecycle_status": "validated" if not issues else "validation_failed",
            "model_version": result["model_version"],
            "result": result, "consistency_ok": True, "issues": issues, "updated_at": _now(),
            "validation_status": "passed" if not issues else "failed",
            "formal_spec_valid": bool(schema_formal_ok and evidence_snapshot_matches),
            "formal_spec_errors": formal_errors,
            "evidence_revalidation": {
                "checked_at": _now(),
                "matches_snapshot": evidence_hash_matches,
                "binding_version": current_evidence.get("binding_version"),
                "binding_hash": current_evidence.get("binding_hash"),
                "status": current_evidence.get("status"),
                "formal_ok": bool(current_evidence.get("formal_ok")),
            },
        })
        run.setdefault("state_history", []).extend([
            _history_event("calculated", request_id=str(run.get("request_id") or "")),
            _history_event("internally_consistent", request_id=str(run.get("request_id") or "")),
            _history_event(
                "validated" if not issues else "validation_failed",
                request_id=str(run.get("request_id") or ""),
            ),
        ])
        _save(workspace_id, state)


def get_run(
    workspace_id: str,
    run_id: str,
) -> dict[str, Any]:
    return dict(
        _load(workspace_id)["runs"].get(run_id) or {}
    )


def list_runs(
    workspace_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return lightweight acquisition run summaries newest-first."""

    rows: list[dict[str, Any]] = []
    summary_keys = (
        "run_id", "workspace_id", "status", "lifecycle_status",
        "model_version", "spec_version", "spec_hash", "input_hash",
        "spec_id", "scenario_id", "discount_rate", "consistency_ok",
        "spec_snapshot_hash", "evidence_binding_version", "evidence_binding_hash",
        "evidence_status", "evidence_formal_ok", "evidence_revalidation",
        "validation_status", "formal_spec_valid", "formal_spec_errors",
        "request_id", "created_at", "issues",
    )
    for run in _load(workspace_id)["runs"].values():
        indicators = (run.get("result") or {}).get("indicators") or {}
        row = {key: copy.deepcopy(run.get(key)) for key in summary_keys}
        row.update({
            "invest_type": "asset_acquisition",
            "industry": "资产收购",
            "available": bool(run.get("available")),
            "started_at": run.get("created_at"),
            "finished_at": run.get("finished_at") or run.get("created_at"),
            "indicators": {
                key: copy.deepcopy(indicators.get(key))
                for key in ("project_irr_pct", "equity_irr_pct", "npv_wan", "minimum_dscr")
            },
        })
        rows.append(row)
    rows.sort(
        key=lambda row: (str(row.get("created_at") or ""), str(row.get("run_id") or "")),
        reverse=True,
    )
    return rows[: max(1, min(int(limit or 50), 100))]
