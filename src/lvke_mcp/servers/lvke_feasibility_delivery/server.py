"""Official SDK MCP server for feasibility delivery orchestration."""

from __future__ import annotations

import json

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime import resource_registry
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
        "next_actions": {"type": "array", "items": {"oneOf": [{"type": "string"}, {"type": "object", "additionalProperties": True}]}},
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
        {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _WS, "project_context_id": _ID, "delivery_mode": {"type": "string", "enum": list(DELIVERY_MODES)}, "evidence_policy": {"type": "string", "enum": list(EVIDENCE_POLICIES)}, "release_scope": {"type": "string", "enum": list(RELEASE_SCOPES)}, "reconstructed_source_ids": {"type": "array", "items": _ID}, "reconstruction_records": {"type": "array", "items": {"type": "object", "additionalProperties": True}}, "unresolved_inputs": {"type": "array", "items": {"type": "string"}}, "release_limitations": {"type": "array", "items": {"type": "string"}}, "idempotency_key": _KEY}, "required": ["workspace_id", "delivery_mode", "idempotency_key"]},
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
            "bind_workspace_lineage": {"type": "boolean", "default": False},
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
        "lvke_list_resources",
        "按领域和显式 workspace 分页列举原始 Lvke Resource；不转换 URI、记录或二进制。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "domain": {"type": "string", "enum": list(resource_registry.DOMAINS)},
                "resource_type": {"type": "string"},
                "cursor": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "required": ["workspace_id", "domain"],
        },
        lambda a: resource_registry.list_resources(
            a["workspace_id"],
            a["domain"],
            resource_type=a.get("resource_type", ""),
            cursor=a.get("cursor", ""),
            limit=int(a.get("limit", 50)),
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "lvke_read_resource",
        "从 lvke:// URI 自动识别领域并在显式 workspace 内读取原始内容；跨工作区读取继续 fail-closed。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"workspace_id": _WS, "uri": _URI},
            "required": ["workspace_id", "uri"],
        },
        lambda a: resource_registry.read_resource(a["workspace_id"], a["uri"]),
        _OUTPUT,
        read,
    )
    server.register_resource_provider(lambda: [], _read_resource)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "DELIVERY_MODES",
    "EVIDENCE_POLICIES",
    "OfficialStdioServer",
    "RELEASE_SCOPES",
    "ReadResourceContents",
    "SERVER",
    "SERVER_NAME",
    "SERVER_VERSION",
    "STAGES",
    "STAGE_STATUSES",
    "_ID",
    "_KEY",
    "_OUTPUT",
    "_URI",
    "_WS",
    "_read_resource",
    "build_server",
    "get_logger",
    "json",
    "logger",
    "main",
    "resource_registry",
    "service",
    "types",
]
