"""Deterministic validation use cases for immutable report revisions."""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters.report_repository import PREPARATION_STORE
from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_STORE
from lvke_mcp.domains.finance import gate as finance_gate
from lvke_mcp.domains.reports import doc_service as doc
from lvke_mcp.domains.reports import readiness as report_artifacts
from lvke_mcp.domains.reports.read_model import (
    resolve_revision_record,
    supplied_document_snapshot,
)


def validate_report(workspace_id: str, revision_id: str) -> dict[str, Any]:
    record, native_alias = resolve_revision_record(workspace_id, revision_id)
    if record is None:
        return _failure("revision_not_found", "未找到研报修订")
    payload = record.get("payload") or {}
    native = str(payload.get("native_revision_id") or "")
    document = supplied_document_snapshot(workspace_id, payload.get("document_snapshot"))
    if document is None:
        document = doc.read_document(workspace_id, revision_id=native)
    if document is None:
        return _failure("document_snapshot_missing", "修订缺少不可变 document_snapshot")

    content = str(document.get("content") or "")
    upstream = payload.get("upstream") or {}
    report_type = str(document.get("report_type") or "generic_feasibility")
    expected_chapters = list(upstream.get("outline") or [])
    structure = doc.validate_report_structure(
        content,
        report_type,
        expected_chapters=expected_chapters,
    )
    run_id = str(upstream.get("run_id") or "")
    narrative = finance_gate.verify_narrative_numbers(
        workspace_id,
        content,
        run_id=run_id,
    )
    binding = finance_gate.assert_publish_finance_binding(
        workspace_id,
        expected_run_id=run_id,
        strict=True,
    )
    readiness = report_artifacts.build_readiness(
        workspace_id,
        persist=False,
        revision_id=native,
        document_snapshot=document,
        expected_chapters=expected_chapters,
    )

    blockers: list[str] = []
    bound_preparation_id = str(payload.get("report_preparation_id") or "")
    preparations = sorted(
        PREPARATION_STORE.list(workspace_id),
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    latest_preparation_id = (
        str(preparations[0].get("object_id") or "") if preparations else ""
    )
    if latest_preparation_id and bound_preparation_id != latest_preparation_id:
        blockers.append("upstream_basis_superseded")
    if not structure.get("ok"):
        blockers.append("report_structure_invalid")
    if not narrative.get("ok"):
        blockers.append("finance_narrative_mismatch")
    blockers.extend(
        str(item.get("code") or "finance_binding_blocker")
        for item in (binding.get("blockers") or [])
    )
    blockers.extend(
        str(item.get("code") or "readiness_blocker")
        for item in (readiness.get("blockers") or [])
    )
    warnings = [
        str(item.get("message") or item.get("code") or "")
        for item in (readiness.get("warnings") or [])
    ]
    if native_alias:
        warnings.append("native_revision_id 输入已弃用；请改用 report_revision_id")
    for research_id in upstream.get("research_package_ids") or []:
        research = RESEARCH_STORE.get(workspace_id, research_id)
        research_status = str((research or {}).get("status") or "")
        if research_status == "partial":
            warnings.append(f"{research_id}: partial 研究限制必须保留")
        elif research_status not in {"done", "completed", "ok"}:
            blockers.append(
                f"research_package_not_usable:{research_id}:{research_status or 'unknown'}"
            )
    task_status = str(payload.get("task_status") or "")
    if task_status in {"failed", "cancelled"}:
        warnings.append("起草任务未完成；当前修订不能视为生成成功")

    blockers = sorted(set(blockers))
    blocked = bool(blockers)
    return {
        "success": not blocked,
        "transport_success": True,
        "business_success": not blocked,
        "completed": not blocked,
        "outcome": "blocked" if blocked else "ok",
        "status": "blocked" if blocked else "ok",
        "valid": not blocked,
        "report_revision_id": record["object_id"],
        "native_revision_id": native,
        "run_id": run_id,
        "finance_tables_package_id": str(upstream.get("finance_tables_package_id") or ""),
        "basis_hash": str(payload.get("basis_hash") or record.get("basis_hash") or ""),
        "structure": structure,
        "finance_narrative": narrative,
        "finance_binding": binding,
        "readiness": readiness,
        "bound_preparation_id": bound_preparation_id,
        "latest_preparation_id": latest_preparation_id,
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": blockers,
        "next_actions": (
            ["工程校验已通过，可导出 formal_candidate 工件"]
            if not blockers
            else ["按 blockers 修订后重新执行 report_validate"]
        ),
    }


def _failure(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "transport_success": True,
        "business_success": False,
        "completed": False,
        "outcome": "blocked",
        "status": "blocked",
        "code": code,
        "message": message,
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [],
    }