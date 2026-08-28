#!/usr/bin/env python3
"""Write uniform build metadata consumed by all 14 Lvke MCP servers.

在构建或插件安装时运行。写出的 ``build_metadata.json`` 让每个服务输出
同一 commit、同一 UTC build_time、同一 plugin_version；不写则服务启动时
显式报 ``build_metadata_incomplete``，而不是退化成占位串。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvke_mcp.runtime.build_metadata import (  # noqa: E402
    BUILD_METADATA_FILENAME,
    utc_now_iso,
)

# 单一生成物随 wheel/插件运行代码一起分发；仓库根不再维护第二份副本。
TARGET = ROOT / "src" / "lvke_mcp" / "runtime" / BUILD_METADATA_FILENAME


def _git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()


def _project_version() -> str:
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return ""
    try:
        parsed = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    project = parsed.get("project")
    return str(project.get("version") or "") if isinstance(project, dict) else ""


def _plugin_version() -> str:
    manifest = ROOT / "plugins" / "lvke-mcp" / ".codex-plugin" / "plugin.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("version") or "") if isinstance(payload, dict) else ""


def _tracked_worktree_clean() -> bool:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return not completed.stdout.strip()


def write_build_metadata(
    *,
    commit: str = "",
    build_time: str = "",
    plugin_version: str = "",
    require_clean: bool = False,
) -> dict[str, str]:
    """Resolve and persist build metadata; return the written payload.

    任一字段无法解析时抛 ``RuntimeError``，不写出半份文件——半份元数据会让
    服务端把占位串当成真实构建时间。
    """

    resolved_commit = commit.strip() or _git_sha()
    resolved_time = build_time.strip() or utc_now_iso()
    resolved_version = plugin_version.strip() or _plugin_version() or _project_version()

    if require_clean and not _tracked_worktree_clean():
        raise RuntimeError(
            "build_metadata_incomplete: tracked worktree 非 clean，"
            "只能生成研发包；技术发布/正式候选须 clean checkout"
        )

    missing = [
        name
        for name, value in (
            ("build_commit", resolved_commit),
            ("build_time", resolved_time),
            ("plugin_version", resolved_version),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("build_metadata_incomplete: 无法解析 " + ", ".join(missing))

    payload = {
        "build_commit": resolved_commit,
        "build_time": resolved_time,
        "plugin_version": resolved_version,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    temporary = TARGET.with_name(f"{TARGET.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, TARGET)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="", help="覆盖 build_commit")
    parser.add_argument("--build-time", default="", help="覆盖 build_time (UTC ISO-8601)")
    parser.add_argument("--plugin-version", default="", help="覆盖 plugin_version")
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="拒绝 dirty tracked worktree（技术发布/正式候选）",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="技术发布模式：等价于 --require-clean",
    )
    args = parser.parse_args()

    try:
        payload = write_build_metadata(
            commit=args.commit,
            build_time=args.build_time,
            plugin_version=args.plugin_version,
            require_clean=args.require_clean or args.release,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for key in ("build_commit", "build_time", "plugin_version"):
        print(f"{key}={payload[key]}")
    print("written: " + str(TARGET.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
