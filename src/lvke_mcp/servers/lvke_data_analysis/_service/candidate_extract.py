"""Deterministic field candidate extraction with three-gate numeric attribution.

This is deterministic string matching over already-ingested content. It is
deliberately not an LLM extraction path and does not infer units, table
semantics, or missing project inputs.
"""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters.data_analysis_repository import CANDIDATE_STORE

from .envelope import _missing
from .ingest import _documents_from_task
from .numeric_gates import (
    _build_term_fields,
    _locator_text,
    _numeric_cell_value,
    _numeric_text_measure,
)
from .unit_rules import normalize_controlled_measure


_FIELD_HEADERS = frozenset({"field", "key", "name", "metric", "parameter", "字段", "参数", "指标", "项目"})
_VALUE_HEADERS = frozenset({"value", "numeric_value", "amount", "数值", "值", "金额", "取值"})
_UNIT_HEADERS = frozenset({"unit", "units", "单位", "计量单位"})


def _header_kind(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _FIELD_HEADERS:
        return "field"
    if normalized in _VALUE_HEADERS:
        return "value"
    if normalized in _UNIT_HEADERS:
        return "unit"
    return ""


def _structured_csv_candidate(
    document: dict[str, Any],
    field: str,
    aliases: list[str],
    spec: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve a field/value/unit tuple from one CSV row without proximity guessing."""

    cells = [
        item for item in (document.get("locators") or [])
        if isinstance(item, dict)
        and item.get("kind") == "cell"
        and item.get("table_kind") == "csv"
        and item.get("is_header") is not True
    ]
    if not cells:
        return None
    rows: dict[int, dict[str, dict[str, Any]]] = {}
    for cell in cells:
        kind = _header_kind(cell.get("header_name"))
        if not kind:
            continue
        rows.setdefault(int(cell.get("row_index") or 0), {})[kind] = cell
    alias_set = {str(item).strip().lower() for item in aliases if str(item).strip()}
    for row_index in sorted(rows):
        row = rows[row_index]
        label = row.get("field")
        value_cell = row.get("value")
        if label is None or value_cell is None:
            continue
        label_value = str(label.get("original_value") or "").strip()
        if label_value.lower() not in alias_set:
            continue
        raw_numeric = _numeric_cell_value(value_cell)
        unit_cell = row.get("unit")
        source_unit = str((unit_cell or {}).get("original_value") or "").strip()
        expected_unit = str(spec.get("expected_unit") or "").strip()
        measure = (
            normalize_controlled_measure(raw_numeric, source_unit, expected_unit)
            if raw_numeric is not None
            else {
                "numeric_value": None,
                "raw_unit": source_unit or None,
                "normalized_unit": None,
                "unit_rule": "value_not_numeric",
            }
        )
        gate_passed = measure.get("numeric_value") is not None
        return {
            "field": field,
            "metric": field,
            "matched_alias": label_value,
            "source_id": str(document.get("source_id") or ""),
            "source_type": document.get("source_type"),
            "formal_use_allowed": document.get("formal_use_allowed"),
            "candidate_kind": "structured_csv_row",
            "value": value_cell.get("original_value"),
            "original_value": value_cell.get("original_value"),
            "expected_unit": expected_unit or None,
            "excerpt": f"{label_value},{value_cell.get('original_value')},{source_unit}",
            "locator": value_cell,
            "label_locator": label,
            "unit_locator": unit_cell,
            "row_index": row_index,
            **measure,
            "attribution_gate": {
                "status": "passed" if gate_passed else "rejected",
                "reason": None if gate_passed else measure.get("unit_rule"),
                "method": "csv_same_row_header_mapping",
            },
        }
    return None


def _document_segments(document: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Preserve source locators when looking for deterministic field candidates."""

    segments: list[tuple[dict[str, Any], str]] = []
    for locator in document.get("locators") or []:
        if not isinstance(locator, dict):
            continue
        text = _locator_text(locator)
        if text:
            segments.append((locator, text))
    if segments:
        return segments
    content = str(document.get("content") or "").strip()
    locators = [item for item in (document.get("locators") or []) if isinstance(item, dict)]
    if content:
        # A web snapshot intentionally has one whole-document locator.  This is
        # still auditable because it carries the immutable content hash and URL.
        segments.append((locators[0] if locators else {"kind": "document_text"}, content))
    return segments


def _excerpt(text: str, start: int, width: int = 160) -> str:
    """Return the sentence containing the hit, bounded for auditable display."""

    if not text:
        return ""
    start = max(0, min(int(start), len(text) - 1))
    separators = "。！？!?；;\n\r"
    beginning = start
    while beginning > 0 and text[beginning - 1] not in separators:
        beginning -= 1
    ending = start
    while ending < len(text) and text[ending] not in separators:
        ending += 1
    if ending < len(text):
        ending += 1
    excerpt = text[beginning:ending].strip()
    if len(excerpt) <= width:
        return excerpt
    relative = start - beginning
    window_start = max(0, min(relative - width // 2, len(excerpt) - width))
    return excerpt[window_start:window_start + width].strip()


def extract_candidates(
    workspace_id: str,
    task_id: str,
    field_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Find locator-backed fact candidates, never confirmed finance inputs.

    This is deterministic string matching over already-ingested content.  It is
    deliberately not an LLM extraction path and does not infer units, table
    semantics, or missing project inputs.
    """

    documents = _documents_from_task(
        workspace_id,
        task_id,
    )
    if not documents:
        return _missing("analysis_task_not_found", "没有可抽取候选的分析任务")
    candidates: list[dict[str, Any]] = []
    missing_fields: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    # Gate 2 needs the full field set to know which alias terms are shared
    # (non-discriminative) versus unique to one field, so build it once up front.
    term_fields = _build_term_fields(field_specs)
    for spec in field_specs:
        field = str(spec.get("field") or "").strip()
        aliases = [field, *[str(item).strip() for item in (spec.get("aliases") or [])]]
        aliases = list(dict.fromkeys(item for item in aliases if item))
        source_filter = {str(item) for item in (spec.get("source_ids") or []) if str(item)}
        found = False
        found_value = False
        for document in documents:
            source_id = str(document.get("source_id") or "")
            if source_filter and source_id not in source_filter:
                continue
            structured = _structured_csv_candidate(document, field, aliases, spec)
            if structured is not None:
                locator = structured.get("locator") or {}
                locator_ref = str(locator.get("locator") or locator.get("cell") or "csv")
                key = (field.lower(), source_id, locator_ref)
                if key not in seen:
                    seen.add(key)
                    found = True
                    found_value = structured.get("numeric_value") is not None
                    structured["candidate_id"] = f"candidate_{len(candidates) + 1:03d}"
                    candidates.append(structured)
                continue
            for locator, text in _document_segments(document):
                lowered = text.lower()
                match_offset = min((lowered.find(alias.lower()) for alias in aliases if alias.lower() in lowered), default=-1)
                if match_offset < 0:
                    continue
                locator_ref = str(locator.get("locator") or locator.get("cell") or locator.get("page") or "document")
                key = (field.lower(), source_id, locator_ref)
                if key in seen:
                    continue
                seen.add(key)
                found = True
                cell_value = locator.get("original_value") if locator.get("kind") == "cell" else None
                # A cell whose whole content is just the field label is a table
                # anchor, not the field's value.  Returning it as a numeric or
                # confirmed fact would silently convert a header into evidence.
                is_header_anchor = (
                    locator.get("kind") == "cell"
                    and isinstance(cell_value, str)
                    and cell_value.strip().lower() in {alias.lower() for alias in aliases}
                )
                # A parsed table cell carries its own authoritative number; prose
                # goes through the offset-aware three-gate scanner.  A header
                # anchor is never a value, numeric or otherwise.
                if is_header_anchor:
                    numeric_value: int | float | None = None
                    numeric_measure: dict[str, Any] = {}
                elif locator.get("kind") == "cell":
                    numeric_value = _numeric_cell_value(locator)
                    numeric_measure = {}
                else:
                    numeric_measure = _numeric_text_measure(
                        text,
                        field,
                        spec,
                        term_fields,
                        segment_role=str(locator.get("role") or locator.get("segment_role") or ""),
                    )
                    numeric_value = numeric_measure.get("numeric_value")
                # `value`/`original_value` 必须与 `numeric_value`/`excerpt` 同源。
                # 原值此前锚在"别名首次出现处"，数字由三道门在它自己找到的 offset
                # 上取——同一段文本里别名出现多次时两者指向不同事实：value 是某一句，
                # numeric_value 与 excerpt 是另一句。下游按 value 复核、按
                # numeric_value 计算，就成了"复核通过但算错数"。
                prose_offset = int(numeric_measure.get("measure_offset", match_offset))
                original_value = (
                    None
                    if is_header_anchor
                    else (cell_value if locator.get("kind") == "cell" else _excerpt(text, prose_offset))
                )
                # A field is satisfied when it yields either a retained original
                # value or a gate-approved number, so a purely numeric hit does
                # not get mislabelled ``candidate_without_value``.
                found_value = found_value or original_value is not None or numeric_value is not None
                candidate = {
                        "candidate_id": f"candidate_{len(candidates) + 1:03d}",
                        "field": field,
                        "metric": field,
                        "matched_alias": next((alias for alias in aliases if alias.lower() in lowered), field),
                        "source_id": source_id,
                        "source_type": document.get("source_type"),
                        "formal_use_allowed": document.get("formal_use_allowed"),
                        "candidate_kind": "header_anchor" if is_header_anchor else "text_or_cell_candidate",
                        "value": original_value,
                        "original_value": original_value,
                        "numeric_value": numeric_value,
                        "expected_unit": str(spec.get("expected_unit") or "") or None,
                        "excerpt": _excerpt(text, prose_offset),
                        "locator": locator,
                    }
                if numeric_measure:
                    candidate.update(numeric_measure)
                candidates.append(candidate)
        if not found:
            missing_fields.append({
                "field": field,
                "reason": "no_matching_locator",
                "aliases_tried": aliases,
                "expected_unit": str(spec.get("expected_unit") or "") or None,
                "source_ids": sorted(source_filter),
                "next_action": "先用 analysis_query 检查原文 locator，再调整 field/aliases/expected_unit",
            })
        elif not found_value:
            missing_fields.append({
                "field": field,
                "reason": "candidate_without_value",
                "aliases_tried": aliases,
                "expected_unit": str(spec.get("expected_unit") or "") or None,
                "next_action": "命中的是表头或单位不相容数值；核对 locator 和单位后重试",
            })

    status_value = "partial" if missing_fields else "ok"
    payload = {
        "analysis_task_id": task_id,
        "field_specs": field_specs,
        "fact_candidates": candidates,
        "missing_fields": missing_fields,
        "extraction_boundary": "候选保留原值、原文与 locator；numeric_value 仅在复合单位/最近标签/单位相容三道门全过时给出，任一不过即为 null（宁缺勿糊），绝不推断 FinanceSpec 输入。",
    }
    record = CANDIDATE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_extract_candidates",
        status=status_value,
        source_ids=[str(item.get("source_id")) for item in candidates],
        basis={"analysis_task_id": task_id, "field_specs": field_specs},
    )
    return {
        "success": True,
        "status": status_value,
        "candidate_set_id": record["object_id"],
        "fact_candidates": candidates,
        "missing_fields": missing_fields,
        "resource_uris": [record["resource_uri"]],
        "warnings": (["部分字段没有 locator 支撑的候选，候选集合记为 partial"] if missing_fields else []),
        "blockers": [],
        "next_actions": ["核对 locator、单位、时点和范围后，再调用 analysis_compare 或 analysis_build_evidence_pack"],
    }
