"""Stage 3 · 章节切片器。

把单份 MD 切成「整章 chunk」+「超长章节段落级 sub-chunk」。
章节号归一化：在标题里识别中文/阿拉伯数字 + 关键词匹配映射到 1-9 章；
非正文标题（封面 / 目录 / 附表 / 附录）记 chapter_no=0。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# 章节关键词 → 章节号
CHAPTER_KEYWORDS: list[tuple[str, int]] = [
    ("总论", 1), ("概述", 1), ("项目概况", 1), ("概 述", 1),
    ("背景", 2), ("必要性", 2),
    ("需求", 3), ("建设规模", 3), ("规模", 3), ("产出方案", 3), ("市场分析", 3),
    ("建设方案", 4), ("总体方案", 4), ("工程方案", 4), ("总体设计", 4),
    ("投资估算", 5), ("资金筹措", 5), ("总投资", 5),
    ("财务", 6), ("经济评价", 6), ("经济分析", 6), ("效益分析", 6),
    ("风险", 7),
    ("保障", 8), ("节能", 8), ("环境影响", 8), ("环境", 8), ("劳动安全", 8), ("职业卫生", 8),
    ("结论", 9), ("建议", 9), ("研究结论", 9),
]

# 章节号识别
ZH_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
CN_CHAPTER_RE = re.compile(r"第\s*([一二三四五六七八九十]+|\d+)\s*章")
LEADING_NUM_RE = re.compile(r"^\s*(\d{1,2})[\.、\s]")


def _parse_chapter_no(title: str) -> int:
    m = CN_CHAPTER_RE.search(title)
    if m:
        raw = m.group(1)
        if raw.isdigit():
            n = int(raw)
        else:
            n = ZH_NUM.get(raw[0], 0)
            if len(raw) > 1 and raw[0] == "十":
                # "十一章" 这类极少；可研 9 章封顶，简单兜底
                n = 10 + ZH_NUM.get(raw[1], 0)
        if 1 <= n <= 9:
            return n
    m2 = LEADING_NUM_RE.match(title.lstrip("# ").strip())
    if m2:
        n = int(m2.group(1))
        if 1 <= n <= 9:
            return n
    # 关键词回退
    for kw, no in CHAPTER_KEYWORDS:
        if kw in title:
            return no
    return 0


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    report_id: str
    chapter_no: int
    chapter_title: str
    level: int
    content: str
    char_len: int
    parent_chunk_id: str | None = None


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
TOC_LIKE_LINE_RE = re.compile(r"\.\.\.\.+\s*\d+\s*$")
PAGE_NUM_RE = re.compile(r"^\s*\d{1,4}\s*$")

# 公开案例库常见的"纯文本章节行"模式：
#   第一章 / 第一部份 / 第一节 / 第一部分
#   一、 二、 三、…（顶级数字编号）
#   1、 1.1 1.1.1（阿拉伯数字编号）
# 仅捕获**单独成行**且开头位于行首的章节起点。
PLAINTEXT_SECTION_RE = re.compile(
    r"^[ \t]*("
    r"第\s*[一二三四五六七八九十百\d]+\s*(?:章|部分|部份|节)"      # 第X章/部分/节
    r"|[一二三四五六七八九十]\s*、"                                # 一、 二、
    r"|\d{1,2}\s*[、\.]\s+"                                         # 1、 1.
    r")\s*([^\n]{1,80})$",
    re.MULTILINE,
)
TOC_HYPERLINK_RE = re.compile(r"HYPERLINK\s+\\l", re.IGNORECASE)


def _looks_like_toc(text: str) -> bool:
    """目录页：高密度形如 `xxx ... 12` 的行或 HYPERLINK 标记。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    toc_lines = sum(1 for ln in lines if TOC_LIKE_LINE_RE.search(ln))
    hyperlink_lines = sum(1 for ln in lines if TOC_HYPERLINK_RE.search(ln))
    if hyperlink_lines >= 5 and hyperlink_lines / len(lines) > 0.3:
        return True
    return toc_lines >= 5 and toc_lines / len(lines) > 0.4


def _strip_toc_block(text: str) -> str:
    """剔除 Word TOC 残留行（"HYPERLINK \\l _TocXX..." 形式），返回净化后的正文。"""
    if "HYPERLINK" not in text:
        return text
    kept: list[str] = []
    for ln in text.splitlines():
        if TOC_HYPERLINK_RE.search(ln):
            continue
        if ln.strip().startswith("TOC \\o"):
            continue
        kept.append(ln)
    return "\n".join(kept)


def _split_by_plaintext_sections(text: str) -> list[tuple[str, str]]:
    """按纯文本章节行切分；返回 ``[(title, body), ...]``。无匹配时返回 ``[]``。

    空 body 的章节也会保留（title 转为可检索的 placeholder content），
    避免丢失"第一部份"这类只在标题里出现的章节信息。
    """
    matches = list(PLAINTEXT_SECTION_RE.finditer(text))
    if len(matches) < 3:  # 少于 3 个章节标记，认为不可靠
        return []
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = (m.group(1) + (m.group(2) or "")).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        # body 为空时仍保留章节(title 作为可检索 content)
        sections.append((title, body or title))
    return sections


