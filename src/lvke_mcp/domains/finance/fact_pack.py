"""Authoritative finance_fact_pack.v1 normalization, sealing and projection.

The client may edit candidate facts, but it cannot self-assert evidence grade,
review status or delivery grade.  Confirmation replays every source/evidence
binding against the workspace source store and seals the resulting snapshot.
Only a valid confirmed seal may project facts into the deterministic engine.

Seal v2 is server-only: content hash + HMAC (workspace-local secret) + durable
ledger entry.  Public SHA-256 alone is not enough to forge a formal pack.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

VERSION = "finance_fact_pack.v1"
LEGACY_VERSION = "finance_fact_pack.v0"
SEAL_VERSION = "finance_fact_pack_seal.v2"
LEGACY_SEAL_VERSION = "finance_fact_pack_seal.v1"

DOMAIN_KEYS = (
    "construction_items",
    "products",
    "cost_items",
    "staff_detail",
    "asset_classes",
    "wc_turnover",
    "funding_plan",
    "debt_schedule",
    "amort_bases",
    "distribution_policy",
    "cost_behavior",
    "tax_component_policy",
)

CORE_DOMAIN_KEYS = DOMAIN_KEYS[:9]
POLICY_DOMAIN_KEYS = DOMAIN_KEYS[9:]

EvidenceResolver = Callable[..., dict[str, Any]]

_SEAL_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash_material(pack: dict[str, Any]) -> dict[str, Any]:
    material = copy.deepcopy(pack)
    # Signatures are never part of the content hash material.
    material.pop("fact_pack_hash", None)
    material.pop("seal_mac", None)
    material.pop("seal_id", None)
    return material


def compute_fact_pack_hash(pack: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical(_hash_material(pack)).encode("utf-8")
    ).hexdigest()


def _mcp_data_root() -> Path:
    from lvke_mcp.runtime.workspace import data_root

    return data_root()


def _workspace_root(workspace_id: str) -> Path:
    root = _mcp_data_root() / "workspaces" / str(workspace_id or "local")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _seal_secret(workspace_id: str) -> bytes:
    """Return a durable per-workspace HMAC secret (create if missing)."""
    env_secret = (
        os.environ.get("LVKE_FACT_PACK_SEAL_SECRET")
        or ""
    ).strip()
    if env_secret:
        return env_secret.encode("utf-8")
    path = _workspace_root(workspace_id) / ".fact_pack_seal_secret"
    with _SEAL_LOCK:
        if path.is_file():
            raw = path.read_bytes().strip()
            if raw:
                return raw
        secret = secrets.token_bytes(32)
        path.write_bytes(secret)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return secret


def _seal_ledger_path(workspace_id: str) -> Path:
    return _workspace_root(workspace_id) / "fact_pack_seal_ledger.jsonl"


def _record_seal_ledger(
    workspace_id: str,
    *,
    seal_id: str,
    content_hash: str,
    seal_mac: str,
    ceiling: str,
) -> None:
    entry = {
        "seal_id": seal_id,
        "content_hash": content_hash,
        "seal_mac": seal_mac,
        "ceiling": ceiling,
        "sealed_at": _now_iso(),
        "workspace_id": workspace_id,
    }
    path = _seal_ledger_path(workspace_id)
    with _SEAL_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(entry) + "\n")


def _ledger_has_seal(
    workspace_id: str,
    *,
    seal_id: str,
    content_hash: str,
    seal_mac: str,
) -> bool:
    path = _seal_ledger_path(workspace_id)
    if not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            str(row.get("seal_id") or "") == seal_id
            and str(row.get("content_hash") or "") == content_hash
            and str(row.get("seal_mac") or "") == seal_mac
        ):
            return True
    return False


def compute_fact_pack_mac(
    pack: dict[str, Any],
    *,
    workspace_id: str,
    content_hash: str | None = None,
) -> str:
    digest = content_hash or compute_fact_pack_hash(pack)
    material = f"{digest}|{workspace_id}|{pack.get('seal_id') or ''}|{pack.get('sealed_at') or ''}"
    secret = _seal_secret(workspace_id)
    return "hmac-sha256:" + hmac.new(
        secret, material.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_fact_pack_seal(
    pack: Any,
    *,
    workspace_id: str | None = None,
    require_ledger: bool = True,
) -> dict[str, Any]:
    issues: list[str] = []
    if not isinstance(pack, dict):
        return {"ok": False, "issues": ["finance_fact_pack 缺失或不是对象"]}
    if pack.get("version") != VERSION:
        issues.append("finance_fact_pack version 非 v1")
    if str(pack.get("confirmation_status") or "") != "confirmed":
        issues.append("finance_fact_pack 未 confirmed")
    seal_version = str(pack.get("seal_version") or "")
    if seal_version not in {SEAL_VERSION, LEGACY_SEAL_VERSION}:
        issues.append("finance_fact_pack 未由服务端 seal")
    if not str(pack.get("sealed_at") or "").strip():
        issues.append("finance_fact_pack 缺 sealed_at")

    # Content hash excludes mac/id so re-verify is stable.
    expected = compute_fact_pack_hash(pack)
    if str(pack.get("fact_pack_hash") or "") != expected:
        issues.append("finance_fact_pack hash 不匹配")

    # Legacy v1 seals (hash-only) are no longer accepted for formal use.
    if seal_version == LEGACY_SEAL_VERSION:
        issues.append("finance_fact_pack seal 为 v1（仅公开 hash），须重新服务端确认")
    elif seal_version == SEAL_VERSION:
        seal_id = str(pack.get("seal_id") or "").strip()
        seal_mac = str(pack.get("seal_mac") or "").strip()
        if not seal_id:
            issues.append("finance_fact_pack 缺 seal_id")
        if not seal_mac:
            issues.append("finance_fact_pack 缺 seal_mac")
        ws = str(
            workspace_id
            or pack.get("seal_workspace_id")
            or pack.get("project_id")
            or ""
        ).strip()
        if not ws:
            issues.append("finance_fact_pack 缺 seal workspace 上下文")
        elif seal_mac and seal_id:
            expected_mac = compute_fact_pack_mac(
                pack, workspace_id=ws, content_hash=expected,
            )
            if not hmac.compare_digest(seal_mac, expected_mac):
                issues.append("finance_fact_pack seal_mac 不匹配")
            elif require_ledger and not _ledger_has_seal(
                ws,
                seal_id=seal_id,
                content_hash=expected,
                seal_mac=seal_mac,
            ):
                issues.append("finance_fact_pack 不在服务端 seal ledger")

    return {"ok": not issues, "issues": issues, "expected_hash": expected}


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _stable_item_id(domain: str, row: dict[str, Any], index: int) -> str:
    explicit = str(row.get("item_id") or row.get("id") or "").strip()
    if explicit:
        return explicit
    identity = {
        "domain": domain,
        "name": row.get("name") or row.get("category") or "",
        "unit": row.get("unit") or "",
        "index": index,
    }
    return f"{domain[:8]}_{hashlib.sha256(_canonical(identity).encode('utf-8')).hexdigest()[:12]}"


def _rows_with_item_ids(domain: str, value: Any) -> list[dict[str, Any]]:
    rows = _rows(value)
    for index, row in enumerate(rows):
        row["item_id"] = _stable_item_id(domain, row, index)
    return rows


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _nonnegative_present(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        return float(value) >= 0
    except (TypeError, ValueError):
        return False


def _inventory_complete(value: Any) -> bool:
    row = _record(value)
    aliases = (
        ("raw", "raw_material", "materials", "原材料"),
        ("fuel", "energy", "燃料", "动力", "燃料及动力"),
        ("wip", "work_in_progress", "在产品"),
        ("finished", "finished_goods", "fg", "产成品"),
    )
    def complete_component(raw: Any) -> bool:
        if isinstance(raw, dict):
            return _positive(raw.get("days")) and _positive(raw.get("base_wan"))
        return False

    return all(
        any(complete_component(row.get(key)) for key in group)
        for group in aliases
    )


def _year_sequence_issues(
    rows: list[dict[str, Any]],
    *,
    label: str,
    expected_start: int | None = None,
    expected_end: int | None = None,
) -> list[str]:
    if not rows:
        return []
    years: list[int] = []
    issues: list[str] = []
    for row in rows:
        raw_year = row.get("year") if row.get("year") is not None else row.get("period")
        try:
            years.append(int(raw_year))
        except (TypeError, ValueError):
            issues.append(f"{label} 缺少有效 year")
    if len(years) != len(rows):
        return issues
    if len(set(years)) != len(years):
        issues.append(f"{label} 年份重复: {years}")
    ordered = sorted(years)
    if years != ordered:
        issues.append(f"{label} 年份未排序: {years}")
    if ordered and ordered != list(range(ordered[0], ordered[-1] + 1)):
        issues.append(f"{label} 年份不连续: {ordered}")
    if expected_start is not None and ordered and ordered[0] != expected_start:
        issues.append(f"{label} 必须从 year={expected_start} 开始")
    if expected_end is not None and ordered and ordered[-1] != expected_end:
        issues.append(f"{label} 必须结束于 year={expected_end}")
    return issues


def assess_domain_depth(domains: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    construction = _rows(domains.get("construction_items"))
    qty_indicator = sum(
        1 for row in construction
        if str(row.get("name") or "").strip()
        and _positive(row.get("quantity"))
        and _positive(row.get("indicator_yuan") or row.get("indicator"))
    )
    checks["construction_items"] = {
        "ok": len(construction) >= 3 and qty_indicator >= 3,
        "detail_count": len(construction),
        "quantity_indicator_pairs": qty_indicator,
        "required": "≥3 项且工程量×指标成对",
    }

    products = _rows(domains.get("products"))
    valid_products = [
        row for row in products
        if str(row.get("name") or "").strip()
        and _positive(row.get("price_per_unit"))
        and _positive(row.get("capacity"))
        and isinstance(row.get("ramp"), list)
        and len(row.get("ramp") or []) >= 2
    ]
    checks["products"] = {
        "ok": bool(valid_products),
        "detail_count": len(valid_products),
        "required": "≥1 个产品，含单价、达产数量和≥2点爬坡",
    }

    costs = _record(domains.get("cost_items"))
    valid_costs = {str(k): v for k, v in costs.items() if str(k).strip() and _positive(v)}
    checks["cost_items"] = {
        "ok": len(valid_costs) >= 3,
        "detail_count": len(valid_costs),
        "required": "≥3 个成本项目",
    }

    staff = _rows(domains.get("staff_detail"))
    valid_staff = [
        row for row in staff
        if str(row.get("category") or row.get("name") or "").strip()
        and _positive(row.get("headcount"))
        and _positive(row.get("avg_wage_yuan"))
    ]
    checks["staff_detail"] = {
        "ok": bool(valid_staff),
        "detail_count": len(valid_staff),
        "required": "≥1 类人员，含定员和人均年工资",
    }

    assets = _rows(domains.get("asset_classes"))
    valid_assets = [
        row for row in assets
        if str(row.get("name") or "").strip()
        and _positive(row.get("original_wan") or row.get("original_value_wan"))
        and _positive(row.get("dep_years") or row.get("depreciation_years"))
    ]
    checks["asset_classes"] = {
        "ok": len(valid_assets) >= 2,
        "detail_count": len(valid_assets),
        "required": "≥2 个资产类别，含原值和折旧年限",
    }

    wc = _record(domains.get("wc_turnover"))
    short_term_loan = wc.get("short_term_loan_wan")
    self_funded = wc.get("self_funded_wan")
    checks["wc_turnover"] = {
        "ok": (
            _positive(wc.get("receivable"))
            and _positive(wc.get("cash"))
            and _positive(wc.get("payable"))
            and _inventory_complete(wc.get("inventory_detail"))
            and _nonnegative_present(short_term_loan)
            and _nonnegative_present(self_funded)
            and float(short_term_loan or 0.0) + float(self_funded or 0.0) > 0
        ),
        "required": "应收/现金/应付 + 原料/燃料/在产品/产成品周转 + 短贷/自筹来源",
    }

    funding = _record(domains.get("funding_plan"))
    schedule = _rows(funding.get("annual_schedule") or funding.get("schedule"))
    funding_year_issues = _year_sequence_issues(
        schedule, label="funding_plan", expected_start=1,
    )
    funding_balance_issues: list[str] = []
    for row in schedule:
        atomic_fields = (
            "construction_investment_wan", "construction_interest_wan",
            "working_capital_wan", "capital_own_wan", "loan_wan", "gov_subsidy_wan",
        )
        if any(row.get(key) in (None, "") for key in atomic_fields):
            funding_balance_issues.append(f"funding_plan year={row.get('year')} 缺原子字段")
            continue
        uses = sum(float(row.get(key) or 0.0) for key in atomic_fields[:3])
        sources = sum(float(row.get(key) or 0.0) for key in atomic_fields[3:])
        if abs(uses - sources) > 0.05:
            funding_balance_issues.append(
                f"funding_plan year={row.get('year')} 用途 {uses} != 来源 {sources}"
            )
    valid_schedule = [
        row for row in schedule
        if (
            _nonnegative_present(row.get("construction_investment_wan"))
            and _nonnegative_present(row.get("construction_interest_wan"))
            and _nonnegative_present(row.get("working_capital_wan"))
        )
        and (
            _positive(row.get("capital_own_wan"))
            or _positive(row.get("loan_wan"))
            or _positive(row.get("gov_subsidy_wan"))
        )
    ]
    checks["funding_plan"] = {
        "ok": bool(valid_schedule) and not funding_year_issues and not funding_balance_issues,
        "detail_count": len(valid_schedule),
        "year_issues": funding_year_issues,
        "balance_issues": funding_balance_issues,
        "required": "≥1 个建设期投资用途+资金来源分年",
    }

    debt = _record(domains.get("debt_schedule"))
    draws = _rows(debt.get("draws") or debt.get("schedule"))
    principal_schedule = _rows(debt.get("principal_schedule"))
    interest_schedule = _rows(debt.get("reference_interest_schedule"))
    debt_year_issues = _year_sequence_issues(draws, label="debt_schedule.draws", expected_start=1)
    debt_year_issues.extend(_year_sequence_issues(principal_schedule, label="debt_schedule.principal_schedule"))
    debt_year_issues.extend(_year_sequence_issues(interest_schedule, label="debt_schedule.reference_interest_schedule"))
    repay_sources = _rows(debt.get("debt_repay_sources") or debt.get("repay_sources"))
    valid_draws = [row for row in draws if _positive(row.get("draw_wan") or row.get("loan_wan"))]
    valid_sources = [
        row for row in repay_sources
        if str(row.get("name") or "").strip()
        and (_positive(row.get("share")) or _positive(row.get("annual_wan")))
    ]
    checks["debt_schedule"] = {
        "ok": (
            bool(valid_draws)
            and len(valid_sources) >= 3
            and _positive(debt.get("loan_rate"))
            and _positive(debt.get("loan_years"))
            and not debt_year_issues
        ),
        "draw_count": len(valid_draws),
        "repay_source_count": len(valid_sources),
        "year_issues": debt_year_issues,
        "required": "提款计划、利率/期限及≥3个偿债资金来源",
    }

    amort = _rows(domains.get("amort_bases"))
    valid_amort = [
        row for row in amort
        if str(row.get("name") or "").strip()
        and _positive(row.get("original_wan") or row.get("original_value_wan"))
        and _positive(row.get("amort_years") or row.get("amortization_years"))
    ]
    amort_names = [str(row.get("name") or "") for row in valid_amort]
    amort_classes_ok = (
        any("土地" in name for name in amort_names)
        and any("其他" in name for name in amort_names)
    )
    checks["amort_bases"] = {
        "ok": len(valid_amort) >= 2 and amort_classes_ok,
        "detail_count": len(valid_amort),
        "required": "土地使用权+其他资产两类摊销基础，含原值和摊销年限",
    }

    distribution = _record(domains.get("distribution_policy"))
    checks["distribution_policy"] = {
        "ok": bool(
            distribution.get("statutory_reserve_rate") is not None
            or distribution.get("statutory_reserve_confirmed_zero") is True
            and (
                distribution.get("arbitrary_reserve_confirmed_zero") is True
                or distribution.get("arbitrary_reserve_rate") is not None
            )
            and (
                distribution.get("investor_distribution_confirmed_zero") is True
                or distribution.get("investor_distribution_rate") is not None
                or isinstance(distribution.get("investor_distribution_schedule_wan"), list)
            )
            and str(distribution.get("retained_profit_policy") or "").strip()
        ),
        "required": "法定/任意公积金、投资方分配与留存政策（零值须显式确认）",
    }

    behavior = _record(domains.get("cost_behavior"))
    behavior_items = _record(behavior.get("items") or behavior)
    behavior_items.pop("confirmed", None)
    cost_names = set(valid_costs)
    behavior_ok = bool(cost_names) and cost_names.issubset(set(behavior_items))
    if behavior_ok:
        for name in cost_names:
            rule = behavior_items.get(name)
            if isinstance(rule, str):
                kind = rule.lower()
                rule = {"type": kind}
            else:
                rule = _record(rule)
                kind = str(rule.get("type") or rule.get("behavior") or "").lower()
            if kind not in {"fixed", "variable", "mixed"}:
                behavior_ok = False
                break
            if kind == "mixed" and not (
                _nonnegative_present(rule.get("variable_share"))
                and str(rule.get("driver_fact_path") or "").strip()
            ):
                behavior_ok = False
                break
    checks["cost_behavior"] = {
        "ok": behavior_ok and behavior.get("confirmed") is True,
        "required": "每个成本项确认 fixed/variable/mixed；mixed 含比例和驱动 fact_path",
    }

    tax_policy = _record(domains.get("tax_component_policy"))
    checks["tax_component_policy"] = {
        "ok": bool(
            tax_policy.get("confirmed") is True
            and _nonnegative_present(tax_policy.get("vat_output_rate"))
            and _nonnegative_present(tax_policy.get("vat_input_rate"))
            and _nonnegative_present(tax_policy.get("income_tax_rate"))
            and str(tax_policy.get("surtax_base") or "")
            == "vat_and_consumption_tax_payable"
            and _nonnegative_present(tax_policy.get("urban_maintenance_rate"))
            and _nonnegative_present(tax_policy.get("education_surcharge_rate"))
            and _nonnegative_present(tax_policy.get("local_education_surcharge_rate"))
        ),
        "required": "销项/进项/所得税率及以实际应纳增值税与消费税合计为基数的三项附加税率",
    }

    passed = sum(1 for item in checks.values() if item.get("ok"))
    return {
        "ok": passed == len(DOMAIN_KEYS),
        "coverage": round(passed / len(DOMAIN_KEYS), 4),
        "passed": passed,
        "required": len(DOMAIN_KEYS),
        "by_domain": checks,
        "missing_domains": [key for key in DOMAIN_KEYS if not checks[key].get("ok")],
    }


def _domain_fact_leaves(domain: str, value: Any) -> list[dict[str, Any]]:
    """Enumerate labeled numeric fact leaves that formal evidence must each bind.

    Each leaf: {"fact_path": str, "value": float, "unit": str|None, "period": Any}.
    fact_path is a stable address (e.g. construction_items[0].quantity).
    """
    leaves: list[dict[str, Any]] = []

    def _add(fact_path: str, raw: Any, *, unit: Any = None, period: Any = None) -> None:
        if raw in (None, "", [], {}):
            return
        if isinstance(raw, (dict, list, tuple)):
            return
        normalized: Any
        if isinstance(raw, bool):
            normalized = raw
        else:
            try:
                normalized = float(raw)
            except (TypeError, ValueError):
                normalized = str(raw).strip()
        leaves.append({
            "fact_path": fact_path,
            "value": normalized,
            "unit": (str(unit) if unit not in (None, "") else None),
            "period": period,
        })

    if domain in {
        "construction_items", "products", "staff_detail", "asset_classes", "amort_bases",
    }:
        for idx, row in enumerate(_rows(value)):
            ident = _stable_item_id(domain, row, idx)
            for key in (
                "amount_wan", "quantity", "indicator_yuan", "indicator",
                "price_per_unit", "capacity", "headcount", "avg_wage_yuan",
                "original_wan", "original_value_wan", "dep_years", "depreciation_years",
                "amort_years", "amortization_years",
            ):
                if row.get(key) not in (None, ""):
                    _add(f"{domain}[item_id={ident}].{key}", row.get(key), unit=row.get("unit"))
        return leaves
    if domain == "cost_items":
        for name, raw in _record(value).items():
            _add(f"cost_items[{name}]", raw)
        return leaves
    if domain == "wc_turnover":
        row = _record(value)
        for key in (
            "receivable", "cash", "payable", "inventory",
            "short_term_loan_wan", "self_funded_wan",
        ):
            if row.get(key) not in (None, ""):
                _add(f"wc_turnover.{key}", row.get(key))
        inv = _record(row.get("inventory_detail"))
        for key in ("raw", "fuel", "wip", "finished"):
            component = _record(inv.get(key))
            for field, unit in (("base_wan", "万元"), ("days", "天")):
                if component.get(field) not in (None, ""):
                    _add(
                        f"wc_turnover.inventory_detail.{key}.{field}",
                        component.get(field),
                        unit=unit,
                    )
            if component.get("base_source") not in (None, ""):
                _add(
                    f"wc_turnover.inventory_detail.{key}.base_source",
                    component.get("base_source"),
                )
        return leaves
    if domain == "funding_plan":
        funding = _record(value)
        for row in _rows(funding.get("annual_schedule") or funding.get("schedule")):
            period = row.get("year") or row.get("period")
            for key in (
                "construction_investment_wan", "construction_interest_wan",
                "capital_own_wan", "loan_wan", "gov_subsidy_wan",
                "working_capital_wan",
            ):
                if row.get(key) not in (None, ""):
                    _add(f"funding_plan[year={period}].{key}", row.get(key), period=period)
        return leaves
    if domain == "debt_schedule":
        debt = _record(value)
        for key in ("loan_rate", "loan_years"):
            if debt.get(key) not in (None, ""):
                _add(f"debt_schedule.{key}", debt.get(key))
        for row in _rows(debt.get("draws") or debt.get("schedule")):
            period = row.get("year") or row.get("period")
            draw = row.get("draw_wan") or row.get("loan_wan")
            if draw not in (None, ""):
                _add(f"debt_schedule.draws[year={period}].draw_wan", draw, period=period)
        for row in _rows(debt.get("principal_schedule")):
            period = row.get("year") or row.get("period")
            _add(
                f"debt_schedule.principal_schedule[year={period}].principal_wan",
                row.get("principal_wan"), period=period,
            )
        for row in _rows(debt.get("reference_interest_schedule")):
            period = row.get("year") or row.get("period")
            _add(
                f"debt_schedule.reference_interest_schedule[year={period}].interest_wan",
                row.get("interest_wan"), period=period,
            )
        for idx, row in enumerate(_rows(debt.get("debt_repay_sources") or debt.get("repay_sources"))):
            ident = _stable_item_id("debt_repay_sources", row, idx)
            for key in ("share", "annual_wan"):
                if row.get(key) not in (None, ""):
                    _add(f"debt_schedule.repay_sources[item_id={ident}].{key}", row.get(key))
        if debt.get("repayment_allocation_method") not in (None, ""):
            _add("debt_schedule.repayment_allocation_method", debt.get("repayment_allocation_method"))
        return leaves
    if domain == "distribution_policy":
        for key, raw in _record(value).items():
            _add(f"distribution_policy.{key}", raw)
        return leaves
    if domain == "cost_behavior":
        behavior = _record(value)
        items = _record(behavior.get("items") or behavior)
        for name, raw_rule in items.items():
            if name == "confirmed":
                continue
            rule = {"type": raw_rule} if isinstance(raw_rule, str) else _record(raw_rule)
            for key in ("type", "variable_share", "driver_fact_path"):
                if rule.get(key) not in (None, ""):
                    _add(f"cost_behavior.items[{name}].{key}", rule.get(key))
        _add("cost_behavior.confirmed", behavior.get("confirmed"))
        return leaves
    if domain == "tax_component_policy":
        for key, raw in _record(value).items():
            _add(f"tax_component_policy.{key}", raw)
        return leaves
    return leaves


def _domain_numeric_anchors(domain: str, value: Any) -> list[float]:
    """Backward-compatible numeric anchor list (values only)."""
    return [
        float(leaf["value"])
        for leaf in _domain_fact_leaves(domain, value)
        if isinstance(leaf.get("value"), (int, float)) and not isinstance(leaf.get("value"), bool)
    ]


def _values_close(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) is bool(right)
    try:
        left_n = float(left)
        right_n = float(right)
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()
    scale = max(abs(left_n), abs(right_n), 1.0)
    return abs(left_n - right_n) <= max(0.01, scale * 0.005)


def _evidence_supports_domain(
    domain: str,
    domain_value: Any,
    evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Require reviewed evidence to bind EVERY numeric fact leaf.

    Rules (fail-closed for formal):
    - each fact leaf must be bound by an approved evidence row, preferably by
      matching fact_path; otherwise by reviewed_value == leaf value (+unit/period).
    - every reviewed evidence value must match some leaf (no orphan numbers).
    - 100% leaf coverage required; partial coverage only supports reference.
    """
    leaves = _domain_fact_leaves(domain, domain_value)
    if not evidence_rows:
        return {
            "ok": False,
            "leaf_count": len(leaves),
            "matched_leaves": 0,
            "unmatched_leaves": [leaf["fact_path"] for leaf in leaves],
            "missing_fact_paths": [leaf["fact_path"] for leaf in leaves],
            "detail": "missing evidence rows",
        }
    if not all(bool(row.get("binding_ok")) for row in evidence_rows):
        return {
            "ok": False,
            "leaf_count": len(leaves),
            "matched_leaves": 0,
            "unmatched_leaves": [leaf["fact_path"] for leaf in leaves],
            "missing_fact_paths": [leaf["fact_path"] for leaf in leaves],
            "detail": "binding_ok incomplete",
        }
    if not leaves:
        return {
            "ok": True,
            "leaf_count": 0,
            "matched_leaves": 0,
            "unmatched_leaves": [],
            "missing_fact_paths": [],
            "detail": "no numeric leaves; binding presence only",
        }

    # Index evidence by fact_path and by numeric value.
    evidence_by_path: dict[str, dict[str, Any]] = {}
    evidence_values: list[tuple[Any, dict[str, Any]]] = []
    orphan_values: list[Any] = []
    for row in evidence_rows:
        fact_path = str(row.get("fact_path") or "").strip()
        raw = None
        for key in (
            "reviewed_value", "value", "amount", "amount_wan", "numeric_value", "cell_value",
        ):
            if row.get(key) not in (None, ""):
                raw = row.get(key)
                break
        number = raw if raw not in (None, "") else None
        if fact_path:
            evidence_by_path[fact_path] = row
        if number is not None:
            evidence_values.append((number, row))

    leaf_by_path = {str(leaf["fact_path"]): leaf for leaf in leaves}
    matched_paths: list[str] = []
    unmatched_paths: list[str] = []
    value_mismatches: list[dict[str, Any]] = []
    unit_period_mismatches: list[dict[str, Any]] = []
    used_value_indices: set[int] = set()
    for leaf in leaves:
        path = leaf["fact_path"]
        target = leaf["value"]
        # Prefer fact_path binding with value confirmation.
        row = evidence_by_path.get(path)
        bound = False
        if row is not None:
            bound_val: Any = None
            for key in ("reviewed_value", "value", "amount", "amount_wan", "numeric_value"):
                if row.get(key) not in (None, ""):
                    bound_val = row.get(key)
                    break
            # Formal requires exact fact_path identity, value, unit and period.
            bound = bound_val is not None and _values_close(bound_val, target)
            expected_unit = leaf.get("unit")
            expected_period = leaf.get("period")
            actual_unit = row.get("unit")
            actual_period = row.get("period") or row.get("year")
            if bound and expected_unit not in (None, "") and str(actual_unit or "") != str(expected_unit):
                unit_period_mismatches.append({"fact_path": path, "expected_unit": expected_unit, "actual_unit": actual_unit})
                bound = False
            if bound and expected_period not in (None, "") and str(actual_period or "") != str(expected_period):
                unit_period_mismatches.append({"fact_path": path, "expected_period": expected_period, "actual_period": actual_period})
                bound = False
            if bound_val is not None and not _values_close(bound_val, target):
                value_mismatches.append({"fact_path": path, "expected": target, "actual": bound_val})
            if bound:
                for idx, (_candidate, candidate_row) in enumerate(evidence_values):
                    if candidate_row is row:
                        used_value_indices.add(idx)
                        break
        if bound:
            matched_paths.append(path)
        else:
            unmatched_paths.append(path)

    # Orphan reviewed values that matched no leaf at all.
    for idx, (candidate, _r) in enumerate(evidence_values):
        if not any(_values_close(candidate, leaf["value"]) for leaf in leaves):
            orphan_values.append(candidate)

    orphan_paths = [
        str(row.get("fact_path") or "") for row in evidence_rows
        if str(row.get("fact_path") or "").strip() and str(row.get("fact_path") or "") not in leaf_by_path
    ]
    fully_covered = not unmatched_paths and not orphan_values and not unit_period_mismatches and not orphan_paths
    # 100% leaf coverage → formal-eligible. Partial → reference only.
    ok = fully_covered
    return {
        "ok": ok,
        "leaf_count": len(leaves),
        "matched_leaves": len(matched_paths),
        "unmatched_leaves": unmatched_paths,
        "missing_fact_paths": unmatched_paths,
        "orphan_evidence_values": orphan_values,
        "orphan_fact_paths": orphan_paths,
        "value_mismatches": value_mismatches,
        "unit_period_mismatches": unit_period_mismatches,
        "coverage": round(len(matched_paths) / len(leaves), 4) if leaves else 1.0,
        "detail": (
            "all fact leaves bound to approved evidence"
            if ok else
            f"unbound leaves={unmatched_paths[:4]}; orphans={orphan_values[:4]}"
        ),
    }


