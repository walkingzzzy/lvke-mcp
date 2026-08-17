"""docx 导出编排。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote



from .base import (
    _failure,
    _resolve_revision_record,
)

from .sections import (
    validate,
)


def export_docx(
    workspace_id: str,
    revision_id: str,
    kind: str,
    mirror_to_project: bool = False,
) -> dict[str, Any]:
    record, native_alias = _resolve_revision_record(
        workspace_id,
        revision_id,
    )
    if record is None:
        return _failure("revision_not_found", "未找到研报修订")
    if kind not in {"draft", "formal_candidate"}:
        return _failure("invalid_artifact_kind", "kind 必须为 draft 或 formal_candidate")
    validation: dict[str, Any] | None = None
    if kind == "formal_candidate":
        validation = validate(
            workspace_id,
            revision_id,
        )
    from lvke_mcp.domains.reports import artifacts

    payload = record.get("payload") or {}
    document_snapshot = payload.get("document_snapshot")
    if not isinstance(document_snapshot, dict) or not isinstance(
        document_snapshot.get("content"), str,
    ):
        return _failure(
            "document_snapshot_missing",
            "指定研报修订缺少不可变正文快照，拒绝导出",
        )
    resolved_revision_id = str(record.get("object_id") or revision_id)
    expected_run_id = str((payload.get("upstream") or {}).get("run_id") or "")

    try:
        created = (
            artifacts._create_revision_bound_deliverable_artifact(  # noqa: SLF001
                workspace_id,
                report_revision_id=resolved_revision_id,
                document_snapshot=document_snapshot,
                expected_run_id=expected_run_id,
            )
            if kind == "formal_candidate"
            else artifacts._create_revision_bound_draft_export(  # noqa: SLF001
                workspace_id,
                report_revision_id=resolved_revision_id,
                document_snapshot=document_snapshot,
                expected_run_id=expected_run_id,
            )
        )
    except Exception as exc:  # noqa: BLE001
        code = str(getattr(exc, "code", "artifact_blocked"))
        return _failure(code, str(getattr(exc, "message", "工件生成被现有交付门禁阻断")))
    artifact_id = str(created.get("artifact_id") or "")
    docx_font_audit: dict[str, Any] = {}
    font_warnings: list[str] = []
    try:
        from lvke_mcp.domains.reports.docx_fonts import audit_docx_fonts

        docx_path = artifacts._artifact_root(  # noqa: SLF001
            workspace_id,
            artifact_id,
        ) / "report.docx"
        docx_font_audit = audit_docx_fonts(docx_path.read_bytes())
        if (
            docx_font_audit.get("invalid_locale_font_count")
            or not docx_font_audit.get("portable_cjk_fonts")
        ):
            font_warnings.append("DOCX 字体审计存在可移植中文渲染问题")
    except Exception as exc:  # noqa: BLE001
        docx_font_audit = {"audit_error": type(exc).__name__}
        font_warnings.append("DOCX 字体审计未完成，工件仍已生成")
    base = f"lvke://report-generation/workspaces/{workspace_id}/artifacts/{artifact_id}"
    file_uris = [
        f"{base}/files/{quote(str(item.get('name') or item.get('filename') or ''), safe='')}"
        for item in (created.get("files") or [])
        if item.get("name") or item.get("filename")
    ]
    # External project-folder persistence is an explicit side effect.  The
    # authoritative Resource remains available regardless of this opt-in.
    mirror_paths: list[str] = []
    if mirror_to_project and kind == "draft":
        try:
            from lvke_mcp.domains.reports import artifact_mirror

            src_dir = artifacts._artifact_root(  # noqa: SLF001
                workspace_id,
                artifact_id,
            )
            mirrored = artifact_mirror.mirror_dir(
                workspace_id, src_dir, category=f"report/{artifact_id}"
            )
            if mirrored:
                mirror_paths = [str(mirrored)]
        except Exception:  # noqa: BLE001 - 镜像绝不影响交付
            mirror_paths = []
    warnings = (
        ["formal_candidate 已通过当前 revision 的确定性工程校验"]
        if kind == "formal_candidate"
        else ["draft 工件可能包含未解决的完整度问题"]
    )
    if native_alias:
        warnings.append("native_revision_id 输入已弃用；请改用 report_revision_id")
    task_status = str((record.get("payload") or {}).get("task_status") or "")
    if kind == "draft" and task_status in {"failed", "cancelled"}:
        warnings.append("起草任务未完成；该草稿可能只含占位或既有正文")
    warnings.extend(font_warnings)
    if validation:
        warnings.extend(str(item) for item in validation.get("warnings") or [])
        warnings.extend(
            f"质量提示：{item}" for item in validation.get("quality_issues") or []
        )
    if mirror_to_project and kind == "formal_candidate":
        warnings.append("formal_candidate 不执行项目目录镜像；请通过 Resource 读取工件")
    return {
        "success": True,
        "status": "ok",
        "artifact_id": artifact_id,
        "report_revision_id": resolved_revision_id,
        "artifact_kind": created.get("kind"),
        "docx_font_audit": docx_font_audit,
        "validation_complete": kind == "formal_candidate",
        # 交付物落盘绝对目录：report.docx / basis_snapshot.json / manifest.json
        # / index.json 均在此目录下（见 artifacts._artifact_root）。
        "deliverable_path": str(artifacts._artifact_root(workspace_id, artifact_id)),  # noqa: SLF001
        "resource_uris": [base, *file_uris],
        "project_mirror_paths": mirror_paths,
        "warnings": warnings,
        "blockers": [],
        "next_actions": [],
    }
