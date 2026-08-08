"""lvke-project-planning lifecycle 拆分：建设规模候选生命周期。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from lvke_mcp.domains.project_planning import application as service
from lvke_mcp.runtime.storage import sha256_json

from .base import _candidate, _payload, _put_candidate, _selection


def get_industry_constraints(
    workspace_id: str, project_context_id: str
) -> dict[str, Any]:
    context = service.PROJECT_CONTEXT_STORE.get(
        workspace_id, project_context_id
    )
    if context is None:
        return service._blocked("project_context_not_found", "ProjectContext 不存在")
    path = Path(__file__).resolve().parents[3] / "config" / "industry_params.yaml"
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
        "酒店": ("hotel", "hospitality", "酒店", "宾馆"),
        "能源": ("energy", "power", "solar", "wind", "storage", "能源", "电力", "光伏", "风电", "储能"),
        "文旅": ("cultural", "tourism", "travel", "文旅", "旅游"),
        "房地产": ("real_estate", "real estate", "property", "housing", "房地产", "地产"),
        "基础设施": ("infrastructure", "municipal", "基础设施", "市政"),
        "公共服务": ("public_service", "public service", "government", "公共服务"),
        "矿产加工": ("mineral", "mineral_processing", "mining", "ore", "矿产", "选矿"),
    }
    selected_key = next(
        (label for label, tokens in aliases.items() if any(token in industry for token in tokens)),
        "",
    )
    if not selected_key:
        # 线性交通工程（轨道、铁路、公路）的规模由线路长度、车站数、敷设
        # 方式、车辆段决定，本表的容积率/建筑密度/厂房占比等用地口径参数
        # 对其无意义。宁可诚实报缺，也不返回语义错误的通用参数。
        linear_transport = any(
            token in industry
            for token in (
                "urban_rail", "rail_transit", "railway", "metro", "subway", "highway",
                "轨道", "地铁", "轻轨", "市域铁路", "有轨电车", "铁路", "公路",
            )
        )
        next_actions = ["提供地方规划证据或显式 technical_fixture 约束后进入规模求解"]
        if linear_transport:
            next_actions = [
                "线性交通工程的规模参数（线路长度、车站数、敷设方式、车辆段规模）"
                "不在本表用地口径内，需以工可批复或线网规划证据显式提供",
            ]
        return service._envelope(
            success=False,
            status="missing_inputs",
            code="industry_constraints_not_available",
            blockers=["industry_specific_constraints_required"],
            next_actions=next_actions,
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