"""pack 快照构造、v0 迁移与确认后投影。"""

from __future__ import annotations

import copy
import secrets
from typing import Any

from lvke_mcp.runtime.evidence_qualification import project_fact_may_be_certified

from .base import (
    DOMAIN_KEYS,
    EvidenceResolver,
    LEGACY_VERSION,
    SEAL_VERSION,
    VERSION,
    _now_iso,
    _record,
    _rows,
    compute_fact_pack_hash,
)

from .completeness import (
    _positive,
    _rows_with_item_ids,
)

from .depth import (
    assess_domain_depth,
)

from .evidence import (
    _evidence_supports_domain,
)

from .seal import (
    _record_seal_ledger,
    compute_fact_pack_mac,
    verify_fact_pack_seal,
)


_EVIDENCE_ROW_FIELDS = frozenset({
    # 事实定位
    "domain", "fact_path",
    # 来源标识（三选一，语义等价）
    "source_id", "source_snapshot_id", "file_id",
    # 证据定位
    "evidence_id", "locator", "page_or_cell",
    # 调用方申报值（非权威，仅作候选）
    "claimed_value", "value", "amount_wan", "unit", "period", "year",
    # 调用方申报的评级/复核状态（非权威）
    "evidence_grade", "grade", "review_status", "status",
})


def _domain_from_fact_path(fact_path: str) -> str:
    """Derive the owning domain from a fact_path's leading segment.

    fact_path 形如 ``construction_items[0].quantity`` 或
    ``debt_schedule.draws[year=1].draw_wan``，首段即域名。仅在调用方
    未显式给出 domain 时用于补全，推断不出时返回空串并由调用点报错。
    """
    head = str(fact_path or "").strip().split(".", 1)[0].split("[", 1)[0].strip()
    return head if head in DOMAIN_KEYS else ""


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
    evidence_policy = str(raw.get("evidence_policy") or "formal_evidence")
    reconstruction_records = [
        dict(row) for row in raw.get("reconstruction_records") or []
        if isinstance(row, dict)
    ]
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
    rejected_evidence: list[dict[str, Any]] = []
    for index, client_row in enumerate(raw.get("evidence") or []):
        if not isinstance(client_row, dict):
            rejected_evidence.append({
                "index": index,
                "reason": "evidence 行必须是对象",
                "field": "evidence",
            })
            continue
        fact_path = str(client_row.get("fact_path") or "").strip()
        domain = str(client_row.get("domain") or "").strip()
        if not domain and fact_path:
            # 只给 fact_path 的行此前被静默丢弃。fact_path 的首段就是域名，
            # 能推断则推断，推断不出再报错，不再无声吞掉整行。
            domain = _domain_from_fact_path(fact_path)
        if domain not in DOMAIN_KEYS:
            rejected_evidence.append({
                "index": index,
                "field": "domain",
                "fact_path": fact_path,
                "value": str(client_row.get("domain") or ""),
                "reason": (
                    "domain 缺失且无法从 fact_path 推断"
                    if not str(client_row.get("domain") or "").strip()
                    else "domain 不在受支持的域列表中"
                ),
                "allowed": list(DOMAIN_KEYS),
            })
            continue
        # source_snapshot_id 是 data-acquisition 侧的自然命名，此前既不被读取
        # 也不报错，整行会以"缺 source_id"的形式静默失败。这里显式接受为别名。
        source_id = str(
            client_row.get("source_id")
            or client_row.get("source_snapshot_id")
            or client_row.get("file_id")
            or ""
        ).strip()
        evidence_id = str(client_row.get("evidence_id") or "").strip()
        locator = str(client_row.get("locator") or client_row.get("page_or_cell") or "").strip()
        unknown_fields = sorted(set(client_row) - _EVIDENCE_ROW_FIELDS)
        if unknown_fields:
            rejected_evidence.append({
                "index": index,
                "field": unknown_fields[0],
                "fact_path": fact_path,
                "unknown_fields": unknown_fields,
                "reason": "evidence 行包含未知字段；请改用受支持字段，不做静默忽略",
                "allowed": sorted(_EVIDENCE_ROW_FIELDS),
            })
            continue
        if not source_id:
            rejected_evidence.append({
                "index": index,
                "field": "source_id",
                "fact_path": fact_path,
                "reason": "缺少来源标识；可用 source_id、source_snapshot_id 或 file_id",
            })
            continue
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
    missing.extend(
        f"evidence_row[{item['index']}]:{item['field']}:{item['reason']}"
        for item in rejected_evidence
    )
    result: dict[str, Any] = {
        "version": VERSION,
        "project_id": str(raw.get("project_id") or workspace_id),
        "seal_workspace_id": str(workspace_id),
        "valuation_date": str(raw.get("valuation_date") or ""),
        "confirmation_status": "confirmed" if confirmed else "draft",
        "domains": domains,
        "evidence": evidence,
        "rejected_evidence": rejected_evidence,
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
        "evidence_policy": evidence_policy,
        # 证据资格由服务端判定，不采信 raw 自报：调用方声明 certified=true 不构成
        # 认证。缺省也必须是 False —— 旧写法 (policy != "source_reconstructed")
        # 会让 controlled_assumption / technical_fixture 拿到 True。
        "project_fact_certified": project_fact_may_be_certified(
            evidence_policy,
            own_qualification_passed=(
                source_coverage >= 0.999 and not missing and bool(confirmed)
            ),
        ),
        "reconstruction_records": reconstruction_records,
        "reconstructed_source_ids": [
            str(row.get("reconstruction_id") or "")
            for row in reconstruction_records
            if str(row.get("reconstruction_id") or "")
        ],
        "unresolved_inputs": [str(item) for item in raw.get("unresolved_inputs") or []],
        "release_limitations": [str(item) for item in raw.get("release_limitations") or []],
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
        principal_plan = [round(float(row.get("principal_wan") or 0), 6) for row in principal_source_rows]
        if principal_plan:
            out["loan_principal_by_year"] = principal_plan
        # Reference interest remains in the sealed Fact Pack.  It must not be
        # promoted to an engine input because model interest is recalculated
        # from the confirmed draw/principal schedule and rate.
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
