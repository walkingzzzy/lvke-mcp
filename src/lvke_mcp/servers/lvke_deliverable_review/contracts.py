"""Public review contracts and invariant helpers."""

from __future__ import annotations

from typing import Any

TARGET_TYPES = {
    "finance_run", "finance_tables_package", "finance_xlsx", "finance_xlsx_source",
    "acquisition_run", "acquisition_tables_package", "report_revision",
    "report_artifact", "combined_deliverable",
}
VERDICTS = {"pass", "conditional_pass", "fail", "incomplete"}
SEVERITIES = {"P0", "P1", "P2", "P3"}
FINDING_STATUSES = {
    "open", "confirmed", "rejected", "remediation_in_progress",
    "false_positive_appeal", "waiver_requested", "waived", "resolved", "superseded",
}
DEPLOYMENT_MODES = {"enforced", "shadow"}

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
EVIDENCE_TRACKS = {"real", "source_reconstructed", "technical_fixture", "controlled_assumption"}
PROJECT_TYPES = {"generic_feasibility", "asset_acquisition"}
TRANSACTION_STRUCTURES = {
    "new_build", "operation_lease", "asset_acquisition", "equity_acquisition",
    "ppp", "other",
}
ASSET_TYPES = {"general", "amusement_park", "solar_power", "hotel_lease", "mineral_processing"}


def normalize_project_context(value: Any, *, target_type: str) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    inferred_project_type = (
        "asset_acquisition" if target_type.startswith("acquisition_") else "generic_feasibility"
    )
    project_type = str(raw.get("project_type") or inferred_project_type).strip()
    transaction = str(
        raw.get("transaction_structure")
        or ("asset_acquisition" if project_type == "asset_acquisition" else "new_build")
    ).strip()
    asset_type = str(raw.get("asset_type") or "general").strip()
    evidence_track = str(raw.get("evidence_track") or "real").strip()
    industry_code = str(raw.get("industry_code") or "general").strip()
    if project_type not in PROJECT_TYPES:
        raise ValueError("project_type_invalid")
    if transaction not in TRANSACTION_STRUCTURES:
        raise ValueError("transaction_structure_invalid")
    if asset_type not in ASSET_TYPES:
        raise ValueError("asset_type_invalid")
    if evidence_track not in EVIDENCE_TRACKS:
        raise ValueError("evidence_track_invalid")
    if project_type == "asset_acquisition" and transaction not in {
        "asset_acquisition", "equity_acquisition", "operation_lease",
    }:
        raise ValueError("transaction_structure_project_type_mismatch")
    return {
        "industry_code": industry_code,
        "project_type": project_type,
        "transaction_structure": transaction,
        "target_type": target_type,
        "asset_type": asset_type,
        "evidence_track": evidence_track,
    }


def require_write_context(args: dict[str, Any]) -> tuple[str, str, str]:
    workspace_id = str(args.get("workspace_id") or "").strip()
    key = str(args.get("idempotency_key") or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id_required")
    if not key or len(key) > 160:
        raise ValueError("idempotency_key_required")
    return workspace_id, "", key


def normalize_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict):
        raise ValueError("target_required")
    target_type = str(target.get("target_type") or "").strip()
    target_id = str(target.get("target_id") or "").strip()
    if target_type not in TARGET_TYPES:
        raise ValueError("target_type_invalid")
    if not target_id:
        raise ValueError("target_id_required")
    return {**target, "target_type": target_type, "target_id": target_id}


def finding_blocks(finding: dict[str, Any]) -> bool:
    if str(finding.get("status") or "open") in {"resolved", "rejected", "superseded"}:
        return False
    severity = str(finding.get("severity") or "P2")
    if severity == "P0":
        return True
    if severity == "P1":
        return str(finding.get("status") or "open") != "waived"
    return bool(finding.get("blocking")) and str(finding.get("status")) != "waived"


def verdict_for(findings: list[dict[str, Any]], incomplete_reasons: list[str]) -> str:
    if incomplete_reasons:
        return "incomplete"
    active = [row for row in findings if row.get("status", "open") not in {"resolved", "rejected", "superseded"}]
    if any(row.get("severity") == "P0" for row in active):
        return "fail"
    if any(finding_blocks(row) for row in active):
        return "fail"
    if active:
        return "conditional_pass"
    return "pass"
