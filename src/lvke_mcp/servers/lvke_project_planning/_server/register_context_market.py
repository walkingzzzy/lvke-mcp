"""ProjectContext, market case and revenue driver tool registration."""

from __future__ import annotations

from mcp import types

from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.domains.project_planning import application as service
from lvke_mcp.servers.lvke_project_planning import lifecycle

from .schema_parts import (
    _CONTEXT,
    _KEY,
    _MARKET_CANDIDATE,
    _OUTPUT,
    _REVENUE_CANDIDATE,
    _STRING,
    _schema,
)


def _register_context(
    server: OfficialStdioServer,
    read: types.ToolAnnotations,
    write: types.ToolAnnotations,
) -> None:
    """ProjectContext create/validate/revise."""

    server.register_tool(
        "project_context_create",
        "创建不可变 ProjectContext 草稿；身份由 MCP 宿主绑定，重复请求由幂等键保护。",
        _schema(
            {
                "context": _CONTEXT,
                "idempotency_key": _KEY,
            },
            ["context", "idempotency_key"],
        ),
        lambda a: service.create_project_context(
            a["workspace_id"],
            a["context"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "project_context_validate",
        "校验 ProjectContext 并固化 InputApplicability；资料缺失返回精确字段，不补默认值。",
        _schema(
            {
                "project_context_id": _STRING,
                "idempotency_key": _KEY,
            },
            ["project_context_id", "idempotency_key"],
        ),
        lambda a: service.validate_project_context(
            a["workspace_id"],
            a["project_context_id"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "project_context_revise",
        "基于 expected_basis_hash 创建新 ProjectContext revision，并返回下游 stale 清单。",
        _schema(
            {
                "project_context_id": _STRING,
                "expected_basis_hash": {
                    "type": "string",
                    "pattern": r"^sha256:[0-9a-f]{64}$",
                },
                "patch": _CONTEXT,
                "idempotency_key": _KEY,
            },
            [
                "project_context_id",
                "expected_basis_hash",
                "patch",
                "idempotency_key",
            ],
        ),
        lambda a: service.revise_project_context(
            a["workspace_id"],
            a["project_context_id"],
            a["expected_basis_hash"],
            a["patch"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )


def _register_market(
    server: OfficialStdioServer,
    read: types.ToolAnnotations,
    write: types.ToolAnnotations,
) -> None:
    """Market case prepare/compare/validate/confirm."""

    server.register_tool(
        "planning_prepare_market_case",
        "基于不可变 ProjectContext、EvidencePack 和显式口径创建多路径市场案例；不自动选择或平均。",
        _schema(
            {
                "project_context_id": _STRING,
                "evidence_pack_id": _STRING,
                "candidates": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": _MARKET_CANDIDATE,
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "evidence_pack_id", "candidates", "idempotency_key"],
        ),
        lambda a: service.prepare_market_case(
            a["workspace_id"],
            a["project_context_id"],
            a["evidence_pack_id"],
            a["candidates"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_compare_market_cases",
        "逐对比较市场路径的期间、地区、单位和目标量偏差；明确返回 aggregation=none。",
        _schema({"market_case_id": _STRING}, ["market_case_id"]),
        lambda a: service.compare_market_cases(
            a["workspace_id"], a["market_case_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_validate_market_case",
        "校验市场案例的多路径、口径、份额算术和 evidence locator，搜索摘要会被拒绝。",
        _schema({"market_case_id": _STRING}, ["market_case_id"]),
        lambda a: service.validate_market_case(
            a["workspace_id"], a["market_case_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_market_case",
        "由 Codex 显式选择一个市场路径、说明理由并列出全部舍弃候选，固化不可变 revision。",
        _schema(
            {
                "market_case_id": _STRING,
                "selected_candidate_id": _STRING,
                "selection_reason": {**_STRING, "maxLength": 10000},
                "confirmation_reason": {**_STRING, "maxLength": 10000},
                "rejected_candidate_ids": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": _STRING,
                },
                "supersedes_market_case_id": {"type": "string", "default": ""},
                "expected_basis_hash": {
                    "type": "string",
                    "pattern": r"^(?:|sha256:[0-9a-f]{64})$",
                    "default": "",
                },
                "idempotency_key": _KEY,
            },
            [
                "market_case_id",
                "selected_candidate_id",
                "selection_reason",
                "rejected_candidate_ids",
                "idempotency_key",
            ],
        ),
        lambda a: service.confirm_market_case(
            a["workspace_id"],
            a["market_case_id"],
            a["selected_candidate_id"],
            a.get("selection_reason") or a.get("confirmation_reason") or "",
            a["rejected_candidate_ids"],
            idempotency_key=a["idempotency_key"],
            supersedes_market_case_id=a.get("supersedes_market_case_id", ""),
            expected_basis_hash=a.get("expected_basis_hash", ""),
        ),
        _OUTPUT,
        write,
    )


def _register_revenue(
    server: OfficialStdioServer,
    read: types.ToolAnnotations,
    write: types.ToolAnnotations,
) -> None:
    """Revenue driver prepare/compare/validate/confirm."""

    server.register_tool(
        "planning_prepare_revenue_drivers",
        "从已确认市场案例固化一个或多个收入驱动候选；候选不自动选择或平均。",
        _schema(
            {
                "project_context_id": _STRING,
                "market_case_id": _STRING,
                "candidates": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": _REVENUE_CANDIDATE,
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "market_case_id", "candidates", "idempotency_key"],
        ),
        lambda a: lifecycle.prepare_revenue_drivers(
            a["workspace_id"], a["project_context_id"], a["market_case_id"], a["candidates"], idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_compare_revenue_candidates",
        "比较候选逐年收入差异；不合并候选、不计算平均值。",
        _schema({"revenue_driver_set_id": _STRING}, ["revenue_driver_set_id"]),
        lambda a: lifecycle.compare_revenue_candidates(
            a["workspace_id"], a["revenue_driver_set_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_validate_revenue_drivers",
        "校验收入模型、逐年曲线和 flat 正式证据门禁。",
        _schema({"revenue_driver_set_id": _STRING}, ["revenue_driver_set_id"]),
        lambda a: lifecycle.validate_revenue_drivers(
            a["workspace_id"], a["revenue_driver_set_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_revenue_drivers",
        "显式选择收入候选及舍弃项，生成不可变 confirmed RevenueDriverSet。",
        _schema(
            {
                "revenue_driver_set_id": _STRING,
                "selected_candidate_id": _STRING,
                "rejected_candidate_ids": {
                    "type": "array", "uniqueItems": True, "items": _STRING
                },
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "confirmation_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "idempotency_key": _KEY,
            },
            [
                "revenue_driver_set_id", "selected_candidate_id",
                "rejected_candidate_ids", "selection_reason", "idempotency_key"
            ],
        ),
        lambda a: lifecycle.confirm_revenue_drivers(
            a["workspace_id"], a["revenue_driver_set_id"], a["selected_candidate_id"],
            a["rejected_candidate_ids"],
            a.get("selection_reason") or a.get("confirmation_reason") or "",
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
