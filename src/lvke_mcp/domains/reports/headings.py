"""Shared, deterministic Markdown heading matching for report documents."""

from __future__ import annotations

import re


#: 多级编号（``3.1 需求分析`` / ``5.3.2 设备选型``）必须单独成条，且允许以空白
#: 作为终止符。此前只有一条 ``\d+(?:\s*[.．]\s*\d+)*\s*(?:章|节|篇|部分|[、.．:：])``：
#: 多级编号后面通常直接跟空格而非「、」或「.」，终止符匹配不上就整体回溯到零重复，
#: 于是 ``3.1 需求分析`` 只被吃掉 ``3.``，规范化结果是 ``1 需求分析``。后果是三级
#: 小节标题永远匹配不上 outline 里登记的裸标题（``需求分析``），``section_span``
#: 返回 None，``report_propose_section`` 一律以 ``section_absent_from_document``
#: 拒绝——正文的最小可写单元被压成「整章」，这是长报告写不进去的直接原因。
#:
#: 单位字（章/节/篇/部分）必须 **独立成词**，即后面紧跟空白或行尾（``(?=\s|$)``）。
#: 不加这个限定时，``8.4.3 节能措施与碳排放测算`` 会被前面的 ``\s*`` 吃掉空格、
#: 再拿正文首字 ``节`` 当终止符，规范化成 ``能措施与碳排放测算``——标题被吃掉一个字。
#: 同时保留 ``第 3.1 节 方案`` 这类单位字真的独立存在的形式。
_MULTI_LEVEL_ARABIC_PREFIX_RE = re.compile(
    r"^\s*(?:第\s*)?\d+(?:\s*[.．]\s*\d+)+"
    r"(?:\s*(?:章|节|篇|部分)(?=\s|$)|[、.．:：]|(?=\s))\s*"
)
_ARABIC_PREFIX_RE = re.compile(
    r"^\s*(?:第\s*)?\d+"
    r"(?:\s*(?:章|节|篇|部分)(?=\s|$)|\s*[、.．:：])\s*"
)
_CHINESE_CHAPTER_PREFIX_RE = re.compile(
    r"^\s*第\s*[零〇一二三四五六七八九十百千两]+\s*(?:章|节|篇|部分)\s*"
)
_PAREN_PREFIX_RE = re.compile(
    r"^\s*[（(]\s*(?:\d+|[零〇一二三四五六七八九十百千两]+)\s*[）)]\s*"
)
_CHINESE_LIST_PREFIX_RE = re.compile(
    r"^\s*[零〇一二三四五六七八九十百千两]+\s*[、.．]\s*"
)


def canonical_heading_title(title: str) -> str:
    """Remove a conventional chapter-number prefix without fuzzy matching."""

    value = str(title or "").strip()
    for pattern in (
        _CHINESE_CHAPTER_PREFIX_RE,
        _PAREN_PREFIX_RE,
        _MULTI_LEVEL_ARABIC_PREFIX_RE,
        _ARABIC_PREFIX_RE,
        _CHINESE_LIST_PREFIX_RE,
    ):
        cleaned = pattern.sub("", value, count=1).strip()
        if cleaned != value:
            return cleaned or value
    return value


def heading_titles_match(left: str, right: str) -> bool:
    """Return true only for an exact match after chapter-prefix normalization."""

    left_title = canonical_heading_title(left)
    right_title = canonical_heading_title(right)
    return bool(left_title and left_title == right_title)
