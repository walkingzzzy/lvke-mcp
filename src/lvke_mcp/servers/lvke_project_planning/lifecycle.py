"""Candidate/validation/confirmation lifecycles for planning objects."""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import yaml

from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.domains.project_planning import application as service


def _payload(record: dict[str, Any] | None) -> dict[str, Any]:
    return dict((record or {}).get("payload") or {})


def _candidate(
    store: Any,
    workspace_id: str,
    object_id: str,
    object_type: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    record = store.get(workspace_id, object_id)
    payload = _payload(record)
    if record is None or payload.get("object_type") != object_type:
        return None, service._blocked(
            f"{object_type.lower()}_not_found", f"{object_type} 不存在或不属于当前作用域"
        )
    if payload.get("status") not in {"candidate", "calculated"}:
        return None, service._blocked(
            f"{object_type.lower()}_not_candidate", f"{object_type} 不是可确认候选"
        )
    return record, None


def _selection(
    candidate_ids: list[str],
    selected_candidate_id: str,
    rejected_candidate_ids: list[str],
    selection_reason: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    known = set(candidate_ids)
    if selected_candidate_id not in known:
        return None, service._blocked("planning_candidate_not_found", "选定候选不存在")
    if set(rejected_candidate_ids) != known - {selected_candidate_id}:
        return None, service._blocked(
            "planning_rejected_candidates_incomplete", "必须明确列出全部舍弃候选"
        )
    reason = str(selection_reason or "").strip()
    if len(reason) < 10:
        return None, service._blocked(
            "planning_selection_reason_insufficient", "选择理由至少 10 个字符"
        )
    return {
        "selected_candidate_id": selected_candidate_id,
        "rejected_candidate_ids": sorted(rejected_candidate_ids),
        "selection_reason": reason,
        "aggregation": "none",
    }, None


def _put_candidate(
    store: Any,
    workspace_id: str,
    payload: dict[str, Any],
    *,
    producer: str,
    parent_ids: list[str],
    basis: dict[str, Any],
    id_field: str,
) -> dict[str, Any]:
    record = store.put(
        workspace_id,
        payload,
        producer=producer,
        status=str(payload.get("status") or "candidate"),
        source_ids=parent_ids,
        basis=basis,
    )
    return service._envelope(
        success=True,
        status="ok",
        resource_uris=[record["resource_uri"]],
        object_id=record["object_id"],
        **{
            id_field: record["object_id"],
            id_field.removesuffix("_id"): service._planning_view(record, id_field),
        },
        basis_hash=record["basis_hash"],
        content_hash=record["content_hash"],
        idempotent_replay=False,
    )


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


def get_industry_constraints(
    workspace_id: str, project_context_id: str
) -> dict[str, Any]:
    context = service.PROJECT_CONTEXT_STORE.get(
        workspace_id, project_context_id
    )
    if context is None:
        return service._blocked("project_context_not_found", "ProjectContext 不存在")
    path = Path(__file__).resolve().parents[2] / "config" / "industry_params.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    project = _payload(context)
    industry = str(project.get("industry_code") or "").lower()
    aliases = {
        "制造": ("manufacturing", "制造"),
        "仓储物流": ("logistics", "warehouse", "仓储", "物流"),
        "农业": ("agriculture", "agri", "农业"),
        "化工": ("chemical", "化工"),
        "电子": ("electronics", "electronic", "电子"),
        "机械": ("machinery", "mechanical", "机械"),
    }
    selected_key = next(
        (label for label, tokens in aliases.items() if any(token in industry for token in tokens)),
        "",
    )
    if not selected_key:
        return service._envelope(
            success=False,
            status="missing_inputs",
            code="industry_constraints_not_available",
            blockers=["industry_specific_constraints_required"],
            next_actions=["提供地方规划证据或显式 technical_fixture 约束后进入规模求解"],
            project_context_id=project_context_id,
            industry_code=project.get("industry_code"),
            matched_industry_key=None,
            supported_industries=sorted(aliases),
            parameters={},
            evidence_eligibility="none",
        )
    parameters = {
        **dict(manifest.get("default") or {}),
        **dict((manifest.get("industry") or {}).get(selected_key) or {}),
    }
    warnings = ["行业参数为规划技术基线，必须由地方规划证据复核后方可用于正式候选"]
    return service._envelope(
        success=True,
        status="ok",
        warnings=warnings,
        project_context_id=project_context_id,
        industry_code=project.get("industry_code"),
        matched_industry_key=selected_key,
        parameters=parameters,
        parameter_version="industry_params.v1",
        parameter_manifest_hash=sha256_json(manifest),
        evidence_eligibility="technical_fixture",
    )


def solve_build_scale(
    workspace_id: str,
    project_context_id: str,
    market_case_id: str,
    alternatives: list[dict[str, Any]],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    request = {
        "project_context_id": project_context_id,
        "market_case_id": market_case_id,
        "alternatives": alternatives,
    }

    def mutate() -> dict[str, Any]:
        context, market, error = service._confirmed_market_basis(
            workspace_id, project_context_id, market_case_id
        )
        if error:
            return error
        assert context is not None and market is not None
        if not 1 <= len(alternatives) <= 20:
            return service._blocked("build_scale_alternatives_invalid", "建设规模方案数量必须为 1..20")
        ids = [str(item.get("candidate_id") or "") for item in alternatives]
        if "" in ids or len(set(ids)) != len(ids):
            return service._blocked("build_scale_candidate_ids_invalid", "建设规模方案 ID 必须非空且唯一")
        solved = []
        for item in alternatives:
            target = service._decimal((item.get("target_capacity") or {}).get("value"))
            land = service._decimal(item.get("land_area_m2"))
            intensity = service._decimal(item.get("capacity_intensity_per_m2"))
            facilities = item.get("facilities") or []
            if not target or not land or not intensity or target <= 0 or land <= 0 or intensity <= 0:
                return service._blocked("build_scale_inputs_invalid", "规模方案目标、用地和产能强度必须大于 0")
            floor = sum((service._decimal(row.get("floor_area_m2")) or Decimal("0")) for row in facilities)
            footprint = sum((service._decimal(row.get("footprint_m2")) or Decimal("0")) for row in facilities)
            constraints = item.get("constraints") or {}
            green = service._decimal(constraints.get("green_area_m2")) or Decimal("0")
            violations = []
            if floor * intensity < target:
                violations.append("capacity_floor_area_insufficient")
            if floor / land > (service._decimal(constraints.get("plot_ratio_max")) or Decimal("-1")):
                violations.append("plot_ratio_constraint_failed")
            if footprint / land > (service._decimal(constraints.get("building_coverage_max")) or Decimal("-1")):
                violations.append("building_coverage_constraint_failed")
            if green / land < (service._decimal(constraints.get("green_ratio_min")) or Decimal("0")):
                violations.append("green_ratio_constraint_failed")
            solved.append({
                **item,
                "feasible": not violations,
                "violations": violations,
                "calculations": {
                    "required_floor_area_m2": float((target / intensity).quantize(Decimal("0.01"))),
                    "facility_floor_area_m2": float(floor),
                    "facility_footprint_m2": float(footprint),
                    "plot_ratio": float((floor / land).quantize(Decimal("0.000001"))),
                    "building_coverage": float((footprint / land).quantize(Decimal("0.000001"))),
                    "green_ratio": float((green / land).quantize(Decimal("0.000001"))),
                    "capacity_margin": float((floor * intensity - target).quantize(Decimal("0.01"))),
                },
            })
        payload = {
            "object_type": "BuildScaleCase",
            "project_context_id": project_context_id,
            "market_case_id": market_case_id,
            "candidates": solved,
            "selection": None,
            "status": "candidate",
            "evidence_track": _payload(context).get("evidence_track", "real"),
            "parent_object_ids": [project_context_id, market_case_id],
        }
        return _put_candidate(
            service.BUILD_SCALE_STORE,
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_solve_build_scale",
            parent_ids=payload["parent_object_ids"],
            basis={"context_basis_hash": context["basis_hash"], "market_basis_hash": market["basis_hash"], **request},
            id_field="build_scale_case_id",
        )

    return service._idempotent_mutation(
        workspace_id,
        operation="planning_solve_build_scale",
        idempotency_key=idempotency_key,
        request_payload=request,
        mutation=mutate,
    )


def validate_build_scale(
    workspace_id: str, build_scale_case_id: str
) -> dict[str, Any]:
    record, error = _candidate(
        service.BUILD_SCALE_STORE, workspace_id, build_scale_case_id, "BuildScaleCase"
    )
    if error:
        return error
    candidates = _payload(record).get("candidates") or []
    feasible_ids = [item["candidate_id"] for item in candidates if item.get("feasible")]
    if not feasible_ids:
        return service._envelope(
            success=False,
            status="blocked",
            code="build_scale_no_feasible_candidate",
            blockers=sorted({code for item in candidates for code in item.get("violations") or []}),
            valid=False,
        )
    return service._envelope(success=True, status="ok", valid=True, feasible_candidate_ids=feasible_ids)


def confirm_build_scale(
    workspace_id: str,
    build_scale_case_id: str,
    selected_candidate_id: str,
    rejected_candidate_ids: list[str],
    selection_reason: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    record, error = _candidate(
        service.BUILD_SCALE_STORE, workspace_id, build_scale_case_id, "BuildScaleCase"
    )
    if error:
        return error
    payload = _payload(record)
    candidates = payload.get("candidates") or []
    selection, error = _selection(
        [item["candidate_id"] for item in candidates], selected_candidate_id,
        rejected_candidate_ids, selection_reason
    )
    if error:
        return error
    selected = next(item for item in candidates if item["candidate_id"] == selected_candidate_id)
    if not selected.get("feasible"):
        return service._blocked("build_scale_candidate_infeasible", "不可确认违反约束的建设规模方案")
    return service.create_build_scale_case(
        workspace_id,
        payload["project_context_id"],
        payload["market_case_id"],
        selected["target_capacity"],
        selected["land_area_m2"],
        selected["capacity_intensity_per_m2"],
        selected["constraints"],
        selected["facilities"],
        parent_candidate_id=build_scale_case_id,
        selection=selection,
        idempotency_key=idempotency_key,
    )


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
        payload = {
            "object_type": "CostDriverSet",
            **request,
            "status": "candidate",
            "evidence_track": _payload(context).get("evidence_track", "real"),
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
            if consumption is None:
                consumption = Decimal("1")
            price = service._decimal(row.get("unit_price_yuan"))
            conversion = service._decimal(row.get("conversion_to_wan"))
            if conversion is None:
                conversion = Decimal("0.0001")
            loss = service._decimal(row.get("loss_rate"))
            if loss is None:
                loss = Decimal("0")
            if (
                quantity is None
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
            "evidence_track": _payload(context).get("evidence_track", "real"),
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
    invalid = [index for index, row in enumerate(positions) if not row.get("headcount") or row.get("avg_wage_yuan") is None]
    if invalid:
        return service._envelope(
            success=False, status="blocked", code="labor_plan_validation_failed",
            blockers=[f"/positions/{index}" for index in invalid], valid=False
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
    if len(str(confirmation_reason or "").strip()) < 10:
        return service._blocked("labor_confirmation_reason_insufficient", "确认理由至少 10 个字符")
    payload = _payload(record)
    return service.create_labor_plan(
        workspace_id, payload["project_context_id"], payload["build_scale_case_id"], payload["positions"],
        parent_candidate_id=labor_plan_id,
        selection={"confirmation_reason": confirmation_reason.strip()}, idempotency_key=idempotency_key
    )


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
