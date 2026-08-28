"""server builder 与 stdio 启动入口。"""

from __future__ import annotations

import json

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from lvke_mcp.adapters.finance_model_repository import FACT_PACK_STORE, SPEC_STORE
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.domains.finance.parameter_resolver import (
    finance_input_schema,
    finance_spec_candidate_schema,
)

from .analysis_tools import (
    _install_get_analysis_aggregate,
    _resolve_analysis_resource,
    _tool_build_balance_sheet,
    _tool_build_basis_of_estimate,
    _tool_get_balance_sheet,
    _tool_get_basis_of_estimate,
    _tool_get_monte_carlo,
    _tool_list_analyses,
    _tool_read_analysis_resource,
    _tool_run_monte_carlo,
)

from .calc_tools import (
    _CALCULATOR_TOOL_BY_OPERATION,
    _tool_finance_calculate,
)

from .run_tools import (
    _tool_generate_package,
    _tool_get_run,
    _tool_import_vendor_review,
    _tool_run_model,
)

from .validation_tools import (
    _tool_promote_to_formal,
    _tool_validate_post_generation,
)

from .schemas import (
    SERVER_NAME,
    SERVER_VERSION,
    _BOE_ENTRY_SCHEMA,
    _DISTRIBUTION_SCHEMA,
    _FINANCE_SPEC_SCHEMA_URI,
    _output_schema,
    logger,
)

from .spec_tools import (
    _tool_confirm_fact_pack,
    _tool_confirm_spec,
    _tool_get_fact_pack,
    _tool_prepare_fact_pack,
    _tool_prepare_spec,
    _tool_validate_spec,
)

