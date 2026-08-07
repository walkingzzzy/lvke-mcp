"""legacy 门禁快照与 blocker 兼容层。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lvke_mcp.runtime.storage import sha256_json, utc_now


def _legacy_blockers(values: Any) -> list[str]:
    blockers: list[str] = []
    for value in values or []:
        if isinstance(value, dict):
            text = str(value.get("code") or value.get("rule") or value.get("message") or "")
        else:
            text = str(value or "")
        if text:
            blockers.append(text[:500])
    return sorted(set(blockers))


def _legacy_gate_result(
    passed: bool | None,
    source: str,
    *,
    blockers: Any = (),
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "verdict": "pass" if passed is True else ("fail" if passed is False else "unknown"),
        "passed": passed,
        "source": source,
        "blockers": _legacy_blockers(blockers),
        "details": deepcopy(details or {}),
    }


def _legacy_gate_snapshot(
    workspace_id: str,
    resolved: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the legacy engineering/publish decision for shadow comparison.

    The snapshot is derived from the resolved target or by invoking the exact
    read-only legacy validator. Callers cannot submit their own legacy verdict.
    Unknown is preserved when an old surface never had a corresponding gate.
    """

    target_type = str(resolved.get("target_type") or "")
    target_id = str(resolved.get("target_id") or "")
    snapshot = resolved.get("snapshot") if isinstance(resolved.get("snapshot"), dict) else {}
    validation = _legacy_gate_result(None, "legacy_gate_unavailable")
    publish = _legacy_gate_result(None, "legacy_publish_gate_unavailable")

    try:
        if target_type == "finance_run":
            from lvke_mcp.domains.finance.tables_application import validate_tables

            result = validate_tables(workspace_id, target_id)
            assessment = result.get("validation") if isinstance(result.get("validation"), dict) else {}
            valid = assessment.get("valid")
            formal = result.get("validation_complete")
            validation = _legacy_gate_result(
                valid if isinstance(valid, bool) else None,
                "tables_validate",
                blockers=result.get("blockers") or assessment.get("blockers"),
                details={"status": result.get("status"), "run_id": result.get("run_id")},
            )
            publish = _legacy_gate_result(
                formal if isinstance(formal, bool) else None,
                "tables_validate.validation_complete",
                blockers=result.get("blockers") or assessment.get("gate_blockers"),
            )
        elif target_type == "finance_tables_package":
            payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
            assessment = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
            valid = assessment.get("valid")
            formal = payload.get("validation_complete")
            validation = _legacy_gate_result(
                valid if isinstance(valid, bool) else None,
                "finance_tables_package.validation",
                blockers=assessment.get("blockers"),
                details={"package_status": snapshot.get("status")},
            )
            publish = _legacy_gate_result(
                formal if isinstance(formal, bool) else None,
                "finance_tables_package.validation_complete",
                blockers=assessment.get("gate_blockers"),
            )
        elif target_type in {"finance_xlsx", "finance_xlsx_source"}:
            validation = _legacy_gate_result(None, "external_xlsx_has_no_legacy_validator")
            publish = _legacy_gate_result(None, "external_xlsx_has_no_legacy_publish_gate")
        elif target_type == "acquisition_run":
            available = snapshot.get("available") is True
            succeeded = str(snapshot.get("status") or "") == "succeeded"
            consistent = snapshot.get("consistency_ok") is True
            open_blockers = [
                row for row in snapshot.get("issues") or []
                if isinstance(row, dict)
                and row.get("blocking") is True
                and str(row.get("status") or "open") == "open"
            ]
            validation = _legacy_gate_result(
                available and succeeded and consistent,
                "acquisition_run.consistency",
                blockers=open_blockers,
                details={
                    "status": snapshot.get("status"),
                    "consistency_ok": snapshot.get("consistency_ok"),
                    "formal_spec_valid": snapshot.get("formal_spec_valid"),
                },
            )
            publish = _legacy_gate_result(
                available
                and succeeded
                and consistent
                and snapshot.get("formal_spec_valid") is True
                and snapshot.get("evidence_formal_ok") is True
                and not open_blockers,
                "acquisition_run.deterministic_validation",
                blockers=open_blockers,
                details={"validation_status": snapshot.get("validation_status")},
            )
        elif target_type == "acquisition_tables_package":
            payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
            integrity = payload.get("integrity") if isinstance(payload.get("integrity"), dict) else {}
            integrity_passed = str(integrity.get("status") or "") == "passed"
            validation = _legacy_gate_result(
                integrity_passed,
                "acquisition_tables_package.integrity",
                blockers=integrity.get("blockers"),
            )
            run_id = str((resolved.get("bindings") or {}).get("finance_run_id") or "")
            from lvke_mcp.domains.asset_acquisition import backend as acquisition_service

            run = (
                acquisition_service.get_run(
                    workspace_id,
                    run_id,
                )
                if run_id
                else {}
            )
            run_valid = bool(run) and bool(
                run.get("status") == "succeeded"
                and run.get("consistency_ok") is True
                and run.get("formal_spec_valid") is True
                and run.get("evidence_formal_ok") is True
            )
            publish = _legacy_gate_result(
                integrity_passed and run_valid,
                "acquisition_tables_package.integrity_and_run_validation",
                blockers=integrity.get("blockers"),
                details={"run_id": run_id, "run_validation_status": run.get("validation_status")},
            )
        elif target_type == "report_revision":
            from lvke_mcp.domains.reports.validation import validate_report

            result = validate_report(workspace_id, target_id)
            valid = result.get("valid")
            validation = _legacy_gate_result(
                valid if isinstance(valid, bool) else None,
                "report_validate",
                blockers=result.get("blockers"),
                details={
                    "status": result.get("status"),
                    "report_revision_id": result.get("report_revision_id"),
                },
            )
            publish = _legacy_gate_result(
                None,
                "report_revision_had_no_standalone_legacy_release_gate",
            )
        elif target_type == "report_artifact":
            validation = _legacy_gate_result(
                True,
                "artifact_current_and_integrity_gate",
                details={
                    "artifact_family": snapshot.get("artifact_family"),
                    "artifact_id": snapshot.get("artifact_id"),
                },
            )
            publish = _legacy_gate_result(
                None,
                "artifact_release_readiness_not_embedded_in_snapshot",
            )
        elif target_type == "combined_deliverable":
            component_snapshots = [
                _legacy_gate_snapshot(
                    workspace_id,
                    component,
                )
                for component in snapshot.get("components") or []
                if isinstance(component, dict)
            ]

            def combined(kind: str) -> dict[str, Any]:
                verdicts = [str((row.get(kind) or {}).get("verdict") or "unknown") for row in component_snapshots]
                passed: bool | None
                if any(value == "fail" for value in verdicts):
                    passed = False
                elif verdicts and all(value == "pass" for value in verdicts):
                    passed = True
                else:
                    passed = None
                return _legacy_gate_result(
                    passed,
                    f"combined_component_{kind}",
                    details={"component_verdicts": verdicts},
                )

            validation = combined("validation")
            publish = combined("publish")
        else:
            validation = _legacy_gate_result(None, "target_type_has_no_legacy_validator")
            publish = _legacy_gate_result(None, "target_type_has_no_legacy_publish_gate")
    except Exception:  # noqa: BLE001 - comparison must remain honest and non-blocking
        validation = _legacy_gate_result(
            None,
            "legacy_gate_lookup_failed",
            blockers=["legacy_gate_lookup_failed"],
        )
        publish = _legacy_gate_result(
            None,
            "legacy_publish_gate_lookup_failed",
            blockers=["legacy_gate_lookup_failed"],
        )

    body = {
        "schema_version": "deliverable_review_legacy_gate_snapshot.v1",
        "target": {
            key: resolved.get(key)
            for key in ("target_type", "target_id", "target_sha256")
        },
        "captured_at": utc_now(),
        "validation": validation,
        "publish": publish,
    }
    return {**body, "content_hash": sha256_json(body)}
