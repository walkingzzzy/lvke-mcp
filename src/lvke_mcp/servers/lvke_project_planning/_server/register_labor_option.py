"""Labor plan, policy basis, option comparison and read-only registration."""

from __future__ import annotations

from mcp import types

from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.domains.project_planning import application as service
from lvke_mcp.servers.lvke_project_planning import lifecycle

from .schema_parts import (
    _KEY,
    _LABOR_REQUIREMENT,
    _OPTION,
    _OPTION_CONSTRAINT,
    _OPTION_CRITERION,
    _OUTPUT,
    _POLICY_CANDIDATE,
    _STRING,
    _schema,
)


def _register_labor(
    server: OfficialStdioServer,
    read: types.ToolAnnotations,
    write: types.ToolAnnotations,
) -> None:
    """Environmental templates and labor plan routes."""

    server.register_tool(
        "planning_get_env_templates",
        "读取环保成本方案所需字段模板；模板不是项目合规结论或正式证据。",
        _schema(
            {
                "project_type": _STRING,
                "pollutant_types": {
                    "type": "array", "minItems": 1, "uniqueItems": True,
                    "items": {"type": "string", "enum": ["wastewater", "waste_gas", "solid_waste", "noise"]}
                },
            },
            ["project_type", "pollutant_types"],
        ),
        lambda a: lifecycle.get_environmental_scheme_templates(
            a["project_type"], a["pollutant_types"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_infer_labor_plan",
        "按工作量、单人班次能力、班次、覆盖和自动化因子确定性推导岗位人数。",
        _schema(
            {
                "project_context_id": _STRING,
                "build_scale_case_id": _STRING,
                "position_requirements": {
                    "type": "array", "minItems": 1, "maxItems": 200, "items": _LABOR_REQUIREMENT
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "build_scale_case_id", "position_requirements", "idempotency_key"],
        ),
        lambda a: lifecycle.infer_labor_plan(
            a["workspace_id"], a["project_context_id"], a["build_scale_case_id"],
            a["position_requirements"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
        public_input_schema=_schema(
            {
                "project_context_id": _STRING,
                "build_scale_case_id": _STRING,
                "position_requirements": {
                    "type": "array", "minItems": 1, "maxItems": 200,
                    "items": _LABOR_REQUIREMENT,
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "build_scale_case_id", "position_requirements", "idempotency_key"],
        ),
    )
    server.register_tool(
        "planning_validate_labor_plan",
        "校验岗位、人数、班次、工资、福利和计算轨迹。",
        _schema({"labor_plan_id": _STRING}, ["labor_plan_id"]),
        lambda a: lifecycle.validate_labor_plan(
            a["workspace_id"], a["labor_plan_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_labor_plan",
        "确认劳动定员候选并生成工资福利 FinanceSpec ledger。",
        _schema(
            {
                "labor_plan_id": _STRING,
                "confirmation_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "idempotency_key": _KEY,
            },
            ["labor_plan_id", "confirmation_reason", "idempotency_key"],
        ),
        lambda a: lifecycle.confirm_labor_plan(
            a["workspace_id"],
            a["labor_plan_id"],
            a.get("confirmation_reason") or a.get("selection_reason") or "",
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )


def _register_policy_option(
    server: OfficialStdioServer,
    read: types.ToolAnnotations,
    write: types.ToolAnnotations,
) -> None:
    """Policy basis and option comparison routes."""

    server.register_tool(
        "planning_prepare_policy_basis",
        "将已固化政策来源分类为适用、待核实、排除或过期候选，不自动选择。",
        _schema(
            {
                "project_context_id": _STRING,
                "candidates": {
                    "type": "array", "minItems": 1, "maxItems": 200, "items": _POLICY_CANDIDATE
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "candidates", "idempotency_key"],
        ),
        lambda a: lifecycle.prepare_policy_basis(
            a["workspace_id"], a["project_context_id"], a["candidates"], idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_confirm_policy_basis",
        "由 Codex 显式选择政策候选并固化 PolicyBasis；过期或排除政策不可选择。",
        _schema(
            {
                "policy_basis_id": _STRING,
                "selected_candidate_ids": {
                    "type": "array", "minItems": 1, "uniqueItems": True, "items": _STRING
                },
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "confirmation_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "idempotency_key": _KEY,
            },
            ["policy_basis_id", "selected_candidate_ids", "selection_reason", "idempotency_key"],
        ),
        lambda a: lifecycle.confirm_policy_basis(
            a["workspace_id"], a["policy_basis_id"], a["selected_candidate_ids"],
            a.get("selection_reason") or a.get("confirmation_reason") or "",
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_prepare_option_comparison",
        "固化设备、建筑、工艺、场址或运营模式候选，按显式权重和方向确定性计分，不自动选择。",
        _schema(
            {
                "project_context_id": _STRING,
                "category": {
                    "type": "string",
                    "enum": ["equipment", "building", "process", "site", "operating_model"],
                },
                "criteria": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": _OPTION_CRITERION,
                },
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 20,
                    "items": _OPTION,
                },
                "mandatory_constraints": {
                    "type": "array",
                    "maxItems": 50,
                    "items": _OPTION_CONSTRAINT,
                    "default": [],
                },
                "basis_object_ids": {
                    "type": "array",
                    "maxItems": 20,
                    "uniqueItems": True,
                    "items": _STRING,
                    "default": [],
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "category", "criteria", "options", "idempotency_key"],
        ),
        lambda a: service.prepare_option_comparison(
            a["workspace_id"],
            a["project_context_id"],
            a["category"],
            a["criteria"],
            a["options"],
            a.get("mandatory_constraints", []),
            a.get("basis_object_ids", []),
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_validate_policy_basis",
        "只读复核政策候选证据绑定、分类与已确认选择的资格，不修改对象。",
        _schema({"policy_basis_id": _STRING}, ["policy_basis_id"]),
        lambda a: lifecycle.validate_policy_basis(
            a["workspace_id"], a["policy_basis_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_validate_option_comparison",
        "校验方案、指标、强制约束和至少一个可行方案。",
        _schema({"option_comparison_id": _STRING}, ["option_comparison_id"]),
        lambda a: lifecycle.validate_option_comparison(
            a["workspace_id"], a["option_comparison_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_score_option_comparison",
        "读取已固化的确定性评分、排名和评分方法，不重新选择方案。",
        _schema({"option_comparison_id": _STRING}, ["option_comparison_id"]),
        lambda a: lifecycle.score_option_comparison(
            a["workspace_id"], a["option_comparison_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_option_comparison",
        "显式选择可行方案并固化确认 revision；与旧 selection 名称共享同一确定性实现。",
        _schema(
            {
                "option_comparison_id": _STRING,
                "selected_option_id": _STRING,
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "confirmation_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "rejected_option_ids": {
                    "type": "array", "minItems": 1, "maxItems": 19,
                    "uniqueItems": True, "items": _STRING
                },
                "idempotency_key": _KEY,
            },
            [
                "option_comparison_id", "selected_option_id", "selection_reason",
                "rejected_option_ids", "idempotency_key"
            ],
        ),
        lambda a: service.confirm_option_selection(
            a["workspace_id"], a["option_comparison_id"], a["selected_option_id"],
            a.get("selection_reason") or a.get("confirmation_reason") or "",
            a["rejected_option_ids"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )


def _register_query(
    server: OfficialStdioServer,
    read: types.ToolAnnotations,
    write: types.ToolAnnotations,
) -> None:
    """Read-only lookup and listing routes."""

    server.register_tool(
        "planning_resolve_industry_skill",
        "根据不可变 ProjectContext 的 industry_code、project_type、transaction_structure 和 asset_type 返回唯一主行业 Skill。",
        _schema(
            {"project_context_id": _STRING},
            ["project_context_id"],
        ),
        lambda a: service.resolve_industry_skill(
            a["workspace_id"],
            a["project_context_id"],
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_get_object",
        "按对象类型读取指定不可变规划对象；直接委托原 JSONArtifactStore，不转换记录。",
        _schema(
            {
                "object_type": {
                    "type": "string",
                    "enum": [
                        "ProjectContext",
                        "InputApplicability",
                        "MarketSizingCase",
                        "RevenueDriverSet",
                        "BuildScaleCase",
                        "CostDriverSet",
                        "LaborPlan",
                        "OptionComparison",
                        "PolicyBasis",
                    ],
                },
                "object_id": _STRING,
            },
            ["object_type", "object_id"],
        ),
        lambda a: service.get_planning_object(
            a["workspace_id"],
            a["object_type"],
            a["object_id"],
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "project_context_list",
        "分页列出当前工作区的 ProjectContext revisions。",
        _schema(
            {
                "cursor": {"type": "string", "default": ""},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            [],
        ),
        lambda a: service.list_project_contexts(
            a["workspace_id"],
            cursor=a.get("cursor", ""),
            limit=int(a.get("limit", 50)),
        ),
        _OUTPUT,
        read,
    )
