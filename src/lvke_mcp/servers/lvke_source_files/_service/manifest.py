"""Chunked-upload session manifest reads, ownership and continuity checks."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .envelope import _blocked, _now
from .paths import _manifest_path


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
