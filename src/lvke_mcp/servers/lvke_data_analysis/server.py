"""Official-SDK MCP server for source analysis and evidence packs."""

from __future__ import annotations

import json

from mcp import types

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.servers.lvke_data_analysis import service

SERVER_NAME = "lvke-data-analysis"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)

_OUTPUT = {
    "type": "object", "additionalProperties": True,
    "properties": {
        "success": {"type": "boolean"},
        "status": {
            "type": "string",
            "enum": ["ok", "partial", "missing_inputs", "blocked", "failed", "upstream_failure"],
        },
        "resource_uris": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["success", "status", "resource_uris", "warnings", "blockers", "next_actions"],
}
_INDEXED_OUTPUT = {
    **_OUTPUT,
    "properties": {
        **_OUTPUT["properties"],
        # 「摄入成功」不等于「检索得到」：把可检索规模显式声明出来，
        # 让调用方能区分"没索引到正文"与"正文里确实没这个词"。
        "indexed_char_count": {"type": "integer", "minimum": 0},
        "indexed_cjk_char_count": {"type": "integer", "minimum": 0},
        "indexed_document_count": {"type": "integer", "minimum": 0},
        "empty_content_source_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        *_OUTPUT["required"],
        "indexed_char_count",
        "indexed_cjk_char_count",
        "indexed_document_count",
    ],
}
_WS = {"type": "string", "minLength": 1}
_IDS = {
    "type": "array", "maxItems": 100,
    "items": {"type": "string", "minLength": 1}, "default": [],
}
_EVIDENCE_TRACK = {
    "type": "string",
    "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption"],
    "default": "real",
}
_RECONSTRUCTION_RECORD = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reconstruction_id": {"type": "string", "minLength": 1},
        "source_uri": {"type": "string", "pattern": r"^lvke://.+"},
        "content_hash": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
        "locator": {"type": "string", "minLength": 1},
        "source_kind": {"type": "string", "enum": ["client_report", "finance_template", "historical_statement", "scenario_note"]},
        "method": {"type": "string", "enum": ["table_extract", "formula_replay", "explicit_mapping"]},
        "original_formula_available": {"type": "boolean"},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["reconstruction_id", "source_uri", "content_hash", "locator", "source_kind", "method", "original_formula_available", "limitations"],
}
_FIXTURE_MANIFEST = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fixture_id": {"type": "string", "minLength": 1},
        "fixture_version": {"type": "string", "minLength": 1},
        "project_type": {"type": "string", "minLength": 1},
        "industry_code": {"type": "string", "minLength": 1},
        "source_snapshot_ids": _IDS,
        "content_hashes": {"type": "object", "additionalProperties": {"type": "string", "pattern": r"^(?:sha256:)?[0-9a-fA-F]{64}$"}},
        "allowed_fields": _IDS,
        "prohibited_extrapolations": _IDS,
        "generated_at": {"type": "string", "format": "date-time"},
        "generator_version": {"type": "string", "minLength": 1},
        "test_scope": _IDS,
        "reconstruction_records": {"type": "array", "items": _RECONSTRUCTION_RECORD},
    },
    "required": ["fixture_id", "fixture_version", "project_type", "industry_code", "source_snapshot_ids", "content_hashes", "allowed_fields", "prohibited_extrapolations", "generated_at", "generator_version", "test_scope"],
}
_MISSING_OBSERVATION = {
    "type": "object", "additionalProperties": False,
    "properties": {"source_id": {"type": "string"}, "metric": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["source_id", "metric", "reason"],
}
_COMPARE_OUTPUT = {
    **_OUTPUT,
    "properties": {
        **_OUTPUT["properties"],
        "consistent": {"type": "array", "items": {"type": "object"}},
        "conflicts": {"type": "array", "items": {"type": "object"}},
        "missing": {"type": "array", "items": _MISSING_OBSERVATION},
        "unable_to_compare": {"type": "array", "items": {"type": "object"}},
        "period_mismatches": {"type": "array", "items": {"type": "object"}},
    },
    "required": [*_OUTPUT["required"], "consistent", "conflicts", "missing", "unable_to_compare", "period_mismatches"],
}
_MISSING_FIELD = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "field": {"type": "string"},
        "reason": {"type": "string"},
        "aliases_tried": {"type": "array", "items": {"type": "string"}},
        "expected_unit": {"type": ["string", "null"]},
        "source_ids": {"type": "array", "items": {"type": "string"}},
        "next_action": {"type": "string"},
    },
    "required": ["field", "reason"],
}
_PACK_OUTPUT = {
    **_OUTPUT,
    "properties": {
        **_OUTPUT["properties"],
        "evidence_pack_id": {"type": "string"},
        "missing_fields": {"type": "array", "items": _MISSING_FIELD},
    },
    # 失败路径（如 analysis_task_not_found）不携带 pack 字段，成功路径必须给出
    # missing_fields（无缺口时为空数组），缺口不允许静默省略。
    "if": {
        "properties": {"status": {"enum": ["ok", "partial"]}},
        "required": ["status"],
    },
    "then": {"required": ["evidence_pack_id", "missing_fields"]},
}
_EXTRACT_OUTPUT = {
    **_OUTPUT,
    "properties": {
        **_OUTPUT["properties"],
        "candidate_set_id": {"type": "string"},
        "fact_candidates": {"type": "array", "items": {"type": "object"}},
        "missing_fields": {"type": "array", "items": _MISSING_FIELD},
    },
    "if": {"properties": {"status": {"enum": ["ok", "partial"]}}, "required": ["status"]},
    "then": {"required": ["candidate_set_id", "fact_candidates", "missing_fields"]},
}
_PROFILE_OUTPUT = {
    **_OUTPUT,
    "properties": {
        **_OUTPUT["properties"],
        "data_profile_id": {"type": "string"},
        "profiles": {"type": "array", "items": {"type": "object"}},
        "skipped": {"type": "array", "items": {"type": "object"}},
    },
    "if": {"properties": {"status": {"enum": ["ok", "partial"]}}, "required": ["status"]},
    "then": {"required": ["data_profile_id", "profiles", "skipped"]},
}
_NORMALIZE_OUTPUT = {
    **_OUTPUT,
    "properties": {
        **_OUTPUT["properties"],
        "comparison_id": {"type": "string"},
        "normalized_observations": {"type": "array", "items": {"type": "object"}},
        "unprocessed": {"type": "array", "items": {"type": "object"}},
        "comparison": {"type": "object"},
    },
    "if": {"properties": {"success": {"const": True}}},
    "then": {"required": ["comparison_id", "normalized_observations", "unprocessed", "comparison"]},
}
_TREND_OUTPUT = {
    **_OUTPUT,
    "properties": {
        **_OUTPUT["properties"],
        "financial_trend_id": {"type": "string"},
        "results": {"type": "array", "items": {"type": "object"}},
        "rejected": {"type": "array", "items": {"type": "object"}},
        "issues": {"type": "array", "items": {"type": "object"}},
    },
    "if": {"properties": {"success": {"const": True}}},
    "then": {"required": ["financial_trend_id", "results", "rejected", "issues"]},
}
_BENCHMARK_OBSERVATION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "benchmark_id": {"type": "string", "minLength": 1},
        "source_id": {"type": "string", "minLength": 1},
        "metric": {"type": "string", "minLength": 1},
        "value": {"type": "number"},
        "unit": {"type": "string", "minLength": 1},
        "period": {"type": "string", "minLength": 1},
        "region": {"type": "string", "minLength": 1},
        "scope": {"type": "string", "minLength": 1},
        "tax_basis": {"type": "string", "minLength": 1},
        "locator": {
            "type": "object",
            "additionalProperties": True,
            "description": "来源中的页码、表格、单元格或正文定位信息",
        },
    },
    "required": [
        "source_id",
        "metric",
        "value",
        "unit",
        "period",
        "region",
        "scope",
        "tax_basis",
        "locator",
    ],
}
_BENCHMARK_OUTPUT = {
    **_OUTPUT,
    "properties": {
        **_OUTPUT["properties"],
        "benchmark_comparison_id": {"type": "string"},
        "comparable_results": {"type": "array", "items": {"type": "object"}},
        "unable_to_compare": {"type": "array", "items": {"type": "object"}},
        "aggregation": {"type": "string", "const": "none"},
    },
    "if": {
        "properties": {"status": {"enum": ["ok", "partial"]}},
        "required": ["status"],
    },
    "then": {
        "required": [
            "benchmark_comparison_id",
            "comparable_results",
            "unable_to_compare",
            "aggregation",
        ]
    },
}


