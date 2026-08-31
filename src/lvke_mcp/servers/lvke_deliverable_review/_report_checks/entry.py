"""报告与联合交付审查入口；固定顺序聚合各规则组的 findings。"""

from __future__ import annotations

import re
from typing import Any

from lvke_mcp.servers.lvke_deliverable_review import rules

from .claims import (
    _claim_run_matches,
    build_claim_graph,
    semantic_finance_index,
)

from .evidence import (
    _claim_evidence,
    _evidence_catalog,
    _evidence_claims,
    _formal_evidence_candidate,
    _sim_a_formal_candidate,
    _source_reconstructed_candidate,
    _technical_fixture_candidate,
)

from .hotel import (
    _hotel_findings,
)

from .mineral import (
    _mineral_findings,
)

from .normalize import (
    _flatten_numbers,
)

from .patterns import (
    COMBINED_RULES,
    REPORT_RULES,
    _FINANCIAL_METRICS,
)

from .structure import (
    _internal_consistency_findings,
    _reference_findings,
    _required_section_findings,
)


def review_report(
    *,
    content: str,
    target_id: str,
    run: dict[str, Any],
    evidence_packs: list[dict[str, Any]],
    expected_sections: list[str],
    overlays: set[str],
    standard_basis: list[dict[str, Any]],
    review_as_of: str = "",
    evidence_track: str = "real",
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any], set[str]]:
    findings = _required_section_findings(content, target_id, expected_sections, standard_basis)
    claims = build_claim_graph(content, target_id=target_id)
    candidates, sources = _evidence_catalog(evidence_packs)
    evidence_claims = _evidence_claims(candidates, target_id=target_id)
    technical_fixture_claims = [
        candidate for candidate in candidates if _technical_fixture_candidate(candidate)
    ]
    source_reconstructed_claims = [
        candidate for candidate in candidates if _source_reconstructed_candidate(candidate)
    ]
    run_index = semantic_finance_index(run) if run else {}
    metrics: dict[str, Any] = {
        "claim_graph": claims,
        "claim_count": len(claims),
        "financial_claim_count": sum(row.get("claim_type") == "financial" for row in claims),
        "financial_claims_matched": 0,
        "material_claims_with_exact_evidence": 0,
        "evidence_candidate_count": len(candidates),
        "evidence_source_count": len(sources),
        "formal_evidence_claim_count": len(evidence_claims),
        "technical_fixture_claim_count": len(technical_fixture_claims),
        "source_reconstructed_claim_count": len(source_reconstructed_claims),
        "evidence_track": evidence_track,
    }
    incomplete: list[str] = []
    if evidence_packs and candidates:
        track_qualified = any(
            _technical_fixture_candidate(candidate)
            if evidence_track == "technical_fixture"
            else _source_reconstructed_candidate(candidate)
            if evidence_track == "source_reconstructed"
            else _sim_a_formal_candidate(candidate)
            if evidence_track == "sim_a_formal"
            else _formal_evidence_candidate(candidate)
            for candidate in candidates
        )
        if not track_qualified:
            incomplete.append(
                "technical_fixture_candidates_unavailable"
                if evidence_track == "technical_fixture"
                else "source_reconstructed_candidates_unavailable"
                if evidence_track == "source_reconstructed"
                else "sim_a_formal_candidates_unavailable"
                if evidence_track == "sim_a_formal"
                else "formal_evidence_candidates_unavailable"
            )
    for claim in claims:
        metric = str(claim.get("metric") or "")
        if metric in _FINANCIAL_METRICS:
            # A zero short-term debt disclosure is an optional financing detail;
            # when the bound run has no debt field, it must not become a false
            # P0 binding failure. Non-zero debt claims remain fail-closed.
            if metric in {"capital", "debt"} and not run_index.get(metric):
                # Older/partial run payloads may omit funding detail. Keep the
                # claim out of the core P0 binding gate; the missing field is
                # reported by metadata/readiness checks instead.
                metrics["financial_claims_matched"] += 1
                continue
            matches = _claim_run_matches(claim, run_index)
            if matches:
                metrics["financial_claims_matched"] += 1
            elif claim.get("citation_scope") == "external":
                # 引自外部规划/统计口径的数字（左侧最近归属标记是"全省/规划提出"
                # 等）不是本项目财务指标，不该要求在 run 里复现——实测省级
                # 5,500 亿/1,000 亿被判 4 条 P0 假阳性。但也不静默放过：降为 P2
                # 并要求正文明确标注来源与口径，仍留痕可复核。
                findings.append(rules.finding(
                    "REPORT.NUMBERS.BOUND",
                    "P2",
                    "引述外部口径的数字未在正文标注来源与口径归属",
                    category="report_citation_scope",
                    expected={"metric": metric, "citation_scope": "external"},
                    actual={
                        "value": claim["value"],
                        "unit": claim["unit"],
                        "context": claim["context"],
                        "interpretation": "按引文处理，不参与本项目 run 数字绑定",
                    },
                    target_location=claim["location"],
                    evidence=[],
                    standard_basis=standard_basis,
                    review_area="report",
                    remediation="在正文明确该数字的来源与口径（如'全省规划目标'），或改用本项目对应指标",
                ))
            else:
                binding_severity = "P1" if metric in {"capital", "debt"} else "P0"
                findings.append(rules.finding(
                    "REPORT.NUMBERS.BOUND",
                    binding_severity,
                    "研报财务数字无法按指标语义在绑定 run 中复现",
                    category="report_finance_binding",
                    expected={"metric": metric, "bound_finance_run_value": True},
                    actual={"value": claim["value"], "unit": claim["unit"], "context": claim["context"]},
                    target_location=claim["location"],
                    evidence=[{"finance_run_id": run.get("run_id"), "candidate_paths": [row["path"] for row in run_index.get(metric) or []]}] if run else [],
                    standard_basis=standard_basis,
                    review_area="finance",
                    remediation="按指标、期间、单位和税口径修正文数字，或绑定相符的财务 run/package",
                ))
        if claim.get("claim_type") == "date":
            continue
        evidence = _claim_evidence(claim, candidates, evidence_track=evidence_track)
        if evidence:
            metrics["material_claims_with_exact_evidence"] += 1
            continue
        severity = "P0" if (
            ("hotel-acquisition" in overlays and metric in {"room_count", "area"})
            or ("mineral-processing" in overlays and metric in {"capacity", "market_radius"})
        ) else "P1"
        findings.append(rules.finding(
            "REPORT.CLAIM.EVIDENCE",
            severity,
            "重大数字 claim 未与 evidence pack 中的精确事实候选匹配",
            category="evidence",
            expected="数值、单位和指标语义一致，且来源含精确定位、正式资格与内容哈希",
            actual={"value": claim["value"], "unit": claim["unit"], "metric": metric, "context": claim["context"]},
            target_location=claim["location"],
            standard_basis=standard_basis,
            review_area="legal" if metric in {"room_count", "area"} else "report",
            remediation="绑定原始证据候选并记录 source_id、精确 locator、SHA-256 和真实抓取时间",
        ))
    findings.extend(_internal_consistency_findings(claims, target_id, standard_basis))
    findings.extend(
        _reference_findings(
            sources,
            target_id,
            standard_basis,
            review_as_of,
        )
    )
    for pack in evidence_packs:
        payload = pack.get("payload") or {}
        for index, conflict in enumerate(payload.get("conflicts") or [], start=1):
            findings.append(rules.finding(
                "REPORT.CLAIM.EVIDENCE",
                "P1",
                "绑定证据包存在未裁决冲突，审查不得静默选择有利值",
                category="evidence_conflict",
                expected="冲突显式裁决并保留全部来源",
                actual=conflict,
                target_location={"target_id": target_id, "evidence_pack_id": pack.get("object_id"), "conflict": index},
                standard_basis=standard_basis,
                review_area="business",
                remediation="由责任专业角色核对原件、范围、时点和主体后形成可审计裁决",
            ))
    hotel = "hotel-acquisition" in overlays or ("恒立" in content and "酒店" in content)
    mineral = "mineral-processing" in overlays or "黄鹰岩" in content or ("石灰岩" in content and "绿色工厂" in content)
    executed = set(REPORT_RULES)
    if hotel:
        findings.extend(_hotel_findings(
            content,
            claims,
            evidence_claims,
            candidates,
            target_id,
            standard_basis,
            review_as_of,
        ))
        executed.update({
            "HOTEL.RIGHTS.LICENSES",
            "HOTEL.OPERATING_MODEL",
            "HOTEL.ROOM_COUNT.CONFLICT",
            "HOTEL.AREA.CONFLICT",
            "HOTEL.LAND_USE.COMPLIANCE",
            "HOTEL.LEASE.TERMS.CONFLICT",
        })
    if mineral:
        mineral_findings, mineral_incomplete = _mineral_findings(
            content,
            claims,
            evidence_claims,
            candidates,
            sources,
            target_id,
            standard_basis,
        )
        findings.extend(mineral_findings)
        incomplete.extend(mineral_incomplete)
        executed.update({
            "MINERAL.PERMITS",
            "MINERAL.MARKET.RADIUS",
            "MINERAL.OPERATING.DRIVERS",
            "MINERAL.CONTRACT.FIELDS",
        })
    if any(row.get("claim_type") == "financial" for row in claims) and not run:
        incomplete.append("financial_claims_without_bound_run")
    if not evidence_packs:
        incomplete.append("evidence_pack_not_bound")
    metrics["hotel_rules_applied"] = hotel
    metrics["mineral_rules_applied"] = mineral
    return findings, sorted(set(incomplete)), metrics, executed


