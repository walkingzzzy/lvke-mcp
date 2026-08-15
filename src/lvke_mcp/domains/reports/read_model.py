"""Read-only access and deterministic section validation for report revisions."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from lvke_mcp.adapters.report_repository import REVISION_STORE
from lvke_mcp.domains.reports.headings import heading_titles_match

_SECTION_ID_RE = re.compile(r"^sec_[a-z0-9][a-z0-9_-]{2,79}$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_QUANTIFIED_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<value>-?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>元/kWh|元/千瓦时|GW|MW|kW|MWh|kWh|亿元|万元|元|公顷|ha|"
    r"公里|千米|km|万平方米|平方米|㎡|亩|米|m|吨|t|年|个月|月|天|小时|h|%|％)",
    re.IGNORECASE,
)
_CITATION_RE = re.compile(
    r"\[[^\]\n]+\]\((?:https?://|lvke://)[^)\n]+\)|"
    r"\[\^[^\]]+\]|"
    r"\[(?:\d+|[FS]\d+|[A-Z]\d+)\]|"
    r"\[(?:来源|证据|财务表)[^\]]*\]|"
    r"（来源[:：][^)）]+[)）]|"
    r"(?:lvke://|evidence_id[:=]|source_snapshot_id[:=]|locator[:=])",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"[^\u3002！？!?;；\n]+[\u3002！？!?;；]?|\n")


def generated_section_id(title: str, order: int, parent_section_id: str = "") -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", title.lower()))[:32] or "section"
    digest = hashlib.sha256(
        f"{parent_section_id}|{order}|{title}".encode("utf-8")
    ).hexdigest()[:10]
    return f"sec_{slug}_{digest}"


def normalize_outline(value: Any) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    if not isinstance(value, list):
        return [], [], ["outline_invalid"]
    titles: list[str] = []
    sections: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            title = item.strip()
            explicit_id = ""
            parent_section_id = ""
            depth = 1
        elif isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            explicit_id = str(item.get("section_id") or "").strip()
            parent_section_id = str(item.get("parent_section_id") or "").strip()
            try:
                depth = int(item.get("depth") or 1)
            except (TypeError, ValueError):
                depth = 0
        else:
            errors.append(f"outline_item_invalid:{index}")
            continue
        if not title:
            errors.append(f"outline_title_required:{index}")
            continue
        section_id = explicit_id or generated_section_id(title, index, parent_section_id)
        if not _SECTION_ID_RE.fullmatch(section_id):
            errors.append(f"outline_section_id_invalid:{index}")
            continue
        if section_id in seen_ids:
            errors.append(f"outline_section_id_duplicate:{section_id}")
            continue
        if depth < 1 or depth > 6:
            errors.append(f"outline_depth_invalid:{index}")
            continue
        if parent_section_id and parent_section_id not in seen_ids:
            errors.append(f"outline_parent_invalid:{index}")
            continue
        seen_ids.add(section_id)
        titles.append(title)
        sections.append(
            {
                "section_id": section_id,
                "title": title,
                "order": index,
                "parent_section_id": parent_section_id or None,
                "depth": depth,
            }
        )
    return titles, sections, errors


def revision_sections(record: dict[str, Any]) -> list[dict[str, Any]]:
    upstream = (record.get("payload") or {}).get("upstream") or {}
    descriptors = upstream.get("sections")
    if isinstance(descriptors, list) and all(isinstance(item, dict) for item in descriptors):
        return [dict(item) for item in descriptors]
    _titles, sections, _errors = normalize_outline(list(upstream.get("outline") or []))
    return sections


def section_span(content: str, title: str) -> dict[str, Any] | None:
    headings = list(_HEADING_RE.finditer(content))
    matches: list[dict[str, Any]] = []
    for index, match in enumerate(headings):
        if not heading_titles_match(match.group(2), title):
            continue
        level = len(match.group(1))
        end = len(content)
        for following in headings[index + 1 :]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        matches.append({
            "start": match.start(),
            "end": end,
            "level": level,
            "heading": match.group(0).strip(),
            "content": content[match.start():end],
        })
    # Duplicate canonical headings are ambiguous. Fail closed instead of
    # reading or replacing an arbitrary occurrence.
    return matches[0] if len(matches) == 1 else None


def section_content(content: str, title: str) -> tuple[str, bool]:
    span = section_span(content, title)
    if span is None:
        return "", False
    return str(span["content"]).strip(), True


def capture_document_snapshot(workspace_id: str, *, revision_id: str = "") -> dict[str, Any]:
    from lvke_mcp.domains.reports import doc_service as doc

    document = dict(doc.read_document(workspace_id, revision_id=revision_id))
    meta = doc.ensure_workspace(workspace_id)
    document["report_type"] = doc.resolve_report_type(meta)
    return document


def supplied_document_snapshot(workspace_id: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("content"), str):
        return None
    snapshot = dict(value)
    claimed_workspace = str(snapshot.get("workspace_id") or workspace_id)
    if claimed_workspace != workspace_id:
        return None
    snapshot["workspace_id"] = workspace_id
    snapshot["revision_id"] = str(snapshot.get("revision_id") or "")
    snapshot["report_type"] = str(snapshot.get("report_type") or "generic_feasibility")
    return snapshot


def resolve_revision_record(
    workspace_id: str, revision_id: str
) -> tuple[dict[str, Any] | None, bool]:
    """Resolve a public revision, with one-cycle native-id compatibility."""

    try:
        record = REVISION_STORE.get(workspace_id, revision_id)
    except ValueError:
        record = None
    if record is not None:
        return record, False
    matches = [
        item
        for item in REVISION_STORE.list(workspace_id)
        if str((item.get("payload") or {}).get("native_revision_id") or "") == revision_id
    ]
    if not matches:
        return None, False
    matches.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return matches[0], True


def list_sections(workspace_id: str, revision_id: str) -> dict[str, Any]:
    record, _native_alias = resolve_revision_record(workspace_id, revision_id)
    if record is None:
        return _failure("revision_not_found", "未找到研报修订")
    sections = revision_sections(record)
    return _ok(
        {
            "report_revision_id": record["object_id"],
            "sections": sections,
            "section_count": len(sections),
        },
        [],
    )


def get_section(workspace_id: str, revision_id: str, section_id: str) -> dict[str, Any]:
    record, _native_alias = resolve_revision_record(workspace_id, revision_id)
    if record is None:
        return _failure("revision_not_found", "未找到研报修订")
    descriptor = next(
        (item for item in revision_sections(record) if item.get("section_id") == section_id),
        None,
    )
    if descriptor is None:
        return _failure("section_not_found", "未找到该研报章节")
    document = supplied_document_snapshot(
        workspace_id, (record.get("payload") or {}).get("document_snapshot")
    )
    if document is None:
        native_revision_id = str((record.get("payload") or {}).get("native_revision_id") or "")
        if not native_revision_id:
            return _failure("revision_snapshot_missing", "修订缺少不可变 document_snapshot")
        try:
            document = capture_document_snapshot(workspace_id, revision_id=native_revision_id)
        except Exception:  # noqa: BLE001
            return _failure("revision_snapshot_missing", "修订快照不存在或不可读取")
    content, found = section_content(str(document.get("content") or ""), descriptor["title"])
    return _ok(
        {
            "report_revision_id": record["object_id"],
            "section": descriptor,
            "content": content,
            "content_hash": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "found_in_document": found,
        },
        [] if found else ["章节已在 outline 固化，但当前正文尚无对应标题"],
    )


def quantitative_statements(content: str) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = []
    lines = content.splitlines(keepends=True)
    paragraph_lines: list[tuple[int, int, str]] = []

    def flush() -> None:
        if not paragraph_lines:
            return
        start_line = paragraph_lines[0][0]
        paragraph_offset = paragraph_lines[0][1]
        paragraph = "".join(text for _line, _offset, text in paragraph_lines)
        paragraph_lines.clear()
        if not paragraph.strip() or paragraph.lstrip().startswith("#"):
            return
        searchable = re.sub(
            r"〔\d{4}〕\d+号?", lambda match: " " * len(match.group(0)), paragraph
        )
        if not _QUANTIFIED_VALUE_RE.search(searchable):
            return
        sentence_spans = [
            match for match in _SENTENCE_RE.finditer(paragraph) if match.group(0) != "\n"
        ]
        citations = list(_CITATION_RE.finditer(paragraph))
        for value_match in _QUANTIFIED_VALUE_RE.finditer(searchable):
            sentence_index = next(
                (
                    index
                    for index, sentence in enumerate(sentence_spans)
                    if sentence.start() <= value_match.start() < sentence.end()
                ),
                -10,
            )
            bound: list[str] = []
            for citation in citations:
                citation_sentence = next(
                    (
                        index
                        for index, sentence in enumerate(sentence_spans)
                        if sentence.start() <= citation.start() < sentence.end()
                    ),
                    10,
                )
                if (
                    0 < citation_sentence < len(sentence_spans)
                    and _CITATION_RE.fullmatch(
                        sentence_spans[citation_sentence].group(0).strip()
                    )
                ):
                    citation_sentence -= 1
                if abs(citation_sentence - sentence_index) <= 1:
                    bound.append(citation.group(0))
            global_offset = paragraph_offset + value_match.start()
            excerpt_match = (
                sentence_spans[sentence_index]
                if 0 <= sentence_index < len(sentence_spans)
                else None
            )
            statements.append(
                {
                    "line": content.count("\n", 0, global_offset) + 1
                    if global_offset >= 0
                    else start_line,
                    "offset": global_offset,
                    "values": [value_match.group(0).strip()],
                    "value": value_match.group("value"),
                    "unit": value_match.group("unit"),
                    "citation_count": len(bound),
                    "citations": bound,
                    "excerpt": (
                        excerpt_match.group(0).strip() if excerpt_match else paragraph
                    )[:300],
                }
            )

    offset = 0
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            flush()
            offset += len(line)
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            flush()
            offset += len(line)
            continue
        paragraph_lines.append((line_number, offset, line))
        offset += len(line)
    flush()
    return statements


def validate_section(workspace_id: str, revision_id: str, section_id: str) -> dict[str, Any]:
    record, _native_alias = resolve_revision_record(workspace_id, revision_id)
    if record is None:
        return _failure("revision_not_found", "未找到研报修订")
    fetched = get_section(workspace_id, revision_id, section_id)
    if fetched.get("status") != "ok":
        return fetched
    content = str(fetched.get("content") or "")
    blockers: list[str] = []
    warnings: list[str] = []
    if not fetched.get("found_in_document"):
        blockers.append("section_heading_missing")
    if not re.sub(r"^#{1,6}\s+.+$", "", content, flags=re.MULTILINE).strip():
        blockers.append("section_content_empty")
    placeholders = re.findall(
        r"(?:TODO|TBD|待补充|待完善|XXX)", content, flags=re.IGNORECASE
    )
    if placeholders:
        blockers.append("section_placeholder_present")
    quantitative = quantitative_statements(content)
    numeric_count = sum(len(item["values"]) for item in quantitative)
    citation_count = len(_CITATION_RE.findall(content))
    uncited = [item for item in quantitative if item["citation_count"] == 0]
    if uncited:
        blockers.append("section_citation_missing")
        warnings.append("章节含未绑定引用或证据 locator 的定量陈述")
    blocked = bool(blockers)
    return {
        **fetched,
        "success": not blocked,
        "transport_success": True,
        "business_success": not blocked,
        "completed": not blocked,
        "outcome": "blocked" if blocked else "ok",
        "status": "blocked" if blocked else "ok",
        "code": "section_citation_missing" if uncited else None,
        "valid": not blocked,
        "validation": {
            "scope": "section_only",
            "heading_present": bool(fetched.get("found_in_document")),
            "placeholder_count": len(placeholders),
            "numeric_statement_count": numeric_count,
            "citation_marker_count": citation_count,
            "uncited_numeric_statements": uncited,
            "evidence_basis_bound": bool((record.get("payload") or {}).get("basis_hash")),
        },
        "validation_complete": False,
        "warnings": [
            *warnings,
            "章节局部校验不能替代整篇 report_validate 与统一交付审查",
        ],
        "blockers": blockers,
        "next_actions": [] if not blockers else ["修订该章节后重新执行局部及整篇校验"],
    }


def _ok(data: dict[str, Any], note: str | list[str]) -> dict[str, Any]:
    warnings = [note] if isinstance(note, str) else list(note)
    return {
        "success": True,
        "status": "ok",
        **data,
        "resource_uris": list(data.get("resource_uris") or []),
        "warnings": warnings,
        "blockers": [],
        "next_actions": [],
    }


def _failure(code: str, message: str) -> dict[str, Any]:
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
