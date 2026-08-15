"""lvke-project-planning lifecycle 拆分：成本驱动候选生命周期。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from lvke_mcp.domains.project_planning import application as service

from .base import _candidate, _payload, _put_candidate


def prepare_cost_drivers(
    workspace_id: str,
    project_context_id: str,
    build_scale_case_id: str,
    invest_breakdown: dict[str, Any],
    operating_cost_items: list[dict[str, Any]],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    request = {
        "project_context_id": project_context_id,
        "build_scale_case_id": build_scale_case_id,
        "invest_breakdown": invest_breakdown,
        "operating_cost_items": operating_cost_items,
    }

    def mutate() -> dict[str, Any]:
        context = service.PROJECT_CONTEXT_STORE.get(workspace_id, project_context_id)
        scale = service.BUILD_SCALE_STORE.get(workspace_id, build_scale_case_id)
        if context is None or scale is None or _payload(scale).get("status") != "confirmed":
            return service._blocked("cost_driver_basis_not_confirmed", "必须绑定已确认 ProjectContext 与 BuildScaleCase")
        evidence_track, evidence_policy, project_fact_certified = (
            service._planning_evidence_qualification(context, scale)
        )
        payload = {
            "object_type": "CostDriverSet",
            **request,
            "status": "candidate",
            "evidence_track": evidence_track,
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_certified,
            "parent_object_ids": [project_context_id, build_scale_case_id],
        }
        return _put_candidate(
            service.COST_DRIVER_STORE, workspace_id, payload,
            producer="lvke-project-planning.planning_prepare_cost_drivers",
            parent_ids=payload["parent_object_ids"],
            basis={"context_basis_hash": context["basis_hash"], "scale_basis_hash": scale["basis_hash"], **request}, id_field="cost_driver_set_id"
        )

    return service._idempotent_mutation(
        workspace_id,
        operation="planning_prepare_cost_drivers", idempotency_key=idempotency_key,
        request_payload=request, mutation=mutate
    )


def _calculated_cost_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    calculated: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        row = dict(item)
        amount = service._decimal(row.get("annual_amount_wan"))
        if amount is None:
            quantity = service._decimal(row.get("annual_quantity"))
            consumption = service._decimal(row.get("unit_consumption"))
            price = service._decimal(row.get("unit_price_yuan"))
            conversion = service._decimal(row.get("conversion_to_wan"))
            if conversion is None:
                conversion = Decimal("0.0001")
            loss = service._decimal(row.get("loss_rate"))
            if loss is None:
                loss = Decimal("0")
            if (
                quantity is None
                or consumption is None
                or price is None
                or quantity < 0
                or consumption < 0
                or price < 0
                or conversion <= 0
                or loss < 0
            ):
                errors.append(f"/operating_cost_items/{index}")
                continue
            amount = quantity * consumption * price * conversion * (Decimal("1") + loss)
        if amount < 0:
            errors.append(f"/operating_cost_items/{index}/annual_amount_wan")
            continue
        row["annual_amount_wan"] = float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        row["calculation_trace"] = {
            "method": "explicit_amount" if item.get("annual_amount_wan") is not None else "quantity_consumption_price",
            "formula": "quantity * unit_consumption * unit_price_yuan * conversion_to_wan * (1 + loss_rate)",
            "annual_quantity_semantics": "cost_calculation_quantity",
            "design_capacity_semantics": "engineering_capacity_only_not_used_in_amount",
        }
        calculated.append(row)
    return calculated, errors


def calculate_cost_drivers(
    workspace_id: str,
    cost_driver_set_id: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    request = {"cost_driver_set_id": cost_driver_set_id}

    def mutate() -> dict[str, Any]:
        record, error = _candidate(
            service.COST_DRIVER_STORE, workspace_id, cost_driver_set_id, "CostDriverSet"
        )
        if error:
            return error
        payload = _payload(record)
        calculated, errors = _calculated_cost_items(payload.get("operating_cost_items") or [])
        if errors:
            return service._envelope(
                success=False, status="missing_inputs", code="cost_driver_calculation_inputs_missing",
                blockers=errors, field_errors={path: {"code": "calculation_inputs_required"} for path in errors}
            )
        new_payload = {
            **payload,
            "operating_cost_items": calculated,
            "annual_operating_cost_wan": round(sum(float(row["annual_amount_wan"]) for row in calculated), 2),
            "status": "calculated",
            "parent_object_ids": [cost_driver_set_id, *list(payload.get("parent_object_ids") or [])],
        }
        return _put_candidate(
            service.COST_DRIVER_STORE, workspace_id, new_payload,
            producer="lvke-project-planning.planning_calculate_cost_drivers",
            parent_ids=new_payload["parent_object_ids"],
            basis={"candidate_basis_hash": record["basis_hash"], "calculation_method": "cost_drivers.v1"}, id_field="cost_driver_set_id"
        )

    return service._idempotent_mutation(
        workspace_id,
        operation="planning_calculate_cost_drivers", idempotency_key=idempotency_key,
        request_payload=request, mutation=mutate
    )


def validate_cost_drivers(
    workspace_id: str, cost_driver_set_id: str
) -> dict[str, Any]:
    record, error = _candidate(
        service.COST_DRIVER_STORE, workspace_id, cost_driver_set_id, "CostDriverSet"
    )
    if error:
        return error
    payload = _payload(record)
    items, errors = _calculated_cost_items(payload.get("operating_cost_items") or [])
    invest = payload.get("invest_breakdown") or {}
    values = {key: service._decimal(invest.get(key)) for key in (
        "construction_wan", "civil_wan", "equipment_wan", "installation_wan",
        "other_wan", "reserve_wan", "interest_wan", "working_capital_wan"
    )}
    if any(value is None or value < 0 for value in values.values()):
        errors.append("/invest_breakdown")
    elif abs(
        values["construction_wan"]
        - sum(values[key] for key in ("civil_wan", "equipment_wan", "installation_wan", "other_wan", "reserve_wan"))
    ) > Decimal("0.01"):
        errors.append("/invest_breakdown/construction_wan")
    if len(items) < 3:
        errors.append("/operating_cost_items")
    if errors:
        return service._envelope(
            success=False, status="blocked", code="cost_driver_validation_failed",
            blockers=sorted(set(errors)), valid=False
        )
    return service._envelope(success=True, status="ok", valid=True)


def confirm_cost_drivers(
    workspace_id: str,
    cost_driver_set_id: str,
    confirmation_reason: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    record, error = _candidate(
        service.COST_DRIVER_STORE, workspace_id, cost_driver_set_id, "CostDriverSet"
    )
    if error:
        return error
    if len(str(confirmation_reason or "").strip()) < 10:
        return service._blocked("cost_confirmation_reason_insufficient", "确认理由至少 10 个字符")
    payload = _payload(record)
    items, errors = _calculated_cost_items(payload.get("operating_cost_items") or [])
    if errors:
        return service._blocked("cost_driver_not_calculable", "成本候选仍缺少计算输入")
    return service.create_cost_driver_set(
        workspace_id, payload["project_context_id"], payload["build_scale_case_id"],
        payload["invest_breakdown"], items,
        parent_candidate_id=cost_driver_set_id,
        selection={"confirmation_reason": confirmation_reason.strip()}, idempotency_key=idempotency_key
    )


def get_environmental_scheme_templates(
    project_type: str, pollutant_types: list[str]
) -> dict[str, Any]:
    templates = {
        "wastewater": {"required_fields": ["pollutant", "annual_quantity", "treatment_process", "design_capacity", "capex_wan", "opex_wan"]},
        "waste_gas": {"required_fields": ["pollutant", "annual_quantity", "treatment_process", "design_capacity", "capex_wan", "opex_wan"]},
        "solid_waste": {"required_fields": ["pollutant", "annual_quantity", "disposal_route", "capex_wan", "opex_wan"]},
        "noise": {"required_fields": ["source", "control_measure", "capex_wan", "opex_wan"]},
    }
    selected = {key: templates[key] for key in pollutant_types if key in templates}
    unknown = sorted(set(pollutant_types) - set(selected))
    return service._envelope(
        success=not unknown,
        status="ok" if not unknown else "partial",
        code="" if not unknown else "environmental_template_not_found",
        blockers=unknown,
        project_type=project_type,
        templates=selected,
        template_version="environmental-cost.v1",
        evidence_eligibility="schema_only",
    )
