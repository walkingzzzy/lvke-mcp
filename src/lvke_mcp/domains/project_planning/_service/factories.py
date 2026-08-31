"""lvke-project-planning application 拆分：对象工厂（revenue / build_scale / cost / labor）。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from lvke_mcp.adapters.project_planning_repository import (
    BUILD_SCALE_STORE,
    COST_DRIVER_STORE,
    LABOR_PLAN_STORE,
    PROJECT_CONTEXT_STORE,
    REVENUE_DRIVER_STORE,
)
from lvke_mcp.runtime.evidence_qualification import (
    declared_evidence_policy,
    project_fact_may_be_certified,
)
from lvke_mcp.runtime.formal_promotion import FormalLineageError
from lvke_mcp.runtime.quality_severity import split_quality_codes

from .base import (
    _blocked,
    _decimal,
    _envelope,
    _idempotent_mutation,
    _planning_evidence_qualification,
    _planning_formal_lineage,
    _planning_view,
)
from .market import _confirmed_market_basis


def create_revenue_driver_set(
    workspace_id: str,
    project_context_id: str,
    market_case_id: str,
    revenue_spec: dict[str, Any],
    op_years: int,
    *,
    mode: str = "estimate_preview",
    flat_evidence_binding: dict[str, Any] | None = None,
    parent_candidate_id: str = "",
    selection: dict[str, Any] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    normalized_spec = dict(revenue_spec or {})
    if str(normalized_spec.get("model") or "") == "tourism":
        from lvke_mcp.domains.finance.revenue_models import normalize_tourism_revenue

        normalized_spec, normalization_errors = normalize_tourism_revenue(normalized_spec)
    else:
        normalization_errors = []
    request_payload = {
        "project_context_id": project_context_id,
        "market_case_id": market_case_id,
        "revenue_spec": normalized_spec,
        "op_years": op_years,
        "mode": mode,
        "flat_evidence_binding": flat_evidence_binding,
        "parent_candidate_id": parent_candidate_id,
        "selection": selection,
    }

    def mutate() -> dict[str, Any]:
        context, market, error = _confirmed_market_basis(
            workspace_id,
            project_context_id,
            market_case_id,
        )
        if error:
            return error
        assert context is not None and market is not None
        if normalization_errors:
            return _envelope(
                success=False,
                status="blocked",
                code="revenue_component_conflict",
                message="文旅收入产品树与兼容字段不一致",
                blockers=["revenue_component_conflict"],
                field_errors={item["path"]: item for item in normalization_errors},
            )
        context_payload = context.get("payload") or {}
        market_payload = market.get("payload") or {}
        evidence_track = str(market_payload.get("evidence_track") or context_payload.get("evidence_track") or "real")
        evidence_policy = declared_evidence_policy(market_payload, default=evidence_track)
        project_fact_certified = project_fact_may_be_certified(
            evidence_policy,
            own_qualification_passed=True,
            parents=[market_payload, context_payload],
        )
        try:
            formal_lineage = _planning_formal_lineage(workspace_id, context, market)
        except FormalLineageError as exc:
            return _blocked(exc.code, exc.message)
        if mode not in {"estimate_preview", "review_candidate"}:
            return _blocked("revenue_mode_invalid", "mode 必须为 estimate_preview 或 review_candidate")
        if not isinstance(op_years, int) or isinstance(op_years, bool) or not 1 <= op_years <= 100:
            return _blocked("revenue_op_years_invalid", "op_years 必须为 1 到 100 的整数")
        model = str(normalized_spec.get("model") or "")
        required_by_model = {
            "product_sales": ("products",),
            "property_sales": ("saleable_area", "price_per_sqm"),
            "tourism": ("annual_visitors", "visitor_unit"),
            "gov_payment": ("annual_gov_payment_wan",),
            "flat": ("annual_revenue_wan",),
        }
        if model not in required_by_model:
            return _blocked("revenue_model_invalid", "收入模型必须为已注册的五种模型之一")
        missing = [
            field
            for field in required_by_model[model]
            if normalized_spec.get(field) in (None, "", [])
        ]
        if missing:
            return _envelope(
                success=False,
                status="missing_inputs",
                code="revenue_driver_missing_inputs",
                message="收入驱动缺少模型必填字段",
                blockers=missing,
                field_errors={f"/revenue_spec/{field}": {"code": "required"} for field in missing},
            )
        quality_issues = list((selection or {}).get("quality_issues") or [])
        if model == "flat" and mode == "review_candidate":
            binding = flat_evidence_binding or {}
            if not all(binding.get(field) for field in ("source_id", "content_hash", "locator")):
                quality_issues.append({
                    "code": "flat_revenue_formal_evidence_required",
                    "path": "/flat_evidence_binding",
                    "blocking": False,
                })
            if str(binding.get("evidence_track") or "") == "source_reconstructed":
                required_reconstruction = (
                    "reconstruction_id", "source_uri", "source_kind", "method", "limitations",
                )
                if any(
                    field not in binding or binding.get(field) in (None, "")
                    for field in required_reconstruction
                ):
                    quality_issues.append({
                        "code": "flat_revenue_reconstruction_binding_incomplete",
                        "path": "/flat_evidence_binding",
                        "blocking": False,
                    })
        from lvke_mcp.domains.finance.revenue_models import expand

        expanded = expand({"revenue": normalized_spec}, op_years)
        revenue_series = list(expanded.get("revenue_by_year") or [])
        if len(revenue_series) != op_years or any(
            _decimal(value) is None or _decimal(value) < 0 for value in revenue_series
        ):
            return _blocked("revenue_expansion_invalid", "收入模型未生成有效的非负逐年序列")
        release_limitations = sorted({
            *[str(item) for item in (selection or {}).get("release_limitations") or []],
            *[str(item.get("code") or "") for item in quality_issues],
        } - {""})
        normalized_selection = {
            **dict(selection or {}),
            "quality_issues": quality_issues,
            "release_limitations": release_limitations,
        }
        payload = {
            "object_type": "RevenueDriverSet",
            "project_context_id": project_context_id,
            "market_case_id": market_case_id,
            "mode": mode,
            "evidence_track": evidence_track,
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_certified,
            "evidence_origin": formal_lineage.get("evidence_origin"),
            "formal_promotion": formal_lineage.get("formal_promotion"),
            "revenue_spec": normalized_spec,
            "op_years": op_years,
            "expanded": expanded,
            "flat_evidence_binding": flat_evidence_binding,
            "finance_spec_ledger": [
                {
                    "target_object_type": "FinanceSpec",
                    "target_pointer": "/revenue",
                    "value": normalized_spec,
                    "source_object_id": market_case_id,
                    "transformation": "lvke_mcp.domains.finance.revenue_models.expand",
                }
            ],
            "status": "confirmed",
            "parent_candidate_id": parent_candidate_id or None,
            "selection": normalized_selection,
            "quality_issues": quality_issues,
            "release_limitations": release_limitations,
            "parent_object_ids": [
                project_context_id,
                market_case_id,
                *([parent_candidate_id] if parent_candidate_id else []),
            ],
            "next_actions": ["将 finance_spec_ledger 交给 finance_prepare_spec，不在 planning 层重算收入"],
        }
        record = REVENUE_DRIVER_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_create_revenue_drivers",
            status="confirmed",
            source_ids=payload["parent_object_ids"],
            basis={
                "context_basis_hash": context["basis_hash"],
                "market_basis_hash": market["basis_hash"],
                "revenue_spec": normalized_spec,
                "op_years": op_years,
                "mode": mode,
            },
        )
        return _envelope(
            success=True,
            status="partial" if quality_issues else "ok",
            blockers=[],
            warnings=["flat 收入证据绑定不完整；收入对象已固化为质量受限候选。"] if quality_issues else [],
            quality_issues=quality_issues,
            release_limitations=release_limitations,
            resource_uris=[record["resource_uri"]],
            next_actions=payload["next_actions"],
            revenue_driver_set_id=record["object_id"],
            object_id=record["object_id"],
            revenue_driver_set=_planning_view(record, "revenue_driver_set_id"),
            finance_spec_ledger=payload["finance_spec_ledger"],
            lineage={"project_context_id": project_context_id, "market_case_id": market_case_id},
            evidence_track=evidence_track,
            evidence_policy=evidence_policy,
            project_fact_certified=project_fact_certified,
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_create_revenue_drivers",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )


def create_build_scale_case(
    workspace_id: str,
    project_context_id: str,
    market_case_id: str,
    target_capacity: dict[str, Any],
    land_area_m2: float,
    capacity_intensity_per_m2: float,
    constraints: dict[str, Any],
    facilities: list[dict[str, Any]],
    *,
    parent_candidate_id: str = "",
    selection: dict[str, Any] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    request_payload = {
        "project_context_id": project_context_id,
        "market_case_id": market_case_id,
        "target_capacity": target_capacity,
        "land_area_m2": land_area_m2,
        "capacity_intensity_per_m2": capacity_intensity_per_m2,
        "constraints": constraints,
        "facilities": facilities,
        "parent_candidate_id": parent_candidate_id,
        "selection": selection,
    }

    def mutate() -> dict[str, Any]:
        context, market, error = _confirmed_market_basis(
            workspace_id, project_context_id, market_case_id
        )
        if error:
            return error
        assert context is not None and market is not None
        evidence_track, evidence_policy, project_fact_certified = (
            _planning_evidence_qualification(context, market)
        )
        try:
            formal_lineage = _planning_formal_lineage(workspace_id, context, market)
        except FormalLineageError as exc:
            return _blocked(exc.code, exc.message)
        target = _decimal(target_capacity.get("value"))
        land = _decimal(land_area_m2)
        intensity = _decimal(capacity_intensity_per_m2)
        if target is None or target <= 0 or land is None or land <= 0 or intensity is None or intensity <= 0:
            return _blocked("build_scale_inputs_invalid", "目标产能、用地和单位面积产能必须大于 0")
        market_selected = (((market.get("payload") or {}).get("selection") or {}).get("selected_candidate") or {})
        market_volume = _decimal(market_selected.get("computed_target_volume"))
        quality_issues: list[dict[str, Any]] = []
        if (
            market_volume is not None
            and str(target_capacity.get("unit") or "") == str(market_selected.get("unit") or "")
            and target > market_volume
        ):
            quality_issues.append({
                "code": "build_capacity_exceeds_selected_market",
                "path": "/target_capacity/value",
                "blocking": False,
            })
        required_floor = (target / intensity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        floor_total = sum((_decimal(item.get("floor_area_m2")) or Decimal("0")) for item in facilities)
        footprint_total = sum((_decimal(item.get("footprint_m2")) or Decimal("0")) for item in facilities)
        plot_ratio = floor_total / land
        coverage = footprint_total / land
        plot_min = _decimal(constraints.get("plot_ratio_min")) or Decimal("0")
        plot_max = _decimal(constraints.get("plot_ratio_max"))
        coverage_max = _decimal(constraints.get("building_coverage_max"))
        green_min = _decimal(constraints.get("green_ratio_min"))
        green_area = _decimal(constraints.get("green_area_m2"))
        failures: list[str] = []
        if floor_total < required_floor:
            failures.append("capacity_floor_area_insufficient")
        if plot_max is None or plot_ratio > plot_max or plot_ratio < plot_min:
            failures.append("plot_ratio_constraint_failed")
        if coverage_max is None or coverage > coverage_max:
            failures.append("building_coverage_constraint_failed")
        if green_min is None or green_area is None or green_area / land < green_min:
            failures.append("green_ratio_constraint_failed")
        quality_issues.extend(
            {"code": code, "blocking": False}
            for code in failures
        )
        calculations = {
            "required_floor_area_m2": float(required_floor),
            "facility_floor_area_m2": float(floor_total),
            "facility_footprint_m2": float(footprint_total),
            "plot_ratio": float(plot_ratio.quantize(Decimal("0.000001"))),
            "building_coverage": float(coverage.quantize(Decimal("0.000001"))),
            "green_ratio": (
                float((green_area / land).quantize(Decimal("0.000001")))
                if green_area is not None
                else None
            ),
            "capacity_margin": float((floor_total * intensity - target).quantize(Decimal("0.01"))),
        }
        existing_selection = dict(selection or {})
        combined_quality_issues = [
            *list(existing_selection.get("quality_issues") or []),
            *quality_issues,
        ]
        normalized_selection = {
            **existing_selection,
            "quality_issues": combined_quality_issues,
            "release_limitations": sorted({
                *[str(item) for item in existing_selection.get("release_limitations") or []],
                *[str(item.get("code") or "") for item in combined_quality_issues],
            } - {""}),
        }
        payload = {
            "object_type": "BuildScaleCase",
            "project_context_id": project_context_id,
            "market_case_id": market_case_id,
            "target_capacity": target_capacity,
            "land_area_m2": float(land),
            "capacity_intensity_per_m2": float(intensity),
            "constraints": constraints,
            "facilities": facilities,
            "calculations": calculations,
            "evidence_track": evidence_track,
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_certified,
            "evidence_origin": formal_lineage.get("evidence_origin"),
            "formal_promotion": formal_lineage.get("formal_promotion"),
            "status": "confirmed",
            "parent_candidate_id": parent_candidate_id or None,
            "selection": normalized_selection,
            "quality_issues": combined_quality_issues,
            "release_limitations": normalized_selection["release_limitations"],
            "parent_object_ids": [
                project_context_id,
                market_case_id,
                *([parent_candidate_id] if parent_candidate_id else []),
            ],
            "planning_conversion_ledger": [
                {"target_pointer": "/build_scale/target_capacity", "value": target_capacity},
                {"target_pointer": "/build_scale/land_area_m2", "value": float(land)},
            ],
        }
        record = BUILD_SCALE_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_create_build_scale",
            status="confirmed",
            source_ids=payload["parent_object_ids"],
            basis={
                "context_basis_hash": context["basis_hash"],
                "market_basis_hash": market["basis_hash"],
                **request_payload,
            },
        )
        # blockers 曾硬编码成 []，feasible 曾只看本次重算的 failures 而忽略
        # 上游传入的 quality_issues —— 于是求解阶段 feasible=false 的候选，
        # 确认后顶层会翻成 true。严重性交给 quality_severity 判定，feasible
        # 必须同时满足"本次约束无违反"和"没有阻断级质量问题"。
        combined_blockers, _ = split_quality_codes(
            str(item.get("code") or "") for item in combined_quality_issues
        )
        return _envelope(
            success=not combined_blockers,
            status="blocked" if combined_blockers else ("partial" if combined_quality_issues else "ok"),
            blockers=combined_blockers,
            warnings=["建设规模约束未完全满足；选择已固化并保留质量诊断。"] if combined_quality_issues else [],
            quality_issues=combined_quality_issues,
            release_limitations=normalized_selection["release_limitations"],
            feasible=not failures and not combined_blockers,
            resource_uris=[record["resource_uri"]],
            next_actions=["基于 BuildScaleCase 编制投资和定员驱动，不把估算规模冒充设计成果"],
            build_scale_case_id=record["object_id"],
            object_id=record["object_id"],
            build_scale_case=_planning_view(record, "build_scale_case_id"),
            calculations=calculations,
            planning_conversion_ledger=payload["planning_conversion_ledger"],
            evidence_track=evidence_track,
            evidence_policy=evidence_policy,
            project_fact_certified=project_fact_certified,
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_create_build_scale",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )


def create_cost_driver_set(
    workspace_id: str,
    project_context_id: str,
    build_scale_case_id: str,
    invest_breakdown: dict[str, Any],
    operating_cost_items: list[dict[str, Any]],
    *,
    parent_candidate_id: str = "",
    selection: dict[str, Any] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    request_payload = {
        "project_context_id": project_context_id,
        "build_scale_case_id": build_scale_case_id,
        "invest_breakdown": invest_breakdown,
        "operating_cost_items": operating_cost_items,
        "parent_candidate_id": parent_candidate_id,
        "selection": selection,
    }

    def mutate() -> dict[str, Any]:
        context = PROJECT_CONTEXT_STORE.get(workspace_id, project_context_id)
        scale = BUILD_SCALE_STORE.get(workspace_id, build_scale_case_id)
        if context is None or scale is None:
            return _blocked("cost_driver_basis_not_found", "ProjectContext 或 BuildScaleCase 不存在")
        if (scale.get("payload") or {}).get("project_context_id") != project_context_id:
            return _blocked("planning_basis_mismatch", "BuildScaleCase 与 ProjectContext 不属于同一 basis")
        evidence_track, evidence_policy, project_fact_certified = (
            _planning_evidence_qualification(context, scale)
        )
        try:
            formal_lineage = _planning_formal_lineage(workspace_id, context, scale)
        except FormalLineageError as exc:
            return _blocked(exc.code, exc.message)
        amount_fields = (
            "construction_wan", "civil_wan", "equipment_wan", "installation_wan",
            "other_wan", "reserve_wan", "interest_wan", "working_capital_wan",
        )
        amounts = {field: _decimal(invest_breakdown.get(field)) for field in amount_fields}
        quality_issues = list((selection or {}).get("quality_issues") or [])
        invalid_amount_fields = [
            field for field, value in amounts.items() if value is None or value < 0
        ]
        quality_issues.extend(
            {
                "code": "investment_breakdown_invalid",
                "path": f"/invest_breakdown/{field}",
                "blocking": False,
            }
            for field in invalid_amount_fields
        )
        construction_fields = (
            "civil_wan", "equipment_wan", "installation_wan", "other_wan", "reserve_wan",
        )
        construction_components = (
            sum((amounts[field] for field in construction_fields), Decimal("0"))
            if all(amounts[field] is not None and amounts[field] >= 0 for field in construction_fields)
            else None
        )
        construction_total = amounts["construction_wan"]
        if (
            construction_total is not None
            and construction_total >= 0
            and construction_components is not None
            and abs(construction_total - construction_components) > Decimal("0.01")
        ):
            quality_issues.append({
                "code": "investment_breakdown_inconsistent",
                "path": "/invest_breakdown/construction_wan",
                "blocking": False,
            })
        cost_items: dict[str, float] = {}
        for index, item in enumerate(operating_cost_items):
            name = str(item.get("name") or "").strip()
            amount = _decimal(item.get("annual_amount_wan"))
            if not name or amount is None or amount < 0:
                quality_issues.append({
                    "code": "operating_cost_item_invalid",
                    "path": f"/operating_cost_items/{index}",
                    "blocking": False,
                })
                continue
            if name in cost_items:
                quality_issues.append({
                    "code": "operating_cost_item_duplicate",
                    "path": f"/operating_cost_items/{index}/name",
                    "blocking": False,
                })
                continue
            cost_items[name] = float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        if len(cost_items) < 3:
            quality_issues.append({
                "code": "operating_cost_detail_insufficient",
                "path": "/operating_cost_items",
                "blocking": False,
            })
        project_total_parts = [
            amounts[field] for field in ("construction_wan", "interest_wan", "working_capital_wan")
        ]
        project_total = (
            sum(project_total_parts, Decimal("0"))
            if all(value is not None and value >= 0 for value in project_total_parts)
            else None
        )
        ledger = [
            {
                "target_object_type": "FinanceInputRevision",
                "target_pointer": "/invest_breakdown",
                "value": invest_breakdown,
            },
            {
                "target_object_type": "FinanceInputRevision",
                "target_pointer": "/cost_items",
                "value": cost_items,
            },
        ]
        release_limitations = sorted({
            *[str(item) for item in (selection or {}).get("release_limitations") or []],
            *[str(item.get("code") or "") for item in quality_issues],
        } - {""})
        normalized_selection = {
            **dict(selection or {}),
            "quality_issues": quality_issues,
            "release_limitations": release_limitations,
        }
        payload = {
            "object_type": "CostDriverSet",
            "project_context_id": project_context_id,
            "build_scale_case_id": build_scale_case_id,
            "invest_breakdown": invest_breakdown,
            "operating_cost_items": operating_cost_items,
            "annual_operating_cost_wan": round(sum(cost_items.values()), 2),
            "project_total_investment_wan": (
                float(project_total.quantize(Decimal("0.01")))
                if project_total is not None
                else None
            ),
            "evidence_track": evidence_track,
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_certified,
            "evidence_origin": formal_lineage.get("evidence_origin"),
            "formal_promotion": formal_lineage.get("formal_promotion"),
            "finance_spec_ledger": ledger,
            "status": "confirmed",
            "parent_candidate_id": parent_candidate_id or None,
            "selection": normalized_selection,
            "quality_issues": quality_issues,
            "release_limitations": release_limitations,
            "parent_object_ids": [
                project_context_id,
                build_scale_case_id,
                *([parent_candidate_id] if parent_candidate_id else []),
            ],
        }
        record = COST_DRIVER_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_create_cost_drivers",
            status="confirmed",
            source_ids=payload["parent_object_ids"],
            basis={"context_basis_hash": context["basis_hash"], **request_payload},
        )
        return _envelope(
            success=True,
            status="partial" if quality_issues else "ok",
            blockers=[],
            warnings=["投资或经营成本明细不完整；对象已固化并保留原始输入。"] if quality_issues else [],
            quality_issues=quality_issues,
            release_limitations=release_limitations,
            resource_uris=[record["resource_uri"]],
            next_actions=["将投资与成本 ledger 合并进 FinanceSpec，并由财务服务重新校验"],
            cost_driver_set_id=record["object_id"],
            object_id=record["object_id"],
            cost_driver_set=_planning_view(record, "cost_driver_set_id"),
            finance_spec_ledger=ledger,
            evidence_track=evidence_track,
            evidence_policy=evidence_policy,
            project_fact_certified=project_fact_certified,
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_create_cost_drivers",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )


def create_labor_plan(
    workspace_id: str,
    project_context_id: str,
    build_scale_case_id: str,
    positions: list[dict[str, Any]],
    *,
    parent_candidate_id: str = "",
    selection: dict[str, Any] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    request_payload = {
        "project_context_id": project_context_id,
        "build_scale_case_id": build_scale_case_id,
        "positions": positions,
        "parent_candidate_id": parent_candidate_id,
        "selection": selection,
    }

    def mutate() -> dict[str, Any]:
        context = PROJECT_CONTEXT_STORE.get(workspace_id, project_context_id)
        scale = BUILD_SCALE_STORE.get(workspace_id, build_scale_case_id)
        if context is None or scale is None:
            return _blocked("labor_plan_basis_not_found", "ProjectContext 或 BuildScaleCase 不存在")
        if (scale.get("payload") or {}).get("project_context_id") != project_context_id:
            return _blocked("planning_basis_mismatch", "BuildScaleCase 与 ProjectContext 不属于同一 basis")
        evidence_track, evidence_policy, project_fact_certified = (
            _planning_evidence_qualification(context, scale)
        )
        try:
            formal_lineage = _planning_formal_lineage(workspace_id, context, scale)
        except FormalLineageError as exc:
            return _blocked(exc.code, exc.message)
        finance_rows: list[dict[str, Any]] = []
        wage_total = Decimal("0")
        welfare_total = Decimal("0")
        headcount_total = 0
        names: set[str] = set()
        quality_issues = list((selection or {}).get("quality_issues") or [])
        for index, item in enumerate(positions):
            name = str(item.get("name") or "").strip()
            category = str(item.get("category") or "").strip()
            headcount = item.get("headcount")
            wage = _decimal(item.get("avg_wage_yuan"))
            welfare_rate = _decimal(item.get("welfare_rate"))
            valid = (
                bool(name) and bool(category) and name not in names
                and isinstance(headcount, int) and not isinstance(headcount, bool) and headcount > 0
                and wage is not None and wage >= 0
                and welfare_rate is not None and Decimal("0") <= welfare_rate <= Decimal("1")
            )
            if not valid:
                quality_issues.append({
                    "code": "labor_position_invalid",
                    "path": f"/positions/{index}",
                    "blocking": False,
                })
                continue
            names.add(name)
            base = Decimal(headcount) * wage / Decimal("10000")
            wage_total += base
            welfare_total += base * welfare_rate
            headcount_total += headcount
            finance_rows.append(
                {
                    "category": category,
                    "name": name,
                    "headcount": headcount,
                    "avg_wage_yuan": float(wage),
                }
            )
        if not finance_rows:
            quality_issues.append({
                "code": "labor_positions_required",
                "path": "/positions",
                "blocking": False,
            })
        wage_total = wage_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        welfare_total = welfare_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ledger = [
            {
                "target_object_type": "FinanceInputRevision",
                "target_pointer": "/labor_plan",
                "value": finance_rows,
            },
            {
                "target_object_type": "FinanceInputRevision",
                "target_pointer": "/cost_items/工资",
                "value": float(wage_total),
            },
            {
                "target_object_type": "FinanceInputRevision",
                "target_pointer": "/cost_items/福利",
                "value": float(welfare_total),
            },
        ]
        release_limitations = sorted({
            *[str(item) for item in (selection or {}).get("release_limitations") or []],
            *[str(item.get("code") or "") for item in quality_issues],
        } - {""})
        normalized_selection = {
            **dict(selection or {}),
            "quality_issues": quality_issues,
            "release_limitations": release_limitations,
        }
        payload = {
            "object_type": "LaborPlan",
            "project_context_id": project_context_id,
            "build_scale_case_id": build_scale_case_id,
            "positions": positions,
            "headcount_total": headcount_total,
            "annual_wage_wan": float(wage_total),
            "annual_welfare_wan": float(welfare_total),
            "evidence_track": evidence_track,
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_certified,
            "evidence_origin": formal_lineage.get("evidence_origin"),
            "formal_promotion": formal_lineage.get("formal_promotion"),
            "finance_spec_ledger": ledger,
            "status": "confirmed",
            "parent_candidate_id": parent_candidate_id or None,
            "selection": normalized_selection,
            "quality_issues": quality_issues,
            "release_limitations": release_limitations,
            "parent_object_ids": [
                project_context_id,
                build_scale_case_id,
                *([parent_candidate_id] if parent_candidate_id else []),
            ],
        }
        record = LABOR_PLAN_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_create_labor_plan",
            status="confirmed",
            source_ids=payload["parent_object_ids"],
            basis={"context_basis_hash": context["basis_hash"], **request_payload},
        )
        return _envelope(
            success=True,
            status="partial" if quality_issues else "ok",
            blockers=[],
            warnings=["人员数量或工资参数不完整；对象已固化并保留原始岗位输入。"] if quality_issues else [],
            quality_issues=quality_issues,
            release_limitations=release_limitations,
            resource_uris=[record["resource_uri"]],
            next_actions=["将 labor_plan 与工资福利 ledger 合并到 CostDriverSet/FinanceSpec，冲突时 fail closed"],
            labor_plan_id=record["object_id"],
            object_id=record["object_id"],
            labor_plan=_planning_view(record, "labor_plan_id"),
            finance_spec_ledger=ledger,
            evidence_track=evidence_track,
            evidence_policy=evidence_policy,
            project_fact_certified=project_fact_certified,
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_create_labor_plan",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )
