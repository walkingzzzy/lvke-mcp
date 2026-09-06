"""Classify financial model data-quality findings.

The delivery workflow is intentionally open. Evidence, review, formal
release, stage, template, and completeness findings are diagnostics, not
gates. Financial contradictions are retained as high-severity data-quality
diagnostics, but they do not become workflow gates either.
"""

from __future__ import annotations

from typing import Iterable


#: Financial model data-quality findings. They remain visible as ``fail``
#: diagnostics, but never stop a workflow.
FINANCE_DATA_QUALITY_CODES: frozenset[str] = frozenset({
    "project_scale_inconsistent",
    "input_revision_scale_drift",
    "market_capacity_unit_mismatch",
    "scale_reconciliation_failed",
    "investment_scope_ambiguous",
    "finance_consistency_failed",
    "finance_reconciliation_failed",
    "finance_narrative_mismatch",
    "finance_run_consistency_failed",
    "finance_run_failed",
    "finance_run_persistence_failed",
    "finance_spec_prepare_failed",
    "finance_spec_confirm_failed",
    "finance_tables_validation_failed",
    "finance_tables_incomplete",
    "finance_tables_run_binding_mismatch",
    "finance_run_spec_binding_mismatch",
    "finance_input_conflict",
    "finance_binding_mismatch",
    "finance_artifact_mismatch",
    "finance_key_report_values_unmapped",
    "finance_ungrounded",
})

# Kept as compatibility exports for callers that import the old policy names.
# Gate sets must stay empty: no delivery condition may become a hard gate.
BLOCKING_CODES: frozenset[str] = frozenset()
NON_BLOCKING_BY_DESIGN: frozenset[str] = frozenset()
NON_BLOCKING_SUFFIXES: tuple[str, ...] = (
    "_pending", "_output_refs_missing", "_input_refs_missing",
    "_basis_hash_missing", "_object_required", "_package_required",
    "_revision_required", "_run_required", "_blockers_present",
)
NON_BLOCKING_INFIXES: tuple[str, ...] = (
    "_output_kind_missing:", "_parent_stage_binding_missing:",
    "stage_order_invalid:",
)

# Detailed finance diagnostics may carry a field suffix. This is deliberately
# separate from ``BLOCKING_PREFIXES``, which must remain empty.
FINANCE_DATA_QUALITY_PREFIXES: tuple[str, ...] = (
    "project_scale_inconsistent",
    "input_revision_scale_drift",
    "scale_reconciliation_failed",
    "market_capacity_unit_mismatch",
    "investment_scope_ambiguous",
    "finance_consistency_failed",
    "finance_reconciliation_failed",
    "finance_narrative_mismatch",
    "finance_run_consistency_failed",
    "finance_run_failed",
    "finance_run_persistence_failed",
    "finance_spec_prepare_failed",
    "finance_spec_confirm_failed",
    "finance_tables_validation_failed",
    "finance_tables_incomplete",
    "finance_tables_run_binding_mismatch",
    "finance_run_spec_binding_mismatch",
    "finance_input_conflict",
    "finance_binding_mismatch",
    "finance_artifact_mismatch",
    "finance_key_report_values_unmapped",
    "finance_ungrounded",
    # Legacy acquisition modules use upper-case data-quality codes.
    "TABLE_PACKAGE_INCOMPLETE",
    "RUN_SPEC_MISMATCH",
    "RUN_NOT_READY",
)
BLOCKING_PREFIXES: tuple[str, ...] = ()


def is_blocking(code: str) -> bool:
    """Compatibility predicate: delivery quality never blocks a workflow."""

    return False


def is_finance_data_quality_issue(code: object) -> bool:
    """Return whether a diagnostic concerns financial model data quality."""

    text = str(code or "").strip()
    return bool(
        text
        and (
            text in FINANCE_DATA_QUALITY_CODES
            or text.startswith(FINANCE_DATA_QUALITY_PREFIXES)
        )
    )


def split_quality_codes(codes: Iterable[object]) -> tuple[list[str], list[str]]:
    """Split codes into ``(blockers, quality_issues)``, both sorted and deduped.

    调用点只需把手上全部问题码交给它，不要自己再判一遍严重性——那正是
    严重性判定在六处各写一套、最后集体退化成 ``[]`` 的成因。
    """

    blocking: set[str] = set()
    quality: set[str] = set()
    for item in codes:
        text = str(item or "").strip()
        if not text:
            continue
        quality.add(text)
        if is_blocking(text):
            blocking.add(text)
    return sorted(blocking), sorted(quality)


