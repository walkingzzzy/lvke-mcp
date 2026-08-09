"""Skill inventory resolution: host-declared truth first, disk only offline.

一个 Skill 名字"在磁盘上找得到"并不等于"当前宿主任务真的加载了它"。源码 checkout、
构建树和 ``~/.claude/skills`` 三处都可能存在，而 Codex 实际加载的是
``~/.codex/plugins/cache/<plugin>/skills``；反过来，一台只装了插件的机器上源码树根本
不存在。用磁盘冒充运行时 inventory 会两头出错：

- 假阳性：源码 checkout 在，于是路由报 installed，调用方以为已取得行业口径，
  实际宿主里那个 Skill 从未被加载，只能静默退回自己的通用假设。
- 假阴性：只装插件的机器上源码树缺失，本可加载的 Skill 被判为缺失而阻断。

因此运行时资格只承认两类**声明**，按优先级：

1. ``host_declared`` —— 宿主显式声明 ``LVKE_MCP_SKILL_INVENTORY`` /
   ``LVKE_MCP_SKILL_INVENTORY_FILE``。宿主最清楚自己加载了什么，优先级最高。
2. ``packaged_build`` —— 构建时随代码写出的 ``skill_inventory.json``
   （与 ``build_metadata.json`` 同一模式）。它就是与这份运行代码一同发布的
   Skill 集合，因此是合法的运行时依据，且**不需要每个宿主手写 env** ——
   只依赖 env 的话，没有任何宿主设置它时该门禁会对所有真实调用恒定降级。

两者都没有时不假装知道：磁盘扫描仅作诚实降级，标注 ``source="disk_offline"``，
它只能证明"仓库里写了"，不能证明"这份部署带上了"。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_ENV_INLINE = "LVKE_MCP_SKILL_INVENTORY"
_ENV_FILE = "LVKE_MCP_SKILL_INVENTORY_FILE"

# 构建时随代码一同写出的已发布 Skill 清单（与 build_metadata.json 同一模式）。
# 它描述的是"与这份运行代码一起发布的 Skill 集合"，因此比扫描源码树可靠：
# 源码树可能不存在（只装插件），也可能存在但与运行代码无关（另一个 checkout）。
SKILL_INVENTORY_FILENAME = "skill_inventory.json"

_REPO_ROOT = Path(__file__).resolve().parents[3]
# 离线校验用的搜索根。顺序无语义，命中即计入；这些路径不代表宿主运行时状态。
OFFLINE_SKILL_ROOTS: tuple[Path, ...] = (
    _REPO_ROOT / "skills",
    _REPO_ROOT / "plugins" / "lvke-mcp" / "skills",
)


def _packaged_inventory_file() -> Path | None:
    """Return the packaged inventory file if present.

    只认包内这一处。它在 wheel/插件安装布局与源码树布局下都是同一相对位置，
    因此不需要第二个回退位置；多一个回退只会在这份缺失时悄悄顶上，让"清单缺失"
    这件事变得不可观测。
    """

    candidate = Path(__file__).resolve().parent / SKILL_INVENTORY_FILENAME
    return candidate if candidate.is_file() else None


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


def declared_skill_inventory() -> dict[str, Any]:
    """Return the declared inventory (host env or packaged manifest).

    ``source`` is part of the contract: callers must surface it so a reader can
    tell a real declaration from "we could not know".
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

    packaged = _packaged_inventory_file()
    if packaged is not None:
        try:
            names = _parse_names(packaged.read_text(encoding="utf-8"))
        except OSError:
            names = set()
        if names:
            # 与运行代码同批发布的清单：它就是这份部署真正带上的 Skill 集合，
            # 因此是运行时资格的合法来源，而不是"猜"。
            return {"source": "packaged_build", "names": names, "detail": ""}

    return {
        "source": "unavailable",
        "names": set(),
        "detail": (
            f"未找到已发布 Skill 清单（{SKILL_INVENTORY_FILENAME}），"
            f"宿主也未设置 {_ENV_INLINE} / {_ENV_FILE}；"
            "无法在运行时校验 Skill 可加载性"
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


#: 可作为运行时资格依据的来源。``disk_offline`` 不在其中：源码树存在只说明
#: 仓库里写了这个 Skill，不说明这份部署带上了它。
AUTHORITATIVE_SOURCES = frozenset({"host_declared", "packaged_build"})


def resolve_skill_inventory() -> dict[str, Any]:
    """Resolve the inventory used for gating.

    优先级：宿主显式声明 → 随代码发布的清单 → 离线磁盘扫描（仅诚实降级用）。
    前两者是权威来源，第三者只用于"能证明缺失"，不能证明可加载。
    """

    declared = declared_skill_inventory()
    if declared["source"] in AUTHORITATIVE_SOURCES:
        return declared
    offline = offline_skill_names()
    if not offline:
        return declared
    return {
        "source": "disk_offline",
        "names": offline,
        "detail": declared["detail"],
    }
