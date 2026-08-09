"""Resolve finance parameters and retain field-level source lineage."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from lvke_mcp.domains.finance.spec import FINANCE_SPEC_SCHEMA as _BASE_FINANCE_SPEC_SCHEMA
from lvke_mcp.domains.finance.spec import (
    FINANCE_SPEC_V3_SCHEMA as _FINANCE_SPEC_V3_SCHEMA,
)


SOURCE_PRIORITY = (
    "user_input",
    "project_document",
    "policy_profile",
    "industry_profile",
    "llm_suggestion",
    "system_default",
)

# Only fields consumed by ``finance_model.compute_financials`` or by the
# governed resolver below may enter the effective input hash.  Keeping this
# list here makes aliases and unknown-field handling identical for every MCP
# entry point instead of letting each layer guess independently.
CANONICAL_FINANCE_FIELDS = frozenset({
    "total_investment_wan", "annual_revenue_wan", "revenue_wan",
    "invest_breakdown", "is_operating", "capital_own_ratio", "capital_own_wan",
    "loan_ratio", "loan_wan", "loan_rate", "loan_years", "loan_grace_years",
    "loan_balloon_pct", "loan_repay_method", "loan_draw_by_year", "loan_draw_plan",
    "loan_interest_by_year", "loan_principal_by_year", "debt_interest_by_year",
    "debt_principal_by_year", "debt_repay_sources", "equity_inject_by_year",
    "funding_annual_schedule", "gov_subsidy_wan", "annual_operating_subsidy_wan",
    "calc_period_years", "cost_items", "cost_behavior", "cost_policy",
    "operating_cost_by_year", "opex_by_year", "income_tax_rate", "vat_rate",
    "vat_input_rate", "surtax_on_vat", "surtax_vat_rate", "tax_component_policy",
    "urban_maintenance_rate", "education_surcharge_rate",
    "local_education_surcharge_rate", "consumption_tax_payable_wan",
    "revenue_tax_inclusive", "depreciation_years", "depreciation_classes",
    "amortization_years", "amort_bases", "intangible_assets_wan",
    "other_assets_wan", "salvage_rate", "labor_plan", "staff_detail",
    "wage_detail", "wage_wan", "wc_turnover", "wc_turnover_days",
    "working_capital_by_year", "construction_interest_by_year",
    "construction_investment_by_year", "construction_outlay_by_year",
    "build_outlay_by_year", "terminal_fixed_asset_recover_wan",
    "terminal_working_capital_recover_wan", "use_initial_working_capital_ratio",
    "distribution_policy", "industry", "invest_type", "build_period_months",
    "discount_rate", "discount_rate_scenarios", "fare_multiplier_by_year",
    "renewal_capex_plan", "fiscal_support_policy", "project_metadata",
    # Governance fields are consumed by the formal table/rendering gates.  The
    # fact-pack adapter intentionally materializes both the nested policy and
    # these flat confirmations, so they are effective inputs rather than
    # unknown metadata.
    "cost_behavior_confirmed", "tax_component_policy_confirmed",
    "statutory_reserve_rate", "arbitrary_reserve_confirmed_zero",
    "investor_distribution_confirmed_zero",
})

_NON_COMPUTE_METADATA_FIELDS = frozenset({
    "finance_spec", "finance_fact_pack", "fact_pack", "auto_inject_env_opex",
    "_auto_injected_cost_items", "_cost_suggestions",
    "input_sources", "_missing_inputs", "construction_detail",
})

_COMPATIBILITY_ALIASES = {
    "construction_invest_by_year": "construction_investment_by_year",
    "idc_by_year": "construction_interest_by_year",
}


def finance_input_schema() -> dict[str, Any]:
    """Return the single public schema for deterministic finance inputs."""

    nonnegative = {"type": "number", "minimum": 0, "description": "金额单位：万元"}
    rate = {"type": "number", "minimum": 0, "maximum": 1, "description": "小数税率/比例，0.05 表示 5%"}
    positive_int = {"type": "integer", "minimum": 1}
    number_series = {
        "type": "array",
        "items": {"type": "number", "minimum": 0},
        "minItems": 1,
        "description": "按年排列的金额序列，单位：万元",
    }
    properties: dict[str, Any] = {
        field: {"description": "确定性财务引擎支持的受控输入"}
        for field in CANONICAL_FINANCE_FIELDS
    }
    for field in (
        "total_investment_wan", "annual_revenue_wan", "revenue_wan",
        "capital_own_wan", "loan_wan", "gov_subsidy_wan",
        "annual_operating_subsidy_wan", "intangible_assets_wan",
        "other_assets_wan", "wage_wan", "terminal_fixed_asset_recover_wan",
        "terminal_working_capital_recover_wan",
    ):
        properties[field] = copy.deepcopy(nonnegative)
    for field in (
        "capital_own_ratio", "loan_ratio", "loan_rate", "loan_balloon_pct",
        "income_tax_rate", "vat_rate", "vat_input_rate", "surtax_vat_rate",
        "urban_maintenance_rate", "education_surcharge_rate",
        "local_education_surcharge_rate", "salvage_rate",
        "use_initial_working_capital_ratio", "statutory_reserve_rate",
        "discount_rate",
    ):
        properties[field] = copy.deepcopy(rate)
    for field in (
        "calc_period_years", "loan_years", "loan_grace_years",
        "depreciation_years", "amortization_years", "build_period_months",
    ):
        properties[field] = copy.deepcopy(positive_int)
    for field in (
        "operating_cost_by_year", "opex_by_year", "working_capital_by_year",
        "construction_interest_by_year", "construction_investment_by_year",
        "construction_outlay_by_year", "build_outlay_by_year",
        "loan_draw_by_year", "loan_interest_by_year", "loan_principal_by_year",
        "debt_interest_by_year", "debt_principal_by_year",
        "equity_inject_by_year",
        "fare_multiplier_by_year", "discount_rate_scenarios",
    ):
        properties[field] = copy.deepcopy(number_series)
    properties["construction_invest_by_year"] = {
        **copy.deepcopy(number_series),
        "description": "兼容别名：映射到 construction_investment_by_year。",
    }
    properties["idc_by_year"] = {
        **copy.deepcopy(number_series),
        "description": "兼容别名：映射到 construction_interest_by_year。",
    }
    properties["consumption_tax_payable_wan"] = {
        "oneOf": [copy.deepcopy(nonnegative), copy.deepcopy(number_series)],
        "description": "消费税应纳税额；可为达产年金额或逐年金额序列，单位：万元",
    }
    properties["cost_items"] = {
        "type": "object",
        "additionalProperties": {"type": "number", "minimum": 0},
        "description": "现金经营成本明细，键为科目名称、值为万元；与逐年经营成本互斥",
    }
    investment_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "category": {
                "type": "string",
                "enum": ["civil", "equipment", "installation", "other"],
            },
            "unit": {"type": "string"},
            "quantity": {"type": "number", "exclusiveMinimum": 0},
            "indicator": {"type": "number", "minimum": 0},
            "indicator_yuan": {"type": "number", "minimum": 0},
            "amount_wan": copy.deepcopy(nonnegative),
        },
        "required": ["name"],
        "description": "投资细项；金额单位万元，或使用 quantity×indicator_yuan/10000。",
    }
    amount_detail = {
        "type": "object",
        "additionalProperties": {"type": "number", "minimum": 0},
        "description": "标准科目键到金额的映射，金额单位万元。",
    }
    properties["invest_breakdown"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "construction_wan": {
                **copy.deepcopy(nonnegative),
                "description": "建设投资合计；兼容扁平形态下与 equipment_wan 同传时表示建筑工程费。",
            },
            "civil_wan": {**copy.deepcopy(nonnegative), "description": "建筑工程费。"},
            "equipment_wan": {**copy.deepcopy(nonnegative), "description": "设备及工器具购置费。"},
            "installation_wan": {**copy.deepcopy(nonnegative), "description": "安装工程费。"},
            "other_wan": {**copy.deepcopy(nonnegative), "description": "工程建设其他费合计。"},
            "reserve_wan": {**copy.deepcopy(nonnegative), "description": "预备费合计。"},
            "interest_wan": {**copy.deepcopy(nonnegative), "description": "建设期利息。"},
            "working_capital_wan": {**copy.deepcopy(nonnegative), "description": "流动资金。"},
            "basic_reserve_rate": copy.deepcopy(rate),
            "construction_detail": amount_detail,
            "other_detail": amount_detail,
            "contingency_detail": amount_detail,
            "construction_items": {
                "type": "array", "items": investment_item,
                "description": "工程费用明细；正式参考级应提供工程量和估算指标。",
            },
            "engineering_items": {
                "type": "array", "items": investment_item,
                "description": "construction_items 的兼容别名。",
            },
            "other_items": {
                "type": "array", "items": investment_item,
                "description": "工程建设其他费用明细。",
            },
            "contingency_items": {
                "type": "array", "items": investment_item,
                "description": "预备费明细。",
            },
        },
        "description": "投资三段式：工程费用+工程建设其他费+预备费；另列建设期利息和流动资金。",
    }
    turnover_component = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "days": {"type": "number", "minimum": 0, "maximum": 3650},
            "annual_base_wan": copy.deepcopy(nonnegative),
            "base_wan": {
                **copy.deepcopy(nonnegative),
                "description": "annual_base_wan 的兼容别名。",
            },
            "base_source": {
                "type": "string",
                "minLength": 1,
                "description": "年周转基数的科目或不可变来源定位。",
            },
        },
        "required": ["days"],
    }
    properties["wc_turnover"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "receivable_days": {"type": "number", "minimum": 0, "maximum": 3650},
            "inventory_days": {"type": "number", "minimum": 0, "maximum": 3650},
            "cash_days": {"type": "number", "minimum": 0, "maximum": 3650},
            "payable_days": {"type": "number", "minimum": 0, "maximum": 3650},
            "receivable": {
                "oneOf": [
                    {"type": "number", "minimum": 0, "maximum": 3650},
                    turnover_component,
                ],
                "description": "兼容键：应收账款周转天数，或 days+annual_base_wan 对象。",
            },
            "inventory": {
                "oneOf": [
                    {"type": "number", "minimum": 0, "maximum": 3650},
                    turnover_component,
                ],
                "description": "兼容键：存货周转天数，或 days+annual_base_wan 对象。",
            },
            "cash": {
                "oneOf": [
                    {"type": "number", "minimum": 0, "maximum": 3650},
                    turnover_component,
                ],
                "description": "兼容键：现金周转天数，或 days+annual_base_wan 对象。",
            },
            "payable": {
                "oneOf": [
                    {"type": "number", "minimum": 0, "maximum": 3650},
                    turnover_component,
                ],
                "description": "兼容键：应付账款周转天数，或 days+annual_base_wan 对象。",
            },
            "inventory_detail": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "raw": copy.deepcopy(turnover_component),
                    "fuel": copy.deepcopy(turnover_component),
                    "wip": copy.deepcopy(turnover_component),
                    "finished": copy.deepcopy(turnover_component),
                },
                "required": ["raw", "fuel", "wip", "finished"],
                "description": (
                    "四类存货分项：raw=原材料、fuel=燃料及动力、"
                    "wip=在产品、finished=产成品；每项须给周转天数和年基数。"
                ),
            },
            "short_term_loan_wan": {
                **copy.deepcopy(nonnegative),
                "description": "流动资金来源中的短期借款，单位万元。",
            },
            "self_funded_wan": {
                **copy.deepcopy(nonnegative),
                "description": "流动资金来源中的企业自筹，单位万元。",
            },
        },
        "minProperties": 1,
        "description": (
            "周转法流动资金参数；正式候选须提供应收、存货、现金、应付四类。"
            "未提供 annual_base_wan 时，应收默认基于收入，其余默认基于现金经营成本；"
            "short_term_loan_wan+self_funded_wan 应与流动资金需求闭合。"
        ),
    }
    properties["wc_turnover_days"] = {
        "type": "number", "minimum": 0, "maximum": 3650,
        "description": "兼容的统一周转天数；正式候选优先提供 wc_turnover 分项。",
    }
    staff_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "category": {"type": "string", "minLength": 1},
            "name": {"type": "string", "minLength": 1},
            "headcount": {"type": "number", "exclusiveMinimum": 0},
            "avg_wage_yuan": {"type": "number", "minimum": 0},
        },
        "required": ["headcount", "avg_wage_yuan"],
        "anyOf": [{"required": ["category"]}, {"required": ["name"]}],
        "description": "人员类别×人数×人均年工资；工资单位为元/人·年。",
    }
    for field in ("labor_plan", "staff_detail", "wage_detail"):
        properties[field] = {
            "type": "array",
            "items": copy.deepcopy(staff_item),
            "description": "劳动定员与工资明细；用于附表6-1独立复算。",
        }

    depreciation_class = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "original_value_wan": copy.deepcopy(nonnegative),
            "original_wan": copy.deepcopy(nonnegative),
            "depreciation_years": copy.deepcopy(positive_int),
            "dep_years": copy.deepcopy(positive_int),
            "salvage_rate": copy.deepcopy(rate),
        },
        "required": ["name"],
        "description": "固定资产分类折旧参数；金额单位万元。",
    }
    properties["depreciation_classes"] = {
        "type": "array",
        "items": depreciation_class,
        "minItems": 1,
        "description": "按房屋建筑、游乐设备等资产类别分别计算折旧。",
    }
    properties["renewal_capex_plan"] = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "year": {"type": "integer", "minimum": 1},
                "name": {"type": "string", "minLength": 1},
                "asset_class": {"type": "string", "minLength": 1},
                "amount_wan": copy.deepcopy(nonnegative),
                "depreciation_years": copy.deepcopy(positive_int),
                "salvage_rate": copy.deepcopy(rate),
            },
            "required": ["year", "amount_wan", "depreciation_years"],
        },
        "description": "运营期更新改造投资计划；year 为运营年序号，金额单位万元。",
    }
    properties["fiscal_support_policy"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["disabled", "fixed_revenue", "actual_cash_and_debt_service_gap"],
            },
            "annual_cap_wan": copy.deepcopy(nonnegative),
            "cap_by_year": copy.deepcopy(number_series),
            "include_debt_service": {"type": "boolean"},
        },
        "required": ["mode"],
        "description": "财政支持规则；缺口模式仅据实弥补现金及必要偿债缺口，不保证利润。",
    }
    properties["project_metadata"] = {
        "type": "object",
        "additionalProperties": True,
        "description": "项目标识与估值口径元数据；进入输入哈希及 FinanceRun 快照。",
    }
    for field in ("amort_bases", "debt_repay_sources", "loan_draw_plan"):
        properties[field] = {"type": "array", "items": {"type": "object"}}
    for field in (
        "cost_behavior", "tax_component_policy", "distribution_policy",
    ):
        properties[field] = {"type": "object"}
    properties["funding_annual_schedule"] = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "year": {"type": "integer", "minimum": 1},
                "construction_investment_wan": copy.deepcopy(nonnegative),
                "construction_interest_wan": copy.deepcopy(nonnegative),
                "working_capital_wan": copy.deepcopy(nonnegative),
                "capital_own_wan": copy.deepcopy(nonnegative),
                "loan_wan": copy.deepcopy(nonnegative),
                "gov_subsidy_wan": copy.deepcopy(nonnegative),
            },
            "required": [
                "year", "construction_investment_wan", "construction_interest_wan",
                "working_capital_wan", "capital_own_wan", "loan_wan", "gov_subsidy_wan",
            ],
        },
        "description": "建设期逐年资金用途与来源原子计划；各年用途与来源必须闭合。",
    }
    for field in (
        "is_operating", "surtax_on_vat", "revenue_tax_inclusive",
        "cost_behavior_confirmed", "tax_component_policy_confirmed",
        "arbitrary_reserve_confirmed_zero", "investor_distribution_confirmed_zero",
    ):
        properties[field] = {"type": "boolean"}
    properties["loan_repay_method"] = {
        "type": "string",
        "enum": ["equal_principal", "equal_payment", "bullet", "custom"],
    }
    properties["cost_policy"] = {
        "type": "string",
        "enum": ["user_items", "hybrid", "spec_variable"],
    }
    for field in ("industry", "invest_type"):
        properties[field] = {"type": "string", "minLength": 1}
    properties["annual_operating_cost_wan"] = {
        **copy.deepcopy(nonnegative),
        "description": "兼容别名：达产年现金经营成本，映射到 cost_items.年经营成本",
    }
    properties["capital_pct"] = {
        "type": "number", "minimum": 0, "maximum": 100,
        "description": (
            "兼容别名：0~1 按比例解释（0.4=40%），"
            "1~100 按百分数解释（40=40%）；1 表示100%"
        ),
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "description": (
            "确定性财务输入。annual_operating_cost_wan/cost_items/"
            "operating_cost_by_year/opex_by_year 不得提供冲突口径。"
        ),
    }


def finance_spec_candidate_schema() -> dict[str, Any]:
    """Return the discoverable candidate FinanceSpec contract used by MCP tools."""

    # Keep the public contract independent from runtime monkeypatching and from
    # the finance execution service.  Some clients inspect tools/list without
    # loading the calculation engine at all.
    schema = copy.deepcopy(_BASE_FINANCE_SPEC_SCHEMA)
    schema["additionalProperties"] = False
    properties = schema.setdefault("properties", {})
    # v3 判别字段与收购域字段必须出现在对外契约里：候选 schema 是
    # additionalProperties=False，缺声明会让客户端连 finance_kind 都传不进来，
    # 从而只能靠推断，通用可研又被拉进酒店收购校验器。
    for field, field_schema in _FINANCE_SPEC_V3_SCHEMA["properties"].items():
        if field not in properties:
            properties[field] = copy.deepcopy(field_schema)
    properties["finance_kind"] = copy.deepcopy(
        _FINANCE_SPEC_V3_SCHEMA["properties"]["finance_kind"]
    )
    input_schema = finance_input_schema()
    properties["finance_inputs"] = {
        **copy.deepcopy(input_schema),
        "description": "结构化确定性财务输入；与顶层 input_revision 重复字段必须一致。",
    }
    for field, field_schema in input_schema["properties"].items():
        if field not in properties:
            properties[field] = {
                **copy.deepcopy(field_schema),
                "description": (
                    str(field_schema.get("description") or "确定性财务输入")
                    + "；兼容扁平候选字段，推荐放入 finance_inputs 或 input_revision。"
                ),
            }
    schema["description"] = (
        "FinanceSpec 候选：收入模型、成本税费语义及完整确定性财务输入。"
        "未知字段拒绝；重复输入必须归一化后一致。"
    )
    return schema


def canonicalize_finance_inputs(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return effective inputs, an adoption ledger and rejected fields.

    Unknown fields are never retained in the effective dict.  Supported aliases
    are converted exactly once and conflicts fail closed instead of silently
    choosing one spelling.
    """

    effective: dict[str, Any] = {}
    adoption: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    source = dict(raw or {})

    for key, value in source.items():
        if key in _COMPATIBILITY_ALIASES:
            effective_key = _COMPATIBILITY_ALIASES[key]
            existing = effective.get(effective_key, source.get(effective_key))
            if existing is not None and existing != value:
                rejected.append({
                    "input": key,
                    "reason": "alias_conflict",
                    "conflicts_with": effective_key,
                })
                continue
            effective[effective_key] = value
            adoption.append({
                "input": key,
                "effective": effective_key,
                "raw_value": value,
                "effective_value": value,
                "status": "mapped",
            })
            continue
        if key in CANONICAL_FINANCE_FIELDS:
            if key == "cost_items" and value is not None and not isinstance(value, dict):
                rejected.append({
                    "input": key,
                    "reason": "cost_items_must_be_dict",
                    "detail": "cost_items 必须为 {名称: 金额} dict 形,列表形 [{name, ratio_pct}] 不被引擎支持",
                })
                continue
            if key == "invest_breakdown" and isinstance(value, dict):
                value = copy.deepcopy(value)
                if "engineering_items" in value and "construction_items" not in value:
                    value["construction_items"] = value.pop("engineering_items")
                elif "engineering_items" in value:
                    rejected.append({
                        "input": "invest_breakdown.engineering_items",
                        "reason": "alias_conflict",
                        "conflicts_with": "invest_breakdown.construction_items",
                    })
                    continue
            if key == "discount_rate":
                try:
                    if not 0 < float(value) < 1:
                        raise ValueError
                except (TypeError, ValueError):
                    rejected.append({"input": key, "reason": "rate_out_of_range"})
                    continue
            if key == "discount_rate_scenarios":
                if not isinstance(value, list) or any(
                    not isinstance(item, (int, float)) or not 0 < float(item) < 1
                    for item in value
                ):
                    rejected.append({"input": key, "reason": "invalid_discount_rate_scenarios"})
                    continue
            if key == "fare_multiplier_by_year":
                if not isinstance(value, list) or any(
                    not isinstance(item, (int, float)) or float(item) < 0
                    for item in value
                ):
                    rejected.append({"input": key, "reason": "invalid_fare_multiplier_by_year"})
                    continue
            if key == "renewal_capex_plan":
                if not isinstance(value, list) or any(
                    not isinstance(item, dict)
                    or not isinstance(item.get("year"), int)
                    or item.get("year", 0) < 1
                    or not isinstance(item.get("amount_wan"), (int, float))
                    or float(item.get("amount_wan") or 0) < 0
                    or not isinstance(item.get("depreciation_years"), int)
                    or item.get("depreciation_years", 0) < 1
                    or not _valid_optional_salvage_rate(item)
                    for item in value
                ):
                    rejected.append({"input": key, "reason": "invalid_renewal_capex_plan"})
                    continue
            if key == "fiscal_support_policy":
                if (
                    not isinstance(value, dict)
                    or value.get("mode") not in {
                        "disabled", "fixed_revenue", "actual_cash_and_debt_service_gap",
                    }
                ):
                    rejected.append({"input": key, "reason": "invalid_fiscal_support_policy"})
                    continue
            if key == "project_metadata" and not isinstance(value, dict):
                rejected.append({"input": key, "reason": "project_metadata_must_be_dict"})
                continue
            effective[key] = value
            adoption.append({
                "input": key, "effective": key, "raw_value": value,
                "effective_value": value, "status": "accepted",
            })
        elif key in _NON_COMPUTE_METADATA_FIELDS:
            adoption.append({
                "input": key, "effective": None, "status": "excluded_metadata",
            })
        elif key == "capital_pct":
            try:
                raw_percentage = float(value)
            except (TypeError, ValueError):
                rejected.append({"input": key, "reason": "invalid_percentage"})
                continue
            if not 0 <= raw_percentage <= 100:
                rejected.append({"input": key, "reason": "percentage_out_of_range"})
                continue
            mapped = raw_percentage if raw_percentage <= 1 else raw_percentage / 100.0
            existing = effective.get("capital_own_ratio", source.get("capital_own_ratio"))
            if existing is not None and abs(float(existing) - mapped) > 1e-12:
                rejected.append({
                    "input": key, "reason": "alias_conflict",
                    "conflicts_with": "capital_own_ratio",
                })
                continue
            effective["capital_own_ratio"] = mapped
            adoption.append({
                "input": key, "effective": "capital_own_ratio",
                "raw_value": value, "effective_value": mapped, "status": "mapped",
                "detected_representation": "ratio" if raw_percentage <= 1 else "percent",
                "warning": "capital_pct=1 按100%解释" if raw_percentage == 1 else None,
            })
        elif key == "annual_operating_cost_wan":
            if source.get("cost_items") or source.get("operating_cost_by_year") or source.get("opex_by_year"):
                rejected.append({
                    "input": key, "reason": "cost_input_conflict",
                    "conflicts_with": "cost_items|operating_cost_by_year|opex_by_year",
                })
                continue
            try:
                mapped_cost = float(value)
            except (TypeError, ValueError):
                rejected.append({"input": key, "reason": "invalid_number"})
                continue
            if mapped_cost < 0:
                rejected.append({"input": key, "reason": "negative_cost"})
                continue
            effective["cost_items"] = {"年经营成本": mapped_cost}
            adoption.append({
                "input": key, "effective": "cost_items.年经营成本",
                "raw_value": value, "effective_value": mapped_cost, "status": "mapped",
            })
        else:
            rejected.append({"input": key, "reason": "unknown_field"})
    return effective, adoption, rejected