def _migrate_v0_pack(raw_pack: dict[str, Any]) -> dict[str, Any]:
    """Normalize a v0 pack into an explicit v1 draft without confirming it."""
    raw = copy.deepcopy(raw_pack)
    raw["version"] = VERSION
    raw["confirmation_status"] = "draft"
    domains = _record(raw.get("domains"))
    funding = _record(domains.get("funding_plan"))
    schedule = []
    for row in _rows(funding.get("annual_schedule") or funding.get("schedule")):
        item = dict(row)
        if "construction_outlay_wan" in item and "construction_investment_wan" not in item:
            item["legacy_construction_outlay_wan"] = item.pop("construction_outlay_wan")
        if "interest_wan" in item and "construction_interest_wan" not in item:
            item["legacy_interest_wan"] = item.pop("interest_wan")
        schedule.append(item)
    if schedule:
        funding["annual_schedule"] = schedule
        domains["funding_plan"] = funding
    debt = _record(domains.get("debt_schedule"))
    draws = []
    principal = []
    interest = []
    for row in _rows(debt.get("draws") or debt.get("schedule")):
        item = {"year": row.get("year") or row.get("period"), "draw_wan": row.get("draw_wan") or row.get("loan_wan")}
        draws.append(item)
        if row.get("principal_wan") is not None or row.get("principal") is not None:
            principal.append({"year": item["year"], "principal_wan": row.get("principal_wan") or row.get("principal")})
        if row.get("interest_wan") is not None or row.get("interest") is not None:
            interest.append({"year": item["year"], "interest_wan": row.get("interest_wan") or row.get("interest")})
    if draws:
        debt["draws"] = draws
    if principal:
        debt["principal_schedule"] = principal
    if interest:
        debt["reference_interest_schedule"] = interest
    domains["debt_schedule"] = debt
    raw["domains"] = domains
    raw["evidence"] = []
    raw.pop("fact_pack_hash", None)
    raw.pop("seal_mac", None)
    raw.pop("seal_id", None)
    return raw


