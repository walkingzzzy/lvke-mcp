"""Resumable chunked upload sessions over the governed staging tree."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any

from lvke_mcp.adapters import source_files_repository as source_api
from lvke_mcp.runtime.storage import canonical_json, require_safe_id, sha256_json

from .constants import CHUNK_LIMIT, SESSION_TTL
from .envelope import _blocked, _envelope, _now
from .imports import _commit_and_parse, _decode_content
from .manifest import _load_manifest, _owned_manifest, _validate_chunk_continuity
from .paths import _manifest_path, _session_dir, _session_lock, _write_json_atomic


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
