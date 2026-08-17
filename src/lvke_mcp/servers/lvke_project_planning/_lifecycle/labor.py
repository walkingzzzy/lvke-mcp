"""lvke-project-planning lifecycle 拆分：劳动力计划候选生命周期。"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from lvke_mcp.domains.project_planning import application as service

from .base import _candidate, _payload, _put_candidate


def infer_labor_plan(
    workspace_id: str,
    project_context_id: str,
    build_scale_case_id: str,
    position_requirements: list[dict[str, Any]],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    request = {
        "project_context_id": project_context_id,
        "build_scale_case_id": build_scale_case_id,
        "position_requirements": position_requirements,
    }

    def mutate() -> dict[str, Any]:
        context = service.PROJECT_CONTEXT_STORE.get(workspace_id, project_context_id)
        scale = service.BUILD_SCALE_STORE.get(workspace_id, build_scale_case_id)
        if context is None or scale is None or _payload(scale).get("status") != "confirmed":
            return service._blocked("labor_plan_basis_not_confirmed", "必须绑定已确认 ProjectContext 与 BuildScaleCase")
        evidence_track, evidence_policy, project_fact_certified = (
            service._planning_evidence_qualification(context, scale)
        )
        positions = []
        traces = []
        for index, row in enumerate(position_requirements):
            workload = service._decimal(row.get("annual_workload"))
            capacity = service._decimal(row.get("capacity_per_person_shift"))
            shifts = row.get("shift_count")
            coverage = service._decimal(row.get("coverage_factor"))
            if coverage is None:
                coverage = Decimal("1")
            automation = service._decimal(row.get("automation_adjustment"))
            if automation is None:
                automation = Decimal("1")
            if (
                workload is None or workload <= 0 or capacity is None or capacity <= 0
                or not isinstance(shifts, int) or isinstance(shifts, bool) or shifts <= 0
                or coverage <= 0 or automation <= 0
            ):
                return service._blocked("labor_inference_inputs_invalid", f"第 {index + 1} 个岗位工作量参数无效")
            raw = workload / (capacity * Decimal(shifts)) * coverage * automation
            headcount = max(1, math.ceil(raw))
            position = {
                "category": row["category"],
                "name": row["name"],
                "headcount": headcount,
                "avg_wage_yuan": row["avg_wage_yuan"],
                "welfare_rate": row["welfare_rate"],
                "annual_growth_rate": row.get("annual_growth_rate", 0),
                "evidence_bindings": row.get("evidence_bindings") or [],
                "shift_count": shifts,
            }
            positions.append(position)
            traces.append({
                "position": row["name"],
                "formula": "ceil(workload / (capacity_per_person_shift * shift_count) * coverage_factor * automation_adjustment)",
                "raw_headcount": float(raw),
                "headcount": headcount,
            })
        payload = {
            "object_type": "LaborPlan",
            "project_context_id": project_context_id,
            "build_scale_case_id": build_scale_case_id,
            "position_requirements": position_requirements,
            "positions": positions,
            "calculation_trace": traces,
            "status": "candidate",
            "evidence_track": evidence_track,
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_certified,
            "parent_object_ids": [project_context_id, build_scale_case_id],
        }
        return _put_candidate(
            service.LABOR_PLAN_STORE, workspace_id, payload,
            producer="lvke-project-planning.planning_infer_labor_plan",
            parent_ids=payload["parent_object_ids"],
            basis={"context_basis_hash": context["basis_hash"], "scale_basis_hash": scale["basis_hash"], **request}, id_field="labor_plan_id"
        )

    return service._idempotent_mutation(
        workspace_id,
        operation="planning_infer_labor_plan", idempotency_key=idempotency_key,
        request_payload=request, mutation=mutate
    )


def validate_labor_plan(
    workspace_id: str, labor_plan_id: str
) -> dict[str, Any]:
    record, error = _candidate(
        service.LABOR_PLAN_STORE, workspace_id, labor_plan_id, "LaborPlan"
    )
    if error:
        return error
    positions = _payload(record).get("positions") or []
    if not positions:
        return service._envelope(
            success=True,
            status="partial",
            code="labor_plan_validation_failed",
            blockers=[],
            warnings=["人员方案为空；对象仍可确认，但无法形成定员与工资测算。"],
            quality_issues=[{
                "code": "labor_positions_required",
                "path": "/positions",
                "blocking": False,
            }],
            field_errors={"/positions": {"code": "required"}},
            valid=False,
        )
    invalid = [index for index, row in enumerate(positions) if not row.get("headcount") or row.get("avg_wage_yuan") is None]
    if invalid:
        paths = [f"/positions/{index}" for index in invalid]
        return service._envelope(
            success=True,
            status="partial",
            code="labor_plan_validation_failed",
            blockers=[],
            warnings=["人员数量或工资参数不完整；人员方案仍可确认和进入下游。"],
            quality_issues=[
                {"code": "labor_plan_validation_failed", "path": path, "blocking": False}
                for path in paths
            ],
            field_errors={path: {"code": "invalid_or_missing_value"} for path in paths},
            valid=False,
        )
    return service._envelope(success=True, status="ok", valid=True)


def confirm_labor_plan(
    workspace_id: str,
    labor_plan_id: str,
    confirmation_reason: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    record, error = _candidate(
        service.LABOR_PLAN_STORE, workspace_id, labor_plan_id, "LaborPlan"
    )
    if error:
        return error
    reason = str(confirmation_reason or "").strip()
    payload = _payload(record)
    quality_issues = (
        [{
            "code": "labor_confirmation_reason_insufficient",
            "path": "/confirmation_reason",
            "blocking": False,
        }]
        if len(reason) < 10 else []
    )
    return service.create_labor_plan(
        workspace_id, payload["project_context_id"], payload["build_scale_case_id"], payload["positions"],
        parent_candidate_id=labor_plan_id,
        selection={
            "confirmation_reason": reason,
            "quality_issues": quality_issues,
            "release_limitations": [item["code"] for item in quality_issues],
        },
        idempotency_key=idempotency_key
    )
