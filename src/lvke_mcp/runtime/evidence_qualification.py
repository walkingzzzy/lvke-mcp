"""Fail-closed evidence qualification shared by domain projections.

Evidence labels describe how an object may be used; they do not by themselves
certify a project fact.  A downstream projection may retain certification only
when it explicitly declares ``formal_evidence``, passes its own qualification
gate, and every supplied factual parent is already certified.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


FORMAL_EVIDENCE = "formal_evidence"
SIM_A_FORMAL = "sim_a_formal"
CERTIFYING_POLICIES = frozenset({FORMAL_EVIDENCE, SIM_A_FORMAL})
NON_FORMAL_EVIDENCE_POLICIES = frozenset({
    "browser_snapshot",
    "candidate",
    "codex-browser",
    "controlled_assumption",
    "estimate_preview",
    "real",
    "source_reconstructed",
    "technical_fixture",
    "unverified_external_text",
})


def evidence_payload(value: Any) -> Mapping[str, Any]:
    """Return the business payload from either a record or a raw payload."""

    if not isinstance(value, Mapping):
        return {}
    nested = value.get("payload")
    return nested if isinstance(nested, Mapping) else value


def declared_evidence_policy(value: Any, *, default: str = "") -> str:
    """Read an explicit policy without upgrading legacy ``real`` labels."""

    payload = evidence_payload(value)
    for field in ("evidence_policy", "evidence_eligibility"):
        policy = str(payload.get(field) or "").strip()
        if policy:
            return policy
    track = str(payload.get("evidence_track") or "").strip()
    return track or str(default or "").strip()


def combine_evidence_policies(
    values: Iterable[Any],
    *,
    empty_policy: str = "candidate",
) -> str:
    """Combine parents, preserving the most restrictive declared policy."""

    policies = [
        declared_evidence_policy(value)
        for value in values
        if declared_evidence_policy(value)
    ]
    if not policies:
        return empty_policy
    if all(policy in CERTIFYING_POLICIES for policy in policies):
        return SIM_A_FORMAL if SIM_A_FORMAL in policies else FORMAL_EVIDENCE
    priority = (
        "controlled_assumption",
        "source_reconstructed",
        "technical_fixture",
        "estimate_preview",
        "browser_snapshot",
        "codex-browser",
        "unverified_external_text",
        "candidate",
        "real",
    )
    for policy in priority:
        if policy in policies:
            return policy
    # Unknown policies cannot acquire formal standing.  Keep their label so
    # callers can diagnose the unsupported upstream value.
    return sorted(policies)[0]


def project_fact_may_be_certified(
    evidence_policy: str,
    *,
    own_qualification_passed: bool,
    parents: Iterable[Any] = (),
) -> bool:
    """Return True only for an explicitly formal, fully certified lineage.

    Lineage correctness is inductive, not recursive: this checks only the
    parents it is handed, so each projection must pass its own immediate
    factual parents and must itself have been written through this gate.
    Full-chain soundness therefore follows from every writer using it — an
    omitted ``parents`` argument silently narrows the check to one object,
    so callers with upstream objects are expected to supply them.
    """

    policy = str(evidence_policy or "").strip()
    if policy not in CERTIFYING_POLICIES:
        return False
    if own_qualification_passed is not True:
        return False
    for parent in parents:
        payload = evidence_payload(parent)
        parent_policy = declared_evidence_policy(payload)
        if policy == FORMAL_EVIDENCE and parent_policy != FORMAL_EVIDENCE:
            return False
        if policy == SIM_A_FORMAL and parent_policy not in CERTIFYING_POLICIES:
            return False
        if payload.get("project_fact_certified") is not True:
            return False
    return True
