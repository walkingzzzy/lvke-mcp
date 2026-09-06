"""工件创建与失效：事件追加、记录固化、草稿/正式导出与刷新。"""

from __future__ import annotations

import copy
import shutil
import uuid
from typing import Any


from lvke_mcp.domains.reports import doc_service
from lvke_mcp.runtime.quality_severity import split_quality_codes

from .base import (
    DEFAULT_TEMPLATE_VERSION,
    DRAFT_MARKER,
    DeliverableArtifactError,
    SCHEMA_VERSION,
    _GOVERNED_SNAPSHOTS,
    _SAFE_OPERATION_ID,
    _canonical_hash,
    _now,
    _validate_template_version,
    _validate_workspace_id,
)

from .directory import (
    _build_artifact_directory,
    _set_docx_metadata,
    _verify_files,
)

from .formal_gate import (
    _capture_basis,
    _draft_basis_quality_issues,
    _marker_markdown,
)

from .storage import (
    _artifact_root,
    _read_state,
    _require_workspace,
    _state_guard,
    _state_path,
    _write_json_atomic,
)


def _basis_change_reasons(
    stored: dict[str, Any],
    current: dict[str, Any],
) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []

    def changed(code: str, message: str, expected: Any, actual: Any) -> None:
        if expected != actual:
            reasons.append({
                "code": code,
                "message": message,
                "expected": expected,
                "actual": actual,
            })

    stored_doc = stored.get("document") or {}
    current_doc = current.get("document") or {}
    changed(
        "DOCUMENT_REVISION_CHANGED", "当前文档修订已变化",
        stored_doc.get("revision_id"), current_doc.get("revision_id"),
    )
    changed(
        "DOCUMENT_CONTENT_CHANGED", "当前文档正文已变化",
        stored_doc.get("content_hash"), current_doc.get("content_hash"),
    )
    changed(
        "WORKSPACE_METADATA_CHANGED", "工作区报告元数据已变化",
        stored_doc.get("metadata_hash"), current_doc.get("metadata_hash"),
    )
    changed(
        "WORKSPACE_VERSION_CHANGED", "工作区版本已变化",
        stored.get("workspace_version"), current.get("workspace_version"),
    )
    changed(
        "SOURCE_FILES_CHANGED", "原始资料文件、解析或复核状态已变化",
        (stored.get("sources") or {}).get("hash"),
        (current.get("sources") or {}).get("hash"),
    )
    changed(
        "TEMPLATE_VERSION_CHANGED", "报告模板版本已变化",
        stored.get("template_version"), current.get("template_version"),
    )
    changed(
        "PUBLISH_READINESS_CHANGED", "发布就绪度依据已变化",
        (stored.get("readiness") or {}).get("hash"),
        (current.get("readiness") or {}).get("hash"),
    )

    stored_finance = stored.get("finance") or {}
    current_finance = current.get("finance") or {}
    changed(
        "FINANCE_BINDING_CHANGED", "财务绑定已变化",
        (stored_finance.get("binding") or {}).get("hash"),
        (current_finance.get("binding") or {}).get("hash"),
    )
    changed(
        "FINANCE_RUN_REBOUND", "报告已绑定到不同财务 run",
        stored_finance.get("run_id"), current_finance.get("run_id"),
    )
    changed(
        "FINANCE_RUN_CHANGED", "绑定财务 run 内容已变化",
        stored_finance.get("run_hash"), current_finance.get("run_hash"),
    )
    changed(
        "FINANCE_PUBLISH_GATE_CHANGED", "财务正式发布门禁结果已变化",
        _canonical_hash(stored_finance.get("publish_gate") or {}),
        _canonical_hash(current_finance.get("publish_gate") or {}),
    )

    for name in _GOVERNED_SNAPSHOTS:
        changed(
            f"{name.upper()}_CHANGED", f"{name} 已变化",
            ((stored.get("artifacts") or {}).get(name) or {}).get("hash"),
            ((current.get("artifacts") or {}).get(name) or {}).get("hash"),
        )
    changed(
        "APPENDIX_FILES_CHANGED", "附表文件内容或可用性已变化",
        _canonical_hash(stored.get("appendix_files") or []),
        _canonical_hash(current.get("appendix_files") or []),
    )

    if stored.get("fingerprint") != current.get("fingerprint") and not reasons:
        reasons.append({
            "code": "ARTIFACT_BASIS_CHANGED",
            "message": "交付工件输入依据指纹已变化",
            "expected": stored.get("fingerprint"),
            "actual": current.get("fingerprint"),
        })
    return reasons


