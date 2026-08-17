from __future__ import annotations

import uuid

from lvke_mcp.domains.project_planning import application as planning
from lvke_mcp.servers.lvke_project_planning._lifecycle.build_scale import (
    confirm_build_scale,
    validate_build_scale,
)
from lvke_mcp.servers.lvke_project_planning._lifecycle.cost import (
    confirm_cost_drivers,
    validate_cost_drivers,
)
from lvke_mcp.servers.lvke_project_planning._lifecycle.labor import (
    confirm_labor_plan,
    validate_labor_plan,
)
from lvke_mcp.servers.lvke_project_planning._lifecycle.revenue import (
    confirm_revenue_drivers,
    validate_revenue_drivers,
)


def _put(store, workspace_id: str, payload: dict, *, status: str = "candidate") -> str:
    record = store.put(
        workspace_id,
        payload,
        producer="tests.project-planning-nonblocking",
        status=status,
        basis={"test_case": payload["object_type"], "nonce": uuid.uuid4().hex},
    )
    return str(record["object_id"])


def _basis(workspace_id: str) -> tuple[str, str, str]:
    context_id = _put(
        planning.PROJECT_CONTEXT_STORE,
        workspace_id,
        {
            "object_type": "ProjectContext",
            "project_name": "质量门禁回归项目",
            "industry_code": "tourism",
            "project_type": "new_build",
            "status": "confirmed",
            "evidence_track": "controlled_assumption",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
        },
        status="confirmed",
    )
    market_id = _put(
        planning.MARKET_CASE_STORE,
        workspace_id,
        {
            "object_type": "MarketSizingCase",
            "project_context_id": context_id,
            "status": "confirmed",
            "selection": {
                "selected_candidate": {
                    "candidate_id": "market-base",
                    "computed_target_volume": 100.0,
                    "unit": "万人次/年",
                }
            },
            "evidence_track": "controlled_assumption",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
        },
        status="confirmed",
    )
    scale_id = _put(
        planning.BUILD_SCALE_STORE,
        workspace_id,
        {
            "object_type": "BuildScaleCase",
            "project_context_id": context_id,
            "market_case_id": market_id,
            "target_capacity": {"value": 150.0, "unit": "万人次/年"},
            "land_area_m2": 1000.0,
            "capacity_intensity_per_m2": 0.01,
            "constraints": {
                "plot_ratio_min": 1.0,
                "plot_ratio_max": 1.2,
                "building_coverage_max": 0.4,
                "green_ratio_min": 0.3,
                "green_area_m2": 0.0,
            },
            "facilities": [{"floor_area_m2": 100.0, "footprint_m2": 100.0}],
            "calculations": {},
            "status": "confirmed",
            "evidence_track": "controlled_assumption",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
        },
        status="confirmed",
    )
    return context_id, market_id, scale_id


def test_infeasible_build_scale_is_confirmed_with_diagnostics() -> None:
    workspace_id = "planning-gateless-" + uuid.uuid4().hex
    context_id, market_id, _ = _basis(workspace_id)
    candidate_id = _put(
        planning.BUILD_SCALE_STORE,
        workspace_id,
        {
            "object_type": "BuildScaleCase",
            "project_context_id": context_id,
            "market_case_id": market_id,
            "candidates": [{
                "candidate_id": "scale-infeasible",
                "target_capacity": {"value": 150.0, "unit": "万人次/年"},
                "land_area_m2": 1000.0,
                "capacity_intensity_per_m2": 0.01,
                "constraints": {
                    "plot_ratio_min": 1.0,
                    "plot_ratio_max": 1.2,
                    "building_coverage_max": 0.4,
                    "green_ratio_min": 0.3,
                    "green_area_m2": 0.0,
                },
                "facilities": [{"floor_area_m2": 100.0, "footprint_m2": 100.0}],
                "feasible": False,
                "violations": [
                    "build_capacity_exceeds_selected_market",
                    "capacity_floor_area_insufficient",
                    "plot_ratio_constraint_failed",
                    "green_ratio_constraint_failed",
                ],
            }],
            "status": "candidate",
        },
    )

    checked = validate_build_scale(workspace_id, candidate_id)
    confirmed = confirm_build_scale(
        workspace_id,
        candidate_id,
        "scale-infeasible",
        [],
        "短理由",
        idempotency_key="confirm-infeasible-scale",
    )

    assert checked["success"] is True
    assert checked["valid"] is False
    assert checked["blockers"] == []
    assert confirmed["success"] is True
    assert confirmed["status"] == "partial"
    assert confirmed["blockers"] == []
    assert confirmed["build_scale_case_id"]
    assert confirmed["feasible"] is False
    assert confirmed["quality_issues"]


