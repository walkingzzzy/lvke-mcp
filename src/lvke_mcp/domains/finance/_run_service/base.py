"""模型/模板版本、十三表交付键与元信息、hash 与幂等键原语、工作区读写与 manifest 解析。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any, Optional

from lvke_mcp.domains.finance.industry_registry import select_industry_profile
from lvke_mcp.domains.finance.model_manifest import (
    ModelManifest,
    build_manifest,
    manifest_from_dict,
)
from lvke_mcp.domains.finance.policy_registry import select_policy_profile


MODEL_VERSION = "finance_model.v2.4"


TEMPLATE_VERSION = "finance_tables.v3"


# 正式交付编号（附表6-1/6-2/6-3不是简单的第7/8/9张表）。
DELIVERY_TABLE_META: tuple[tuple[str, str, str], ...] = (
    ("investment", "附表1", "固定资产投资估算表"),
    ("interest-during-construction", "附表2", "建设期贷款利息表"),
    ("working-capital", "附表3", "流动资金估算表"),
    ("funding", "附表4", "投资使用计划与资金筹措表"),
    ("income-statement", "附表5", "营业收入、税金及附加和增值税估算表"),
    ("total-cost", "附表6", "总成本费用估算表"),
    ("wage", "附表6-1", "工资及附加估算表"),
    ("depreciation", "附表6-2", "固定资产折旧费估算表"),
    ("amortization", "附表6-3", "无形资产及其他资产摊销估算表"),
    ("profit-distribution", "附表7", "利润与利润分配表"),
    ("debt-service", "附表8", "还款付息测算表"),
    ("cashflow", "附表9", "项目投资现金流量表"),
    ("capital-cashflow", "附表10", "项目资本金流量表"),
    # 附表11 财务计划现金流量表。2023 大纲 financial_sustainability 要求此表，
    # 附表9/10 只覆盖项目投资与资本金两个口径，给不出「各期期末现金、累计盈余、
    # 是否存在资金缺口」——即原 known_gap 所述"只能部分覆盖"。
    #
    # 编号取 11 而非内部代号 C03：权威参考工作簿《投资类项目经济计算表.xlsx》
    # 没有这张表（实测「财务计划」/「附表11」零命中），无外部编号可继承；
    # 附表10 是现有最大号（13 张表只排到 10，因 6-1/6-2/6-3 是附表6 子表），
    # 11 是自然续号。交付件里出现"控制表 C03"会让审查方无从对应大纲条款。
    #
    # 它的参考结构已在 docs/reference_table_schema.json 冻结，并以
    # reference_provenance=engine_defined_no_reference_sheet 显式声明"无底稿"，
    # 因此可达 reference 级、不会卡住正式交付门禁 all_tables_reference_grade。
    ("financial-plan", "附表11", "财务计划现金流量表"),
)


# 唯一交付成员和顺序由 DELIVERY_TABLE_META 派生；参考来源 sheet 不得进入此集合。
DELIVERY_TABLE_KEYS: tuple[str, ...] = tuple(item[0] for item in DELIVERY_TABLE_META)
DELIVERY_TABLE_SCHEMA_VERSION = "finance_delivery_tables.v1"
ENGINE_DELIVERY_COUNT = len(DELIVERY_TABLE_KEYS)
REFERENCE_SOURCE_SHEET_COUNT = 15
REVIEW_WORKBOOK_SHEET_COUNT = 16

# 必需列只描述跨渲染后端必须存在的稳定语义；可变的明细列仍由表 builder 管理。
_DELIVERY_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "investment": ("name",),
    "interest-during-construction": ("item", "total"),
    "working-capital": ("item", "amount"),
    "funding": ("name", "amount"),
    "income-statement": ("item", "total"),
    "total-cost": ("item", "total"),
    "wage": ("item", "total"),
    "depreciation": ("item", "total"),
    "amortization": ("item", "total"),
    "profit-distribution": ("item", "total"),
    "debt-service": ("item", "total"),
    "cashflow": ("item", "total"),
    "capital-cashflow": ("item", "total"),
    # 附表11 是逐年记录表（每行一年），不是"项目/合计"式科目表，故必填列取
    # period + cumulative 这两个立表根据。键名以运行时生产者
    # annual._build_financial_plan 为准（**不是** statements.financial_plan_rows，
    # 后者全仓无调用方、键名不同，是同语义的第二份实现且已成死代码）。
    "financial-plan": ("period", "cumulative"),
}

_DELIVERY_REQUIRED_COLUMN_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "investment": (("amount", "total"),),
}

_DELIVERY_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "investment": ("finance_inputs.invest_breakdown", "investment"),
    "interest-during-construction": ("funding.loan", "annual.interest_during_construction"),
    "working-capital": ("finance_inputs.wc_turnover", "investment.working_capital"),
    "funding": ("investment.total", "funding"),
    "income-statement": ("annual.income_statement",),
    "total-cost": ("annual.total_cost",),
    "wage": ("annual.wage",),
    "depreciation": ("annual.depreciation_table",),
    "amortization": ("annual.amortization_table",),
    "profit-distribution": ("annual.profit_distribution",),
    "debt-service": ("annual.debt_service",),
    "cashflow": ("annual.project_cashflow",),
    "capital-cashflow": ("annual.capital_cashflow",),
    "financial-plan": ("annual.financial_plan",),
}

_DELIVERY_RECONCILIATION_RULES: dict[str, tuple[str, ...]] = {
    "investment": ("investment_total_reconciles",),
    "interest-during-construction": ("construction_interest_reconciles",),
    "working-capital": ("working_capital_reconciles",),
    "funding": ("funding_sources_equal_uses", "annual_funding_plan_reconciles"),
    "income-statement": ("revenue_tax_reconciles",),
    "total-cost": ("total_cost_reconciles",),
    "wage": ("wage_subtotal_reconciles",),
    "depreciation": ("depreciation_rollforward_reconciles",),
    "amortization": ("amortization_rollforward_reconciles",),
    "profit-distribution": ("profit_distribution_reconciles",),
    "debt-service": ("debt_balance_reconciles", "coverage_ratios_recompute"),
    "cashflow": ("project_cashflow_reconciles",),
    "capital-cashflow": ("capital_cashflow_reconciles",),
    # 只声明确有实现的判据：checks.py 的「财务计划无资金缺口年」按
    # annual.financial_plan 的 gap 逐年判定（非阻断，属可持续性提示）。
    # 不编造无人执行的规则名，否则会变成永远无法满足的伪要求。
    "financial-plan": ("financial_plan_no_funding_gap",),
}


def delivery_table_contract() -> list[dict[str, Any]]:
    """Return the versioned 13-table contract in immutable delivery order."""

    return [
        {
            "table_code": key,
            "table_id": key,
            "delivery_no": delivery_no,
            "title": title,
            "order": order,
            "unit": "万元",
            "period_semantics": (
                # 附表11 与附表9/10 同属全周期表：_build_financial_plan 先输出
                # 建设期各年，再输出运营期各年。
                "construction_and_operation_years"
                if key in {"cashflow", "capital-cashflow", "financial-plan"}
                else "construction_years"
                if key in {"investment", "interest-during-construction", "funding"}
                else "operation_years"
            ),
            "required_columns": list(_DELIVERY_REQUIRED_COLUMNS[key]),
            "required_column_groups": [
                list(group)
                for group in _DELIVERY_REQUIRED_COLUMN_GROUPS.get(key, ())
            ],
            "minimum_rows": 1,
            "formula_dependencies": list(_DELIVERY_DEPENDENCIES[key]),
            "reconciliation_rules": list(_DELIVERY_RECONCILIATION_RULES[key]),
            "source": "deterministic_finance_run",
            "schema_version": DELIVERY_TABLE_SCHEMA_VERSION,
        }
        for order, (key, delivery_no, title) in enumerate(DELIVERY_TABLE_META, start=1)
    ]


def delivery_table_contract_hash() -> str:
    return _sha256_hex(_stable_json(delivery_table_contract()))


def delivery_count_semantics() -> dict[str, int]:
    return {
        "engine_delivery_count": ENGINE_DELIVERY_COUNT,
        "reference_source_sheet_count": REFERENCE_SOURCE_SHEET_COUNT,
        "review_workbook_sheet_count": REVIEW_WORKBOOK_SHEET_COUNT,
    }


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _sha256_hex(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_input_hash(finance_inputs: dict[str, Any], *, invest_type: str = "",
                       build_period_months: Any = None, industry: str = "") -> str:
    payload = {
        "finance": finance_inputs or {},
        "invest_type": invest_type or "",
        "build_period_months": build_period_months,
        "industry": industry or "",
    }
    return _sha256_hex(_stable_json(payload))


def compute_spec_hash(spec: Optional[dict[str, Any]]) -> str:
    if not spec:
        return _sha256_hex("null")
    return _sha256_hex(_stable_json(spec))


def compute_table_bundle_hash(tables: dict[str, Any]) -> str:
    delivery = {k: (tables or {}).get(k) for k in DELIVERY_TABLE_KEYS}
    return _sha256_hex(_stable_json(delivery))


def compute_idempotency_key(
    workspace_id: str,
    *,
    input_hash: str,
    spec_hash: str,
    spec_id: str = "",
    model_version: str = MODEL_VERSION,
    template_version: str = TEMPLATE_VERSION,
    manifest_hash: str = "",
    valuation_date: str = "",
    basis_of_estimate_hash: str = "",
) -> str:
    raw = "|".join([
        str(workspace_id),
        input_hash or "",
        spec_hash or "",
        spec_id or "",
        model_version or MODEL_VERSION,
        template_version or TEMPLATE_VERSION,
        manifest_hash or "",
        valuation_date or "",
        basis_of_estimate_hash or "",
    ])
    return _sha256_hex(raw)


def _resolve_valuation_date_for_mode(mode: str, valuation_date: str = "") -> tuple[str, list[str]]:
    """Return a valid valuation date; absent values use an explicit run snapshot date."""
    value = str(valuation_date or "").strip()
    if value:
        try:
            date.fromisoformat(value)
        except ValueError:
            return "", [f"valuation_date 格式无效：{value}，应为 YYYY-MM-DD"]
        return value, []
    return date.today().isoformat(), []


def resolve_model_manifest(
    *,
    industry: str = "",
    valuation_date: str = "",
    requested_manifest: Optional[dict[str, Any]] = None,
) -> tuple[ModelManifest, dict[str, Any], dict[str, Any], list[str]]:
    """Resolve governed model, policy, industry and template versions for a run."""
    as_of = valuation_date or date.today().isoformat()
    errors: list[str] = []
    try:
        policy = select_policy_profile(as_of=as_of)
    except Exception as exc:  # noqa: BLE001
        policy = {}
        errors.append(f"policy_profile: {exc}")
    try:
        industry_profile = select_industry_profile(industry)
    except Exception as exc:  # noqa: BLE001
        industry_profile = {}
        errors.append(f"industry_profile: {exc}")

    if isinstance(requested_manifest, dict):
        manifest = manifest_from_dict(requested_manifest)
    else:
        manifest = build_manifest(
            industry_profile_version=str(industry_profile.get("version") or "general.v1"),
            policy_version=str(policy.get("version") or "cn_tax_policy.2026-01"),
            model_version=MODEL_VERSION,
            template_version=TEMPLATE_VERSION,
            effective_from=str(policy.get("effective_from") or "2026-01-01"),
        )
    errors.extend(manifest.validate(as_of=as_of))
    if policy and manifest.policy_version != policy.get("version"):
        errors.append(
            f"manifest policy_version={manifest.policy_version} does not match active policy={policy.get('version')}"
        )
    if industry_profile and manifest.industry_profile_version != industry_profile.get("version"):
        errors.append(
            "manifest industry_profile_version="
            f"{manifest.industry_profile_version} does not match resolved profile={industry_profile.get('version')}"
        )
    return manifest, policy, industry_profile, errors


def _read_workspace_req(workspace_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """读 MCP 自有 workspace 的 requirement 快照（无则空，调用方以参数覆盖）。"""
    from lvke_mcp.runtime.workspace import workspace_root

    meta: dict[str, Any] = {}
    try:
        path = workspace_root(str(workspace_id)) / "requirement.json"
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
    except Exception:  # noqa: BLE001
        meta = {}
    req = meta.get("requirement") or {}
    if not isinstance(req, dict):
        req = {}
    finance_in = dict(req.get("finance") or {})
    return meta, req, finance_in


def _markdown_table_row_count(md: str) -> int:
    """Count data rows in a rendered Markdown table.

    Delivery tables are stored as GFM Markdown strings (header + ``| --- |``
    separator + data rows), so a plain ``isinstance(list/dict)`` check misses
    them and reports 0.  Count pipe rows, drop the separator row(s), then drop
    one header row; never go below zero.
    """

    data_rows = 0
    saw_header = False
    for line in md.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = stripped.strip("|").split("|")
        if cells and all(re.fullmatch(r"[\s:\-]*", cell) for cell in cells):
            continue  # the ``| --- | --- |`` separator row is not data
        if not saw_header:
            saw_header = True  # first non-separator pipe row is the header
            continue
        data_rows += 1
    return data_rows


def _table_manifest(fin: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    tables = (fin or {}).get("tables") or {}
    out: list[dict[str, Any]] = []
    meta_by_key = {key: (delivery_no, title) for key, delivery_no, title in DELIVERY_TABLE_META}
    contract_by_key = {
        item["table_code"]: item for item in delivery_table_contract()
    }
    contract_hash = delivery_table_contract_hash()
    for key in DELIVERY_TABLE_KEYS:
        tbl = tables.get(key)
        if tbl is None:
            continue
        delivery_no, title = meta_by_key.get(key, ("", key))
        contract = contract_by_key.get(key, {})
        content = _stable_json(tbl)
        if isinstance(tbl, list):
            row_count = len(tbl)
        elif isinstance(tbl, dict):
            row_count = len(tbl.get("rows") or [])
        elif isinstance(tbl, str):
            row_count = _markdown_table_row_count(tbl)
        else:
            row_count = 0
        out.append({
            "table_code": key,
            "table_id": key,
            "delivery_no": delivery_no,
            "title": title,
            "order": contract.get("order"),
            "unit": contract.get("unit"),
            "period_semantics": contract.get("period_semantics"),
            "required_columns": list(contract.get("required_columns") or []),
            "required_column_groups": [
                list(group) for group in contract.get("required_column_groups") or []
            ],
            "minimum_rows": contract.get("minimum_rows"),
            "formula_dependencies": list(contract.get("formula_dependencies") or []),
            "reconciliation_rules": list(contract.get("reconciliation_rules") or []),
            "source": contract.get("source"),
            "schema_version": contract.get("schema_version"),
            "contract_hash": contract_hash,
            "run_id": run_id or fin.get("run_id") or "",
            "template_version": fin.get("template_version") or TEMPLATE_VERSION,
            "row_count": row_count,
            "content_hash": _sha256_hex(content),
        })
    return out


def _ensure_workspace(workspace_id: str) -> None:
    """确保 MCP workspace 目录存在。"""
    from lvke_mcp.runtime.workspace import workspace_root

    workspace_root(str(workspace_id)).mkdir(parents=True, exist_ok=True)


def _project_brief(workspace_id: str) -> str:
    """项目简述（MCP 边界无资料链；LLM 定 spec 时回退空简述）。"""
    return ""
