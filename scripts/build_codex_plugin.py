#!/usr/bin/env python3
"""Build the Codex plugin Skill bundle from repository Skill sources."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from write_build_metadata import write_build_metadata  # noqa: E402
from write_skill_inventory import write_skill_inventory  # noqa: E402
from validate_skill_tool_mapping import validate_skill_tool_mapping  # noqa: E402

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
    "lvke-research-report-review",
    "lvke-review-release",
    "lvke-source-evidence",
    "lvke-tool-coordination",
    # 行业专用：城市轨道交通不能走通用公共服务口径（规模、收入结构、成本口径三处都不同）。
    "lvke-urban-rail-transit",
)


def _rewrite_skill_links(content: str) -> str:
    """Rename only the nested SKILL.md links, never a sibling Skill's top-level one."""

    return re.sub(
        r"(?P<path>[^\s)\]]*preserved/[^\s)\]]*)SKILL\.md",
        lambda match: match.group("path") + "REFERENCE.md",
        content,
    )


def _ignore_preserved_extras(_directory: str, names: list[str]) -> set[str]:
    return {"self-improvement"} if "self-improvement" in names else set()


def build(*, release: bool = False) -> None:
    if release:
        write_build_metadata(require_clean=True)
    else:
        try:
            write_build_metadata()
        except RuntimeError as exc:
            print(f"warning: {exc}", file=sys.stderr)

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

    inventory = write_skill_inventory(PLUGIN_SKILLS_ROOT)
    # check_plugin_sync 必须开：此处刚刚重建过 plugin 树，正是唯一能确认
    # "源树与已发布树一致"的时机。此前不传这个开关，双树漂移永远查不出来。
    problems = validate_skill_tool_mapping(strict=release, check_plugin_sync=True)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        # 构建产出的就是 agent 要照着执行的指引；指引里有不存在的工具就是
        # 坏产物，不能只打印警告然后照常宣布构建成功。
        raise SystemExit(1)

    print(
        f"Built {len(PUBLISHED_SKILLS)} Codex Skills with "
        f"{reference_count} nested reference documents."
    )
    skills = inventory["skills"]
    assert isinstance(skills, list)
    print(f"Skill inventory: {len(skills)} published Skills declared for route gating.")
    if release:
        print("Release build: clean metadata + skill/tool mapping verified.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release",
        action="store_true",
        help="技术发布/正式候选：require clean worktree + 映射校验",
    )
    args = parser.parse_args()
    build(release=args.release)


if __name__ == "__main__":
    main()
