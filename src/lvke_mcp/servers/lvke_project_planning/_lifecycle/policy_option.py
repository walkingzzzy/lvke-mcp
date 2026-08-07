"""lvke-project-planning lifecycle 拆分：政策基础与方案比选。"""

from __future__ import annotations

from typing import Any

from lvke_mcp.domains.project_planning import application as service

from .base import _candidate, _payload, _put_candidate


def prepare_policy_basis(
    workspace_id: str,
    project_context_id: str,
    candidates: list[dict[str, Any]],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    request = {"project_context_id": project_context_id, "candidates": candidates}

    def mutate() -> dict[str, Any]:
        context = service.PROJECT_CONTEXT_STORE.get(workspace_id, project_context_id)
        if context is None:
            return service._blocked("project_context_not_found", "ProjectContext 不存在")
        ids = [str(row.get("candidate_id") or "") for row in candidates]
        if not candidates or "" in ids or len(set(ids)) != len(ids):
            return service._blocked("policy_candidates_invalid", "政策候选必须非空且 ID 唯一")
        allowed = {"applicable", "pending_verification", "excluded", "expired"}
        if any(row.get("classification") not in allowed for row in candidates):
            return service._blocked("policy_classification_invalid", "政策候选分类无效")
        for row in candidates:
            if not all(row.get(field) for field in ("title", "source_snapshot_id", "content_hash", "locator")):
                return service._blocked("policy_evidence_incomplete", "政策候选必须绑定 snapshot、locator 和 hash")
        payload = {
            "object_type": "PolicyBasis",
            "project_context_id": project_context_id,
            "candidates": candidates,
            "selection": None,
            "status": "candidate",
            "evidence_track": _payload(context).get("evidence_track", "real"),
            "parent_object_ids": [project_context_id, *[row["source_snapshot_id"] for row in candidates]],
        }
        return _put_candidate(
            service.POLICY_BASIS_STORE, workspace_id, payload,
            producer="lvke-project-planning.planning_prepare_policy_basis",
            parent_ids=payload["parent_object_ids"],
            basis={"context_basis_hash": context["basis_hash"], **request}, id_field="policy_basis_id"
        )

    return service._idempotent_mutation(
        workspace_id,
        operation="planning_prepare_policy_basis", idempotency_key=idempotency_key,
        request_payload=request, mutation=mutate
    )


def confirm_policy_basis(
    workspace_id: str,
    policy_basis_id: str,
    selected_candidate_ids: list[str],
    selection_reason: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    request = {
        "policy_basis_id": policy_basis_id,
        "selected_candidate_ids": sorted(set(selected_candidate_ids)),
        "selection_reason": selection_reason,
    }

    def mutate() -> dict[str, Any]:
        record, error = _candidate(
            service.POLICY_BASIS_STORE, workspace_id, policy_basis_id, "PolicyBasis"
        )
        if error:
            return error
        payload = _payload(record)
        by_id = {row["candidate_id"]: row for row in payload.get("candidates") or []}
        selected = set(selected_candidate_ids)
        if not selected or not selected <= set(by_id):
            return service._blocked("policy_selection_invalid", "政策选择必须是已知非空候选集合")
        if any(by_id[item]["classification"] not in {"applicable", "pending_verification"} for item in selected):
            return service._blocked("policy_selection_ineligible", "过期或排除政策不得选为政策基础")
        if len(str(selection_reason or "").strip()) < 10:
            return service._blocked("policy_selection_reason_insufficient", "政策选择理由至少 10 个字符")
        confirmed_payload = {
            **payload,
            "status": "confirmed",
            "selection": {
                "selected_candidate_ids": sorted(selected),
                "rejected_candidate_ids": sorted(set(by_id) - selected),
                "selection_reason": selection_reason.strip(),
            },
            "parent_object_ids": [policy_basis_id, *list(payload.get("parent_object_ids") or [])],
        }
        result = _put_candidate(
            service.POLICY_BASIS_STORE, workspace_id, confirmed_payload,
            producer="lvke-project-planning.planning_confirm_policy_basis",
            parent_ids=confirmed_payload["parent_object_ids"],
            basis={"candidate_basis_hash": record["basis_hash"], **request}, id_field="policy_basis_id"
        )
        result["policy_basis"]["status"] = "confirmed"
        return result

    return service._idempotent_mutation(
        workspace_id,
        operation="planning_confirm_policy_basis", idempotency_key=idempotency_key,
        request_payload=request, mutation=mutate
    )


def validate_option_comparison(
    workspace_id: str, option_comparison_id: str
) -> dict[str, Any]:
    record = service.OPTION_COMPARISON_STORE.get(
        workspace_id, option_comparison_id
    )
    if record is None:
        return service._blocked("option_comparison_not_found", "方案比选对象不存在")
    payload = _payload(record)
    blockers = []
    if not payload.get("options"):
        blockers.append("options_missing")
    if not payload.get("criteria"):
        blockers.append("criteria_missing")
    if not any(row.get("eligible") for row in payload.get("options") or []):
        blockers.append("no_eligible_option")
    if blockers:
        return service._envelope(success=False, status="blocked", code="option_comparison_invalid", blockers=blockers, valid=False)
    return service._envelope(success=True, status="ok", valid=True)


def score_option_comparison(
    workspace_id: str, option_comparison_id: str
) -> dict[str, Any]:
    record = service.OPTION_COMPARISON_STORE.get(
        workspace_id, option_comparison_id
    )
    if record is None:
        return service._blocked("option_comparison_not_found", "方案比选对象不存在")
    payload = _payload(record)
    return service._envelope(
        success=True,
        status="ok",
        score_method=payload.get("score_method"),
        score_leader_option_id=payload.get("score_leader_option_id"),
        options=payload.get("options") or [],
        content_hash=record["content_hash"],
    )