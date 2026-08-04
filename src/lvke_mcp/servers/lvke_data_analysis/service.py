"""Deterministic evidence assembly over acquisition snapshots and source files."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from lvke_mcp.runtime.storage import (
    JSONArtifactStore,
    paginate_resource_entries,
    sha256_json,
)
from lvke_mcp.servers.lvke_data_acquisition.service import SOURCE_STORE

INGEST_STORE = JSONArtifactStore("data-analysis", "ingest_tasks", "analysis", "tasks")
EVIDENCE_STORE = JSONArtifactStore(
    "data-analysis", "evidence_packs", "evp", "evidence-packs"
)
CANDIDATE_STORE = JSONArtifactStore(
    "data-analysis", "candidate_sets", "cset", "candidate-sets"
)
PROFILE_STORE = JSONArtifactStore(
    "data-analysis", "data_profiles", "profile", "profiles"
)
NORMALIZED_COMPARE_STORE = JSONArtifactStore(
    "data-analysis", "normalized_comparisons", "ncmp", "normalized-comparisons"
)
FINANCIAL_TREND_STORE = JSONArtifactStore(
    "data-analysis", "financial_trends", "ftrend", "financial-trends"
)
BENCHMARK_COMPARISON_STORE = JSONArtifactStore(
    "data-analysis", "benchmark_comparisons", "bench", "benchmark-comparisons"
)
_RESOURCE_STORES = (
    (INGEST_STORE, "task"),
    (EVIDENCE_STORE, "evidence_pack"),
    (CANDIDATE_STORE, "candidate_set"),
    (PROFILE_STORE, "profile"),
    (NORMALIZED_COMPARE_STORE, "normalized_comparison"),
    (FINANCIAL_TREND_STORE, "financial_trend"),
    (BENCHMARK_COMPARISON_STORE, "benchmark_comparison"),
)

# Exact, auditable conversions only.  The dictionary is opt-in and never uses
# fuzzy unit inference; every applied rule is returned with its basis.
CONTROLLED_UNIT_RULES: dict[str, tuple[str, float, str]] = {
    "元": ("万元", 0.0001, "受控单位字典：1万元=10000元"),
    "千元": ("万元", 0.1, "受控单位字典：1万元=10千元"),
    "万元": ("万元", 1.0, "受控单位字典：单位恒等"),
    "百万元": ("万元", 100.0, "受控单位字典：1百万元=100万元"),
    "亿元": ("万元", 10000.0, "受控单位字典：1亿元=10000万元"),
    "kW": ("MW", 0.001, "受控SI单位字典：1MW=1000kW"),
    "MW": ("MW", 1.0, "受控SI单位字典：单位恒等"),
    "GW": ("MW", 1000.0, "受控SI单位字典：1GW=1000MW"),
    "%": ("%", 1.0, "受控比例单位字典：百分数恒等"),
    "倍": ("倍", 1.0, "受控倍数单位字典：单位恒等"),
}

EVIDENCE_TRACKS = {"real", "technical_fixture", "controlled_assumption"}
_SHA256_PATTERN = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")


def _validate_fixture_manifest(
    manifest: Any,
    selected: list[dict[str, Any]],
    fact_candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate a technical fixture without granting it formal standing."""

    if not isinstance(manifest, dict):
        return None, ["fixture_manifest_required"]
    required = (
        "fixture_id", "fixture_version", "project_type", "industry_code",
        "source_snapshot_ids", "content_hashes", "allowed_fields",
        "prohibited_extrapolations", "generated_at", "generator_version",
        "test_scope",
    )
    missing = [name for name in required if manifest.get(name) in (None, "", [])]
    if missing:
        return None, [f"fixture_manifest_field_required:{name}" for name in missing]
    try:
        datetime.fromisoformat(str(manifest["generated_at"]).replace("Z", "+00:00"))
    except ValueError:
        return None, ["fixture_manifest_generated_at_invalid"]
    source_ids = {str(item) for item in manifest.get("source_snapshot_ids") or [] if str(item)}
    selected_by_id = {
        str(item.get("source_id") or ""): item for item in selected
        if str(item.get("source_id") or "")
    }
    if source_ids != set(selected_by_id):
        return None, ["fixture_manifest_source_set_mismatch"]
    hashes = manifest.get("content_hashes") or {}
    if not isinstance(hashes, dict):
        return None, ["fixture_manifest_content_hashes_invalid"]
    for source_id, source in selected_by_id.items():
        expected = str(hashes.get(source_id) or "")
        actual = str(source.get("content_hash") or "")
        if not _SHA256_PATTERN.fullmatch(expected) or expected.removeprefix("sha256:") != actual.removeprefix("sha256:"):
            return None, [f"fixture_manifest_content_hash_mismatch:{source_id}"]
        if not source.get("locators"):
            return None, [f"fixture_source_locator_required:{source_id}"]
    allowed = {str(item) for item in manifest.get("allowed_fields") or [] if str(item)}
    candidate_fields = {
        str(item.get("field") or item.get("metric") or "")
        for item in fact_candidates if isinstance(item, dict)
    } - {""}
    if not candidate_fields.issubset(allowed):
        return None, ["fixture_candidate_field_not_allowed"]
    normalized = {
        **manifest,
        "source_snapshot_ids": sorted(source_ids),
        "content_hashes": {key: str(hashes[key]) for key in sorted(source_ids)},
        "allowed_fields": sorted(allowed),
        "prohibited_extrapolations": sorted({
            str(item) for item in manifest.get("prohibited_extrapolations") or [] if str(item)
        }),
        "test_scope": sorted({str(item) for item in manifest.get("test_scope") or [] if str(item)}),
    }
    return normalized, []


def controlled_unit_rules() -> list[dict[str, Any]]:
    return [
        {
            "source_unit": source,
            "target_unit": target,
            "factor": factor,
            "conversion_basis": basis,
        }
        for source, (target, factor, basis) in CONTROLLED_UNIT_RULES.items()
    ]


def normalize_financial_period(value: Any) -> dict[str, Any]:
    """Normalize common annual/quarterly/monthly labels without changing granularity."""

    raw = str(value or "").strip().upper()
    annual = re.fullmatch(r"(?:FY)?(\d{4})([AE])?", raw)
    if annual:
        year = int(annual.group(1))
        suffix = annual.group(2) or ""
        return {
            "raw": str(value or ""), "normalized": str(year), "period_type": "annual",
            "year": year, "quarter": None, "month": None,
            "actual_estimate": "actual" if suffix == "A" else ("estimate" if suffix == "E" else "unspecified"),
            "sort_key": year * 100,
        }
    quarter = re.fullmatch(r"(\d{4})[- ]?Q([1-4])([AE])?", raw)
    if quarter:
        year, number = int(quarter.group(1)), int(quarter.group(2))
        suffix = quarter.group(3) or ""
        return {
            "raw": str(value or ""), "normalized": f"{year}-Q{number}", "period_type": "quarterly",
            "year": year, "quarter": number, "month": None,
            "actual_estimate": "actual" if suffix == "A" else ("estimate" if suffix == "E" else "unspecified"),
            "sort_key": year * 100 + number * 3,
        }
    month = re.fullmatch(r"(\d{4})[-/](0[1-9]|1[0-2])", raw)
    if month:
        year, number = int(month.group(1)), int(month.group(2))
        return {
            "raw": str(value or ""), "normalized": f"{year}-{number:02d}", "period_type": "monthly",
            "year": year, "quarter": (number - 1) // 3 + 1, "month": number,
            "actual_estimate": "unspecified", "sort_key": year * 100 + number,
        }
    return {
        "raw": str(value or ""), "normalized": str(value or ""), "period_type": "unknown",
        "year": None, "quarter": None, "month": None,
        "actual_estimate": "unspecified", "sort_key": None,
    }


def _snapshot_document(
    workspace_id: str,
    source_id: str,
) -> dict[str, Any] | None:
    record = SOURCE_STORE.get(
        workspace_id,
        source_id,
    )
    if record is None:
        return None
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    fetched_at = str(
        payload.get("retrieved_at")
        or payload.get("fetched_at")
        or record.get("created_at")
        or ""
    )
    return {
        "source_id": source_id,
        "source_type": "web_snapshot",
        "title": str(payload.get("title") or payload.get("url") or source_id),
        "url": str(payload.get("url") or ""),
        "content": str(payload.get("content") or ""),
        "content_hash": record.get("content_hash"),
        "fetched_at": fetched_at,
        "status": record.get("status") or "ok",
        "formal_use_allowed": bool(payload.get("formal_use_allowed", False)),
        "locators": [
            {
                "kind": "web_snapshot",
                "source_id": source_id,
                "url": str(payload.get("url") or ""),
                "content_hash": record.get("content_hash"),
                "fetched_at": fetched_at,
            }
        ],
    }


def _file_document(
    workspace_id: str,
    file_id: str,
) -> dict[str, Any] | None:
    from lvke_mcp.servers.lvke_source_files import backend as source

    state = source._load_state(workspace_id)  # noqa: SLF001
    record = (state.get("files") or {}).get(file_id)
    if not isinstance(record, dict):
        return None
    analysis = source._load_analysis(workspace_id, file_id)  # noqa: SLF001
    locators = [item for item in (analysis.get("locators") or []) if isinstance(item, dict)]
    ocr_status = str(analysis.get("ocr_status") or record.get("ocr_status") or "")
    formal_use_decision = str(
        analysis.get("formal_use_decision")
        or record.get("formal_use_decision")
        or "not_applicable"
    ).lower()
    ocr_used = ocr_status not in {"", "not_required"} or any(
        str(item.get("kind") or "") == "ocr_block" for item in locators
    )
    unresolved_low_confidence = [
        item
        for item in locators
        if item.get("low_confidence") is True
        and str(item.get("manual_review_status") or "pending").lower() != "approved"
    ]
    formal_use_allowed = bool(record.get("security_formal_use_allowed", False))
    if ocr_used:
        formal_use_allowed = bool(
            formal_use_allowed
            and formal_use_decision == "approved"
            and not unresolved_low_confidence
        )
    text_parts = []
    for locator in locators:
        for key in ("text", "content", "value"):
            value = locator.get(key)
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip())
                break
    return {
        "source_id": file_id,
        "source_type": "controlled_file",
        "title": str(record.get("original_filename") or file_id),
        "content": "\n".join(text_parts),
        "content_hash": record.get("sha256"),
        "fetched_at": str(record.get("created_at") or record.get("updated_at") or ""),
        "status": str(record.get("extract_status") or record.get("status") or "pending"),
        "security_status": record.get("security_review_status") or record.get("security_scan"),
        "formal_use_allowed": formal_use_allowed,
        "formal_use_decision": formal_use_decision,
        "ocr_formal_use_decision": str(
            analysis.get("ocr_formal_use_decision")
            or formal_use_decision
        ).lower(),
        "manual_review_status": str(
            analysis.get("manual_review_status")
            or record.get("manual_review_status")
            or "pending"
        ),
        "ocr_review_ledger_count": len(analysis.get("ocr_review_ledger") or []),
        "unresolved_low_confidence_locator_count": len(unresolved_low_confidence),
        "locators": locators,
    }


def ingest(
    workspace_id: str,
    source_snapshot_ids: list[str],
    file_ids: list[str],
) -> dict[str, Any]:
    if len(source_snapshot_ids) + len(file_ids) > 100:
        return {
            "success": False,
            "transport_success": True,
            "business_success": False,
            "completed": False,
            "outcome": "blocked",
            "status": "blocked",
            "analysis_task_id": "",
            "document_count": 0,
            "failures": [],
            "resource_uris": [],
            "warnings": [],
            "blockers": ["source_id_limit_exceeded"],
            "next_actions": ["每次最多提交 100 个来源 ID，请拆分批次后重试"],
        }
    documents: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for source_id in source_snapshot_ids:
        doc = _snapshot_document(
            workspace_id,
            source_id,
        )
        if doc is None:
            failures.append({"source_id": source_id, "reason": "snapshot_not_found"})
        else:
            documents.append(doc)
    for file_id in file_ids:
        doc = _file_document(
            workspace_id,
            file_id,
        )
        if doc is None:
            failures.append({"source_id": file_id, "reason": "file_not_found"})
        else:
            documents.append(doc)
            # source-files uses ``success`` for extract_status and
            # ``succeeded`` for the file/job lifecycle.  Accept both canonical
            # terminal spellings; keep ``partial`` fail-honest.
            if doc["status"] not in {
                "ready", "completed", "parsed", "ok", "success", "succeeded"
            }:
                failures.append({"source_id": file_id, "reason": "parse_not_complete"})
    succeeded = len(documents)
    status = (
        "ok" if succeeded and not failures
        else ("partial" if succeeded else "blocked")
    )
    record = INGEST_STORE.put(
        workspace_id,
        {"documents": documents, "failures": failures, "status": status},
        producer="lvke-data-analysis.analysis_ingest",
        status=status,
        source_ids=[*source_snapshot_ids, *file_ids],
    )
    return {
        "success": succeeded > 0,
        "status": status,
        "analysis_task_id": record["object_id"],
        "document_count": succeeded,
        "failures": failures,
        "resource_uris": [record["resource_uri"]],
        "warnings": (["部分来源未完成解析或不存在"] if failures else []),
        "blockers": (["no_sources_ingested"] if not succeeded else []),
        "next_actions": ["调用 analysis_query/analysis_compare，确认后固化 evidence pack"] if succeeded else ["检查来源 ID 或等待文件解析完成"],
    }


def status(
    workspace_id: str,
    task_id: str,
) -> dict[str, Any]:
    record = INGEST_STORE.get(
        workspace_id,
        task_id,
    )
    if record is None:
        return _missing("analysis_task_not_found", "未找到分析任务")
    payload = record.get("payload") or {}
    documents = payload.get("documents") or []
    return {
        "success": True,
        "status": str(payload.get("status") or record.get("status") or "partial"),
        "analysis_task_id": task_id,
        "document_count": len(documents),
        "failures": payload.get("failures") or [],
        "resource_uris": [record["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": ["任务为 partial 时不要描述为完整解析"],
    }


def _documents_from_task(
    workspace_id: str,
    task_id: str,
) -> list[dict[str, Any]]:
    record = INGEST_STORE.get(
        workspace_id,
        task_id,
    )
    if record is None:
        return []
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return [item for item in (payload.get("documents") or []) if isinstance(item, dict)]


def query(
    workspace_id: str,
    task_id: str,
    query_text: str,
    limit: int,
) -> dict[str, Any]:
    documents = _documents_from_task(
        workspace_id,
        task_id,
    )
    if not documents:
        return _missing("analysis_task_not_found", "没有可查询的分析任务或文档")
    terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query_text) if term]
    hits = []
    for doc in documents:
        content = str(doc.get("content") or "")
        lowered = content.lower()
        score = sum(lowered.count(term) for term in terms)
        if score <= 0:
            continue
        first = min((lowered.find(term) for term in terms if term in lowered), default=0)
        start = max(0, first - 160)
        snippet = content[start : start + 640]
        hits.append(
            {
                "source_id": doc.get("source_id"),
                "title": doc.get("title"),
                "score": score,
                "snippet": snippet,
                "locators": doc.get("locators") or [],
                "formal_use_allowed": doc.get("formal_use_allowed"),
            }
        )
    hits.sort(key=lambda item: (-int(item["score"]), str(item["source_id"])))
    return {
        "success": True,
        "status": "ok",
        "hits": hits[:limit],
        "resource_uris": [],
        "warnings": ([] if hits else ["未找到匹配证据"]),
        "blockers": [],
        "next_actions": ["核对 locator 与来源资格；不要把命中自动视为已采信事实"],
    }


