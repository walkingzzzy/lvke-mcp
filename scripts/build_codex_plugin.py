#!/usr/bin/env python3
"""Build the Codex plugin Skill bundle from repository Skill sources."""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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

    print(
        f"Built {len(PUBLISHED_SKILLS)} Codex Skills with "
        f"{reference_count} nested reference documents."
    )


if __name__ == "__main__":
    build()