def _append_event(
    state: dict[str, Any],
    artifact_id: str,
    event: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    state.setdefault("history", []).append({
        "event_id": f"artevent_{uuid.uuid4().hex}",
        "artifact_id": artifact_id,
        "event": event,
        "details": copy.deepcopy(details or {}),
        "created_at": _now(),
    })


def _persist_new_record(
    workspace_id: str,
    state: dict[str, Any],
    record: dict[str, Any],
) -> None:
    artifact_id = str(record["artifact_id"])
    now = _now()
    kind = str(record["kind"])
    previous_id = str((state.setdefault("current", {})).get(kind) or "")
    previous = (state.setdefault("artifacts", {})).get(previous_id)
    if previous_id and isinstance(previous, dict) and previous_id != artifact_id:
        previous = copy.deepcopy(previous)
        previous["current"] = False
        previous["superseded_by"] = artifact_id
        previous["superseded_at"] = now
        previous["updated_at"] = now
        state["artifacts"][previous_id] = previous
        _append_event(
            state,
            previous_id,
            "superseded",
            details={"superseded_by": artifact_id, "kind": kind},
        )
    state.setdefault("artifacts", {})[artifact_id] = record
    state.setdefault("current", {})[kind] = artifact_id
    state["created_at"] = str(state.get("created_at") or now)
    state["updated_at"] = now
    _append_event(
        state, artifact_id, "created",
        details={
            "kind": record["kind"],
            "basis_fingerprint": record["basis_fingerprint"],
        },
    )
    _write_json_atomic(_state_path(workspace_id), state)


def _create(
    workspace_id: str,
    *,
    kind: str,
    template_version: str,
    operation_id: str = "",
    report_revision_id: str = "",
    document_snapshot: dict[str, Any] | None = None,
    expected_run_id: str = "",
) -> dict[str, Any]:
    workspace_id = _validate_workspace_id(workspace_id)
    _require_workspace(workspace_id)
    template_version = _validate_template_version(template_version)
    operation_id = str(operation_id or "").strip()
    if operation_id and not _SAFE_OPERATION_ID.fullmatch(operation_id):
        raise DeliverableArtifactError(
            "INVALID_OPERATION_ID", "交付工件操作标识不合法",
        )
    artifact_id = f"deliverable_{uuid.uuid4().hex}"
    final_root = _artifact_root(workspace_id, artifact_id)
    with _state_guard(workspace_id):
        state = _read_state(workspace_id)
        if operation_id:
            existing = next((
                item for item in (state.get("artifacts") or {}).values()
                if str(item.get("operation_id") or "") == operation_id
                and str(item.get("kind") or "") == kind
            ), None)
            if isinstance(existing, dict):
                if str(existing.get("report_revision_id") or "") != str(
                    report_revision_id or ""
                ):
                    raise DeliverableArtifactError(
                        "IDEMPOTENCY_CONFLICT",
                        "相同工件操作标识已绑定其他研报修订",
                    )
                replay = _refresh_record_locked(
                    workspace_id,
                    state,
                    str(existing.get("artifact_id") or ""),
                )
                return {**copy.deepcopy(replay), "idempotent_replay": True}
        basis, content, context = _capture_basis(
            workspace_id,
            template_version=template_version,
            report_revision_id=report_revision_id,
            document_snapshot=document_snapshot,
            expected_run_id=expected_run_id,
        )
        readiness = (basis.get("readiness") or {}).get("snapshot") or {}
        if kind == "formal":
            quality_issues = [
                *copy.deepcopy(readiness.get("blockers") or []),
                *_draft_basis_quality_issues(basis, context),
            ]
            _finance_blockers, diagnostic_issues = split_quality_codes(
                item.get("code")
                for item in quality_issues
                if isinstance(item, dict)
            )
            report_content = content
            blocker_summary = {
                "blockers": [],
                "quality_issues": diagnostic_issues,
                "warnings": copy.deepcopy(readiness.get("warnings") or []),
                "blocker_count": 0,
                "quality_issue_count": len(diagnostic_issues),
                "warning_count": len(readiness.get("warnings") or []),
            }
            subject = "可行性研究报告交付工件"
            keywords = ["可行性研究报告", "交付工件"]
            comments = "由服务端生成；资料缺口与质量问题记录于随附依据快照。"
        else:
            report_content, blocker_summary = _marker_markdown(
                content,
                readiness,
                additional_blockers=_draft_basis_quality_issues(basis, context),
            )
            subject = DRAFT_MARKER
            keywords = [DRAFT_MARKER, "非正式发布件"]
            comments = "专家参考稿/内部复核·非报批终稿；可含未决差异与警告；AI不担责；不得作为正式发布件。"

        raw_docx = doc_service.markdown_to_docx(report_content)
        title = str((context.get("meta") or {}).get("title") or "可行性研究报告")
        docx_bytes = _set_docx_metadata(
            raw_docx,
            title=f"{DRAFT_MARKER} - {title}" if kind == "draft" else title,
            subject=subject,
            keywords=keywords,
            comments=comments,
        )
        from lvke_mcp.domains.reports.docx_fonts import normalize_docx_fonts

        docx_bytes, docx_font_audit = normalize_docx_fonts(docx_bytes)

        second_basis, _second_content, second_context = _capture_basis(
            workspace_id,
            template_version=template_version,
            report_revision_id=report_revision_id,
            document_snapshot=document_snapshot,
            expected_run_id=expected_run_id,
        )
        if second_basis.get("fingerprint") != basis.get("fingerprint"):
            raise DeliverableArtifactError(
                "BASIS_CHANGED_DURING_EXPORT",
                "交付工件生成期间输入依据发生变化，请刷新后重试",
                details={
                    "before": basis.get("fingerprint"),
                    "after": second_basis.get("fingerprint"),
                },
            )

        files, support_warnings = _build_artifact_directory(
            workspace_id,
            artifact_id,
            kind=kind,
            docx_bytes=docx_bytes,
            basis=second_basis,
            blocker_summary=blocker_summary,
            context=second_context,
            docx_font_audit=docx_font_audit,
        )
        try:
            final_basis, _final_content, final_context = _capture_basis(
                workspace_id,
                template_version=template_version,
                report_revision_id=report_revision_id,
                document_snapshot=document_snapshot,
                expected_run_id=expected_run_id,
            )
            if final_basis.get("fingerprint") != second_basis.get("fingerprint"):
                raise DeliverableArtifactError(
                    "BASIS_CHANGED_DURING_EXPORT",
                    "交付工件装配期间输入依据发生变化，请刷新后重试",
                    details={
                        "before": second_basis.get("fingerprint"),
                        "after": final_basis.get("fingerprint"),
                    },
                )
        except Exception:
            if final_root.exists():
                shutil.rmtree(final_root, ignore_errors=True)
            raise
        now = _now()
        manifest_hash = next(
            (item.get("sha256") for item in files if item.get("role") == "manifest"),
            "",
        )
        index_hash = next(
            (item.get("sha256") for item in files if item.get("role") == "index"),
            "",
        )
        record = {
            "schema_version": SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "workspace_id": workspace_id,
            "kind": kind,
            "operation_id": operation_id,
            "status": "succeeded",
            "ok": True,
            "current": True,
            "template_version": template_version,
            "report_revision_id": report_revision_id,
            "evidence_policy": final_basis.get("evidence_policy"),
            "evidence_origin": final_basis.get("evidence_origin"),
            "project_fact_certified": final_basis.get("project_fact_certified"),
            "formal_promotion": copy.deepcopy(final_basis.get("formal_promotion")),
            "basis_fingerprint": final_basis.get("fingerprint"),
            "basis": final_basis,
            "document_revision_id": (final_basis.get("document") or {}).get(
                "revision_id"
            ),
            "finance_run_id": (final_basis.get("finance") or {}).get("run_id"),
            "run_id": (final_basis.get("finance") or {}).get("run_id"),
            "spec_hash": (
                ((final_basis.get("finance") or {}).get("run_snapshot") or {}).get(
                    "spec_hash"
                )
            ),
            "blocker_summary": blocker_summary,
            "support_file_warnings": support_warnings,
            "files": files,
            "manifest_hash": manifest_hash,
            "index_hash": index_hash,
            "integrity_status": "passed",
            "created_at": now,
            "updated_at": now,
            "invalidation_reasons": [],
        }
        try:
            _persist_new_record(workspace_id, state, record)
        except Exception:
            if final_root.exists():
                shutil.rmtree(final_root, ignore_errors=True)
            raise
    return copy.deepcopy(record)


def create_draft_export(
    workspace_id: str,
    *,
    template_version: str = DEFAULT_TEMPLATE_VERSION,
    operation_id: str = "",
) -> dict[str, Any]:
    """Create an immutable, visibly watermarked internal-review DOCX.

    Draft export intentionally permits readiness blockers and warnings.  They
    are rendered into the document and stored in its immutable manifest.
    """

    return _create(
        workspace_id,
        kind="draft",
        template_version=template_version,
        operation_id=operation_id,
    )


def create_deliverable_artifact(
    workspace_id: str,
    *,
    template_version: str = DEFAULT_TEMPLATE_VERSION,
    operation_id: str = "",
) -> dict[str, Any]:
    """Create a formal generic-feasibility artifact after all gates pass."""

    return _create(
        workspace_id,
        kind="formal",
        template_version=template_version,
        operation_id=operation_id,
    )


def _create_revision_bound_draft_export(
    workspace_id: str,
    *,
    report_revision_id: str,
    document_snapshot: dict[str, Any],
    expected_run_id: str = "",
    template_version: str = DEFAULT_TEMPLATE_VERSION,
) -> dict[str, Any]:
    return _create(
        workspace_id,
        kind="draft",
        template_version=template_version,
        report_revision_id=report_revision_id,
        document_snapshot=document_snapshot,
        expected_run_id=expected_run_id,
    )


def _create_revision_bound_deliverable_artifact(
    workspace_id: str,
    *,
    report_revision_id: str,
    document_snapshot: dict[str, Any],
    expected_run_id: str = "",
    template_version: str = DEFAULT_TEMPLATE_VERSION,
) -> dict[str, Any]:
    return _create(
        workspace_id,
        kind="formal",
        template_version=template_version,
        report_revision_id=report_revision_id,
        document_snapshot=document_snapshot,
        expected_run_id=expected_run_id,
    )


def _invalidate_locked(
    workspace_id: str,
    state: dict[str, Any],
    record: dict[str, Any],
    reasons: list[dict[str, Any]],
) -> dict[str, Any]:
    if record.get("status") == "invalidated":
        return record
    now = _now()
    artifact_id = str(record["artifact_id"])
    previous_status = str(record.get("status") or "")
    record["status"] = "invalidated"
    record["current"] = False
    record["integrity_status"] = (
        "failed" if any(
            "FILE_" in str(item.get("code") or "")
            for item in reasons
        )
        else record.get("integrity_status") or "passed"
    )
    record["invalidation_reasons"] = copy.deepcopy(reasons)
    record["invalidated_at"] = now
    record["updated_at"] = now
    record["previous_status"] = previous_status
    state.setdefault("artifacts", {})[artifact_id] = record
    kind = str(record.get("kind") or "")
    if (state.setdefault("current", {})).get(kind) == artifact_id:
        state["current"][kind] = ""
    state["updated_at"] = now
    _append_event(
        state,
        artifact_id,
        "invalidated",
        details={"previous_status": previous_status, "reasons": reasons},
    )
    _write_json_atomic(_state_path(workspace_id), state)
    return record


def _refresh_record_locked(
    workspace_id: str,
    state: dict[str, Any],
    artifact_id: str,
) -> dict[str, Any]:
    raw = (state.get("artifacts") or {}).get(artifact_id)
    if not isinstance(raw, dict):
        raise DeliverableArtifactError("ARTIFACT_NOT_FOUND", "交付工件不存在")
    record = copy.deepcopy(raw)
    root = _artifact_root(workspace_id, artifact_id)
    integrity_failures = _verify_files(root, record.get("files") or [])
    reasons: list[dict[str, Any]] = []
    for failure in integrity_failures:
        reasons.append({
            "code": str(failure.get("code") or "ARTIFACT_INTEGRITY_FAILED"),
            "message": "交付工件文件完整性校验失败",
            "details": copy.deepcopy(failure),
        })
    try:
        report_revision_id = str(record.get("report_revision_id") or "")
        supplied_snapshot = None
        expected_run_id = str(record.get("finance_run_id") or record.get("run_id") or "")
        if report_revision_id:
            from lvke_mcp.adapters.report_repository import REVISION_STORE

            revision = REVISION_STORE.get(workspace_id, report_revision_id)
            supplied_snapshot = (revision or {}).get("payload", {}).get("document_snapshot")
            if not isinstance(supplied_snapshot, dict):
                raise DeliverableArtifactError(
                    "DOCUMENT_SNAPSHOT_INVALID",
                    "工件绑定的研报修订快照不存在",
                )
        current_basis, _content, _context = _capture_basis(
            workspace_id,
            template_version=str(record.get("template_version") or DEFAULT_TEMPLATE_VERSION),
            report_revision_id=report_revision_id,
            document_snapshot=supplied_snapshot,
            expected_run_id=expected_run_id,
        )
        reasons.extend(_basis_change_reasons(record.get("basis") or {}, current_basis))
    except DeliverableArtifactError as exc:
        reasons.append({
            "code": "CURRENT_BASIS_UNAVAILABLE",
            "message": "当前交付依据不可用",
            "details": {"error": exc.code, **copy.deepcopy(exc.details)},
        })
    if reasons:
        record = _invalidate_locked(
            workspace_id,
            state,
            record,
            reasons,
        )
    return record
