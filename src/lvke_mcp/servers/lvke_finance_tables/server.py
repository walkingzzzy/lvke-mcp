"""Official-SDK MCP server for deterministic thirteen-table delivery views."""

from __future__ import annotations

import base64
import json

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.domains.finance import tables_service as service

SERVER_NAME = "lvke-finance-tables"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)
_OUTPUT = {
    "type": "object", "additionalProperties": True,
    "properties": {
        "success": {"type": "boolean"}, "status": {"type": "string"},
        "validation_complete": {"type": "boolean", "description": "由宿主正式发布门禁决定；表名齐全或 XLSX 写出绝不单独为 true"},
        "resource_uris": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["success", "status", "resource_uris", "warnings", "blockers", "next_actions"],
    # validation_complete 只有在真的渲染/校验过表包时才有意义。此前它无条件必填，
    # 于是入参被拒（非法 run_id）这类阻断载荷撞上自己的 schema，被 transport 改写成
    # invalid_tool_output + system_success=False —— 谎报成服务端故障。成功路径仍必填，
    # 否则"表名齐全≠正式可用"这个关键约束就被放弃了。
    "allOf": [{
        "if": {
            "properties": {"success": {"const": True}},
            "required": ["success"],
        },
        "then": {"required": ["validation_complete"]},
    }],
}
_VALIDATE_OUTPUT = _OUTPUT
_BASE = {
    "workspace_id": {"type": "string", "minLength": 1},
    "run_id": {"type": "string", "minLength": 1},
}
_TEMPLATE_VERSION = {"type": "string", "minLength": 1, "description": "可选版本钉住断言；与 run 固化模板版本不一致时报错，不做版本转换"}
_SOURCE_PACKAGE_ID = {
    "type": "string",
    "minLength": 1,
    "description": (
        "可选：消费既有交付表 package 而不重新渲染。省略时会新渲染一个包，"
        "其 content hash 可能与主包不同（package payload 内嵌可变门禁状态）。"
        "要求 XLSX/CSV 与主包同一 package_id 时必须显式传入。"
    ),
}
_TABLE_ID_ALIASES = {
    "construction_interest": "interest-during-construction",
    "working_capital": "working-capital",
    "income_statement": "income-statement",
    "total_cost": "total-cost",
    "profit_distribution": "profit-distribution",
    "debt_service": "debt-service",
    "capital_cashflow": "capital-cashflow",
    "financial_plan": "financial-plan",
}


def _canonical_table_id(table_id: str) -> str:
    return _TABLE_ID_ALIASES.get(table_id, table_id)


def _public_table_ids() -> list[str]:
    canonical = [item["table_id"] for item in service.table_registry()]
    return sorted(set([*canonical, *_TABLE_ID_ALIASES]))


def _resource(uri: str):
    if uri.endswith(("/xlsx", "/xlsx-technical")):
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
            "message": "资源不存在或不属于当前工作区", "validation_complete": False,
            "resource_uris": [], "warnings": [], "blockers": ["resource_not_found"],
            "next_actions": ["调用 lvke_list_resources(domain='finance-tables') 获取当前工作区可读 URI"],
        }
    content, mime_type = resolved
    encoded = isinstance(content, bytes)
    return {
        "success": True, "status": "ok", "validation_complete": False,
        "uri": uri, "mime_type": mime_type,
        "content_encoding": "base64" if encoded else "utf-8",
        "content": base64.b64encode(content).decode("ascii") if encoded else content,
        "resource_uris": [uri], "warnings": [], "blockers": [], "next_actions": [],
    }


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    write = types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    server.register_tool("tables_render", "只消费 run_id 渲染交付表并固化 package；绝不重新计算财务。", {"type": "object", "additionalProperties": False, "properties": {**_BASE, "format": {"type": "string", "enum": ["structured", "markdown"], "default": "structured"}, "template_version": _TEMPLATE_VERSION}, "required": ["workspace_id", "run_id"]}, lambda a: service.render(a["workspace_id"], a["run_id"], a.get("format", "structured"), a.get("template_version", "")), _OUTPUT, write)
    server.register_tool("tables_validate", "校验 run 的交付表 manifest 与必需表；默认 formal，任一正式 blocker 都返回业务失败。", {"type": "object", "additionalProperties": False, "properties": {**_BASE, "validation_scope": {"type": "string", "enum": ["technical", "formal"], "default": "formal", "description": "technical 仅校验结构，formal 还要求宿主正式门禁"}}, "required": ["workspace_id", "run_id"]}, lambda a: service.validate(a["workspace_id"], a["run_id"], validation_scope=a.get("validation_scope", "formal")), _VALIDATE_OUTPUT, read)
    server.register_tool(
        "tables_export_xlsx",
        "从同一 run_id 导出全部 14 张交付财务表的 XLSX（原十三表 + 附表11 财务计划"
        "现金流量表）；默认 validation_scope=formal。"
        "formal 门禁未过时**不拒绝导出**，而是降级为「正式候选·含限制」：文件内写入"
        "不得对外正式交付的标记，未通过项进 release_limitations，需以 tables_validate"
        "(validation_scope=formal) 的 blockers 为正式资格判据。"
        "validation_scope='technical' 时文件内逐表标记为估算预览且永不可取得正式资格。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                **_BASE,
                "template_version": _TEMPLATE_VERSION,
                "finance_tables_package_id": _SOURCE_PACKAGE_ID,
                "validation_scope": {
                    "type": "string",
                    "enum": ["technical", "formal"],
                    "default": "formal",
                    "description": (
                        "technical：产出带文件内警示的过程验收 XLSX，"
                        "validation_complete 恒为 false；"
                        "formal：门禁未过时降级为「正式候选·含限制」并写入文件内标记，不拒绝导出。"
                    ),
                },
            },
            "required": ["workspace_id", "run_id"],
        },
        lambda a: service.export_xlsx(
            a["workspace_id"],
            a["run_id"],
            a.get("template_version", ""),
            a.get("finance_tables_package_id", ""),
            a.get("validation_scope", "formal"),
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "tables_export_csv",
        "从同一 run_id 原生导出 15 个 UTF-8 BOM CSV（14 张交付表 + 数据血缘表）；只写标量单元格，"
        "给定 finance_tables_package_id 时消费既有包。默认 validation_scope=formal；"
        "formal 门禁未过时降级为含限制的正式候选件（文件内写入限制标记、未通过项进 release_limitations），不是拒绝导出；"
        "validation_scope='technical' 可产出过程验收文件，但文件首行标记不可正式使用。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                **_BASE,
                "template_version": _TEMPLATE_VERSION,
                "finance_tables_package_id": _SOURCE_PACKAGE_ID,
                "validation_scope": {
                    "type": "string",
                    "enum": ["technical", "formal"],
                    "default": "formal",
                    "description": (
                        "formal：门禁未过时降级为含限制的正式候选件并写入限制标记，不拒绝导出。"
                        "technical：产出过程验收 CSV，"
                        "每个文件首行写入不可正式使用标记，release_grade=technical_preview，"
                        "validation_complete 恒为 false。"
                    ),
                },
            },
            "required": ["workspace_id", "run_id"],
        },
        lambda a: service.export_csv(
            a["workspace_id"],
            a["run_id"],
            a.get("template_version", ""),
            a.get("finance_tables_package_id", ""),
            a.get("validation_scope", "formal"),
        ),
        _OUTPUT,
        write,
    )
    server.register_tool("tables_get_package", "读取已固化交付表 package 摘要（14 张交付表）。", {"type": "object", "additionalProperties": False, "properties": {"workspace_id": _BASE["workspace_id"], "finance_tables_package_id": {"type": "string", "minLength": 1}}, "required": ["workspace_id", "finance_tables_package_id"]}, lambda a: service.get_package(a["workspace_id"], a["finance_tables_package_id"]), _OUTPUT, read)
    package_table_properties = {
        "workspace_id": _BASE["workspace_id"],
        "finance_tables_package_id": {"type": "string", "minLength": 1},
        "expected_run_id": {"type": "string", "minLength": 1},
    }
    server.register_tool(
        "tables_list_tables",
        "列出不可变交付表 package 中的固定表注册表与单表 Resource；不重新渲染。",
        {"type": "object", "additionalProperties": False, "properties": package_table_properties, "required": ["workspace_id", "finance_tables_package_id"]},
        lambda a: service.list_tables(a["workspace_id"], a["finance_tables_package_id"], a.get("expected_run_id", "")),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "tables_get_table",
        "从已固化 package 按 table_id 读取单表；支持 structured、markdown、csv，不重新计算。",
        {"type": "object", "additionalProperties": False, "properties": {**package_table_properties, "table_id": {"type": "string", "enum": _public_table_ids()}, "format": {"type": "string", "enum": ["structured", "markdown", "csv"], "default": "structured"}}, "required": ["workspace_id", "finance_tables_package_id", "table_id"]},
        lambda a: service.get_table(a["workspace_id"], a["finance_tables_package_id"], _canonical_table_id(a["table_id"]), a.get("format", "structured"), a.get("expected_run_id", "")),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "tables_validate_table",
        "局部校验已固化 package 中的一张表；结果不能替代整包勾稽或正式交付门禁。",
        {"type": "object", "additionalProperties": False, "properties": {**package_table_properties, "table_id": {"type": "string", "enum": _public_table_ids()}}, "required": ["workspace_id", "finance_tables_package_id", "table_id"]},
        lambda a: service.validate_table(a["workspace_id"], a["finance_tables_package_id"], _canonical_table_id(a["table_id"]), a.get("expected_run_id", "")),
        _OUTPUT,
        read,
    )
    # Protocol-level Resource calls lack workspace identity. Dynamic access is
    # centralized in lvke-feasibility-delivery.
    server.register_resource_provider(lambda: [], lambda _uri: None)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
