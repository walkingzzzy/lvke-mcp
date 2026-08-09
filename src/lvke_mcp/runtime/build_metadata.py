"""Uniform build metadata for every Lvke MCP server.

三个不变量：

1. 构建时间必须是真实的 UTC 时刻，不能是 ``source-checkout`` 这类占位串。
   构建/插件安装时由 ``scripts/write_build_metadata.py`` 写入
   ``build_metadata.json``；环境变量 ``LVKE_MCP_BUILD_TIME`` 可覆盖。
2. 14 个服务必须输出同一 commit、同一 build_time、同一 plugin_version。
   因此解析结果在进程内固化一次，所有 server 共享同一份快照。
3. 启动时校验元数据完整性，缺失时显式返回 ``build_metadata_incomplete``，
   不静默退化为 ``unknown``。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BUILD_METADATA_FILENAME = "build_metadata.json"
INCOMPLETE_CODE = "build_metadata_incomplete"
_PLACEHOLDERS = frozenset({"", "unknown", "source-checkout", "none", "null"})


def _repo_root() -> Path | None:
    """Locate the checkout root without hardcoding directory depth."""

    here = Path(__file__).resolve()
    return next(
        (candidate for candidate in here.parents if (candidate / ".git").exists()),
        None,
    )


def _metadata_file() -> Path | None:
    """Return the first readable build metadata file.

    查找顺序：显式环境变量 → 包内（wheel/插件安装布局）→ 仓库根（源码树）。
    """

    configured = str(os.getenv("LVKE_MCP_BUILD_METADATA_FILE") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path(__file__).resolve().parent / BUILD_METADATA_FILENAME)
    root = _repo_root()
    if root is not None:
        candidates.append(root / BUILD_METADATA_FILENAME)
    return next((path for path in candidates if path.is_file()), None)


def _load_metadata_file() -> dict[str, Any]:
    path = _metadata_file()
    if path is None:
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _resolve_commit(file_metadata: dict[str, Any]) -> str:
    """Resolve the build commit from env, metadata file, then git plumbing."""

    configured = str(os.getenv("LVKE_MCP_GIT_SHA") or "").strip()
    if configured:
        return configured
    from_file = str(file_metadata.get("build_commit") or "").strip()
    if from_file:
        return from_file
    try:
        root = _repo_root()
        if root is None:
            return ""
        git_dir = root / ".git"
        if git_dir.is_file():
            marker = git_dir.read_text(encoding="utf-8").strip()
            if marker.startswith("gitdir:"):
                git_dir = (git_dir.parent / marker.split(":", 1)[1].strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(":", 1)[1].strip()
        loose = git_dir / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith(("#", "^")):
                    sha, _, name = line.partition(" ")
                    if name.strip() == ref:
                        return sha
    except (OSError, ValueError):
        return ""
    return ""


def _resolve_build_time(file_metadata: dict[str, Any]) -> str:
    """Resolve a real UTC build timestamp; never invent one at import time."""

    for candidate in (
        str(os.getenv("LVKE_MCP_BUILD_TIME") or "").strip(),
        str(file_metadata.get("build_time") or "").strip(),
    ):
        if candidate and candidate.lower() not in _PLACEHOLDERS:
            return candidate
    return ""


def _resolve_plugin_version(file_metadata: dict[str, Any]) -> str:
    for candidate in (
        str(os.getenv("LVKE_MCP_PLUGIN_VERSION") or "").strip(),
        str(file_metadata.get("plugin_version") or "").strip(),
    ):
        if candidate:
            return candidate
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("lvke-mcp")
    except (ImportError, PackageNotFoundError):
        return ""


def utc_now_iso() -> str:
    """Return the current instant as a second-precision UTC ISO-8601 string."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in _PLACEHOLDERS


class BuildMetadata:
    """Immutable per-process snapshot shared by all servers."""

    __slots__ = ("build_commit", "build_time", "plugin_version", "source")

    def __init__(
        self,
        *,
        build_commit: str,
        build_time: str,
        plugin_version: str,
        source: str,
    ) -> None:
        self.build_commit = build_commit
        self.build_time = build_time
        self.plugin_version = plugin_version
        self.source = source

    @property
    def missing_fields(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in ("build_commit", "build_time", "plugin_version")
            if _is_placeholder(str(getattr(self, name)))
        )

    @property
    def complete(self) -> bool:
        return not self.missing_fields

    def envelope_fields(self) -> dict[str, Any]:
        """Fields merged into every tool envelope.

        缺失字段用显式 ``build_metadata_incomplete`` 标注，而不是写
        ``source-checkout`` 占位串让调用方误以为拿到了真实构建时间。
        """

        missing = self.missing_fields
        payload: dict[str, Any] = {
            "build_commit": self.build_commit or INCOMPLETE_CODE,
            "build_time": self.build_time or INCOMPLETE_CODE,
            "plugin_version": self.plugin_version or INCOMPLETE_CODE,
            "build_metadata_complete": not missing,
        }
        if missing:
            payload["build_metadata_status"] = INCOMPLETE_CODE
            payload["build_metadata_missing"] = list(missing)
        return payload

    def startup_report(self) -> dict[str, Any]:
        """Startup validation result; callers surface this instead of guessing."""

        missing = self.missing_fields
        if not missing:
            return {"status": "ok", "metadata_source": self.source}
        return {
            "status": "incomplete",
            "code": INCOMPLETE_CODE,
            "missing": list(missing),
            "metadata_source": self.source,
            "message": "构建元数据不完整，无法确认服务构建版本",
            "next_actions": [
                "构建或安装插件时运行 scripts/write_build_metadata.py 写入 "
                f"{BUILD_METADATA_FILENAME}",
                "或设置 LVKE_MCP_BUILD_TIME / LVKE_MCP_GIT_SHA / "
                "LVKE_MCP_PLUGIN_VERSION 环境变量",
            ],
        }


def _resolve() -> BuildMetadata:
    file_metadata = _load_metadata_file()
    path = _metadata_file()
    return BuildMetadata(
        build_commit=_resolve_commit(file_metadata),
        build_time=_resolve_build_time(file_metadata),
        plugin_version=_resolve_plugin_version(file_metadata),
        source=str(path) if path is not None else "environment",
    )


# 进程内解析一次：14 个服务共享同一快照，避免各自算出不同 build_time。
BUILD_METADATA = _resolve()


def build_metadata() -> BuildMetadata:
    return BUILD_METADATA