def _valid_optional_salvage_rate(item: dict[str, Any]) -> bool:
    if "salvage_rate" not in item:
        return True
    value = item.get("salvage_rate")
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= float(value) < 1
    )


@dataclass(frozen=True)
class ResolvedParameter:
    field: str
    value: Any
    source: str
    source_ref: str = ""
    confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "source_ref": self.source_ref,
            "confirmed": self.confirmed,
        }


def resolve_parameter(field: str, candidates: list[dict[str, Any]]) -> ResolvedParameter | None:
    valid = [item for item in candidates if item.get("value") not in (None, "")]
    if not valid:
        return None
    by_source = {source: idx for idx, source in enumerate(SOURCE_PRIORITY)}
    valid.sort(key=lambda item: by_source.get(str(item.get("source")), len(SOURCE_PRIORITY)))
    winner = valid[0]
    return ResolvedParameter(
        field=field,
        value=winner.get("value"),
        source=str(winner.get("source") or "system_default"),
        source_ref=str(winner.get("source_ref") or ""),
        confirmed=bool(winner.get("confirmed")),
    )


def formal_source_ok(parameter: ResolvedParameter | None) -> bool:
    if parameter is None:
        return False
    if parameter.source in {"llm_suggestion", "system_default"}:
        return False
    return parameter.confirmed or parameter.source in {"policy_profile", "industry_profile"}


