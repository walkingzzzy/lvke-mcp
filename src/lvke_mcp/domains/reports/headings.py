"""Shared, deterministic Markdown heading matching for report documents."""

from __future__ import annotations

import re


_ARABIC_PREFIX_RE = re.compile(
    r"^\s*(?:第\s*)?\d+(?:\s*[.．]\s*\d+)*\s*(?:章|节|篇|部分|[、.．:：])\s*"
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