#: quality_status 的等级。fail > unclassified > warn > pass：
#: 一旦出现 material_conflict，即使还有未知码也不得显示为"通过"；
#: 未知码也不得显示为 pass（会漏报），只能停在 unclassified。
_QUALITY_RANK = {
    "pass": 0,
    "warn": 1,
    "unclassified": 2,
    "fail": 3,
}

# Known workflow diagnostics should render as warnings instead of appearing as
# unknown rule codes. Unknown codes still remain unclassified for visibility.
_WORKFLOW_DIAGNOSTIC_PREFIXES: tuple[str, ...] = (
    "formal_", "review_", "research_", "evidence_", "citation_",
    "source_", "reconstruction_", "controlled_", "project_fact_",
    "required_component_", "delivery_", "report_", "stage_",
    "governed_", "appendix_", "market_", "option_", "revenue_",
    "cost_", "labor_", "assumption_", "template_", "knowledge_",
)


def classify_quality(code: object) -> dict[str, bool | str]:
    """Classify one diagnostic code into the diagnostic-only envelope model.

    技术验收阶段的统一判定入口：不再用 `is_blocking()` 决定"整条链停不停"，
    而是把每个问题码归入四档 `quality_status` 并告诉调用方是否需要
    (a) 生成诊断对象、是否 (b) 构成数值可信度冲突。任何业务质量码都不触发
    下游计算停止；是否可用于正式研报由 `formal_report_allowed` 表达，
    与 `success` / `ready` 完全解耦。

    - ``fail``：影响数值可信度的口径/勾稽冲突（material conflict），必须
      固化 QualityDiagnostic 并保留冲突双方。
    - ``warn``：结果可用但置信度有限（证据待补、阶段未完成、可行性较差）。
    - ``unclassified``：未登记的规则码。按方案"不会因新规则未登记而停止
      技术诊断"，自动标记人工确认，不得显示为质量通过。
    - ``pass``：无问题（调用方在无问题时直接给 pass，本函数只分类问题码）。
    """

    text = str(code or "").strip()
    if not text:
        return {
            "quality_status": "unclassified",
            "diagnostic_required": True,
            "material_conflict": False,
            "formal_report_allowed": False,
        }
    if text in NON_BLOCKING_BY_DESIGN:
        return {
            "quality_status": "warn",
            "diagnostic_required": True,
            "material_conflict": False,
            "formal_report_allowed": False,
        }
    if text.startswith(_WORKFLOW_DIAGNOSTIC_PREFIXES) or text.endswith(NON_BLOCKING_SUFFIXES):
        return {
            "quality_status": "warn",
            "diagnostic_required": True,
            "material_conflict": False,
            "formal_report_allowed": True,
        }
    if is_finance_data_quality_issue(text):
        return {
            "quality_status": "fail",
            "diagnostic_required": True,
            "material_conflict": True,
            "formal_report_allowed": True,
        }
    # 结构完整性码（阶段未完成、对象缺失等）：置信度不足而非口径冲突。
    if text.startswith(NON_BLOCKING_INFIXES) or text.endswith(NON_BLOCKING_SUFFIXES):
        return {
            "quality_status": "warn",
            "diagnostic_required": True,
            "material_conflict": False,
            "formal_report_allowed": False,
        }
    return {
        "quality_status": "unclassified",
        "diagnostic_required": True,
        "material_conflict": False,
        "formal_report_allowed": False,
    }


def aggregate_quality_status(codes: Iterable[object]) -> str:
    """Return the worst ``quality_status`` across a set of diagnostic codes.

    等级：fail > unclassified > warn > pass。未知码会把整体停在
    ``unclassified``，避免"混着未知码却显示质量通过"。
    """

    worst = "pass"
    for item in codes:
        text = str(item or "").strip()
        if not text:
            continue
        status = str(classify_quality(text)["quality_status"] or "unclassified")
        if _QUALITY_RANK.get(status, 0) > _QUALITY_RANK.get(worst, 0):
            worst = status
    return worst
