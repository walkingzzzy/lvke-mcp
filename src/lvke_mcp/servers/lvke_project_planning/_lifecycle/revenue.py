"""lvke-project-planning lifecycle 拆分：收入驱动候选生命周期。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from lvke_mcp.domains.project_planning import application as service

from .base import _candidate, _payload, _put_candidate, _selection


def prepare_revenue_drivers(
    workspace_id: str,
    project_context_id: str,
    market_case_id: str,
    candidates: list[dict[str, Any]],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    request = {
        "project_context_id": project_context_id,
        "market_case_id": market_case_id,
        "candidates": candidates,
    }

    def mutate() -> dict[str, Any]:
        context, market, error = service._confirmed_market_basis(
            workspace_id, project_context_id, market_case_id
        )
        if error:
            return error
        assert context is not None and market is not None
        if not 1 <= len(candidates) <= 20:
            return service._blocked("revenue_candidates_invalid", "收入候选数量必须为 1..20")
        ids = [str(item.get("candidate_id") or "") for item in candidates]
        if "" in ids or len(set(ids)) != len(ids):
            return service._blocked("revenue_candidate_ids_invalid", "收入候选 ID 必须非空且唯一")
        prepared: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            spec = dict(candidate.get("revenue_spec") or {})
            years = candidate.get("op_years")
            if not isinstance(years, int) or isinstance(years, bool) or not 1 <= years <= 100:
                return service._blocked("revenue_op_years_invalid", f"第 {index + 1} 个候选经营期无效")
            try:
                from lvke_mcp.domains.finance.revenue_models import expand

                expanded = expand({"revenue": spec}, years)
            except (KeyError, TypeError, ValueError):
                return service._blocked("revenue_candidate_invalid", f"第 {index + 1} 个收入候选无法展开")
            series = list(expanded.get("revenue_by_year") or [])
            if len(series) != years or any((service._decimal(value) or Decimal("-1")) < 0 for value in series):
                return service._blocked("revenue_candidate_invalid", f"第 {index + 1} 个收入候选逐年序列无效")
            prepared.append({**candidate, "revenue_spec": spec, "expanded": expanded})
        payload = {
            "object_type": "RevenueDriverSet",
            "project_context_id": project_context_id,
            "market_case_id": market_case_id,
            "candidates": prepared,
            "selection": None,
            "status": "candidate",
            "evidence_track": _payload(context).get("evidence_track", "real"),
            "parent_object_ids": [project_context_id, market_case_id],
        }
        return _put_candidate(
            service.REVENUE_DRIVER_STORE,
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_prepare_revenue_drivers",
            parent_ids=payload["parent_object_ids"],
            basis={
                "context_basis_hash": context["basis_hash"],
                "market_basis_hash": market["basis_hash"],
                **request,
            },
            id_field="revenue_driver_set_id",
        )

    return service._idempotent_mutation(
        workspace_id,
        operation="planning_prepare_revenue_drivers",
        idempotency_key=idempotency_key,
        request_payload=request,
        mutation=mutate,
    )


def compare_revenue_candidates(
    workspace_id: str, revenue_driver_set_id: str
) -> dict[str, Any]:
    record, error = _candidate(
        service.REVENUE_DRIVER_STORE,
        workspace_id,
        revenue_driver_set_id,
        "RevenueDriverSet",
    )
    if error:
        return error
    candidates = _payload(record).get("candidates") or []
    comparisons = []
    for left_index, left in enumerate(candidates):
        for right in candidates[left_index + 1 :]:
            left_series = list((left.get("expanded") or {}).get("revenue_by_year") or [])
            right_series = list((right.get("expanded") or {}).get("revenue_by_year") or [])
            comparable = len(left_series) == len(right_series)
            comparisons.append({
                "left_candidate_id": left["candidate_id"],
                "right_candidate_id": right["candidate_id"],
                "comparable": comparable,
                "annual_revenue_differences_wan": (
                    [round(float(a) - float(b), 2) for a, b in zip(left_series, right_series, strict=True)]
                    if comparable
                    else []
                ),
            })
    return service._envelope(
        success=True,
        status="ok",
        comparisons=comparisons,
        aggregation="none",
        selection_required=True,
    )


def validate_revenue_drivers(
    workspace_id: str, revenue_driver_set_id: str
) -> dict[str, Any]:
    record, error = _candidate(
        service.REVENUE_DRIVER_STORE,
        workspace_id,
        revenue_driver_set_id,
        "RevenueDriverSet",
    )
    if error:
        return error
    candidates = _payload(record).get("candidates") or []
    if not candidates:
        return service._blocked("revenue_candidates_missing", "收入候选为空")
    blockers: list[str] = []
    for item in candidates:
        if item.get("mode", "estimate_preview") == "review_candidate" and (
            (item.get("revenue_spec") or {}).get("model") == "flat"
        ):
            binding = item.get("flat_evidence_binding") or {}
            if not all(binding.get(field) for field in ("source_id", "content_hash", "locator")):
                blockers.append("flat_revenue_formal_evidence_required")
            if str(binding.get("evidence_track") or "") == "source_reconstructed" and any(
                field not in binding or binding.get(field) in (None, "")
                for field in ("reconstruction_id", "source_uri", "source_kind", "method", "limitations")
            ):
                blockers.append("flat_revenue_reconstruction_binding_incomplete")
    if blockers:
        return service._envelope(
            success=False,
            status="blocked",
            code="revenue_driver_validation_failed",
            blockers=sorted(set(blockers)),
            valid=False,
        )
    return service._envelope(success=True, status="ok", valid=True)


def confirm_revenue_drivers(
    workspace_id: str,
    revenue_driver_set_id: str,
    selected_candidate_id: str,
    rejected_candidate_ids: list[str],
    selection_reason: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    record, error = _candidate(
        service.REVENUE_DRIVER_STORE,
        workspace_id,
        revenue_driver_set_id,
        "RevenueDriverSet",
    )
    if error:
        return error
    payload = _payload(record)
    candidates = payload.get("candidates") or []
    selection, error = _selection(
        [item["candidate_id"] for item in candidates],
        selected_candidate_id,
        rejected_candidate_ids,
        selection_reason,
    )
    if error:
        return error
    selected = next(item for item in candidates if item["candidate_id"] == selected_candidate_id)
    return service.create_revenue_driver_set(
        workspace_id,
        payload["project_context_id"],
        payload["market_case_id"],
        selected["revenue_spec"],
        selected["op_years"],
        mode=selected.get("mode", "estimate_preview"),
        flat_evidence_binding=selected.get("flat_evidence_binding"),
        parent_candidate_id=revenue_driver_set_id,
        selection=selection,
        idempotency_key=idempotency_key,
    )