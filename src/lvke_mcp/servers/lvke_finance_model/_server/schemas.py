"""服务标识、schema URI、BoE/分布 JSON Schema 与弃用提示常量。"""

from __future__ import annotations



from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.responses import ok


SERVER_NAME = "lvke-finance-model"


SERVER_VERSION = "0.3.0"


_FINANCE_SPEC_SCHEMA_URI = "lvke://schemas/finance-spec-v3"


logger = get_logger(SERVER_NAME)


_BOE_ENTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target_pointer": {"type": "string", "pattern": r"^/(?:spec|input_revision)/"},
        "value": {},
        "unit": {"type": "string", "minLength": 1},
        "period": {"type": "string", "minLength": 1},
        "source_type": {
            "type": "string",
            "enum": [
                "evidence_pack",
                "market_sizing_case",
                "build_scale_case",
                "revenue_driver_set",
                "cost_driver_set",
                "labor_plan",
                "source_reconstructed",
                "technical_fixture",
                "controlled_assumption",
            ],
        },
        "source_object_id": {"type": "string", "minLength": 1},
        "method": {"type": "string", "minLength": 1},
        "selection_reason": {"type": "string", "minLength": 10},
        "uncertainty": {"type": "string"},
        "candidate_values": {"type": "array", "items": {}},
        "rejected_values": {"type": "array", "items": {}},
        "locator": {"type": "string", "minLength": 1},
        "content_hash": {
            "type": "string",
            "pattern": r"^(?:sha256:)?[0-9a-fA-F]{64}$",
        },
        "evidence_eligibility": {
            "type": "string",
            "enum": ["formal_evidence", "source_reconstructed", "technical_fixture", "controlled_assumption"],
        },
        "reconstruction": {
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
        },
        "reconstruction_record": {
            "type": "object",
            "additionalProperties": True,
            "description": "reconstruction 的兼容别名",
        },
    },
    "required": [
        "target_pointer",
        "value",
        "unit",
        "period",
        "source_type",
        "source_object_id",
        "method",
        "selection_reason",
        "locator",
        "content_hash",
        "evidence_eligibility",
    ],
}


_DISTRIBUTION_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "field": {
                    "type": "string",
                    "enum": ["revenue_scale", "operating_cost_scale", "construction_scale"],
                },
                "distribution": {"const": "uniform"},
                "low": {"type": "number", "exclusiveMinimum": 0},
                "high": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["field", "distribution", "low", "high"],
        },
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "field": {
                    "type": "string",
                    "enum": ["revenue_scale", "operating_cost_scale", "construction_scale"],
                },
                "distribution": {"const": "triangular"},
                "low": {"type": "number", "exclusiveMinimum": 0},
                "mode": {"type": "number", "exclusiveMinimum": 0},
                "high": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["field", "distribution", "low", "mode", "high"],
        },
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "field": {
                    "type": "string",
                    "enum": ["revenue_scale", "operating_cost_scale", "construction_scale"],
                },
                "distribution": {"const": "normal"},
                "mean": {"type": "number", "exclusiveMinimum": 0},
                "stddev": {"type": "number", "exclusiveMinimum": 0},
                "low": {"type": "number", "exclusiveMinimum": 0},
                "high": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["field", "distribution", "mean", "stddev", "low", "high"],
        },
    ]
}


_STATUS_VALUES = ["ok", "partial", "missing_inputs", "blocked", "failed"]


# 兼容期迁移提示（方案 8.4：旧工具在一个兼容周期内返回 deprecation 信息）
_DEPRECATED_RENDER_HINT = (
    "deprecated：finance_render_tables 将移除，请迁移到 "
    "lvke-finance-tables.tables_render（同一 run_id 渲染，不重算）"
)


_DEPRECATED_PACKAGE_HINT = (
    "deprecated：finance_generate_package 将移除，请由 lvke-finance-authoring "
    "Skill 显式编排 finance_run_model → lvke-finance-tables.tables_render"
)


def _output_schema(
    tool_properties: dict | None = None,
    *,
    success_required: list[str] | tuple[str, ...] = (),
    deprecated: bool = False,
) -> dict:
    """构造单工具专属输出契约。

    - envelope 字段全响应必含（成功与失败路径一致）；
    - 使用平坦对象 schema，避免 MCP 客户端把条件交叉类型显示为
      ``unknown & unknown``；
    - 成功/失败的领域字段由处理器和契约测试校验，不在公开 schema 中使用
      ``allOf/if/then``；
    - DEPRECATED 工具全响应必含 ``deprecated: true``。
    """
    props: dict = {
        "success": {"type": "boolean"},
        "status": {"type": "string", "enum": list(_STATUS_VALUES)},
        "resource_uris": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
        "data": {},
        "source": {"type": "string"},
        "code": {"type": "string"},
        "message": {"type": "string"},
    }
    required = ["success", "status", "resource_uris", "warnings", "blockers", "next_actions"]
    if deprecated:
        props["deprecated"] = {"const": True}
        required.append("deprecated")
    if tool_properties:
        props.update(tool_properties)
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": props,
        "required": required,
    }
