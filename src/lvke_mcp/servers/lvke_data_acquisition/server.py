"""Official-SDK MCP server for external search and immutable source snapshots."""

from __future__ import annotations

import json

from mcp import types

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.servers.lvke_data_acquisition import service

SERVER_NAME = "lvke-data-acquisition"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)

_WS = {"type": "string", "minLength": 1}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "success": {"type": "boolean"},
        "status": {"type": "string", "enum": ["ok", "partial", "empty", "blocked", "failed", "upstream_failure"]},
        "resource_uris": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["success", "status", "resource_uris", "warnings", "blockers", "next_actions"],
}

_CANDIDATE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate_id": {"type": "string"},
        "url": {"type": "string", "format": "uri"},
        "domain": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "provider": {"type": "string"},
        "rank": {"type": "integer"},
        "relevance": {"type": "number", "minimum": 0, "maximum": 1},
        "query": {"type": "string"},
        "query_index": {"type": "integer"},
    },
    "required": ["candidate_id", "url", "domain", "title", "summary", "provider", "rank", "relevance", "query", "query_index"],
}
_DISCOVER_OUTPUT = {
    **_OUTPUT_SCHEMA,
    "properties": {
        **_OUTPUT_SCHEMA["properties"],
        "discovery_set_id": {"type": "string"},
        "candidates": {"type": "array", "items": _CANDIDATE},
        "skipped": {"type": "array", "items": {"type": "object"}},
    },
    "if": {"properties": {"success": {"const": True}}},
    "then": {"required": ["discovery_set_id", "candidates", "skipped"]},
}
_COLLECT_OUTPUT = {
    **_OUTPUT_SCHEMA,
    "properties": {
        **_OUTPUT_SCHEMA["properties"],
        "source_collection_id": {"type": "string"},
        "source_snapshot_ids": {"type": "array", "items": {"type": "string"}},
        "collected": {"type": "array", "items": {"type": "object"}},
        "unknown_candidate_ids": {"type": "array", "items": {"type": "string"}},
    },
    "if": {"properties": {"success": {"const": True}}},
    "then": {"required": ["source_collection_id", "source_snapshot_ids", "collected", "unknown_candidate_ids"]},
}
_EXTERNAL_IMPORT_OUTPUT = {
    **_OUTPUT_SCHEMA,
    "properties": {
        **_OUTPUT_SCHEMA["properties"],
        "source_snapshot_id": {"type": "string"},
        "content_hash": {"type": "string"},
        "source_url": {"type": "string", "format": "uri"},
        "content_origin": {"type": "string", "const": "external_mcp_extract"},
        "provider": {"type": "string"},
        "provider_tool": {"type": "string"},
        "retrieved_at": {"type": "string"},
        "external_content_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "extraction_receipt_verified": {"type": "boolean"},
        "formal_use_allowed": {"type": "boolean"},
    },
    "if": {"properties": {"success": {"const": True}}},
    "then": {
        "required": [
            "source_snapshot_id", "content_hash", "source_url", "content_origin",
            "provider", "provider_tool", "retrieved_at", "external_content_hash",
            "extraction_receipt_verified", "formal_use_allowed",
        ]
    },
}


