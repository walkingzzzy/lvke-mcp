"""Official-SDK MCP server for deterministic thirteen-table delivery views."""

from __future__ import annotations

import base64
import json

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.servers.lvke_finance_tables import service

SERVER_NAME = "lvke-finance-tables"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)
_OUTPUT = {"type": "object", "additionalProperties": True, "properties": {"success": {"type": "boolean"}, "status": {"type": "string"}, "formal_delivery_ready": {"type": "boolean", "description": "由宿主正式发布门禁决定；表名齐全或 XLSX 写出绝不单独为 true"}, "resource_uris": {"type": "array", "items": {"type": "string"}}, "warnings": {"type": "array", "items": {"type": "string"}}, "blockers": {"type": "array", "items": {"type": "string"}}, "next_actions": {"type": "array", "items": {"type": "string"}}}, "required": ["success", "status", "formal_delivery_ready", "resource_uris", "warnings", "blockers", "next_actions"]}
_VALIDATE_OUTPUT = {
    **_OUTPUT,
    "properties": {
        **_OUTPUT["properties"],
        "full_review_required": {"const": True},
        "review_id": {"type": ["string", "null"]},
        "deliverable_review_id": {"type": ["string", "null"]},
        "deliverable_review_status": {"type": "string"},
        "deliverable_formally_deliverable": {"type": "boolean"},
    },
    "required": [
        *_OUTPUT["required"],
        "full_review_required",
        "review_id",
        "deliverable_review_id",
        "deliverable_review_status",
        "deliverable_formally_deliverable",
    ],
}
_BASE = {
    "workspace_id": {"type": "string", "minLength": 1},
    "run_id": {"type": "string", "minLength": 1},
}
_TEMPLATE_VERSION = {"type": "string", "minLength": 1, "description": "可选版本钉住断言；与 run 固化模板版本不一致时报错，不做版本转换"}


