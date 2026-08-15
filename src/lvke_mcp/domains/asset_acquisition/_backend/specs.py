"""spec 保存、确认与读取；含决策阈值与最高价校验。"""

from __future__ import annotations

import copy
import math
import uuid
from typing import Any


from lvke_mcp.domains.finance.spec import mark_spec_confirmed, validate, validate_for_formal

from .base import (
    _hash,
    _now,
    _same_optional_number,
)

from .evidence import (
    PROCESS_ACCEPTANCE_BASIS_FIELDS,
    RECONSTRUCTION_RECORD_FIELDS,
    _bind_spec_evidence,
    _is_estimate_preview_spec,
    _is_process_acceptance_spec,
    _sanitize_client_evidence_claims,
    process_acceptance_gaps,
)

from .store import (
    _active_idempotency_record,
    _idempotency_record,
    _load,
    _save,
    _state_guard,
)


def _decision_thresholds(spec: dict[str, Any]) -> tuple[dict[str, float | None], str]:
    raw = spec.get("decision_thresholds") or {}
    if not isinstance(raw, dict):
        return {}, "DECISION_THRESHOLDS_INVALID"
    try:
        target = float(raw["target_project_irr"])
        minimum_dscr = (
            float(raw["minimum_dscr"])
            if raw.get("minimum_dscr") is not None else None
        )
    except (KeyError, TypeError, ValueError):
        return {}, "DECISION_THRESHOLDS_INVALID"
    if not math.isfinite(target) or not 0 < target <= 5:
        return {}, "DECISION_THRESHOLDS_INVALID"
    if minimum_dscr is not None and (
        not math.isfinite(minimum_dscr) or minimum_dscr <= 0
    ):
        return {}, "DECISION_THRESHOLDS_INVALID"
    return {
        "target_project_irr": target,
        "minimum_dscr": minimum_dscr,
    }, ""