def _read_resource(arguments: dict) -> dict:
    uri = arguments["uri"]
    record = service.resolve_resource(
        uri,
        arguments["workspace_id"],
    )
    if record is None:
        return {
            "success": False,
            "transport_success": True,
            "business_success": False,
            "completed": False,
            "outcome": "blocked",
            "status": "blocked",
            "code": "resource_not_found",
            "message": "资源不存在或不属于当前工作区",
            "resource_uris": [],
            "warnings": [],
            "blockers": ["resource_not_found"],
            "next_actions": ["调用 data_list_resources 获取可读 URI"],
        }
    return {
        "success": True,
        "status": "ok",
        "uri": uri,
        "mime_type": "application/json",
        "content": json.dumps(record, ensure_ascii=False, indent=2),
        "resource_uris": [uri],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read_open = types.ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )
    write_open = types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )
    server.register_tool(
        "data_discover",
        "用一个或多个查询建立去重、可筛选的公开来源候选集合；候选摘要不是证据。设置 auto_expand=true 和 target_count（如 40）可从基础查询自动扩展出政策/市场/技术等可研角度，聚合到目标数量。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "queries": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "string", "minLength": 1}},
                "limit_per_query": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "domain_allowlist": {"type": "array", "maxItems": 50, "items": {"type": "string", "minLength": 1}, "default": []},
                "domain_denylist": {"type": "array", "maxItems": 50, "items": {"type": "string", "minLength": 1}, "default": []},
                "target_count": {"type": "integer", "minimum": 10, "maximum": 100, "default": 0},
                "auto_expand": {"type": "boolean", "default": False},
                "total_timeout_seconds": {"type": "number", "minimum": 1, "maximum": 300, "default": 120},
            },
            "required": ["workspace_id", "queries"],
        },
        lambda args: service.discover(
            args["workspace_id"],
            args["queries"],
            limit_per_query=int(args.get("limit_per_query", 5)),
            domain_allowlist=args.get("domain_allowlist", []),
            domain_denylist=args.get("domain_denylist", []),
            target_count=int(args.get("target_count", 0)),
            auto_expand=bool(args.get("auto_expand", False)),
            total_timeout_seconds=float(args.get("total_timeout_seconds", 120)),
        ),
        _DISCOVER_OUTPUT,
        read_open,
    )
    server.register_tool(
        "data_search",
        "搜索公开网络并返回结果元数据；不将搜索摘要冒充正式证据。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 5},
                "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 120, "default": 30},
            },
            "required": ["workspace_id", "query"],
        },
        lambda args: service.search(
            args["workspace_id"], args["query"], int(args.get("limit", 5)),
            float(args.get("timeout_seconds", 30)),
        ),
        _OUTPUT_SCHEMA,
        read_open,
    )
    server.register_tool(
        "data_fetch",
        "抓取 URL 并固化不可变原始来源快照；采集阶段不调用 LLM 总结。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "urls": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string", "format": "uri"}},
                "content_mode": {"type": "string", "enum": ["raw", "readable"], "default": "readable"},
                "extraction_provider": {
                    "type": "string",
                    "enum": ["auto", "tavily", "direct_http"],
                    "default": "auto",
                },
            },
            "required": ["workspace_id", "urls"],
        },
        lambda args: service.fetch(
            args["workspace_id"], args["urls"],
            content_mode=args.get("content_mode", "readable"),
            extraction_provider=args.get("extraction_provider", "auto"),
        ),
        _OUTPUT_SCHEMA,
        write_open,
    )
    server.register_tool(
        "data_import_external_snapshot",
        "将 Tavily Hikari 等外部 MCP 对选定公网 URL 提取的正文固化为 Lvke 不可变快照；拒绝搜索摘要、answer 和 research synthesis。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "url": {"type": "string", "format": "uri"},
                "title": {"type": "string", "maxLength": 2000},
                "content": {"type": "string", "minLength": 1, "maxLength": 5000000},
                "provider": {"type": "string", "minLength": 1, "maxLength": 200},
                "provider_tool": {"type": "string", "minLength": 1, "maxLength": 200},
                "retrieved_at": {"type": "string", "format": "date-time"},
                "content_kind": {
                    "type": "string",
                    "enum": ["extracted_full_text", "raw_content"],
                },
                "mime_type": {"type": "string", "minLength": 1, "maxLength": 200, "default": "text/markdown"},
                "extraction_receipt": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "provider": {"type": "string", "minLength": 1},
                        "provider_tool": {"type": "string", "minLength": 1},
                        "url": {"type": "string", "format": "uri"},
                        "retrieved_at": {"type": "string", "minLength": 1},
                        "content_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                        "signature": {"type": "string", "pattern": "^hmac-sha256:[0-9a-f]{64}$"},
                    },
                    "required": [
                        "provider", "provider_tool", "url", "retrieved_at",
                        "content_hash", "signature",
                    ],
                },
            },
            "required": [
                "workspace_id", "url", "title", "content", "provider",
                "provider_tool", "retrieved_at", "content_kind",
            ],
        },
        lambda args: service.import_external_snapshot(
            args["workspace_id"],
            url=args["url"],
            title=args["title"],
            content=args["content"],
            provider=args["provider"],
            provider_tool=args["provider_tool"],
            retrieved_at=args["retrieved_at"],
            content_kind=args["content_kind"],
            mime_type=args.get("mime_type", "text/markdown"),
            extraction_receipt=args.get("extraction_receipt"),
        ),
        _EXTERNAL_IMPORT_OUTPUT,
        write_open,
    )
    server.register_tool(
        "data_collect",
        "只抓取 discovery_set 中明确选定的候选 URL，并沿用 data_fetch 的 URL/SSRF/密钥安全门。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "discovery_set_id": {"type": "string", "minLength": 1},
                "selected_candidate_ids": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string", "minLength": 1}},
                "content_mode": {"type": "string", "enum": ["raw", "readable"], "default": "readable"},
            },
            "required": ["workspace_id", "discovery_set_id", "selected_candidate_ids"],
        },
        lambda args: service.collect(
            args["workspace_id"],
            args["discovery_set_id"],
            args["selected_candidate_ids"],
            content_mode=args.get("content_mode", "readable"),
        ),
        _COLLECT_OUTPUT,
        write_open,
    )
    server.register_tool(
        "data_audit_urls",
        "对 URL 执行不可变公网安全审计；可选 live 模式只检查可达性，不采集正文或授予证据资格。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "urls": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {"type": "string", "format": "uri"},
                },
                "audit_mode": {
                    "type": "string",
                    "enum": ["safety", "live"],
                    "default": "safety",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 30,
                    "default": 10,
                },
            },
            "required": ["workspace_id", "urls"],
        },
        lambda args: service.audit_urls(
            args["workspace_id"],
            args["urls"],
            audit_mode=args.get("audit_mode", "safety"),
            timeout_seconds=float(args.get("timeout_seconds", 10)),
        ),
        _OUTPUT_SCHEMA,
        write_open,
    )
    server.register_tool(
        "data_get_url_audit",
        "读取指定不可变 UrlAudit 及其 basis/content hash。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "url_audit_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "url_audit_id"],
        },
        lambda args: service.get_url_audit(
            args["workspace_id"],
            args["url_audit_id"],
        ),
        _OUTPUT_SCHEMA,
        read_open,
    )
    server.register_tool(
        "data_capture_source_view",
        "绑定已导入 PNG/JPEG、来源快照、URL、viewport 和时间；不操作浏览器且不提升正式证据资格。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "source_snapshot_id": {"type": "string", "minLength": 1},
                "image_file_id": {"type": "string", "minLength": 1},
                "url": {"type": "string", "format": "uri"},
                "viewport": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "width": {"type": "integer", "minimum": 1, "maximum": 10000},
                        "height": {"type": "integer", "minimum": 1, "maximum": 10000},
                        "device_scale_factor": {
                            "type": "number",
                            "minimum": 0.1,
                            "maximum": 10,
                            "default": 1,
                        },
                    },
                    "required": ["width", "height"],
                },
                "captured_at": {"type": "string", "format": "date-time"},
                "image_content_hash": {
                    "type": "string",
                    "pattern": r"^(?:sha256:)?[0-9a-fA-F]{64}$",
                },
                "page_title": {"type": "string", "maxLength": 2000},
            },
            "required": [
                "workspace_id",
                "source_snapshot_id",
                "image_file_id",
                "url",
                "viewport",
                "captured_at",
            ],
        },
        lambda args: service.capture_source_view(
            args["workspace_id"],
            source_snapshot_id=args["source_snapshot_id"],
            image_file_id=args["image_file_id"],
            url=args["url"],
            viewport=args["viewport"],
            captured_at=args["captured_at"],
            image_content_hash=args.get("image_content_hash", ""),
            page_title=args.get("page_title", ""),
        ),
        _OUTPUT_SCHEMA,
        write_open,
    )
    server.register_tool(
        "data_get_visual_capture",
        "读取指定不可变 VisualSourceCapture 及其来源和截图 lineage。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "visual_capture_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "visual_capture_id"],
        },
        lambda args: service.get_visual_capture(
            args["workspace_id"],
            args["visual_capture_id"],
        ),
        _OUTPUT_SCHEMA,
        read_open,
    )
    server.register_tool(
        "data_provider_status",
        "列出不含密钥的 Web provider 能力与可用状态。",
        {"type": "object", "additionalProperties": False, "properties": {}},
        lambda _args: service.provider_status(),
        _OUTPUT_SCHEMA,
        read_open,
    )
    server.register_tool(
        "data_list_resources",
        "按显式工作区分页列举采集来源及不可变过程记录。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "resource_type": {
                    "type": "string",
                    "enum": [
                        "source_snapshot",
                        "search_set",
                        "discovery_set",
                        "source_collection",
                        "url_audit",
                        "visual_capture",
                    ],
                },
                "cursor": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            "required": ["workspace_id"],
        },
        lambda args: service.list_resources(
            args["workspace_id"],
            resource_type=args.get("resource_type", ""),
            cursor=args.get("cursor", ""),
            limit=int(args.get("limit", 50)),
        ),
        _OUTPUT_SCHEMA,
        read_open,
    )
    server.register_tool(
        "data_read_resource",
        "在显式工作区作用域内读取不可变采集 Resource。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "uri": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "uri"],
        },
        _read_resource,
        _OUTPUT_SCHEMA,
        read_open,
    )
    # Protocol-level Resource requests carry no workspace scope. Keep them
    # disabled; scoped tools above are authoritative.
    server.register_resource_provider(lambda: [], lambda _uri: None)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
