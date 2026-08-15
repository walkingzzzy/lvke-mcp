"""正文解析与结构校验：章节切分、锚点、单章合并、结构与一致性检查。"""

from __future__ import annotations

import re
from typing import Any, Optional

from lvke_mcp.domains.reports.headings import canonical_heading_title

from .outline import (
    report_chapter_titles,
    report_structure,
)


def default_report_markdown(title: str = "可行性研究报告", report_type: str = "") -> str:
    """按结构类型生成大纲骨架（含三级小节 + 待补充占位）。"""
    struct = report_structure(report_type)
    lines = [f"# {title}", ""]
    for idx, chapter in enumerate(struct["chapters"], start=1):
        lines.append(f"## {idx}. {chapter['title']}")
        lines.append("")
        subs = chapter.get("subs") or []
        if subs:
            for sidx, sub in enumerate(subs, start=1):
                lines.append(f"### {idx}.{sidx} {sub}")
                lines.append("")
                lines.append("（待补充）")
                lines.append("")
        else:
            lines.append("（待补充）")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def parse_revision_sections(markdown: str) -> list[dict[str, Any]]:
    """把报告 markdown 解析为章节列表(参考可研 parse_revision_sections)。

    每节: ``{level, title, anchor, line, body}``。``anchor`` 用标题归一化生成,
    便于审查意见定位。仅按 ``##``(2 级)切分章节主体。
    """
    lines = markdown.splitlines()
    sections: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    body_lines: list[str] = []

    def _flush() -> None:
        if current is not None:
            current["body"] = "\n".join(body_lines).strip()
            sections.append(current)

    for i, raw in enumerate(lines):
        m = _HEADING_RE.match(raw)
        if m and len(m.group(1)) == 2:
            _flush()
            title = m.group(2).strip()
            current = {
                "level": 2,
                "title": title,
                "anchor": _anchor_for(title),
                "line": i + 1,
                "body": "",
            }
            body_lines = []
        elif current is not None:
            body_lines.append(raw)
    _flush()
    return sections


def _anchor_for(title: str) -> str:
    return canonical_heading_title(title)


def _strip_leading_chapter_title(text: str, target_title: str) -> str:
    """剥离 proposed 开头「与目标章同名」的标题行（任意 #/##/### 级别）。"""
    lines = text.splitlines()
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx < len(lines):
        m = re.match(r"^#{0,3}\s*(.+?)\s*$", lines[idx])
        if m and _anchor_for(m.group(1)) == _anchor_for(target_title):
            idx += 1
            while idx < len(lines) and not lines[idx].strip():
                idx += 1
            return "\n".join(lines[idx:]).strip()
    return text.strip()


def merge_single_chapter_proposal(
    base_md: str, target_title: str, proposed_content: str
) -> Optional[str]:
    """把「单章草稿」合并进完整 base 文档，返回完整 proposed_report。"""
    base_secs = parse_revision_sections(base_md)
    if not base_secs:
        return None
    tgt_anchor = _anchor_for(target_title)
    matching_sections = [s for s in base_secs if s["anchor"] == tgt_anchor]
    if len(matching_sections) != 1:
        return None

    base_lines = base_md.splitlines()
    preamble = "\n".join(base_lines[: base_secs[0]["line"] - 1]).rstrip()
    body_text = _strip_leading_chapter_title(proposed_content, target_title)

    out: list[str] = []
    if preamble:
        out.append(preamble)
        out.append("")
    for s in base_secs:
        out.append(f"## {s['title']}")
        body = body_text if s is matching_sections[0] else s["body"]
        if body:
            out.append("")
            out.append(body)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def validate_report_structure(
    markdown: str,
    report_type: str = "",
    *,
    expected_chapters: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Validate chapter presence, uniqueness and descriptor order.

    Chapter-number prefixes are ignored, but the remaining title must match
    exactly. Duplicate or out-of-order matches fail closed.
    """
    chapters = [
        _anchor_for(str(chapter))
        for chapter in (expected_chapters or [])
        if str(chapter).strip()
    ] or report_chapter_titles(report_type)
    if expected_chapters:
        section_titles = [
            match.group(2).strip()
            for raw_line in str(markdown or "").splitlines()
            if (match := _HEADING_RE.match(raw_line))
        ]
    else:
        section_titles = [s["title"] for s in parse_revision_sections(markdown)]
    normalized = [_anchor_for(t) for t in section_titles]
    missing: list[str] = []
    present: list[str] = []
    duplicates: list[str] = []
    matched_positions: list[int] = []
    for chapter in chapters:
        positions = [index for index, title in enumerate(normalized) if title == chapter]
        if len(positions) == 1:
            present.append(chapter)
            matched_positions.append(positions[0])
        elif len(positions) > 1:
            duplicates.append(chapter)
        else:
            missing.append(chapter)
    issues = [f"缺少章节：{c}" for c in missing]
    issues.extend(f"重复章节：{c}" for c in duplicates)
    out_of_order = any(
        current >= following
        for current, following in zip(matched_positions, matched_positions[1:])
    )
    if out_of_order:
        issues.append("章节顺序与固化大纲不一致")
    return {
        "ok": not issues,
        "missing_chapters": missing,
        "present_chapters": present,
        "duplicate_chapters": duplicates,
        "out_of_order": out_of_order,
        "issues": issues,
    }
