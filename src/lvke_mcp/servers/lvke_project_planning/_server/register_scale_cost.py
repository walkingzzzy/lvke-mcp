"""Build-scale, direct object creation and cost driver tool registration."""

from __future__ import annotations

from mcp import types

from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.domains.project_planning import application as service
from lvke_mcp.servers.lvke_project_planning import lifecycle

from .schema_parts import (
    _BUILD_ALTERNATIVE,
    _BUILD_CONSTRAINTS,
    _COST_CANDIDATE_ITEM,
    _EVIDENCE_BINDING,
    _FACILITY,
    _INVEST_BREAKDOWN,
    _KEY,
    _OPERATING_COST_ITEM,
    _OUTPUT,
    _POSITION,
    _REVENUE_SPEC,
    _STRING,
    _TARGET_CAPACITY,
    _schema,
)


def _register_build_scale(
    server: OfficialStdioServer,
    read: types.ToolAnnotations,
    write: types.ToolAnnotations,
) -> None:
    """Build-scale constraints/solve/validate/confirm."""

    server.register_tool(
        "planning_get_industry_constraints",
        "读取 ProjectContext 对应的版本化行业规划技术参数；参数不自动取得正式证据资格。",
        _schema({"project_context_id": _STRING}, ["project_context_id"]),
        lambda a: lifecycle.get_industry_constraints(
            a["workspace_id"], a["project_context_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_solve_build_scale",
        "对多个规模方案确定性计算产能、用地、容积率、密度和绿地约束。",
        _schema(
            {
                "project_context_id": _STRING,
                "market_case_id": _STRING,
                "alternatives": {
                    "type": "array", "minItems": 1, "maxItems": 20, "items": _BUILD_ALTERNATIVE
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "market_case_id", "alternatives", "idempotency_key"],
        ),
        lambda a: lifecycle.solve_build_scale(
            a["workspace_id"], a["project_context_id"], a["market_case_id"], a["alternatives"], idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_validate_build_scale",
        "校验规模候选的产能、用地和规划约束，至少要求一个可行候选。",
        _schema({"build_scale_case_id": _STRING}, ["build_scale_case_id"]),
        lambda a: lifecycle.validate_build_scale(
            a["workspace_id"], a["build_scale_case_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_build_scale",
        "显式选择一个可行规模方案并固化不可变 BuildScaleCase。",
        _schema(
            {
                "build_scale_case_id": _STRING,
                "selected_candidate_id": _STRING,
                "rejected_candidate_ids": {"type": "array", "uniqueItems": True, "items": _STRING},
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "confirmation_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "idempotency_key": _KEY,
            },
            [
                "build_scale_case_id", "selected_candidate_id", "rejected_candidate_ids",
                "selection_reason", "idempotency_key"
            ],
        ),
        lambda a: lifecycle.confirm_build_scale(
            a["workspace_id"], a["build_scale_case_id"], a["selected_candidate_id"],
            a["rejected_candidate_ids"],
            a.get("selection_reason") or a.get("confirmation_reason") or "",
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )


def _register_direct_create(
    server: OfficialStdioServer,
    read: types.ToolAnnotations,
    write: types.ToolAnnotations,
) -> None:
    """Direct immutable object creation routes."""

    server.register_tool(
        "planning_create_revenue_drivers",
        "创建不可变 RevenueDriverSet；复用财务收入展开器，不在 planning 层复制收入公式。",
        _schema(
            {
                "project_context_id": _STRING,
                "market_case_id": _STRING,
                "revenue_spec": _REVENUE_SPEC,
                "op_years": {"type": "integer", "minimum": 1, "maximum": 100},
                "mode": {
                    "type": "string",
                    "enum": ["estimate_preview", "review_candidate"],
                    "default": "estimate_preview",
                },
                "flat_evidence_binding": _EVIDENCE_BINDING,
                "idempotency_key": _KEY,
            },
            [
                "project_context_id",
                "market_case_id",
                "revenue_spec",
                "op_years",
                "idempotency_key",
            ],
        ),
        lambda a: service.create_revenue_driver_set(
            a["workspace_id"],
            a["project_context_id"],
            a["market_case_id"],
            a["revenue_spec"],
            a["op_years"],
            mode=a.get("mode", "estimate_preview"),
            flat_evidence_binding=a.get("flat_evidence_binding"),
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_create_build_scale",
        "以目标产能、用地、单位面积产能和规划约束确定性创建 BuildScaleCase。",
        _schema(
            {
                "project_context_id": _STRING,
                "market_case_id": _STRING,
                "target_capacity": _TARGET_CAPACITY,
                "land_area_m2": {"type": "number", "exclusiveMinimum": 0},
                "capacity_intensity_per_m2": {"type": "number", "exclusiveMinimum": 0},
                "constraints": _BUILD_CONSTRAINTS,
                "facilities": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 200,
                    "items": _FACILITY,
                },
                "idempotency_key": _KEY,
            },
            [
                "project_context_id",
                "market_case_id",
                "target_capacity",
                "land_area_m2",
                "capacity_intensity_per_m2",
                "constraints",
                "facilities",
                "idempotency_key",
            ],
        ),
        lambda a: service.create_build_scale_case(
            a["workspace_id"],
            a["project_context_id"],
            a["market_case_id"],
            a["target_capacity"],
            a["land_area_m2"],
            a["capacity_intensity_per_m2"],
            a["constraints"],
            a["facilities"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_create_cost_drivers",
        "创建投资三段式和现金经营成本驱动；建设投资不闭合或成本明细不足时阻断。",
        _schema(
            {
                "project_context_id": _STRING,
                "build_scale_case_id": _STRING,
                "invest_breakdown": _INVEST_BREAKDOWN,
                "operating_cost_items": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 200,
                    "items": _OPERATING_COST_ITEM,
                },
                "idempotency_key": _KEY,
            },
            [
                "project_context_id",
                "build_scale_case_id",
                "invest_breakdown",
                "operating_cost_items",
                "idempotency_key",
            ],
        ),
        lambda a: service.create_cost_driver_set(
            a["workspace_id"],
            a["project_context_id"],
            a["build_scale_case_id"],
            a["invest_breakdown"],
            a["operating_cost_items"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_create_labor_plan",
        "按岗位、人数、人均工资和福利率创建可复算 LaborPlan，不使用行业默认值补齐。",
        _schema(
            {
                "project_context_id": _STRING,
                "build_scale_case_id": _STRING,
                "positions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 200,
                    "items": _POSITION,
                },
                "idempotency_key": _KEY,
            },
            [
                "project_context_id",
                "build_scale_case_id",
                "positions",
                "idempotency_key",
            ],
        ),
        lambda a: service.create_labor_plan(
            a["workspace_id"],
            a["project_context_id"],
            a["build_scale_case_id"],
            a["positions"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )


def _register_cost(
    server: OfficialStdioServer,
    read: types.ToolAnnotations,
    write: types.ToolAnnotations,
) -> None:
    """Cost driver prepare/calculate/validate/confirm."""

    server.register_tool(
        "planning_prepare_cost_drivers",
        "固化投资和经营成本候选；数量、单耗和单价可在后续确定性计算中展开。",
        _schema(
            {
                "project_context_id": _STRING,
                "build_scale_case_id": _STRING,
                "invest_breakdown": _INVEST_BREAKDOWN,
                "operating_cost_items": {
                    "type": "array", "minItems": 1, "maxItems": 200, "items": _COST_CANDIDATE_ITEM
                },
                "idempotency_key": _KEY,
            },
            [
                "project_context_id", "build_scale_case_id", "invest_breakdown",
                "operating_cost_items", "idempotency_key"
            ],
        ),
        lambda a: lifecycle.prepare_cost_drivers(
            a["workspace_id"], a["project_context_id"], a["build_scale_case_id"],
            a["invest_breakdown"], a["operating_cost_items"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_calculate_cost_drivers",
        "按数量×单耗×单价×换算系数×损耗率计算成本并固化新候选 revision。",
        _schema(
            {"cost_driver_set_id": _STRING, "idempotency_key": _KEY},
            ["cost_driver_set_id", "idempotency_key"],
        ),
        lambda a: lifecycle.calculate_cost_drivers(
            a["workspace_id"], a["cost_driver_set_id"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_validate_cost_drivers",
        "校验投资闭合、经营成本可复算性和明细完整性。",
        _schema({"cost_driver_set_id": _STRING}, ["cost_driver_set_id"]),
        lambda a: lifecycle.validate_cost_drivers(
            a["workspace_id"], a["cost_driver_set_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_cost_drivers",
        "确认已计算成本候选并生成 FinanceSpec 转换 ledger。",
        _schema(
            {
                "cost_driver_set_id": _STRING,
                "confirmation_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "idempotency_key": _KEY,
            },
            ["cost_driver_set_id", "confirmation_reason", "idempotency_key"],
        ),
        lambda a: lifecycle.confirm_cost_drivers(
            a["workspace_id"],
            a["cost_driver_set_id"],
            a.get("confirmation_reason") or a.get("selection_reason") or "",
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
