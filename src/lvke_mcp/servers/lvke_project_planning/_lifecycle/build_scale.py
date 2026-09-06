"""lvke-project-planning lifecycle 拆分：建设规模候选生命周期。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from lvke_mcp.domains.finance.industry_aliases import normalize_industry
from lvke_mcp.domains.project_planning import application as service
from lvke_mcp.runtime.quality_severity import is_blocking
from lvke_mcp.runtime.storage import sha256_json

from .base import _candidate, _payload, _put_candidate, _selection


def _scale_violations(
    *,
    target: Decimal,
    target_unit: str,
    land: Decimal,
    intensity: Decimal,
    floor: Decimal,
    footprint: Decimal,
    green: Decimal,
    constraints: dict[str, Any],
    market_selected: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    market_volume = service._decimal(market_selected.get("computed_target_volume"))
    market_unit = str(market_selected.get("unit") or "")
    if market_volume is not None:
        if target_unit != market_unit:
            violations.append("market_capacity_unit_mismatch")
        elif target > market_volume:
            violations.append("build_capacity_exceeds_selected_market")
    if floor * intensity < target:
        violations.append("capacity_floor_area_insufficient")
    plot_ratio = floor / land
    plot_min = service._decimal(constraints.get("plot_ratio_min"))
    plot_max = service._decimal(constraints.get("plot_ratio_max"))
    if plot_min is None or plot_max is None or plot_ratio < plot_min or plot_ratio > plot_max:
        violations.append("plot_ratio_constraint_failed")
    coverage_max = service._decimal(constraints.get("building_coverage_max"))
    if coverage_max is None or footprint / land > coverage_max:
        violations.append("building_coverage_constraint_failed")
    green_min = service._decimal(constraints.get("green_ratio_min"))
    if green_min is None or green / land < green_min:
        violations.append("green_ratio_constraint_failed")
    return violations


def _match_field_template(
    manifest: dict[str, Any], industry: str
) -> tuple[str, dict[str, Any]]:
    """Resolve a field-only template for工程 types the land-use table cannot describe."""

    templates = manifest.get("field_templates")
    if not isinstance(templates, dict):
        return "", {}
    for key, template in templates.items():
        if not isinstance(template, dict):
            continue
        tokens = template.get("applies_to_industry_tokens") or []
        if any(str(token).lower() in industry for token in tokens):
            return str(key), template
    return "", {}


def _resolve_industry_key(
    project: dict[str, Any], aliases: dict[str, tuple[str, ...]]
) -> tuple[str, str]:
    """Resolve the planning parameter key from all typed context discriminators."""

    discriminators = (
        project.get("asset_type"),
        project.get("industry_code"),
        project.get("project_type"),
        project.get("transaction_structure"),
        project.get("target_type"),
    )
    normalized_inputs = [
        str(value).strip().lower() for value in discriminators if str(value or "").strip()
    ]
    for value in normalized_inputs:
        normalized = normalize_industry(value)
        if normalized in aliases:
            return normalized, " ".join(normalized_inputs)

    industry = " ".join(normalized_inputs)
    selected_key = next(
        (
            label
            for label, tokens in aliases.items()
            if any(str(token).lower() in industry for token in tokens)
        ),
        "",
    )
    return selected_key, industry


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
    # supported_industries 此前只列中文键，而 industry_code 实际接受的是英文码
    # （machinery → 机械）。照中文清单填"机械"这里能拿到参数，但
    # planning_resolve_industry_skill 的路由表只认英文 prefix，会直接拒绝 ——
    # 清单把调用方引向死路。所以把每个行业的可接受写法一并暴露。
    supported_industry_codes = sorted(
        {token for tokens in aliases.values() for token in tokens} | set(aliases)
    )
    industry_code_map = {key: sorted(tokens) for key, tokens in sorted(aliases.items())}
    selected_key, industry = _resolve_industry_key(project, aliases)
    if not selected_key:
        # 线性交通工程（轨道、铁路、公路）的规模由线路长度、车站数、敷设
        # 方式、车辆段决定，本表的容积率/建筑密度/厂房占比等用地口径参数
        # 对其无意义。此前只能诚实报缺；现在改为返回**字段模板与校验规则**，
        # 仍不给任何默认取值，也不授予正式证据资格。
        template_key, template = _match_field_template(manifest, industry)
        linear_transport = any(
            token in industry
            for token in (
                "urban_rail", "rail_transit", "railway", "metro", "subway", "highway",
                "轨道", "地铁", "轻轨", "市域铁路", "有轨电车", "铁路", "公路",
            )
        )
        if template:
            required = [
                name
                for name, spec in (template.get("fields") or {}).items()
                if isinstance(spec, dict) and spec.get("required")
            ]
            return service._envelope(
                success=True,
                status="missing_inputs",
                code="industry_field_template_only",
                warnings=[
                    "返回的是字段模板与校验规则，不含任何参数取值；"
                    "模板本身不构成正式证据，须由工可批复或线网规划证据逐项填充",
                ],
                blockers=[],
                quality_issues=["industry_specific_constraints_required"],
                next_actions=[
                    "按 field_template 逐项提供线路长度、车站数、敷设比例、车辆段、"
                    "设计速度、编组与行车间隔，并附工可批复或线网规划 locator",
                ],
                project_context_id=project_context_id,
                industry_code=project.get("industry_code"),
                matched_industry_key=None,
                matched_field_template_key=template_key,
                supported_industries=sorted(aliases),
                supported_industry_codes=supported_industry_codes,
                industry_code_map=industry_code_map,
                supported_field_templates=sorted(
                    (manifest.get("field_templates") or {}).keys()
                ),
                parameters={},
                field_template=template,
                field_template_version=str(template.get("template_version") or ""),
                required_fields=sorted(required),
                validation_rules=list(template.get("validation_rules") or []),
                parameter_manifest_hash=sha256_json(manifest),
                evidence_eligibility="field_template_only",
            )
        next_actions = ["提供地方规划证据或显式 technical_fixture 约束后进入规模求解"]
        if linear_transport:
            next_actions = [
                "线性交通工程的规模参数（线路长度、车站数、敷设方式、车辆段规模）"
                "不在本表用地口径内，需以工可批复或线网规划证据显式提供",
            ]
        return service._envelope(
            success=True,
            status="missing_inputs",
            code="industry_constraints_not_available",
            blockers=[],
            quality_issues=["industry_specific_constraints_required"],
            next_actions=next_actions,
            project_context_id=project_context_id,
            industry_code=project.get("industry_code"),
            matched_industry_key=None,
            supported_industries=sorted(aliases),
            supported_industry_codes=supported_industry_codes,
            industry_code_map=industry_code_map,
            supported_field_templates=sorted(
                (manifest.get("field_templates") or {}).keys()
            ),
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
        market_selected = (
            ((_payload(market).get("selection") or {}).get("selected_candidate") or {})
        )
        evidence_track, evidence_policy, project_fact_certified = (
            service._planning_evidence_qualification(context, market)
        )
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
            violations = _scale_violations(
                target=target,
                target_unit=str((item.get("target_capacity") or {}).get("unit") or ""),
                land=land,
                intensity=intensity,
                floor=floor,
                footprint=footprint,
                green=green,
                constraints=constraints,
                market_selected=market_selected,
            )
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
            "evidence_track": evidence_track,
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_certified,
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
        violations = sorted({
            code for item in candidates for code in item.get("violations") or []
        })
        return service._envelope(
            success=True,
            status="partial",
            code="build_scale_no_feasible_candidate",
            blockers=[],
            warnings=["当前无满足全部约束的建设规模候选；候选仍可选择并固化。"],
            quality_issues=[
                {"code": code, "blocking": False}
                for code in (violations or ["build_scale_no_feasible_candidate"])
            ],
            valid=False,
            feasible_candidate_ids=[],
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
    quality_issues = []
    if not selected.get("feasible"):
        # 严重性一律交给 quality_severity 判定。此处曾把 blocking 硬编码成
        # False，于是"市场单位与产能单位不相容"这类口径非法问题被降级成随件
        # 披露的限制项，确认后顶层 feasible 还从 false 翻成 true。
        quality_issues = [
            {
                "code": str(code),
                "blocking": is_blocking(str(code)),
                "candidate_id": selected_candidate_id,
            }
            for code in (selected.get("violations") or ["build_scale_candidate_infeasible"])
        ]
    selection = {
        **selection,
        "quality_issues": quality_issues,
        "release_limitations": [item["code"] for item in quality_issues],
    }
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
