"""JSON Schema primitives and candidate fragments for planning tools.

Every fragment is shared by reference across tool schemas; ``_schema`` is
the common envelope builder that injects ``workspace_id``.
"""

from __future__ import annotations

_PROJECT_PLANNING_CANDIDATE_SCHEMA_URI = (
    "lvke://schemas/project-planning-candidate"
)

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
        "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption", "sim_a_formal"],
        "default": "real",
    },
    "promotion_id": {
        "type": "string",
        "pattern": r"^zmprom_[0-9a-f]{24}$",
        "description": "SIM-A 正式上下文唯一允许的资格入口；其余正式字段由服务端推导。",
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
            ],
            "description": (
                "证据定位符，需能在 EvidencePack 的 sources[].locators 或 "
                "fact_candidates[].locator 中解析到。比较前两侧都会重归一化："
                "结构化对象/数组按 canonical JSON（sort_keys=True, "
                "separators=(\",\",\":\"), ensure_ascii=False）序列化；JSON 字符串"
                "先 loads 再同样序列化。因此 json.dumps 的默认写法（带空格）、"
                "indent 缩进、键序不同、ensure_ascii=True 都能匹配，无需自行紧凑化。"
                "纯字符串 locator 按原文精确匹配（仅去首尾空白），数值类型需一致"
                "（1 与 1.0 不等价）。"
            ),
        },
        "evidence_track": {
            "type": "string",
            "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption", "sim_a_formal"],
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
        "conversion_to_wan": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 1,
            "default": 0.0001,
            "description": (
                "把 数量×单耗×单价 的乘积换算为万元的乘数，不是「1 万元 = 10000 元」的进率。"
                "单价按元计价时取 0.0001（等于 ÷10000）；单价已按万元计价时取 1。"
                "填 10000 会把金额放大 1 亿倍。省略时默认 0.0001。"
            ),
        },
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
_PROJECT_PLANNING_CANDIDATE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Project Planning Candidate",
    "description": (
        "规划 prepare/solve 入口使用的市场、收入、规模、成本、劳动、政策或方案候选。"
    ),
    "x-lvke-schema-uri": _PROJECT_PLANNING_CANDIDATE_SCHEMA_URI,
    "oneOf": [
        _MARKET_CANDIDATE,
        _REVENUE_CANDIDATE,
        _BUILD_ALTERNATIVE,
        _COST_CANDIDATE_ITEM,
        _LABOR_REQUIREMENT,
        _POLICY_CANDIDATE,
        _OPTION,
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
