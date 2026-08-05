"""Shared Agent Coordination Contract for Lvke MCP responses.

The MCP servers remain deterministic infrastructure.  This module exposes the
small amount of execution context an Agent needs to continue a workflow after
an MCP call without turning the servers into a workflow engine or a second
writing agent.
"""

from __future__ import annotations

from typing import Any, Iterable


COORDINATION_CONTRACT_VERSION = "agent-coordination.v1"
QUALITY_STATES = (
    "ok",
    "partial",
    "empty",
    "missing_inputs",
    "blocked",
    "incomplete",
    "failed",
    "upstream_failure",
)
EVIDENCE_ELIGIBILITY = (
    "none",
    "formal_evidence",
    "source_reconstructed",
    "technical_fixture",
    "controlled_assumption",
    "estimate_preview",
    "candidate",
    "selected_fact",
)
DEFAULT_STAGES = {
    "lvke-data-acquisition": "source_discovery",
    "lvke-data-analysis": "evidence_analysis",
    "lvke-project-planning": "project_context",
    "lvke-deep-research": "deep_research",
    "lvke-finance-model": "finance_model",
    "lvke-finance-tables": "finance_tables",
    "lvke-report-generation": "report_revision",
    "lvke-deliverable-review": "deliverable_review",
    "lvke-asset-acquisition": "asset_acquisition",
    "finance-calc": "finance_calculation",
    "excel-bridge": "finance_tables",
}
OUTPUT_ID_TYPES = {
    "object_id": "domain_object",
    "task_id": "task",
    "run_id": "finance_run",
    "spec_id": "finance_spec",
    "package_id": "package",
    "finance_tables_package_id": "finance_tables_package",
    "report_revision_id": "report_revision",
    "review_id": "review",
    "research_package_id": "research_package",
    "evidence_pack_id": "evidence_pack",
    "source_snapshot_id": "source_snapshot",
    "discovery_set_id": "discovery_set",
    "candidate_set_id": "candidate_set",
}
PROJECT_CONTEXT_FIELDS = (
    "workspace_id",
    "industry_code",
    "project_type",
    "transaction_structure",
    "target_type",
    "asset_type",
    "evidence_track",
)
INPUT_ID_FIELDS = (
    "parent_object_ids",
    "source_snapshot_ids",
    "selected_source_ids",
    "evidence_pack_ids",
    "research_package_ids",
    "analysis_task_id",
    "report_preparation_id",
    "proposal_id",
    "expected_run_id",
)


