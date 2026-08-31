"""Numeric extraction gates for deterministic field candidate assembly.

Extracting a number from prose is only safe when three independent gates all
pass; any one failing keeps ``numeric_value`` at ``None`` (宁缺勿糊 — a missing
number is safer than a silently wrong one).  The gates, in order:

  Gate 1 (composite unit): ``0.35元/千瓦时`` / ``68元/吨`` are captured as ONE
      semantic unit.  Dropping the denominator would turn ``0.35元/千瓦时``
      into a bare ``0.35元`` and silently mis-scale the field.
  Gate 2 (nearest label + qualifier): a number is attributed to a field only
      when the field's declared label is the *closest* label to it, so two
      same-unit fields (总投资 / 年产值, both 万元) do not cross-contaminate.
      ``require_terms`` / ``exclude_terms`` further separate near-synonym
      measures (销项税率 vs 进项税率) whose labels alone cannot.
  Gate 3 (unit compatibility): the scanned unit must canonically equal the
      caller's ``expected_unit``; a cross-unit value (``2亿元`` for a ``吨/年``
      field) is discarded, never coerced.

The primitives (number/unit patterns, normalisation, calendar-year filter)
are reused from the research-engine quantitative module so that finance
extraction and citation audit agree on what a number even is.
"""

from __future__ import annotations

import re
from typing import Any

from lvke_mcp.domains.research.quantitative import (
    _NUMBER as _Q_NUMBER,
    _UNIT as _Q_UNIT,
    _is_calendar_year,
    _normalize_number,
    _normalize_unit,
)

from .unit_rules import _canon_unit

# Adjacent labels within this many characters merge into one phrase (Gate 2).
_PHRASE_GAP = 2

# Finance denominators for composite units.  Multi-character forms precede the
# shorter ones they contain (``千瓦时`` before ``千瓦``) so the longer unit wins.
_DENOM_UNIT = (
    r"(?:千瓦时|兆瓦时|吉瓦时|千瓦|兆瓦|吉瓦|kWh|MWh|GWh|kW|MW|GW|度|"
    r"吨标煤|标煤|吨|公斤|千克|克|平方米|平米|㎡|m²|m2|平方公里|公顷|ha|亩|立方米|方|"
    r"户|人次|人|千米|公里|km|米|辆|台|套|件|个|年|月|日|小时|班)"
)
# Numerator units: the research-engine set plus finance-specific units it
# deliberately omits (utilisation hours, energy in Chinese, standard coal).
_EXTRA_NUM_UNIT = r"(?:小时|千瓦时|兆瓦时|吉瓦时|千瓦|兆瓦|吉瓦|kWh|MWh|GWh|kW|MW|GW|度|吨标煤|标煤)"
_NUM_UNIT = rf"(?:{_EXTRA_NUM_UNIT}|{_Q_UNIT})"
# Offset-aware measure scanner (Gate 1).  The denominator of a composite unit
# may appear as a suffix (``0.35元/千瓦时``) or, in common Chinese pricing
# wording, as a prefix (``每千瓦时0.35元``).  Both are captured so neither drops
# the denominator and mis-scales the field.
_TEXT_MEASURE_RE = re.compile(
    rf"(?:每\s*(?P<pre_denom>{_DENOM_UNIT})\s*)?"
    rf"(?<!第)(?P<value>{_Q_NUMBER})\s*(?P<unit>{_NUM_UNIT})"
    rf"(?:\s*[/／]\s*(?P<denom>{_DENOM_UNIT}))?",
    re.IGNORECASE,
)

_CELL_REF = re.compile(r"^([A-Za-z]+)([1-9][0-9]*)$")


def _locator_text(locator: dict[str, Any]) -> str:
    """Return a locator's extracted text/value without fabricating a value."""

    for key in ("text", "content", "original_value", "display_value", "cached_value", "value"):
        value = locator.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def _numeric_cell_value(locator: dict[str, Any]) -> int | float | None:
    """Only expose a numeric candidate when it was already a parsed table cell.

    Parsing arbitrary prose into a number would make a token such as a year look
    like an investment or capacity value.  Table cells are already parsed, so
    their numeric value is authoritative; prose is handled by the offset-aware
    three-gate scanner (:func:`_numeric_text_value`) instead.
    """

    if locator.get("kind") != "cell":
        return None
    value = locator.get("cached_value", locator.get("display_value"))
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _cell_position(reference: str) -> tuple[int, int] | None:
    match = _CELL_REF.fullmatch(str(reference or ""))
    if match is None:
        return None
    column = 0
    for letter in match.group(1).upper():
        column = column * 26 + (ord(letter) - ord("A") + 1)
    return int(match.group(2)), column


