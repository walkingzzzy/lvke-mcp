"""报告编排共享基座：终态集合、read_model 别名、成功/失败信封与 section 补丁工具。"""

from __future__ import annotations

import re
from typing import Any


from lvke_mcp.domains.reports import read_model as report_read_model


_TASK_TERMINAL = {"done", "completed", "partial", "failed", "cancelled"}


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


_normalize_outline = report_read_model.normalize_outline


_revision_sections = report_read_model.revision_sections


_section_content = report_read_model.section_content


_section_span = report_read_model.section_span


_capture_document_snapshot = report_read_model.capture_document_snapshot


_supplied_document_snapshot = report_read_model.supplied_document_snapshot


_resolve_revision_record = report_read_model.resolve_revision_record


def _resolve_target_sections(
    preparation_payload: dict[str, Any],
    requested: list[Any],
) -> tuple[list[str], list[dict[str, str]], str | None]:
    """Map stable section IDs or exact titles to one canonical descriptor."""

    descriptors = [
        item
        for item in preparation_payload.get("sections") or []
        if isinstance(item, dict)
    ]
    if not descriptors:
        _titles, descriptors, _errors = _normalize_outline(
            list(preparation_payload.get("outline") or [])
        )
    by_id = {str(item.get("section_id") or ""): item for item in descriptors}
    by_title = {str(item.get("title") or ""): item for item in descriptors}
    canonical_titles: list[str] = []
    mappings: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for raw in requested:
        value = str(raw or "").strip()
        descriptor = by_id.get(value) or by_title.get(value)
        if descriptor is None:
            return [], [], "proposal_target_outside_outline"
        section_id = str(descriptor.get("section_id") or "")
        if section_id in seen_ids:
            return [], [], "proposal_target_duplicate"
        seen_ids.add(section_id)
        title = str(descriptor.get("title") or "")
        canonical_titles.append(title)
        mappings.append({"section_id": section_id, "title": title})
    return canonical_titles, mappings, None


def _merge_section_patch(
    document: str,
    descriptor: dict[str, Any],
    proposed_content: str,
    *,
    descriptors: list[dict[str, Any]] | None = None,
) -> tuple[str, str] | None:
    title = str(descriptor.get("title") or "").strip()
    if not title:
        return None
    span = _section_span(document, title)
    patch = str(proposed_content or "").strip()
    if not patch:
        return None
    level = int(descriptor.get("depth") or 1)
    level = min(6, max(1, level))
    first_heading = _HEADING_RE.match(patch)
    if first_heading and first_heading.group(2).strip() == title:
        replacement = patch
    else:
        replacement = f"{'#' * (int(span['level']) if span else level)} {title}\n\n{patch}"
    if span is None:
        # A frozen outline may legitimately precede any body text. Insert the
        # missing section by immutable descriptor order; do not guess from the
        # prose or create a second free-form outline.
        ordered = sorted(
            [item for item in (descriptors or []) if isinstance(item, dict)],
            key=lambda item: (int(item.get("order") or 0), str(item.get("section_id") or "")),
        )
        target_order = int(descriptor.get("order") or 0)
        insert_at = len(document)
        for candidate in ordered:
            if int(candidate.get("order") or 0) <= target_order:
                continue
            candidate_span = _section_span(document, str(candidate.get("title") or ""))
            if candidate_span is not None:
                insert_at = int(candidate_span["start"])
                break
        prefix = document[:insert_at].rstrip()
        suffix = document[insert_at:].lstrip()
        merged = replacement.rstrip()
        if prefix:
            merged = prefix + "\n\n" + merged
        if suffix:
            merged = merged + "\n\n" + suffix
        return merged.rstrip() + "\n", ""
    original = str(span["content"])
    trailing = original[len(original.rstrip()):]
    if not trailing:
        trailing = "\n\n" if int(span["end"]) < len(document) else "\n"
    replacement = replacement.rstrip() + trailing
    merged = document[: int(span["start"])] + replacement + document[int(span["end"]):]
    return merged, original.strip()


def _materialize_local_document_snapshot(
    workspace_id: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Persist an explicit local snapshot as the native proposal baseline.

    The document proposal service derives diff/apply freshness from the native
    current revision. Keeping only a public report snapshot would give the two
    layers different baselines and turn a section patch into an apparent whole-
    document replacement.
    """

    from lvke_mcp.domains.reports import doc_service as doc

    meta = doc.ensure_workspace(workspace_id)
    current_revision_id = str(meta.get("current_revision_id") or "")
    current_content = doc.revision_content(workspace_id, current_revision_id) or ""
    content = str(snapshot.get("content") or "")
    if current_content == content:
        native_revision_id = current_revision_id
    else:
        revision = doc._save_revision(  # noqa: SLF001 - same product boundary
            workspace_id,
            content=content,
            parent_id=current_revision_id,
            summary="绑定 report_start 显式不可变正文快照",
            source="agent",
        )
        native_revision_id = str(revision["revision_id"])
        meta["current_revision_id"] = native_revision_id
        meta["updated_at"] = str(revision.get("created_at") or "")
        doc._write_meta(workspace_id, meta)  # noqa: SLF001 - same product boundary
    return {**snapshot, "revision_id": native_revision_id}


def _normalize_finance_binding(args: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    typed = args.get("finance_binding")
    legacy_run = str(args.get("run_id") or "")
    legacy_package = str(args.get("finance_tables_package_id") or "")
    if isinstance(typed, dict):
        errors = []
        if legacy_run or legacy_package:
            errors.append("ambiguous_finance_binding")
        kind = str(typed.get("kind") or "")
        if kind not in {"generic_feasibility", "asset_acquisition"}:
            errors.append("finance_binding_kind_invalid")
        return {
            "kind": kind,
            "run_id": str(typed.get("run_id") or ""),
            "package_id": str(typed.get("package_id") or ""),
        }, errors
    return {
        "kind": "generic_feasibility",
        "run_id": legacy_run,
        "package_id": legacy_package,
    }, []


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
        "success": False, "transport_success": True,
        "business_success": False, "completed": False, "outcome": "blocked",
        "status": "blocked", "code": code, "message": message,
        "resource_uris": [], "warnings": [], "blockers": [code], "next_actions": [],
    }