def build_source_ledger(
    resolved: dict[str, ResolvedParameter | None],
) -> list[dict[str, Any]]:
    return [item.to_dict() for item in resolved.values() if item is not None]


def resolve_run_inputs(
    finance_inputs: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,
    policy_profile: dict[str, Any] | None = None,
    industry_profile: dict[str, Any] | None = None,
) -> tuple[
    dict[str, Any], list[dict[str, Any]], list[str],
    list[dict[str, Any]], list[dict[str, Any]],
]:
    """Apply governed defaults without overriding project-confirmed values."""
    resolved_inputs, input_adoption, rejected_inputs = canonicalize_finance_inputs(
        finance_inputs or {}
    )
    policy = policy_profile or {}
    tax_policy = policy.get("tax") or {}
    spec_tax = (spec or {}).get("tax") or {}
    source_ledger: list[dict[str, Any]] = []

    mappings = {
        "income_tax_rate": "income_tax_rate",
        "vat_rate": "manufacturing_vat_rate",
        "vat_input_rate": "vat_input_rate",
    }
    for field, policy_key in mappings.items():
        policy_item = tax_policy.get(policy_key) or {}
        policy_value = policy_item.get("value") if isinstance(policy_item, dict) else policy_item
        candidates = [
            {
                "value": resolved_inputs.get(field), "source": "user_input",
                "confirmed": field in resolved_inputs,
            },
            {
                "value": spec_tax.get(field), "source": "project_document",
                "confirmed": (spec or {}).get("confirmation_status") == "confirmed",
            },
            {
                "value": policy_value, "source": "policy_profile",
                "source_ref": (policy_item.get("source_ref") if isinstance(policy_item, dict) else ""),
            },
        ]
        item = resolve_parameter(f"tax.{field}", candidates)
        if item is not None:
            resolved_inputs[field] = item.value
            source_ledger.append(item.to_dict())

    surtax = tax_policy.get("surtax") or {}
    if "surtax_on_vat" not in resolved_inputs and isinstance(surtax, dict):
        resolved_inputs["surtax_on_vat"] = surtax.get("base_mode") == "vat_base"
        source_ledger.append(ResolvedParameter(
            field="tax.surtax_on_vat",
            value=resolved_inputs["surtax_on_vat"],
            source="policy_profile",
            source_ref=str(surtax.get("source_ref") or ""),
        ).to_dict())
    if "surtax_vat_rate" not in resolved_inputs and isinstance(surtax, dict):
        if surtax.get("value") is not None:
            resolved_inputs["surtax_vat_rate"] = surtax.get("value")
            source_ledger.append(ResolvedParameter(
                field="tax.surtax_vat_rate",
                value=surtax.get("value"),
                source="policy_profile",
                source_ref=str(surtax.get("source_ref") or ""),
            ).to_dict())
    if isinstance(surtax, dict):
        for field in (
            "urban_maintenance_rate",
            "education_surcharge_rate",
            "local_education_surcharge_rate",
        ):
            if field in resolved_inputs or surtax.get(field) is None:
                continue
            resolved_inputs[field] = surtax[field]
            source_ledger.append(ResolvedParameter(
                field=f"tax.{field}", value=surtax[field], source="policy_profile",
                source_ref=str(surtax.get("source_ref") or ""),
            ).to_dict())

    profile = industry_profile or {}
    revenue = (spec or {}).get("revenue") or {}
    revenue_model = str(revenue.get("model") or "flat")
    required = list(profile.get("required_inputs") or [])
    required.extend(
        ((profile.get("required_by_revenue_model") or {}).get(revenue_model) or [])
    )

    def _required_value(field: str) -> Any:
        if field == "construction_items":
            return (resolved_inputs.get("invest_breakdown") or {}).get("construction_items")
        if field in {
            "products", "saleable_area", "price_per_sqm", "absorption",
            "annual_visitors", "spend_per_visitor", "visitor_ramp",
            "annual_gov_payment_wan", "payment_ramp", "annual_revenue_wan",
            "annual_passenger_trips", "passenger_unit", "average_fare_yuan",
            "ridership_ramp", "fare_multiplier_by_year",
        }:
            return revenue.get(field, resolved_inputs.get(field))
        return resolved_inputs.get(field)

    missing: list[str] = []
    for field in dict.fromkeys(str(item) for item in required):
        value = _required_value(field)
        if value in (None, "", [], {}):
            missing.append(str(field))
    allowed_models = [str(item) for item in profile.get("allowed_revenue_models") or []]
    if allowed_models and revenue_model not in allowed_models:
        missing.append(f"revenue.model_not_allowed:{revenue_model}")
    return resolved_inputs, source_ledger, missing, input_adoption, rejected_inputs
