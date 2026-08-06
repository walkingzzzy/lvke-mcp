"""Aggregated MCP server for local reference catalogues and archive search."""

from __future__ import annotations

from mcp import types

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.servers.lvke_reference import service

SERVER_NAME = "lvke-reference"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)
_OUTPUT = {"type": "object", "additionalProperties": True, "properties": {"success": {"type": "boolean"}}}
_STRING = {"type": "string"}


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": required}


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    server.register_tool("reference_search", "搜索本地行业、客户、专家、政策或档案数据。", _schema({
        "dataset": {"type": "string", "enum": ["industry_reports", "clients", "experts", "policies", "archive"]},
        "query": _STRING, "filters": {"type": "object"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
    }, ["dataset"]), lambda a: service.search(a["dataset"], a.get("query", ""), a.get("filters", {}), int(a.get("limit", 20))), _OUTPUT, read)
    server.register_tool("reference_get", "按 ID 读取本地参考、模板或档案章节。", _schema({
        "dataset": {"type": "string", "enum": ["industry_reports", "clients", "experts", "policies", "templates", "archive"]},
        "record_id": {"type": "string", "minLength": 1}, "view": {},
    }, ["dataset", "record_id"]), lambda a: service.get(a["dataset"], a["record_id"], a.get("view")), _OUTPUT, read)
    server.register_tool("reference_list", "列出监测点、客户项目、专家专业、统计字典或模板。", _schema({
        "dataset": {"type": "string", "enum": ["environment_locations", "client_projects", "expert_specialties", "statistics_dictionaries", "templates"]},
        "owner_id": _STRING, "filters": {"type": "object"},
    }, ["dataset"]), lambda a: service.list_items(a["dataset"], a.get("owner_id", ""), a.get("filters", {})), _OUTPUT, read)
    server.register_tool("reference_observe", "查询本地空气、水质或统计指标记录。", _schema({
        "dataset": {"type": "string", "enum": ["air_quality", "water_quality", "statistics"]},
        "subject": {"type": "string", "minLength": 1}, "period": {"type": "integer"}, "filters": {"type": "object"},
    }, ["dataset", "subject"]), lambda a: service.observe(a["dataset"], a["subject"], a.get("period"), a.get("filters", {})), _OUTPUT, read)
    server.register_tool("reference_verify", "校验本地政策记录的有效状态。", _schema({
        "dataset": {"type": "string", "enum": ["policy"]}, "record_id": {"type": "string", "minLength": 1}, "as_of": _STRING,
    }, ["dataset", "record_id"]), lambda a: service.verify(a["dataset"], a["record_id"], a.get("as_of", "")), _OUTPUT, read)
    server.register_tool("template_fill", "使用既有模板填充器生成 Markdown。", _schema({"template_id": {"type": "string", "minLength": 1}, "data": {"type": "object"}, "format": {"type": "string", "enum": ["markdown"], "default": "markdown"}}, ["template_id", "data"]), lambda a: service.fill_template(a["template_id"], a["data"], a.get("format", "markdown")), _OUTPUT, read)
    server.register_tool("geo_query", "执行本地地理编码或附近 POI 查询。", _schema({
        "operation": {"type": "string", "enum": ["geocode", "nearby_pois"]}, "query_or_point": {},
        "radius_km": {"type": "number", "exclusiveMinimum": 0, "maximum": 100, "default": 5}, "category": _STRING,
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    }, ["operation", "query_or_point"]), lambda a: service.geo_query(a["operation"], a["query_or_point"], float(a.get("radius_km", 5)), a.get("category", ""), a.get("limit")), _OUTPUT, read)
    server.register_tool("geo_distance_matrix", "使用原 Haversine 与公路系数估算实现计算起终点距离矩阵。", _schema({
        "origins": {"type": "array", "minItems": 1, "items": {}},
        "destinations": {"type": "array", "minItems": 1, "items": {}},
        "mode": {"type": "string", "enum": ["haversine_with_highway_estimate"], "default": "haversine_with_highway_estimate"},
    }, ["origins", "destinations"]), lambda a: service.geo_distance_matrix(a["origins"], a["destinations"], a.get("mode", "haversine_with_highway_estimate")), _OUTPUT, read)
    server.register_tool("archive_find_similar_projects", "调用原档案相似项目检索。", _schema({"brief": {}, "top_n": {"type": "integer", "default": 5}}, ["brief"]), service.archive_find_similar, _OUTPUT, read)
    server.register_tool("archive_extract_structure", "调用原档案章节结构提取。", _schema({"report_id": {"type": "string", "minLength": 1}, "with_appendix": {"type": "boolean", "default": True}}, ["report_id"]), service.archive_extract_structure, _OUTPUT, read)
    server.register_tool("archive_compare_cases", "调用原档案案例对比。", _schema({"report_ids": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string"}}, "dim": _STRING}, ["report_ids"]), service.archive_compare_cases, _OUTPUT, read)
    server.register_tool("archive_get_template_paragraph", "调用原档案模板段落检索。", _schema({"scene": {"type": "string", "enum": ["policy-driver", "necessity", "market-demand", "risk-financial", "risk-policy", "conclusion", "site-selection"]}, "industry": _STRING, "top_k": {"type": "integer", "default": 3}}, ["scene"]), service.archive_get_template_paragraph, _OUTPUT, read)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
