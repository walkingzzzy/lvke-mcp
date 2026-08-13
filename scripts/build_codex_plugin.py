#!/usr/bin/env python3
"""Build the Codex plugin Skill bundle from repository Skill sources."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from write_skill_inventory import write_skill_inventory  # noqa: E402

SOURCE_ROOT = ROOT / "skills"
PLUGIN_SKILLS_ROOT = ROOT / "plugins" / "lvke-mcp" / "skills"
PUBLISHED_SKILLS = (
    "lvke-api-contract",
    "lvke-backend",
    "lvke-delivery-guardrails",
    "lvke-error-recovery",
    "lvke-feasibility-study",
    "lvke-finance",
    "lvke-local-verify",
    "lvke-mcp-acceptance",
    "lvke-project-planning",
    "lvke-report",
    "lvke-research",
    "lvke-review-release",
    "lvke-source-evidence",
    "lvke-tool-coordination",
    # 行业专用：城市轨道交通不能走通用公共服务口径（规模、收入结构、成本口径三处都不同）。
    "lvke-urban-rail-transit",
)


def _rewrite_skill_links(content: str) -> str:
    """Rename only the nested SKILL.md links, never a sibling Skill's top-level one.

    Nested SKILL.md files are renamed to REFERENCE.md, but每个 Skill 的顶层
    SKILL.md 保持原名。此前这里无条件替换全部 "SKILL.md" 字样，于是跨 Skill 的
    ``../../lvke-finance/SKILL.md`` 也被改成 REFERENCE.md —— 目标文件并不存在，
    城轨 catalog 的全部转派链接因此在插件树里断掉。
    """

    # 只改路径中出现 preserved/ 的 SKILL.md 引用（那些文件确实被改名了）。
    # 跨 Skill 的顶层引用不含 preserved/，原样保留。
    return re.sub(
        r"(?P<path>[^\s)\]]*preserved/[^\s)\]]*)SKILL\.md",
        lambda match: match.group("path") + "REFERENCE.md",
        content,
    )


def _ignore_preserved_extras(_directory: str, names: list[str]) -> set[str]:
    return {"self-improvement"} if "self-improvement" in names else set()


def build() -> None:
    PLUGIN_SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    for existing in PLUGIN_SKILLS_ROOT.iterdir():
        if existing.is_dir() and existing.name not in PUBLISHED_SKILLS:
            shutil.rmtree(existing)

    reference_count = 0
    for name in PUBLISHED_SKILLS:
        source = SOURCE_ROOT / name
        destination = PLUGIN_SKILLS_ROOT / name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, ignore=_ignore_preserved_extras)

        for nested_skill in sorted(destination.rglob("SKILL.md")):
            if nested_skill == destination / "SKILL.md":
                continue
            nested_skill.rename(nested_skill.with_name("REFERENCE.md"))
            reference_count += 1

        for markdown in destination.rglob("*.md"):
            content = markdown.read_text(encoding="utf-8")
            rewritten = _rewrite_skill_links(content)
            if rewritten != content:
                markdown.write_text(rewritten, encoding="utf-8")

    # 写出已发布 Skill 清单：路由门禁据此判定 Skill 可加载性。清单必须在
    # Skill 复制完成之后生成，才能反映本次真正发布的集合。
    inventory = write_skill_inventory(PLUGIN_SKILLS_ROOT)

    print(
        f"Built {len(PUBLISHED_SKILLS)} Codex Skills with "
        f"{reference_count} nested reference documents."
    )
    skills = inventory["skills"]
    assert isinstance(skills, list)
    print(f"Skill inventory: {len(skills)} published Skills declared for route gating.")


if __name__ == "__main__":
    build()