def _span_distance(a0: int, a1: int, b0: int, b1: int) -> int:
    """Gap between two character spans; 0 when they overlap."""

    if a1 <= b0:
        return b0 - a1
    if b1 <= a0:
        return a0 - b1
    return 0


def _build_term_fields(field_specs: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Map each lower-cased alias term to the set of fields declaring it.

    A term shared by several fields (``税率``) has no discriminative power on
    its own; a term declared by exactly one field (``企业所得税``) is the
    identifying evidence.  Gate 2 relies on this distinction.
    """

    term_fields: dict[str, set[str]] = {}
    for spec in field_specs:
        field = str(spec.get("field") or "").strip()
        if not field:
            continue
        aliases = [field, *[str(item).strip() for item in (spec.get("aliases") or [])]]
        for alias in aliases:
            if alias:
                term_fields.setdefault(alias.lower(), set()).add(field)
    return term_fields


def _label_occurrences(
    text_low: str, term_fields: dict[str, set[str]]
) -> list[tuple[int, int, frozenset[str]]]:
    """Every occurrence of every declared label term, with its owning fields."""

    occurrences: list[tuple[int, int, frozenset[str]]] = []
    for term, fields in term_fields.items():
        start = text_low.find(term)
        while start != -1:
            occurrences.append((start, start + len(term), frozenset(fields)))
            start = text_low.find(term, start + 1)
    return occurrences


#: 分句标记。标签与数字之间一旦跨越这些标记，就不属于同一分句，不能配对。
#: 顿号是关键：中文成本/投资明细正是用它并列的（"主要原材料 46,968.00 万元、
#: 水电能源 1,600.00 万元"）。纯字符距离下"水电能源"离 46,968 只有 13 字符、
#: 离它自己的 1,600 有 19 字符，于是 Gate 2 把原材料的金额判给了水电能源，
#: 且 attribution_gate 仍报 passed —— 候选事实的 numeric_value 是别项的数。
_CLAUSE_MARKERS = frozenset("，,；;。、：:（）()")


def _crosses_clause_boundary(text: str, a_end: int, b_start: int) -> bool:
    """True when a clause marker separates two spans."""

    if not text or a_end >= b_start:
        return False
    return any(ch in _CLAUSE_MARKERS for ch in text[a_end:b_start])


def _nearest_label_fields(
    occurrences: list[tuple[int, int, frozenset[str]]],
    m_start: int,
    m_end: int,
    text: str = "",
) -> tuple[set[str], int, int]:
    """Fields owning the label phrase closest to a measure (Gate 2 core).

    The single nearest label seeds a phrase; adjacent labels (gap ≤
    ``_PHRASE_GAP``) merge in.  Owning fields are the phrase terms' intersection
    (``企业所得税``+``税率`` narrows to the income-tax field); an empty
    intersection means the adjacent labels conflict, so we fall back to the
    union and let the unit / qualifier gates decide rather than guessing here.

    Returns the owning fields together with the winning phrase span so the
    qualifier gate can inspect that phrase's own context rather than a window
    around the number (which would let a distant ``进项`` leak into a nearby
    销项 rate).
    """

    if not occurrences:
        return set(), m_start, m_end
    # 先按分句过滤：跨越顿号/逗号等标记的标签与本数字不在同一分句，不参与
    # 最近判定。全部被过滤时退回原集合，让单位与限定词门去裁决，而不是在这里
    # 猜一个——宁可后续门拒绝，也不要配错。
    if text:
        same_clause = [
            occ for occ in occurrences
            if not _crosses_clause_boundary(text, occ[1], m_start)
            and not _crosses_clause_boundary(text, m_end, occ[0])
        ]
        if same_clause:
            occurrences = same_clause
    ordered = sorted(occurrences, key=lambda o: _span_distance(m_start, m_end, o[0], o[1]))
    p_start, p_end = ordered[0][0], ordered[0][1]
    phrase = [ordered[0]]
    for occ in ordered[1:]:
        if _span_distance(p_start, p_end, occ[0], occ[1]) <= _PHRASE_GAP:
            p_start = min(p_start, occ[0])
            p_end = max(p_end, occ[1])
            phrase.append(occ)
    intersection: set[str] = set(phrase[0][2])
    union: set[str] = set()
    for _, _, fields in phrase:
        intersection &= set(fields)
        union |= set(fields)
    return (intersection or union), p_start, p_end


def _numeric_text_measure(
    text: str,
    field: str,
    spec: dict[str, Any],
    term_fields: dict[str, set[str]],
    *,
    segment_role: str = "",
) -> dict[str, Any]:
    """Return a prose number only when all three attribution gates pass.

    Returns ``None`` (not a guess) whenever any gate rejects: no expected unit
    match, the nearest label belongs to another field, a required qualifier is
    absent, or an excluded qualifier is present.
    """

    expected = _canon_unit(str(spec.get("expected_unit") or "")) if spec.get("expected_unit") else ""
    if not expected:
        # Prose without an expected unit remains a text candidate.  Otherwise
        # nearby menu/version/year numbers can be promoted merely because a
        # broad alias appears in the same page fragment.
        return {"numeric_value": None, "attribution_gate": {"status": "rejected", "reason": "expected_unit_required"}}
    if str(segment_role or "").strip().lower() in {
        "nav", "navigation", "header", "footer", "breadcrumb", "menu",
    }:
        return {"numeric_value": None, "attribution_gate": {"status": "rejected", "reason": "excluded_segment_role"}}
    require = [str(t).strip().lower() for t in (spec.get("require_terms") or []) if str(t).strip()]
    exclude = [str(t).strip().lower() for t in (spec.get("exclude_terms") or []) if str(t).strip()]
    text_low = text.lower()
    occurrences = _label_occurrences(text_low, term_fields)
    own_occurrences = [occ for occ in occurrences if field in occ[2]]
    best: tuple[int, dict[str, Any]] | None = None
    saw_measure = False
    rejection_reason = "no_compatible_measure"
    for match in _TEXT_MEASURE_RE.finditer(text):
        saw_measure = True
        number = _normalize_number(match.group("value"))
        raw_base_unit = re.sub(r"\s+", "", str(match.group("unit") or ""))
        base_unit = _normalize_unit(match.group("unit"))
        if _is_calendar_year(number, base_unit):
            continue  # a bare year is scope, never a measured value
        # A denominator may appear after the numerator (``0.35元/千瓦时``) or as a
        # Chinese prefix (``每千瓦时0.35元``); both mean the same composite unit.
        denom = match.group("denom") or match.group("pre_denom")
        unit_str = base_unit + (f"/{denom}" if denom else "")
        raw_unit = raw_base_unit + (f"/{denom}" if denom else "")
        # Gate 3: unit must canonically match the caller's expected unit.
        if expected and _canon_unit(unit_str) != expected:
            rejection_reason = "unit_incompatible"
            continue
        # Gate 2: the closest declared label must belong to this field.
        owners, p_start, p_end = _nearest_label_fields(
            occurrences, match.start(), match.end(), text
        )
        if field not in owners:
            rejection_reason = "nearest_label_mismatch"
            continue
        # Qualifiers are judged on the winning label phrase plus the modifier
        # that immediately precedes it (``进项``/``销项`` sit just before
        # ``税率``), not a window around the number — otherwise a distant
        # ``进项`` in the same sentence would leak into a nearby 销项 rate.
        if require or exclude:
            phrase_window = text_low[max(0, p_start - _PHRASE_GAP - 4): p_end]
            if require and not all(term in phrase_window for term in require):
                rejection_reason = "required_qualifier_missing"
                continue
            if exclude and any(term in phrase_window for term in exclude):
                rejection_reason = "excluded_qualifier_present"
                continue
        try:
            value_f = float(number)
        except ValueError:
            continue
        value: int | float = int(value_f) if value_f.is_integer() else value_f
        distance = min(
            (_span_distance(match.start(), match.end(), o[0], o[1]) for o in own_occurrences),
            default=len(text),
        )
        measure = {
            "numeric_value": value,
            "raw_unit": re.sub(r"\s+", "", raw_unit),
            "normalized_unit": _canon_unit(unit_str),
            "numeric_offset": match.start("value"),
            "numeric_end_offset": match.end("value"),
            "measure_offset": match.start(),
            "measure_end_offset": match.end(),
            "attribution_gate": {
                "status": "passed",
                "reason": None,
                "label_span": [p_start, p_end],
            },
        }
        if best is None or distance < best[0]:
            best = (distance, measure)
    if best is not None:
        return best[1]
    return {
        "numeric_value": None,
        "attribution_gate": {
            "status": "rejected",
            "reason": rejection_reason if saw_measure else "no_measure_found",
        },
    }