def test_incomplete_cost_and_empty_labor_are_confirmed() -> None:
    workspace_id = "planning-gateless-" + uuid.uuid4().hex
    context_id, _, scale_id = _basis(workspace_id)
    cost_id = _put(
        planning.COST_DRIVER_STORE,
        workspace_id,
        {
            "object_type": "CostDriverSet",
            "project_context_id": context_id,
            "build_scale_case_id": scale_id,
            "invest_breakdown": {"construction_wan": 100.0},
            "operating_cost_items": [{"name": "人工"}],
            "status": "candidate",
        },
    )
    labor_id = _put(
        planning.LABOR_PLAN_STORE,
        workspace_id,
        {
            "object_type": "LaborPlan",
            "project_context_id": context_id,
            "build_scale_case_id": scale_id,
            "positions": [],
            "status": "candidate",
        },
    )

    cost_check = validate_cost_drivers(workspace_id, cost_id)
    cost_confirmed = confirm_cost_drivers(
        workspace_id,
        cost_id,
        "短",
        idempotency_key="confirm-incomplete-cost",
    )
    labor_check = validate_labor_plan(workspace_id, labor_id)
    labor_confirmed = confirm_labor_plan(
        workspace_id,
        labor_id,
        "短",
        idempotency_key="confirm-empty-labor",
    )

    for checked in (cost_check, labor_check):
        assert checked["success"] is True
        assert checked["valid"] is False
        assert checked["status"] == "partial"
        assert checked["blockers"] == []
    for confirmed, id_field in (
        (cost_confirmed, "cost_driver_set_id"),
        (labor_confirmed, "labor_plan_id"),
    ):
        assert confirmed["success"] is True
        assert confirmed["status"] == "partial"
        assert confirmed["blockers"] == []
        assert confirmed[id_field]
        assert confirmed["quality_issues"]


def test_flat_revenue_missing_evidence_is_confirmed() -> None:
    workspace_id = "planning-gateless-" + uuid.uuid4().hex
    context_id, market_id, _ = _basis(workspace_id)
    candidate_id = _put(
        planning.REVENUE_DRIVER_STORE,
        workspace_id,
        {
            "object_type": "RevenueDriverSet",
            "project_context_id": context_id,
            "market_case_id": market_id,
            "candidates": [{
                "candidate_id": "flat-review",
                "revenue_spec": {"model": "flat", "annual_revenue_wan": 100.0},
                "op_years": 5,
                "mode": "review_candidate",
                "flat_evidence_binding": {},
            }],
            "status": "candidate",
        },
    )

    checked = validate_revenue_drivers(workspace_id, candidate_id)
    confirmed = confirm_revenue_drivers(
        workspace_id,
        candidate_id,
        "flat-review",
        [],
        "按当前资料先固化候选",
        idempotency_key="confirm-flat-revenue",
    )

    assert checked["success"] is True
    assert checked["valid"] is False
    assert checked["blockers"] == []
    assert confirmed["success"] is True
    assert confirmed["status"] == "partial"
    assert confirmed["blockers"] == []
    assert confirmed["revenue_driver_set_id"]
    assert confirmed["quality_issues"]