def _read_resource(arguments: dict) -> dict:
    workspace_id = arguments["workspace_id"]
    uri = arguments["uri"]
    record = service.resolve_resource(
        uri,
        workspace_id,
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
            "next_actions": ["调用 analysis_list_resources 获取可读 URI"],
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
    read = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    write = types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    server.register_tool("analysis_ingest", "接收来源快照或受控文件解析结果，建立可查询分析任务；partial 不冒充完整。", {
        "type": "object", "additionalProperties": False,
        "properties": {"workspace_id": _WS, "source_snapshot_ids": _IDS, "file_ids": _IDS},
        "required": ["workspace_id"],
    }, lambda a: service.ingest(
        a["workspace_id"], a.get("source_snapshot_ids", []), a.get("file_ids", []),
    ), _OUTPUT, write)
    server.register_tool(
        "analysis_status",
        "读取解析、入库和 partial/失败状态，并返回已索引字符数/文档数与失败原因。",
        {
            "type": "object", "additionalProperties": False,
            "properties": {"workspace_id": _WS, "analysis_task_id": {"type": "string", "minLength": 1}},
            "required": ["workspace_id", "analysis_task_id"],
        },
        lambda a: service.status(a["workspace_id"], a["analysis_task_id"]),
        _INDEXED_OUTPUT,
        read,
    )
    server.register_tool(
        "analysis_query",
        "在已摄入来源中检索并返回原始 locator 与资格状态；中文按确定性 n-gram 分词并支持子串回退，"
        "空结果会说明是未索引到正文还是正文确实不含该词。",
        {
            "type": "object", "additionalProperties": False,
            "properties": {"workspace_id": _WS, "analysis_task_id": {"type": "string", "minLength": 1}, "query": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
            "required": ["workspace_id", "analysis_task_id", "query"],
        },
        lambda a: service.query(
            a["workspace_id"], a["analysis_task_id"], a["query"], int(a.get("limit", 20)),
        ),
        _INDEXED_OUTPUT,
        read,
    )
    field_spec = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "field": {"type": "string", "minLength": 1, "description": "稳定字段名，例如 annual_revenue_wan 或 项目总投资"},
            "aliases": {"type": "array", "maxItems": 20, "items": {"type": "string", "minLength": 1}, "default": [], "description": "资料中的同义标签，例如 年营业收入、营业收入（万元）"},
            "expected_unit": {"type": "string", "minLength": 1, "description": "必须与原文单位相容，例如 万元、%、万人、平方米；不自动换算"},
            "source_ids": {"type": "array", "maxItems": 100, "items": {"type": "string", "minLength": 1}, "default": []},
            # Gate-2 qualifiers: a prose number is attributed to this field only
            # when every ``require_terms`` token sits in the winning label's local
            # window and no ``exclude_terms`` token does — the only way to split
            # near-synonym measures (销项 vs 进项税率) whose labels alone collide.
            "require_terms": {"type": "array", "maxItems": 10, "items": {"type": "string", "minLength": 1}, "default": []},
            "exclude_terms": {"type": "array", "maxItems": 10, "items": {"type": "string", "minLength": 1}, "default": []},
        },
        "required": ["field"],
    }
    server.register_tool("analysis_extract_candidates", "按调用方明确字段、别名与期望单位，从已摄入资料确定性提取带 locator 的事实候选；散文数值须过复合单位/最近标签+限定词/单位相容三道门才给 numeric_value，任一不过留空，绝不猜。", {
        "type": "object", "additionalProperties": False,
        "properties": {"workspace_id": _WS, "analysis_task_id": {"type": "string", "minLength": 1}, "field_specs": {"type": "array", "minItems": 1, "maxItems": 50, "items": field_spec, "description": "示例：[{'field':'annual_revenue_wan','aliases':['年营业收入'],'expected_unit':'万元'}]"}},
        "required": ["workspace_id", "analysis_task_id", "field_specs"],
    }, lambda a: service.extract_candidates(
        a["workspace_id"], a["analysis_task_id"], a["field_specs"],
    ), _EXTRACT_OUTPUT, write)
    server.register_tool("analysis_profile_tabular", "只对已摄入受控资料的 cell locator 做表格画像，输出表头、已观察尺寸、数值/公式统计；不重算公式。", {
        "type": "object", "additionalProperties": False,
        "properties": {"workspace_id": _WS, "analysis_task_id": {"type": "string", "minLength": 1}, "file_ids": _IDS},
        "required": ["workspace_id", "analysis_task_id"],
    }, lambda a: service.profile_tabular(
        a["workspace_id"], a["analysis_task_id"], a.get("file_ids", []),
    ), _PROFILE_OUTPUT, write)
    observation = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "source_id": {"type": "string"}, "metric": {"type": "string"}, "value": {},
            "unit": {"type": "string"}, "as_of": {"type": "string"}, "period": {"type": "string"},
            "scope": {"type": "string"}, "entity": {"type": "string"},
            "dimension": {"type": "string"}, "segment": {"type": "string"},
            "statement": {"type": "string"}, "is_common_size_base": {"type": "boolean"},
            "locator": {"type": "object"},
        },
        "required": ["source_id", "metric", "value"],
    }
    comparison_mode = {"type": "string", "enum": ["source_reconciliation", "peer", "segment"], "default": "source_reconciliation"}
    server.register_tool("analysis_compare", "按来源对账、同业或可加总分部三种显式语义比较观察值；期间粒度不一致会返回 partial。", {
        "type": "object", "additionalProperties": False,
        "properties": {"observations": {"type": "array", "minItems": 2, "items": observation}, "comparison_mode": comparison_mode},
        "required": ["observations"],
    }, lambda a: service.compare(a["observations"], a.get("comparison_mode", "source_reconciliation")), _COMPARE_OUTPUT, read)
    conversion_rule = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "metric": {"type": "string", "minLength": 1},
            "source_unit": {"type": "string", "minLength": 1},
            "target_unit": {"type": "string", "minLength": 1},
            "factor": {"type": "number", "exclusiveMinimum": 0},
            "conversion_basis": {"type": "string", "minLength": 1},
        },
        "required": ["metric", "source_unit", "target_unit", "factor", "conversion_basis"],
    }
    server.register_tool("analysis_normalize_compare", "按调用方规则或显式启用的受控精确单位字典归一化，再按指定业务语义比较；绝不模糊猜测。", {
        "type": "object", "additionalProperties": False,
        "properties": {
            "workspace_id": _WS,
            "observations": {"type": "array", "minItems": 1, "maxItems": 500, "items": observation},
            "conversion_rules": {"type": "array", "maxItems": 100, "items": conversion_rule, "default": []},
            "use_controlled_unit_dictionary": {"type": "boolean", "default": False},
            "comparison_mode": comparison_mode,
        },
        "required": ["workspace_id", "observations"],
    }, lambda a: service.normalize_compare(
        a["workspace_id"], a["observations"], a.get("conversion_rules", []),
        use_controlled_unit_dictionary=bool(a.get("use_controlled_unit_dictionary", False)),
        comparison_mode=a.get("comparison_mode", "source_reconciliation"),
    ), _NORMALIZE_OUTPUT, write)
    server.register_tool(
        "analysis_compare_benchmark",
        "按完全一致的指标、期间、地区、范围、单位和税基比较项目值与 benchmark；口径不兼容时返回 partial 且不计算偏差。",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _WS,
                "subject": _BENCHMARK_OBSERVATION,
                "benchmarks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": _BENCHMARK_OBSERVATION,
                },
                "attention_threshold_pct": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1000,
                    "default": 15,
                },
                "material_threshold_pct": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000,
                    "default": 30,
                },
            },
            "required": ["workspace_id", "subject", "benchmarks"],
        },
        lambda a: service.compare_benchmark(
            a["workspace_id"],
            a["subject"],
            a["benchmarks"],
            attention_threshold_pct=float(a.get("attention_threshold_pct", 15)),
            material_threshold_pct=float(a.get("material_threshold_pct", 30)),
        ),
        _BENCHMARK_OUTPUT,
        write,
    )
    server.register_tool("analysis_financial_trends", "按严格财务期间计算同比、环比、CAGR 和共同比；缺基期、零基期或未知期间均返回结构化问题。", {
        "type": "object", "additionalProperties": False,
        "properties": {
            "workspace_id": _WS,
            "observations": {"type": "array", "minItems": 1, "maxItems": 5000, "items": observation},
            "methods": {"type": "array", "uniqueItems": True, "items": {"type": "string", "enum": ["yoy", "qoq", "cagr", "common_size"]}, "default": ["yoy", "qoq", "cagr", "common_size"]},
            "common_size_bases": {"type": "object", "additionalProperties": {"type": "string"}, "default": {}},
        },
        "required": ["workspace_id", "observations"],
    }, lambda a: service.financial_trends(
        a["workspace_id"], a["observations"], a.get("methods"), a.get("common_size_bases"),
    ), _TREND_OUTPUT, write)
    server.register_tool("analysis_list_unit_rules", "读取受控精确单位字典；规则只在调用方显式启用时应用。", {
        "type": "object", "additionalProperties": False, "properties": {},
    }, lambda _a: {
        "success": True, "status": "ok", "rules": service.controlled_unit_rules(),
        "resource_uris": [], "warnings": [], "blockers": [], "next_actions": [],
    }, _OUTPUT, read)
    server.register_tool("analysis_build_evidence_pack", "把选定来源、服务端候选事实、显式冲突与 missing_fields 缺口固化为不可变 evidence_pack_id；调用方自报候选仅具 estimate_preview 资格。", {
        "type": "object", "additionalProperties": False,
        "properties": {"workspace_id": _WS, "analysis_task_id": {"type": "string", "minLength": 1}, "selected_source_ids": _IDS, "candidate_set_id": {"type": "string", "minLength": 1}, "selected_candidate_ids": _IDS, "fact_candidates": {"type": "array", "items": {"type": "object"}, "default": []}, "conflicts": {"type": "array", "items": {"type": "object"}, "default": []}, "expected_fields": {"type": "array", "items": {"type": "string", "minLength": 1}, "default": []}, "evidence_track": _EVIDENCE_TRACK, "fixture_manifest": _FIXTURE_MANIFEST, "reconstruction_records": {"type": "array", "items": _RECONSTRUCTION_RECORD}},
        "required": ["workspace_id", "analysis_task_id"],
    }, lambda a: service.build_evidence_pack(
        a["workspace_id"], a["analysis_task_id"], a.get("selected_source_ids"),
        a.get("fact_candidates", []), a.get("conflicts", []),
        a.get("expected_fields", []), a.get("candidate_set_id", ""),
        a.get("selected_candidate_ids"), a.get("evidence_track", "real"),
        a.get("fixture_manifest"),
        a.get("reconstruction_records"),
    ), _PACK_OUTPUT, write)
    # Protocol-level Resource requests carry neither workspace nor identity.
    # Dynamic access is centralized in lvke-feasibility-delivery.
    server.register_resource_provider(lambda: [], lambda _uri: None)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
