"""Official stdio MCP server for governed asset-acquisition finance."""

from __future__ import annotations

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.runtime.schemas import make_tool_output_schema
from lvke_mcp.servers.lvke_asset_acquisition import service

SERVER_NAME = "lvke-asset-acquisition"
SERVER_VERSION = "0.1.0"
_ASSET_ACQUISITION_SPEC_SCHEMA_URI = "lvke://schemas/asset-acquisition-spec"
logger = get_logger(SERVER_NAME)

_STRING = {"type": "string", "minLength": 1}
_KEY = {**_STRING, "maxLength": 200}
_OUTPUT = make_tool_output_schema(
    additional_properties=True,
    required=("resource_uris", "warnings", "blockers", "next_actions"),
)
_MONEY = {"type": "number", "minimum": 0, "description": "金额单位：万元"}
_RATE = {"type": "number", "minimum": 0, "maximum": 1}
_EVIDENCE_IDS = {
    "type": "array", "items": {"type": "string", "minLength": 1},
    "uniqueItems": True,
}
# P1-018：process_acceptance 要求的两组记录数组，展开必填键让调用方可发现。
# 与 backend.RECONSTRUCTION_RECORD_FIELDS / PROCESS_ACCEPTANCE_BASIS_FIELDS 同步。
# 注意：这两个 schema 只声明字段名与类型，不设 required（非 process_acceptance
# 场景下这些数组可选或可以部分填）。required 约束在 backend 的判定逻辑里，
# 失败时 PROCESS_ACCEPTANCE_BASIS_INCOMPLETE 的 details.gaps 会列出缺项。
_RECONSTRUCTION_RECORD_SCHEMA = {
    "type": "object",
    "properties": {
        "reconstruction_id": {"type": "string"},
        "source_uri": {"type": "string"},
        "content_hash": {"type": "string", "description": "必须以 sha256: 开头"},
        "locator": {"type": "string"},
        "source_kind": {"type": "string"},
        "method": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}
_PROCESS_ACCEPTANCE_BASIS_SCHEMA = {
    "type": "object",
    "properties": {
        "field": {"type": "string"},
        "value": {"description": "任意类型的字段取值"},
        "source_ref": {"type": "string"},
        "locator": {"type": "string"},
        "content_hash": {"type": "string", "description": "必须以 sha256: 开头"},
        "method": {"type": "string"},
        "limitation": {"type": "string"},
    },
    "additionalProperties": True,
}
_PROJECT_PARTY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "entity_id": _STRING,
        "name": _STRING,
        "roles": {
            "type": "array", "minItems": 1, "uniqueItems": True,
            "items": {"type": "string", "enum": [
                "buyer", "seller", "asset_owner", "operator", "license_holder",
                "lessor", "lessee", "lender", "guarantor", "appraiser",
            ]},
        },
        "status": {"type": "string", "enum": [
            "pending", "confirmed", "rejected", "unknown", "not_applicable",
        ]},
        "valid_from": {"type": "string", "format": "date"},
        "valid_to": {"type": "string", "format": "date"},
        "evidence_ids": _EVIDENCE_IDS,
    },
    "required": ["entity_id", "name", "roles", "status", "evidence_ids"],
}
_SOURCE_LOCATOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "source_id": _STRING, "resource_uri": {"type": "string", "format": "uri"},
        "locator": _STRING, "content_hash": _STRING,
        "page": {"type": "integer", "minimum": 1}, "sheet": _STRING,
        "cell_range": _STRING,
        "evidence_track": {"type": "string", "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption"]},
        "reconstruction_id": _STRING,
    },
    "required": ["source_id", "locator", "content_hash"],
}
_HISTORICAL_STATEMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "entity_id": _STRING,
        "period_start": {"type": "string", "format": "date"},
        "period_end": {"type": "string", "format": "date"},
        "statement_type": {"type": "string", "enum": [
            "balance_sheet", "income_statement", "cash_flow",
        ]},
        "source_format": {"type": "string", "enum": ["xls", "xlsx", "pdf", "docx", "json", "other"]},
        "evidence_track": {"type": "string", "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption"]},
        "reconstruction_records": {"type": "array", "items": {"type": "object"}},
        "normalized_accounts": {"type": "object", "additionalProperties": {"type": "number"}},
        "reconciliation": {
            "type": "object", "additionalProperties": False,
            "properties": {"ok": {"type": "boolean"}, "difference_wan": {"type": "number"}, "note": {"type": "string"}},
            "required": ["ok"],
        },
        "anomalies": {
            "type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"code": _STRING, "message": _STRING, "severity": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]}},
                "required": ["code", "message"],
            },
        },
        "source_locators": {"type": "array", "items": _SOURCE_LOCATOR_SCHEMA, "minItems": 1},
    },
    "required": [
        "entity_id", "period_start", "period_end", "statement_type",
        "source_format", "normalized_accounts", "reconciliation", "source_locators",
    ],
}
_ASSET_SCOPE_ITEM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "scope_id": _STRING, "type": _STRING, "name": _STRING,
        "included": {"type": "boolean"},
        "status": {"type": "string", "enum": ["pending", "confirmed", "rejected", "unknown"]},
        "accounting_treatment": {"type": "string", "enum": [
            "depreciable", "amortizable", "non_depreciable", "expensed",
        ]},
        "value_wan": _MONEY, "depreciable_basis_wan": _MONEY,
        "area_sqm": {"type": "number", "minimum": 0},
        "depreciation_years": {"type": "integer", "minimum": 1},
        "residual_rate": _RATE,
        "depreciation_start_year": {"type": "integer", "minimum": 1},
        "non_depreciable_reason": {"type": "string"},
        "conflicts": {"type": "array", "items": {"type": "string"}},
        "resolution": {"type": "string"},
        "evidence_ids": _EVIDENCE_IDS,
    },
    "required": ["scope_id", "type", "included", "status", "evidence_ids"],
}
_TRANSACTION_COMMON = {
    "acquisition_type": {"type": "string", "enum": ["asset", "equity", "mixed"]},
    "purchase_price": _MONEY,
    "transaction_taxes": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0}},
    "tax_burden_party": {"type": "string", "enum": ["buyer", "seller", "shared", "pending"]},
    "asset_scope": {"type": "array", "items": _ASSET_SCOPE_ITEM_SCHEMA, "minItems": 1},
    "closing_date": {"type": "string", "format": "date"},
    "valuation_value": _MONEY,
    "valuation_date": {"type": "string", "format": "date"},
    "financing_ratio": _RATE,
    "interest_rate": _RATE,
    "tenor": {"type": "integer", "minimum": 1},
    "repayment": {"type": "string", "enum": ["equal_principal", "equal_payment", "bullet", "custom"]},
    "exit_value": _MONEY,
    "exit_year": {"type": "integer", "minimum": 1},
    "closing_conditions": {"type": "array", "items": {"type": "string"}},
    "veto_items": {"type": "array", "items": {"type": "string"}},
}
_HOTEL_TRANSACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        **_TRANSACTION_COMMON,
        "calculation_granularity": {"const": "monthly"},
        "operating_mode": {
            "type": "string",
            "enum": ["owner_lessor", "mixed_owner_operator"],
        },
    },
    "required": ["calculation_granularity"],
}
_SOLAR_TRANSACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        **_TRANSACTION_COMMON,
        "calculation_granularity": {"const": "annual"},
        "model_start_date": {"type": "string", "format": "date"},
    },
    "required": ["calculation_granularity"],
}
_COMMON_SPEC_PROPERTIES = {
    "version": {"type": "string", "const": "finance_spec.v3"},
    "industry": {"type": "string"},
    "invest_type": {"type": "string", "const": "asset_acquisition"},
    "selected_scenario_id": {"type": "string", "minLength": 1},
    "confirmation_status": {"type": "string", "enum": ["candidate", "confirmed"]},
    "project_parties": {"type": "array", "items": _PROJECT_PARTY_SCHEMA},
    "historical_statements": {"type": "array", "items": _HISTORICAL_STATEMENT_SCHEMA},
    "decision_thresholds": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "target_project_irr": {"type": "number", "exclusiveMinimum": 0, "maximum": 5},
            "minimum_dscr": {"type": "number", "exclusiveMinimum": 0},
            "maximum_purchase_price_wan": _MONEY,
        },
    },
    "evidence_links": {
        "type": "object", "additionalProperties": _EVIDENCE_IDS,
        "description": "字段路径到不可变证据 ID 的绑定",
    },
    "evidence_policy": {
        "type": "string",
        "enum": ["formal_evidence", "source_reconstructed", "technical_fixture", "controlled_assumption"],
        "description": "confirmation_scope=process_acceptance 时必须为 source_reconstructed",
    },
    "project_fact_certified": {
        "type": "boolean",
        "description": "confirmation_scope=process_acceptance 时必须显式为 false",
    },
    "reconstruction_records": {
        "type": "array", "items": _RECONSTRUCTION_RECORD_SCHEMA,
        "description": (
            "来源重建记录。confirmation_scope=process_acceptance 时至少一条，"
            "每条七个键齐全且 content_hash 以 sha256: 开头"
        ),
    },
    "reconstructed_source_ids": {"type": "array", "items": _STRING},
    "unresolved_inputs": {"type": "array", "items": _STRING},
    "release_limitations": {"type": "array", "items": _STRING},
    "business_decision_status": {
        "type": "string", "enum": ["not_selected", "selected"],
        "description": "confirmation_scope=process_acceptance 时必须为 not_selected",
    },
    "process_acceptance_basis": {
        "type": "array", "items": _PROCESS_ACCEPTANCE_BASIS_SCHEMA,
        "description": (
            "流程验收逐字段依据。confirmation_scope=process_acceptance 时至少一条，"
            "每条七个键齐全且 content_hash 以 sha256: 开头"
        ),
    },
}
_HOTEL_OPERATION_SCHEMA = {
    "type": "object", "additionalProperties": True,
    "properties": {
        "rooms": {"type": "integer", "minimum": 1},
        "adr": {"oneOf": [{"type": "number", "minimum": 0}, {"type": "array", "items": {"type": "number", "minimum": 0}}]},
        "occupancy": {"oneOf": [_RATE, {"type": "array", "items": _RATE}]},
        "operating_days": {"type": "integer", "minimum": 1, "maximum": 366},
        "payroll": {"oneOf": [_MONEY, {"type": "array", "items": _MONEY}]},
        "utilities": {"oneOf": [_MONEY, {"type": "array", "items": _MONEY}]},
        "maintenance_capex": {"oneOf": [_MONEY, {"type": "array", "items": _MONEY}]},
        "evidence_ids": _EVIDENCE_IDS,
    },
}
_SOLAR_OPERATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "installed_capacity_mw": {"type": "number", "exclusiveMinimum": 0, "description": "装机容量，MW"},
        "annual_generation_mwh": {"type": "number", "exclusiveMinimum": 0, "description": "年上网电量，MWh"},
        "utilization_hours": {"type": "number", "exclusiveMinimum": 0, "description": "年利用小时，h"},
        "tariff_yuan_per_kwh": {"type": "number", "exclusiveMinimum": 0, "description": "含税上网电价，元/kWh"},
        "annual_opex_wan": {"oneOf": [_MONEY, {"type": "array", "items": _MONEY, "minItems": 1}]},
        "maintenance_capex_wan": {"oneOf": [_MONEY, {"type": "array", "items": _MONEY, "minItems": 1}]},
        "remaining_operating_years": {"type": "integer", "minimum": 1},
        "curtailment_rate": _RATE,
        "degradation_rate": _RATE,
        "evidence_ids": _EVIDENCE_IDS,
    },
}
_HOTEL_SPEC_SCHEMA = {
    "type": "object", "additionalProperties": True,
    "properties": {
        **_COMMON_SPEC_PROPERTIES,
        "asset_type": {"type": "string", "const": "hotel_lease", "default": "hotel_lease"},
        "transaction": _HOTEL_TRANSACTION_SCHEMA,
        "hotel_operation": _HOTEL_OPERATION_SCHEMA,
        "lease_portfolio": {"type": "object"},
    },
    "required": ["version", "transaction"],
}
_SOLAR_SPEC_SCHEMA = {
    "type": "object", "additionalProperties": True,
    "properties": {
        **_COMMON_SPEC_PROPERTIES,
        "asset_type": {"type": "string", "const": "solar_power"},
        "transaction": _SOLAR_TRANSACTION_SCHEMA,
        "solar_operation": _SOLAR_OPERATION_SCHEMA,
    },
    "required": ["version", "asset_type", "transaction"],
}
_SPEC_SCHEMA = {
    "oneOf": [_HOTEL_SPEC_SCHEMA, _SOLAR_SPEC_SCHEMA],
    "description": "按 asset_type 判别的 FinanceSpec v3 候选；业务层继续报告正式交付缺项",
    "x-lvke-schema-uri": _ASSET_ACQUISITION_SPEC_SCHEMA_URI,
    "examples": [{
        "version": "finance_spec.v3",
        "asset_type": "solar_power",
        "transaction": {"calculation_granularity": "annual"},
        "solar_operation": {"installed_capacity_mw": 10, "tariff_yuan_per_kwh": 0.42},
    }],
}


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "properties": properties, "required": required,
    }