def _resource(uri: str):
    if uri.endswith("/xlsx"):
        path = service.xlsx_path_from_uri(uri)
        return None if path is None else ReadResourceContents(path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if "/csv/" in uri:
        path = service.csv_path_from_uri(uri)
        return None if path is None else ReadResourceContents(path.read_bytes(), "text/csv; charset=utf-8")
    record = service.CSV_EXPORT_STORE.resolve_uri(uri) or service.PACKAGE_STORE.resolve_uri(uri)
    return None if record is None else ReadResourceContents(json.dumps(record, ensure_ascii=False, indent=2), "application/json")


def _read_scoped_resource(
    workspace_id: str,
    uri: str,
) -> dict:
    resolved = service.resolve_resource(uri, workspace_id)
    if resolved is None:
        return {
            "success": False, "transport_success": True, "business_success": False,
            "completed": False, "outcome": "blocked",
            "status": "blocked", "code": "resource_not_found",
            "message": "资源不存在或不属于当前工作区", "formal_delivery_ready": False,
            "resource_uris": [], "warnings": [], "blockers": ["resource_not_found"],
            "next_actions": ["调用 tables_list_resources 获取当前工作区可读 URI"],
        }
    content, mime_type = resolved
    encoded = isinstance(content, bytes)
    return {
        "success": True, "status": "ok", "formal_delivery_ready": False,
        "uri": uri, "mime_type": mime_type,
        "content_encoding": "base64" if encoded else "utf-8",
        "content": base64.b64encode(content).decode("ascii") if encoded else content,
        "resource_uris": [uri], "warnings": [], "blockers": [], "next_actions": [],
    }


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    write = types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    server.register_tool("tables_render", "只消费 run_id 渲染十三表并固化 package；绝不重新计算财务。", {"type": "object", "additionalProperties": False, "properties": {**_BASE, "format": {"type": "string", "enum": ["structured", "markdown"], "default": "structured"}, "template_version": _TEMPLATE_VERSION}, "required": ["workspace_id", "run_id"]}, lambda a: service.render(a["workspace_id"], a["run_id"], a.get("format", "structured"), a.get("template_version", "")), _OUTPUT, write)
    server.register_tool("tables_validate", "校验 run 的十三表 manifest 与必需表；默认 formal，任一正式 blocker 都返回业务失败。", {"type": "object", "additionalProperties": False, "properties": {**_BASE, "validation_scope": {"type": "string", "enum": ["technical", "formal"], "default": "formal", "description": "technical 仅校验结构，formal 还要求宿主正式门禁"}}, "required": ["workspace_id", "run_id"]}, lambda a: service.validate(a["workspace_id"], a["run_id"], validation_scope=a.get("validation_scope", "formal")), _VALIDATE_OUTPUT, read)
    server.register_tool("tables_export_xlsx", "从同一 run_id 导出带 lineage 的 XLSX；不接收任何散乱财务参数。", {"type": "object", "additionalProperties": False, "properties": {**_BASE, "template_version": _TEMPLATE_VERSION}, "required": ["workspace_id", "run_id"]}, lambda a: service.export_xlsx(a["workspace_id"], a["run_id"], a.get("template_version", "")), _OUTPUT, write)
    server.register_tool("tables_export_csv", "从同一 run_id 原生导出十三张 UTF-8 BOM CSV；只写标量单元格，不把 JSON 序列化入表。", {"type": "object", "additionalProperties": False, "properties": {**_BASE, "template_version": _TEMPLATE_VERSION}, "required": ["workspace_id", "run_id"]}, lambda a: service.export_csv(a["workspace_id"], a["run_id"], a.get("template_version", "")), _OUTPUT, write)
    server.register_tool("tables_get_package", "读取已固化十三表 package 摘要。", {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _BASE["workspace_id"], "finance_tables_package_id": {"type": "string", "minLength": 1}}, "required": ["workspace_id", "finance_tables_package_id"]}, lambda a: service.get_package(a["workspace_id"], a["finance_tables_package_id"]), _OUTPUT, read)
    package_table_properties = {
        "workspace_id": _BASE["workspace_id"],
        "finance_tables_package_id": {"type": "string", "minLength": 1},
        "expected_run_id": {"type": "string", "minLength": 1},
    }
    server.register_tool(
        "tables_list_tables",
        "列出不可变十三表 package 中的固定表注册表与单表 Resource；不重新渲染。",
        {"type": "object", "additionalProperties": False, "properties": package_table_properties, "required": ["workspace_id", "finance_tables_package_id"]},
        lambda a: service.list_tables(a["workspace_id"], a["finance_tables_package_id"], a.get("expected_run_id", "")),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "tables_get_table",
        "从已固化 package 按 table_id 读取单表；支持 structured、markdown、csv，不重新计算。",
        {"type": "object", "additionalProperties": False, "properties": {**package_table_properties, "table_id": {"type": "string", "enum": [item["table_id"] for item in service.table_registry()]}, "format": {"type": "string", "enum": ["structured", "markdown", "csv"], "default": "structured"}}, "required": ["workspace_id", "finance_tables_package_id", "table_id"]},
        lambda a: service.get_table(a["workspace_id"], a["finance_tables_package_id"], a["table_id"], a.get("format", "structured"), a.get("expected_run_id", "")),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "tables_validate_table",
        "局部校验已固化 package 中的一张表；结果不能替代整包勾稽或正式交付门禁。",
        {"type": "object", "additionalProperties": False, "properties": {**package_table_properties, "table_id": {"type": "string", "enum": [item["table_id"] for item in service.table_registry()]}}, "required": ["workspace_id", "finance_tables_package_id", "table_id"]},
        lambda a: service.validate_table(a["workspace_id"], a["finance_tables_package_id"], a["table_id"], a.get("expected_run_id", "")),
        _OUTPUT,
        read,
    )
    for table in service.table_registry():
        table_id = table["table_id"]
        server.register_tool(
            table["alias_tool"],
            f"读取{table['delivery_no']}《{table['title']}》；只消费已固化 package，不重新计算。",
            {"type": "object", "additionalProperties": False, "properties": {**package_table_properties, "format": {"type": "string", "enum": ["structured", "markdown", "csv"], "default": "structured"}}, "required": ["workspace_id", "finance_tables_package_id"]},
            lambda a, fixed_table_id=table_id: service.get_table(a["workspace_id"], a["finance_tables_package_id"], fixed_table_id, a.get("format", "structured"), a.get("expected_run_id", "")),
            _OUTPUT,
            read,
        )
    server.register_tool("tables_list_resources", "按显式工作区分页列举十三表 package、manifest、CSV 与 XLSX。", {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _BASE["workspace_id"], "resource_type": {"type": "string", "enum": ["package", "csv_manifest", "csv", "xlsx"]}, "cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}, "required": ["workspace_id"]}, lambda a: service.list_resources(a["workspace_id"], resource_type=a.get("resource_type", ""), cursor=a.get("cursor", ""), limit=int(a.get("limit", 50))), _OUTPUT, read)
    server.register_tool("tables_read_resource", "在显式工作区内读取十三表 Resource；支持 uri/resource_uri 两种兼容字段，二进制内容以 base64 返回。", {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _BASE["workspace_id"], "uri": {"type": "string", "minLength": 1}, "resource_uri": {"type": "string", "minLength": 1}}, "required": ["workspace_id"], "oneOf": [{"required": ["uri"]}, {"required": ["resource_uri"]}]}, lambda a: _read_scoped_resource(a["workspace_id"], a.get("uri") or a.get("resource_uri") or ""), _OUTPUT, read)
    # Protocol-level Resource calls lack workspace identity. Dynamic access is
    # available only through the two scoped tools above.
    server.register_resource_provider(lambda: [], lambda _uri: None)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
