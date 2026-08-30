"""项目事件投影、新鲜度判定与审查投影。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    validate_object_formal_lineage,
)
from lvke_mcp.runtime.storage import require_safe_id, sha256_json, utc_now
from lvke_mcp.runtime.evidence_qualification import (
    SIM_A_FORMAL,
    declared_evidence_policy,
    project_fact_may_be_certified,
)
from lvke_mcp.servers.lvke_deliverable_review import rules
from lvke_mcp.servers.lvke_deliverable_review.contracts import SEVERITY_ORDER, finding_blocks, normalize_target, verdict_for
from lvke_mcp.servers.lvke_deliverable_review.store import STORE

from .base import (
    REPO_ROOT,
    _classify_retest_operations,
    _shadow_comparison,
)

from .target_resolve import (
    _binding_snapshot,
    _resolve_target,
)


def _project_events(workspace_id: str, review_id: str) -> dict[str, Any]:
    events = STORE.events(workspace_id, review_id)
    if not events:
        raise ValueError("review_not_found")
    chain_ok, chain_reasons = STORE.verify_event_chain(workspace_id, review_id)
    retest_operations = _classify_retest_operations(events, review_id)
    completed_retest_operations = retest_operations["completed"]
    retest_terminal_events = [
        event
        for event in events
        if (
            event.get("event_type") == "retest_failed"
            and str((event.get("payload") or {}).get("operation_id") or "")
            in retest_operations["failed"]
        ) or (
            event.get("event_type") == "retest_completed"
            and (event.get("payload") or {}).get("side") == "parent"
            and str((event.get("payload") or {}).get("operation_id") or "")
            in completed_retest_operations
        )
    ]
    latest_retest_terminal = max(
        retest_terminal_events,
        key=lambda row: int(row.get("sequence") or 0),
        default=None,
    )
    active_failed_retest_operations: dict[str, dict[str, Any]] = {}
    if latest_retest_terminal is not None and latest_retest_terminal.get("event_type") == "retest_failed":
        latest_operation_id = str(
            (latest_retest_terminal.get("payload") or {}).get("operation_id") or ""
        )
        active_failed_retest_operations[latest_operation_id] = deepcopy(
            retest_operations["failed"][latest_operation_id]
        )
    state: dict[str, Any] = {
        "schema_version": "deliverable_review.v1", "workspace_id": workspace_id,
        "review_id": review_id, "review_status": "created", "overall_verdict": "incomplete",
        "technical_verdict": "incomplete", "release_verdict": "incomplete",
        "target": {}, "bindings": {}, "rule_pack": {}, "standards": {}, "project_context": {}, "findings": [],
        "incomplete_reasons": [], "coverage": {}, "exports": [], "retests": [],
        "invalidated": False,
        "deployment_mode": "enforced", "legacy_gate_snapshot": {},
        "automated_gate_verdict": "unknown", "shadow_comparison": {},
        "pending_retest_operation_ids": sorted(retest_operations["pending"]),
        "failed_retest_operations": deepcopy(retest_operations["failed"]),
        "active_failed_retest_operations": active_failed_retest_operations,
        "invalid_retest_operations": deepcopy(retest_operations["invalid"]),
    }
    findings: dict[str, dict[str, Any]] = {}
    disposition_seen = False
    engine_completed = False
    engine_failed = False
    last_review_basis_change_sequence = 0
    for event in events:
        event_type = str(event.get("event_type") or "")
        payload = deepcopy(event.get("payload") or {})
        audit = {
            "sequence": event.get("sequence"), "event_type": event_type,

            "event_hash": event.get("event_hash"),
        }
        if event_type == "review_created":
            state.update({key: deepcopy(payload.get(key)) for key in (
                "review_preparation_id", "preparation_basis_hash",
                "preparation_content_hash", "target", "target_spec",
                "bindings", "upstream_snapshot", "rule_pack", "standards", "mode", "execution",
                "review_profile", "review_mode",
                "project_context",
                "deployment_mode", "legacy_gate_snapshot",
                "evidence_metadata",
                "engine_version", "recalculation_environment_version", "created_at",
            )})
            state["deployment_mode"] = str(payload.get("deployment_mode") or "enforced")
            state["legacy_gate_snapshot"] = deepcopy(payload.get("legacy_gate_snapshot") or {})
        elif event_type == "review_running":
            state["review_status"] = "running"
            state["started_at"] = payload.get("started_at")
        elif event_type == "review_completed":
            last_review_basis_change_sequence = max(last_review_basis_change_sequence, int(event.get("sequence") or 0))
            engine_completed = True
            state["review_status"] = "findings_ready"
            state["completed_at"] = payload.get("completed_at")
            state["incomplete_reasons"] = list(payload.get("incomplete_reasons") or [])
            state["coverage"] = deepcopy(payload.get("coverage") or {})
            completed_findings = list(payload.get("findings") or [])
            completed_verdict = str(payload.get("overall_verdict") or "incomplete")
            state["automated_gate_verdict"] = (
                "pass"
                if completed_verdict in {"pass", "conditional_pass"}
                and not state["incomplete_reasons"]
                and not any(finding_blocks(row) for row in completed_findings)
                else "fail"
            )
            for raw in completed_findings:
                row = deepcopy(raw)
                row["history"] = [{**audit, "status": row.get("status", "open")}]
                findings[str(row.get("finding_id") or "")] = row
        elif event_type == "review_failed":
            last_review_basis_change_sequence = max(last_review_basis_change_sequence, int(event.get("sequence") or 0))
            engine_failed = True
            engine_completed = True
            state["review_status"] = "findings_ready"
            state["completed_at"] = payload.get("completed_at")
            reason = str(payload.get("incomplete_reason") or "review_engine_failed")
            state["incomplete_reasons"] = sorted(set([*state["incomplete_reasons"], reason]))
            state["automated_gate_verdict"] = "fail"
        elif event_type == "finding_disposition_recorded":
            last_review_basis_change_sequence = max(last_review_basis_change_sequence, int(event.get("sequence") or 0))
            finding_id = str(payload.get("finding_id") or "")
            row = findings.get(finding_id)
            if row is not None:
                disposition_seen = True
                row["status"] = payload.get("new_status")
                for key in (
                    "disposition", "note", "closure_basis", "before_value", "after_value",
                    "remediation_evidence", "false_positive_reason", "waiver_scope",
                    "waiver_expires_at", "waiver_invalidation_conditions",
                    "waiver_impact", "waiver_compensating_controls",
                    "waiver_responsible_party",
                ):
                    if key in payload:
                        row[key] = deepcopy(payload.get(key))
                row.setdefault("history", []).append({**audit, **payload})
        elif event_type == "finding_retested":
            operation_id = str(payload.get("operation_id") or "")
            if operation_id and operation_id not in completed_retest_operations:
                continue
            last_review_basis_change_sequence = max(last_review_basis_change_sequence, int(event.get("sequence") or 0))
            finding_id = str(payload.get("finding_id") or "")
            row = findings.get(finding_id)
            if row is not None:
                disposition_seen = True
                row["status"] = str(payload.get("new_status") or row.get("status") or "open")
                row["retest_result"] = deepcopy(payload)
                row.setdefault("history", []).append({**audit, **payload})
        elif event_type == "retest_linked":
            operation_id = str(payload.get("operation_id") or "")
            if operation_id and operation_id not in completed_retest_operations:
                continue
            last_review_basis_change_sequence = max(last_review_basis_change_sequence, int(event.get("sequence") or 0))
            state["retests"].append({**payload, **audit})
        elif event_type == "retest_completed":
            operation_id = str(payload.get("operation_id") or "")
            if operation_id in completed_retest_operations:
                last_review_basis_change_sequence = max(
                    last_review_basis_change_sequence,
                    int(event.get("sequence") or 0),
                )
        elif event_type == "retest_failed":
            last_review_basis_change_sequence = max(
                last_review_basis_change_sequence,
                int(event.get("sequence") or 0),
            )
        elif event_type == "review_exported":
            state["exports"].append({**payload, **audit})
        elif event_type == "suite_assessment_submitted":
            state.setdefault("suite_assessments", {})[str(payload.get("dimension") or "")] = {
                **payload,
                **audit,
            }
            for raw in payload.get("findings") or []:
                row = deepcopy(raw)
                row["history"] = [{**audit, "status": row.get("status", "open")}]
                findings[str(row.get("finding_id") or "")] = row
        elif event_type == "suite_dimension_confirmed":
            state.setdefault("suite_dimension_confirmations", {})[
                str(payload.get("dimension") or "")
            ] = {**payload, **audit}
        elif event_type == "suite_finalized":
            state["schema_version"] = "ReviewDossier.v2"
            state["review_dossier_id"] = str(payload.get("dossier_id") or "")
            state["review_dossier_hash"] = str(payload.get("dossier_hash") or "")
            state["dimension_results"] = deepcopy(payload.get("dimension_results") or [])
            state["suite_overall_verdict"] = str(payload.get("overall_verdict") or "incomplete")
            state["suite_hard_gate_blockers"] = list(payload.get("hard_gate_blockers") or [])
            state["formal_suite_review_complete"] = bool(payload.get("formal_suite_review_complete"))
        elif event_type == "review_invalidated":
            state["invalidated"] = True
            state["invalidation"] = {**payload, **audit}

    ordered_findings = sorted(
        findings.values(), key=lambda row: (
            SEVERITY_ORDER.get(str(row.get("severity") or ""), 9), str(row.get("finding_id") or ""),
        ),
    )
    state["findings"] = ordered_findings
    if not chain_ok:
        state["incomplete_reasons"] = sorted(set([
            *state["incomplete_reasons"], *[f"event_chain:{reason}" for reason in chain_reasons],
        ]))
        state["invalidated"] = True
    state["incomplete_reasons"] = sorted(set([
        *state["incomplete_reasons"],
        *[
            f"retest_operation_failed:{operation_id}:{payload.get('code') or 'unknown'}"
            for operation_id, payload in active_failed_retest_operations.items()
        ],
        *[
            f"retest_operation_invalid:{operation_id}:{reason}"
            for operation_id, reason in retest_operations["invalid"].items()
        ],
    ]))
    state["retest_in_progress"] = bool(state["pending_retest_operation_ids"])

    technical_verdict = verdict_for(ordered_findings, state["incomplete_reasons"])
    if not engine_completed or state["pending_retest_operation_ids"]:
        technical_verdict = "incomplete"
    active_blockers = [row for row in ordered_findings if finding_blocks(row)]
    state["active_blocking_finding_ids"] = [str(row.get("finding_id") or "") for row in active_blockers]
    state["pending_quality_rule_ids"] = sorted(
        str(row.get("rule_id") or "")
        for row in ordered_findings
        if row.get("manual_review_required") is True
        and row.get("status") != "resolved"
    )
    if state["invalidated"]:
        technical_verdict = "incomplete"
        state["review_status"] = "invalidated"
        state["validation_status"] = "invalidated"
    elif state["pending_retest_operation_ids"]:
        state["review_status"] = "retest_required"
        state["validation_status"] = "incomplete"
    elif state["active_failed_retest_operations"] or state["invalid_retest_operations"]:
        state["review_status"] = "retest_required"
        state["validation_status"] = "failed"
    elif engine_completed:
        if active_blockers:
            state["review_status"] = "remediation_in_progress" if disposition_seen else "findings_ready"
            state["validation_status"] = "failed"
        elif state["incomplete_reasons"]:
            state["review_status"] = "findings_ready"
            state["validation_status"] = "incomplete"
        else:
            state["review_status"] = "validated"
            state["validation_status"] = (
                "passed" if technical_verdict == "pass" else "conditional"
            )
    elif engine_failed:
        state["review_status"] = "findings_ready"
        state["validation_status"] = "failed"
    else:
        state["validation_status"] = "pending"
    validation_complete = bool(
        engine_completed
        and not engine_failed
        and not state["pending_retest_operation_ids"]
        and chain_ok
    )
    if state.get("deployment_mode") == "shadow":
        state["shadow_comparison"] = _shadow_comparison(
            state,
            validation_complete,
        )
        # Technical verification is a deterministic diagnostic projection.
        deterministic_blocking_ids = [
            str(row.get("finding_id") or "")
            for row in active_blockers
            if row.get("manual_review_required") is not True
        ]
        state["technical_verification_verdict"] = (
            "technical_pass" if not deterministic_blocking_ids and engine_completed
            else "technical_fail"
        )
        state["technical_verification_blockers"] = deterministic_blocking_ids
    state["generated"] = True
    state["validation_complete"] = validation_complete
    state["validated"] = bool(validation_complete)
    state["event_chain_hash"] = STORE.event_chain_hash(workspace_id, review_id)
    state["event_chain_valid"] = chain_ok
    state["event_count"] = len(events)
    state["finding_counts"] = {
        severity: sum(1 for row in ordered_findings if row.get("severity") == severity)
        for severity in ("P0", "P1", "P2", "P3")
    }
    state["active_finding_counts"] = {
        severity: sum(
            1 for row in ordered_findings
            if row.get("severity") == severity
            and row.get("status") not in {"resolved", "rejected", "superseded", "waived"}
        ) for severity in ("P0", "P1", "P2", "P3")
    }
    blockers: list[str] = []
    if state["invalidated"]:
        blockers.append("review_invalidated")
    blockers.extend(str(item) for item in state["incomplete_reasons"])
    blockers.extend(f"blocking_finding:{item}" for item in state["active_blocking_finding_ids"])
    blockers.extend(
        f"retest_in_progress:{operation_id}"
        for operation_id in state["pending_retest_operation_ids"]
    )
    blockers.extend(str(item) for item in state.get("suite_hard_gate_blockers") or [])
    if str((state.get("target") or {}).get("target_type") or "") == "review_package":
        if not state.get("review_dossier_id"):
            blockers.append("review_suite_not_finalized")
        elif not state.get("formal_suite_review_complete"):
            blockers.append("formal_suite_review_incomplete")
        if str(state.get("review_mode") or "") == "external":
            blockers.append("external_review_release_forbidden")
    if state.get("deployment_mode") == "shadow":
        blockers.append("shadow_mode_release_forbidden")
    evidence_track = str((state.get("project_context") or {}).get("evidence_track") or "real")
    review_purpose = str(
        (state.get("project_context") or {}).get("review_purpose")
        or (state.get("project_context") or {}).get("release_scope")
        or ("project_delivery" if evidence_track in {"real", "sim_a_formal"} else "process_acceptance")
    )
    evidence_metadata = (
        state.get("evidence_metadata")
        if isinstance(state.get("evidence_metadata"), dict)
        else {}
    )
    release_evidence_policy = declared_evidence_policy(
        evidence_metadata,
        default=evidence_track,
    )
    project_fact_certified = project_fact_may_be_certified(
        release_evidence_policy,
        own_qualification_passed=True,
        parents=[evidence_metadata],
    )
    if evidence_track == SIM_A_FORMAL:
        release_evidence_policy = SIM_A_FORMAL
        try:
            canonical_lineage = validate_object_formal_lineage(
                workspace_id,
                evidence_metadata,
            )
        except FormalLineageError as exc:
            project_fact_certified = False
            blockers.append(f"formal_lineage:{exc.code}")
        else:
            project_fact_certified = True
            state["evidence_metadata"] = {
                **evidence_metadata,
                **canonical_lineage,
            }
    if review_purpose == "project_delivery" and release_evidence_policy not in {"formal_evidence", "sim_a_formal"}:
        blockers.append({
            "controlled_assumption": "controlled_assumption_release_forbidden",
            "source_reconstructed": "source_reconstructed_release_forbidden",
            "technical_fixture": "technical_fixture_release_forbidden",
        }.get(release_evidence_policy, "formal_evidence_required"))
    elif review_purpose == "project_delivery" and not project_fact_certified:
        blockers.append("project_fact_certification_required")
    standards = state.get("standards") if isinstance(state.get("standards"), dict) else {}
    standard_packages = {
        str(row.get("package_id") or ""): row
        for row in standards.get("packages") or []
        if isinstance(row, dict)
    }
    methodology = standard_packages.get("PKG-STD-011") or {}
    if review_purpose == "project_delivery" and "PKG-STD-011" in standard_packages:
        # 直接检查 S003 方法书全文是否真的在册。原先只看 gate_status：该键在
        # material-snapshot 路径下并不存在（.get() 返回 None 而恰好阻断），一旦
        # lock 文件把 gate_status 标成 passed 而 S003 实际仍缺，阻断就会失效。
        methodology_artifacts = {
            str(item.get("artifact_id") or ""): item
            for item in methodology.get("artifacts") or []
            if isinstance(item, dict)
        }
        methodology_present = bool(
            methodology_artifacts.get("S003", {}).get("sha256")
        )
        if not methodology_present or methodology.get("gate_status") != "passed":
            blockers.append("standard_methodology_full_text_required:PKG-STD-011")
    state["blockers"] = sorted(set(blockers))
    state["warnings"] = [
        f"standard_framework_only:{item}:full_methodology_conformance_not_claimed"
        for item in standards.get("framework_only") or []
    ]
    # The technical result is independent of release eligibility.  The public
    # overall verdict is release-aware so it can never say pass while an
    # effective blocker is present.
    release_verdict = technical_verdict
    if state["blockers"]:
        release_verdict = (
            "incomplete" if technical_verdict == "incomplete" else "fail"
        )
    state["technical_verdict"] = technical_verdict
    state["release_verdict"] = release_verdict
    state["overall_verdict"] = release_verdict
    if release_verdict == "fail" and state["validation_status"] in {"passed", "conditional"}:
        state["validation_status"] = "failed"
        state["review_status"] = "release_blocked"
    state["validation_summary"] = {
        "status": state["validation_status"],
        "complete": state["validation_complete"],
        "overall_verdict": state["overall_verdict"],
        "technical_verdict": state["technical_verdict"],
        "release_verdict": state["release_verdict"],
        "review_purpose": review_purpose,
        "evidence_policy": release_evidence_policy,
        "project_fact_certified": project_fact_certified,
        "event_chain_valid": state["event_chain_valid"],
        "event_chain_hash": state["event_chain_hash"],
    }
    return state


def _freshness_reasons(workspace_id: str, state: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    target_spec = state.get("target_spec") or state.get("target") or {}
    try:
        normalized = normalize_target(target_spec)
        resolved, blockers = _resolve_target(
            workspace_id,
            normalized,
        )
    except (ValueError, OSError):
        resolved, blockers = None, ["target_reresolution_failed"]
    if blockers or resolved is None:
        reasons.extend(str(item) for item in blockers or ["target_unavailable"])
    else:
        if str(resolved.get("target_sha256") or "") != str((state.get("target") or {}).get("target_sha256") or ""):
            reasons.append("target_content_changed")
        current_upstream = _binding_snapshot(
            workspace_id,
            resolved.get("bindings") or {},
        )
        recorded_upstream = state.get("upstream_snapshot") or {}
        if recorded_upstream and sha256_json(current_upstream) != sha256_json(recorded_upstream):
            reasons.append("upstream_binding_changed")
    components = [
        str(row.get("rule_pack_id") or "")
        for row in ((state.get("rule_pack") or {}).get("components") or [])
        if str(row.get("rule_pack_id") or "")
    ]
    component_types = [
        str(row.get("target_type") or "")
        for row in ((target_spec.get("components") or []) if isinstance(target_spec, dict) else [])
        if isinstance(row, dict) and str(row.get("target_type") or "")
    ]
    try:
        current_pack = rules.compose(
            str((state.get("target") or {}).get("target_type") or ""),
            components,
            component_types=component_types,
            project_context=state.get("project_context") or {},
        )
        if current_pack.get("content_hash") != (state.get("rule_pack") or {}).get("content_hash"):
            reasons.append("rule_pack_changed")
        current_standards = rules.standards_snapshot(
            REPO_ROOT,
            current_pack.get("standard_package_ids") or [],
            review_purpose=str(
                (state.get("project_context") or {}).get("review_purpose")
                or (state.get("project_context") or {}).get("release_scope")
                or "project_delivery"
            ),
        )
        if current_standards.get("content_hash") != (state.get("standards") or {}).get("content_hash"):
            reasons.append("standards_changed")
    except ValueError:
        reasons.append("rule_pack_unavailable")
    return sorted(set(reasons))


def _project(workspace_id: str, review_id: str, *, check_freshness: bool = True) -> dict[str, Any]:
    state = _project_events(workspace_id, require_safe_id(review_id, "review_id"))
    if check_freshness and not state.get("invalidated"):
        reasons = _freshness_reasons(workspace_id, state)
        if reasons:
            STORE.append(
                workspace_id, review_id, "review_invalidated",
                {"reasons": reasons, "invalidated_at": utc_now()}, "system:freshness-check",
            )
            state = _project_events(workspace_id, review_id)
    return state