def build_fact_pack_snapshot(
    raw_pack: Any,
    *,
    workspace_id: str,
    confirm: bool = False,
    evidence_resolver: EvidenceResolver | None = None,
) -> dict[str, Any]:
    raw = _record(raw_pack)
    legacy_migration = str(raw.get("version") or "") == LEGACY_VERSION
    if legacy_migration:
        # v0 may be read and normalized for editing, but it can never be
        # confirmed in-place.  The caller must save the v1 draft and confirm
        # again so the split funding semantics and policy domains are explicit.
        raw = _migrate_v0_pack(raw)
        confirm = False
    raw_domains = _record(raw.get("domains"))
    domains: dict[str, Any] = {}
    for key in DOMAIN_KEYS:
        if key in {"construction_items", "products", "staff_detail", "asset_classes", "amort_bases"}:
            domains[key] = _rows_with_item_ids(key, raw_domains.get(key))
        else:
            domains[key] = _record(raw_domains.get(key))
    debt_domain = _record(domains.get("debt_schedule"))
    if debt_domain:
        repay_key = "debt_repay_sources" if "debt_repay_sources" in debt_domain else "repay_sources"
        if repay_key in debt_domain:
            debt_domain[repay_key] = _rows_with_item_ids(
                "debt_repay_sources", debt_domain.get(repay_key),
            )
        domains["debt_schedule"] = debt_domain
    # Policy domains are not depth-gated leaf domains, but must survive sealing.
    for policy_key in ("distribution_policy", "cost_behavior"):
        if isinstance(raw_domains.get(policy_key), dict):
            domains[policy_key] = _record(raw_domains.get(policy_key))

    depth = assess_domain_depth(domains)
    resolver = evidence_resolver
    if resolver is None:
        from lvke_mcp.adapters.source_files_repository import (
            resolve_authoritative_evidence_binding,
        )

        resolver = resolve_authoritative_evidence_binding

    evidence: list[dict[str, Any]] = []
    for client_row in raw.get("evidence") or []:
        if not isinstance(client_row, dict):
            continue
        domain = str(client_row.get("domain") or "").strip()
        if domain not in DOMAIN_KEYS:
            continue
        source_id = str(client_row.get("source_id") or client_row.get("file_id") or "").strip()
        evidence_id = str(client_row.get("evidence_id") or "").strip()
        locator = str(client_row.get("locator") or client_row.get("page_or_cell") or "").strip()
        fact_path = str(client_row.get("fact_path") or "").strip()
        binding = resolver(
            workspace_id,
            source_id=source_id,
            evidence_id=evidence_id,
            locator=locator,
        )
        # Client may supply claimed numeric value only as candidate; authoritative
        # reviewed_value from resolver always wins when present.
        claimed = client_row.get("claimed_value")
        if claimed is None:
            claimed = client_row.get("value")
        if claimed is None:
            claimed = client_row.get("amount_wan")
        row_out = {
            "domain": domain,
            "fact_path": fact_path,
            **binding,
            "client_reported": {
                "evidence_grade": str(client_row.get("evidence_grade") or client_row.get("grade") or ""),
                "review_status": str(client_row.get("review_status") or client_row.get("status") or ""),
                "claimed_value": claimed,
                "unit": client_row.get("unit"),
                "period": client_row.get("period") or client_row.get("year"),
            },
        }
        if not row_out.get("fact_path") and fact_path:
            row_out["fact_path"] = fact_path
        if row_out.get("reviewed_value") in (None, "") and claimed not in (None, ""):
            # Tests/resolvers without reviewed_value may still provide a claimed
            # numeric; keep it only as non-authoritative candidate for matching
            # when binding_ok and resolver explicitly allows it.
            if binding.get("binding_ok") and binding.get("allow_claimed_value"):
                row_out["reviewed_value"] = claimed
        row_out["normalized_value"] = row_out.get("reviewed_value")
        row_out["unit"] = client_row.get("unit")
        row_out["period"] = client_row.get("period") or client_row.get("year")
        evidence.append(row_out)

    binding_by_domain: dict[str, dict[str, Any]] = {}
    for domain in DOMAIN_KEYS:
        rows = [row for row in evidence if row.get("domain") == domain]
        value_match = _evidence_supports_domain(domain, domains.get(domain), rows)
        binding_ok = bool(rows) and all(bool(row.get("binding_ok")) for row in rows) and bool(
            value_match.get("ok")
        )
        binding_by_domain[domain] = {
            "count": len(rows),
            "ok_count": sum(1 for row in rows if row.get("binding_ok")),
            "ok": binding_ok,
            "value_match": value_match,
        }
    binding_passed = sum(1 for item in binding_by_domain.values() if item.get("ok"))
    source_coverage = round(binding_passed / len(DOMAIN_KEYS), 4)

    confirmed = bool(confirm)
    if not confirmed:
        ceiling = "summary"
    elif not depth.get("ok"):
        ceiling = "summary"
    elif source_coverage >= 0.999:
        ceiling = "formal_candidate"
    else:
        ceiling = "reference"

    missing = [f"domain:{key}" for key in depth.get("missing_domains") or []]
    missing.extend(
        f"evidence:{key}"
        for key, item in binding_by_domain.items()
        if depth["by_domain"][key].get("ok") and not item.get("ok")
    )
    result: dict[str, Any] = {
        "version": VERSION,
        "project_id": str(raw.get("project_id") or workspace_id),
        "seal_workspace_id": str(workspace_id),
        "valuation_date": str(raw.get("valuation_date") or ""),
        "confirmation_status": "confirmed" if confirmed else "draft",
        "domains": domains,
        "evidence": evidence,
        "domain_coverage": depth.get("coverage"),
        "source_coverage": source_coverage,
        "depth_assessment": depth,
        "binding_assessment": {
            "ok": source_coverage >= 0.999,
            "coverage": source_coverage,
            "by_domain": binding_by_domain,
        },
        "missing": missing,
        "delivery_grade_ceiling": ceiling,
        "ai_role": "candidate_extraction_only",
    }
    if legacy_migration:
        result["migration_required"] = True
        result["missing"].append("finance_fact_pack.v1_confirmation_required")
    if confirmed:
        seal_id = f"seal_{secrets.token_hex(8)}"
        result.update({
            "sealed_at": _now_iso(),
            "seal_version": SEAL_VERSION,
            "seal_id": seal_id,
            "seal_workspace_id": str(workspace_id),
        })
        content_hash = compute_fact_pack_hash(result)
        result["fact_pack_hash"] = content_hash
        seal_mac = compute_fact_pack_mac(
            result, workspace_id=str(workspace_id), content_hash=content_hash,
        )
        result["seal_mac"] = seal_mac
        _record_seal_ledger(
            str(workspace_id),
            seal_id=seal_id,
            content_hash=content_hash,
            seal_mac=seal_mac,
            ceiling=ceiling,
        )
    return result


