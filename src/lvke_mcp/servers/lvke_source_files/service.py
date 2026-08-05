"""MCP adapter for governed source-file staging, parsing and resources."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import shutil
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from lvke_mcp.runtime.storage import (
    canonical_json,
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
)
from lvke_mcp.adapters import source_files_repository as source_api
from lvke_mcp.runtime.workspace import workspace_root
from lvke_mcp.runtime.coordination import build_coordination
from lvke_mcp.servers.lvke_source_files.external_corpora import (
    ExternalCorpusError,
    configured_import_roots,
    resolve_project_corpora,
)

DIRECT_CONTENT_LIMIT = 8 * 1024 * 1024
CHUNK_LIMIT = 4 * 1024 * 1024
SESSION_TTL = timedelta(hours=24)
DOMAIN = "source-files"


def _envelope(
    *,
    success: bool,
    status: str,
    code: str = "",
    message: str = "",
    resource_uris: list[str] | None = None,
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    next_actions: list[str] | None = None,
    retryable: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": success,
        "business_success": success,
        "system_success": True,
        "transport_success": True,
        "status": status,
        "resource_uris": resource_uris or [],
        "warnings": warnings or [],
        "blockers": blockers or [],
        "next_actions": next_actions or [],
        **extra,
    }
    if code:
        payload["code"] = code.lower()
    if message:
        payload["message"] = message
    if retryable:
        payload["retryable"] = True
    payload["coordination"] = build_coordination(
        payload, server_name="lvke-source-files"
    )
    return payload


def _blocked(
    code: str,
    message: str,
    *,
    next_actions: list[str] | None = None,
    retryable: bool = False,
    field_errors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _envelope(
        success=False,
        status="blocked",
        code=code,
        message=message,
        blockers=[code.lower()],
        next_actions=next_actions,
        retryable=retryable,
        **({"field_errors": field_errors} if field_errors else {}),
    )


def _from_source_exception(exc: source_api.SourceFileError) -> dict[str, Any]:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = str(detail.get("code") or "source_operation_failed").lower()
    return _blocked(
        code,
        str(detail.get("message") or "原始资料操作失败"),
        retryable=bool(detail.get("retryable")),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source_root(workspace_id: str) -> Path:
    return workspace_root(require_safe_id(workspace_id, "workspace_id")) / "source-files"


def _sessions_root(workspace_id: str) -> Path:
    root = _source_root(workspace_id) / "staging" / "mcp_sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_dir(workspace_id: str, upload_id: str) -> Path:
    return _sessions_root(workspace_id) / require_safe_id(upload_id, "upload_id")


def _manifest_path(workspace_id: str, upload_id: str) -> Path:
    return _session_dir(workspace_id, upload_id) / "manifest.json"


def _session_lock(workspace_id: str, upload_id: str) -> FileLock:
    directory = _session_dir(workspace_id, upload_id)
    directory.mkdir(parents=True, exist_ok=True)
    return FileLock(str(directory / "session.lock"), timeout=30)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as target:
            json.dump(value, target, ensure_ascii=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_manifest(workspace_id: str, upload_id: str) -> dict[str, Any] | None:
    try:
        value = json.loads(
            _manifest_path(workspace_id, upload_id).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _owned_manifest(
    workspace_id: str,
    upload_id: str,
    *,
    allow_expired: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    manifest = _load_manifest(workspace_id, upload_id)
    if (
        not manifest
        or manifest.get("workspace_id") != workspace_id
    ):
        return None, _blocked(
            "source_upload_not_found",
            "上传会话不存在或不属于当前作用域",
        )
    try:
        expires = datetime.fromisoformat(str(manifest["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None, _blocked("source_upload_invalid", "上传会话清单无效")
    if not allow_expired and expires <= _now():
        return None, _blocked(
            "source_upload_expired",
            "上传会话已过期，请创建新会话",
        )
    return manifest, None


def _decode_content(content_base64: str, *, limit: int) -> tuple[bytes | None, dict[str, Any] | None]:
    try:
        raw = base64.b64decode(content_base64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return None, _blocked("content_encoding_invalid", "content_base64 不是合法 Base64")
    if not raw:
        return None, _blocked("source_empty", "空文件不能作为原始证据")
    if len(raw) > limit:
        return None, _blocked(
            "source_content_too_large",
            "单次内容超过允许上限",
            field_errors={"/content_base64": {"max_decoded_bytes": limit}},
        )
    return raw, None


def _stage_bytes(workspace_id: str, raw: bytes) -> Path:
    staging = _source_root(workspace_id) / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / f"mcp-{uuid.uuid4().hex}.part"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _file_uri(workspace_id: str, file_id: str) -> str:
    return f"lvke://{DOMAIN}/workspaces/{workspace_id}/files/{file_id}"


def _analysis_uri(workspace_id: str, file_id: str) -> str:
    return f"lvke://{DOMAIN}/workspaces/{workspace_id}/analyses/{file_id}"


def _job_uri(workspace_id: str, job_id: str) -> str:
    return f"lvke://{DOMAIN}/workspaces/{workspace_id}/parse-jobs/{job_id}"


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    visible = dict(record)
    for key in (
        "request_id",
        "revision_observed_by",
        "upload_identity_hash",
        "worker_token",
        "_worker_control",
    ):
        visible.pop(key, None)
    return visible


def _commit_and_parse(
    workspace_id: str,
    staged_path: Path,
    *,
    original_filename: str,
    declared_mime: str,
    idempotency_key: str,
    expected_sha256: str = "",
    expected_size: int | None = None,
    parse_immediately: bool = True,
) -> dict[str, Any]:
    try:
        record = source_api.commit_staged_source_file(
            workspace_id,
            staged_path,
            original_filename,
            declared_mime,
            idempotency_key=idempotency_key,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
    except source_api.SourceFileError as exc:
        return _from_source_exception(exc)
    file_id = str(record.get("file_id") or "")
    job_id = str(record.get("parse_job_id") or "")
    state = source_api._load_state(workspace_id)  # noqa: SLF001
    current_job = state["jobs"].get(job_id, {})
    if parse_immediately and current_job.get("status") == "queued" and job_id:
        source_api.parse_source_file(workspace_id, job_id)
        state = source_api._load_state(workspace_id)  # noqa: SLF001
    stored = source_api._require_source_record_from_state(state, workspace_id, file_id)
    job = source_api._public_parse_job(state["jobs"].get(job_id, {}))  # noqa: SLF001
    parse_status = str(job.get("status") or "queued")
    status = "partial" if parse_status in {"partial", "failed"} else "ok"
    success = status == "ok"
    warnings = []
    if parse_status == "partial":
        warnings.append("原始资料已固化，但解析结果为 partial，不具备自动正式证据资格")
    elif parse_status == "failed":
        warnings.append("原始资料已固化，但解析失败，可调用 source_parse_retry")
    resource_uris = [_file_uri(workspace_id, file_id), _job_uri(workspace_id, job_id)]
    if parse_status in {"succeeded", "partial"}:
        resource_uris.append(_analysis_uri(workspace_id, file_id))
    return _envelope(
        success=success,
        status=status,
        code="source_parse_incomplete" if not success else "",
        resource_uris=resource_uris,
        warnings=warnings,
        next_actions=[
            "读取 source_file_get 检查安全扫描、解析状态与 formal_use 决策",
            "对候选 evidence locator 完成人工复核后再绑定正式财务输入",
        ],
        file_id=file_id,
        parse_job_id=job_id,
        source_file=_public_record(stored),
        parse_job=job,
        idempotent_replay=bool(record.get("idempotent_replay")),
        evidence_eligibility="candidate",
        formal_evidence_candidate=False,
        lineage={
            "source_sha256": stored.get("sha256"),
            "source_version": stored.get("version"),
            "parse_job_id": job_id,
        },
    )


def import_content(
    workspace_id: str,
    *,
    original_filename: str,
    declared_mime: str,
    content_base64: str,
    idempotency_key: str,
    expected_sha256: str = "",
    parse_immediately: bool = True,
) -> dict[str, Any]:
    raw, error = _decode_content(content_base64, limit=DIRECT_CONTENT_LIMIT)
    if error:
        return error
    assert raw is not None
    staged = _stage_bytes(workspace_id, raw)
    return _commit_and_parse(
        workspace_id,
        staged,
        original_filename=original_filename,
        declared_mime=declared_mime,
        idempotency_key=idempotency_key,
        expected_sha256=expected_sha256,
        expected_size=len(raw),
        parse_immediately=parse_immediately,
    )


def _configured_import_roots() -> list[Path]:
    return list(configured_import_roots())


def resolve_external_corpus(project_name: str) -> dict[str, Any]:
    """Resolve one registered project without importing or mutating source data."""

    try:
        resolved = resolve_project_corpora(project_name)
    except ExternalCorpusError as exc:
        return _blocked(
            "external_corpus_unavailable",
            str(exc),
            next_actions=[
                "配置 LVKE_EXTERNAL_CORPUS_ROOT 并核对 config/external_corpora.v1.json",
                "使用包含已登记项目名称的一句话指令",
            ],
        )
    return _envelope(
        success=True,
        status="ok",
        evidence_eligibility="none",
        **resolved,
    )


def _resolve_local_source(local_path: str) -> tuple[Path | None, dict[str, Any] | None]:
    if str(os.getenv("LVKE_MCP_TRANSPORT") or "stdio").lower() not in {"stdio", "local"}:
        return None, _blocked(
            "local_path_transport_forbidden",
            "本地路径导入仅允许 stdio/local MCP transport",
        )
    raw_path = Path(str(local_path or ""))
    if not raw_path.is_absolute():
        return None, _blocked("local_path_invalid", "local_path 必须是绝对路径")
    try:
        roots = _configured_import_roots()
    except ExternalCorpusError as exc:
        return None, _blocked(
            "external_corpus_unavailable",
            str(exc),
            next_actions=[
                "配置 LVKE_SOURCE_IMPORT_ROOTS，或配置 LVKE_EXTERNAL_CORPUS_ROOT 并修复资料 marker",
            ],
        )
    if not roots:
        return None, _blocked(
            "local_import_roots_unconfigured",
            "未配置可用的本地资料导入根，拒绝本地路径导入",
        )
    try:
        path_stat = raw_path.lstat()
        resolved = raw_path.resolve(strict=True)
    except OSError:
        return None, _blocked("local_source_not_found", "本地源文件不存在")
    if raw_path.is_symlink() or not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
        return None, _blocked(
            "local_source_unsafe",
            "本地源必须是允许目录内的单链接普通文件",
        )
    if not any(resolved == root or root in resolved.parents for root in roots):
        return None, _blocked("local_source_outside_roots", "本地源文件不在允许导入目录内")
    return resolved, None


def import_local_path(
    workspace_id: str,
    *,
    local_path: str,
    original_filename: str,
    declared_mime: str,
    idempotency_key: str,
    expected_sha256: str = "",
    parse_immediately: bool = True,
) -> dict[str, Any]:
    source, error = _resolve_local_source(local_path)
    if error:
        return error
    assert source is not None
    staging = _source_root(workspace_id) / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"mcp-local-{uuid.uuid4().hex}.part"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        with os.fdopen(descriptor, "rb") as source_stream, staged.open("xb") as target:
            current = os.fstat(source_stream.fileno())
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                return _blocked("local_source_unsafe", "本地源文件状态已变化，拒绝导入")
            shutil.copyfileobj(source_stream, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        return _commit_and_parse(
            workspace_id,
            staged,
            original_filename=original_filename,
            declared_mime=declared_mime,
            idempotency_key=idempotency_key,
            expected_sha256=expected_sha256,
            expected_size=source.stat().st_size,
            parse_immediately=parse_immediately,
        )
    finally:
        staged.unlink(missing_ok=True)


def upload_begin(
    workspace_id: str,
    *,
    original_filename: str,
    declared_mime: str,
    total_size: int,
    expected_sha256: str,
    idempotency_key: str,
) -> dict[str, Any]:
    workspace_id = require_safe_id(workspace_id, "workspace_id")
    if total_size <= 0 or total_size > source_api._max_upload_bytes():
        return _blocked(
            "source_size_invalid",
            "total_size 必须大于 0 且不超过文件上限",
            field_errors={"/total_size": {"max": source_api._max_upload_bytes()}},
        )
    normalized_hash = str(expected_sha256 or "").lower().removeprefix("sha256:")
    if len(normalized_hash) != 64 or any(char not in "0123456789abcdef" for char in normalized_hash):
        return _blocked("source_hash_invalid", "expected_sha256 必须是 SHA-256 十六进制摘要")
    request_payload = {
        "original_filename": original_filename,
        "declared_mime": declared_mime.lower().strip(),
        "total_size": total_size,
        "expected_sha256": normalized_hash,
    }
    scope = canonical_json({"workspace_id": workspace_id, "idempotency_key": idempotency_key})
    upload_id = "ups_" + hashlib.sha256(scope.encode("utf-8")).hexdigest()[:24]
    with _session_lock(workspace_id, upload_id):
        existing = _load_manifest(workspace_id, upload_id)
        request_hash = sha256_json(request_payload)
        if existing:
            if existing.get("request_hash") != request_hash:
                return _blocked("idempotency_conflict", "同一幂等键已用于不同上传会话")
            return _envelope(
                success=True,
                status="ok",
                upload_id=upload_id,
                upload_status=existing.get("upload_status"),
                expires_at=existing.get("expires_at"),
                idempotent_replay=True,
            )
        created = _now()
        manifest = {
            "schema_version": "source_upload_session.v1",
            "upload_id": upload_id,
            "workspace_id": workspace_id,
            **request_payload,
            "request_hash": request_hash,
            "upload_status": "open",
            "chunks": {},
            "chunk_operations": {},
            "created_at": created.isoformat(),
            "expires_at": (created + SESSION_TTL).isoformat(),
        }
        _write_json_atomic(_manifest_path(workspace_id, upload_id), manifest)
    return _envelope(
        success=True,
        status="ok",
        upload_id=upload_id,
        upload_status="open",
        chunk_limit_bytes=CHUNK_LIMIT,
        expires_at=manifest["expires_at"],
        idempotent_replay=False,
        next_actions=["按 offset_bytes 调用 source_upload_chunk，完成后调用 source_upload_commit"],
    )


def upload_chunk(
    workspace_id: str,
    upload_id: str,
    *,
    offset_bytes: int,
    content_base64: str,
    idempotency_key: str,
) -> dict[str, Any]:
    raw, error = _decode_content(content_base64, limit=CHUNK_LIMIT)
    if error:
        return error
    assert raw is not None
    with _session_lock(workspace_id, upload_id):
        manifest, error = _owned_manifest(
            workspace_id, upload_id
        )
        if error:
            return error
        assert manifest is not None
        if manifest.get("upload_status") != "open":
            return _blocked("source_upload_not_open", "上传会话已提交或中止")
        end = offset_bytes + len(raw)
        if offset_bytes < 0 or end > int(manifest["total_size"]):
            return _blocked("source_chunk_range_invalid", "分块范围超出上传清单")
        digest = hashlib.sha256(raw).hexdigest()
        operation_hash = sha256_json(
            {"offset_bytes": offset_bytes, "size_bytes": len(raw), "sha256": digest}
        )
        operations = manifest.setdefault("chunk_operations", {})
        prior_operation = operations.get(idempotency_key)
        if prior_operation:
            if prior_operation != operation_hash:
                return _blocked("idempotency_conflict", "同一幂等键已用于不同分块")
            return _envelope(
                success=True,
                status="ok",
                upload_id=upload_id,
                offset_bytes=offset_bytes,
                size_bytes=len(raw),
                sha256=digest,
                idempotent_replay=True,
            )
        chunks = manifest.setdefault("chunks", {})
        for existing in chunks.values():
            start = int(existing["offset_bytes"])
            existing_end = start + int(existing["size_bytes"])
            if offset_bytes < existing_end and end > start:
                if (
                    offset_bytes == start
                    and len(raw) == int(existing["size_bytes"])
                    and digest == existing["sha256"]
                ):
                    operations[idempotency_key] = operation_hash
                    _write_json_atomic(_manifest_path(workspace_id, upload_id), manifest)
                    return _envelope(
                        success=True,
                        status="ok",
                        upload_id=upload_id,
                        offset_bytes=offset_bytes,
                        size_bytes=len(raw),
                        sha256=digest,
                        idempotent_replay=True,
                    )
                return _blocked("source_chunk_overlap", "分块与已上传范围重叠")
        chunk_path = _session_dir(workspace_id, upload_id) / f"chunk-{offset_bytes:012d}.part"
        with chunk_path.open("xb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        chunks[str(offset_bytes)] = {
            "offset_bytes": offset_bytes,
            "size_bytes": len(raw),
            "sha256": digest,
            "filename": chunk_path.name,
        }
        operations[idempotency_key] = operation_hash
        manifest["updated_at"] = _now().isoformat()
        _write_json_atomic(_manifest_path(workspace_id, upload_id), manifest)
    return _envelope(
        success=True,
        status="ok",
        upload_id=upload_id,
        offset_bytes=offset_bytes,
        size_bytes=len(raw),
        sha256=digest,
        received_bytes=sum(int(item["size_bytes"]) for item in chunks.values()),
        idempotent_replay=False,
    )


def _validate_chunk_continuity(manifest: dict[str, Any]) -> list[dict[str, int]]:
    gaps: list[dict[str, int]] = []
    expected = 0
    ordered = sorted(
        manifest.get("chunks", {}).values(), key=lambda item: int(item["offset_bytes"])
    )
    for chunk in ordered:
        start = int(chunk["offset_bytes"])
        if start != expected:
            gaps.append({"start": expected, "end": start})
        expected = start + int(chunk["size_bytes"])
    total = int(manifest["total_size"])
    if expected != total:
        gaps.append({"start": expected, "end": total})
    return gaps


def upload_commit(
    workspace_id: str,
    upload_id: str,
    *,
    idempotency_key: str,
    parse_immediately: bool = True,
) -> dict[str, Any]:
    with _session_lock(workspace_id, upload_id):
        manifest, error = _owned_manifest(
            workspace_id, upload_id
        )
        if error:
            return error
        assert manifest is not None
        if manifest.get("upload_status") == "committed":
            response = dict(manifest.get("commit_response") or {})
            response["idempotent_replay"] = True
            return response
        if manifest.get("upload_status") != "open":
            return _blocked("source_upload_not_open", "上传会话已中止")
        gaps = _validate_chunk_continuity(manifest)
        if gaps:
            return _blocked(
                "source_chunks_incomplete",
                "上传分块不连续或不完整",
                field_errors={"/chunks": {"missing_ranges": gaps}},
            )
        assembled = _session_dir(workspace_id, upload_id) / "assembled.part"
        hasher = hashlib.sha256()
        size = 0
        try:
            with assembled.open("xb") as target:
                for chunk in sorted(
                    manifest["chunks"].values(), key=lambda item: int(item["offset_bytes"])
                ):
                    path = _session_dir(workspace_id, upload_id) / str(chunk["filename"])
                    raw = path.read_bytes()
                    if (
                        len(raw) != int(chunk["size_bytes"])
                        or hashlib.sha256(raw).hexdigest() != chunk["sha256"]
                    ):
                        return _blocked("source_chunk_integrity_failed", "暂存分块完整性校验失败")
                    target.write(raw)
                    hasher.update(raw)
                    size += len(raw)
                target.flush()
                os.fsync(target.fileno())
            if size != int(manifest["total_size"]):
                return _blocked("source_size_mismatch", "合并文件大小与上传清单不一致")
            if hasher.hexdigest() != manifest["expected_sha256"]:
                return _blocked("source_hash_mismatch", "合并文件哈希与上传清单不一致")
            response = _commit_and_parse(
                workspace_id,
                assembled,
                original_filename=str(manifest["original_filename"]),
                declared_mime=str(manifest["declared_mime"]),
                idempotency_key=idempotency_key,
                expected_sha256=str(manifest["expected_sha256"]),
                expected_size=int(manifest["total_size"]),
                parse_immediately=parse_immediately,
            )
            if response.get("status") in {"ok", "partial"} and response.get("file_id"):
                manifest["upload_status"] = "committed"
                manifest["committed_at"] = _now().isoformat()
                manifest["commit_idempotency_hash"] = hashlib.sha256(
                    idempotency_key.encode("utf-8")
                ).hexdigest()
                manifest["commit_response"] = response
                _write_json_atomic(_manifest_path(workspace_id, upload_id), manifest)
                for chunk in manifest["chunks"].values():
                    (_session_dir(workspace_id, upload_id) / str(chunk["filename"])).unlink(
                        missing_ok=True
                    )
            return response
        finally:
            assembled.unlink(missing_ok=True)


def upload_abort(
    workspace_id: str,
    upload_id: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    with _session_lock(workspace_id, upload_id):
        manifest, error = _owned_manifest(
            workspace_id,
            upload_id,
            allow_expired=True,
        )
        if error:
            return error
        assert manifest is not None
        if manifest.get("upload_status") == "committed":
            return _blocked("source_upload_already_committed", "已提交会话不能中止")
        request_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        if manifest.get("upload_status") == "aborted":
            if manifest.get("abort_idempotency_hash") != request_hash:
                return _blocked("idempotency_conflict", "会话已由其他幂等请求中止")
            return _envelope(
                success=True,
                status="ok",
                upload_id=upload_id,
                upload_status="aborted",
                idempotent_replay=True,
            )
        for chunk in manifest.get("chunks", {}).values():
            (_session_dir(workspace_id, upload_id) / str(chunk["filename"])).unlink(
                missing_ok=True
            )
        manifest["upload_status"] = "aborted"
        manifest["aborted_at"] = _now().isoformat()
        manifest["abort_idempotency_hash"] = request_hash
        manifest["chunks"] = {}
        _write_json_atomic(_manifest_path(workspace_id, upload_id), manifest)
    return _envelope(
        success=True,
        status="ok",
        upload_id=upload_id,
        upload_status="aborted",
        idempotent_replay=False,
    )


def upload_status(
    workspace_id: str,
    upload_id: str,
) -> dict[str, Any]:
    manifest, error = _owned_manifest(
        workspace_id,
        upload_id,
        allow_expired=True,
    )
    if error:
        return error
    assert manifest is not None
    expires = datetime.fromisoformat(str(manifest["expires_at"]).replace("Z", "+00:00"))
    upload_state = str(manifest.get("upload_status") or "invalid")
    expired = upload_state == "open" and expires <= _now()
    return _envelope(
        success=not expired,
        status="blocked" if expired else "ok",
        code="source_upload_expired" if expired else "",
        blockers=["source_upload_expired"] if expired else [],
        upload_id=upload_id,
        upload_status="expired" if expired else upload_state,
        total_size=manifest.get("total_size"),
        received_bytes=sum(
            int(item["size_bytes"]) for item in manifest.get("chunks", {}).values()
        ),
        expires_at=manifest.get("expires_at"),
        file_id=(manifest.get("commit_response") or {}).get("file_id"),
    )


def list_source_files(
    workspace_id: str,
    *,
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    state = source_api._load_state(workspace_id)  # noqa: SLF001
    entries = [
        {
            **_public_record(record),
            "uri": _file_uri(workspace_id, str(record["file_id"])),
        }
        for record in state["files"].values()
    ]
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _blocked(str(exc), "原始资料分页游标无效或列表已变化")
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[entry["uri"] for entry in page["resources"]],
        source_files=page["resources"],
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
        snapshot_hash=page["snapshot_hash"],
    )


def get_source_file(
    workspace_id: str,
    file_id: str,
) -> dict[str, Any]:
    try:
        _state, record = source_api._require_source_record(  # noqa: SLF001
            workspace_id, file_id, "mcp"
        )
    except source_api.SourceFileError as exc:
        return _from_source_exception(exc)
    analysis = source_api._load_analysis(workspace_id, file_id)  # noqa: SLF001
    uris = [_file_uri(workspace_id, file_id)]
    if analysis:
        uris.append(_analysis_uri(workspace_id, file_id))
    return _envelope(
        success=True,
        status="ok",
        resource_uris=uris,
        file_id=file_id,
        source_file=_public_record(record),
        analysis=analysis or None,
    )


def parse_status(
    workspace_id: str,
    job_id: str,
) -> dict[str, Any]:
    try:
        job, _record = source_api._require_parse_job_from_state(  # noqa: SLF001
            source_api._load_state(workspace_id),  # noqa: SLF001
            workspace_id,
            job_id,
            "mcp",
        )
    except source_api.SourceFileError as exc:
        return _from_source_exception(exc)
    public = source_api._public_parse_job(job)  # noqa: SLF001
    state = str(public.get("status") or "failed")
    success = state in {"queued", "running", "succeeded"}
    status = "ok" if success else "partial" if state == "partial" else "blocked"
    return _envelope(
        success=success,
        status=status,
        code="source_parse_incomplete" if not success else "",
        blockers=[] if state == "partial" else (["source_parse_incomplete"] if not success else []),
        resource_uris=[_job_uri(workspace_id, job_id)],
        task_status=state,
        parse_job=public,
    )


def parse_retry(
    workspace_id: str,
    job_id: str,
    *,
    idempotency_key: str,
    parse_immediately: bool = True,
) -> dict[str, Any]:
    request_hash = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    scope = f"mcp-retry:{job_id}:{idempotency_key}"
    try:
        with source_api._state_guard(workspace_id):  # noqa: SLF001
            state = source_api._load_state(workspace_id)  # noqa: SLF001
            old, file_record = source_api._require_parse_job_from_state(  # noqa: SLF001
                state, workspace_id, job_id, "mcp"
            )
            prior = source_api._active_idempotency_record(  # noqa: SLF001
                state["idempotency"], scope
            )
            if prior:
                if prior.get("request_hash") != request_hash:
                    return _blocked("idempotency_conflict", "同一幂等键已用于不同解析重试")
                new_id = str(prior.get("job_id") or "")
                new_job, _ = source_api._require_parse_job_from_state(  # noqa: SLF001
                    state, workspace_id, new_id, "mcp"
                )
                public = source_api._public_parse_job(new_job)  # noqa: SLF001
                return _envelope(
                    success=True,
                    status="ok",
                    resource_uris=[_job_uri(workspace_id, new_id)],
                    parse_job_id=new_id,
                    parse_job=public,
                    idempotent_replay=True,
                )
            if old.get("status") not in {"failed", "partial", "cancelled"}:
                return _blocked("parse_retry_not_allowed", "只有 failed/partial/cancelled 任务可以重试")
            if file_record.get("parse_job_id") != job_id:
                return _blocked("parse_retry_not_allowed", "解析任务已被更新尝试取代")
            new_id = f"job_{uuid.uuid4().hex}"
            job = {
                "job_id": new_id,
                "file_id": old["file_id"],
                "workspace_id": workspace_id,
                "status": "queued",
                "progress": 0,
                "attempt": int(old.get("attempt") or 1) + 1,
                "created_at": _now().isoformat(),
                "retry_of": job_id,
            }
            state["jobs"][new_id] = job
            file_record.update(
                {
                    "parse_job_id": new_id,
                    "status": "queued",
                    "extract_status": "queued",
                    "ocr_status": "pending",
                    "deterministic_status": "pending",
                    "updated_at": _now().isoformat(),
                }
            )
            state["idempotency"][scope] = source_api._idempotency_record(  # noqa: SLF001
                scope,
                request_hash,
                job_id=new_id,
                file_id=str(old["file_id"]),
            )
            source_api._save_state(workspace_id, state)  # noqa: SLF001
        if parse_immediately:
            source_api.parse_source_file(workspace_id, new_id)
        return parse_status(workspace_id, new_id) | {
            "parse_job_id": new_id,
            "idempotent_replay": False,
        }
    except source_api.SourceFileError as exc:
        return _from_source_exception(exc)


def parse_cancel(
    workspace_id: str,
    job_id: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    scope = f"mcp-cancel:{job_id}:{idempotency_key}"
    try:
        with source_api._state_guard(workspace_id):  # noqa: SLF001
            state = source_api._load_state(workspace_id)  # noqa: SLF001
            job, record = source_api._require_parse_job_from_state(  # noqa: SLF001
                state, workspace_id, job_id, "mcp"
            )
            prior = source_api._active_idempotency_record(  # noqa: SLF001
                state["idempotency"], scope
            )
            if prior:
                return _envelope(
                    success=True,
                    status="ok",
                    resource_uris=[_job_uri(workspace_id, job_id)],
                    task_status="cancelled",
                    idempotent_replay=True,
                )
            if job.get("status") not in {"queued", "running", "cancelled"}:
                return _blocked("parse_cancel_not_allowed", "终态解析任务不能取消")
            job.update(
                {
                    "status": "cancelled",
                    "progress": 100,
                    "finished_at": _now().isoformat(),
                    "worker_token": "",
                }
            )
            if record.get("parse_job_id") == job_id:
                record.update(
                    {
                        "status": "cancelled",
                        "extract_status": "cancelled",
                        "updated_at": _now().isoformat(),
                    }
                )
            state["idempotency"][scope] = source_api._idempotency_record(  # noqa: SLF001
                scope, hashlib.sha256(job_id.encode("utf-8")).hexdigest(), job_id=job_id
            )
            source_api._save_state(workspace_id, state)  # noqa: SLF001
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[_job_uri(workspace_id, job_id)],
            task_status="cancelled",
            idempotent_replay=False,
        )
    except source_api.SourceFileError as exc:
        return _from_source_exception(exc)


def list_resources(
    workspace_id: str,
    *,
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    state = source_api._load_state(workspace_id)  # noqa: SLF001
    entries: list[dict[str, Any]] = []
    visible_files: set[str] = set()
    for record in state["files"].values():
        file_id = str(record["file_id"])
        visible_files.add(file_id)
        public = _public_record(record)
        entries.append(
            {
                "uri": _file_uri(workspace_id, file_id),
                "name": file_id,
                "resource_type": "SourceFile",
                "mime_type": "application/json",
                "content_hash": sha256_json(public),
            }
        )
        analysis = source_api._load_analysis(workspace_id, file_id)  # noqa: SLF001
        if analysis:
            entries.append(
                {
                    "uri": _analysis_uri(workspace_id, file_id),
                    "name": f"{file_id}-analysis",
                    "resource_type": "SourceAnalysis",
                    "mime_type": "application/json",
                    "content_hash": sha256_json(analysis),
                }
            )
    for job in state["jobs"].values():
        if str(job.get("file_id") or "") not in visible_files:
            continue
        job_id = str(job["job_id"])
        public = source_api._public_parse_job(job)  # noqa: SLF001
        entries.append(
            {
                "uri": _job_uri(workspace_id, job_id),
                "name": job_id,
                "resource_type": "ParseJob",
                "mime_type": "application/json",
                "content_hash": sha256_json(public),
            }
        )
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _blocked(str(exc), "Resource 分页游标无效或列表已变化")
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[entry["uri"] for entry in page["resources"]],
        resources=page["resources"],
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
        snapshot_hash=page["snapshot_hash"],
    )


def read_resource(
    workspace_id: str,
    uri: str,
) -> dict[str, Any]:
    prefix = f"lvke://{DOMAIN}/workspaces/{workspace_id}/"
    if not str(uri).startswith(prefix):
        return _blocked("resource_not_found", "资源不存在或不属于当前工作区")
    parts = str(uri)[len(prefix) :].split("/")
    if len(parts) != 2:
        return _blocked("resource_not_found", "Resource URI 无效")
    segment, object_id = parts
    try:
        require_safe_id(object_id, "object_id")
        state = source_api._load_state(workspace_id)  # noqa: SLF001
        if segment in {"files", "analyses"}:
            record = source_api._require_source_record_from_state(  # noqa: SLF001
                state, workspace_id, object_id, "mcp"
            )
            content_value = (
                _public_record(record)
                if segment == "files"
                else source_api._load_analysis(workspace_id, object_id)  # noqa: SLF001
            )
            if segment == "analyses" and not content_value:
                return _blocked("resource_not_found", "解析 Resource 尚不存在")
        elif segment == "parse-jobs":
            job, _record = source_api._require_parse_job_from_state(  # noqa: SLF001
                state, workspace_id, object_id, "mcp"
            )
            content_value = source_api._public_parse_job(job)  # noqa: SLF001
        else:
            return _blocked("resource_not_found", "未知 Resource 类型")
    except (source_api.SourceFileError, ValueError) as exc:
        if isinstance(exc, source_api.SourceFileError):
            return _from_source_exception(exc)
        return _blocked("resource_not_found", "Resource URI 无效")
    content = json.dumps(content_value, ensure_ascii=False, indent=2)
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[uri],
        uri=uri,
        mime_type="application/json",
        content=content,
        content_hash=sha256_json(content_value),
    )
