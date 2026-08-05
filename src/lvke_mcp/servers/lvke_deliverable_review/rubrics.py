"""Deterministic section rubrics and immutable revision comparisons."""

from __future__ import annotations

import re
from typing import Any

from lvke_mcp.domains.reports import read_model as report_read_model
from lvke_mcp.runtime.storage import JSONArtifactStore

RUBRIC_VERSION = "feasibility-section.v1"
PASS_SCORE = 4.0

ASSESSMENT_STORE = JSONArtifactStore(
    "deliverable-review", "rubric_assessments", "rva", "rubric-assessments"
)
COMPARISON_STORE = JSONArtifactStore(
    "deliverable-review", "rubric_comparisons", "rvc", "rubric-comparisons"
)

RUBRIC = {
    "rubric_id": "feasibility-section",
    "rubric_version": RUBRIC_VERSION,
    "title": "可行性研究报告章节确定性质量评分",
    "dimensions": [
        {"dimension": "data_support", "weight": 0.18, "source_skill": "doc-review-citation-verification"},
        {"dimension": "scale_reasonableness", "weight": 0.12, "source_skill": "doc-review-consistency"},
        {"dimension": "finance_binding", "weight": 0.16, "source_skill": "doc-review-numerics-cross-check"},
        {"dimension": "internal_consistency", "weight": 0.18, "source_skill": "doc-review-consistency"},
        {"dimension": "compliance_boundary", "weight": 0.12, "source_skill": "doc-review-compliance"},
        {"dimension": "risk_completeness", "weight": 0.12, "source_skill": "doc-review-risk-completeness"},
        {"dimension": "decision_readability", "weight": 0.12, "source_skill": "doc-review-decision-support"},
    ],
    "pass_score": PASS_SCORE,
    "hard_floor": 3,
    "scorer": "deterministic_rules",
}


