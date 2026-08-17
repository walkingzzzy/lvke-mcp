"""审查导出：markdown/docx/xlsx 与导出完整性判定。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lvke_mcp.runtime.storage import canonical_json, require_safe_id, sha256_json, utc_now
from lvke_mcp.runtime.workspace import deliverable_dir
from lvke_mcp.servers.lvke_deliverable_review.contracts import normalize_target
from lvke_mcp.servers.lvke_deliverable_review.store import STORE

from .base import (
    EXPORT_STORE,
    _REPORT_ARTIFACT_DOMAINS,
    _blocked,
    _message,
    _next_actions,
    _ok,
    _parse_timestamp,
    _write,
)

from .events import (
    _project,
)


def _export_root(workspace_id: str, export_id: str) -> Path:
    """审查导出（json/md/docx/xlsx）落盘根，统一到仓库 ``lvke产出/``。"""
    return (
        deliverable_dir(
            require_safe_id(workspace_id, "workspace_id"),
            "review",
            "exports",
        )
        / require_safe_id(export_id, "export_id")
    )


def _write_once_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("immutable_export_conflict")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _review_markdown(state: dict[str, Any]) -> str:
    lines = [
        f"# 交付物审查报告 {state['review_id']}", "",
        f"- 目标：`{(state.get('target') or {}).get('target_type')}` / `{(state.get('target') or {}).get('target_id')}`",
        f"- 目标哈希：`{(state.get('target') or {}).get('target_sha256')}`",
        f"- 规则包：`{(state.get('rule_pack') or {}).get('rule_pack_id')}` / `{(state.get('rule_pack') or {}).get('version')}`",
        f"- 总体结论：`{state.get('overall_verdict')}`",
        f"- 校验状态：`{state.get('validation_status')}`",
        f"- 校验完成：`{str(bool(state.get('validation_complete'))).lower()}`", "",
        "## Findings", "",
    ]
    if not state.get("findings"):
        lines.append("无 findings。")
    for row in state.get("findings") or []:
        lines.extend([
            f"### {row.get('severity')} {row.get('rule_id')} ({row.get('status')})", "",
            str(row.get("message") or ""), "",
            f"- 定位：`{canonical_json(row.get('target_location') or {})}`",
            f"- 期望：`{canonical_json(row.get('expected'))}`",
            f"- 实际：`{canonical_json(row.get('actual'))}`",
            f"- 差额/容差：`{canonical_json(row.get('difference'))}` / `{canonical_json(row.get('tolerance'))}`",
            f"- 检查分类：`{row.get('review_area') or '-'}`",
            f"- 整改建议：{row.get('remediation') or '-'}", "",
        ])
    lines.extend(["## 不可核查项", ""])
    if state.get("incomplete_reasons"):
        lines.extend(f"- `{item}`" for item in state["incomplete_reasons"])
    else:
        lines.append("无。")
    return "\n".join(lines) + "\n"


def _review_docx(state: dict[str, Any]) -> bytes:
    from docx import Document

    document = Document()
    document.add_heading(f"交付物审查报告 {state['review_id']}", level=1)
    document.add_paragraph(f"总体结论：{state.get('overall_verdict')}  审查状态：{state.get('review_status')}")
    document.add_paragraph(f"目标：{canonical_json(state.get('target') or {})}")
    document.add_heading("Findings", level=2)
    for row in state.get("findings") or []:
        document.add_heading(f"{row.get('severity')} {row.get('rule_id')} ({row.get('status')})", level=3)
        document.add_paragraph(str(row.get("message") or ""))
        document.add_paragraph(f"定位：{canonical_json(row.get('target_location') or {})}")
        document.add_paragraph(f"期望：{canonical_json(row.get('expected'))}")
        document.add_paragraph(f"实际：{canonical_json(row.get('actual'))}")
        document.add_paragraph(f"整改建议：{row.get('remediation') or '-'}")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _findings_xlsx(state: dict[str, Any]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "findings"
    headers = [
        "finding_id", "rule_id", "rule_pack_version", "category", "severity", "blocking",
        "status", "confidence", "message", "expected", "actual", "difference", "tolerance",
        "target_location", "evidence", "standard_basis", "review_area", "remediation",
    ]
    sheet.append(headers)
    for row in state.get("findings") or []:
        sheet.append([
            row.get(key) if isinstance(row.get(key), (str, int, float, bool, type(None)))
            else canonical_json(row.get(key))
            for key in headers
        ])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(60, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _export_file_uri(workspace_id: str, export_id: str, filename: str) -> str:
    return f"lvke://deliverable-review/workspaces/{workspace_id}/exports/{export_id}/files/{quote(filename)}"


def _export_resource_uri(workspace_id: str, export_id: str) -> str:
    return f"lvke://deliverable-review/workspaces/{workspace_id}/exports/{export_id}"


def _export_review_locked(
    workspace_id: str,
    review_id: str,
    requested_formats: Any,
) -> dict[str, Any]:
    """Export one validation snapshot while holding its mutation lock."""

    try:
        state = _project(workspace_id, review_id)
    except ValueError:
        return _blocked("review_not_found", _message("review_not_found"))
    quality_issues: list[str] = []
    if not state.get("validation_complete"):
        quality_issues.append("review_validation_incomplete")
    for previous in state.get("exports") or []:
        integrity_reasons = _export_integrity_reasons(
            workspace_id,
            {
                "review_id": review_id,
                "export_id": previous.get("export_id"),
                "export_record_id": previous.get("export_record_id"),
                "export_record_hash": previous.get("export_record_hash"),
                "export_basis_hash": previous.get("export_basis_hash"),
                "export_files": previous.get("files"),
            },
        )
        if integrity_reasons:
            return _blocked(
                "review_export_integrity_failed",
                "既有审查导出记录或文件完整性校验失败",
                review_id=review_id,
                integrity_reasons=integrity_reasons,
            )
    requested = list(
        requested_formats or ["json", "markdown", "docx", "xlsx"]
    )
    allowed = {"json", "markdown", "docx", "xlsx"}
    if not requested or any(item not in allowed for item in requested):
        return _blocked("export_format_invalid", "导出格式仅支持 json、markdown、docx、xlsx")
    export_basis = {
        "review_id": review_id,
        "event_chain_hash": state.get("event_chain_hash"),
        "formats": sorted(set(requested)),
        "review_status": state.get("review_status"),
        "overall_verdict": state.get("overall_verdict"),
    }
    export_id = (
        "rvexp_" + sha256_json(export_basis).removeprefix("sha256:")[:24]
    )
    output = _export_root(workspace_id, export_id)
    payloads: dict[str, tuple[str, bytes, str]] = {
        "json": (
            "review.json",
            json.dumps(
                state, ensure_ascii=False, indent=2, default=str,
            ).encode("utf-8"),
            "application/json",
        ),
        "markdown": (
            "review.md",
            _review_markdown(state).encode("utf-8"),
            "text/markdown; charset=utf-8",
        ),
        "docx": (
            "review.docx",
            _review_docx(state),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        "xlsx": (
            "findings.xlsx",
            _findings_xlsx(state),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    }
    files = []
    for format_name in sorted(set(requested)):
        filename, content, media_type = payloads[format_name]
        _write_once_bytes(output / filename, content)
        files.append({
            "format": format_name,
            "filename": filename,
            "media_type": media_type,
            "bytes": len(content),
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            "uri": _export_file_uri(workspace_id, export_id, filename),
        })
    record = EXPORT_STORE.put(
        workspace_id,
        {
            **export_basis,
            "export_id": export_id,
            "files": files,
        },
        producer="lvke-deliverable-review.review_export",
        source_ids=[review_id],
        basis=export_basis,
        schema_version="deliverable_review_export.v1",
    )
    export_envelope = {
        "review_id": review_id,
        "export_id": export_id,
        "export_record_id": record.get("object_id"),
        "export_record_hash": record.get("content_hash"),
        "export_basis_hash": record.get("basis_hash"),
        "export_files": files,
    }
    integrity_reasons = _export_integrity_reasons(
        workspace_id,
        export_envelope,
    )
    if integrity_reasons:
        return _blocked(
            "review_export_integrity_failed",
            "新生成的审查导出记录或文件完整性校验失败",
            review_id=review_id,
            integrity_reasons=integrity_reasons,
        )
    STORE.append(
        workspace_id,
        review_id,
        "review_exported",
        {
            "export_id": export_id,
            "export_record_id": record["object_id"],
            "export_record_hash": record["content_hash"],
            "export_basis_hash": record["basis_hash"],
            "files": files,
            "exported_at": utc_now(),
        },
    )
    return _ok(
        review_id=review_id,
        export_id=export_id,
        export_record_id=record["object_id"],
        export_record_hash=record["content_hash"],
        export_basis_hash=record["basis_hash"],
        files=files,
        resource_uris=[_export_resource_uri(workspace_id, export_id), *[row["uri"] for row in files]],
        quality_issues=quality_issues,
        warnings=[f"质量提示：{item}" for item in quality_issues],
        blockers=[],
        next_actions=_next_actions(
            _project(workspace_id, review_id, check_freshness=False)
        ),
    )


def export_review(args: dict[str, Any]) -> dict[str, Any]:
    def execute(workspace_id: str) -> dict[str, Any]:
        return _export_review_locked(
            workspace_id,
            str(args.get("review_id") or ""),
            args.get("formats"),
        )

    return _write("review_export", args, execute)


def _release_export_integrity_reasons(
    workspace_id: str,
    release_payload: dict[str, Any],
) -> list[str]:
    # Records produced before export files were embedded remain readable; all
    # current release records include the field and are verified strictly.
    if "export_files" not in release_payload:
        return []
    export_id = str(release_payload.get("export_id") or "")
    rows = release_payload.get("export_files")
    if not export_id or not isinstance(rows, list) or not rows:
        return ["release_export_manifest_invalid"]
    try:
        root = _export_root(workspace_id, export_id)
    except ValueError:
        return ["release_export_id_invalid"]
    reasons: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            reasons.append("release_export_entry_invalid")
            continue
        filename = str(row.get("filename") or "")
        if (
            not filename
            or filename in seen
            or Path(filename).name != filename
            or filename in {".", ".."}
        ):
            reasons.append("release_export_filename_invalid")
            continue
        seen.add(filename)
        path = root / filename
        if not path.is_file():
            reasons.append(f"release_export_missing:{filename}")
            continue
        try:
            content = path.read_bytes()
            expected_bytes = int(row.get("bytes") or -1)
        except (OSError, TypeError, ValueError):
            reasons.append(f"release_export_unreadable:{filename}")
            continue
        if expected_bytes != len(content):
            reasons.append(f"release_export_size_mismatch:{filename}")
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if str(row.get("sha256") or "") != digest:
            reasons.append(f"release_export_hash_mismatch:{filename}")
    return sorted(set(reasons))


def _export_record_integrity_reasons(
    workspace_id: str,
    export_envelope: dict[str, Any],
) -> list[str]:
    export_record_id = str(export_envelope.get("export_record_id") or "")
    if not export_record_id:
        return ["release_export_record_id_missing"]
    try:
        record = EXPORT_STORE.get(workspace_id, export_record_id)
    except ValueError:
        record = None
    if record is None:
        return ["release_export_record_missing"]
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ["release_export_record_payload_invalid"]
    export_basis = {
        key: deepcopy(payload.get(key))
        for key in (
            "review_id",
            "event_chain_hash",
            "formats",
            "review_status",
            "overall_verdict",
        )
    }
    content_hash = sha256_json(payload)
    expected_record_id = (
        f"{EXPORT_STORE.id_prefix}_"
        f"{content_hash.removeprefix('sha256:')[:24]}"
    )
    expected_uri = EXPORT_STORE.uri(workspace_id, expected_record_id)
    declared_record_hash = str(
        export_envelope.get("export_record_hash") or ""
    )
    declared_basis_hash = str(
        export_envelope.get("export_basis_hash") or ""
    )
    reasons: list[str] = []
    if record.get("content_hash") != content_hash:
        reasons.append("release_export_record_content_hash_mismatch")
    if record.get("basis_hash") != sha256_json(export_basis):
        reasons.append("release_export_record_basis_hash_mismatch")
    if record.get("object_id") != expected_record_id:
        reasons.append("release_export_record_object_id_mismatch")
    if export_record_id != expected_record_id:
        reasons.append("release_export_record_reference_mismatch")
    if record.get("workspace_id") != workspace_id:
        reasons.append("release_export_record_workspace_mismatch")
    if record.get("resource_uri") != expected_uri:
        reasons.append("release_export_record_uri_mismatch")
    if record.get("producer") != "lvke-deliverable-review.review_export":
        reasons.append("release_export_record_producer_mismatch")
    if record.get("schema_version") != "deliverable_review_export.v1":
        reasons.append("release_export_record_schema_mismatch")
    if declared_record_hash and declared_record_hash != content_hash:
        reasons.append("release_export_record_declared_hash_mismatch")
    if declared_basis_hash and declared_basis_hash != sha256_json(export_basis):
        reasons.append("release_export_record_declared_basis_mismatch")
    if str(payload.get("review_id") or "") != str(
        export_envelope.get("review_id") or ""
    ):
        reasons.append("release_export_record_review_mismatch")
    if str(payload.get("export_id") or "") != str(
        export_envelope.get("export_id") or ""
    ):
        reasons.append("release_export_record_export_id_mismatch")
    if payload.get("files") != export_envelope.get("export_files"):
        reasons.append("release_export_record_files_mismatch")
    if str(export_envelope.get("review_id") or "") not in {
        str(item) for item in record.get("source_ids") or []
    }:
        reasons.append("release_export_record_source_mismatch")
    return sorted(set(reasons))


def _export_integrity_reasons(
    workspace_id: str,
    export_envelope: dict[str, Any],
) -> list[str]:
    return sorted(set([
        *_export_record_integrity_reasons(workspace_id, export_envelope),
        *_release_export_integrity_reasons(workspace_id, export_envelope),
    ]))


def latest_review_for_target(
    workspace_id: str,
    target_type: str,
    target_id: str,
    *,
    artifact_domain: str = "",
) -> dict[str, Any] | None:
    """Return the latest review state after applying freshness projection."""

    try:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        normalized = normalize_target({
            "target_type": target_type,
            "target_id": target_id,
        })
    except ValueError:
        return None
    if normalized["target_type"] == "report_artifact":
        if artifact_domain not in _REPORT_ARTIFACT_DOMAINS:
            return None
    elif artifact_domain:
        return None

    candidates: list[dict[str, Any]] = []
    for review_id in STORE.review_ids(workspace_id):
        try:
            state = _project(workspace_id, review_id)
        except (OSError, ValueError):
            continue
        target = state.get("target") or {}
        if (
            target.get("target_type") != normalized["target_type"]
            or str(target.get("target_id") or "") != normalized["target_id"]
        ):
            continue
        if normalized["target_type"] == "report_artifact":
            target_spec = state.get("target_spec") or {}
            if target_spec.get("artifact_domain") != artifact_domain:
                continue
        candidates.append(state)
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            _parse_timestamp(row.get("created_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            str(row.get("review_id") or ""),
        ),
        reverse=True,
    )
    return deepcopy(candidates[0])
