"""版本常量、ID 白名单正则、错误类型与 hash/时间原语。"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any




SCHEMA_VERSION = "deliverable_artifacts.v1"


MANIFEST_SCHEMA_VERSION = "deliverable_manifest.v1"


BASIS_SCHEMA_VERSION = "deliverable_basis.v1"


DEFAULT_TEMPLATE_VERSION = "feasibility-report.v1"


DRAFT_MARKER = "专家参考稿/内部复核·非报批终稿"


_SAFE_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


_SAFE_ARTIFACT_ID = re.compile(r"^deliverable_[0-9a-f]{32}$")


_SHA256_EVIDENCE_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


_SAFE_REVISION_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")


_SAFE_TEMPLATE_VERSION = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


_LOCK = threading.RLock()


# MCP 域内 governed 工件只有 evidence_pack（公共 repository 中的 EVIDENCE_STORE）；
# fact_pack / research_decisions / appendix_manifest 为 hermes 独有概念，
# MCP 无对应持久化，不参与工件依据指纹。
_GOVERNED_SNAPSHOTS = ("evidence_pack",)


_SUPPORT_SUFFIXES = {
    ".csv",
    ".docx",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".xls",
    ".xlsx",
}


_VERIFIED_APPENDIX_STATES = {"approved", "ready", "reviewed", "verified"}


class DeliverableArtifactError(RuntimeError):
    """Machine-readable service error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _bytes_hash(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _file_hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise DeliverableArtifactError(
            "ARTIFACT_FILE_UNREADABLE",
            "工件文件不可读取",
            details={"filename": path.name, "error": type(exc).__name__},
        ) from exc
    return "sha256:" + digest.hexdigest(), size


def _without_volatile_timestamps(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_volatile_timestamps(item)
            for key, item in value.items()
            if str(key) != "computed_at"
        }
    if isinstance(value, list):
        return [_without_volatile_timestamps(item) for item in value]
    return value


def _validate_workspace_id(workspace_id: str) -> str:
    value = str(workspace_id or "").strip()
    if not _SAFE_WORKSPACE_ID.fullmatch(value):
        raise DeliverableArtifactError("INVALID_WORKSPACE_ID", "工作区 id 不合法")
    return value


def _validate_artifact_id(artifact_id: str) -> str:
    value = str(artifact_id or "").strip()
    if not _SAFE_ARTIFACT_ID.fullmatch(value):
        raise DeliverableArtifactError("INVALID_ARTIFACT_ID", "工件 id 不合法")
    return value


def _validate_template_version(template_version: str) -> str:
    value = str(template_version or DEFAULT_TEMPLATE_VERSION).strip()
    if not _SAFE_TEMPLATE_VERSION.fullmatch(value):
        raise DeliverableArtifactError(
            "INVALID_TEMPLATE_VERSION", "报告模板版本不合法",
        )
    return value
