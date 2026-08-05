"""Official SDK MCP server for feasibility delivery orchestration."""

from __future__ import annotations

import json

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.servers.lvke_feasibility_delivery import service
from lvke_mcp.servers.lvke_feasibility_delivery.contracts import DELIVERY_MODES, EVIDENCE_POLICIES, RELEASE_SCOPES, STAGES, STAGE_STATUSES

SERVER_NAME = "lvke-feasibility-delivery"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)

_WS = {"type": "string", "minLength": 1}
_ID = {"type": "string", "minLength": 1}
_URI = {"type": "string", "minLength": 1, "maxLength": 8192}
_KEY = {"type": "string", "minLength": 1, "maxLength": 256}
_OUTPUT = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "success": {"type": "boolean"},
        "status": {"type": "string"},
        "resource_uris": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["success", "status", "resource_uris", "warnings", "blockers", "next_actions"],
}


def _read_resource(uri: str):
    resolved = service.resolve_resource(uri)
    if resolved is None:
        return None
    content, mime_type = resolved
    return ReadResourceContents(content, mime_type)


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    write = types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    server.register_tool(
        "feasibility_start",
        "创建不可变可研交付运行和阶段清单。",
        {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _WS, "project_context_id": _ID, "delivery_mode": {"type": "string", "enum": list(DELIVERY_MODES)}, "evidence_policy": {"type": "string", "enum": list(EVIDENCE_POLICIES)}, "release_scope": {"type": "string", "enum": list(RELEASE_SCOPES)}, "project_fact_certified": {"type": "boolean"}, "reconstructed_source_ids": {"type": "array", "items": _ID}, "unresolved_inputs": {"type": "array", "items": {"type": "string"}}, "release_limitations": {"type": "array", "items": {"type": "string"}}, "idempotency_key": _KEY}, "required": ["workspace_id", "delivery_mode", "idempotency_key"]},
        service.start, _OUTPUT, write,
    )
    server.register_tool(
        "feasibility_status",
        "读取指定可研交付运行快照。",
        {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _WS, "delivery_run_id": _ID}, "required": ["workspace_id", "delivery_run_id"]},
        service.status, _OUTPUT, read,
    )
    server.register_tool(
        "feasibility_stage",
        "写入阶段快照；上游重开时标记所有下游为 stale。",
        {"type": "object", "additionalProperties": False, "properties": {
            "workspace_id": _WS, "delivery_run_id": _ID,
            "stage": {"type": "string", "enum": list(STAGES[:-1])},
            "status": {"type": "string", "enum": list(STAGE_STATUSES)},
            "input_refs": {"type": "array", "items": _ID},
            "output_refs": {"type": "array", "items": _ID},
            "basis_hash": {"type": "string"},
            "stage_basis_hash": {"type": "string"},
            "expected_basis_hash": {"type": "string"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "blockers": {"type": "array", "items": {"type": "string"}},
            "next_actions": {"type": "array", "items": {"type": "string"}},
            "reopen": {"type": "boolean", "default": False},
            "idempotency_key": _KEY,
        }, "required": ["workspace_id", "delivery_run_id", "stage", "status", "idempotency_key"]},
        service.stage, _OUTPUT, write,
    )
    server.register_tool(
        "feasibility_next_actions",
        "根据当前阶段和 blocker 生成下一步工具调用。",
        {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _WS, "delivery_run_id": _ID}, "required": ["workspace_id", "delivery_run_id"]},
        service.next_actions, _OUTPUT, read,
    )
    server.register_tool(
        "feasibility_checkpoint",
        "保存当前交付运行恢复点。",
        {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _WS, "delivery_run_id": _ID, "reason": {"type": "string"}, "idempotency_key": _KEY}, "required": ["workspace_id", "delivery_run_id", "idempotency_key"]},
        service.checkpoint, _OUTPUT, write,
    )
    server.register_tool(
        "feasibility_resume",
        "从交付 checkpoint 创建新的不可变运行快照。",
        {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _WS, "checkpoint_id": _ID, "supplemental_inputs": {"type": "object"}, "idempotency_key": _KEY}, "required": ["workspace_id", "checkpoint_id", "idempotency_key"]},
        service.resume, _OUTPUT, write,
    )
    server.register_tool(
        "feasibility_validate",
        "按 technical 或 formal 范围校验阶段顺序、lineage 和发布条件。",
        {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _WS, "delivery_run_id": _ID, "scope": {"type": "string", "enum": ["technical", "formal"], "default": "technical"}}, "required": ["workspace_id", "delivery_run_id"]},
        service.validate, _OUTPUT, read,
    )
    server.register_tool(
        "feasibility_release",
        "将 formal 校验通过的运行快照固化为 release。",
        {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _WS, "delivery_run_id": _ID, "release_scope": {"type": "string", "enum": list(RELEASE_SCOPES)}, "release_note": {"type": "string"}, "idempotency_key": _KEY}, "required": ["workspace_id", "delivery_run_id", "idempotency_key"]},
        service.release, _OUTPUT, write,
    )
    server.register_tool(
        "feasibility_list_resources",
        "列出当前 workspace 的交付运行、checkpoint 和 release Resource。",
        {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _WS, "resource_type": {"type": "string"}, "cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "required": ["workspace_id"]},
        service.list_resources, _OUTPUT, read,
    )
    server.register_tool(
        "feasibility_read_resource",
        "在当前 workspace 读取交付 Resource。",
        {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _WS, "uri": _URI}, "required": ["workspace_id", "uri"]},
        service.read_resource, _OUTPUT, read,
    )
    server.register_resource_provider(lambda: [], _read_resource)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