def _sort_year_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Sort rows by year and report duplicate/missing year issues."""
    issues: list[str] = []
    if not rows:
        return [], issues
    decorated: list[tuple[int, int, dict[str, Any]]] = []
    years: list[int] = []
    for index, row in enumerate(rows):
        year_raw = row.get("year") if row.get("year") is not None else row.get("period")
        if year_raw in (None, ""):
            # Preserve relative order for rows without year by using index+1.
            year = index + 1
        else:
            try:
                year = int(year_raw)
            except (TypeError, ValueError):
                issues.append(f"invalid year value: {year_raw!r}")
                year = index + 1
        years.append(year)
        decorated.append((year, index, row))
    if len(set(years)) != len(years):
        issues.append("duplicate year values in schedule")
    if years != sorted(years):
        issues.append(f"schedule years not sorted: {years}")
    decorated.sort(key=lambda item: (item[0], item[1]))
    sorted_rows = []
    for year, _index, row in decorated:
        item = dict(row)
        item["year"] = year
        sorted_rows.append(item)
    return sorted_rows, issues


def project_confirmed_fact_pack(
    finance_inputs: dict[str, Any],
    spec: dict[str, Any] | None,
    *,
    expected_workspace_id: str,
    expected_build_years: int | None = None,
    expected_calc_years: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    """Project a valid sealed fact pack into deterministic engine inputs.

    ``expected_workspace_id`` is required for formal-safe callers.  When provided,
    the pack's seal_workspace_id/project_id must match; pack-self-reported workspace
    is never trusted alone.
    """
    out = dict(finance_inputs or {})
    pack = out.get("finance_fact_pack")
    claimed_ws = str(
        (pack or {}).get("seal_workspace_id")
        or (pack or {}).get("project_id")
        or ""
    ).strip()
    expected = str(expected_workspace_id or "").strip()
    if not expected:
        return out, spec, {
            "applied": False,
            "issues": ["project_confirmed_fact_pack 缺少 expected_workspace_id"],
            "workspace_bound": False,
        }
    if claimed_ws != expected:
        return out, spec, {
            "applied": False,
            "issues": [f"fact_pack seal workspace {claimed_ws} != expected {expected}"],
            "workspace_bound": False,
        }
    workspace_id = expected
    seal = verify_fact_pack_seal(pack, workspace_id=workspace_id or None)
    if not seal.get("ok"):
        return out, spec, {
            "applied": False,
            "issues": seal.get("issues") or [],
            "workspace_bound": bool(expected),
        }
    ceiling = str((pack or {}).get("delivery_grade_ceiling") or "summary")
    depth = (pack or {}).get("depth_assessment") if isinstance(pack, dict) else None
    depth_ok = bool((depth or {}).get("ok")) if isinstance(depth, dict) else False
    domains = _record((pack or {}).get("domains"))
    build_years = int(expected_build_years or out.get("build_years") or 0)
    calc_years = int(expected_calc_years or out.get("calc_period_years") or out.get("calc_years") or 0)

    breakdown = _record(out.get("invest_breakdown"))
    breakdown["construction_items"] = _rows(domains.get("construction_items"))
    out["invest_breakdown"] = breakdown
    out["cost_items"] = _record(domains.get("cost_items"))
    out["staff_detail"] = _rows(domains.get("staff_detail"))

    staff_total = sum(
        float(row.get("headcount") or 0) * float(row.get("avg_wage_yuan") or 0) / 10_000
        for row in out["staff_detail"]
        if _positive(row.get("headcount")) and _positive(row.get("avg_wage_yuan"))
    )
    if staff_total > 0:
        out["wage_wan"] = round(staff_total, 6)

    out["depreciation_classes"] = [
        {
            **row,
            "original_value_wan": row.get("original_value_wan") or row.get("original_wan"),
            "depreciation_years": row.get("depreciation_years") or row.get("dep_years"),
        }
        for row in _rows(domains.get("asset_classes"))
    ]
    out["wc_turnover"] = _record(domains.get("wc_turnover"))

    funding = _record(domains.get("funding_plan"))
    funding_rows, funding_year_issues = _sort_year_rows(
        _rows(funding.get("annual_schedule") or funding.get("schedule"))
    )
    if build_years:
        funding_years = [int(row.get("year") or 0) for row in funding_rows]
        if funding_years != list(range(1, build_years + 1)):
            funding_year_issues.append(
                f"funding_plan 年份必须覆盖 1..{build_years}: {funding_years}"
            )
    if funding_rows:
        # v1 uses atomic construction investment and construction interest.
        out["construction_outlay_by_year"] = [
            round(
                float(row.get("construction_investment_wan") or 0)
                + float(row.get("construction_interest_wan") or 0),
                6,
            )
            for row in funding_rows
        ]
        out["construction_investment_by_year"] = [
            round(float(row.get("construction_investment_wan") or 0), 6)
            for row in funding_rows
        ]
        out["construction_interest_by_year"] = [
            round(float(row.get("construction_interest_wan") or 0), 6)
            for row in funding_rows
        ]
        out["working_capital_by_year"] = [
            round(float(row.get("working_capital_wan") or row.get("wc_wan") or 0), 6)
            for row in funding_rows
        ]
        out["equity_inject_by_year"] = [
            round(float(row.get("capital_own_wan") or 0), 6)
            for row in funding_rows
        ]
        out["funding_annual_schedule"] = [
            {
                "year": int(row.get("year") or index + 1),
                "construction_outlay_wan": round(
                    float(row.get("construction_investment_wan") or 0)
                    + float(row.get("construction_interest_wan") or 0), 6,
                ),
                "construction_investment_wan": round(
                    float(row.get("construction_investment_wan") or 0), 6,
                ),
                "capital_own_wan": round(float(row.get("capital_own_wan") or 0), 6),
                "loan_wan": round(float(row.get("loan_wan") or 0), 6),
                "gov_subsidy_wan": round(float(row.get("gov_subsidy_wan") or 0), 6),
                "construction_interest_wan": round(
                    float(row.get("construction_interest_wan") or 0), 6,
                ),
                "working_capital_wan": round(
                    float(row.get("working_capital_wan") or row.get("wc_wan") or 0), 6,
                ),
            }
            for index, row in enumerate(funding_rows)
        ]
        out["loan_draw_by_year"] = [
            round(float(row.get("loan_wan") or 0), 6) for row in funding_rows
        ]
        # The confirmed funding plan is authoritative for the investment
        # breakdown as well; do not let scalar finance_inputs resurrect a
        # conflicting working-capital amount.
        breakdown = _record(out.get("invest_breakdown"))
        breakdown["construction_wan"] = round(
            sum(float(row.get("construction_investment_wan") or 0) for row in funding_rows), 6,
        )
        breakdown["interest_wan"] = round(
            sum(float(row.get("construction_interest_wan") or 0) for row in funding_rows), 6,
        )
        breakdown["working_capital_wan"] = round(
            sum(float(row.get("working_capital_wan") or 0) for row in funding_rows), 6,
        )
        out["invest_breakdown"] = breakdown

    debt = _record(domains.get("debt_schedule"))
    debt_draws, debt_year_issues = _sort_year_rows(_rows(debt.get("draws")))
    if build_years:
        bad_draw_years = [int(row.get("year") or 0) for row in debt_draws if not 1 <= int(row.get("year") or 0) <= build_years]
        if bad_draw_years:
            debt_year_issues.append(f"debt draws 越出建设期 1..{build_years}: {bad_draw_years}")
    if debt_draws:
        out["loan_draw_by_year"] = [
            round(float(row.get("draw_wan") or row.get("loan_wan") or 0), 6)
            for row in debt_draws
        ]
        principal_rows, principal_year_issues = _sort_year_rows(
            _rows(debt.get("principal_schedule"))
        )
        interest_rows, interest_year_issues = _sort_year_rows(
            _rows(debt.get("reference_interest_schedule"))
        )
        if build_years and calc_years:
            op_range = range(build_years + 1, calc_years + 1)
            for label, rows in (
                ("principal_schedule", principal_rows),
                ("reference_interest_schedule", interest_rows),
            ):
                years = [int(row.get("year") or 0) for row in rows]
                if years and (years != list(range(years[0], years[-1] + 1)) or any(year not in op_range for year in years)):
                    debt_year_issues.append(
                        f"{label} 年份必须连续且位于 {build_years + 1}..{calc_years}: {years}"
                    )
        debt_year_issues.extend(principal_year_issues)
        debt_year_issues.extend(interest_year_issues)
        principal_source_rows = [
            row for row in principal_rows
            if not build_years or int(row.get("year") or 0) > build_years
        ]
        interest_source_rows = [
            row for row in interest_rows
            if not build_years or int(row.get("year") or 0) > build_years
        ]
        principal_plan = [round(float(row.get("principal_wan") or 0), 6) for row in principal_source_rows]
        interest_plan = [round(float(row.get("interest_wan") or 0), 6) for row in interest_source_rows]
        if principal_plan:
            out["loan_principal_by_year"] = principal_plan
            out["principal_schedule_by_year"] = principal_plan
            out["principal_schedule_source"] = "fact_pack.debt_schedule.principal_schedule"
        if interest_plan:
            out["interest_schedule_by_year_reference"] = interest_plan
            out["interest_schedule_reference_only"] = True
    for source_key, target_key in (
        ("loan_rate", "loan_rate"),
        ("loan_years", "loan_years"),
        ("grace_years", "loan_grace_years"),
        ("repay_method", "loan_repay_method"),
    ):
        if debt.get(source_key) not in (None, ""):
            out[target_key] = debt[source_key]
    out["debt_repay_sources"] = _rows(
        debt.get("debt_repay_sources") or debt.get("repay_sources")
    )

    # Funding loan draws vs debt draws consistency.
    funding_loans = out.get("funding_annual_schedule") or []
    consistency_issues = list(funding_year_issues) + list(debt_year_issues)
    for label, rows in (("funding_plan", funding_loans), ("debt_draws", debt_draws)):
        years = [int(row.get("year") or 0) for row in rows if isinstance(row, dict)]
        if years and years != list(range(min(years), max(years) + 1)):
            consistency_issues.append(f"{label} 年份不连续: {years}")
    if funding_loans and debt_draws:
        funding_loan_by_year = {
            int(row.get("year") or 0): round(float(row.get("loan_wan") or 0), 6)
            for row in funding_loans if isinstance(row, dict)
        }
        debt_loan_by_year = {
            int(row.get("year") or 0): round(float(row.get("draw_wan") or row.get("loan_wan") or 0), 6)
            for row in debt_draws
        }
        for year in sorted(set(funding_loan_by_year) | set(debt_loan_by_year)):
            if abs(float(debt_loan_by_year.get(year, 0)) - float(funding_loan_by_year.get(year, 0))) > 0.05:
                consistency_issues.append(
                    f"funding loan_wan year[{year}]={funding_loan_by_year.get(year, 0)} "
                    f"!= debt draw[{year}]={debt_loan_by_year.get(year, 0)}"
                )
                break

    amort = _rows(domains.get("amort_bases"))
    out["amort_bases"] = amort
    amort_total = sum(
        float(row.get("original_wan") or row.get("original_value_wan") or 0)
        for row in amort
    )
    if amort_total > 0:
        out["intangible_assets_wan"] = round(amort_total, 6)
        lives = [
            int(float(row.get("amort_years") or row.get("amortization_years") or 0))
            for row in amort
            if _positive(row.get("amort_years") or row.get("amortization_years"))
        ]
        if lives:
            out["amortization_years"] = max(lives)

    # distribution_policy domain → explicit zeros vs missing.
    dist = _record(domains.get("distribution_policy"))
    if dist:
        out["distribution_policy"] = dist
        if dist.get("statutory_reserve_rate") is not None:
            out["statutory_reserve_rate"] = dist.get("statutory_reserve_rate")
        if dist.get("arbitrary_reserve_rate") is not None:
            out["arbitrary_reserve_rate"] = dist.get("arbitrary_reserve_rate")
        if dist.get("investor_distribution_rate") is not None:
            out["investor_distribution_rate"] = dist.get("investor_distribution_rate")
        if dist.get("arbitrary_reserve_confirmed_zero") is True:
            out["arbitrary_reserve_confirmed_zero"] = True
        if dist.get("investor_distribution_confirmed_zero") is True:
            out["investor_distribution_confirmed_zero"] = True

    behavior = _record(domains.get("cost_behavior"))
    if behavior:
        out["cost_behavior"] = behavior
        out["cost_behavior_confirmed"] = behavior.get("confirmed") is True

    tax_policy = _record(domains.get("tax_component_policy"))
    if tax_policy:
        out["tax_component_policy"] = tax_policy
        out["tax_component_policy_confirmed"] = tax_policy.get("confirmed") is True
        for source_key, target_key in (
            ("vat_output_rate", "vat_rate"),
            ("vat_input_rate", "vat_input_rate"),
            ("income_tax_rate", "income_tax_rate"),
        ):
            if tax_policy.get(source_key) is not None:
                out[target_key] = tax_policy[source_key]
        if str(tax_policy.get("surtax_base") or "") in {
            "vat_payable", "vat_and_consumption_tax_payable",
        }:
            out["surtax_on_vat"] = True
            for key in (
                "urban_maintenance_rate",
                "education_surcharge_rate",
                "local_education_surcharge_rate",
            ):
                if tax_policy.get(key) is not None:
                    out[key] = tax_policy[key]
            out["surtax_vat_rate"] = round(sum(float(tax_policy.get(key) or 0.0) for key in (
                "urban_maintenance_rate",
                "education_surcharge_rate",
                "local_education_surcharge_rate",
            )), 8)

    resolved_spec = copy.deepcopy(spec) if isinstance(spec, dict) else {}
    revenue = _record(resolved_spec.get("revenue"))
    products = _rows(domains.get("products"))
    if products:
        revenue.update({"model": "product_sales", "products": products})
        resolved_spec["revenue"] = revenue
        resolved_spec.setdefault("version", "finance_spec.v1")
        resolved_spec["source_hint"] = "confirmed_fact_pack"
    return out, (resolved_spec or spec), {
        "applied": True,
        "fact_pack_hash": pack.get("fact_pack_hash"),
        "delivery_grade_ceiling": ceiling,
        "depth_ok": depth_ok,
        "formal_candidate": ceiling == "formal_candidate" and depth_ok and not consistency_issues,
        "workspace_bound": True,
        "expected_workspace_id": expected,
        "schedule_issues": consistency_issues,
    }
