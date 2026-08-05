"""Official-SDK MCP server for immutable project planning objects."""

from __future__ import annotations

from mcp import types

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.domains.project_planning import application as service
from lvke_mcp.servers.lvke_project_planning import lifecycle

SERVER_NAME = "lvke-project-planning"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)

_STRING = {"type": "string", "minLength": 1}
_WS = {
    **_STRING,
    "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
}
_KEY = {**_STRING, "maxLength": 200}
_REGION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "country": {"type": "string", "default": "中国"},
        "province": _STRING,
        "city": {"type": "string"},
        "district": {"type": "string"},
        "address": {"type": "string"},
        "administrative_code": {"type": "string"},
    },
    "required": ["province"],
}
_CONTEXT_PROPERTIES = {
    "project_name": _STRING,
    "industry_code": _STRING,
    "project_type": {
        "type": "string",
        "enum": [
            "new_build",
            "expansion",
            "renovation",
            "acquisition",
            "operation_lease",
            "other",
        ],
    },
    "region": _REGION,
    "objective": _STRING,
    "report_type": {
        "type": "string",
        "enum": [
            "feasibility_study",
            "project_application",
            "investment_decision",
            "technical_validation",
            "other",
        ],
    },
    "transaction_structure": {
        "type": "string",
        "enum": [
            "none",
            "greenfield",
            "asset_transfer",
            "equity_transfer",
            "operation_lease",
            "ppp",
            "other",
        ],
        "default": "none",
    },
    "target_type": {
        "type": "string",
        "enum": ["project", "company", "asset", "concession", "other"],
    },
    "asset_type": {
        "type": "string",
        "enum": [
            "none",
            "solar_power",
            "hotel_lease",
            "amusement_park",
            "industrial",
            "infrastructure",
            "other",
        ],
    },
    "evidence_track": {
        "type": "string",
        "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption"],
        "default": "real",
    },
    "description": {"type": "string", "maxLength": 10000},
    "tags": {
        "type": "array",
        "maxItems": 50,
        "items": {"type": "string", "minLength": 1, "maxLength": 100},
        "default": [],
    },
}
_CONTEXT = {
    "type": "object",
    "additionalProperties": False,
    "properties": _CONTEXT_PROPERTIES,
    "minProperties": 1,
}
_EVIDENCE_BINDING = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_id": _STRING,
        "source_type": {
            "type": "string",
            "enum": [
                "web_snapshot",
                "controlled_file",
                "technical_fixture",
                "selected_fact",
                "source_reconstructed",
                "search_summary",
            ],
        },
        "content_hash": {
            "type": "string",
            "pattern": r"^(?:sha256:)?[0-9a-fA-F]{64}$",
        },
        "locator": {
            "oneOf": [
                _STRING,
                {"type": "object", "minProperties": 1},
                {"type": "array", "minItems": 1},
            ]
        },
        "evidence_track": {
            "type": "string",
            "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption"],
        },
        "reconstruction_id": _STRING,
        "source_uri": _STRING,
        "source_kind": {
            "type": "string",
            "enum": ["client_report", "finance_template", "historical_statement", "scenario_note"],
        },
        "method": {
            "type": "string",
            "enum": ["table_extract", "formula_replay", "explicit_mapping"],
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "source_id",
        "source_type",
        "content_hash",
        "locator",
        "evidence_track",
    ],
}
_MARKET_CANDIDATE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate_id": _STRING,
        "method": {
            "type": "string",
            "enum": ["top_down", "bottom_up", "analogy", "capacity_factor"],
        },
        "market_size": {"type": "number", "exclusiveMinimum": 0},
        "unit": _STRING,
        "period": _STRING,
        "region": _STRING,
        "target_share": {"type": "number", "minimum": 0, "maximum": 1},
        "target_volume": {"type": "number", "minimum": 0},
        "formula_inputs": {
            "type": "object",
            "additionalProperties": {"type": ["number", "string"]},
            "default": {},
        },
        "evidence_bindings": {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": _EVIDENCE_BINDING,
        },
        "notes": {"type": "string", "maxLength": 5000},
    },
    "required": [
        "method",
        "market_size",
        "unit",
        "period",
        "region",
        "target_share",
        "evidence_bindings",
    ],
}
_RATE_SERIES = {
    "type": "array",
    "maxItems": 100,
    "items": {"type": "number", "minimum": 0, "maximum": 1},
}
_REVENUE_SPEC = {
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "model": {"type": "string", "const": "product_sales"},
                "products": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": _STRING,
                            "unit": _STRING,
                            "price_per_unit": {"type": "number", "minimum": 0},
                            "price_unit": {"type": "string", "enum": ["yuan", "wan"]},
                            "capacity": {"type": "number", "minimum": 0},
                            "ramp": _RATE_SERIES,
                            "var_cost_rate": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["name", "unit", "price_per_unit", "price_unit", "capacity"],
                    },
                },
            },
            "required": ["model", "products"],
        },
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "model": {"type": "string", "const": "property_sales"},
                "saleable_area": {"type": "number", "exclusiveMinimum": 0},
                "price_per_sqm": {"type": "number", "minimum": 0},
                "absorption": _RATE_SERIES,
            },
            "required": ["model", "saleable_area", "price_per_sqm"],
        },
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "model": {"type": "string", "const": "tourism"},
                "annual_visitors": {"type": "number", "exclusiveMinimum": 0},
                "visitor_unit": _STRING,
                "spend_per_visitor": {"type": "number", "minimum": 0},
                "ticket_price_yuan": {"type": "number", "minimum": 0},
                "secondary_spend_yuan": {"type": "number", "minimum": 0},
                "fixed_annual_revenue_wan": {"type": "number", "minimum": 0},
                "other_revenue_wan": {"type": "number", "minimum": 0},
                "visitor_ramp": _RATE_SERIES,
                "tourism_revenue_components": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": _STRING,
                            "basis": {"type": "string", "enum": ["per_visitor", "fixed_annual"]},
                            "price_per_visitor_yuan": {"type": "number", "minimum": 0},
                            "participation_rate": {"type": "number", "minimum": 0, "maximum": 1},
                            "annual_revenue_wan": {"type": "number", "minimum": 0},
                            "ramp": _RATE_SERIES,
                        },
                        "required": ["name", "basis"],
                    },
                },
            },
            "required": ["model", "annual_visitors", "visitor_unit"],
        },
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "model": {"type": "string", "const": "gov_payment"},
                "annual_gov_payment_wan": {"type": "number", "minimum": 0},
                "payment_ramp": _RATE_SERIES,
                "vat_refund_rate": {"type": "number", "minimum": 0, "maximum": 1},
                "fiscal_subsidy_wan": {"type": "number", "minimum": 0},
            },
            "required": ["model", "annual_gov_payment_wan"],
        },
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "model": {"type": "string", "const": "flat"},
                "annual_revenue_wan": {"type": "number", "minimum": 0},
                "ramp": _RATE_SERIES,
            },
            "required": ["model", "annual_revenue_wan"],
        },
    ]
}
_TARGET_CAPACITY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "value": {"type": "number", "exclusiveMinimum": 0},
        "unit": _STRING,
    },
    "required": ["value", "unit"],
}
_BUILD_CONSTRAINTS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "plot_ratio_min": {"type": "number", "minimum": 0, "maximum": 20},
        "plot_ratio_max": {"type": "number", "exclusiveMinimum": 0, "maximum": 20},
        "building_coverage_max": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "green_ratio_min": {"type": "number", "minimum": 0, "maximum": 1},
        "green_area_m2": {"type": "number", "minimum": 0},
    },
    "required": [
        "plot_ratio_min",
        "plot_ratio_max",
        "building_coverage_max",
        "green_ratio_min",
        "green_area_m2",
    ],
}
_FACILITY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": _STRING,
        "purpose": _STRING,
        "floor_area_m2": {"type": "number", "minimum": 0},
        "footprint_m2": {"type": "number", "minimum": 0},
    },
    "required": ["name", "purpose", "floor_area_m2", "footprint_m2"],
}
_INVEST_BREAKDOWN = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        field: {"type": "number", "minimum": 0}
        for field in (
            "construction_wan",
            "civil_wan",
            "equipment_wan",
            "installation_wan",
            "other_wan",
            "reserve_wan",
            "interest_wan",
            "working_capital_wan",
        )
    },
    "required": [
        "construction_wan",
        "civil_wan",
        "equipment_wan",
        "installation_wan",
        "other_wan",
        "reserve_wan",
        "interest_wan",
        "working_capital_wan",
    ],
}
_OPERATING_COST_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": _STRING,
        "annual_amount_wan": {"type": "number", "minimum": 0},
        "basis": _STRING,
        "evidence_bindings": {
            "type": "array",
            "maxItems": 100,
            "items": _EVIDENCE_BINDING,
        },
    },
    "required": ["name", "annual_amount_wan", "basis", "evidence_bindings"],
}
_POSITION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "category": _STRING,
        "name": _STRING,
        "headcount": {"type": "integer", "minimum": 1},
        "avg_wage_yuan": {"type": "number", "minimum": 0},
        "welfare_rate": {"type": "number", "minimum": 0, "maximum": 1},
        "annual_growth_rate": {"type": "number", "minimum": -1, "maximum": 10},
        "evidence_bindings": {
            "type": "array",
            "maxItems": 100,
            "items": _EVIDENCE_BINDING,
        },
    },
    "required": [
        "category",
        "name",
        "headcount",
        "avg_wage_yuan",
        "welfare_rate",
        "evidence_bindings",
    ],
}
_REVENUE_CANDIDATE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate_id": _STRING,
        "revenue_spec": _REVENUE_SPEC,
        "op_years": {"type": "integer", "minimum": 1, "maximum": 100},
        "mode": {
            "type": "string",
            "enum": ["estimate_preview", "review_candidate"],
            "default": "estimate_preview",
        },
        "flat_evidence_binding": _EVIDENCE_BINDING,
        "notes": {"type": "string", "maxLength": 5000},
    },
    "required": ["candidate_id", "revenue_spec", "op_years"],
}
_BUILD_ALTERNATIVE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate_id": _STRING,
        "target_capacity": _TARGET_CAPACITY,
        "land_area_m2": {"type": "number", "exclusiveMinimum": 0},
        "capacity_intensity_per_m2": {"type": "number", "exclusiveMinimum": 0},
        "constraints": _BUILD_CONSTRAINTS,
        "facilities": {
            "type": "array",
            "minItems": 1,
            "maxItems": 200,
            "items": _FACILITY,
        },
        "notes": {"type": "string", "maxLength": 5000},
    },
    "required": [
        "candidate_id",
        "target_capacity",
        "land_area_m2",
        "capacity_intensity_per_m2",
        "constraints",
        "facilities",
    ],
}
_COST_CANDIDATE_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": _STRING,
        "category": {
            "type": "string",
            "enum": [
                "raw_material",
                "utility",
                "environmental",
                "maintenance",
                "insurance",
                "lease",
                "sales",
                "management",
                "overhaul",
                "other",
            ],
        },
        "annual_amount_wan": {"type": "number", "minimum": 0},
        "annual_quantity": {"type": "number", "minimum": 0},
        "quantity_unit": {"type": "string"},
        "unit_consumption": {"type": "number", "minimum": 0},
        "unit_price_yuan": {"type": "number", "minimum": 0},
        "conversion_to_wan": {"type": "number", "exclusiveMinimum": 0},
        "loss_rate": {"type": "number", "minimum": 0, "maximum": 10},
        "tax_basis": {"type": "string"},
        "period": {"type": "string"},
        "basis": _STRING,
        "pollutant": {"type": "string"},
        "treatment_process": {"type": "string"},
        "design_capacity": {"type": "number", "minimum": 0},
        "environmental_capex_wan": {"type": "number", "minimum": 0},
        "environmental_opex_wan": {"type": "number", "minimum": 0},
        "evidence_bindings": {
            "type": "array",
            "maxItems": 100,
            "items": _EVIDENCE_BINDING,
            "default": [],
        },
    },
    "required": ["name", "category", "basis", "evidence_bindings"],
}
_LABOR_REQUIREMENT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "category": _STRING,
        "name": _STRING,
        "annual_workload": {"type": "number", "exclusiveMinimum": 0},
        "workload_unit": _STRING,
        "capacity_per_person_shift": {"type": "number", "exclusiveMinimum": 0},
        "shift_count": {"type": "integer", "minimum": 1, "maximum": 10},
        "coverage_factor": {"type": "number", "exclusiveMinimum": 0, "maximum": 10},
        "automation_adjustment": {"type": "number", "exclusiveMinimum": 0, "maximum": 10},
        "avg_wage_yuan": {"type": "number", "minimum": 0},
        "welfare_rate": {"type": "number", "minimum": 0, "maximum": 1},
        "annual_growth_rate": {"type": "number", "minimum": -1, "maximum": 10},
        "evidence_bindings": {
            "type": "array",
            "maxItems": 100,
            "items": _EVIDENCE_BINDING,
            "default": [],
        },
    },
    "required": [
        "category",
        "name",
        "annual_workload",
        "workload_unit",
        "capacity_per_person_shift",
        "shift_count",
        "coverage_factor",
        "automation_adjustment",
        "avg_wage_yuan",
        "welfare_rate",
        "evidence_bindings",
    ],
}
_POLICY_CANDIDATE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate_id": _STRING,
        "title": _STRING,
        "document_number": {"type": "string"},
        "classification": {
            "type": "string",
            "enum": ["applicable", "pending_verification", "excluded", "expired"],
        },
        "reason": _STRING,
        "source_snapshot_id": _STRING,
        "content_hash": {"type": "string", "pattern": r"^(?:sha256:)?[0-9a-fA-F]{64}$"},
        "locator": _STRING,
        "effective_date": {"type": "string"},
        "expiry_date": {"type": "string"},
    },
    "required": [
        "candidate_id",
        "title",
        "classification",
        "reason",
        "source_snapshot_id",
        "content_hash",
        "locator",
    ],
}
_OPTION_CRITERION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "criterion_id": _STRING,
        "label": _STRING,
        "weight": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "direction": {
            "type": "string",
            "enum": ["higher_is_better", "lower_is_better"],
        },
        "unit": _STRING,
        "description": {"type": "string", "maxLength": 5000},
    },
    "required": ["criterion_id", "label", "weight", "direction", "unit"],
}
_OPTION_CONSTRAINT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "constraint_id": _STRING,
        "label": _STRING,
        "description": {"type": "string", "maxLength": 5000},
    },
    "required": ["constraint_id", "label"],
}
_OPTION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "option_id": _STRING,
        "name": _STRING,
        "values": {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": {"type": "number"},
        },
        "evidence_bindings": {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": {
                "type": "array",
                "minItems": 1,
                "maxItems": 100,
                "items": _EVIDENCE_BINDING,
            },
        },
        "constraint_results": {
            "type": "object",
            "additionalProperties": {"type": "boolean"},
            "default": {},
        },
        "notes": {"type": "string", "maxLength": 10000},
    },
    "required": [
        "option_id",
        "name",
        "values",
        "evidence_bindings",
        "constraint_results",
    ],
}
_OUTPUT = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "success": {"type": "boolean"},
        "status": {
            "type": "string",
            "enum": [
                "ok",
                "partial",
                "missing_inputs",
                "blocked",
                "failed",
                "upstream_failure",
            ],
        },
        "resource_uris": {
            "type": "array",
            "items": {"type": "string"},
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "success",
        "status",
        "resource_uris",
        "warnings",
        "blockers",
        "next_actions",
    ],
}


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_id": _WS,
            **properties,
        },
        "required": ["workspace_id", *required],
    }


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read = types.ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    write = types.ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    server.register_tool(
        "project_context_create",
        "创建不可变 ProjectContext 草稿；身份由 MCP 宿主绑定，重复请求由幂等键保护。",
        _schema(
            {
                "context": _CONTEXT,
                "idempotency_key": _KEY,
            },
            ["context", "idempotency_key"],
        ),
        lambda a: service.create_project_context(
            a["workspace_id"],
            a["context"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "project_context_validate",
        "校验 ProjectContext 并固化 InputApplicability；资料缺失返回精确字段，不补默认值。",
        _schema(
            {
                "project_context_id": _STRING,
                "idempotency_key": _KEY,
            },
            ["project_context_id", "idempotency_key"],
        ),
        lambda a: service.validate_project_context(
            a["workspace_id"],
            a["project_context_id"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "project_context_revise",
        "基于 expected_basis_hash 创建新 ProjectContext revision，并返回下游 stale 清单。",
        _schema(
            {
                "project_context_id": _STRING,
                "expected_basis_hash": {
                    "type": "string",
                    "pattern": r"^sha256:[0-9a-f]{64}$",
                },
                "patch": _CONTEXT,
                "idempotency_key": _KEY,
            },
            [
                "project_context_id",
                "expected_basis_hash",
                "patch",
                "idempotency_key",
            ],
        ),
        lambda a: service.revise_project_context(
            a["workspace_id"],
            a["project_context_id"],
            a["expected_basis_hash"],
            a["patch"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_prepare_market_case",
        "基于不可变 ProjectContext、EvidencePack 和显式口径创建多路径市场案例；不自动选择或平均。",
        _schema(
            {
                "project_context_id": _STRING,
                "evidence_pack_id": _STRING,
                "candidates": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": _MARKET_CANDIDATE,
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "evidence_pack_id", "candidates", "idempotency_key"],
        ),
        lambda a: service.prepare_market_case(
            a["workspace_id"],
            a["project_context_id"],
            a["evidence_pack_id"],
            a["candidates"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_compare_market_cases",
        "逐对比较市场路径的期间、地区、单位和目标量偏差；明确返回 aggregation=none。",
        _schema({"market_case_id": _STRING}, ["market_case_id"]),
        lambda a: service.compare_market_cases(
            a["workspace_id"], a["market_case_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_validate_market_case",
        "校验市场案例的多路径、口径、份额算术和 evidence locator，搜索摘要会被拒绝。",
        _schema({"market_case_id": _STRING}, ["market_case_id"]),
        lambda a: service.validate_market_case(
            a["workspace_id"], a["market_case_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_market_case",
        "由 Codex 显式选择一个市场路径、说明理由并列出全部舍弃候选，固化不可变 revision。",
        _schema(
            {
                "market_case_id": _STRING,
                "selected_candidate_id": _STRING,
                "selection_reason": {**_STRING, "maxLength": 10000},
                "rejected_candidate_ids": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": _STRING,
                },
                "supersedes_market_case_id": {"type": "string", "default": ""},
                "expected_basis_hash": {
                    "type": "string",
                    "pattern": r"^(?:|sha256:[0-9a-f]{64})$",
                    "default": "",
                },
                "idempotency_key": _KEY,
            },
            [
                "market_case_id",
                "selected_candidate_id",
                "selection_reason",
                "rejected_candidate_ids",
                "idempotency_key",
            ],
        ),
        lambda a: service.confirm_market_case(
            a["workspace_id"],
            a["market_case_id"],
            a["selected_candidate_id"],
            a["selection_reason"],
            a["rejected_candidate_ids"],
            idempotency_key=a["idempotency_key"],
            supersedes_market_case_id=a.get("supersedes_market_case_id", ""),
            expected_basis_hash=a.get("expected_basis_hash", ""),
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_get_market_case",
        "读取指定不可变 MarketSizingCase revision、选择与 lineage。",
        _schema({"market_case_id": _STRING}, ["market_case_id"]),
        lambda a: service.get_market_case(
            a["workspace_id"], a["market_case_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_prepare_revenue_drivers",
        "从已确认市场案例固化一个或多个收入驱动候选；候选不自动选择或平均。",
        _schema(
            {
                "project_context_id": _STRING,
                "market_case_id": _STRING,
                "candidates": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": _REVENUE_CANDIDATE,
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "market_case_id", "candidates", "idempotency_key"],
        ),
        lambda a: lifecycle.prepare_revenue_drivers(
            a["workspace_id"], a["project_context_id"], a["market_case_id"], a["candidates"], idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_compare_revenue_candidates",
        "比较候选逐年收入差异；不合并候选、不计算平均值。",
        _schema({"revenue_driver_set_id": _STRING}, ["revenue_driver_set_id"]),
        lambda a: lifecycle.compare_revenue_candidates(
            a["workspace_id"], a["revenue_driver_set_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_validate_revenue_drivers",
        "校验收入模型、逐年曲线和 flat 正式证据门禁。",
        _schema({"revenue_driver_set_id": _STRING}, ["revenue_driver_set_id"]),
        lambda a: lifecycle.validate_revenue_drivers(
            a["workspace_id"], a["revenue_driver_set_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_revenue_drivers",
        "显式选择收入候选及舍弃项，生成不可变 confirmed RevenueDriverSet。",
        _schema(
            {
                "revenue_driver_set_id": _STRING,
                "selected_candidate_id": _STRING,
                "rejected_candidate_ids": {
                    "type": "array", "uniqueItems": True, "items": _STRING
                },
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "idempotency_key": _KEY,
            },
            [
                "revenue_driver_set_id", "selected_candidate_id",
                "rejected_candidate_ids", "selection_reason", "idempotency_key"
            ],
        ),
        lambda a: lifecycle.confirm_revenue_drivers(
            a["workspace_id"], a["revenue_driver_set_id"], a["selected_candidate_id"],
            a["rejected_candidate_ids"], a["selection_reason"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_get_revenue_drivers",
        "读取指定不可变 RevenueDriverSet 候选或确认 revision。",
        _schema({"revenue_driver_set_id": _STRING}, ["revenue_driver_set_id"]),
        lambda a: service.get_planning_object(
            a["workspace_id"], "RevenueDriverSet", a["revenue_driver_set_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_get_industry_constraints",
        "读取 ProjectContext 对应的版本化行业规划技术参数；参数不自动取得正式证据资格。",
        _schema({"project_context_id": _STRING}, ["project_context_id"]),
        lambda a: lifecycle.get_industry_constraints(
            a["workspace_id"], a["project_context_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_solve_build_scale",
        "对多个规模方案确定性计算产能、用地、容积率、密度和绿地约束。",
        _schema(
            {
                "project_context_id": _STRING,
                "market_case_id": _STRING,
                "alternatives": {
                    "type": "array", "minItems": 1, "maxItems": 20, "items": _BUILD_ALTERNATIVE
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "market_case_id", "alternatives", "idempotency_key"],
        ),
        lambda a: lifecycle.solve_build_scale(
            a["workspace_id"], a["project_context_id"], a["market_case_id"], a["alternatives"], idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_validate_build_scale",
        "校验规模候选的产能、用地和规划约束，至少要求一个可行候选。",
        _schema({"build_scale_case_id": _STRING}, ["build_scale_case_id"]),
        lambda a: lifecycle.validate_build_scale(
            a["workspace_id"], a["build_scale_case_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_build_scale",
        "显式选择一个可行规模方案并固化不可变 BuildScaleCase。",
        _schema(
            {
                "build_scale_case_id": _STRING,
                "selected_candidate_id": _STRING,
                "rejected_candidate_ids": {"type": "array", "uniqueItems": True, "items": _STRING},
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "idempotency_key": _KEY,
            },
            [
                "build_scale_case_id", "selected_candidate_id", "rejected_candidate_ids",
                "selection_reason", "idempotency_key"
            ],
        ),
        lambda a: lifecycle.confirm_build_scale(
            a["workspace_id"], a["build_scale_case_id"], a["selected_candidate_id"],
            a["rejected_candidate_ids"], a["selection_reason"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_get_build_scale",
        "读取指定不可变 BuildScaleCase 候选或确认 revision。",
        _schema({"build_scale_case_id": _STRING}, ["build_scale_case_id"]),
        lambda a: service.get_planning_object(
            a["workspace_id"], "BuildScaleCase", a["build_scale_case_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_create_revenue_drivers",
        "创建不可变 RevenueDriverSet；复用财务收入展开器，不在 planning 层复制收入公式。",
        _schema(
            {
                "project_context_id": _STRING,
                "market_case_id": _STRING,
                "revenue_spec": _REVENUE_SPEC,
                "op_years": {"type": "integer", "minimum": 1, "maximum": 100},
                "mode": {
                    "type": "string",
                    "enum": ["estimate_preview", "review_candidate"],
                    "default": "estimate_preview",
                },
                "flat_evidence_binding": _EVIDENCE_BINDING,
                "idempotency_key": _KEY,
            },
            [
                "project_context_id",
                "market_case_id",
                "revenue_spec",
                "op_years",
                "idempotency_key",
            ],
        ),
        lambda a: service.create_revenue_driver_set(
            a["workspace_id"],
            a["project_context_id"],
            a["market_case_id"],
            a["revenue_spec"],
            a["op_years"],
            mode=a.get("mode", "estimate_preview"),
            flat_evidence_binding=a.get("flat_evidence_binding"),
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_create_build_scale",
        "以目标产能、用地、单位面积产能和规划约束确定性创建 BuildScaleCase。",
        _schema(
            {
                "project_context_id": _STRING,
                "market_case_id": _STRING,
                "target_capacity": _TARGET_CAPACITY,
                "land_area_m2": {"type": "number", "exclusiveMinimum": 0},
                "capacity_intensity_per_m2": {"type": "number", "exclusiveMinimum": 0},
                "constraints": _BUILD_CONSTRAINTS,
                "facilities": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 200,
                    "items": _FACILITY,
                },
                "idempotency_key": _KEY,
            },
            [
                "project_context_id",
                "market_case_id",
                "target_capacity",
                "land_area_m2",
                "capacity_intensity_per_m2",
                "constraints",
                "facilities",
                "idempotency_key",
            ],
        ),
        lambda a: service.create_build_scale_case(
            a["workspace_id"],
            a["project_context_id"],
            a["market_case_id"],
            a["target_capacity"],
            a["land_area_m2"],
            a["capacity_intensity_per_m2"],
            a["constraints"],
            a["facilities"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_create_cost_drivers",
        "创建投资三段式和现金经营成本驱动；建设投资不闭合或成本明细不足时阻断。",
        _schema(
            {
                "project_context_id": _STRING,
                "build_scale_case_id": _STRING,
                "invest_breakdown": _INVEST_BREAKDOWN,
                "operating_cost_items": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 200,
                    "items": _OPERATING_COST_ITEM,
                },
                "idempotency_key": _KEY,
            },
            [
                "project_context_id",
                "build_scale_case_id",
                "invest_breakdown",
                "operating_cost_items",
                "idempotency_key",
            ],
        ),
        lambda a: service.create_cost_driver_set(
            a["workspace_id"],
            a["project_context_id"],
            a["build_scale_case_id"],
            a["invest_breakdown"],
            a["operating_cost_items"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_create_labor_plan",
        "按岗位、人数、人均工资和福利率创建可复算 LaborPlan，不使用行业默认值补齐。",
        _schema(
            {
                "project_context_id": _STRING,
                "build_scale_case_id": _STRING,
                "positions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 200,
                    "items": _POSITION,
                },
                "idempotency_key": _KEY,
            },
            [
                "project_context_id",
                "build_scale_case_id",
                "positions",
                "idempotency_key",
            ],
        ),
        lambda a: service.create_labor_plan(
            a["workspace_id"],
            a["project_context_id"],
            a["build_scale_case_id"],
            a["positions"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_prepare_cost_drivers",
        "固化投资和经营成本候选；数量、单耗和单价可在后续确定性计算中展开。",
        _schema(
            {
                "project_context_id": _STRING,
                "build_scale_case_id": _STRING,
                "invest_breakdown": _INVEST_BREAKDOWN,
                "operating_cost_items": {
                    "type": "array", "minItems": 1, "maxItems": 200, "items": _COST_CANDIDATE_ITEM
                },
                "idempotency_key": _KEY,
            },
            [
                "project_context_id", "build_scale_case_id", "invest_breakdown",
                "operating_cost_items", "idempotency_key"
            ],
        ),
        lambda a: lifecycle.prepare_cost_drivers(
            a["workspace_id"], a["project_context_id"], a["build_scale_case_id"],
            a["invest_breakdown"], a["operating_cost_items"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_calculate_cost_drivers",
        "按数量×单耗×单价×换算系数×损耗率计算成本并固化新候选 revision。",
        _schema(
            {"cost_driver_set_id": _STRING, "idempotency_key": _KEY},
            ["cost_driver_set_id", "idempotency_key"],
        ),
        lambda a: lifecycle.calculate_cost_drivers(
            a["workspace_id"], a["cost_driver_set_id"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_validate_cost_drivers",
        "校验投资闭合、经营成本可复算性和明细完整性。",
        _schema({"cost_driver_set_id": _STRING}, ["cost_driver_set_id"]),
        lambda a: lifecycle.validate_cost_drivers(
            a["workspace_id"], a["cost_driver_set_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_cost_drivers",
        "确认已计算成本候选并生成 FinanceSpec 转换 ledger。",
        _schema(
            {
                "cost_driver_set_id": _STRING,
                "confirmation_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "idempotency_key": _KEY,
            },
            ["cost_driver_set_id", "confirmation_reason", "idempotency_key"],
        ),
        lambda a: lifecycle.confirm_cost_drivers(
            a["workspace_id"], a["cost_driver_set_id"], a["confirmation_reason"], idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_get_cost_drivers",
        "读取指定不可变 CostDriverSet 候选、计算或确认 revision。",
        _schema({"cost_driver_set_id": _STRING}, ["cost_driver_set_id"]),
        lambda a: service.get_planning_object(
            a["workspace_id"], "CostDriverSet", a["cost_driver_set_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_get_env_templates",
        "读取环保成本方案所需字段模板；模板不是项目合规结论或正式证据。",
        _schema(
            {
                "project_type": _STRING,
                "pollutant_types": {
                    "type": "array", "minItems": 1, "uniqueItems": True,
                    "items": {"type": "string", "enum": ["wastewater", "waste_gas", "solid_waste", "noise"]}
                },
            },
            ["project_type", "pollutant_types"],
        ),
        lambda a: lifecycle.get_environmental_scheme_templates(
            a["project_type"], a["pollutant_types"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_infer_labor_plan",
        "按工作量、单人班次能力、班次、覆盖和自动化因子确定性推导岗位人数。",
        _schema(
            {
                "project_context_id": _STRING,
                "build_scale_case_id": _STRING,
                "position_requirements": {
                    "type": "array", "minItems": 1, "maxItems": 200, "items": _LABOR_REQUIREMENT
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "build_scale_case_id", "position_requirements", "idempotency_key"],
        ),
        lambda a: lifecycle.infer_labor_plan(
            a["workspace_id"], a["project_context_id"], a["build_scale_case_id"],
            a["position_requirements"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_validate_labor_plan",
        "校验岗位、人数、班次、工资、福利和计算轨迹。",
        _schema({"labor_plan_id": _STRING}, ["labor_plan_id"]),
        lambda a: lifecycle.validate_labor_plan(
            a["workspace_id"], a["labor_plan_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_labor_plan",
        "确认劳动定员候选并生成工资福利 FinanceSpec ledger。",
        _schema(
            {
                "labor_plan_id": _STRING,
                "confirmation_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "idempotency_key": _KEY,
            },
            ["labor_plan_id", "confirmation_reason", "idempotency_key"],
        ),
        lambda a: lifecycle.confirm_labor_plan(
            a["workspace_id"], a["labor_plan_id"], a["confirmation_reason"], idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_get_labor_plan",
        "读取指定不可变 LaborPlan 候选或确认 revision。",
        _schema({"labor_plan_id": _STRING}, ["labor_plan_id"]),
        lambda a: service.get_planning_object(
            a["workspace_id"], "LaborPlan", a["labor_plan_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_prepare_policy_basis",
        "将已固化政策来源分类为适用、待核实、排除或过期候选，不自动选择。",
        _schema(
            {
                "project_context_id": _STRING,
                "candidates": {
                    "type": "array", "minItems": 1, "maxItems": 200, "items": _POLICY_CANDIDATE
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "candidates", "idempotency_key"],
        ),
        lambda a: lifecycle.prepare_policy_basis(
            a["workspace_id"], a["project_context_id"], a["candidates"], idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_confirm_policy_basis",
        "由 Codex 显式选择政策候选并固化 PolicyBasis；过期或排除政策不可选择。",
        _schema(
            {
                "policy_basis_id": _STRING,
                "selected_candidate_ids": {
                    "type": "array", "minItems": 1, "uniqueItems": True, "items": _STRING
                },
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "idempotency_key": _KEY,
            },
            ["policy_basis_id", "selected_candidate_ids", "selection_reason", "idempotency_key"],
        ),
        lambda a: lifecycle.confirm_policy_basis(
            a["workspace_id"], a["policy_basis_id"], a["selected_candidate_ids"],
            a["selection_reason"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_prepare_option_comparison",
        "固化设备、建筑、工艺、场址或运营模式候选，按显式权重和方向确定性计分，不自动选择。",
        _schema(
            {
                "project_context_id": _STRING,
                "category": {
                    "type": "string",
                    "enum": ["equipment", "building", "process", "site", "operating_model"],
                },
                "criteria": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": _OPTION_CRITERION,
                },
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 20,
                    "items": _OPTION,
                },
                "mandatory_constraints": {
                    "type": "array",
                    "maxItems": 50,
                    "items": _OPTION_CONSTRAINT,
                    "default": [],
                },
                "basis_object_ids": {
                    "type": "array",
                    "maxItems": 20,
                    "uniqueItems": True,
                    "items": _STRING,
                    "default": [],
                },
                "idempotency_key": _KEY,
            },
            ["project_context_id", "category", "criteria", "options", "idempotency_key"],
        ),
        lambda a: service.prepare_option_comparison(
            a["workspace_id"],
            a["project_context_id"],
            a["category"],
            a["criteria"],
            a["options"],
            a.get("mandatory_constraints", []),
            a.get("basis_object_ids", []),
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_confirm_option_selection",
        "由 Codex/人员显式选择可行方案、提交理由并列出全部未选方案，生成新的不可变确认对象。",
        _schema(
            {
                "option_comparison_id": _STRING,
                "selected_option_id": _STRING,
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "rejected_option_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 19,
                    "uniqueItems": True,
                    "items": _STRING,
                },
                "idempotency_key": _KEY,
            },
            [
                "option_comparison_id",
                "selected_option_id",
                "selection_reason",
                "rejected_option_ids",
                "idempotency_key",
            ],
        ),
        lambda a: service.confirm_option_selection(
            a["workspace_id"],
            a["option_comparison_id"],
            a["selected_option_id"],
            a["selection_reason"],
            a["rejected_option_ids"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_validate_option_comparison",
        "校验方案、指标、强制约束和至少一个可行方案。",
        _schema({"option_comparison_id": _STRING}, ["option_comparison_id"]),
        lambda a: lifecycle.validate_option_comparison(
            a["workspace_id"], a["option_comparison_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_score_option_comparison",
        "读取已固化的确定性评分、排名和评分方法，不重新选择方案。",
        _schema({"option_comparison_id": _STRING}, ["option_comparison_id"]),
        lambda a: lifecycle.score_option_comparison(
            a["workspace_id"], a["option_comparison_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_confirm_option_comparison",
        "显式选择可行方案并固化确认 revision；与旧 selection 名称共享同一确定性实现。",
        _schema(
            {
                "option_comparison_id": _STRING,
                "selected_option_id": _STRING,
                "selection_reason": {**_STRING, "minLength": 10, "maxLength": 10000},
                "rejected_option_ids": {
                    "type": "array", "minItems": 1, "maxItems": 19,
                    "uniqueItems": True, "items": _STRING
                },
                "idempotency_key": _KEY,
            },
            [
                "option_comparison_id", "selected_option_id", "selection_reason",
                "rejected_option_ids", "idempotency_key"
            ],
        ),
        lambda a: service.confirm_option_selection(
            a["workspace_id"], a["option_comparison_id"], a["selected_option_id"],
            a["selection_reason"], a["rejected_option_ids"],
            idempotency_key=a["idempotency_key"]
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "planning_get_option_comparison",
        "读取指定不可变 OptionComparison 候选或确认 revision。",
        _schema({"option_comparison_id": _STRING}, ["option_comparison_id"]),
        lambda a: service.get_planning_object(
            a["workspace_id"], "OptionComparison", a["option_comparison_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_get_policy_basis",
        "读取指定不可变 PolicyBasis 候选或确认 revision。",
        _schema({"policy_basis_id": _STRING}, ["policy_basis_id"]),
        lambda a: service.get_planning_object(
            a["workspace_id"], "PolicyBasis", a["policy_basis_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_resolve_industry_skill",
        "根据不可变 ProjectContext 的 industry_code、project_type、transaction_structure 和 asset_type 返回唯一主行业 Skill。",
        _schema(
            {"project_context_id": _STRING},
            ["project_context_id"],
        ),
        lambda a: service.resolve_industry_skill(
            a["workspace_id"],
            a["project_context_id"],
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "planning_get_object",
        "读取指定不可变 RevenueDriverSet、BuildScaleCase、CostDriverSet、LaborPlan 或 OptionComparison。",
        _schema(
            {
                "object_type": {
                    "type": "string",
                    "enum": [
                        "RevenueDriverSet",
                        "BuildScaleCase",
                        "CostDriverSet",
                        "LaborPlan",
                        "OptionComparison",
                    ],
                },
                "object_id": _STRING,
            },
            ["object_type", "object_id"],
        ),
        lambda a: service.get_planning_object(
            a["workspace_id"],
            a["object_type"],
            a["object_id"],
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "project_context_get",
        "按不可变 ID 读取 ProjectContext。",
        _schema(
            {"project_context_id": _STRING},
            ["project_context_id"],
        ),
        lambda a: service.get_project_context(
            a["workspace_id"],
            a["project_context_id"],
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "project_context_list",
        "分页列出当前工作区的 ProjectContext revisions。",
        _schema(
            {
                "cursor": {"type": "string", "default": ""},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            [],
        ),
        lambda a: service.list_project_contexts(
            a["workspace_id"],
            cursor=a.get("cursor", ""),
            limit=int(a.get("limit", 50)),
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "project_context_list_resources",
        "分页列出 ProjectContext 与 InputApplicability Resource。",
        _schema(
            {
                "resource_type": {
                    "type": "string",
                    "enum": [
                        "",
                        "ProjectContext",
                        "InputApplicability",
                        "MarketSizingCase",
                        "RevenueDriverSet",
                        "BuildScaleCase",
                        "CostDriverSet",
                        "LaborPlan",
                        "OptionComparison",
                    ],
                    "default": "",
                },
                "cursor": {"type": "string", "default": ""},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            [],
        ),
        lambda a: service.list_resources(
            a["workspace_id"],
            resource_type=a.get("resource_type", ""),
            cursor=a.get("cursor", ""),
            limit=int(a.get("limit", 50)),
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "project_context_read_resource",
        "在显式 workspace 作用域内读取不可变 planning Resource。",
        _schema({"uri": _STRING}, ["uri"]),
        lambda a: service.read_resource(
            a["workspace_id"],
            a["uri"],
        ),
        _OUTPUT,
        read,
    )
    # Protocol-level resources carry no explicit workspace assertion. Scoped
    # tools above remain authoritative until protocol auth context is available.
    server.register_resource_provider(lambda: [], lambda _uri: None)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