def _resource(uri: str):
    resolved = service.resolve_resource(uri)
    return None if resolved is None else ReadResourceContents(resolved[0], resolved[1])


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    write = types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    base = {"workspace_id": _STRING}
    run_base = {**base, "run_id": _STRING}
    keyed = {"idempotency_key": _KEY}

    server.register_tool(
        "acquisition_validate_spec", "按 asset_type 校验 FinanceSpec v3 资产收购契约；酒店采用月度模型，光伏采用年度运营模型。",
        _schema({"spec": _SPEC_SCHEMA}, ["spec"]),
        lambda a: service.validate_spec(a["spec"]), _OUTPUT, read,
    )
    server.register_tool(
        "acquisition_save_spec", "保存不可变候选收购 Spec；客户端确认字段不会获得确认效力。",
        _schema({**base, "spec": _SPEC_SCHEMA, **keyed}, ["workspace_id", "spec", "idempotency_key"]),
        lambda a: service.save_spec(
            a["workspace_id"], a["spec"], a["idempotency_key"]
        ), _OUTPUT, write,
    )
    server.register_tool(
        "acquisition_confirm_spec", "以新不可变修订确认候选 Spec；process_acceptance 保留 source_reconstructed 与未决业务事实。",
        _schema({**base, "spec_id": _STRING, "note": {"type": "string", "default": ""}, "confirmation_scope": {"type": "string", "enum": ["project_candidate", "process_acceptance"], "default": "project_candidate"}, **keyed}, ["workspace_id", "spec_id", "idempotency_key"]),
        lambda a: service.confirm_spec(
            a["workspace_id"], a["spec_id"],
            a.get("note", ""), a["idempotency_key"], a.get("confirmation_scope", "project_candidate")
        ), _OUTPUT, write,
    )
    server.register_tool(
        "acquisition_run_model", "仅消费已确认 spec_id；hotel_lease 运行月度模型，solar_power 运行年度模型。",
        _schema({**base, "spec_id": _STRING, "discount_rate": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.08}, "scenario_id": {"type": "string", "minLength": 1, "default": "base", "description": "必须等于已确认 Spec 的 selected_scenario_id"}, **keyed}, ["workspace_id", "spec_id", "idempotency_key"]),
        lambda a: service.run_model(
            a["workspace_id"], a["spec_id"],
            float(a.get("discount_rate", 0.08)), a.get("scenario_id", "base"),
            a["idempotency_key"]
        ), _OUTPUT, write,
    )
    server.register_tool(
        "acquisition_get_run", "读取固化收购运行，支持 summary/result/governance/full 视图。",
        _schema({**run_base, "view": {"type": "string", "enum": ["summary", "result", "governance", "full"], "default": "summary"}}, ["workspace_id", "run_id"]),
        lambda a: service.get_run(
            a["workspace_id"], a["run_id"], a.get("view", "summary")
        ), _OUTPUT, read,
    )
    server.register_tool(
        "acquisition_create_scenario_matrix", "按既定独立维度计算最多 64 组笛卡尔积；收购价不联动经营参数。",
        _schema({**run_base, "dimensions": {
            "type": "object", "additionalProperties": False, "minProperties": 1,
            "properties": {
                field: {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": 64}
                for field in (
                    "transaction.purchase_price", "transaction.financing_ratio",
                    "transaction.interest_rate", "transaction.tenor",
                    "transaction.exit_value", "lease_portfolio.market_rent",
                    "hotel_operation.adr", "hotel_operation.occupancy",
                    "hotel_operation.maintenance_capex",
                    "solar_operation.tariff_yuan_per_kwh",
                    "solar_operation.annual_generation_mwh",
                    "solar_operation.utilization_hours", "solar_operation.annual_opex_wan",
                    "solar_operation.maintenance_capex_wan",
                    "solar_operation.curtailment_rate",
                )
            },
            "description": "合法独立情景维度；笛卡尔积最多64组",
        }, **keyed}, ["workspace_id", "run_id", "dimensions", "idempotency_key"]),
        lambda a: service.create_scenario_matrix(
            a["workspace_id"],
            a["run_id"],
            a["dimensions"],
            a["idempotency_key"],
        ), _OUTPUT, write,
    )
    server.register_tool(
        "acquisition_solve_max_price", "按目标 IRR/最低 DSCR 求解最高可接受收购价。",
        _schema({**run_base, "target_irr": {"type": ["number", "null"]}, "min_dscr": {"type": ["number", "null"]}, "lower": {"type": "number", "minimum": 0, "default": 0}, "upper": {"type": ["number", "null"]}, **keyed}, ["workspace_id", "run_id", "idempotency_key"]),
        service.solve_max_price, _OUTPUT, write,
    )
    server.register_tool(
        "acquisition_generate_artifact", "从一致性通过的 run 生成 Word、Excel、report-data 和附件索引。",
        _schema({**run_base, **keyed}, ["workspace_id", "run_id", "idempotency_key"]),
        lambda a: service.generate_artifact(
            a["workspace_id"],
            a["run_id"],
            a["idempotency_key"],
        ), _OUTPUT, write,
    )
    server.register_tool(
        "acquisition_get_artifact", "读取固化收购工件及其内容和数值一致性状态。",
        _schema({**base, "artifact_id": _STRING}, ["workspace_id", "artifact_id"]),
        lambda a: service.get_artifact(
            a["workspace_id"],
            a["artifact_id"],
        ), _OUTPUT, read,
    )
    server.register_tool(
        "acquisition_render_tables", "只消费固化 v3 run 生成不可变资产收购十三表 package，不重算模型。",
        _schema({**run_base, **keyed}, ["workspace_id", "run_id", "idempotency_key"]),
        lambda a: service.render_tables(
            a["workspace_id"],
            a["run_id"],
            a["idempotency_key"],
        ), _OUTPUT, write,
    )
    server.register_tool(
        "acquisition_export_tables_xlsx", "只消费收购十三表 package，导出结构完全一致的 XLSX。",
        _schema({**base, "acquisition_tables_package_id": _STRING, **keyed}, ["workspace_id", "acquisition_tables_package_id", "idempotency_key"]),
        lambda a: service.export_tables(
            a["workspace_id"],
            a["acquisition_tables_package_id"],
            a["idempotency_key"],
        ), _OUTPUT, write,
    )
    server.register_tool(
        "acquisition_export_tables_csv", "只消费通过列级完整性与勾稽门禁的收购十三表 package，导出 UTF-8 BOM 标量 CSV。",
        _schema({**base, "acquisition_tables_package_id": _STRING, **keyed}, ["workspace_id", "acquisition_tables_package_id", "idempotency_key"]),
        lambda a: service.export_tables_csv(
            a["workspace_id"],
            a["acquisition_tables_package_id"],
            a["idempotency_key"],
        ), _OUTPUT, write,
    )
    server.register_schema_resource(
        _ASSET_ACQUISITION_SPEC_SCHEMA_URI,
        _SPEC_SCHEMA,
        name="asset-acquisition-spec",
        title="Asset Acquisition Spec",
        description="酒店租赁与光伏资产收购服务端使用的完整判别式 Spec Schema。",
    )
    server.register_resource_provider(lambda: [], _resource)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
