"""Official-SDK MCP server for final report drafting and delivery artifacts."""

from __future__ import annotations

import base64

from mcp import types

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.domains.reports import application as service

SERVER_NAME = "lvke-report-generation"
SERVER_VERSION = "0.1.0"
_REPORT_PREPARATION_SCHEMA_URI = "lvke://schemas/report-preparation"
logger = get_logger(SERVER_NAME)
_OUTPUT = {"type": "object", "additionalProperties": True, "properties": {"success": {"type": "boolean"}, "status": {"type": "string"}, "resource_uris": {"type": "array", "items": {"type": "string"}}, "warnings": {"type": "array", "items": {"type": "string"}}, "blockers": {"type": "array", "items": {"type": "string"}}, "next_actions": {"type": "array", "items": {"type": "string"}}}, "required": ["success", "status", "resource_uris", "warnings", "blockers", "next_actions"]}
_PREPARATION_OUTPUT = {
    **_OUTPUT,
    "properties": {
        **_OUTPUT["properties"],
        "ready": {"type": "boolean", "description": "兼容字段；等同 draft_ready，不代表正式发布资格"},
        "draft_ready": {"type": "boolean"},
        "formal_ready": {"type": "boolean"},
        "formal_blockers": {"type": "array", "items": {"type": "string"}},
    },
}
_VALIDATE_OUTPUT = _OUTPUT
_WS = {"type": "string", "minLength": 1}
# 键名与审查侧 _project_metadata_findings 的 aliases 主名一一对应；
# additionalProperties 保持 True 以接纳该表登记的别名（invest_type / base_date 等）。
_PROJECT_METADATA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "project_type": {"type": "string", "minLength": 1},
        "industry": {"type": "string", "minLength": 1},
        "valuation_date": {"type": "string", "minLength": 1},
        "currency": {"type": "string", "minLength": 1},
        "amount_unit": {"type": "string", "minLength": 1},
        "tax_basis": {"type": "string", "minLength": 1},
        "forecast_period": {"type": ["integer", "string"]},
    },
}


def _read_scoped_resource(workspace_id: str, uri: str) -> dict:
    value = service.resolve_resource(
        uri,
        workspace_id,
    )
    if value is None:
        return {
            "success": False, "transport_success": True, "business_success": False,
            "completed": False, "outcome": "blocked",
            "status": "blocked", "code": "resource_not_found",
            "message": "资源不存在或不属于当前工作区", "resource_uris": [],
            "warnings": [], "blockers": ["resource_not_found"],
            "next_actions": ["调用 report_list_resources 获取当前工作区可读 URI"],
        }
    content, mime_type = value
    encoded = isinstance(content, bytes)
    return {
        "success": True, "status": "ok", "uri": uri, "mime_type": mime_type,
        "content_encoding": "base64" if encoded else "utf-8",
        "content": base64.b64encode(content).decode("ascii") if encoded else content,
        "resource_uris": [uri], "warnings": [], "blockers": [], "next_actions": [],
    }


