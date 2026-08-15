"""lvke-deep-research 工具注册（build_server 的注册循环拆出）。"""

from __future__ import annotations

from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.domains.research import application as package_service

from .schemas import (
    _PREPARE_OUTPUT,
    _START_OUTPUT,
    _CONTINUE_OUTPUT,
    _STATUS_OUTPUT,
    _CANCEL_OUTPUT,
    _REPORT_OUTPUT,
    _EVIDENCE_OUTPUT,
    _BUNDLE_OUTPUT,
    _QUALITY_CONFIRM_OUTPUT,
    _PLAN_OUTPUT,
    _EVENT_OUTPUT,
    _CHECKPOINT_OUTPUT,
    _RESUME_OUTPUT,
    _MIXED_SOURCE_DESCRIPTOR,
)
from .tool_annotations import _read_only, _agent_write, _cancel, _bundle_write
from .dispatch import _tool_dr_start, _tool_dr_status, _tool_dr_cancel, _tool_dr_get_report, _tool_dr_get_evidence


def register_all(server: OfficialStdioServer) -> None:
    """把 18 个 deep research 工具注册到传入的 server 实例。"""

    _ws_schema = {"type": "string", "description": "工作区 ID（必填）"}
    _task_schema = {"type": "string", "description": "dr_start 返回的 task_id"}
    _basis_schema = {
        "type": "string",
        "pattern": r"^sha256:[0-9a-f]{64}$",
        "description": "dr_get_plan 返回的当前不可变 basis_hash",
    }

    server.register_tool(
        name="dr_prepare",
        description="准备研究 brief、子问题、澄清项、预算和预期交付物；不启动网络消耗。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "topic": {"type": "string", "minLength": 1},
                "industry": {"type": "string"}, "region": {"type": "string"},
                "objective": {"type": "string"},
                "known_materials": {"type": "array", "items": {"type": "string"}},
                "profile": {"type": "string", "enum": ["quick", "deep", "deep_assist", "deep_standard", "deep_max"], "default": "deep_standard"},
                "budget": {"type": "object"},
            },
            "required": ["workspace_id", "topic"],
        },
        handler=package_service.prepare,
        output_schema=_PREPARE_OUTPUT,
        annotations=_read_only,
    )

    server.register_tool(
        name="dr_start",
        description=(
            "建立由当前 Agent 执行的 DR 会话；MCP 不配置或调用内置 LLM。"
            "Agent 用数据采集/分析工具取得依据后，通过 dr_submit 固化研究。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "topic": {"type": "string", "description": "研究主题（必填）"},
                "industry": {"type": "string"},
                "region": {"type": "string"},
                "subqueries": {"type": "array", "items": {"type": "string"}},
                "chapters": {"type": "array", "items": {"type": "string"}},
                "profile": {"type": "string", "enum": ["quick", "deep", "deep_assist", "deep_standard", "deep_max"], "default": "quick"},
                "verify_urls": {"type": "boolean", "default": False},
                "research_brief": {"type": "object"},
                "budget": {"type": "object"},
                "source_policy": {"type": "object"},
                "output_contract": {"type": "object"},
                "analysis_inputs": {"type": "array", "items": {"type": "object"}},
                "plan_items": {"type": "array", "items": {"type": "object"}},
                "idempotency_key": {"type": "string", "description": "同一研究意图幂等键，避免重复烧预算"},
            },
            "required": ["workspace_id", "topic"],
        },
        handler=_tool_dr_start,
        output_schema=_START_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_get_plan",
        description="读取任务的当前或指定不可变 ResearchPlanRevision。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "plan_revision_id": {"type": "string"},
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=lambda args: package_service.get_plan(
            args["workspace_id"],
            args["task_id"],
            plan_revision_id=str(args.get("plan_revision_id") or ""),
        ),
        output_schema=_PLAN_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_propose_plan_revision",
        description="创建研究计划修订提案；提案不会改写当前 revision。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "expected_basis_hash": _basis_schema,
                "changes": {
                    "type": "object",
                    "additionalProperties": False,
                    "minProperties": 1,
                    "properties": {
                        "research_brief": {"type": "object"},
                        "plan_items": {"type": "array", "items": {"type": "object"}},
                        "budget": {"type": "object"},
                        "quality_state": {"type": "object"},
                        "pending_work": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "reason": {"type": "string", "maxLength": 2000},
            },
            "required": ["workspace_id", "task_id", "expected_basis_hash", "changes"],
        },
        handler=package_service.propose_plan_revision,
        output_schema=_PLAN_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_apply_plan_revision",
        description="基于未变更的 basis 应用计划提案，生成新的不可变 revision。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "proposal_id": {"type": "string", "minLength": 1},
                "expected_basis_hash": _basis_schema,
            },
            "required": ["workspace_id", "task_id", "proposal_id", "expected_basis_hash"],
        },
        handler=package_service.apply_plan_revision,
        output_schema=_PLAN_OUTPUT,
        annotations=_bundle_write,
    )
    server.register_tool(
        name="dr_add_sources",
        description="将显式分类、带 hash/locator 的混合来源绑定到新计划 revision。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "expected_basis_hash": _basis_schema,
                "sources": {
                    "type": "array", "minItems": 1, "maxItems": 200,
                    "items": _MIXED_SOURCE_DESCRIPTOR,
                },
            },
            "required": ["workspace_id", "task_id", "expected_basis_hash", "sources"],
        },
        handler=package_service.add_sources,
        output_schema=_PLAN_OUTPUT,
        annotations=_agent_write,
        # Source descriptors are a client-authored evidence contract.  Publish
        # their complete discriminated shape so callers can bind an imported
        # source file without a preliminary schema-resource round trip.
        public_input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "expected_basis_hash": _basis_schema,
                "sources": {
                    "type": "array", "minItems": 1, "maxItems": 200,
                    "items": _MIXED_SOURCE_DESCRIPTOR,
                },
            },
            "required": ["workspace_id", "task_id", "expected_basis_hash", "sources"],
        },
    )
    server.register_tool(
        name="dr_remove_sources",
        description="在新计划 revision 中排除来源并记录原因；不删除原始快照或旧 revision。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "expected_basis_hash": _basis_schema,
                "source_object_ids": {
                    "type": "array", "minItems": 1, "maxItems": 200,
                    "uniqueItems": True, "items": {"type": "string", "minLength": 1},
                },
                "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
            "required": ["workspace_id", "task_id", "expected_basis_hash", "source_object_ids", "reason"],
        },
        handler=package_service.remove_sources,
        output_schema=_PLAN_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_list_events",
        description="按游标读取结构化研究事件；不返回 chain-of-thought 或隐藏推理。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "after_cursor": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=lambda args: package_service.list_events(
            args["workspace_id"],
            args["task_id"],
            after_cursor=str(args.get("after_cursor") or ""),
            limit=int(args.get("limit") or 50),
        ),
        output_schema=_EVENT_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_create_checkpoint",
        description="固化当前计划、预算、来源、质量和待办，返回有期限的签名恢复令牌。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "expected_basis_hash": _basis_schema,
                "reason": {"type": "string", "maxLength": 2000},
                "expires_in_seconds": {"type": "integer", "minimum": 60, "maximum": 604800, "default": 86400},
            },
            "required": ["workspace_id", "task_id", "expected_basis_hash"],
        },
        handler=package_service.create_checkpoint,
        output_schema=_CHECKPOINT_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_resume",
        description="校验签名令牌并从 checkpoint 创建新研究任务；原任务与历史版本不变。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "resume_token": {"type": "string", "pattern": r"^drresume\.v1\..+"},
                "supplemental_questions": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": {"type": "string", "maxLength": 200},
            },
            "required": ["workspace_id", "resume_token"],
        },
        handler=package_service.resume_from_checkpoint,
        output_schema=_RESUME_OUTPUT,
        annotations=_bundle_write,
    )
    server.register_tool(
        name="dr_submit",
        description="将 Agent 撰写、带 source locator 的研究发现固化为 partial research_package_id；不把 Agent 文本冒充独立质量审计完成。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema, "task_id": _task_schema,
                "report_md": {"type": "string", "minLength": 1},
                "citations": {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": True}},
                "evidence_pack_ids": {"type": "array", "items": {"type": "string"}},
                "source_snapshot_ids": {"type": "array", "items": {"type": "string"}},
                "quality_summary": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "query_rounds": {"type": "integer", "minimum": 0},
                        "usable_source_count": {"type": "integer", "minimum": 0},
                        "citation_coverage": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                        "missing_fields": {"type": "array", "items": {"type": "string"}},
                        "conflicts": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "market_field_bindings": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "unresolved_inputs": {"type": "array", "items": {"type": "string"}},
                "release_limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["workspace_id", "task_id", "report_md", "citations"],
        },
        handler=package_service.submit_agent,
        output_schema=_BUNDLE_OUTPUT,
        annotations=_bundle_write,
    )
    server.register_tool(
        name="dr_continue",
        description="从 needs_clarification/partial/blocked checkpoint 开新续研任务；保留 lineage 且不降低质量阈值。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema, "task_id": _task_schema,
                "supplemental_questions": {"type": "array", "items": {"type": "string"}},
                "clarifications": {"type": "object"}, "additional_budget": {"type": "object"},
                "industry": {"type": "string"}, "region": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=package_service.continue_task,
        output_schema=_CONTINUE_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_confirm_quality",
        description="为 dr_submit 固化的 partial 研究包创建独立质量确认；可明确接受 source_reconstructed 的资料限制，但不认证项目事实。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "research_package_id": {"type": "string", "minLength": 1},
                "query_rounds": {"type": "integer", "minimum": 0},
                "usable_source_count": {"type": "integer", "minimum": 0},
                "citation_coverage": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                "missing_fields": {"type": "array", "items": {"type": "string"}},
                "conflicts": {"type": "array", "items": {"type": "object"}},
                "accept_material_limitations": {"type": "boolean", "default": False},
            },
            "required": ["workspace_id", "research_package_id"],
        },
        handler=package_service.confirm_quality,
        output_schema=_QUALITY_CONFIRM_OUTPUT,
        annotations=_bundle_write,
    )
    server.register_tool(
        name="dr_status",
        description="轮询研究进度：返回 status/轮次/预算/质量门。status=partial 是诚实产出非失败；done 才可采信。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=_tool_dr_status,
        output_schema=_STATUS_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_cancel",
        description="用户喊停时中止研究任务。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "reason": {"type": "string", "maxLength": 2000},
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=_tool_dr_cancel,
        output_schema=_CANCEL_OUTPUT,
        annotations=_cancel,
    )
    server.register_tool(
        name="dr_get_report",
        description="取研究报告正文（Markdown）+ 引用审计 + 质量门结论。仅任务终态可读。财务数字不得取自本报告。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=_tool_dr_get_report,
        output_schema=_REPORT_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_get_evidence",
        description="取证据图谱 + 来源清单 + 引用，供研报正文引用与人工采信。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=_tool_dr_get_evidence,
        output_schema=_EVIDENCE_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_get_bundle",
        description=(
            "终态固化 research_package_id，并返回报告、证据、来源、质量与引用审计 Resource URI；"
            "只登记真实存在的 artifact（含 checkpoint），缺失的诚实省略。"
        ),
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=lambda args: package_service.bundle(args["workspace_id"], args["task_id"]),
        output_schema=_BUNDLE_OUTPUT,
        annotations=_bundle_write,
    )

    # Protocol-level resources/list and resources/read carry no workspace scope.
    # Dynamic access is centralized in lvke-feasibility-delivery.
    server.register_resource_provider(lambda: [], lambda _uri: None)