def coordination_schema() -> dict[str, Any]:
    """Return the public JSON Schema for the coordination contract."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "contract_version": {"type": "string", "const": COORDINATION_CONTRACT_VERSION},
            "project_context": {"type": "object"},
            "stage": {"type": "string", "minLength": 1},
            "input_object_ids": {"type": "array", "items": {"type": "string"}},
            "output_object_ids": {"type": "array", "items": {"type": "string"}},
            "expected_output_types": {"type": "array", "items": {"type": "string"}},
            "quality_state": {"type": "string", "enum": list(QUALITY_STATES)},
            "evidence_eligibility": {"type": "string", "enum": list(EVIDENCE_ELIGIBILITY)},
            "next_actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "action": {"type": "string", "minLength": 1},
                        "target_tool": {"type": "string"},
                        "reason": {"type": "string"},
                        "required_inputs": {"type": "array", "items": {"type": "string"}},
                        "safe_to_retry": {"type": "boolean"},
                    },
                    "required": ["action", "reason", "required_inputs", "safe_to_retry"],
                },
            },
            "retry_policy": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "retryable": {"type": "boolean"},
                    "retry_after_seconds": {"type": ["integer", "null"], "minimum": 0},
                    "max_attempts": {"type": ["integer", "null"], "minimum": 1},
                },
                "required": ["retryable"],
            },
            "resume_token": {"type": ["string", "null"]},
            "lineage": {"type": "object"},
        },
        "required": [
            "contract_version",
            "project_context",
            "stage",
            "input_object_ids",
            "output_object_ids",
            "expected_output_types",
            "quality_state",
            "evidence_eligibility",
            "next_actions",
            "retry_policy",
            "resume_token",
            "lineage",
        ],
    }


def _strings(values: Iterable[Any] | None) -> list[str]:
    if isinstance(values, str):
        return [values] if values else []
    return [str(value) for value in values or [] if str(value)]


def _add_ids(target: list[str], value: Any) -> None:
    if isinstance(value, (list, tuple, set)):
        target.extend(str(item) for item in value if item)
    elif value:
        target.append(str(value))


def _lineage_ids(lineage: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key, value in lineage.items():
        if str(key).endswith(("_id", "_ids")):
            _add_ids(values, value)
    return values


def _action(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "action": str(value.get("action") or value.get("name") or "continue"),
            "target_tool": str(value.get("target_tool") or value.get("tool") or ""),
            "reason": str(value.get("reason") or "由当前业务状态决定下一步"),
            "required_inputs": _strings(value.get("required_inputs") or value.get("fields")),
            "safe_to_retry": bool(value.get("safe_to_retry", False)),
        }
    return {
        "action": str(value or "continue"),
        "target_tool": "",
        "reason": "由当前业务状态决定下一步",
        "required_inputs": [],
        "safe_to_retry": False,
    }


def build_coordination(result: dict[str, Any], *, server_name: str | None = None) -> dict[str, Any]:
    """Build a stable coordination payload from an existing tool result."""

    status = str(result.get("status") or ("failed" if result.get("success") is False else "ok"))
    quality_state = status if status in QUALITY_STATES else "ok"
    object_ids: list[str] = []
    inferred_output_types: list[str] = []
    for key, object_type in OUTPUT_ID_TYPES.items():
        value = result.get(key)
        if value:
            object_ids.append(str(value))
            inferred_output_types.append(object_type)
    output_types = _strings(result.get("expected_output_types"))
    if not output_types and result.get("object_type"):
        output_types = [str(result["object_type"])]
    if not output_types:
        output_types = list(dict.fromkeys(inferred_output_types))
    track = str(result.get("evidence_track") or "")
    inferred_eligibility = {
        "source_reconstructed": "source_reconstructed",
        "technical_fixture": "technical_fixture",
        "controlled_assumption": "controlled_assumption",
        "estimate_preview": "estimate_preview",
    }.get(track, "none")
    if track == "real" and result.get("formal_use_allowed") is True:
        inferred_eligibility = "formal_evidence"
    eligibility = str(result.get("evidence_eligibility") or inferred_eligibility)
    if eligibility not in EVIDENCE_ELIGIBILITY:
        eligibility = "none"
    retry_after = result.get("retry_after_seconds")
    if retry_after is not None:
        try:
            retry_after = max(0, int(retry_after))
        except (TypeError, ValueError):
            retry_after = None
    max_attempts = result.get("max_attempts")
    if max_attempts is not None:
        try:
            max_attempts = max(1, int(max_attempts))
        except (TypeError, ValueError):
            max_attempts = None
    project_context = result.get("project_context")
    lineage = result.get("lineage")
    normalized_context = dict(project_context) if isinstance(project_context, dict) else {}
    for field in PROJECT_CONTEXT_FIELDS:
        if field not in normalized_context and result.get(field) is not None:
            normalized_context[field] = result[field]
    input_ids = _strings(result.get("input_object_ids"))
    for field in INPUT_ID_FIELDS:
        _add_ids(input_ids, result.get(field))
    if isinstance(lineage, dict):
        input_ids.extend(_lineage_ids(lineage))
    return {
        "contract_version": COORDINATION_CONTRACT_VERSION,
        "project_context": normalized_context,
        "stage": str(
            result.get("stage")
            or result.get("workflow_stage")
            or DEFAULT_STAGES.get(str(server_name or ""), "unspecified")
        ),
        "input_object_ids": list(dict.fromkeys(input_ids)),
        "output_object_ids": list(dict.fromkeys(object_ids)),
        "expected_output_types": output_types,
        "quality_state": quality_state,
        "evidence_eligibility": eligibility,
        "next_actions": [_action(item) for item in result.get("next_actions") or []],
        "retry_policy": {
            "retryable": bool(result.get("retryable", False)),
            "retry_after_seconds": retry_after,
            "max_attempts": max_attempts,
        },
        "resume_token": result.get("resume_token"),
        "lineage": dict(lineage) if isinstance(lineage, dict) else {},
    }