def _input_schema(
    properties: dict,
    required: list[str],
) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    write = types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    server.register_tool(
        "report_prepare",
        "校验 evidence/research 与类型化财务绑定；通用和资产收购分别查询对应领域服务。",
        _input_schema(
            {
                "workspace_id": _WS,
                "evidence_pack_ids": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {"type": "string", "minLength": 1},
                },
                "research_package_ids": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {"type": "string", "minLength": 1},
                },
                "finance_binding": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["generic_feasibility", "asset_acquisition"],
                        },
                        "run_id": {"type": "string", "minLength": 1},
                        "package_id": {"type": "string", "minLength": 1},
                    },
                    "required": ["kind", "run_id", "package_id"],
                },
                "run_id": {"type": "string"},
                "finance_tables_package_id": {"type": "string"},
                "outline": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {
                        "oneOf": [
                            {"type": "string", "minLength": 1},
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "section_id": {
                                        "type": "string",
                                        "pattern": "^sec_[a-z0-9][a-z0-9_-]{2,79}$",
                                    },
                                    "title": {"type": "string", "minLength": 1},
                                    "parent_section_id": {"type": "string"},
                                    "depth": {"type": "integer", "minimum": 1, "maximum": 6},
                                },
                                "required": ["title"],
                            },
                        ]
                    },
                },
                "template_version": {"type": "string"},
                "evidence_policy": {"type": "string"},
                "project_fact_certified": {"type": "boolean"},
                "reconstruction_records": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "reconstructed_source_ids": {"type": "array", "items": {"type": "string"}},
                "unresolved_inputs": {"type": "array", "items": {"type": "string"}},
                "release_limitations": {"type": "array", "items": {"type": "string"}},
                "project_context_id": {"type": "string"},
                # 审查侧 PROJECT.METADATA.COMPLETE 从 preparation payload 读这七个字段
                # （别名见 deliverable_review/service.py 的 _project_metadata_findings）。
                # 不在此声明会因 additionalProperties=False 被静默丢弃，使该规则数据源恒空。
                "project_metadata": _PROJECT_METADATA,
                "upstream_refs": {"type": "array", "items": {"type": "string", "minLength": 1}},
            },
            ["workspace_id", "evidence_pack_ids", "research_package_ids"],
        ),
        service.prepare,
        _PREPARATION_OUTPUT,
        read,
    )
    report_preparation_schema = next(
        spec.input_schema for spec in server.tool_specs if spec.name == "report_prepare"
    )
    report_preparation_schema["x-lvke-schema-uri"] = _REPORT_PREPARATION_SCHEMA_URI
    server.register_tool(
        "report_start",
        "创建绑定上游依据的 Agent 草稿会话；必须提交不可变正文快照。",
        _input_schema(
            {
                "workspace_id": _WS,
                "report_preparation_id": {"type": "string", "minLength": 1},
                "chapters": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                },
                "document_snapshot": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "workspace_id": _WS,
                        "revision_id": {"type": "string"},
                        "report_type": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["content"],
                },
            },
            ["workspace_id", "report_preparation_id"],
        ),
        service.start,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "report_status",
        "固化 Agent 当前草稿为绑定上游 basis 的 report_revision_id；不运行任何内置模型。",
        _input_schema(
            {
                "workspace_id": _WS,
                "task_id": {"type": "string", "minLength": 1},
            },
            ["workspace_id", "task_id"],
        ),
        lambda a: service.status(
            a["workspace_id"],
            a["task_id"],
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "report_validate",
        "执行结构、数字、财务绑定与 readiness 工程校验。",
        _input_schema(
            {
                "workspace_id": _WS,
                "report_revision_id": {"type": "string", "minLength": 1},
            },
            ["workspace_id", "report_revision_id"],
        ),
        lambda a: service.validate(
            a["workspace_id"],
            a["report_revision_id"],
        ),
        _VALIDATE_OUTPUT,
        read,
    )
    section_ref = {
        "workspace_id": _WS,
        "report_revision_id": {"type": "string", "minLength": 1},
    }
    server.register_tool(
        "report_list_sections",
        "列出 preparation 中固化并随 revision 继承的稳定章节描述符。",
        _input_schema(section_ref, ["workspace_id", "report_revision_id"]),
        lambda a: service.list_sections(
            a["workspace_id"], a["report_revision_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "report_get_section",
        "按稳定 section_id 读取指定 revision 的章节正文，不按章号猜测。",
        _input_schema(
            {**section_ref, "section_id": {"type": "string", "minLength": 1}},
            ["workspace_id", "report_revision_id", "section_id"],
        ),
        lambda a: service.get_section(
            a["workspace_id"],
            a["report_revision_id"],
            a["section_id"],
        ),
        _OUTPUT,
        read,
    )
    section_basis = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "report_preparation_id": {"type": "string", "minLength": 1},
            "basis_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "report_revision_id": {"type": "string", "minLength": 1},
            "upstream_refs": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "citation_locators": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "upstream_basis_hashes": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["report_preparation_id", "basis_hash"],
    }
    server.register_tool(
        "report_propose_section",
        "为稳定 section_id 创建普通 proposal；仍必须 report_diff → report_apply。",
        _input_schema(
            {
                "workspace_id": _WS,
                "report_revision_id": {"type": "string", "minLength": 1},
                "section_id": {"type": "string", "minLength": 1},
                "summary": {"type": "string", "minLength": 1},
                "proposed_content": {"type": "string", "minLength": 1},
                "basis": section_basis,
            },
            [
                "workspace_id",
                "report_revision_id",
                "section_id",
                "summary",
                "proposed_content",
                "basis",
            ],
        ),
        service.propose_section,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "report_validate_section",
        "局部检查章节结构、占位符、数字与引用；不授予整篇 readiness。",
        _input_schema(
            {**section_ref, "section_id": {"type": "string", "minLength": 1}},
            ["workspace_id", "report_revision_id", "section_id"],
        ),
        lambda a: service.validate_section(
            a["workspace_id"],
            a["report_revision_id"],
            a["section_id"],
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "report_export_docx",
        "生成 draft 或经确定性工程校验的 formal_candidate 不可变 DOCX 工件；项目目录镜像必须显式启用。",
        _input_schema(
            {
                "workspace_id": _WS,
                "report_revision_id": {"type": "string", "minLength": 1},
                "kind": {
                    "type": "string",
                    "enum": ["draft", "formal_candidate"],
                    "default": "draft",
                },
                "mirror_to_project": {"type": "boolean", "default": False},
            },
            ["workspace_id", "report_revision_id"],
        ),
        lambda a: service.export_docx(
            a["workspace_id"],
            a["report_revision_id"],
            a.get("kind", "draft"),
            bool(a.get("mirror_to_project", False)),
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "report_propose",
        "基于已固化 preparation/basis/revision 创建正文修改提案；不直接覆盖当前修订。",
        _input_schema(
            {
                "workspace_id": _WS,
                "summary": {"type": "string", "minLength": 1},
                "proposed_content": {"type": "string", "minLength": 1},
                "target_sections": {"type": "array", "items": {"type": "string"}},
                "basis": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "report_preparation_id": {"type": "string", "minLength": 1},
                        "basis_hash": {
                            "type": "string",
                            "pattern": "^sha256:[0-9a-f]{64}$",
                        },
                        "report_revision_id": {"type": "string", "minLength": 1},
                    },
                    "required": [
                        "report_preparation_id",
                        "basis_hash",
                        "report_revision_id",
                    ],
                },
            },
            ["workspace_id", "summary", "proposed_content", "basis"],
        ),
        service.propose,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "report_diff",
        "读取提案与当前正文差异，apply 前必须调用。",
        _input_schema(
            {
                "workspace_id": _WS,
                "proposal_id": {"type": "string", "minLength": 1},
            },
            ["workspace_id", "proposal_id"],
        ),
        lambda a: service.diff(
            a["workspace_id"],
            a["proposal_id"],
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "report_apply",
        "应用已核对提案并生成新修订；默认执行结构与财务一致性检查。enforce_structure=false 仅跳过章节结构校验（技术验证用途），不影响财务一致性校验。",
        _input_schema(
            {
                "workspace_id": _WS,
                "proposal_id": {"type": "string", "minLength": 1},
                "enforce_structure": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否执行章节结构校验。设为 false 时跳过结构校验（仅用于技术验证），不具备正式交付资格。",
                },
            },
            ["workspace_id", "proposal_id"],
        ),
        lambda a: service.apply(
            a["workspace_id"],
            a["proposal_id"],
            enforce_structure=a.get("enforce_structure", True),
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "report_get_readiness",
        "按不可变 report revision 只读获取交付 readiness；省略 revision 时兼容解析最新修订。",
        _input_schema(
            {
                "workspace_id": _WS,
                "report_revision_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "要校验的不可变研报修订；省略时解析当前工作区最新修订",
                },
            },
            ["workspace_id"],
        ),
        lambda a: service.readiness(
            a["workspace_id"],
            a.get("report_revision_id", ""),
        ),
        _OUTPUT,
        read,
    )
    server.register_schema_resource(
        _REPORT_PREPARATION_SCHEMA_URI,
        report_preparation_schema,
        name="report-preparation",
        title="Report Preparation",
        description="报告准备阶段用于绑定 evidence、research、财务 package 与提纲的完整 Schema。",
    )
    # Protocol-level Resource calls have no workspace identity. Dynamic access
    # is centralized in lvke-feasibility-delivery.
    server.register_resource_provider(lambda: [], lambda _uri: None)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
