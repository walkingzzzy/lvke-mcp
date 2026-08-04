"""Dictionary-scoped normalization for Chinese administrative names."""

from __future__ import annotations

from difflib import get_close_matches
from typing import Iterable

_SPECIAL_ALIASES = {
    "内蒙古自治区": "内蒙古",
    "广西壮族自治区": "广西",
    "西藏自治区": "西藏",
    "宁夏回族自治区": "宁夏",
    "新疆维吾尔自治区": "新疆",
    "香港特别行政区": "香港",
    "澳门特别行政区": "澳门",
}
_ADMIN_SUFFIXES = (
    "特别行政区",
    "自治区",
    "自治州",
    "地区",
    "市",
    "省",
    "县",
    "区",
    "盟",
)


def administrative_base(value: str) -> str:
    """Remove at most one complete administrative suffix."""

    name = str(value or "").strip()
    if name in _SPECIAL_ALIASES:
        return _SPECIAL_ALIASES[name]
    for suffix in _ADMIN_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def resolve_administrative_name(
    value: str,
    candidates: Iterable[str],
    *,
    suggestion_limit: int = 3,
) -> tuple[str | None, list[str]]:
    """Resolve a user label only when it maps to a value in the local dictionary."""

    requested = str(value or "").strip()
    choices = sorted({str(item).strip() for item in candidates if str(item).strip()})
    if requested in choices:
        return requested, []

    base = administrative_base(requested)
    normalized_matches = [item for item in choices if administrative_base(item) == base]
    if len(normalized_matches) == 1:
        return normalized_matches[0], []

    suggestions = get_close_matches(requested, choices, n=suggestion_limit, cutoff=0.35)
    if len(suggestions) < suggestion_limit:
        normalized_choices = {
            administrative_base(item): item for item in choices
        }
        for matched_base in get_close_matches(
            base,
            list(normalized_choices),
            n=suggestion_limit,
            cutoff=0.35,
        ):
            candidate = normalized_choices[matched_base]
            if candidate not in suggestions:
                suggestions.append(candidate)
            if len(suggestions) >= suggestion_limit:
                break
    return None, suggestions[:suggestion_limit]
