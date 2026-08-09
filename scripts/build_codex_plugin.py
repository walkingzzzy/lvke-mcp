#!/usr/bin/env python3
"""Build the Codex plugin Skill bundle from repository Skill sources."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from write_build_metadata import write_build_metadata  # noqa: E402

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
            if "SKILL.md" in content:
                markdown.write_text(
                    content.replace("SKILL.md", "REFERENCE.md"),
                    encoding="utf-8",
                )

    # 插件安装时注入统一构建元数据；不写则 14 个服务启动即报
    # build_metadata_incomplete，而不是退化成 source-checkout 占位串。
    metadata = write_build_metadata()

    print(
        f"Built {len(PUBLISHED_SKILLS)} Codex Skills with "
        f"{reference_count} nested reference documents."
    )
    print(
        f"Build metadata: commit={metadata['build_commit']} "
        f"build_time={metadata['build_time']} "
        f"plugin_version={metadata['plugin_version']}"
    )


if __name__ == "__main__":
    build()