from .validation_tools import (
    _tool_promote_to_formal,
    _tool_validate_post_generation,
)


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    # annotations 仅是客户端提示，不是安全控制（方案 4.6）。
    read_closed = types.ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    write_deterministic = types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    write_nonidempotent = types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
    finance_spec_schema = finance_spec_candidate_schema()
    finance_spec_schema["x-lvke-schema-uri"] = _FINANCE_SPEC_SCHEMA_URI
    server.register_tool(
        name="finance_calculate",
        description="调用原 finance-calc 确定性纯函数；不创建 FinanceRun，也不替代 FinanceSpec 门禁。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": list(_CALCULATOR_TOOL_BY_OPERATION),
                },
                "inputs": {"type": "object"},
            },
            "required": ["operation", "inputs"],
        },
        handler=_tool_finance_calculate,
        output_schema=None,
        annotations=read_closed,
    )
    fact_pack_schema = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "version": {"type": "string"},
            "project_id": {"type": "string"},
            "valuation_date": {"type": "string"},
            "evidence_policy": {
                "type": "string",
                "enum": ["formal_evidence", "source_reconstructed", "sim_a_formal"],
            },
            "project_fact_certified": {"type": "boolean"},
            "domains": {"type": "object"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "description": (
                        "逐值血缘行。未知字段返回字段级 validation_error，不静默忽略。"
                        "来源标识三选一：source_id / source_snapshot_id / file_id。"
                        "claimed_* 与 evidence_grade 等仅为调用方申报值，权威值由服务端解析来源后回填。"
                    ),
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "所属财务域；省略时从 fact_path 首段推断，推断不出则报错",
                        },
                        "fact_path": {
                            "type": "string",
                            "description": "事实指针，如 funding.debt_draw[0].draw_wan",
                        },
                        "source_id": {"type": "string", "description": "来源标识；与下两者等价"},
                        "source_snapshot_id": {
                            "type": "string",
                            "description": "data-acquisition 侧 SourceSnapshot id（src_*）",
                        },
                        "file_id": {
                            "type": "string",
                            "description": "source-files 侧已导入文件 id（src_*）",
                        },
                        "evidence_id": {"type": "string"},
                        "locator": {
                            "type": "string",
                            "description": "正文内可校验定位串；必须在来源正文中真实命中",
                        },
                        "page_or_cell": {"type": "string", "description": "locator 的等价别名"},
                        "claimed_value": {"description": "调用方申报数值（非权威）"},
                        "value": {"description": "claimed_value 的别名"},
                        "amount_wan": {"description": "claimed_value 的别名，单位万元"},
                        "unit": {"type": "string"},
                        "period": {"type": "string"},
                        "year": {"type": "string", "description": "period 的别名"},
                        "evidence_grade": {"type": "string", "description": "调用方申报评级（非权威）"},
                        "grade": {"type": "string", "description": "evidence_grade 的别名"},
                        "review_status": {"type": "string", "description": "调用方申报复核状态（非权威）"},
                        "status": {"type": "string", "description": "review_status 的别名"},
                    },
                },
            },
            "reconstruction_records": {"type": "array", "items": {"type": "object"}},
            "unresolved_inputs": {"type": "array", "items": {"type": "string"}},
            "release_limitations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["version", "evidence_policy", "domains", "evidence"],
    }
    server.register_tool(
        name="finance_prepare_fact_pack",
        description=(
            "规范化并固化 finance_fact_pack.v1 候选；来源重建模式必须绑定已导入的 "
            "Source Snapshot、hash、locator 和 method，不认证项目原始事实。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "fact_pack": fact_pack_schema,
                "idempotency_key": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "fact_pack", "idempotency_key"],
        },
        handler=_tool_prepare_fact_pack,
        output_schema=_output_schema(
            {
                "fact_pack_id": {"type": ["string", "null"]},
                "confirmation_status": {"type": "string"},
                "delivery_grade_ceiling": {"type": "string"},
                "fact_pack_hash": {"type": ["string", "null"]},
                "depth_assessment": {"type": "object"},
                "binding_assessment": {"type": "object"},
                "replayed": {"type": "boolean"},
            },
            success_required=["fact_pack_id", "confirmation_status", "delivery_grade_ceiling"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_confirm_fact_pack",
        description=(
            "复核 Fact Pack 深度和逐事实来源绑定，生成服务端确认的 formal_candidate "
            "不可变修订；source_reconstructed 始终保持 project_fact_certified=false。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "fact_pack_id": {"type": "string", "minLength": 1},
                "idempotency_key": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "fact_pack_id", "idempotency_key"],
        },
        handler=_tool_confirm_fact_pack,
        output_schema=_output_schema(
            {
                "fact_pack_id": {"type": ["string", "null"]},
                "confirmation_status": {"type": "string"},
                "delivery_grade_ceiling": {"type": "string"},
                "fact_pack_hash": {"type": ["string", "null"]},
                "depth_assessment": {"type": "object"},
                "binding_assessment": {"type": "object"},
                "replayed": {"type": "boolean"},
            },
            success_required=["fact_pack_id", "confirmation_status", "delivery_grade_ceiling"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_get_fact_pack",
        description="读取不可变 Finance Fact Pack、深度评估、证据覆盖和 hash，不重算。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "fact_pack_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "fact_pack_id"],
        },
        handler=_tool_get_fact_pack,
        output_schema=_output_schema(
            {
                "fact_pack_id": {"type": ["string", "null"]},
                "confirmation_status": {"type": "string"},
                "delivery_grade_ceiling": {"type": "string"},
                "fact_pack_hash": {"type": ["string", "null"]},
                "depth_assessment": {"type": "object"},
                "binding_assessment": {"type": "object"},
                "replayed": {"type": "boolean"},
            },
            success_required=["fact_pack_id", "confirmation_status", "delivery_grade_ceiling"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_prepare_spec",
        description=(
            "准备/复用确定性 FinanceSpec（收入/成本/税务口径），不调用内置 LLM。"
            "Agent 应先显式提交证据支持的假设；返回 spec、spec_hash、assumptions_to_confirm、missing_inputs。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "description": "工作区 ID"},
                "strategy": {
                    "type": "string",
                    "enum": ["reuse_confirmed", "propose_from_project"],
                    "default": "propose_from_project",
                },
                "force_refresh": {"type": "boolean", "default": False},
                "force_flat": {"type": "boolean", "default": False},
                "spec": finance_spec_schema,
                "input_revision": finance_input_schema(),
                "evidence_pack_ids": {
                    "type": "array", "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
                "fact_pack_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "同工作区已 confirmed 且达到 formal_candidate 的 Finance Fact Pack ID。",
                },
                "unresolved_inputs": {"type": "array", "items": {"type": "string"}},
                "release_limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["workspace_id"],
        },
        handler=_tool_prepare_spec,
        output_schema=_output_schema(
            {
                "spec_hash": {"type": ["string", "null"]},
                "spec_id": {"type": ["string", "null"]},
                "evidence_binding_hash": {"type": "string"},
                "fact_pack_id": {"type": ["string", "null"]},
                "fact_pack_hash": {"type": ["string", "null"]},
                "fact_pack_errors": {"type": "array", "items": {"type": "string"}},
                "missing_inputs": {"type": "array", "items": {"type": "string"}},
                "assumptions_to_confirm": {"type": "array", "items": {"type": "string"}},
                "field_errors": {"type": "array", "items": {"type": "object"}},
                "input_hash": {"type": ["string", "null"]},
                "input_revision_id": {"type": ["integer", "null"]},
            },
            success_required=["spec_id", "spec_hash", "evidence_binding_hash", "missing_inputs", "assumptions_to_confirm"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_confirm_spec",
        description="将候选 FinanceSpec 固化为新的已确认修订；不原地改写候选对象。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "spec_id": {"type": "string", "minLength": 1},
                "note": {"type": "string", "maxLength": 2000},
                "idempotency_key": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "spec_id", "idempotency_key"],
        },
        handler=_tool_confirm_spec,
        output_schema=_output_schema(
            {"spec_id": {"type": "string"}, "spec_hash": {"type": "string"}},
            success_required=["spec_id", "spec_hash"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_validate_spec",
        description="校验 FinanceSpec 结构、数值和可选正式交付缺项；不计算任何财务指标。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "spec": finance_spec_schema,
                "for_formal": {"type": "boolean", "default": False},
            },
            "required": ["spec"],
        },
        handler=_tool_validate_spec,
        output_schema=_output_schema(
            {
                "valid": {"type": "boolean"},
                "missing_inputs": {"type": "array", "items": {"type": "string"}},
            },
            success_required=[
                "valid",
                "missing_inputs",
            ],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_run_model",
        description=(
            "以固化输入与 spec 运行确定性财务模型，返回 run_id、indicators、checks、"
            "table_manifest。工具内部不调用 LLM 做算术。缺输入时返回 missing_inputs，"
            "不伪造 13 表。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string"},
                "idempotency_key": {
                    "type": "string",
                    "minLength": 8,
                    "maxLength": 256,
                    "description": "调用方生成的稳定幂等键；同键异载荷将 fail closed。",
                },
                "spec_id": {"type": "string"},
                "basis_of_estimate_id": {"type": "string"},
                "spec_hash": {"type": "string"},
                "spec": finance_spec_schema,
                "input_revision": finance_input_schema(),
                "input_revision_id": {"type": "integer", "minimum": 0},
                "mode": {
                    "type": "string",
                    "enum": ["estimate_preview", "review_candidate"],
                    "default": "estimate_preview",
                },
                "force_recompute": {"type": "boolean", "default": False},
                "force_flat": {"type": "boolean", "default": False},
                "agent_trace_id": {"type": "string"},
                "tool_call_id": {"type": "string"},
                "valuation_date": {"type": "string", "format": "date"},
                "requested_manifest": {"type": "object"},
                "selected_scenario_id": {"type": "string", "default": "base"},
            },
            "required": ["workspace_id", "idempotency_key"],
        },
        handler=_tool_run_model,
        output_schema=_output_schema(
            {
                "run_id": {"type": ["string", "null"]},
                "missing_inputs": {"type": "array", "items": {"type": "string"}},
                "field_errors": {"type": "array", "items": {"type": "object"}},
            },
            success_required=["run_id", "missing_inputs"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_get_run",
        description="纯查询财务 run（summary/full/tables/checks），不重算、不写库。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string"},
                "run_id": {"type": "string", "description": "省略则取最新 run"},
                "view": {
                    "type": "string",
                    "enum": ["summary", "full", "tables", "checks"],
                    "default": "summary",
                },
            },
            "required": ["workspace_id"],
        },
        handler=_tool_get_run,
        output_schema=_output_schema(
            {
                "run_id": {"type": ["string", "null"]},
                "view": {
                    "type": "string",
                    "enum": ["summary", "full", "tables", "checks"],
                },
            },
            success_required=["run_id", "view"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_validate_post_generation",
        description=(
            "对已完成的 FinanceRun 执行四维后置校验（技术/大纲标准/证据绑定/生存能力），"
            "返回结构化 ValidationReport。永不阻断生成，始终返回结果。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "run_id": {"type": "string", "minLength": 1},
                "spec": {
                    "type": "object",
                    "description": "可选 FinanceSpec，传入后校验证据绑定维度",
                },
                "validation_scope": {
                    "type": "string",
                    "enum": ["technical", "formal"],
                    "default": "technical",
                },
                "finance_inputs": {
                    "type": "object",
                    "description": "可选，传入后校验大纲标准覆盖率",
                },
                "table_manifest": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "可选，传入后校验大纲标准覆盖率",
                },
                "report_sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，传入后校验大纲标准覆盖率",
                },
            },
            "required": ["workspace_id", "run_id"],
        },
        handler=_tool_validate_post_generation,
        output_schema=_output_schema(
            {
                "validation_scope": {"type": "string"},
                "dimensions": {"type": "object"},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "quality_issues": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "overall_status": {"type": "string"},
                "generated_against_standard": {"type": "boolean"},
                "validation_stage": {"type": "string"},
                "dimension_count": {"type": "integer"},
                "dimension_names": {"type": "array", "items": {"type": "string"}},
            },
            success_required=[
                "validation_scope",
                "dimensions",
                "overall_status",
                "dimension_count",
                "dimension_names",
            ],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_promote_to_formal",
        description=(
            "基于前序 FinanceRun 创建不可变正式版修订，链接 parent_run_id，"
            "保留旧版本并返回指标、假设和字段的结构化差异。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "prior_run_id": {"type": "string", "minLength": 1},
                "new_fin": {"type": "object"},
                "validation_report": {"type": "object"},
                "idempotency_key": {"type": "string", "maxLength": 256},
                "model_version": {"type": "string"},
                "template_version": {"type": "string"},
                "input_hash": {"type": "string"},
                "table_bundle_hash": {"type": "string"},
                "agent_trace_id": {"type": "string"},
                "tool_call_id": {"type": "string"},
            },
            "required": ["workspace_id", "prior_run_id", "new_fin"],
        },
        handler=_tool_promote_to_formal,
        output_schema=_output_schema(
            {
                "run_id": {"type": ["string", "null"]},
                "prior_run_id": {"type": ["string", "null"]},
                "formal_grade": {"type": ["string", "null"]},
                "version_sequence": {"type": ["integer", "null"]},
                "diff": {"type": "object"},
                "validation_report": {"type": "object"},
            },
            success_required=[
                "run_id",
                "prior_run_id",
                "formal_grade",
                "version_sequence",
                "diff",
            ],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_build_basis_of_estimate",
        description=(
            "从已确认 FinanceSpec、EvidencePack 与 confirmed planning 对象固化不可变 BoE。"
            "每个重大输入必须含方法、选择理由、locator、hash 和证据资格。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "spec_id": {"type": "string", "minLength": 1},
                "planning_object_ids": {
                    "type": "array", "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "evidence_pack_ids": {
                    "type": "array", "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "entries": {
                    "type": "array", "minItems": 1, "maxItems": 500,
                    "items": _BOE_ENTRY_SCHEMA,
                },
                "unresolved_inputs": {"type": "array", "items": {"type": "string"}},
                "release_limitations": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "required": [
                "workspace_id", "spec_id", "planning_object_ids",
                "evidence_pack_ids", "entries", "idempotency_key"
            ],
        },
        handler=_tool_build_basis_of_estimate,
        output_schema=_output_schema(
            {
                "basis_of_estimate_id": {"type": ["string", "null"]},
                "spec_id": {"type": "string"},
                "technical_ready": {"type": "boolean"},
                "formal_ready": {"type": "boolean"},
                "replayed": {"type": "boolean"},
            },
            success_required=[
                "basis_of_estimate_id", "spec_id", "technical_ready", "formal_ready"
            ],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_get_basis_of_estimate",
        description="读取已固化 Basis of Estimate 及其输入来源、选择和 hash，不重算。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "basis_of_estimate_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "basis_of_estimate_id"],
        },
        handler=_tool_get_basis_of_estimate,
        output_schema=_output_schema(
            {
                "basis_of_estimate_id": {"type": "string"},
                "run_id": {"type": ["string", "null"]},
                "technical_ready": {"type": "boolean"},
                "formal_ready": {"type": "boolean"},
            },
            success_required=["basis_of_estimate_id", "technical_ready", "formal_ready"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_build_balance_sheet",
        description=(
            "仅从已通过勾稽的不可变 FinanceRun 派生资产负债计划。"
            "同时披露账面权益组成、计算权益残差及勾稽差额，不用残差静默补平。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "run_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "run_id"],
        },
        handler=_tool_build_balance_sheet,
        output_schema=_output_schema(
            {
                "balance_sheet_id": {"type": ["string", "null"]},
                "run_id": {"type": "string"},
                "formal_ready": {"type": "boolean"},
            },
            success_required=["balance_sheet_id", "run_id", "formal_ready"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_get_balance_sheet",
        description="读取已固化的资产负债计划，不重算。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "balance_sheet_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "balance_sheet_id"],
        },
        handler=_tool_get_balance_sheet,
        output_schema=_output_schema(
            {
                "balance_sheet_id": {"type": "string"},
                "run_id": {"type": ["string", "null"]},
                "formal_ready": {"type": "boolean"},
            },
            success_required=["balance_sheet_id", "run_id", "formal_ready"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_run_monte_carlo",
        description=(
            "以不可变 FinanceRun 为基准执行带 seed 的确定性 Monte Carlo。"
            "样本只在内存中重算，仅固化 IRR/NPV P5、P50、P95 与失败统计。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "run_id": {"type": "string", "minLength": 1},
                "distributions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": _DISTRIBUTION_SCHEMA,
                },
                "sample_count": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 10000,
                    "default": 1000,
                },
                "seed": {"type": "integer", "minimum": -2147483648, "maximum": 2147483647},
            },
            "required": ["workspace_id", "run_id", "distributions", "seed"],
        },
        handler=_tool_run_monte_carlo,
        output_schema=_output_schema(
            {
                "monte_carlo_id": {"type": ["string", "null"]},
                "run_id": {"type": "string"},
                "sample_count": {"type": "integer"},
                "field_errors": {"type": "array", "items": {"type": "object"}},
            },
            success_required=["monte_carlo_id", "run_id", "sample_count"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_get_monte_carlo",
        description="读取已固化的 Monte Carlo 分位数摘要与分布清单，不重算。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "monte_carlo_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "monte_carlo_id"],
        },
        handler=_tool_get_monte_carlo,
        output_schema=_output_schema(
            {
                "monte_carlo_id": {"type": "string"},
                "run_id": {"type": ["string", "null"]},
                "formal_ready": {"type": "boolean"},
            },
            success_required=["monte_carlo_id", "run_id", "formal_ready"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_list_analyses",
        description="在显式工作区内分页列出高级财务分析 Resource。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "resource_type": {
                    "type": "string",
                    "enum": ["all", "balance_sheet", "monte_carlo", "basis_of_estimate", "fact_pack"],
                    "default": "all",
                },
                "cursor": {"type": "string", "maxLength": 8192},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "required": ["workspace_id"],
        },
        handler=_tool_list_analyses,
        output_schema=_output_schema(
            {
                "analysis_count": {"type": "integer"},
                "next_cursor": {"type": ["string", "null"]},
            },
            success_required=["analysis_count", "next_cursor"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_read_analysis_resource",
        description="按 URI 读取同工作区下的资产负债或 Monte Carlo 不可变 Resource。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "uri": {
                    "type": "string",
                    "pattern": r"^lvke://finance-model/workspaces/",
                    "maxLength": 8192,
                },
            },
            "required": ["workspace_id", "uri"],
        },
        handler=_tool_read_analysis_resource,
        output_schema=_output_schema(
            {
                "object_id": {"type": "string"},
                "content_hash": {"type": "string"},
                "basis_hash": {"type": "string"},
            },
            success_required=["object_id", "content_hash", "basis_hash"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_generate_package",
        description=(
            "[DEPRECATED] 巨型组合入口；新工作流应显式调用 finance_run_model → "
            "lvke-finance-tables.tables_render，不隐藏跨层绑定。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["estimate_preview", "review_candidate"],
                    "default": "estimate_preview",
                },
                "force_refresh_spec": {"type": "boolean", "default": False},
                "force_recompute": {"type": "boolean", "default": False},
                "force_flat": {"type": "boolean", "default": False},
                "confirmed_spec": {
                    "type": "object",
                    "description": "人工确认并冻结的 FinanceSpec；提供后 package 不再调用 LLM 改写",
                },
                "agent_trace_id": {"type": "string"},
                "tool_call_id": {"type": "string"},
                "valuation_date": {"type": "string", "format": "date"},
                "requested_manifest": {"type": "object"},
                "selected_scenario_id": {"type": "string", "default": "base"},
            },
            "required": ["workspace_id"],
        },
        handler=_tool_generate_package,
        output_schema=_output_schema(
            {
                "run_id": {"type": ["string", "null"]},
                "stage": {"type": ["string", "null"]},
            },
            success_required=["run_id", "stage"],
            deprecated=True,
        ),
        annotations=write_nonidempotent,
    )
    server.register_tool(
        name="finance_import_vendor_review",
        description=(
            "导入甲方原生 xlsx 为只读公式参考档，检测本金重复/手工IRR/僵尸公式，"
            "用我方确定性模型重算并生成双轨对照、阻断预警和复核报告。"
            "甲方原值永不作为对外数字源。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string"},
                "xlsx_path": {"type": "string", "description": "甲方 .xlsx/.xlsm 路径"},
                "valuation_date": {
                    "type": "string",
                    "format": "date",
                    "description": "审查估值日；省略时使用调用日",
                },
                "force_recompute": {"type": "boolean", "default": False},
                "cohort_xlsx_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选同批工作簿，用于识别跨模板重复僵尸公式",
                },
            },
            "required": ["workspace_id", "xlsx_path"],
        },
        handler=_tool_import_vendor_review,
        output_schema=_output_schema(
            {
                "reference_id": {"type": ["string", "null"]},
                "review_passed": {"type": "boolean"},
                "run_id": {"type": ["string", "null"]},
                "missing_inputs": {"type": "array", "items": {"type": "string"}},
            },
            success_required=[
                "reference_id",
                "review_passed",
                "run_id",
                "missing_inputs",
            ],
        ),
        annotations=write_deterministic,
    )

    def read_run_resource(uri: str):
        analysis_record = _resolve_analysis_resource(uri)
        if analysis_record is not None:
            return ReadResourceContents(
                json.dumps(analysis_record, ensure_ascii=False, indent=2, default=str),
                "application/json",
            )
        spec_record = SPEC_STORE.resolve_uri(uri)
        if spec_record is not None:
            return ReadResourceContents(
                json.dumps(spec_record, ensure_ascii=False, indent=2, default=str),
                "application/json",
            )
        fact_pack_record = FACT_PACK_STORE.resolve_uri(uri)
        if fact_pack_record is not None:
            return ReadResourceContents(
                json.dumps(fact_pack_record, ensure_ascii=False, indent=2, default=str),
                "application/json",
            )
        prefix = "lvke://finance-model/workspaces/"
        if not uri.startswith(prefix):
            return None
        parts = uri[len(prefix) :].split("/")
        if len(parts) != 3 or parts[1] != "runs":
            return None
        try:
            from lvke_mcp.runtime.storage import require_safe_id
            from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

            workspace_id = require_safe_id(parts[0], "workspace_id")
            run_id = require_safe_id(parts[2], "run_id")
            value = get_workspace_finance_run(
                workspace_id,
                run_id=run_id,
                view="full",
            )
        except Exception:  # noqa: BLE001
            return None
        if not value.get("available"):
            return None
        return ReadResourceContents(
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
            "application/json",
        )

    _install_get_analysis_aggregate(server, read_closed)
    server.register_schema_resource(
        _FINANCE_SPEC_SCHEMA_URI,
        finance_spec_schema,
        name="finance-spec-v3",
        title="FinanceSpec v3",
        description="财务服务端用于准备、校验和运行的完整 FinanceSpec v3 候选 Schema。",
    )
    server.register_resource_provider(lambda: [], read_run_resource)
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()
