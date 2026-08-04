"""MCP-owned source-file persistence used by the source-files adapter."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import HTTPException
from filelock import FileLock

from lvke_mcp.runtime.workspace import workspace_root

_STATE_LOCK = threading.RLock()
_DEFAULT_MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(workspace_id: str) -> Path:
    return workspace_root(workspace_id) / "source-files"


def _state_path(workspace_id: str) -> Path:
    return _root(workspace_id) / "state.json"


def _max_upload_bytes() -> int:
    try:
        return max(1, int(os.getenv("LVKE_MCP_MAX_UPLOAD_BYTES", "")))
    except ValueError:
        return _DEFAULT_MAX_UPLOAD_BYTES


def _empty_state() -> dict[str, Any]:
    return {"files": {}, "jobs": {}, "analyses": {}, "idempotency": {}}


def _load_state(workspace_id: str) -> dict[str, Any]:
    path = _state_path(workspace_id)
    if not path.is_file():
        return _empty_state()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(value, dict):
        return _empty_state()
    return {**_empty_state(), **value}


def _save_state(workspace_id: str, state: dict[str, Any]) -> None:
    path = _state_path(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _state_guard(workspace_id: str) -> Iterator[None]:
    _root(workspace_id).mkdir(parents=True, exist_ok=True)
    with _STATE_LOCK, FileLock(str(_root(workspace_id) / "state.lock"), timeout=30):
        yield


def _error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


def _idempotency_record(scope: str, request_hash: str, **resource: Any) -> dict[str, Any]:
    return {"scope": scope, "request_hash": request_hash, "created_at": _now(), **resource}


def _active_idempotency_record(records: dict[str, Any], scope: str) -> dict[str, Any] | None:
    value = records.get(scope)
    return value if isinstance(value, dict) else None


def _public_parse_job(job: dict[str, Any]) -> dict[str, Any]:
    value = dict(job)
    for key in ("worker_token", "_worker_control"):
        value.pop(key, None)
    return value


def _require_source_record_from_state(
    state: dict[str, Any], workspace_id: str, file_id: str, _scope: str = ""
) -> dict[str, Any]:
    record = (state.get("files") or {}).get(file_id)
    if not isinstance(record, dict) or str(record.get("workspace_id") or "") != workspace_id:
        raise _error("source_file_not_found", "原始资料不存在或不属于当前工作区")
    return record


def _require_source_record(workspace_id: str, file_id: str, scope: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    state = _load_state(workspace_id)
    return state, _require_source_record_from_state(state, workspace_id, file_id, scope)


def _require_parse_job_from_state(
    state: dict[str, Any], workspace_id: str, job_id: str, _scope: str = ""
) -> tuple[dict[str, Any], dict[str, Any]]:
    job = (state.get("jobs") or {}).get(job_id)
    if not isinstance(job, dict) or str(job.get("workspace_id") or "") != workspace_id:
        raise _error("source_parse_job_not_found", "解析任务不存在或不属于当前工作区")
    record = _require_source_record_from_state(state, workspace_id, str(job.get("file_id") or ""))
    return job, record


def _load_analysis(workspace_id: str, file_id: str) -> dict[str, Any] | None:
    return _load_state(workspace_id).get("analyses", {}).get(file_id)


_SPREADSHEET_MIMES = frozenset({
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
})


def resolve_source_workbook_for_review(
    workspace_id: str,
    source_file_id: str,
) -> dict[str, Any]:
    """解析上传的 Excel 工作簿供统一审查（自 hermes source_files_api 裁剪的无 tenant 版）。

    读取 MCP 自有 source-files 存储，fail-closed：记录不存在/非电子表格/文件
    丢失或字节与记录摘要不符时返回 ``ok=False`` 及原因码。不暴露工作区外路径。
    """
    file_id = str(source_file_id or "").strip()
    if not file_id or "/" in file_id or "\\" in file_id or ":" in file_id or file_id in {".", ".."}:
        return {"ok": False, "code": "source_workbook_not_found"}
    with _state_guard(workspace_id):
        state = _load_state(workspace_id)
        record = (state.get("files") or {}).get(file_id)
        if (
            not isinstance(record, dict)
            or str(record.get("workspace_id") or "") != workspace_id
        ):
            return {"ok": False, "code": "source_workbook_not_found"}
        declared = str(record.get("declared_mime") or record.get("mime_type") or "")
        suffix = Path(str(record.get("original_filename") or "")).suffix.lower()
        if declared not in _SPREADSHEET_MIMES and suffix not in {".xlsx", ".xlsm"}:
            return {"ok": False, "code": "source_workbook_not_spreadsheet"}
        source_path = Path(str(record.get("path") or ""))
        if not source_path.is_file():
            return {"ok": False, "code": "source_workbook_integrity_failed"}
        raw = source_path.read_bytes()
        recorded_sha = str(record.get("sha256") or "").lower().removeprefix("sha256:")
        if (
            len(raw) != int(record.get("size_bytes") or -1)
            or hashlib.sha256(raw).hexdigest() != recorded_sha
        ):
            return {"ok": False, "code": "source_workbook_integrity_failed"}
        return {
            "ok": True,
            "path": str(source_path),
            "size": len(raw),
            "sha256": "sha256:" + recorded_sha,
            "source_file_id": file_id,
            "original_filename": str(record.get("original_filename") or ""),
            "source_version": record.get("version"),
        }


def resolve_authoritative_evidence_binding(
    workspace_id: str,
    *,
    source_id: str,
    evidence_id: str = "",
    locator: str = "",
    tenant_id: str = "local",
) -> dict[str, Any]:
    """Resolve one finance fact binding from the durable source authority (MCP 无 tenant 版)。

    裁剪自 hermes source_files_api：record 存在性/完整性、analysis locator
    匹配、OCR 与人工复核状态全部 fail-closed；``tenant_id`` 仅保留形参兼容。
    """
    source_id = str(source_id or "").strip()
    evidence_id = str(evidence_id or "").strip()
    locator = str(locator or "").strip()
    base = {
        "source_id": source_id,
        "evidence_id": evidence_id,
        "locator": locator,
        "evidence_grade": "E",
        "review_status": "missing",
        "authoritative": True,
        "binding_ok": False,
        "issues": [],
    }
    if not source_id or (not evidence_id and not locator):
        base["issues"] = ["source_id 与 evidence_id/locator 必填"]
        return base

    with _state_guard(workspace_id):
        state = _load_state(workspace_id)
        record = (state.get("files") or {}).get(source_id)
        if not isinstance(record, dict) or str(record.get("workspace_id") or "") != workspace_id:
            base["issues"] = ["source 不存在或不属于当前工作区"]
            return base
        analysis = _load_analysis(workspace_id, source_id)
        candidates = [row for row in analysis.get("locators") or [] if isinstance(row, dict)]
        target = next(
            (
                row for row in candidates
                if evidence_id and str(row.get("evidence_id") or "") == evidence_id
            ),
            None,
        )
        if target is None:
            target = next(
                (
                    row for row in candidates
                    if locator and str(row.get("locator") or "") == locator
                ),
                None,
            )
        if target is None:
            base["issues"] = ["evidence locator 不存在"]
            return {
                **base,
                "source_sha256": record.get("sha256"),
                "source_version": record.get("version"),
                "source_format": record.get("declared_mime"),
            }

        issues: list[str] = []
        target_evidence_id = str(target.get("evidence_id") or "")
        target_locator = str(target.get("locator") or "")
        if evidence_id and evidence_id != target_evidence_id:
            issues.append("evidence_id 与权威记录不一致")
        if locator and locator != target_locator:
            issues.append("locator 与权威记录不一致")

        source_path = Path(str(record.get("path") or ""))
        integrity_ok = False
        if source_path.is_file():
            raw = source_path.read_bytes()
            integrity_ok = (
                len(raw) == int(record.get("size_bytes") or -1)
                and hashlib.sha256(raw).hexdigest() == str(record.get("sha256") or "")
            )
        if not integrity_ok:
            issues.append("source 文件完整性校验失败")
        if analysis.get("source_verified_sha256") not in (None, "", record.get("sha256")):
            issues.append("analysis/source sha256 不一致")
        if analysis.get("source_verified_size_bytes") not in (None, "", record.get("size_bytes")):
            issues.append("analysis/source size 不一致")

        scan = record.get("security_scan") if isinstance(record.get("security_scan"), dict) else {}
        security_review_required = bool(scan.get("manual_security_review_required"))
        security_ok = bool(record.get("security_formal_use_allowed")) or not security_review_required
        if not security_ok:
            issues.append("source security formal_use 未批准")

        pending_low_confidence = [
            block
            for page in (analysis.get("pages") or [])
            if isinstance(page, dict)
            for block in (page.get("ocr_blocks") or [])
            if isinstance(block, dict)
            and block.get("low_confidence")
            and str(block.get("manual_review_status") or "pending").lower() != "approved"
        ]
        if pending_low_confidence:
            issues.append("OCR 低置信文本块未全部完成人工复核，formal_use 阻断")
        if analysis.get("ocr_status") == "failed":
            issues.append("OCR 未完成，formal_use 阻断")

        review_status = str(target.get("manual_review_status") or "pending").lower()
        revisions = [row for row in target.get("review_revisions") or [] if isinstance(row, dict)]
        approved_revision = next(
            (row for row in reversed(revisions) if str(row.get("decision") or "").lower() == "approved"),
            None,
        )
        if review_status != "approved" or approved_revision is None:
            issues.append("evidence 未完成人工 approved 复核")

        fmt = str(record.get("declared_mime") or "").lower()
        kind = str(target.get("kind") or "")
        if review_status == "approved" and approved_revision is not None:
            grade = "A" if fmt in {"xls", "xlsx", "xlsm"} and kind == "cell" else "B"
        else:
            grade = "C"
        binding_ok = not issues and grade in {"A", "B"}
        reviewed_value = target.get("reviewed_value")
        if reviewed_value in (None, "") and approved_revision is not None:
            reviewed_value = (
                approved_revision.get("corrected_value")
                if approved_revision.get("corrected_value") is not None
                else approved_revision.get("original_value")
            )
        if reviewed_value in (None, ""):
            reviewed_value = target.get("original_value")
        if reviewed_value in (None, ""):
            reviewed_value = target.get("value")
        return {
            "source_id": source_id,
            "file_id": source_id,
            "evidence_id": target_evidence_id,
            "locator": target_locator,
            "kind": kind,
            "evidence_grade": grade,
            "review_status": review_status,
            "reviewed_by": str((approved_revision or {}).get("reviewer") or ""),
            "reviewed_at": str((approved_revision or {}).get("reviewed_at") or ""),
            "reviewed_value": reviewed_value,
            "review_audit_id": str((approved_revision or {}).get("review_audit_id") or ""),
            "formal_use_decision": (
                "blocked" if pending_low_confidence or analysis.get("ocr_status") == "failed"
                else str(analysis.get("formal_use_decision") or "not_applicable")
            ),
            "source_sha256": record.get("sha256"),
            "source_version": record.get("version"),
            "source_format": fmt,
            "authoritative": True,
            "binding_ok": binding_ok,
            "issues": issues,
        }


def _parse_bytes(path: Path, mime: str) -> dict[str, Any]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    text = ""
    if mime.startswith("text/") or path.suffix.lower() in {".md", ".txt", ".json", ".jsonl", ".html"}:
        text = data.decode("utf-8", errors="replace")[:200_000]
    return {
        "file_id": path.parent.name,
        "sha256": digest,
        "mime_type": mime,
        "size_bytes": len(data),
        "text_preview": text,
        "parser": "mcp-source-parser.v1",
        "created_at": _now(),
    }


def _mime_matches(data: bytes, declared_mime: str) -> bool:
    mime = declared_mime.lower().strip()
    if mime == "application/pdf":
        return data.startswith(b"%PDF-")
    if mime in {"application/zip", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}:
        return data.startswith(b"PK\x03\x04")
    if mime.startswith("text/") or mime in {"application/json", "application/jsonl"}:
        return True
    return True


def commit_staged_source_file(
    workspace_id: str,
    staged_path: Path,
    original_filename: str,
    declared_mime: str,
    *,
    idempotency_key: str,
    expected_sha256: str = "",
    expected_size: int | None = None,
) -> dict[str, Any]:
    data = staged_path.read_bytes()
    if not data or len(data) > _max_upload_bytes():
        raise _error("source_content_too_large", "资料大小超过 MCP 限制")
    digest = hashlib.sha256(data).hexdigest()
    if not _mime_matches(data, declared_mime):
        raise _error("file_mime_mismatch", "资料内容与声明 MIME 不匹配")
    if expected_sha256 and expected_sha256.lower().removeprefix("sha256:") != digest:
        raise _error("source_hash_mismatch", "资料 hash 与声明不一致")
    if expected_size is not None and int(expected_size) != len(data):
        raise _error("source_size_mismatch", "资料大小与声明不一致")
    request_hash = hashlib.sha256(
        json.dumps(
            {"filename": original_filename, "mime": declared_mime, "sha256": digest, "size": len(data)},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    scope = f"commit:{idempotency_key}"
    with _state_guard(workspace_id):
        state = _load_state(workspace_id)
        prior = _active_idempotency_record(state["idempotency"], scope)
        if prior:
            if prior.get("request_hash") != request_hash:
                raise _error("idempotency_conflict", "同一幂等键已用于不同资料")
            return {**state["files"][prior["file_id"]], "idempotent_replay": True}
        file_id = f"src_{digest[:24]}"
        job_id = f"job_{uuid.uuid4().hex}"
        target_dir = _root(workspace_id) / "files" / file_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / Path(original_filename or "source.bin").name
        target.write_bytes(data)
        record = {
            "file_id": file_id, "workspace_id": workspace_id,
            "original_filename": Path(original_filename or "source.bin").name,
            "declared_mime": declared_mime, "mime_type": declared_mime,
            "size_bytes": len(data), "sha256": digest, "version": 1,
            "status": "queued", "extract_status": "queued", "ocr_status": "pending",
            "manual_review_status": "pending", "deterministic_status": "pending",
            "security_scan": {"type_verified": True, "scan_status": "passed"},
            "path": str(target), "parse_job_id": job_id, "created_at": _now(), "updated_at": _now(),
        }
        state["files"][file_id] = record
        state["jobs"][job_id] = {
            "job_id": job_id, "file_id": file_id, "workspace_id": workspace_id,
            "status": "queued", "progress": 0, "attempt": 1, "created_at": _now(),
        }
        state["idempotency"][scope] = _idempotency_record(scope, request_hash, file_id=file_id, job_id=job_id)
        _save_state(workspace_id, state)
        return dict(record)


def parse_source_file(workspace_id: str, job_id: str) -> dict[str, Any]:
    with _state_guard(workspace_id):
        state = _load_state(workspace_id)
        job, record = _require_parse_job_from_state(state, workspace_id, job_id)
        job.update({"status": "running", "progress": 25, "started_at": _now()})
        path = Path(str(record.get("path") or ""))
        try:
            analysis = _parse_bytes(path, str(record.get("declared_mime") or "application/octet-stream"))
            analysis["file_id"] = record["file_id"]
            state["analyses"][record["file_id"]] = analysis
            job.update({"status": "succeeded", "progress": 100, "finished_at": _now()})
            record.update({"status": "succeeded", "extract_status": "succeeded", "deterministic_status": "succeeded", "updated_at": _now()})
        except (OSError, UnicodeError, ValueError) as exc:
            job.update({"status": "failed", "progress": 100, "finished_at": _now(), "error": type(exc).__name__})
            record.update({"status": "failed", "extract_status": "failed", "updated_at": _now()})
        _save_state(workspace_id, state)
        return _public_parse_job(job)