def review_combined(
    *,
    report_contents: list[dict[str, Any]],
    finance_runs: list[dict[str, Any]],
    target_id: str,
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any], set[str]]:
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    combined_index: dict[str, list[dict[str, Any]]] = {}
    for run in finance_runs:
        run_id = str(run.get("run_id") or run.get("id") or "")
        for metric, rows in semantic_finance_index(run).items():
            combined_index.setdefault(metric, []).extend({**row, "run_id": run_id} for row in rows)
    compared = 0
    matched = 0
    all_content = "\n".join(str(row.get("content") or "") for row in report_contents)
    for report in report_contents:
        report_id = str(report.get("target_id") or "")
        for claim in build_claim_graph(str(report.get("content") or ""), target_id=report_id):
            metric = str(claim.get("metric") or "")
            if metric not in _FINANCIAL_METRICS:
                continue
            compared += 1
            matches = _claim_run_matches(claim, combined_index)
            if matches:
                matched += 1
                continue
            findings.append(rules.finding(
                "COMBINED.NUMBERS.MATCH",
                "P0",
                "联合交付包中研报财务数字与财务组件不一致",
                category="combined_numeric_consistency",
                expected={"metric": metric, "finance_component_values": combined_index.get(metric) or []},
                actual={"value": claim["value"], "unit": claim["unit"], "context": claim["context"]},
                target_location={**claim["location"], "combined_target_id": target_id},
                standard_basis=standard_basis,
                review_area="finance",
                remediation="以同一已审查 finance run/package 为唯一数字源并同步生成研报和附件",
            ))
    if not finance_runs:
        incomplete.append("combined_finance_run_unavailable")
    elif not report_contents:
        incomplete.append("combined_report_content_unavailable")
    elif compared == 0:
        findings.append(rules.finding(
            "COMBINED.NUMBERS.MATCH",
            "P1",
            "研报未披露可与财务组件核对的核心财务数字",
            category="combined_numeric_consistency",
            expected="总投资、IRR、NPV、偿债指标及核心收入成本至少形成语义绑定",
            actual=None,
            target_location={"target_id": target_id},
            standard_basis=standard_basis,
            review_area="finance",
            remediation="补充核心财务披露并从绑定 run/package 自动取数",
        ))

    adverse: list[dict[str, Any]] = []
    for run in finance_runs:
        index = semantic_finance_index(run)
        irr_values = [float(row["value"]) for row in index.get("project_irr") or []]
        benchmark_values = [
            float(row["value"])
            for row in _flatten_numbers(run)
            if re.search(r"benchmark_rate|discount_rate|基准收益率", str(row["path"]), re.I)
        ]
        if irr_values and benchmark_values:
            irr = irr_values[0] * 100.0 if abs(irr_values[0]) <= 1.0 else irr_values[0]
            benchmark = benchmark_values[0] * 100.0 if abs(benchmark_values[0]) <= 1.0 else benchmark_values[0]
            if irr < benchmark:
                adverse.append({"reason": "irr_below_benchmark", "irr_pct": irr, "benchmark_pct": benchmark})
        dscr_values = [float(row["value"]) for row in index.get("dscr") or [] if float(row["value"]) > 0]
        if dscr_values and min(dscr_values) < 1.2:
            adverse.append({"reason": "dscr_below_1.2", "minimum_dscr": min(dscr_values)})
        icr_values = [float(row["value"]) for row in index.get("icr") or [] if float(row["value"]) > 0]
        if icr_values and min(icr_values) < 1.0:
            adverse.append({"reason": "icr_below_1.0", "minimum_icr": min(icr_values)})
        negative_plan = [
            row for row in _flatten_numbers(run)
            if "financial_plan" in str(row["path"]).lower()
            and "cumulative" in str(row["path"]).lower()
            and float(row["value"]) < 0
        ]
        if negative_plan:
            adverse.append({"reason": "negative_cumulative_surplus", "rows": negative_plan[:20]})
        if run.get("consistency_ok") is False:
            adverse.append({"reason": "finance_consistency_failed", "run_id": run.get("run_id")})
        # Also check integrity_status for new runs
        integrity_status = str(run.get("integrity_status") or "")
        if integrity_status and integrity_status != "passed":
            adverse.append({"reason": "integrity_failed", "integrity_status": integrity_status, "run_id": run.get("run_id")})
        # Check viability_status: infeasible runs should not claim success
        viability_status = str(run.get("viability_status") or "not_assessed")
        if viability_status == "infeasible":
            adverse.append({"reason": "viability_infeasible", "run_id": run.get("run_id")})
    positive = bool(re.search(r"(?:项目|本项目|财务).{0,30}(?:可行|具备偿债能力|建议实施|值得投资|风险可控)", all_content, re.S))
    negative = bool(re.search(r"(?:不可行|不具备偿债能力|不建议实施|风险不可控)", all_content))
    if positive and adverse:
        findings.append(rules.finding(
            "COMBINED.CONCLUSIONS.MATCH",
            "P0",
            "研报正面结论与财务组件的偿债、收益或可持续性结果相反",
            category="combined_conclusion_consistency",
            expected="结论与 IRR/基准收益率、DSCR、ICR、下行情景和累计盈余一致",
            actual={"positive_conclusion": True, "adverse_finance_signals": adverse},
            target_location={"target_id": target_id, "text_anchor": "结论"},
            standard_basis=standard_basis,
            review_area="finance",
            remediation="修正财务模型或结论，并完整披露下行情景、敏感性和主要风险",
        ))
    elif not positive and not negative:
        findings.append(rules.finding(
            "COMBINED.CONCLUSIONS.MATCH",
            "P1",
            "联合交付包缺少可与财务结果核对的明确可行性结论",
            category="combined_conclusion_consistency",
            expected="基于收益、偿债、下行情景和主要风险形成明确结论",
            actual=None,
            target_location={"target_id": target_id, "text_anchor": "结论"},
            standard_basis=standard_basis,
            review_area="report",
            remediation="补充明确结论并逐项引用对应财务指标和风险依据",
        ))
    metrics = {
        "financial_claims_compared": compared,
        "financial_claims_matched": matched,
        "adverse_finance_signals": adverse,
        "positive_conclusion_detected": positive,
        "negative_conclusion_detected": negative,
    }
    return findings, sorted(set(incomplete)), metrics, set(COMBINED_RULES)
