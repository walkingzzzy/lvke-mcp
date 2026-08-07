"""工件读取、列举与受控下载解析。"""

from __future__ import annotations

import copy
from pathlib import PurePosixPath
from typing import Any



from .base import (
    DeliverableArtifactError,
    _bytes_hash,
    _file_hash,
    _validate_artifact_id,
    _validate_workspace_id,
)

from .directory import (
    _safe_relative_name,
)

from .lifecycle import (
    _refresh_record_locked,
)

from .storage import (
    _artifact_root,
    _read_state,
    _require_workspace,
    _state_guard,
)

from .support_files import (
    _is_relative_to,
    _path_has_symlink,
)


def get_artifact(
    workspace_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    """Return metadata after fresh basis and file-integrity validation."""

    workspace_id = _validate_workspace_id(workspace_id)
    _require_workspace(workspace_id)
    artifact_id = _validate_artifact_id(artifact_id)
    with _state_guard(workspace_id):
        state = _read_state(workspace_id)
        record = _refresh_record_locked(
            workspace_id,
            state,
            artifact_id,
        )
    return copy.deepcopy(record)


def list_artifacts(
    workspace_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List newest artifacts, refreshing each returned artifact first."""

    workspace_id = _validate_workspace_id(workspace_id)
    _require_workspace(workspace_id)
    bounded_limit = max(1, min(int(limit), 200))
    with _state_guard(workspace_id):
        state = _read_state(workspace_id)
        ordered = sorted(
            (state.get("artifacts") or {}).values(),
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )[:bounded_limit]
        result = [
            copy.deepcopy(
                _refresh_record_locked(
                    workspace_id,
                    state,
                    str(item["artifact_id"]),
                )
            )
            for item in ordered
        ]
    return result


def _resolve_artifact_download(
    workspace_id: str,
    artifact_id: str,
    filename: str,
) -> dict[str, Any]:
    """Resolve a safe, current, hash-verified artifact file."""

    safe_name = _safe_relative_name(filename)
    record = get_artifact(workspace_id, artifact_id)
    if record.get("status") != "succeeded" or not record.get("current"):
        raise DeliverableArtifactError(
            "ARTIFACT_NOT_CURRENT",
            "交付工件已失效或不是当前可下载工件",
            details={
                "status": record.get("status"),
                "invalidation_reasons": copy.deepcopy(
                    record.get("invalidation_reasons") or []
                ),
            },
        )
    entry = next(
        (item for item in record.get("files") or [] if item.get("name") == safe_name),
        None,
    )
    if not isinstance(entry, dict):
        raise DeliverableArtifactError(
            "ARTIFACT_FILE_NOT_FOUND", "交付工件文件不存在",
            details={"filename": safe_name},
        )
    root = _artifact_root(workspace_id, artifact_id)
    path = root.joinpath(*PurePosixPath(safe_name).parts)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise DeliverableArtifactError(
            "ARTIFACT_FILE_NOT_FOUND", "交付工件文件不存在",
            details={"filename": safe_name},
        ) from exc
    if (
        not _is_relative_to(resolved, resolved_root)
        or _path_has_symlink(path, root)
        or not resolved.is_file()
    ):
        raise DeliverableArtifactError(
            "INVALID_FILENAME_PATH", "交付工件文件路径不安全",
        )
    actual_hash, actual_size = _file_hash(resolved)
    if actual_hash != entry.get("sha256") or actual_size != entry.get("size_bytes"):
        # A race after get_artifact still fails closed.  The next read will
        # durably persist invalidation; do that now as well.
        get_artifact(workspace_id, artifact_id)
        raise DeliverableArtifactError(
            "ARTIFACT_INTEGRITY_FAILED", "交付工件文件完整性校验失败",
            details={"filename": safe_name},
        )
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "artifact_id": artifact_id,
        "kind": record.get("kind"),
        "status": record.get("status"),
        "filename": safe_name,
        "path": resolved,
        "sha256": actual_hash,
        "size_bytes": actual_size,
        "media_type": entry.get("media_type") or "application/octet-stream",
        "finance_run_id": record.get("finance_run_id"),
        "basis_fingerprint": record.get("basis_fingerprint"),
    }


def read_artifact_download(
    workspace_id: str,
    artifact_id: str,
    filename: str,
) -> dict[str, Any]:
    """Read verified bytes so an HTTP response cannot race a later file open."""

    resolved = _resolve_artifact_download(
        workspace_id,
        artifact_id,
        filename,
    )
    try:
        content = resolved["path"].read_bytes()
    except OSError as exc:
        raise DeliverableArtifactError(
            "ARTIFACT_FILE_UNREADABLE", "交付工件文件不可读取",
            details={"filename": resolved.get("filename")},
        ) from exc
    if (
        _bytes_hash(content) != resolved.get("sha256")
        or len(content) != resolved.get("size_bytes")
    ):
        get_artifact(workspace_id, artifact_id)
        raise DeliverableArtifactError(
            "ARTIFACT_INTEGRITY_FAILED", "下载前工件文件内容发生变化",
            details={"filename": resolved.get("filename")},
        )
    return {**resolved, "content": content}