def _locator_text(locator: dict[str, Any]) -> str:
    """Return a locator's extracted text/value without fabricating a value."""

    for key in ("text", "content", "original_value", "display_value", "cached_value", "value"):
        value = locator.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


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


# ── Prose numeric extraction: the three attribution gates ──────────────────
#
# Extracting a number from prose is only safe when three independent gates all
# pass; any one failing keeps ``numeric_value`` at ``None`` (宁缺勿糊 — a missing
# number is safer than a silently wrong one).  The gates, in order:
#
#   Gate 1 (composite unit): ``0.35元/千瓦时`` / ``68元/吨`` are captured as ONE
#       semantic unit.  Dropping the denominator would turn ``0.35元/千瓦时``
#       into a bare ``0.35元`` and silently mis-scale the field.
#   Gate 2 (nearest label + qualifier): a number is attributed to a field only
#       when the field's declared label is the *closest* label to it, so two
#       same-unit fields (总投资 / 年产值, both 万元) do not cross-contaminate.
#       ``require_terms`` / ``exclude_terms`` further separate near-synonym
#       measures (销项税率 vs 进项税率) whose labels alone cannot.
#   Gate 3 (unit compatibility): the scanned unit must canonically equal the
#       caller's ``expected_unit``; a cross-unit value (``2亿元`` for a ``吨/年``
#       field) is discarded, never coerced.
#
# The primitives (number/unit patterns, normalisation, calendar-year filter)
# are reused from the research-engine quantitative module so that finance
# extraction and citation audit agree on what a number even is.
from lvke_mcp.domains.research.quantitative import (  # noqa: E402
    _NUMBER as _Q_NUMBER,
    _UNIT as _Q_UNIT,
    _is_calendar_year,
    _normalize_number,
    _normalize_unit,
)

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

# Canonical unit forms for Gate 3 comparison (source wording vs expected_unit).
_UNIT_CANON = {
    "千瓦时": "kwh", "兆瓦时": "mwh", "吉瓦时": "gwh", "度": "kwh",
    "千瓦": "kw", "兆瓦": "mw", "吉瓦": "gw",
    "平米": "平方米", "㎡": "平方米", "m²": "平方米", "m2": "平方米",
    "平方公里": "平方公里",
    "ha": "公顷", "公顷": "公顷",
    "标煤": "吨标煤", "吨标煤": "吨标煤",
    "公里": "km", "千米": "km",
}


def _canon_unit(unit: str) -> str:
    """Canonicalise a possibly-composite unit for Gate 3 equality.

    ``元/千瓦时`` and ``元/kWh`` must compare equal; ``千瓦`` and ``kW`` too.
    Each side of a ``/`` is normalised independently, so numerator and
    denominator aliases both resolve.
    """

    parts = re.split(r"[/／]", str(unit or "").strip())
    canon: list[str] = []
    for part in parts:
        token = _normalize_unit(part).lower()
        canon.append(_UNIT_CANON.get(token, token))
    return "/".join(canon)


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