def list_rubrics(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "business_success": True,
        "system_success": True,
        "transport_success": True,
        "status": "ok",
        "rubrics": [RUBRIC],
        "project_context": dict(args.get("project_context") or {}),
        "resource_uris": [],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def _dimension(
    name: str,
    score: int,
    signals: list[str],
    *,
    applicable: bool = True,
) -> dict[str, Any]:
    return {
        "dimension": name,
        "score": max(1, min(5, int(score))),
        "applicable": applicable,
        "signals": signals,
    }


def _score_content(
    content: str,
    section: dict[str, Any],
    validation: dict[str, Any],
    revision_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    details = dict(validation.get("validation") or {})
    numeric = int(details.get("numeric_statement_count") or 0)
    uncited = len(details.get("uncited_numeric_statements") or [])
    citations = int(details.get("citation_marker_count") or 0)
    ratio = 1.0 if numeric == 0 else (numeric - uncited) / numeric
    data_score = 5 if ratio == 1 and citations else 4 if ratio == 1 else 3 if ratio >= 0.75 else 2 if ratio > 0 else 1

    title = str(section.get("title") or "")
    scale_applicable = bool(re.search(r"规模|方案|市场|需求|建设", title + content))
    scale_signals = [key for key in ("规模", "需求", "产能", "面积", "约束", "比选") if key in content]
    scale_score = min(5, 3 + len(scale_signals) // 2) if scale_applicable else 3

    finance_terms = [key for key in ("投资", "收入", "成本", "IRR", "NPV", "现金流", "回收期") if key in content]
    finance_applicable = bool(finance_terms) or bool(re.search(r"财务|投资|融资", title))
    upstream = dict(revision_payload.get("upstream") or {})
    bound_run = bool(upstream.get("run_id") or revision_payload.get("run_id"))
    bound_tables = bool(upstream.get("finance_tables_package_id") or revision_payload.get("finance_tables_package_id"))
    finance_score = (5 if bound_run and bound_tables else 3 if bound_run else 1) if finance_applicable else 3

    blockers = list(validation.get("blockers") or [])
    internal_score = 5 if not blockers else max(1, 5 - len(set(blockers)))

    compliance_claims = len(re.findall(r"(?:符合|满足|依法|必须|应当)", content))
    compliance_applicable = compliance_claims > 0 or bool(re.search(r"合规|消防|环保|安全|标准", title))
    compliance_score = (
        5 if compliance_applicable and citations >= compliance_claims and compliance_claims > 0
        else 4 if compliance_applicable and citations > 0
        else 2 if compliance_applicable
        else 3
    )

    risk_terms = [key for key in ("政策", "市场", "技术", "财务", "进度", "运营", "环境") if key in content]
    risk_applicable = "风险" in title or "风险" in content
    risk_score = min(5, 1 + len(risk_terms) * 4 // 7) if risk_applicable else 3

    decision_terms = [key for key in ("结论", "建议", "条件", "前提", "风险", "措施", "不建议") if key in content]
    decision_score = min(5, 2 + len(decision_terms) // 2)
    if len(content.strip()) < 200:
        decision_score = min(decision_score, 2)

    return [
        _dimension("data_support", data_score, [f"numeric_claims={numeric}", f"cited_ratio={ratio:.3f}"]),
        _dimension("scale_reasonableness", scale_score, scale_signals, applicable=scale_applicable),
        _dimension("finance_binding", finance_score, [f"finance_terms={len(finance_terms)}", f"run_bound={bound_run}", f"tables_bound={bound_tables}"], applicable=finance_applicable),
        _dimension("internal_consistency", internal_score, sorted(set(blockers))),
        _dimension("compliance_boundary", compliance_score, [f"normative_claims={compliance_claims}", f"citations={citations}"], applicable=compliance_applicable),
        _dimension("risk_completeness", risk_score, risk_terms, applicable=risk_applicable),
        _dimension("decision_readability", decision_score, decision_terms),
    ]


def score_section(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    revision_id = str(args["report_revision_id"])
    section_id = str(args["section_id"])
    record, _native = report_read_model.resolve_revision_record(
        workspace_id, revision_id
    )
    if record is None:
        return {
            "success": False,
            "business_success": False,
            "system_success": True,
            "transport_success": True,
            "status": "blocked",
            "code": "rubric_revision_not_found",
            "message": "不可变报告修订不存在或不属于当前工作区",
            "resource_uris": [],
            "warnings": [],
            "blockers": ["rubric_revision_not_found"],
            "next_actions": [],
        }
    section_result = report_read_model.get_section(
        workspace_id, revision_id, section_id
    )
    if section_result.get("status") != "ok":
        return section_result
    validation = report_read_model.validate_section(
        workspace_id, revision_id, section_id
    )
    content = str(section_result.get("content") or "")
    section = dict(section_result.get("section") or {})
    dimensions = _score_content(
        content,
        section,
        validation,
        dict(record.get("payload") or {}),
    )
    weights = {item["dimension"]: float(item["weight"]) for item in RUBRIC["dimensions"]}
    applicable_weight = sum(weights[item["dimension"]] for item in dimensions if item["applicable"])
    weighted = sum(
        item["score"] * weights[item["dimension"]]
        for item in dimensions
        if item["applicable"]
    ) / applicable_weight
    weighted = round(weighted, 4)
    passing = weighted >= PASS_SCORE and all(
        item["score"] >= RUBRIC["hard_floor"] for item in dimensions if item["applicable"]
    )
    payload = {
        "rubric_id": RUBRIC["rubric_id"],
        "rubric_version": RUBRIC_VERSION,
        "report_revision_id": record["object_id"],
        "revision_basis_hash": record["basis_hash"],
        "section_id": section_id,
        "section_content_hash": section_result["content_hash"],
        "section_title": section.get("title"),
        "dimensions": dimensions,
        "weighted_score": weighted,
        "passing": passing,
        "scorer": "deterministic_rules",
        "validation_status": validation.get("status"),
        "validation_blockers": list(validation.get("blockers") or []),
    }
    assessment = ASSESSMENT_STORE.put(
        workspace_id,
        payload,
        producer="lvke-deliverable-review.review_score_section",
        status="passed" if passing else "needs_revision",
        source_ids=[record["object_id"], section_id],
        basis={
            "rubric": RUBRIC,
            "revision_basis_hash": record["basis_hash"],
            "section_content_hash": section_result["content_hash"],
        },
    )
    return {
        "success": True,
        "business_success": True,
        "system_success": True,
        "transport_success": True,
        "status": "ok",
        "rubric_assessment_id": assessment["object_id"],
        "assessment": {**payload, "basis_hash": assessment["basis_hash"], "content_hash": assessment["content_hash"]},
        "resource_uris": [assessment["resource_uri"]],
        "warnings": [] if passing else ["评分仅表示技术 rubric 结果，不替代整篇审查或人工审定"],
        "blockers": [],
        "next_actions": [] if passing else ["由 Codex 修订章节并重新评分"],
    }


def compare_assessments(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    before = ASSESSMENT_STORE.get(
        workspace_id, str(args["before_assessment_id"])
    )
    after = ASSESSMENT_STORE.get(
        workspace_id, str(args["after_assessment_id"])
    )
    if before is None or after is None:
        return {
            "success": False,
            "business_success": False,
            "system_success": True,
            "transport_success": True,
            "status": "blocked",
            "code": "rubric_assessment_not_found",
            "resource_uris": [],
            "warnings": [],
            "blockers": ["rubric_assessment_not_found"],
            "next_actions": [],
        }
    bp = dict(before.get("payload") or {})
    ap = dict(after.get("payload") or {})
    if bp.get("rubric_version") != ap.get("rubric_version"):
        return {
            "success": False,
            "business_success": False,
            "system_success": True,
            "transport_success": True,
            "status": "blocked",
            "code": "rubric_version_mismatch",
            "resource_uris": [],
            "warnings": [],
            "blockers": ["rubric_version_mismatch"],
            "next_actions": ["使用同一 rubric_version 重新评分后比较"],
        }
    before_dims = {item["dimension"]: item for item in bp.get("dimensions") or []}
    after_dims = {item["dimension"]: item for item in ap.get("dimensions") or []}
    deltas = [
        {
            "dimension": name,
            "before": before_dims[name]["score"],
            "after": after_dims[name]["score"],
            "delta": after_dims[name]["score"] - before_dims[name]["score"],
        }
        for name in sorted(set(before_dims) & set(after_dims))
    ]
    payload = {
        "rubric_version": bp["rubric_version"],
        "before_assessment_id": before["object_id"],
        "after_assessment_id": after["object_id"],
        "before_revision_id": bp.get("report_revision_id"),
        "after_revision_id": ap.get("report_revision_id"),
        "before_score": bp.get("weighted_score"),
        "after_score": ap.get("weighted_score"),
        "score_delta": round(float(ap.get("weighted_score") or 0) - float(bp.get("weighted_score") or 0), 4),
        "dimension_deltas": deltas,
        "resolved_validation_blockers": sorted(set(bp.get("validation_blockers") or []) - set(ap.get("validation_blockers") or [])),
        "new_validation_blockers": sorted(set(ap.get("validation_blockers") or []) - set(bp.get("validation_blockers") or [])),
        "improved": float(ap.get("weighted_score") or 0) > float(bp.get("weighted_score") or 0),
    }
    comparison = COMPARISON_STORE.put(
        workspace_id,
        payload,
        producer="lvke-deliverable-review.review_compare_assessments",
        source_ids=[before["object_id"], after["object_id"]],
        basis={
            "before_hash": before["content_hash"],
            "after_hash": after["content_hash"],
            "rubric_version": bp["rubric_version"],
        },
    )
    return {
        "success": True,
        "business_success": True,
        "system_success": True,
        "transport_success": True,
        "status": "ok",
        "rubric_comparison_id": comparison["object_id"],
        "comparison": payload,
        "resource_uris": [comparison["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def resolve_rubric_resource(uri: str) -> tuple[dict[str, Any], str] | None:
    for store, object_type in (
        (ASSESSMENT_STORE, "RubricAssessment"),
        (COMPARISON_STORE, "RubricComparison"),
    ):
        record = store.resolve_uri(uri)
        if record is not None:
            return record, object_type
    return None


__all__ = [
    "ASSESSMENT_STORE",
    "COMPARISON_STORE",
    "RUBRIC",
    "compare_assessments",
    "list_rubrics",
    "resolve_rubric_resource",
    "score_section",
]
