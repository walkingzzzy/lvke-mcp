"""Skill inventory resolution: host-declared truth first, disk only offline.

一个 Skill 名字"在磁盘上找得到"并不等于"当前宿主任务真的加载了它"。源码 checkout、
构建树和 ``~/.claude/skills`` 三处都可能存在，而 Codex 实际加载的是
``~/.codex/plugins/cache/<plugin>/skills``；反过来，一台只装了插件的机器上源码树根本
不存在。用磁盘冒充运行时 inventory 会两头出错：

- 假阳性：源码 checkout 在，于是路由报 installed，调用方以为已取得行业口径，
  实际宿主里那个 Skill 从未被加载，只能静默退回自己的通用假设。
- 假阴性：只装插件的机器上源码树缺失，本可加载的 Skill 被判为缺失而阻断。

因此运行时资格只承认宿主显式声明的 inventory（``LVKE_MCP_SKILL_INVENTORY`` 或
``LVKE_MCP_SKILL_INVENTORY_FILE``）。宿主没声明时不假装知道：返回
``source="unavailable"``，由调用方决定如何降级，且响应里必须如实带上这个来源。
磁盘扫描仅用于离线校验（构建期、测试期），并始终标注 ``source="disk_offline"``。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_ENV_INLINE = "LVKE_MCP_SKILL_INVENTORY"
_ENV_FILE = "LVKE_MCP_SKILL_INVENTORY_FILE"

_REPO_ROOT = Path(__file__).resolve().parents[3]
# 离线校验用的搜索根。顺序无语义，命中即计入；这些路径不代表宿主运行时状态。
OFFLINE_SKILL_ROOTS: tuple[Path, ...] = (
    _REPO_ROOT / "skills",
    _REPO_ROOT / "plugins" / "lvke-mcp" / "skills",
)


def _parse_names(raw: Any) -> set[str]:
    """Accept a JSON array, a JSON object with ``skills``, or a comma/space list."""

    if isinstance(raw, dict):
        raw = raw.get("skills") or raw.get("names") or []
    if isinstance(raw, list):
        return {
            str(item.get("name") if isinstance(item, dict) else item).strip()
            for item in raw
            if str(item.get("name") if isinstance(item, dict) else item).strip()
        }
    text = str(raw or "").strip()
    if not text:
        return set()
    if text[0] in "[{":
        try:
            return _parse_names(json.loads(text))
        except json.JSONDecodeError:
            return set()
    return {part.strip() for part in text.replace(",", " ").split() if part.strip()}


def host_skill_inventory() -> dict[str, Any]:
    """Return the inventory the host declared, or an explicit ``unavailable``.

    ``source`` is part of the contract: callers must surface it so a reader can
    tell a real host inventory from "we could not know".
    """

    file_path = str(os.environ.get(_ENV_FILE) or "").strip()
    if file_path:
        try:
            raw = Path(file_path).read_text(encoding="utf-8")
        except OSError:
            return {
                "source": "unavailable",
                "names": set(),
                "detail": f"{_ENV_FILE} 指向的文件不可读：{file_path}",
            }
        names = _parse_names(raw)
        if not names:
            return {
                "source": "unavailable",
                "names": set(),
                "detail": f"{_ENV_FILE} 内容不是可解析的 Skill 清单：{file_path}",
            }
        return {"source": "host_declared", "names": names, "detail": ""}

    inline = os.environ.get(_ENV_INLINE)
    if inline is not None and str(inline).strip():
        names = _parse_names(inline)
        if not names:
            return {
                "source": "unavailable",
                "names": set(),
                "detail": f"{_ENV_INLINE} 内容不是可解析的 Skill 清单",
            }
        return {"source": "host_declared", "names": names, "detail": ""}

    return {
        "source": "unavailable",
        "names": set(),
        "detail": (
            f"宿主未声明已加载 Skill 清单；设置 {_ENV_INLINE} 或 {_ENV_FILE} "
            "才能在运行时校验 Skill 可加载性"
        ),
    }


def offline_skill_names(roots: tuple[Path, ...] | None = None) -> set[str]:
    """Return Skill names resolving to a readable SKILL.md under offline roots.

    仅供构建期与测试期的离线校验使用：它回答"仓库里是否写了这个 Skill"，
    不回答"宿主是否加载了它"。
    """

    names: set[str] = set()
    for root in roots or OFFLINE_SKILL_ROOTS:
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            # 没有 SKILL.md 的目录无法被加载，即使名字存在也不算。
            if (entry / "SKILL.md").is_file():
                names.add(entry.name)
    return names


def resolve_skill_inventory() -> dict[str, Any]:
    """Resolve the inventory used for gating, preferring the host declaration."""

    host = host_skill_inventory()
    if host["source"] == "host_declared":
        return host
    offline = offline_skill_names()
    if not offline:
        return host
    return {
        "source": "disk_offline",
        "names": offline,
        "detail": host["detail"],
    }
