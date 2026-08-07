"""正文解析与结构校验：章节切分、锚点、单章合并、结构与一致性检查。"""

from __future__ import annotations

import re
from typing import Any, Optional



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
    cleaned = re.sub(r"^[0-9.\s、]+", "", title).strip()
    return cleaned or title.strip()


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
    if not any(s["anchor"] == tgt_anchor for s in base_secs):
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
        body = body_text if s["anchor"] == tgt_anchor else s["body"]
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
    """按结构类型校验章节完整性。

    返回 ``{ok, missing_chapters, present_chapters, issues}``。缺少任一章节标题
    即 ``ok=False``。匹配按"章节名包含"宽松判定,容忍编号前缀差异。
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
    for chapter in chapters:
        if any(chapter in t or t in chapter for t in normalized):
            present.append(chapter)
        else:
            missing.append(chapter)
    issues = [f"缺少章节：{c}" for c in missing]
    return {
        "ok": not missing,
        "missing_chapters": missing,
        "present_chapters": present,
        "issues": issues,
    }
