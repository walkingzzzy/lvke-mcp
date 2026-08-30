"""Pure formal-lineage validators for report preparation and revisions."""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
from lvke_mcp.adapters.finance_tables_repository import PACKAGE_STORE as TABLE_STORE
from lvke_mcp.adapters.report_repository import PREPARATION_STORE
from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_STORE
from lvke_mcp.runtime.evidence_qualification import SIM_A_FORMAL
from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    validate_finance_run,
    validate_finance_tables_package,
    validate_formal_record,
    validate_immutable_record,
    validate_research_package,
)


def formal_report_lineage(
    workspace_id: str,
    *,
    evidence_records: list[dict[str, Any]],
    research_records: list[dict[str, Any]],
    run_id: str,
    table_record: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate every deterministic parent of a generic formal report."""

    if not evidence_records:
        raise FormalLineageError(
            "formal_lineage_parent_required",
            "正式报告缺少 EvidencePack 父对象",
        )
    canonical_rows = [
        validate_formal_record(workspace_id, record)
        for record in evidence_records
    ]
    canonical_rows.extend(
        validate_research_package(workspace_id, record)
        for record in research_records
    )
    canonical_rows.append(validate_finance_run(workspace_id, run_id))
    if not isinstance(table_record, dict):
        raise FormalLineageError(
            "formal_tables_package_not_found",
            "正式报告缺少 FinanceTablesPackage 父对象",
        )
    canonical_rows.append(
        validate_finance_tables_package(workspace_id, table_record)
    )
    canonical = canonical_rows[0]
    if any(row != canonical for row in canonical_rows[1:]):
        raise FormalLineageError(
            "formal_lineage_mixed_promotions",
            "报告父对象来自不同 promotion",
        )
    return canonical


def validate_report_preparation_lineage(
    workspace_id: str,
    preparation: dict[str, Any],
) -> dict[str, Any]:
    """Revalidate a persisted SIM-A report preparation at a later boundary."""

    validate_immutable_record(workspace_id, preparation)
    payload = preparation.get("payload") if isinstance(preparation, dict) else None
    if not isinstance(payload, dict):
        raise FormalLineageError(
            "formal_report_preparation_invalid",
            "报告准备对象 payload 无效",
        )
    if str(payload.get("evidence_policy") or "") != SIM_A_FORMAL:
        raise FormalLineageError(
            "formal_lineage_policy_required",
            "报告准备对象不是 sim_a_formal",
        )
    evidence_records = []
    for evidence_id in payload.get("evidence_pack_ids") or []:
        record = EVIDENCE_STORE.get(workspace_id, str(evidence_id))
        if not isinstance(record, dict):
            raise FormalLineageError(
                "formal_evidence_pack_not_found",
                f"正式 EvidencePack 不存在或不属于当前工作区: {evidence_id}",
            )
        evidence_records.append(record)
    research_records = []
    for research_id in payload.get("research_package_ids") or []:
        record = RESEARCH_STORE.get(workspace_id, str(research_id))
        if not isinstance(record, dict):
            raise FormalLineageError(
                "formal_research_package_not_found",
                f"正式 ResearchPackage 不存在或不属于当前工作区: {research_id}",
            )
        research_records.append(record)
    table_id = str(payload.get("finance_tables_package_id") or "")
    table_record = TABLE_STORE.get(workspace_id, table_id) if table_id else None
    canonical = formal_report_lineage(
        workspace_id,
        evidence_records=evidence_records,
        research_records=research_records,
        run_id=str(payload.get("run_id") or ""),
        table_record=table_record,
    )
    stored = {
        key: payload.get(key)
        for key in (
            "evidence_policy",
            "evidence_origin",
            "project_fact_certified",
            "formal_promotion",
        )
    }
    if stored != canonical:
        raise FormalLineageError(
            "formal_lineage_metadata_mismatch",
            "报告准备对象的 promotion 元数据不是规范值",
        )
    return canonical


def validate_report_revision_lineage(
    workspace_id: str,
    revision: dict[str, Any],
) -> dict[str, Any]:
    """Validate a SIM-A revision and its exact immutable preparation parent."""

    validate_immutable_record(workspace_id, revision)
    payload = revision.get("payload") if isinstance(revision, dict) else None
    if not isinstance(payload, dict):
        raise FormalLineageError(
            "formal_report_revision_invalid",
            "报告修订 payload 无效",
        )
    upstream = payload.get("upstream")
    if not isinstance(upstream, dict) or upstream.get("evidence_policy") != SIM_A_FORMAL:
        raise FormalLineageError(
            "formal_lineage_unsigned_history",
            "正式报告修订缺少规范 SIM-A upstream",
        )
    preparation_id = str(payload.get("report_preparation_id") or "")
    preparation = PREPARATION_STORE.get(workspace_id, preparation_id)
    if not isinstance(preparation, dict):
        raise FormalLineageError(
            "formal_report_preparation_not_found",
            "报告修订绑定的 preparation 不存在或不属于当前工作区",
        )
    canonical = validate_report_preparation_lineage(workspace_id, preparation)
    if (
        str(payload.get("basis_hash") or "") != str(preparation.get("basis_hash") or "")
        or upstream != (preparation.get("payload") or {})
    ):
        raise FormalLineageError(
            "formal_report_parent_mismatch",
            "报告修订与 preparation 的 payload 或 basis_hash 不一致",
        )
    return canonical
