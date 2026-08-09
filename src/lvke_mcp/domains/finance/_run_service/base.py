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


# 13 张交付附表 key（与 finance_model / 测试对齐，不含控制/展示表）
DELIVERY_TABLE_KEYS: tuple[str, ...] = (
    "investment",
    "interest-during-construction",
    "working-capital",
    "funding",
    "income-statement",
    "total-cost",
    "wage",
    "depreciation",
    "amortization",
    "profit-distribution",
    "debt-service",
    "cashflow",
    "capital-cashflow",
)


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
)


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
    """Return the valuation date used by this run and blocking errors, if any.

    Deterministic runs require an explicit valuation date whenever their mode
    is not ``estimate_preview`` so policy and manifest selection are reproducible.
    """
    value = str(valuation_date or "").strip()
    if value:
        try:
            date.fromisoformat(value)
        except ValueError:
            return "", [f"valuation_date 格式无效：{value}，应为 YYYY-MM-DD"]
        return value, []
    if mode != "estimate_preview":
        return "", ["非预览财务 run 必须显式传入 valuation_date，禁止依赖服务器当天日期"]
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
    for key in DELIVERY_TABLE_KEYS:
        tbl = tables.get(key)
        if tbl is None:
            continue
        delivery_no, title = meta_by_key.get(key, ("", key))
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
            "table_id": key,
            "delivery_no": delivery_no,
            "title": title,
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