def _split_paragraphs(text: str, max_chars: int = 2000) -> list[str]:
    """超长章节按段落滑窗二切。"""
    if len(text) <= max_chars:
        return [text]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}".strip() if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= max_chars:
                buf = p
            else:
                # 单段超长，硬切
                for i in range(0, len(p), max_chars):
                    chunks.append(p[i : i + max_chars])
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def split(report_id: str, full_text: str) -> list[Chunk]:
    """Split a full MD document into chunks."""
    full_text = _strip_toc_block(full_text)
    # 找到所有标题位置
    matches = list(HEADING_RE.finditer(full_text))

    # 决策：如果只有 1-2 个 markdown 标题（典型为只有 H1 title），
    # 尝试用纯文本章节行回退切分
    use_plaintext = False
    if len(matches) <= 2:
        plain_sections = _split_by_plaintext_sections(full_text)
        if plain_sections:
            use_plaintext = True

    if use_plaintext:
        chunks: list[Chunk] = []
        for idx, (title, body) in enumerate(plain_sections):
            chapter_no = _parse_chapter_no(title)
            parts = _split_paragraphs(body, max_chars=2000)
            if len(parts) == 1:
                chunks.append(
                    Chunk(
                        chunk_id=f"{report_id}#c{chapter_no}#t{idx}",
                        report_id=report_id,
                        chapter_no=chapter_no,
                        chapter_title=title,
                        level=2,
                        content=parts[0],
                        char_len=len(parts[0]),
                    )
                )
            else:
                parent_id = f"{report_id}#c{chapter_no}#t{idx}"
                summary = parts[0][:600]
                chunks.append(
                    Chunk(
                        chunk_id=parent_id,
                        report_id=report_id,
                        chapter_no=chapter_no,
                        chapter_title=title,
                        level=2,
                        content=summary,
                        char_len=len(summary),
                    )
                )
                for sub_i, sub in enumerate(parts):
                    chunks.append(
                        Chunk(
                            chunk_id=f"{parent_id}#p{sub_i}",
                            report_id=report_id,
                            chapter_no=chapter_no,
                            chapter_title=title,
                            level=3,
                            content=sub,
                            char_len=len(sub),
                            parent_chunk_id=parent_id,
                        )
                    )
        if chunks:
            return chunks

    if not matches:
        # 没标题：整文件一条 chunk，归 chapter_no=0
        text = full_text.strip()
        if not text:
            return []
        return [
            Chunk(
                chunk_id=f"{report_id}#c0#p0",
                report_id=report_id,
                chapter_no=0,
                chapter_title="(no heading)",
                level=0,
                content=text,
                char_len=len(text),
            )
        ]

    chunks: list[Chunk] = []
    for idx, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        body_start = m.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(full_text)
        body = full_text[body_start:body_end].strip()
        if not body:
            continue
        if _looks_like_toc(body):
            chapter_no = 0
        else:
            chapter_no = _parse_chapter_no(title)

        # 长章节段落级二切
        parts = _split_paragraphs(body, max_chars=2000)
        if len(parts) == 1:
            chunks.append(
                Chunk(
                    chunk_id=f"{report_id}#c{chapter_no}#h{idx}",
                    report_id=report_id,
                    chapter_no=chapter_no,
                    chapter_title=title,
                    level=level,
                    content=parts[0],
                    char_len=len(parts[0]),
                )
            )
        else:
            parent_id = f"{report_id}#c{chapter_no}#h{idx}"
            # 父 chunk 记一个 summary：用前 600 字 + 末段
            summary = parts[0][:600]
            chunks.append(
                Chunk(
                    chunk_id=parent_id,
                    report_id=report_id,
                    chapter_no=chapter_no,
                    chapter_title=title,
                    level=level,
                    content=summary,
                    char_len=len(summary),
                )
            )
            for sub_i, sub in enumerate(parts):
                chunks.append(
                    Chunk(
                        chunk_id=f"{parent_id}#p{sub_i}",
                        report_id=report_id,
                        chapter_no=chapter_no,
                        chapter_title=title,
                        level=level + 1,
                        content=sub,
                        char_len=len(sub),
                        parent_chunk_id=parent_id,
                    )
                )
    return chunks

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "CHAPTER_KEYWORDS",
    "CN_CHAPTER_RE",
    "Chunk",
    "HEADING_RE",
    "Iterable",
    "LEADING_NUM_RE",
    "PAGE_NUM_RE",
    "PLAINTEXT_SECTION_RE",
    "TOC_HYPERLINK_RE",
    "TOC_LIKE_LINE_RE",
    "ZH_NUM",
    "_looks_like_toc",
    "_parse_chapter_no",
    "_split_by_plaintext_sections",
    "_split_paragraphs",
    "_strip_toc_block",
    "dataclass",
    "re",
    "split",
]
