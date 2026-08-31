"""Finalize pending suite retests without depending on review lifecycle execution."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.servers.lvke_deliverable_review.contracts import REVIEW_DIMENSIONS
from lvke_mcp.servers.lvke_deliverable_review.store import STORE

from .base import _finding_coverage_rule_id, _finding_match_key
from .events import _project


def append_retest_event_once(
    workspace_id: str,
    review_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    identity_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    operation_id = str(payload.get("operation_id") or "")
    matches = [
        event
        for event in STORE.events(workspace_id, review_id)
        if event.get("event_type") == event_type
        and str((event.get("payload") or {}).get("operation_id") or "") == operation_id
        and all(
            (event.get("payload") or {}).get(field) == payload.get(field)
            for field in identity_fields
        )
    ]
    if matches:
        if len(matches) != 1 or sha256_json(matches[0].get("payload") or {}) != sha256_json(payload):
            raise ValueError("retest_operation_conflict")
        return matches[0]
    return STORE.append(workspace_id, review_id, event_type, payload)


def complete_pending_suite_retest(
    workspace_id: str,
    child_review_id: str,
    *,
    check_catalog: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    """Complete a ReviewPackage retest only after its child dossier is complete."""

    pending: list[tuple[str, dict[str, Any]]] = []
    for parent_review_id in STORE.review_ids(workspace_id):
        events = STORE.events(workspace_id, parent_review_id)
        intents = {
            str((row.get("payload") or {}).get("operation_id") or ""): row.get("payload") or {}
            for row in events
            if row.get("event_type") == "retest_started"
        }
        completed = {
            str((row.get("payload") or {}).get("operation_id") or "")
            for row in events
            if row.get("event_type") == "retest_completed"
        }
        for row in events:
            payload = row.get("payload") or {}
            operation_id = str(payload.get("operation_id") or "")
            if (
                row.get("event_type") == "retest_child_started"
                and str(payload.get("child_review_id") or "") == child_review_id
                and operation_id in intents
                and operation_id not in completed
            ):
                pending.append((parent_review_id, intents[operation_id]))
    if not pending:
        return None
    if len(pending) != 1:
        raise ValueError("retest_operation_conflict")

    parent_review_id, intent = pending[0]
    operation_id = str(intent.get("operation_id") or "")
    child = _project(workspace_id, child_review_id, check_freshness=False)
    # 前置是「子审查已完成七域独立评审」，不是「子审查整体 pass」。
    # 复测的语义是「已就整改后的目标重新独立评审并给出结论」——结论为 failed
    # 也是结论。此前要求 formal_suite_review_complete（overall=="pass"），使
    # 「复测了但只整改一部分」这条最常见路径永远清不掉挂起，父审查再也无法
    # 处置 finding（见 disposition 侧同一缺陷）。
    # 逐条关闭判据在下面仍是 `new_finding is None and coverage_ok`，按 finding
    # 独立裁决，所以部分整改只会关闭真正已修的项，不会因整体放行而误关。
    # 七域齐全只在 standard/deep 下要求。quick profile 的 require_semantic 为
    # False，`quick_preview_without_semantic_assessment` 是合法状态、七域 Assessment
    # 本就不必提交（suite_review.py:1176,1203）；若在这里无条件要求七域，quick
    # 复测会永远卡在 retest_assessment_required —— 那等于把原来的死锁换成另一个
    # 死锁。quick 下只要求引擎已产出确定性结论即可。
    child_profile = str(child.get("review_profile") or child.get("mode") or "quick")
    if child_profile in {"standard", "deep"}:
        assessed = child.get("suite_assessments") or {}
        confirmed = child.get("suite_dimension_confirmations") or {}
        missing_dimensions = sorted(
            (REVIEW_DIMENSIONS - set(assessed)) | (REVIEW_DIMENSIONS - set(confirmed))
        )
        if missing_dimensions:
            return {
                "status": "pending",
                "code": "retest_assessment_required",
                "parent_review_id": parent_review_id,
                "retest_review_id": child_review_id,
                "missing_dimensions": missing_dimensions,
            }
    elif not child.get("findings_available"):
        return {
            "status": "pending",
            "code": "retest_assessment_required",
            "parent_review_id": parent_review_id,
            "retest_review_id": child_review_id,
            "missing_dimensions": [],
        }

    parent_basis = intent.get("parent_basis") or {}
    old_findings = list(parent_basis.get("findings") or [])
    new_by_match = {_finding_match_key(row): row for row in child.get("findings") or []}
    new_by_rule: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for finding in child.get("findings") or []:
        new_by_rule.setdefault(
            (str(finding.get("rule_id") or ""), str(finding.get("category") or "")),
            [],
        ).append(finding)
    metrics = (child.get("coverage") or {}).get("dimension_metrics") or {}
    executed_rules = {
        str(check_id)
        for row in metrics.values()
        if isinstance(row, dict)
        for check_id in row.get("executed_checks") or []
    }
    assessments = child.get("suite_assessments") or {}
    confirmations = child.get("suite_dimension_confirmations") or {}

    closed: list[str] = []
    remaining: list[str] = []
    for old_finding in old_findings:
        old_id = str(old_finding.get("finding_id") or "")
        rule_id = str(old_finding.get("rule_id") or "")
        new_finding = new_by_match.get(_finding_match_key(old_finding))
        if new_finding is None:
            matches = new_by_rule.get(
                (rule_id, str(old_finding.get("category") or "")),
                [],
            )
            new_finding = matches[0] if matches else None
        spec = check_catalog.get(rule_id) or {}
        dimension = str(spec.get("dimension") or old_finding.get("review_area") or "")
        if spec.get("kind") == "semantic":
            coverage_ok = dimension in assessments and dimension in confirmations
        else:
            coverage_ok = _finding_coverage_rule_id(old_finding) in executed_rules
        retest_passed = new_finding is None and coverage_ok
        (closed if retest_passed else remaining).append(old_id)
        append_retest_event_once(
            workspace_id,
            parent_review_id,
            "finding_retested",
            {
                "operation_id": operation_id,
                "finding_id": old_id,
                "new_status": "remediation_in_progress",
                "retest_passed": retest_passed,
                "parent_review_id": parent_review_id,
                "retest_review_id": child_review_id,
                "before_value": deepcopy(old_finding.get("actual")),
                "after_value": (
                    "finding_not_reproduced"
                    if retest_passed
                    else deepcopy((new_finding or {}).get("actual"))
                ),
                "remediation_evidence": deepcopy(intent.get("remediation_evidence") or []),
                "same_rule_pack": (
                    (child.get("rule_pack") or {}).get("content_hash")
                    == (parent_basis.get("rule_pack") or {}).get("content_hash")
                ),
                "retested_at": intent.get("operation_started_at"),
            },
            identity_fields=("finding_id",),
        )
    closed = sorted(set(closed))
    remaining = sorted(set(remaining))
    link = {
        "operation_id": operation_id,
        "parent_review_id": parent_review_id,
        "child_review_id": child_review_id,
        "parent_target_sha256": (parent_basis.get("target") or {}).get("target_sha256"),
        "child_target_sha256": (child.get("target") or {}).get("target_sha256"),
        "parent_rule_pack_hash": (parent_basis.get("rule_pack") or {}).get("content_hash"),
        "child_rule_pack_hash": (child.get("rule_pack") or {}).get("content_hash"),
        "completed": True,
        "closed_finding_ids": closed,
        "remaining_finding_ids": remaining,
        "remediation_evidence": deepcopy(intent.get("remediation_evidence") or []),
        "remediation_evidence_hash": str(intent.get("remediation_evidence_hash") or ""),
        "retested_at": intent.get("operation_started_at"),
    }
    append_retest_event_once(workspace_id, parent_review_id, "retest_linked", link)
    append_retest_event_once(workspace_id, child_review_id, "retest_linked", link)
    completion = {
        "operation_id": operation_id,
        "parent_review_id": parent_review_id,
        "child_review_id": child_review_id,
        "expected_finding_ids": list(intent.get("expected_finding_ids") or []),
        "link_hash": sha256_json(link),
        "completed": True,
    }
    append_retest_event_once(
        workspace_id,
        child_review_id,
        "retest_completed",
        {**completion, "side": "child"},
    )
    append_retest_event_once(
        workspace_id,
        parent_review_id,
        "retest_completed",
        {**completion, "side": "parent"},
    )
    return {
        "status": "completed",
        "parent_review_id": parent_review_id,
        "retest_review_id": child_review_id,
        "closed_finding_ids": closed,
        "remaining_finding_ids": remaining,
    }


__all__ = ["append_retest_event_once", "complete_pending_suite_retest"]
