"""Contracts shared by the feasibility delivery MCP service and server."""

from __future__ import annotations

STAGES: tuple[str, ...] = (
    "project",
    "research",
    "market",
    "option",
    "scale",
    "drivers",
    "finance_spec",
    "finance_run",
    "finance_tables",
    "report",
    "review",
    "released",
)

STAGE_STATUSES: tuple[str, ...] = (
    "pending",
    "in_progress",
    "partial",
    "blocked",
    "completed",
    "stale",
)

DELIVERY_MODES: tuple[str, ...] = (
    "estimate_preview",
    "review_candidate",
    "formal_release",
)

RELEASE_SCOPES: tuple[str, ...] = ("process_acceptance", "project_delivery")
EVIDENCE_POLICIES: tuple[str, ...] = (
    "formal_evidence",
    "sim_a_formal",
    "source_reconstructed",
    "technical_fixture",
    "controlled_assumption",
)

RUN_STATUSES: tuple[str, ...] = (
    "in_progress",
    "partial",
    "blocked",
    "completed",
    "stale",
    "released",
)


def empty_stage_record() -> dict[str, object]:
    return {
        "status": "pending",
        "input_refs": [],
        "output_refs": [],
        "basis_hash": "",
        "warnings": [],
        "blockers": [],
        "next_actions": [],
        "updated_from_run_id": "",
    }


def empty_stages() -> dict[str, dict[str, object]]:
    return {stage: empty_stage_record() for stage in STAGES}