def _max_price_validation(
    run: dict[str, Any], spec: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    thresholds, threshold_error = _decision_thresholds(spec)
    if threshold_error:
        return False, threshold_error, {"decision_thresholds": spec.get("decision_thresholds")}
    analysis = run.get("max_acquisition_price_analysis") or {}
    if not isinstance(analysis, dict) or not analysis:
        return False, "MAX_PRICE_REQUIRED", {"decision_thresholds": thresholds}
    parameters = analysis.get("parameters") or {}
    if (
        not _same_optional_number(
            parameters.get("target_irr"), thresholds["target_project_irr"],
        )
        or not _same_optional_number(
            parameters.get("min_dscr"), thresholds["minimum_dscr"],
        )
    ):
        return False, "MAX_PRICE_THRESHOLD_MISMATCH", {
            "decision_thresholds": thresholds, "parameters": parameters,
        }
    result = analysis.get("result") or {}
    if not result.get("feasible") or not result.get("converged"):
        return False, "MAX_PRICE_NOT_FEASIBLE", {
            "decision_thresholds": thresholds,
            "feasible": bool(result.get("feasible")),
            "converged": bool(result.get("converged")),
            "reason": result.get("reason"),
        }
    expected_hash = _hash({
        key: value for key, value in analysis.items() if key != "analysis_hash"
    })
    if analysis.get("analysis_hash") != expected_hash:
        return False, "MAX_PRICE_HASH_MISMATCH", {
            "expected_analysis_hash": expected_hash,
            "actual_analysis_hash": analysis.get("analysis_hash"),
        }
    return True, "", {"decision_thresholds": thresholds}


def save_spec(
    workspace_id: str, spec: dict[str, Any], *, idempotency_key: str = "", request_id: str = "",
    trusted_confirmation: bool = False,
) -> dict[str, Any]:
    spec = _sanitize_client_evidence_claims(spec)
    if not trusted_confirmation:
        spec["confirmation_status"] = "candidate"
        spec.pop("confirmed_by", None)
        spec.pop("confirmed_at", None)
    body_hash = _hash(spec)
    evidence_binding = _bind_spec_evidence(
        workspace_id,
        spec,
    )
    evidence_binding_hash = str(evidence_binding.get("binding_hash") or "")
    snapshot_hash = _hash({
        "spec_hash": body_hash,
        "evidence_binding_hash": evidence_binding_hash,
        "evidence_binding_version": evidence_binding.get("binding_version"),
    })
    with _state_guard(workspace_id):
        state = _load(workspace_id)
        scope = f"spec:{idempotency_key}" if idempotency_key else ""
        prior = _active_idempotency_record(state["idempotency"], scope) if scope else None
        if prior:
            if prior["body_hash"] != body_hash:
                return {"ok": False, "error": "IDEMPOTENCY_CONFLICT", "resource_id": prior["spec_id"]}
            return {**state["specs"][prior["spec_id"]], "idempotent_replay": True}
        matching = [
            row for row in state["specs"].values()
            if row.get("spec_hash") == body_hash
            and row.get("evidence_binding_hash") == evidence_binding_hash
        ]
        if matching:
            existing = matching[0]
            if scope:
                state["idempotency"][scope] = _idempotency_record(
                    scope, body_hash, spec_id=existing["spec_id"],
                )
                _save(workspace_id, state)
            return {**existing, "idempotent_replay": True}
        spec_id = f"spec_{uuid.uuid4().hex}"
        revision = max((int(row.get("revision") or 0) for row in state["specs"].values()), default=0) + 1
        row = {
            "ok": True, "spec_id": spec_id, "version": spec.get("version"),
            "spec_hash": body_hash, "revision": revision, "spec": spec,
            "snapshot_hash": snapshot_hash,
            "evidence_binding_version": evidence_binding.get("binding_version"),
            "evidence_binding_hash": evidence_binding_hash,
            "evidence_status": evidence_binding.get("status"),
            "evidence_formal_ok": bool(evidence_binding.get("formal_ok")),
            "evidence_binding": evidence_binding,
            "confirmation_status": spec.get("confirmation_status") or "candidate",
            "request_id": request_id, "created_at": _now(),
        }
        state["specs"][spec_id] = row
        if scope:
            state["idempotency"][scope] = _idempotency_record(
                scope, body_hash, spec_id=spec_id,
            )
        _save(workspace_id, state)
        return row


def confirm_saved_spec(
    workspace_id: str,
    spec_id: str,
    *,
    note: str = "",
    confirmation_scope: str = "project_candidate",
    idempotency_key: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    """Create an immutable confirmed revision from a saved candidate spec."""

    now = _now()
    body_hash = _hash({
        "action": "confirm_spec", "spec_id": spec_id, "note": str(note or ""),
        "confirmation_scope": confirmation_scope,
    })
    with _state_guard(workspace_id):
        state = _load(workspace_id)
        source = state["specs"].get(str(spec_id or ""))
        if not source:
            return {"ok": False, "error": "SPEC_NOT_FOUND"}
        candidate = copy.deepcopy(source.get("spec") or {})
        if confirmation_scope not in {"project_candidate", "process_acceptance"}:
            return {"ok": False, "error": "CONFIRMATION_SCOPE_INVALID"}
        if (
            confirmation_scope == "project_candidate"
            and str(candidate.get("evidence_policy") or "") == "source_reconstructed"
        ):
            return {"ok": False, "error": "PROJECT_FACT_EVIDENCE_MISSING"}
        if confirmation_scope == "process_acceptance":
            candidate.update({
                "confirmation_scope": "process_acceptance",
                "project_fact_certified": False,
                "business_decision_status": "not_selected",
            })
            # P1-018：原先只回一个不透明的错误码，调用方不知道 6 项条件差哪一项。
            # 判定不变（gaps 为空等价于原 _is_process_acceptance_spec），只把缺项列出来。
            gaps = process_acceptance_gaps(candidate)
            if gaps:
                return {
                    "ok": False,
                    "error": "PROCESS_ACCEPTANCE_BASIS_INCOMPLETE",
                    "message": "process_acceptance 确认所需的重建依据不完整：" + "；".join(gaps),
                    "details": {
                        "gaps": gaps,
                        "required_reconstruction_record_fields": list(RECONSTRUCTION_RECORD_FIELDS),
                        "required_process_acceptance_basis_fields": list(PROCESS_ACCEPTANCE_BASIS_FIELDS),
                        "next_action": (
                            "在 acquisition_save_spec 的 spec 里补全 reconstruction_records "
                            "与 process_acceptance_basis，再调用 acquisition_confirm_spec "
                            "并传 confirmation_scope=process_acceptance"
                        ),
                    },
                }
        formal_candidate = mark_spec_confirmed(candidate)
        estimate_preview = _is_estimate_preview_spec(candidate)
        process_acceptance = _is_process_acceptance_spec(candidate)
        schema_ok, schema_errors = (
            validate(formal_candidate)
            if estimate_preview or process_acceptance
            else validate_for_formal(formal_candidate)
        )
        evidence_binding = _bind_spec_evidence(
            workspace_id,
            candidate,
        )
        if not schema_ok:
            return {
                "ok": False,
                "error": "SPEC_VALIDATION_FAILED",
                "details": list(schema_errors),
            }
        if not estimate_preview and not process_acceptance and not evidence_binding.get("formal_ok"):
            return {
                "ok": False,
                "error": "EVIDENCE_REVIEW_REQUIRED",
                "evidence_status": evidence_binding.get("status"),
            }
        scope = f"confirm-spec:{idempotency_key}" if idempotency_key else ""
        prior = _active_idempotency_record(state["idempotency"], scope) if scope else None
        if prior:
            if prior.get("body_hash") != body_hash:
                return {
                    "ok": False, "error": "IDEMPOTENCY_CONFLICT",
                    "resource_id": prior.get("spec_id"),
                }
            existing = state["specs"].get(str(prior.get("spec_id") or ""))
            if not existing:
                raise RuntimeError("spec confirmation idempotency record points to missing spec")
            return {**existing, "idempotent_replay": True}
        existing = next((
            row for row in state["specs"].values()
            if row.get("parent_spec_id") == spec_id
            and row.get("confirmation_status") == "confirmed"
            and str((row.get("confirmation") or {}).get("note") or "") == str(note or "")
        ), None)
        if existing:
            if scope:
                state["idempotency"][scope] = _idempotency_record(
                    scope, body_hash, spec_id=existing["spec_id"],
                )
                _save(workspace_id, state)
            return {**existing, "idempotent_replay": True}
        confirmed = formal_candidate
        if estimate_preview:
            confirmed["confirmation_scope"] = "estimate_preview"
        elif process_acceptance:
            confirmed["confirmation_scope"] = "process_acceptance"
            confirmed["project_fact_certified"] = False
            confirmed["business_decision_status"] = "not_selected"
        evidence_binding = _bind_spec_evidence(
            workspace_id,
            confirmed,
        )
        spec_hash = _hash(confirmed)
        snapshot_hash = _hash({
            "spec_hash": spec_hash,
            "evidence_binding_hash": evidence_binding.get("binding_hash"),
            "evidence_binding_version": evidence_binding.get("binding_version"),
        })
        confirmed_id = f"spec_{uuid.uuid4().hex}"
        revision = max(
            (int(row.get("revision") or 0) for row in state["specs"].values()),
            default=0,
        ) + 1
        row = {
            "ok": True,
            "spec_id": confirmed_id,
            "parent_spec_id": spec_id,
            "version": confirmed.get("version"),
            "spec_hash": spec_hash,
            "revision": revision,
            "spec": confirmed,
            "snapshot_hash": snapshot_hash,
            "evidence_binding_version": evidence_binding.get("binding_version"),
            "evidence_binding_hash": evidence_binding.get("binding_hash"),
            "evidence_status": evidence_binding.get("status"),
            "evidence_formal_ok": bool(evidence_binding.get("formal_ok")),
            "evidence_binding": evidence_binding,
            "confirmation_status": "confirmed",
            "confirmation_scope": (
                "estimate_preview" if estimate_preview else (
                    "process_acceptance" if process_acceptance else "formal_input"
                )
            ),
            "confirmation": {
                "note": str(note or ""),
                "confirmed_at": now, "request_id": request_id,
            },
            "request_id": request_id,
            "created_at": now,
        }
        state["specs"][confirmed_id] = row
        if scope:
            state["idempotency"][scope] = _idempotency_record(
                scope, body_hash, spec_id=confirmed_id,
            )
        _save(workspace_id, state)
        return row


def list_specs(
    workspace_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return saved acquisition specs newest-first for workbench recovery."""

    rows = [
        copy.deepcopy(row)
        for row in _load(workspace_id)["specs"].values()
    ]
    rows.sort(
        key=lambda row: (int(row.get("revision") or 0), str(row.get("created_at") or "")),
        reverse=True,
    )
    return rows[: max(1, min(int(limit or 50), 10_000))]


def get_spec(
    workspace_id: str,
    spec_id: str,
) -> dict[str, Any]:
    return copy.deepcopy(
        _load(workspace_id)["specs"].get(spec_id) or {}
    )