def _nearest_label_fields(
    occurrences: list[tuple[int, int, frozenset[str]]],
    m_start: int,
    m_end: int,
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
        owners, p_start, p_end = _nearest_label_fields(occurrences, match.start(), match.end())
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
                original_value = (
                    None
                    if is_header_anchor
                    else (cell_value if locator.get("kind") == "cell" else _excerpt(text, match_offset))
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
                        "excerpt": _excerpt(
                            text,
                            int(numeric_measure.get("measure_offset", match_offset)),
                        ),
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


_CELL_REF = re.compile(r"^([A-Za-z]+)([1-9][0-9]*)$")


def _cell_position(reference: str) -> tuple[int, int] | None:
    match = _CELL_REF.fullmatch(str(reference or ""))
    if match is None:
        return None
    column = 0
    for letter in match.group(1).upper():
        column = column * 26 + (ord(letter) - ord("A") + 1)
    return int(match.group(2)), column


def profile_tabular(
    workspace_id: str,
    task_id: str,
    file_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Create an auditable profile from existing controlled-file cell locators."""

    documents = _documents_from_task(
        workspace_id,
        task_id,
    )
    if not documents:
        return _missing("analysis_task_not_found", "没有可画像的分析任务")
    requested = {str(item) for item in (file_ids or []) if str(item)}
    profiles: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for document in documents:
        source_id = str(document.get("source_id") or "")
        if requested and source_id not in requested:
            continue
        if document.get("source_type") != "controlled_file":
            skipped.append({"source_id": source_id, "reason": "not_controlled_tabular_file"})
            continue
        sheets: dict[str, list[dict[str, Any]]] = {}
        for locator in document.get("locators") or []:
            if isinstance(locator, dict) and locator.get("kind") == "cell" and locator.get("sheet"):
                sheets.setdefault(str(locator["sheet"]), []).append(locator)
        if not sheets:
            skipped.append({"source_id": source_id, "reason": "no_cell_locators"})
            continue
        for sheet_name, cells in sorted(sheets.items()):
            positioned = [(locator, _cell_position(str(locator.get("cell") or ""))) for locator in cells]
            positioned = [(locator, position) for locator, position in positioned if position is not None]
            if not positioned:
                skipped.append({"source_id": source_id, "reason": "invalid_cell_locators"})
                continue
            max_row = max(position[0] for _, position in positioned)
            max_column = max(position[1] for _, position in positioned)
            first_row = min(position[0] for _, position in positioned)
            headers = [
                str(_locator_text(locator))
                for locator, position in sorted(positioned, key=lambda item: item[1][1])
                if position[0] == first_row and _locator_text(locator)
            ][:100]
            numeric_count = sum(
                1
                for locator, _ in positioned
                if isinstance(locator.get("cached_value", locator.get("display_value")), (int, float))
                and not isinstance(locator.get("cached_value", locator.get("display_value")), bool)
            )
            formula_count = sum(1 for locator, _ in positioned if str(locator.get("formula") or ""))
            profiles.append(
                {
                    "source_id": source_id,
                    "sheet": sheet_name,
                    "observed_row_count": max_row,
                    "observed_column_count": max_column,
                    "observed_cell_count": len(positioned),
                    "first_observed_row": first_row,
                    "headers": headers,
                    "numeric_cell_count": numeric_count,
                    "text_cell_count": len(positioned) - numeric_count,
                    "formula_cell_count": formula_count,
                    "formal_use_allowed": document.get("formal_use_allowed"),
                    "profile_boundary": "统计仅基于已解析的非空 cell locator；不重算公式、不补空白单元格。",
                }
            )
    if not profiles:
        reasons = {item["reason"] for item in skipped}
        unsupported = bool(skipped) and reasons == {"not_controlled_tabular_file"}
        code = "unsupported_input_kind" if unsupported else "insufficient_source_data"
        status_value = "blocked" if unsupported else "partial"
        return {
            "success": False,
            "transport_success": True,
            "system_success": True,
            "business_success": False,
            "completed": False,
            "outcome": status_value,
            "status": status_value,
            "code": code,
            "message": (
                "纯文本输入不支持表格画像"
                if unsupported
                else "受控表格资料缺少可画像的 cell locator"
            ),
            "capability_scope": "tabular_only",
            "data_completeness": "insufficient_for_tabular_profile",
            "partial_reasons": sorted(reasons or {"no_controlled_tabular_cells"}),
            "resource_uris": [],
            "warnings": ["输入不是已解析受控表格资料"] if skipped else [],
            "blockers": [code],
            "next_actions": ["先摄入已解析 XLSX/CSV 受控资料"],
        }
    status_value = "ok" if not skipped else "partial"
    payload = {
        "analysis_task_id": task_id,
        "requested_file_ids": sorted(requested),
        "profiles": profiles,
        "skipped": skipped,
    }
    record = PROFILE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_profile_tabular",
        status=status_value,
        source_ids=[str(item.get("source_id")) for item in profiles],
        basis={"analysis_task_id": task_id, "file_ids": sorted(requested)},
    )
    complete = status_value == "ok"
    return {
        "success": complete,
        "transport_success": True,
        "system_success": True,
        "business_success": complete,
        "completed": complete,
        "outcome": status_value,
        "status": status_value,
        "data_profile_id": record["object_id"],
        "profiles": profiles,
        "skipped": skipped,
        "resource_uris": [record["resource_uri"]],
        "warnings": (["部分输入不是已解析受控表格资料，未进行画像"] if skipped else []),
        "blockers": [],
        "next_actions": ["使用 locator 审核表头和单元格含义；画像不是财务输入确认"],
    }


def normalize_compare(
    workspace_id: str,
    observations: list[dict[str, Any]],
    conversion_rules: list[dict[str, Any]] | None = None,
    *,
    use_controlled_unit_dictionary: bool = False,
    comparison_mode: str = "source_reconciliation",
) -> dict[str, Any]:
    """Normalize exact units and expose a comparison without fuzzy inference."""

    conversion_rules = list(conversion_rules or [])
    normalized: list[dict[str, Any]] = []
    unprocessed: list[dict[str, Any]] = []
    applied_rules: list[dict[str, Any]] = []
    for observation in observations:
        metric = str(observation.get("metric") or "").strip()
        unit = str(observation.get("unit") or "").strip()
        value = observation.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            unprocessed.append({**observation, "reason": "non_numeric_value"})
            continue
        matched: dict[str, Any] | None = None
        for rule in conversion_rules:
            if (
                metric.lower() == str(rule.get("metric") or "").strip().lower()
                and unit.lower() == str(rule.get("source_unit") or "").strip().lower()
            ):
                matched = rule
                break
        if matched is None and use_controlled_unit_dictionary:
            controlled = CONTROLLED_UNIT_RULES.get(unit)
            if controlled is None:
                controlled = next(
                    (rule for source, rule in CONTROLLED_UNIT_RULES.items() if source.lower() == unit.lower()),
                    None,
                )
            if controlled is not None:
                target_unit, factor, basis = controlled
                matched = {
                    "metric": metric,
                    "source_unit": unit,
                    "target_unit": target_unit,
                    "factor": factor,
                    "conversion_basis": basis,
                    "rule_source": "controlled_unit_dictionary",
                }
        if matched is None:
            unprocessed.append({**observation, "reason": "no_explicit_conversion_rule"})
            continue
        normalized_value = value * float(matched["factor"])
        normalized_observation = {
                **observation,
                "original_value": value,
                "original_unit": unit,
                "value": normalized_value,
                "unit": str(matched["target_unit"]),
                "conversion_rule": {
                    "metric": str(matched["metric"]),
                    "source_unit": str(matched["source_unit"]),
                    "target_unit": str(matched["target_unit"]),
                    "factor": float(matched["factor"]),
                    "conversion_basis": str(matched["conversion_basis"]),
                    "rule_source": str(matched.get("rule_source") or "caller_declared"),
                },
            }
        period = normalize_financial_period(
            observation.get("period", observation.get("as_of"))
        )
        normalized_observation["period"] = period["normalized"]
        normalized_observation["period_metadata"] = period
        normalized.append(normalized_observation)
        if matched not in applied_rules:
            applied_rules.append(matched)
    comparison = compare(normalized, comparison_mode=comparison_mode) if normalized else {
        "consistent": [], "conflicts": [], "missing": [], "unable_to_compare": [], "warnings": []
    }
    status_value = "partial" if unprocessed or comparison.get("status") == "partial" else "ok"
    payload = {
        "observations": observations,
        "conversion_rules": conversion_rules,
        "use_controlled_unit_dictionary": use_controlled_unit_dictionary,
        "comparison_mode": comparison_mode,
        "normalized_observations": normalized,
        "unprocessed": unprocessed,
        "comparison": comparison,
        "normalization_boundary": "仅按调用方显式规则或明确启用的受控精确单位字典换算；不会推断单位、选择正确值或写入 FinanceSpec。",
    }
    record = NORMALIZED_COMPARE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_normalize_compare",
        status=status_value,
        source_ids=[str(item.get("source_id")) for item in observations if str(item.get("source_id") or "")],
        basis={
            "observations": observations,
            "conversion_rules": conversion_rules,
            "use_controlled_unit_dictionary": use_controlled_unit_dictionary,
            "comparison_mode": comparison_mode,
        },
    )
    warnings = list(comparison.get("warnings") or [])
    if unprocessed:
        warnings.append("部分观察值没有精确匹配的显式换算规则，未做单位转换")
    return {
        "success": True,
        "status": status_value,
        "comparison_id": record["object_id"],
        "normalized_observations": normalized,
        "unprocessed": unprocessed,
        "comparison": comparison,
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": [],
        "next_actions": ["核对 conversion_basis、时点和范围；冲突或缺口不得自动写入 FinanceSpec"],
    }


def _missing_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对每个 metric×来源组合列出缺失观察值；缺口如实结构化，不静默留空。"""
    metric_labels: dict[str, str] = {}
    sources: list[str] = []
    covered: set[tuple[str, str]] = set()
    without_value: set[tuple[str, str]] = set()
    for item in observations:
        source_id = str(item.get("source_id") or "").strip()
        metric = str(item.get("metric") or "").strip()
        if source_id and source_id not in sources:
            sources.append(source_id)
        if metric:
            metric_labels.setdefault(metric.lower(), metric)
        if not source_id or not metric:
            continue
        pair = (source_id, metric.lower())
        if item.get("value") is None:
            without_value.add(pair)
        else:
            covered.add(pair)
    missing: list[dict[str, Any]] = []
    for metric_key, metric_label in metric_labels.items():
        for source_id in sources:
            pair = (source_id, metric_key)
            if pair in covered:
                continue
            missing.append(
                {
                    "source_id": source_id,
                    "metric": metric_label,
                    "reason": "value_missing" if pair in without_value else "no_observation",
                }
            )
    return missing


def _period_mismatches(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in observations:
        metric = str(item.get("metric") or "").strip()
        if not metric or item.get("value") is None:
            continue
        period = normalize_financial_period(item.get("period", item.get("as_of")))
        key = (
            metric.lower(),
            str(item.get("unit") or "").strip().lower(),
            str(item.get("scope") or "").strip().lower(),
        )
        groups.setdefault(key, []).append({**item, "period_metadata": period})
    mismatches: list[dict[str, Any]] = []
    for key, items in groups.items():
        period_types = sorted({str(item["period_metadata"]["period_type"]) for item in items})
        if len(period_types) > 1:
            mismatches.append({
                "comparison_key": "|".join(key),
                "period_types": period_types,
                "periods": sorted({str(item["period_metadata"]["normalized"]) for item in items}),
                "reason": "financial_period_granularity_mismatch",
            })
    return mismatches


def _comparison_key(item: dict[str, Any], *, include_entity: bool, include_dimension: bool) -> str:
    period = normalize_financial_period(item.get("period", item.get("as_of")))
    parts = [
        str(item.get("metric") or "").strip().lower(),
        str(item.get("unit") or "").strip().lower(),
        str(period.get("normalized") or ""),
        str(item.get("scope") or "").strip().lower(),
    ]
    if include_entity:
        parts.append(str(item.get("entity") or "").strip().lower())
    if include_dimension:
        parts.append(str(item.get("dimension") or item.get("segment") or "").strip().lower())
    return "|".join(parts)


def _peer_comparison(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for item in observations:
        rows.setdefault(_comparison_key(item, include_entity=True, include_dimension=False), []).append(item)
    peers: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for key, items in rows.items():
        values = {str(item.get("value")) for item in items}
        row = {"comparison_key": key, "entity": items[0].get("entity"), "observations": items}
        if len(values) > 1:
            conflicts.append({**row, "values": sorted(values), "reason": "duplicate_peer_observation_conflict"})
        else:
            peers.append({**row, "value": items[0].get("value"), "unit": items[0].get("unit")})
    return peers, conflicts


def _segment_comparison(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    duplicate_groups: dict[str, list[dict[str, Any]]] = {}
    for item in observations:
        duplicate_groups.setdefault(_comparison_key(item, include_entity=True, include_dimension=True), []).append(item)
    valid_rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for key, items in duplicate_groups.items():
        values = {str(item.get("value")) for item in items}
        if len(values) > 1:
            conflicts.append({
                "comparison_key": key,
                "observations": items,
                "values": sorted(values),
                "reason": "duplicate_segment_observation_conflict",
            })
        else:
            valid_rows.append(items[0])
    summaries: dict[str, list[dict[str, Any]]] = {}
    for item in valid_rows:
        key = _comparison_key(item, include_entity=True, include_dimension=False)
        summaries.setdefault(key, []).append(item)
    result: list[dict[str, Any]] = []
    for key, items in summaries.items():
        numeric = [item for item in items if isinstance(item.get("value"), (int, float)) and not isinstance(item.get("value"), bool)]
        total = sum(float(item["value"]) for item in numeric)
        segments = []
        for item in items:
            value = item.get("value")
            share = (float(value) / total) if total and isinstance(value, (int, float)) and not isinstance(value, bool) else None
            segments.append({**item, "revenue_share": share})
        result.append({
            "comparison_key": key,
            "entity": items[0].get("entity"),
            "metric": items[0].get("metric"),
            "period": normalize_financial_period(items[0].get("period", items[0].get("as_of")))["normalized"],
            "unit": items[0].get("unit"),
            "segment_total": total,
            "segments": segments,
        })
    return result, conflicts


def compare(
    observations: list[dict[str, Any]],
    comparison_mode: str = "source_reconciliation",
) -> dict[str, Any]:
    if comparison_mode not in {"source_reconciliation", "peer", "segment"}:
        return _missing("unsupported_comparison_mode", "comparison_mode 必须为 source_reconciliation、peer 或 segment")
    groups: dict[str, list[dict[str, Any]]] = {}
    unable = []
    for item in observations:
        metric = str(item.get("metric") or "").strip()
        if not metric or item.get("value") is None:
            unable.append({**item, "reason": "missing_metric_or_value"})
            continue
        key = _comparison_key(item, include_entity=False, include_dimension=False)
        groups.setdefault(key, []).append(item)
    consistent, conflicts = [], []
    peer_rows: list[dict[str, Any]] = []
    segment_summaries: list[dict[str, Any]] = []
    if comparison_mode == "peer":
        peer_rows, conflicts = _peer_comparison([item for items in groups.values() for item in items])
    elif comparison_mode == "segment":
        segment_summaries, conflicts = _segment_comparison([item for items in groups.values() for item in items])
    else:
        for key, items in groups.items():
            values = {str(item.get("value")) for item in items}
            target = consistent if len(values) == 1 else conflicts
            target.append({"comparison_key": key, "observations": items, "values": sorted(values)})
    missing = _missing_observations(observations)
    period_mismatches = _period_mismatches(observations)
    warnings = []
    if conflicts or unable:
        warnings.append("存在冲突或不可比较项，未进行静默合并")
    if missing:
        warnings.append("部分来源缺少指标观察值，比较覆盖不完整")
    if period_mismatches:
        warnings.append("存在年度、季度或月度期间粒度不一致，不得直接比较")
    return {
        "success": True,
        "status": "partial" if conflicts or unable or missing or period_mismatches else "ok",
        "comparison_mode": comparison_mode,
        "consistent": consistent,
        "conflicts": conflicts,
        "peer_rows": peer_rows,
        "segment_summaries": segment_summaries,
        "period_mismatches": period_mismatches,
        "missing": missing,
        "unable_to_compare": unable,
        "resource_uris": [],
        "warnings": warnings,
        "blockers": [],
        "next_actions": ["由用户或受控依据确认冲突口径后再进入财务输入"],
    }


def _benchmark_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def compare_benchmark(
    workspace_id: str,
    subject: dict[str, Any],
    benchmarks: list[dict[str, Any]],
    *,
    attention_threshold_pct: float = 15,
    material_threshold_pct: float = 30,
) -> dict[str, Any]:
    """Compare exact benchmark dimensions and never infer a cross-basis deviation."""

    attention = _benchmark_decimal(attention_threshold_pct)
    material = _benchmark_decimal(material_threshold_pct)
    if (
        attention is None
        or material is None
        or attention < 0
        or material <= attention
        or material > 1000
    ):
        return _missing(
            "benchmark_thresholds_invalid",
            "偏差阈值必须满足 0 <= attention < material <= 1000",
        )
    required_dimensions = ("metric", "unit", "period", "region", "scope", "tax_basis")
    missing_subject = [
        field for field in (*required_dimensions, "value")
        if subject.get(field) in (None, "")
    ]
    subject_value = _benchmark_decimal(subject.get("value"))
    if missing_subject or subject_value is None:
        return _missing(
            "benchmark_subject_invalid",
            "待比较对象缺少数值或完整期间、地区、范围、单位和税基口径",
        )
    comparable: list[dict[str, Any]] = []
    unable: list[dict[str, Any]] = []
    for index, benchmark in enumerate(benchmarks):
        benchmark_id = str(benchmark.get("benchmark_id") or f"benchmark_{index + 1:03d}")
        value = _benchmark_decimal(benchmark.get("value"))
        mismatch_fields = [
            field
            for field in required_dimensions
            if str(subject.get(field) or "").strip().casefold()
            != str(benchmark.get(field) or "").strip().casefold()
        ]
        if value is None:
            mismatch_fields.append("value")
        if mismatch_fields:
            unable.append(
                {
                    "benchmark_id": benchmark_id,
                    "reason": "benchmark_basis_incompatible",
                    "mismatch_fields": sorted(set(mismatch_fields)),
                    "deviation_pct": None,
                }
            )
            continue
        if value == 0:
            unable.append(
                {
                    "benchmark_id": benchmark_id,
                    "reason": "benchmark_zero_base",
                    "mismatch_fields": [],
                    "deviation_pct": None,
                }
            )
            continue
        deviation = ((subject_value - value) / abs(value) * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        absolute_deviation = abs(deviation)
        if absolute_deviation >= material:
            severity = "material"
        elif absolute_deviation >= attention:
            severity = "attention"
        else:
            severity = "within_threshold"
        comparable.append(
            {
                "benchmark_id": benchmark_id,
                "benchmark_value": float(value),
                "subject_value": float(subject_value),
                "deviation_pct": float(deviation),
                "absolute_deviation_pct": float(absolute_deviation),
                "severity": severity,
                "compatible": True,
                "source_id": benchmark.get("source_id"),
                "locator": benchmark.get("locator"),
            }
        )
    status = "partial" if unable else "ok"
    payload = {
        "object_type": "BenchmarkComparison",
        "subject": subject,
        "benchmarks": benchmarks,
        "thresholds": {
            "attention_pct": float(attention),
            "material_pct": float(material),
        },
        "comparable_results": comparable,
        "unable_to_compare": unable,
        "aggregation": "none",
        "comparison_boundary": "只有 metric、period、region、scope、unit 和 tax_basis 完全一致时才计算偏差；不执行模糊换算或跨口径推断。",
    }
    source_ids = [str(subject.get("source_id") or "")]
    source_ids.extend(
        str(item.get("source_id") or "")
        for item in benchmarks
        if isinstance(item, dict)
    )
    record = BENCHMARK_COMPARISON_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_compare_benchmark",
        status=status,
        source_ids=[item for item in source_ids if item],
        basis={
            "subject": subject,
            "benchmarks": benchmarks,
            "thresholds": payload["thresholds"],
        },
    )
    return {
        "success": status == "ok",
        "business_success": status == "ok",
        "system_success": True,
        "transport_success": True,
        "status": status,
        "benchmark_comparison_id": record["object_id"],
        "comparable_results": comparable,
        "unable_to_compare": unable,
        "aggregation": "none",
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "resource_uris": [record["resource_uri"]],
        "warnings": (
            ["部分 benchmark 的期间、地区、范围、单位或税基不兼容，未计算偏差"]
            if unable
            else []
        ),
        "blockers": [],
        "next_actions": ["对 unable_to_compare 项补充同口径 benchmark 或显式归一化后重试"],
    }


def financial_trends(
    workspace_id: str,
    observations: list[dict[str, Any]],
    methods: list[str] | None = None,
    common_size_bases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Calculate research-ready growth and common-size metrics by exact period."""

    requested = list(dict.fromkeys(methods or ["yoy", "qoq", "cagr", "common_size"]))
    unsupported = [item for item in requested if item not in {"yoy", "qoq", "cagr", "common_size"}]
    if unsupported:
        return _missing("unsupported_trend_method", f"不支持的趋势方法：{', '.join(unsupported)}")
    bases = {str(key).lower(): str(value).lower() for key, value in (common_size_bases or {}).items()}
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in observations:
        value = item.get("value")
        period = normalize_financial_period(item.get("period", item.get("as_of")))
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            rejected.append({**item, "reason": "non_numeric_value"})
            continue
        if period["period_type"] == "unknown":
            rejected.append({**item, "reason": "unknown_financial_period"})
            continue
        rows.append({**item, "period": period["normalized"], "period_metadata": period})
    series: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for item in rows:
        key = (
            str(item.get("entity") or "").lower(),
            str(item.get("metric") or "").lower(),
            str(item.get("unit") or "").lower(),
            str(item.get("scope") or "").lower(),
            str(item["period_metadata"]["period_type"]),
        )
        series.setdefault(key, []).append(item)
    results: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for key, items in series.items():
        ordered = sorted(items, key=lambda item: int(item["period_metadata"]["sort_key"]))
        indexed = {int(item["period_metadata"]["sort_key"]): item for item in ordered}
        for item in ordered:
            meta = item["period_metadata"]
            if "yoy" in requested:
                delta = 100 if meta["period_type"] == "annual" else 100
                prior = indexed.get(int(meta["sort_key"]) - delta)
                _append_growth_result(results, issues, item, prior, "yoy")
            if "qoq" in requested and meta["period_type"] == "quarterly":
                year, quarter = int(meta["year"]), int(meta["quarter"])
                previous_key = (year - 1) * 100 + 12 if quarter == 1 else year * 100 + (quarter - 1) * 3
                _append_growth_result(results, issues, item, indexed.get(previous_key), "qoq")
        if "cagr" in requested and len(ordered) >= 2:
            first, last = ordered[0], ordered[-1]
            years = _elapsed_years(first["period_metadata"], last["period_metadata"])
            first_value, last_value = float(first["value"]), float(last["value"])
            if years <= 0 or first_value <= 0 or last_value < 0:
                issues.append({"method": "cagr", "series_key": "|".join(key), "reason": "invalid_cagr_base_or_span"})
            else:
                results.append(_trend_record(last, "cagr", (last_value / first_value) ** (1.0 / years) - 1.0, first))
    if "common_size" in requested:
        _append_common_size(results, issues, rows, bases)
    payload = {
        "observations": observations,
        "methods": requested,
        "common_size_bases": common_size_bases or {},
        "results": results,
        "rejected": rejected,
        "issues": issues,
    }
    status_value = "partial" if rejected or issues else "ok"
    record = FINANCIAL_TREND_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_financial_trends",
        status=status_value,
        source_ids=[str(item.get("source_id")) for item in rows if str(item.get("source_id") or "")],
        basis={"methods": requested, "common_size_bases": common_size_bases or {}, "observations": observations},
    )
    partial_reasons = sorted({
        *[str(item.get("reason") or "rejected_observation") for item in rejected],
        *[str(item.get("reason") or "trend_issue") for item in issues],
    })
    return {
        "success": status_value == "ok",
        "business_success": status_value == "ok",
        "system_success": True,
        "transport_success": True,
        "status": status_value,
        "data_completeness": "complete" if status_value == "ok" else "partial",
        "partial_reasons": partial_reasons,
        "financial_trend_id": record["object_id"],
        "results": results,
        "rejected": rejected,
        "issues": issues,
        "resource_uris": [record["resource_uri"]],
        "warnings": (["部分趋势因期间、缺失值、零基期或口径不足无法计算"] if rejected or issues else []),
        "blockers": [],
        "next_actions": ["核对期间粒度、单位和共同比基准后再用于研报"],
    }


def _trend_record(item: dict[str, Any], method: str, value: float, base: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity": item.get("entity"), "metric": item.get("metric"), "period": item.get("period"),
        "unit": item.get("unit"), "method": method, "result": value,
        "base_period": base.get("period"), "base_value": base.get("value"), "current_value": item.get("value"),
    }


def _append_growth_result(
    results: list[dict[str, Any]], issues: list[dict[str, Any]], item: dict[str, Any],
    prior: dict[str, Any] | None, method: str,
) -> None:
    if prior is None:
        issues.append({"method": method, "entity": item.get("entity"), "metric": item.get("metric"), "period": item.get("period"), "reason": "missing_comparison_period"})
        return
    base = float(prior["value"])
    if base == 0:
        issues.append({"method": method, "entity": item.get("entity"), "metric": item.get("metric"), "period": item.get("period"), "reason": "zero_comparison_base"})
        return
    results.append(_trend_record(item, method, (float(item["value"]) - base) / abs(base), prior))


def _elapsed_years(first: dict[str, Any], last: dict[str, Any]) -> float:
    first_month = _period_month_index(first)
    last_month = _period_month_index(last)
    return (last_month - first_month) / 12.0


def _period_month_index(period: dict[str, Any]) -> int:
    year = int(period["year"])
    if period["period_type"] == "annual":
        month = 12
    elif period["period_type"] == "quarterly":
        month = int(period["quarter"]) * 3
    else:
        month = int(period["month"])
    return year * 12 + month


def _append_common_size(
    results: list[dict[str, Any]], issues: list[dict[str, Any]], rows: list[dict[str, Any]],
    bases: dict[str, str],
) -> None:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for item in rows:
        key = (
            str(item.get("entity") or "").lower(), str(item.get("period") or ""),
            str(item.get("unit") or "").lower(), str(item.get("statement") or "").lower(),
        )
        grouped.setdefault(key, []).append(item)
    defaults = {"income_statement": "revenue", "balance_sheet": "total_assets", "cash_flow_statement": "operating_cash_flow"}
    for key, items in grouped.items():
        statement = key[3]
        base_metric = bases.get(statement) or defaults.get(statement)
        base = next((item for item in items if item.get("is_common_size_base") is True), None)
        if base is None and base_metric:
            base = next((item for item in items if str(item.get("metric") or "").lower() == base_metric), None)
        if base is None or float(base.get("value") or 0) == 0:
            issues.append({"method": "common_size", "group_key": "|".join(key), "reason": "missing_or_zero_common_size_base"})
            continue
        denominator = float(base["value"])
        for item in items:
            results.append(_trend_record(item, "common_size", float(item["value"]) / denominator, base))


def _missing_pack_fields(
    expected_fields: list[str],
    fact_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """列出期望字段中没有带值候选事实支撑的缺口；无缺口返回空数组，不编数补齐。"""
    missing: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field in expected_fields:
        name = str(field or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        supported = False
        attempted = False
        for candidate in fact_candidates:
            if not isinstance(candidate, dict):
                continue
            label = str(candidate.get("metric") or candidate.get("field") or "").strip()
            if label.lower() != name.lower():
                continue
            attempted = True
            if candidate.get("value") is not None:
                supported = True
                break
        if not supported:
            missing.append(
                {
                    "field": name,
                    "reason": "candidate_without_value" if attempted else "no_fact_candidate",
                }
            )
    return missing


# Minimum number of independent source families that must agree on the same
# numeric value before a web-sourced field is auto-accepted for an
# ``estimate_preview`` run.  Two URLs from one registrable domain are one
# family, so this genuinely means two independent origins — never one site
# echoed twice.  Auto-acceptance NEVER upgrades formal-delivery eligibility;
# that still requires the single human node at the thirteen-table review.
_MIN_COROBORATING_FAMILIES = 2


def _registrable_domain(url: str) -> str:
    """Registrable domain (eTLD+1) with a compact Chinese public-suffix set.

    ``a.gov.cn`` and ``b.gov.cn`` are different families; ``news.sina.com.cn``
    and ``finance.sina.com.cn`` collapse to ``sina.com.cn``.  A deliberately
    small implementation — corroboration counting only needs to avoid treating
    two hosts under one publisher as independent origins.
    """

    from urllib.parse import urlparse

    host = urlparse(url).netloc.lower()
    if not host:
        host = str(url or "").strip().lower().split("/")[0]
    host = host.split("@")[-1].split(":")[0]
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    multi_part_suffixes = {
        ("com", "cn"), ("gov", "cn"), ("org", "cn"), ("net", "cn"),
        ("edu", "cn"), ("ac", "cn"),
    }
    if tuple(labels[-2:]) in multi_part_suffixes:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _source_family(source: dict[str, Any]) -> str:
    """Registrable-domain family for a source; controlled files stand alone.

    A controlled upload has no public domain, so each is its own family keyed
    by ``source_id`` — a single uploaded file can never self-corroborate.
    """

    url = str(source.get("url") or "")
    domain = _registrable_domain(url) if url else ""
    return domain or f"file:{source.get('source_id')}"


def _adjudicate_estimate_fields(
    selected: list[dict[str, Any]],
    fact_candidates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Auto-accept a field only when ≥N independent families agree on one value.

    Returns ``field -> {value, unit, families, source_ids, assurance}`` for the
    fields that clear the corroboration bar.  Conflicts (families disagree) and
    thin evidence (one family) are deliberately left out so the caller records
    them as ``missing`` rather than guessing.  The output is always
    ``estimate_preview`` grade — this path exists so upstream stays fully
    automatic for rough sizing, not so it can bypass the human delivery gate.
    """

    family_by_source = {
        str(doc.get("source_id")): _source_family(doc) for doc in selected
    }
    selected_ids = set(family_by_source)
    # field -> value_key -> {families:set, source_ids:set, unit}
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for candidate in fact_candidates:
        if not isinstance(candidate, dict):
            continue
        numeric = candidate.get("numeric_value")
        if not isinstance(numeric, (int, float)) or isinstance(numeric, bool):
            continue  # only gate-approved numbers can auto-corroborate
        source_id = str(candidate.get("source_id") or "")
        if source_id not in selected_ids:
            continue
        field = str(candidate.get("field") or candidate.get("metric") or "").strip()
        if not field:
            continue
        # Bucket by the exact numeric value + unit so different units are never
        # silently merged into one "agreement".
        unit = str(candidate.get("expected_unit") or "")
        value_key = f"{numeric}|{unit}"
        bucket = grouped.setdefault(field, {}).setdefault(
            value_key, {"value": numeric, "unit": unit, "families": set(), "source_ids": set()}
        )
        bucket["families"].add(family_by_source[source_id])
        bucket["source_ids"].add(source_id)
    accepted: dict[str, dict[str, Any]] = {}
    for field, buckets in grouped.items():
        # A field is auto-accepted only when exactly one value bucket clears the
        # family bar.  If two different values each reach it, that is a conflict,
        # not corroboration — leave it for the caller to record as missing.
        clearing = [
            b for b in buckets.values()
            if len(b["families"]) >= _MIN_COROBORATING_FAMILIES
        ]
        if len(clearing) != 1:
            continue
        winner = clearing[0]
        accepted[field] = {
            "value": winner["value"],
            "unit": winner["unit"],
            "families": sorted(winner["families"]),
            "source_ids": sorted(winner["source_ids"]),
            "assurance": "estimate_preview",
        }
    return accepted


def build_evidence_pack(
    workspace_id: str,
    task_id: str,
    selected_source_ids: list[str] | None,
    fact_candidates: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    expected_fields: list[str] | None = None,
    candidate_set_id: str = "",
    selected_candidate_ids: list[str] | None = None,
    evidence_track: str = "real",
    fixture_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_track = str(evidence_track or "real").strip()
    if evidence_track not in EVIDENCE_TRACKS:
        return _missing("evidence_track_invalid", "evidence_track 必须为 real、technical_fixture 或 controlled_assumption")
    if evidence_track != "technical_fixture" and fixture_manifest:
        return _missing("fixture_manifest_not_applicable", "fixture_manifest 仅允许用于 technical_fixture 轨")
    documents = _documents_from_task(
        workspace_id,
        task_id,
    )
    if not documents:
        return _missing("analysis_task_not_found", "没有可固化的分析任务")
    if selected_source_ids == []:
        return _missing("no_selected_sources", "selected_source_ids 显式为空，不会自动回退到全部来源")
    selected = (
        documents
        if selected_source_ids is None
        else [doc for doc in documents if doc.get("source_id") in selected_source_ids]
    )
    if not selected:
        return _missing("no_selected_sources", "未选择有效来源")
    # Formal evidence candidates must be selected from an immutable candidate
    # set produced by this service.  Caller-authored candidate objects remain a
    # compatibility surface for estimate_preview only and can never acquire
    # formal evidence standing merely by being copied into a pack.
    candidate_set = None
    server_signed_candidates = False
    if candidate_set_id:
        candidate_set = CANDIDATE_STORE.get(
            workspace_id,
            candidate_set_id,
        )
        candidate_payload = (candidate_set or {}).get("payload") or {}
        if (
            candidate_set is None
            or str(candidate_payload.get("analysis_task_id") or "") != task_id
        ):
            return _missing("candidate_set_not_found", "未找到属于该分析任务的候选事实集")
        available = {
            str(item.get("candidate_id") or ""): item
            for item in candidate_payload.get("fact_candidates") or []
            if isinstance(item, dict) and item.get("candidate_id")
        }
        requested_ids = [str(item) for item in (selected_candidate_ids or []) if str(item)]
        if requested_ids:
            unknown = sorted(set(requested_ids) - set(available))
            if unknown:
                return _missing("candidate_not_found", "候选事实不存在或不属于指定候选集")
            fact_candidates = [dict(available[item]) for item in requested_ids]
        else:
            fact_candidates = [dict(item) for item in available.values()]
        server_signed_candidates = True
    elif selected_candidate_ids:
        return _missing("candidate_set_required", "selected_candidate_ids 必须与 candidate_set_id 一起使用")

    selected_ids = {str(doc.get("source_id") or "") for doc in selected}
    if server_signed_candidates and any(
        str(item.get("source_id") or "") not in selected_ids
        for item in fact_candidates
        if isinstance(item, dict)
    ):
        return _missing("candidate_source_not_selected", "候选事实来源不在选定来源集合中")

    limits = []
    for doc in selected:
        if evidence_track == "real" and doc.get("source_type") == "web_snapshot":
            limits.append(f"{doc.get('source_id')}: 公开网络候选，未自动升级为正式财务输入")
        if (
            evidence_track == "real"
            and doc.get("source_type") == "controlled_file"
            and not doc.get("formal_use_allowed")
        ):
            limits.append(f"{doc.get('source_id')}: 受控文件尚未具备正式使用资格")
    missing_fields = _missing_pack_fields(expected_fields or [], fact_candidates)
    # Estimate-grade auto-acceptance: fields where ≥N independent source families
    # agree on one gate-approved number.  This keeps upstream fully automatic for
    # rough sizing; it never grants formal delivery (that stays at the downstream
    # thirteen-table human node).  Fields that do not clear the bar are not filled.
    auto_accepted = _adjudicate_estimate_fields(selected, fact_candidates)
    formal_sources_ok = all(bool(doc.get("formal_use_allowed")) for doc in selected)
    formal_evidence_candidate = bool(
        evidence_track == "real"
        and
        server_signed_candidates
        and formal_sources_ok
        and not conflicts
        and not missing_fields
        and fact_candidates
        and all(
            isinstance(item, dict)
            and item.get("candidate_id")
            and item.get("source_id")
            and isinstance(item.get("locator"), dict)
            and item.get("formal_use_allowed") is True
            for item in fact_candidates
        )
    )
    normalized_fixture_manifest = None
    fixture_errors: list[str] = []
    technical_fixture_candidate = False
    if evidence_track == "technical_fixture":
        normalized_fixture_manifest, fixture_errors = _validate_fixture_manifest(
            fixture_manifest,
            selected,
            fact_candidates,
        )
        technical_fixture_candidate = bool(
            normalized_fixture_manifest
            and server_signed_candidates
            and not conflicts
            and not missing_fields
            and fact_candidates
            and all(
                isinstance(item, dict)
                and item.get("candidate_id")
                and item.get("source_id")
                and isinstance(item.get("locator"), dict)
                and item.get("locator")
                for item in fact_candidates
            )
        )
        if fixture_errors:
            limits.extend(fixture_errors)
    elif evidence_track == "controlled_assumption":
        limits.append("controlled_assumption: 受控假设只能用于 estimate_preview")
    payload = {
        "analysis_task_id": task_id,
        "evidence_track": evidence_track,
        "technical_fixture_candidate": technical_fixture_candidate,
        "fixture_manifest": normalized_fixture_manifest,
        "candidate_set_id": candidate_set_id or None,
        "server_signed_candidates": server_signed_candidates,
        "formal_evidence_candidate": formal_evidence_candidate,
        "sources": [
            {
                key: doc.get(key)
                for key in (
                    "source_id", "source_type", "title", "url", "content_hash",
                    "fetched_at", "status", "formal_use_allowed",
                    "formal_use_decision", "ocr_formal_use_decision",
                    "manual_review_status", "ocr_review_ledger_count",
                    "unresolved_low_confidence_locator_count", "locators",
                )
            }
            for doc in selected
        ],
        "fact_candidates": fact_candidates,
        "auto_accepted_estimate_fields": auto_accepted,
        "missing_fields": missing_fields,
        "conflicts": conflicts,
        "limitations": limits,
        "finance_boundary": "证据包不等于已确认 FinanceSpec；real 轨只有服务端候选且来源正式资格完整时可进入正式证据绑定；technical_fixture 仅验证技术链且永不获得正式资格；controlled_assumption 与调用方自报候选只允许 estimate_preview。",
    }
    status_value = "partial" if limits or conflicts or missing_fields else "ok"
    record = EVIDENCE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_build_evidence_pack",
        status=status_value,
        source_ids=[str(doc.get("source_id")) for doc in selected],
        basis=[{"source_id": doc.get("source_id"), "content_hash": doc.get("content_hash")} for doc in selected],
    )
    warnings = [*limits]
    if missing_fields:
        warnings.append("期望字段存在证据缺口，evidence pack 记为 partial")
    partial_reasons = [
        *[f"limitation:{item}" for item in limits],
        *[f"conflict:{item.get('field') or item.get('metric') or 'unknown'}" for item in conflicts],
        *[f"missing_field:{item.get('field') or 'unknown'}" for item in missing_fields],
    ]
    return {
        "success": status_value == "ok",
        "business_success": status_value == "ok",
        "system_success": True,
        "transport_success": True,
        "status": status_value,
        "data_completeness": "complete" if status_value == "ok" else "partial",
        "partial_reasons": partial_reasons,
        "evidence_pack_id": record["object_id"],
        "basis_hash": record["basis_hash"],
        "source_count": len(selected),
        "limitations": limits,
        "missing_fields": missing_fields,
        "auto_accepted_estimate_fields": auto_accepted,
        "formal_evidence_candidate": formal_evidence_candidate,
        "technical_fixture_candidate": technical_fixture_candidate,
        "evidence_track": evidence_track,
        "fixture_manifest_hash": (
            sha256_json(normalized_fixture_manifest) if normalized_fixture_manifest else None
        ),
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": [],
        "next_actions": ["auto_accepted_estimate_fields 可直接喂 estimate_preview 匡算；正式交付仍走十三表人工节点"],
    }


def list_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    allowed = {kind for _store, kind in _RESOURCE_STORES}
    if resource_type and resource_type not in allowed:
        return _missing("resource_type_invalid", "未知 Resource 类型过滤条件")
    entries = []
    for store, kind in _RESOURCE_STORES:
        if resource_type and kind != resource_type:
            continue
        for record in store.list(workspace_id):
            uri = str(record.get("resource_uri") or "")
            if uri:
                entries.append({
                    "uri": uri,
                    "name": str(record.get("object_id") or ""),
                    "resource_type": kind,
                    "mime_type": "application/json",
                    "created_at": record.get("created_at"),
                })
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _missing(str(exc), "Resource 分页游标无效或列表已变化")
    resources = page["resources"]
    return {
        "success": True,
        "status": "ok",
        "resources": resources,
        "next_cursor": page["next_cursor"],
        "has_more": page["has_more"],
        "snapshot_hash": page["snapshot_hash"],
        "resource_uris": [item["uri"] for item in resources],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def resolve_resource(
    uri: str,
    workspace_id: str,
) -> dict[str, Any] | None:
    expected = f"lvke://data-analysis/workspaces/{workspace_id}/"
    if not str(uri).startswith(expected):
        return None
    for store, _kind in _RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None:
            return record
    return None


def _missing(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "transport_success": True,
        "business_success": False,
        "completed": False,
        "outcome": "blocked",
        "status": "blocked",
        "code": code,
        "message": message,
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [],
    }
