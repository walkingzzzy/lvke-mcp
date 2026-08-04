"""Thin orchestration over existing report, document and artifact services."""

from __future__ import annotations

import hmac
import hashlib
import json
import re
from typing import Any
from urllib.parse import quote, unquote

from lvke_mcp.runtime.storage import (
    JSONArtifactStore,
    paginate_resource_entries,
)
from lvke_mcp.domains.review.deliverable_review_compat import full_review_requirement
from lvke_mcp.servers.lvke_asset_acquisition.tables import get_package_record
from lvke_mcp.servers.lvke_data_analysis.service import EVIDENCE_STORE
from lvke_mcp.servers.lvke_deep_research.package_service import PACKAGE_STORE as RESEARCH_STORE
from lvke_mcp.servers.lvke_finance_tables.service import PACKAGE_STORE as TABLE_STORE

PREPARATION_STORE = JSONArtifactStore("report-generation", "preparations", "rprep", "preparations")
BINDING_STORE = JSONArtifactStore("report-generation", "task_bindings", "rjob", "jobs")
REVISION_STORE = JSONArtifactStore("report-generation", "revisions", "rrv", "revisions")

_TASK_TERMINAL = {"done", "completed", "partial", "failed", "cancelled"}
_SECTION_ID_RE = re.compile(r"^sec_[a-z0-9][a-z0-9_-]{2,79}$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _generated_section_id(title: str, order: int, parent_section_id: str = "") -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", title.lower()))[:32] or "section"
    digest = hashlib.sha256(
        f"{parent_section_id}|{order}|{title}".encode("utf-8")
    ).hexdigest()[:10]
    return f"sec_{slug}_{digest}"


def _normalize_outline(value: Any) -> tuple[list[str], list[dict[str, Any]], list[str]]:
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
        section_id = explicit_id or _generated_section_id(title, index, parent_section_id)
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


def _revision_sections(record: dict[str, Any]) -> list[dict[str, Any]]:
    upstream = (record.get("payload") or {}).get("upstream") or {}
    descriptors = upstream.get("sections")
    if isinstance(descriptors, list) and all(isinstance(item, dict) for item in descriptors):
        return [dict(item) for item in descriptors]
    _titles, sections, _errors = _normalize_outline(list(upstream.get("outline") or []))
    return sections


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


def _section_content(content: str, title: str) -> tuple[str, bool]:
    span = _section_span(content, title)
    if span is None:
        return "", False
    return str(span["content"]).strip(), True


def _section_span(content: str, title: str) -> dict[str, Any] | None:
    """Locate one heading-bounded section without touching sibling text."""

    headings = list(_HEADING_RE.finditer(content))
    for index, match in enumerate(headings):
        if match.group(2).strip() != title:
            continue
        level = len(match.group(1))
        end = len(content)
        for following in headings[index + 1:]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        return {
            "start": match.start(),
            "end": end,
            "level": level,
            "heading": match.group(0).strip(),
            "content": content[match.start():end],
        }
    return None


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


def _capture_document_snapshot(
    workspace_id: str,
    *,
    revision_id: str = "",
) -> dict[str, Any]:
    from lvke_mcp.domains.reports import doc_service as doc

    document = dict(doc.read_document(workspace_id, revision_id=revision_id))
    meta = doc.ensure_workspace(workspace_id)
    document["report_type"] = doc.resolve_report_type(meta)
    return document


def _supplied_document_snapshot(
    workspace_id: str,
    value: Any,
) -> dict[str, Any] | None:
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


def _resolve_revision_record(
    workspace_id: str,
    revision_id: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Resolve a public revision, with one-cycle native-id compatibility."""

    try:
        record = REVISION_STORE.get(
            workspace_id,
            revision_id,
        )
    except ValueError:
        record = None
    if record is not None:
        return record, False
    matches = [
        item for item in REVISION_STORE.list(workspace_id)
        if str((item.get("payload") or {}).get("native_revision_id") or "") == revision_id
    ]
    if not matches:
        return None, False
    matches.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return matches[0], True


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


def prepare(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    evidence_ids = list(args.get("evidence_pack_ids") or [])
    research_ids = list(args.get("research_package_ids") or [])
    finance_binding, binding_errors = _normalize_finance_binding(args)
    binding_kind = finance_binding["kind"]
    run_id = finance_binding["run_id"]
    tables_id = finance_binding["package_id"]
    outline, sections, outline_errors = _normalize_outline(args.get("outline") or [])
    blockers: list[str] = [*binding_errors, *outline_errors]
    warnings: list[str] = []
    evidence = []
    for object_id in evidence_ids:
        record = EVIDENCE_STORE.get(
            workspace_id,
            object_id,
        )
        if record is None:
            blockers.append(f"evidence_pack_not_found:{object_id}")
        else:
            evidence.append(record)
    research = []
    for object_id in research_ids:
        record = RESEARCH_STORE.get(
            workspace_id,
            object_id,
        )
        if record is None:
            blockers.append(f"research_package_not_found:{object_id}")
        else:
            research.append(record)
            research_status = str(record.get("status") or "")
            if research_status == "partial":
                warnings.append(f"{object_id}: DR 为 partial，正文必须披露研究限制")
            elif research_status not in {"done", "completed", "ok"}:
                # A package ID alone is not evidence that the corresponding DR
                # task produced usable research artifacts.  In particular, a
                # failed task may only contain a checkpoint and must never be
                # presented as a complete research basis for report drafting.
                blockers.append(
                    f"research_package_not_usable:{object_id}:{research_status or 'unknown'}"
                )
    if not evidence_ids:
        blockers.append("evidence_pack_required")
    if not research_ids:
        blockers.append("research_package_required")
    if binding_kind == "asset_acquisition":
        from lvke_mcp.servers.lvke_asset_acquisition.backend import get_run

        run = (
            get_run(workspace_id, run_id)
            if run_id
            else {}
        )
        run_available = bool(run.get("available") and run.get("status") == "succeeded")
        table_record = (
            get_package_record(
                workspace_id,
                tables_id,
            )
            if tables_id
            else None
        )
        package_required = "acquisition_tables_package_required"
        package_mismatch = "acquisition_tables_run_mismatch"
    else:
        from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

        run = (
            get_workspace_finance_run(
                workspace_id,
                run_id=run_id,
                view="summary",
            )
            if run_id
            else {}
        )
        run_available = bool(run.get("available"))
        table_record = (
            TABLE_STORE.get(workspace_id, tables_id)
            if tables_id
            else None
        )
        package_required = "finance_tables_package_required"
        package_mismatch = "finance_tables_run_mismatch"
    if not run_id or not run_available:
        blockers.append("finance_run_required")
    if table_record is None:
        blockers.append(package_required)
    else:
        table_run = str((table_record.get("payload") or {}).get("run_id") or "")
        if table_run != run_id:
            blockers.append(package_mismatch)
        if binding_kind == "asset_acquisition":
            table_payload = table_record.get("payload") or {}
            integrity = table_payload.get("integrity") or {}
            if integrity.get("status") != "passed":
                blockers.append("acquisition_tables_integrity_failed")
            for field in (
                "spec_hash", "input_hash", "model_version", "evidence_binding_hash",
            ):
                if table_payload.get(field) != run.get(field):
                    blockers.append(f"acquisition_tables_{field}_mismatch")
    basis = {
        "evidence_pack_ids": evidence_ids,
        "research_package_ids": research_ids,
        "run_id": run_id,
        "finance_tables_package_id": tables_id,
        "finance_binding": finance_binding,
        "outline": outline,
        "sections": sections,
        "template_version": str(args.get("template_version") or "default"),
        "upstream_hashes": {
            "evidence": [record.get("basis_hash") for record in evidence],
            "research": [record.get("basis_hash") for record in research],
            "finance_spec": run.get("spec_hash"),
            "finance_tables": table_record.get("basis_hash") if table_record else None,
        },
    }
    status = "blocked" if blockers else ("partial" if warnings else "ok")
    record = PREPARATION_STORE.put(
        workspace_id,
        {**basis, "blockers": blockers, "warnings": warnings},
        producer="lvke-report-generation.report_prepare",
        status=status,
        source_ids=[*evidence_ids, *research_ids, run_id, tables_id],
        basis=basis,
    )
    return {
        "success": not blockers,
        "transport_success": True,
        "business_success": not blockers,
        "completed": not blockers,
        "outcome": "blocked" if blockers else status,
        "status": status,
        "ready": not blockers,
        "report_preparation_id": record["object_id"],
        "basis_hash": record["basis_hash"],
        "generatable_sections": [] if blockers else (sections or [{"section_id": "all", "title": "all", "order": 1, "parent_section_id": None, "depth": 1}]),
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": blockers,
        "next_actions": ["补齐或修复上游不可变对象后重新 report_prepare"] if blockers else ["调用 report_start 创建草稿任务"],
    }


def start(args: dict[str, Any]) -> dict[str, Any]:
    """Create a bound draft workspace; the calling Agent writes the prose.

    MCP is the execution and integrity layer, not a nested LLM client.  The
    previous implementation delegated to the legacy web report generator,
    requiring a second model gateway and producing an opaque second-agent
    workflow.  Keep the public tool for compatibility, but make it a
    deterministic hand-off to ``report_propose → report_diff → report_apply``.
    """
    workspace_id = str(args["workspace_id"])
    preparation_id = str(args["report_preparation_id"])
    prep = PREPARATION_STORE.get(
        workspace_id,
        preparation_id,
    )
    if prep is None:
        return _failure("preparation_not_found", "未找到研报准备记录")
    if (prep.get("payload") or {}).get("blockers"):
        return _failure("preparation_blocked", "上游绑定仍有阻断，拒绝启动研报生成")
    supplied_document = _supplied_document_snapshot(
        workspace_id,
        args.get("document_snapshot"),
    )
    if supplied_document is not None:
        # An explicit immutable snapshot is authoritative for this revision.
        # This prevents a caller-supplied draft from being silently replaced
        # by the native workspace's current text.
        document = _materialize_local_document_snapshot(workspace_id, supplied_document)
    else:
        document = _capture_document_snapshot(workspace_id)
    native_revision = str(document.get("revision_id") or "")
    payload = {
        "native_revision_id": native_revision,
        "report_preparation_id": preparation_id,
        "basis_hash": prep.get("basis_hash"),
        "upstream": (prep.get("payload") or {}),
        "task_status": "agent_drafting",
        "requested_chapters": list(args.get("chapters") or []),
        "document_snapshot": document,
    }
    revision = REVISION_STORE.put(
        workspace_id,
        payload,
        producer="lvke-report-generation.report_start",
        status="agent_drafting",
        source_ids=[preparation_id],
        basis={"preparation_id": preparation_id, "basis_hash": prep.get("basis_hash")},
    )
    return {
        "success": True,
        "status": "agent_action_required",
        "task_id": revision["object_id"],
        "report_revision_id": revision["object_id"],
        "resource_uris": [revision["resource_uri"]],
        "warnings": ["MCP 不调用内置 LLM；正文由当前 Agent 基于上游依据起草"],
        "blockers": [],
        "next_actions": ["调用 report_propose → report_diff → report_apply；完成后用 report_status 固化新修订"],
    }


def status(
    workspace_id: str,
    task_id: str,
) -> dict[str, Any]:
    agent_revision = REVISION_STORE.get(
        workspace_id,
        task_id,
    )
    if agent_revision is not None:
        prior = agent_revision.get("payload") or {}
        document = _capture_document_snapshot(workspace_id)
        native_revision = str(document.get("revision_id") or "")
        payload = {
            **prior,
            "native_revision_id": native_revision,
            "task_status": "agent_drafted",
            "document_snapshot": document,
        }
        revision = REVISION_STORE.put(
            workspace_id,
            payload,
            producer="lvke-report-generation.report_status",
            status="partial",
            source_ids=[str(prior.get("report_preparation_id") or "")],
            basis={"native_revision_id": native_revision, "upstream_basis_hash": prior.get("basis_hash")},
        )
        return {
            "success": True,
            "status": "agent_drafted",
            "task_id": task_id,
            "report_revision_id": revision["object_id"],
            "chapter_progress": [],
            "failed_or_partial_chapters": [],
            "resource_uris": [revision["resource_uri"]],
            "warnings": ["已绑定 Agent 当前草稿修订；仍须 report_validate 后才能导出候选工件"],
            "blockers": [],
            "next_actions": ["调用 report_validate；需要修改时继续 propose→diff→apply"],
        }
    from lvke_mcp.domains.reports.doc_service import load_gen_task as _load_gen_task

    task = _load_gen_task(workspace_id, task_id)
    if task is None:
        return _failure("task_not_found", "未找到研报生成任务")
    task_status = str(task.get("status") or "")
    revision_result = None
    resource_uris: list[str] = []
    if task_status in _TASK_TERMINAL:
        from lvke_mcp.domains.reports.doc_service import load_workspace_snapshot

        snapshot = load_workspace_snapshot(workspace_id)
        native_revision = str(snapshot.get("current_revision_id") or "")
        binding = next(
            (
                row
                for row in BINDING_STORE.list(workspace_id)
                if str((row.get("payload") or {}).get("task_id") or "") == task_id
            ),
            None,
        )
        binding_payload = (binding or {}).get("payload") or {}
        prep = PREPARATION_STORE.get(
            workspace_id,
            str(binding_payload.get("report_preparation_id") or ""),
        )
        document = _capture_document_snapshot(workspace_id)
        payload = {
            "task_id": task_id,
            "native_revision_id": native_revision,
            "report_preparation_id": binding_payload.get("report_preparation_id"),
            "basis_hash": (prep or {}).get("basis_hash"),
            "upstream": (prep or {}).get("payload") or {},
            "task_status": task_status,
            "document_snapshot": document,
        }
        revision = REVISION_STORE.put(
            workspace_id, payload, producer="lvke-report-generation.report_status",
            status="partial" if task_status == "partial" else ("ok" if task_status in {"done", "completed"} else task_status),
            source_ids=[str(binding_payload.get("report_preparation_id") or "")],
            basis={"native_revision_id": native_revision, "upstream_basis_hash": (prep or {}).get("basis_hash")},
        )
        revision_result = revision["object_id"]
        resource_uris.append(revision["resource_uri"])
    chapters = task.get("chapters") or []
    failures = [
        item
        for item in chapters
        if isinstance(item, dict) and str(item.get("state") or "") in {"failed", "partial"}
    ]
    return {
        "success": task_status not in {"failed", "cancelled"},
        "status": task_status or "pending",
        "task_id": task_id,
        "report_revision_id": revision_result,
        "chapter_progress": chapters,
        "failed_or_partial_chapters": failures,
        "resource_uris": resource_uris,
        "warnings": ["任务成功只代表草稿生成；正式工件仍由 readiness 门禁决定"],
        "blockers": [str(task.get("error") or "report_generation_failed")] if task_status == "failed" else [],
        "next_actions": ["终态后调用 report_validate，再导出 draft 或 formal_candidate"],
    }


def validate(
    workspace_id: str,
    revision_id: str,
) -> dict[str, Any]:
    record, native_alias = _resolve_revision_record(
        workspace_id,
        revision_id,
    )
    if record is None:
        return {
            **_failure("revision_not_found", "未找到研报修订"),
            **full_review_requirement(
                workspace_id,
                "report_revision",
                str(revision_id or ""),
            ),
        }
    review_requirement = full_review_requirement(
        workspace_id,
        "report_revision",
        str(record["object_id"]),
    )
    payload = record.get("payload") or {}
    native = str(payload.get("native_revision_id") or "")
    from lvke_mcp.domains.reports import doc_service as doc
    from lvke_mcp.domains.reports import readiness as report_artifacts
    from lvke_mcp.domains.finance import gate as finance_gate

    document = _supplied_document_snapshot(
        workspace_id,
        payload.get("document_snapshot"),
    )
    if document is None:
        document = doc.read_document(workspace_id, revision_id=native)
    if document is None:
        return {
            **_failure(
                "document_snapshot_missing",
                "修订缺少不可变 document_snapshot",
            ),
            **review_requirement,
        }
    content = str(document.get("content") or "")
    upstream = payload.get("upstream") or {}
    report_type = str(document.get("report_type") or "generic_feasibility")
    structure = doc.validate_report_structure(
        content,
        report_type,
        expected_chapters=list(upstream.get("outline") or []),
    )
    run_id = str(upstream.get("run_id") or "")
    narrative = finance_gate.verify_narrative_numbers(
        workspace_id,
        content,
        run_id=run_id,
    )
    binding = finance_gate.assert_publish_finance_binding(
        workspace_id,
        expected_run_id=run_id,
        strict=True,
    )
    readiness = report_artifacts.build_readiness(
        workspace_id,
        persist=False,
        revision_id=native,
        document_snapshot=document,
        expected_chapters=list(upstream.get("outline") or []),
    )
    blockers = []
    bound_preparation_id = str(payload.get("report_preparation_id") or "")
    preparations = sorted(
        PREPARATION_STORE.list(workspace_id),
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    latest_preparation_id = str(preparations[0].get("object_id") or "") if preparations else ""
    if latest_preparation_id and bound_preparation_id != latest_preparation_id:
        blockers.append("upstream_basis_superseded")
    if not structure.get("ok"):
        blockers.append("report_structure_invalid")
    if not narrative.get("ok"):
        blockers.append("finance_narrative_mismatch")
    blockers.extend(
        str(item.get("code") or "finance_binding_blocker")
        for item in (binding.get("blockers") or [])
    )
    blockers.extend(str(item.get("code") or "readiness_blocker") for item in (readiness.get("blockers") or []))
    warnings = [str(item.get("message") or item.get("code") or "") for item in (readiness.get("warnings") or [])]
    if native_alias:
        warnings.append("native_revision_id 输入已弃用；请改用 report_revision_id")
    for research_id in upstream.get("research_package_ids") or []:
        research = RESEARCH_STORE.get(
            workspace_id,
            research_id,
        )
        research_status = str((research or {}).get("status") or "")
        if research_status == "partial":
            warnings.append(f"{research_id}: partial 研究限制必须保留")
        elif research_status not in {"done", "completed", "ok"}:
            blockers.append(
                f"research_package_not_usable:{research_id}:{research_status or 'unknown'}"
            )
    task_status = str((record.get("payload") or {}).get("task_status") or "")
    if task_status in {"failed", "cancelled"}:
        warnings.append("起草任务未完成；当前修订不能视为生成成功")
    blockers = sorted(set(blockers))
    blocked = bool(blockers)
    return {
        "success": not blocked,
        "transport_success": True,
        "business_success": not blocked,
        "completed": not blocked,
        "outcome": "blocked" if blocked else "ok",
        "status": "ok" if not blockers else "blocked",
        "valid": not blockers,
        "report_revision_id": record["object_id"],
        "native_revision_id": native,
        "run_id": run_id,
        "finance_tables_package_id": str(upstream.get("finance_tables_package_id") or ""),
        "basis_hash": str(payload.get("basis_hash") or record.get("basis_hash") or ""),
        "structure": structure,
        "finance_narrative": narrative,
        "finance_binding": binding,
        "readiness": readiness,
        "bound_preparation_id": bound_preparation_id,
        "latest_preparation_id": latest_preparation_id,
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": blockers,
        "next_actions": (
            ["工程校验已通过；仍须完成统一审查、签审与正式释放"]
            if not blockers
            else ["按 blockers 修订或完成既有产品门禁；修订后仍须统一审查"]
        ),
        **review_requirement,
    }


def list_sections(
    workspace_id: str,
    revision_id: str,
) -> dict[str, Any]:
    record, _native_alias = _resolve_revision_record(
        workspace_id, revision_id
    )
    if record is None:
        return _failure("revision_not_found", "未找到研报修订")
    sections = _revision_sections(record)
    return _ok(
        {
            "report_revision_id": record["object_id"],
            "sections": sections,
            "section_count": len(sections),
        },
        [],
    )


def get_section(
    workspace_id: str,
    revision_id: str,
    section_id: str,
) -> dict[str, Any]:
    record, _native_alias = _resolve_revision_record(
        workspace_id, revision_id
    )
    if record is None:
        return _failure("revision_not_found", "未找到研报修订")
    descriptor = next(
        (item for item in _revision_sections(record) if item.get("section_id") == section_id),
        None,
    )
    if descriptor is None:
        return _failure("section_not_found", "未找到该研报章节")
    document = _supplied_document_snapshot(
        workspace_id, (record.get("payload") or {}).get("document_snapshot")
    )
    if document is None:
        native_revision_id = str((record.get("payload") or {}).get("native_revision_id") or "")
        if not native_revision_id:
            return _failure("revision_snapshot_missing", "修订缺少不可变 document_snapshot")
        try:
            document = _capture_document_snapshot(
                workspace_id,
                revision_id=native_revision_id,
            )
        except Exception:  # noqa: BLE001
            return _failure("revision_snapshot_missing", "修订快照不存在或不可读取")
    if document is None:
        return _failure("document_snapshot_missing", "修订缺少不可变 document_snapshot")
    content, found = _section_content(str(document.get("content") or ""), descriptor["title"])
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


def propose_section(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    basis = dict(args.get("basis")) if isinstance(args.get("basis"), dict) else {}
    revision_id = str(args.get("report_revision_id") or "")
    nested_revision_id = str(basis.get("report_revision_id") or "")
    if nested_revision_id and nested_revision_id != revision_id:
        return _failure("proposal_revision_ambiguous", "顶层 revision 与 basis revision 不一致")
    basis["report_revision_id"] = revision_id
    current = get_section(
        workspace_id,
        revision_id,
        str(args.get("section_id") or ""),
    )
    if current.get("status") != "ok":
        return current
    descriptor = current["section"]
    revision, _native_alias = _resolve_revision_record(
        workspace_id,
        revision_id,
    )
    snapshot = ((revision or {}).get("payload") or {}).get("document_snapshot") or {}
    document = str(snapshot.get("content") or "")
    merged = _merge_section_patch(
        document,
        descriptor,
        str(args.get("proposed_content") or ""),
        descriptors=_revision_sections(revision or {}),
    )
    if merged is None:
        return _failure("section_patch_invalid", "目标章节不存在或 proposed_content 为空")
    merged_document, base_section = merged
    basis.update({
        "patch_scope": "section",
        "section_id": str(descriptor["section_id"]),
        "base_section_content_hash": "sha256:" + hashlib.sha256(
            base_section.encode("utf-8")
        ).hexdigest(),
        "merged_document_hash": "sha256:" + hashlib.sha256(
            merged_document.encode("utf-8")
        ).hexdigest(),
    })
    return propose(
        {
            "workspace_id": workspace_id,
            "summary": args["summary"],
            "proposed_content": merged_document,
            "target_sections": [descriptor["title"]],
            "basis": basis,
        }
    )


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
_SENTENCE_RE = re.compile(r"[^\u3002！？!?;\uff1b\n]+[\u3002！？!?;\uff1b]?|\n")


def _quantitative_statements(content: str) -> list[dict[str, Any]]:
    """Return offset-aware numeric claims with locally bound citations."""

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
        # Policy document numbers such as 〔2024〕43号 are identifiers, not
        # evidence of a quantified project statement.
        searchable = re.sub(
            r"〔\d{4}〕\d+号?",
            lambda match: " " * len(match.group(0)),
            paragraph,
        )
        if not _QUANTIFIED_VALUE_RE.search(searchable):
            return
        sentence_spans = [match for match in _SENTENCE_RE.finditer(paragraph) if match.group(0) != "\n"]
        citations = list(_CITATION_RE.finditer(paragraph))
        for value_match in _QUANTIFIED_VALUE_RE.finditer(searchable):
            sentence_index = next(
                (
                    index for index, sentence in enumerate(sentence_spans)
                    if sentence.start() <= value_match.start() < sentence.end()
                ),
                -10,
            )
            bound: list[str] = []
            for citation in citations:
                citation_sentence = next(
                    (
                        index for index, sentence in enumerate(sentence_spans)
                        if sentence.start() <= citation.start() < sentence.end()
                    ),
                    10,
                )
                # A Markdown citation commonly follows the sentence-ending
                # punctuation as a marker-only span: ``结论。[F1]``.  Treat
                # that marker as belonging to the preceding semantic sentence
                # instead of requiring authors to move it before ``。``.
                if (
                    0 < citation_sentence < len(sentence_spans)
                    and _CITATION_RE.fullmatch(
                        sentence_spans[citation_sentence].group(0).strip()
                    )
                ):
                    citation_sentence -= 1
                if abs(citation_sentence - sentence_index) <= 1:
                    bound.append(citation.group(0))
            local_offset = value_match.start()
            global_offset = paragraph_offset + local_offset
            excerpt_match = sentence_spans[sentence_index] if 0 <= sentence_index < len(sentence_spans) else None
            statements.append({
                "line": content.count("\n", 0, global_offset) + 1 if global_offset >= 0 else start_line,
                "offset": global_offset,
                "values": [value_match.group(0).strip()],
                "value": value_match.group("value"),
                "unit": value_match.group("unit"),
                "citation_count": len(bound),
                "citations": bound,
                "excerpt": (excerpt_match.group(0).strip() if excerpt_match else paragraph)[:300],
            })

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


def validate_section(
    workspace_id: str,
    revision_id: str,
    section_id: str,
) -> dict[str, Any]:
    record, _native_alias = _resolve_revision_record(
        workspace_id,
        revision_id,
    )
    if record is None:
        return _failure("revision_not_found", "未找到研报修订")
    fetched = get_section(
        workspace_id, revision_id, section_id
    )
    if fetched.get("status") != "ok":
        return fetched
    content = str(fetched.get("content") or "")
    blockers: list[str] = []
    warnings: list[str] = []
    if not fetched.get("found_in_document"):
        blockers.append("section_heading_missing")
    if not re.sub(r"^#{1,6}\s+.+$", "", content, flags=re.MULTILINE).strip():
        blockers.append("section_content_empty")
    placeholders = re.findall(r"(?:TODO|TBD|待补充|待完善|XXX)", content, flags=re.IGNORECASE)
    if placeholders:
        blockers.append("section_placeholder_present")
    quantitative = _quantitative_statements(content)
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
            "evidence_basis_bound": bool(
                ((record.get("payload") or {}).get("basis_hash"))
            ),
        },
        "formal_delivery_ready": False,
        "warnings": [*warnings, "章节局部校验不能替代整篇 report_validate 与统一交付审查"],
        "blockers": blockers,
        "next_actions": [] if not blockers else ["修订该章节后重新执行局部及整篇校验"],
    }


def export_docx(
    workspace_id: str,
    revision_id: str,
    kind: str,
    mirror_to_project: bool = False,
) -> dict[str, Any]:
    record, native_alias = _resolve_revision_record(
        workspace_id,
        revision_id,
    )
    if record is None:
        return _failure("revision_not_found", "未找到研报修订")
    if kind not in {"draft", "formal_candidate"}:
        return _failure("invalid_artifact_kind", "kind 必须为 draft 或 formal_candidate")
    validation: dict[str, Any] | None = None
    if kind == "formal_candidate":
        validation = validate(
            workspace_id,
            revision_id,
        )
        if not validation.get("valid"):
            return _failure(
                "report_validation_blocked",
                "研报校验或上游 basis 已失效，拒绝生成正式候选工件",
            )
    from lvke_mcp.domains.reports import artifacts

    try:
        created = (
            artifacts.create_deliverable_artifact(
                workspace_id,
            )
            if kind == "formal_candidate"
            else artifacts.create_draft_export(
                workspace_id,
            )
        )
    except Exception as exc:  # noqa: BLE001
        code = str(getattr(exc, "code", "artifact_blocked"))
        return _failure(code, str(getattr(exc, "message", "工件生成被现有交付门禁阻断")))
    artifact_id = str(created.get("artifact_id") or "")
    try:
        from lvke_mcp.domains.reports.docx_fonts import audit_docx_fonts

        docx_path = artifacts._artifact_root(  # noqa: SLF001
            workspace_id,
            artifact_id,
        ) / "report.docx"
        docx_font_audit = audit_docx_fonts(docx_path.read_bytes())
        if docx_font_audit.get("invalid_locale_font_count"):
            return _failure(
                "docx_font_audit_failed",
                "DOCX 字体审计发现 locale 被写入字体名，拒绝交付",
            )
    except Exception:  # noqa: BLE001
        return _failure("docx_font_audit_failed", "DOCX 字体审计失败，拒绝交付")
    base = f"lvke://report-generation/workspaces/{workspace_id}/artifacts/{artifact_id}"
    file_uris = [
        f"{base}/files/{quote(str(item.get('name') or item.get('filename') or ''), safe='')}"
        for item in (created.get("files") or [])
        if item.get("name") or item.get("filename")
    ]
    # External project-folder persistence is an explicit side effect.  The
    # authoritative Resource remains available regardless of this opt-in.
    mirror_paths: list[str] = []
    if mirror_to_project and kind == "draft":
        try:
            from lvke_mcp.domains.reports import artifact_mirror

            src_dir = artifacts._artifact_root(  # noqa: SLF001
                workspace_id,
                artifact_id,
            )
            mirrored = artifact_mirror.mirror_dir(
                workspace_id, src_dir, category=f"report/{artifact_id}"
            )
            if mirrored:
                mirror_paths = [str(mirrored)]
        except Exception:  # noqa: BLE001 - 镜像绝不影响交付
            mirror_paths = []
    warnings = (
        ["正式候选工件尚未完成统一审查与受控发布，不可作为正式交付件"]
        if kind == "formal_candidate"
        else ["draft 工件带内部复核标记，不得作为正式发布件"]
    )
    if native_alias:
        warnings.append("native_revision_id 输入已弃用；请改用 report_revision_id")
    task_status = str((record.get("payload") or {}).get("task_status") or "")
    if kind == "draft" and task_status in {"failed", "cancelled"}:
        warnings.append("起草任务未完成；该草稿可能只含占位或既有正文")
    if validation:
        warnings.extend(str(item) for item in validation.get("warnings") or [])
    if mirror_to_project and kind == "formal_candidate":
        warnings.append("正式候选在统一审查发布前禁止镜像到项目目录")
    return {
        "success": True,
        "status": "ok",
        "artifact_id": artifact_id,
        "artifact_kind": created.get("kind"),
        "docx_font_audit": docx_font_audit,
        "formal_delivery_ready": False,
        "resource_uris": [base, *(file_uris if kind == "draft" else [])],
        "project_mirror_paths": mirror_paths,
        "warnings": warnings,
        "blockers": [],
        "next_actions": ["正式候选必须先完成统一审查、签审和 review_release，再调用 report_release"],
    }


def propose(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.domains.reports.doc_service import create_agent_proposal

    workspace_id = str(args["workspace_id"])
    basis = args.get("basis") if isinstance(args.get("basis"), dict) else {}
    preparation_id = str(basis.get("report_preparation_id") or "")
    basis_hash = str(basis.get("basis_hash") or "")
    revision_id = str(basis.get("report_revision_id") or "")
    try:
        preparation = PREPARATION_STORE.get(
            workspace_id,
            preparation_id,
        )
    except ValueError:
        preparation = None
    if preparation is None:
        return _failure("proposal_basis_not_found", "提案依据不存在或不属于当前工作区")
    expected_hash = str(preparation.get("basis_hash") or "")
    if not basis_hash or not hmac.compare_digest(expected_hash, basis_hash):
        return _failure("proposal_basis_hash_mismatch", "提案依据哈希与已固化准备对象不一致")
    try:
        revision, _native_alias = _resolve_revision_record(
            workspace_id,
            revision_id,
        )
    except ValueError:
        revision = None
    revision_payload = (revision or {}).get("payload") if isinstance(revision, dict) else {}
    if (
        not isinstance(revision_payload, dict)
        or str(revision_payload.get("report_preparation_id") or "") != preparation_id
        or not hmac.compare_digest(str(revision_payload.get("basis_hash") or ""), basis_hash)
    ):
        return _failure("proposal_revision_basis_mismatch", "报告修订未绑定到指定准备对象及 basis_hash")
    preparation_payload = (
        (preparation.get("payload") or {})
        if isinstance(preparation.get("payload"), dict)
        else {}
    )
    verified_basis = {
        "report_preparation_id": preparation_id,
        "basis_hash": basis_hash,
        "report_revision_id": revision_id,
        "outline": list(preparation_payload.get("outline") or []),
    }
    if basis.get("patch_scope") == "section":
        for key in (
            "patch_scope", "section_id", "base_section_content_hash",
            "merged_document_hash",
        ):
            verified_basis[key] = str(basis.get(key) or "")
        if not all(verified_basis[key] for key in (
            "section_id", "base_section_content_hash", "merged_document_hash",
        )):
            return _failure("section_patch_basis_invalid", "单章提案缺少稳定章节绑定")
        proposed_hash = "sha256:" + hashlib.sha256(
            str(args["proposed_content"]).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(verified_basis["merged_document_hash"], proposed_hash):
            return _failure("section_patch_hash_mismatch", "合并后全文哈希与单章提案 basis 不一致")
    requested_sections = list(args.get("target_sections") or [])
    outline = verified_basis["outline"]
    canonical_sections, section_mappings, target_error = _resolve_target_sections(
        preparation_payload,
        requested_sections,
    )
    if outline and target_error:
        return _failure(
            target_error,
            (
                "提案目标章节重复"
                if target_error == "proposal_target_duplicate"
                else "提案目标章节不属于 preparation 固化的自定义 outline"
            ),
        )
    if outline:
        requested_sections = canonical_sections
        verified_basis["target_sections"] = section_mappings
    result = create_agent_proposal(
        workspace_id,
        session_id="mcp-local-agent",
        summary=str(args["summary"]),
        proposed_content=str(args["proposed_content"]),
        target_sections=requested_sections,
        basis=json.dumps(verified_basis, ensure_ascii=False, sort_keys=True),
        expected_outline=outline,
    )
    return _ok({"proposal_id": result.get("id"), "structure_ok": result.get("structure_ok"), "structure_issues": result.get("structure_issues")}, "提案已创建，必须先 diff 后 apply")


def diff(
    workspace_id: str,
    proposal_id: str,
) -> dict[str, Any]:
    from lvke_mcp.domains.reports import doc_service as doc

    try:
        result = doc.diff_agent_proposal(workspace_id, proposal_id=proposal_id)
    except doc.DocServiceError as exc:
        return _failure(exc.code, exc.message)
    return _ok(result, "核对差异后才可 apply")


def apply(
    workspace_id: str,
    proposal_id: str,
    *,
    enforce_structure: bool = True,
) -> dict[str, Any]:
    from lvke_mcp.domains.reports import doc_service as doc

    try:
        proposal = doc._read_proposal(workspace_id, proposal_id)  # noqa: SLF001
    except doc.DocServiceError as exc:
        return _failure(exc.code, exc.message)
    try:
        basis = json.loads(str(proposal.get("basis") or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("proposal_basis_invalid", "提案缺少可验证的结构化 basis")
    if not isinstance(basis, dict):
        return _failure("proposal_basis_invalid", "提案 basis 必须是结构化对象")
    preparation_id = str(basis.get("report_preparation_id") or "")
    revision_id = str(basis.get("report_revision_id") or "")
    basis_hash = str(basis.get("basis_hash") or "")
    try:
        preparation = PREPARATION_STORE.get(
            workspace_id,
            preparation_id,
        )
    except ValueError:
        preparation = None
    revision, native_alias = _resolve_revision_record(
        workspace_id,
        revision_id,
    )
    revision_payload = (revision or {}).get("payload") if isinstance(revision, dict) else {}
    preparation_payload = (preparation or {}).get("payload")
    expected_outline = list(
        (preparation_payload if isinstance(preparation_payload, dict) else {}).get("outline")
        or []
    )
    if (
        preparation is None
        or not hmac.compare_digest(str(preparation.get("basis_hash") or ""), basis_hash)
        or not isinstance(revision_payload, dict)
        or str(revision_payload.get("report_preparation_id") or "") != preparation_id
        or not hmac.compare_digest(str(revision_payload.get("basis_hash") or ""), basis_hash)
        or str(revision_payload.get("native_revision_id") or "")
        != str(proposal.get("base_revision_id") or "")
        or list(proposal.get("expected_outline") or []) != expected_outline
    ):
        return _failure(
            "proposal_basis_stale_or_mismatch",
            "提案的 preparation、basis_hash、revision 或 outline 绑定已失效",
        )

    if basis.get("patch_scope") == "section":
        section_id = str(basis.get("section_id") or "")
        descriptor = next(
            (item for item in _revision_sections(revision or {}) if item.get("section_id") == section_id),
            None,
        )
        snapshot = (revision_payload.get("document_snapshot") or {}) if isinstance(revision_payload, dict) else {}
        span = _section_span(str(snapshot.get("content") or ""), str((descriptor or {}).get("title") or ""))
        current_hash = "sha256:" + hashlib.sha256(
            str((span or {}).get("content") or "").strip().encode("utf-8")
        ).hexdigest()
        if descriptor is None or span is None or not hmac.compare_digest(
            str(basis.get("base_section_content_hash") or ""), current_hash
        ):
            return _failure(
                "section_patch_stale",
                "目标章节内容已变化或章节绑定失效，请基于最新 revision 重新提案",
            )

    try:
        result = doc.apply_agent_proposal(
            workspace_id, proposal_id, readonly=False,
            enforce_structure=enforce_structure,
        )
    except doc.DocServiceError as exc:
        return _failure(exc.code, exc.message)
    native_revision_id = str(result.get("applied_revision_id") or "")
    document_snapshot = _capture_document_snapshot(workspace_id)
    public_revision = REVISION_STORE.put(
        workspace_id,
        {
            **revision_payload,
            "native_revision_id": native_revision_id,
            "parent_report_revision_id": str((revision or {}).get("object_id") or ""),
            "proposal_id": proposal_id,
            "task_status": "agent_drafted",
            "document_snapshot": document_snapshot,
        },
        producer="lvke-report-generation.report_apply",
        status="partial",
        source_ids=[preparation_id, str((revision or {}).get("object_id") or ""), proposal_id],
        basis={
            "native_revision_id": native_revision_id,
            "parent_report_revision_id": str((revision or {}).get("object_id") or ""),
            "upstream_basis_hash": basis_hash,
        },
    )
    warnings = ["已生成新修订；须重新 report_validate"]
    if not enforce_structure:
        warnings.append("结构校验已跳过（enforce_structure=false），不具备正式交付资格")
    if native_alias:
        warnings.append("native_revision_id 输入已弃用；请改用 report_revision_id")
    return _ok(
        {
            **result,
            "report_revision_id": public_revision["object_id"],
            "native_revision_id": native_revision_id,
            "parent_report_revision_id": str((revision or {}).get("object_id") or ""),
            "resource_uris": [public_revision["resource_uri"]],
        },
        warnings,
    )


def readiness(
    workspace_id: str,
    revision_id: str = "",
) -> dict[str, Any]:
    resolved_revision_id = str(revision_id or "").strip()
    if resolved_revision_id:
        record, _native_alias = _resolve_revision_record(
            workspace_id, resolved_revision_id
        )
    else:
        revisions = sorted(
            REVISION_STORE.list(workspace_id),
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )
        record = revisions[0] if revisions else None
    if record is None:
        return _failure("revision_not_found", "未找到指定工作区的研报修订")
    resolved_revision_id = str(record.get("object_id") or "")
    checked = validate(
        workspace_id,
        resolved_revision_id,
    )
    blocked = checked.get("status") != "ok"
    blockers = sorted(set(str(item) for item in (checked.get("blockers") or [])))
    return {
        "success": not blocked,
        "transport_success": True,
        "business_success": not blocked,
        "completed": not blocked,
        "outcome": "blocked" if blocked else "ok",
        "status": "blocked" if blocked else "ok",
        "ready": not blocked,
        "resolved_report_revision_id": resolved_revision_id,
        "run_id": str(checked.get("run_id") or ""),
        "finance_tables_package_id": str(checked.get("finance_tables_package_id") or ""),
        "basis_hash": str(checked.get("basis_hash") or ""),
        "readiness": checked.get("readiness") or {},
        "validation": checked,
        "resource_uris": list(checked.get("resource_uris") or []),
        "warnings": list(checked.get("warnings") or []),
        "blockers": blockers,
        "next_actions": list(checked.get("next_actions") or []),
    }


def release(
    workspace_id: str,
    artifact_id: str,
    note: str = "",
) -> dict[str, Any]:
    from lvke_mcp.domains.reports.artifacts import record_internal_release

    try:
        value = record_internal_release(
            workspace_id,
            artifact_id,
            note=note,
        )
    except Exception as exc:  # noqa: BLE001
        return _failure(str(getattr(exc, "code", "release_blocked")), str(getattr(exc, "message", "发布被阻断")))
    release_record = value.get("release") or {}
    return _ok({
        "artifact_id": artifact_id,
        "release_status": value.get("release_status"),
        "release_record_id": release_record.get("release_id"),
        "quality_note_recorded": True,
    }, "已记录本地交付状态；不构成法律签署")


def list_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    allowed_types = {"preparation", "job", "revision", "artifact", "artifact_file"}
    if resource_type and resource_type not in allowed_types:
        return _failure("resource_type_invalid", "未知 Resource 类型过滤条件")
    entries: dict[str, dict[str, Any]] = {}
    for store, kind in (
        (PREPARATION_STORE, "preparation"),
        (BINDING_STORE, "job"),
        (REVISION_STORE, "revision"),
    ):
        for record in store.list(workspace_id):
            uri = str(record.get("resource_uri") or "")
            if uri:
                entries[uri] = {
                    "uri": uri,
                    "name": str(record.get("object_id") or ""),
                    "resource_type": kind,
                    "mime_type": "application/json",
                    "created_at": record.get("created_at"),
                }

    from lvke_mcp.domains.reports import artifacts

    try:
        artifact_records = artifacts.list_artifacts(
            workspace_id,
            limit=200,
        )
    except Exception:  # noqa: BLE001 - an uninitialized workspace simply has no artifacts
        artifact_records = []
    for record in artifact_records:
        artifact_id = str(record.get("artifact_id") or "")
        base = f"lvke://report-generation/workspaces/{workspace_id}/artifacts/{artifact_id}"
        entries[base] = {
            "uri": base,
            "name": artifact_id,
            "resource_type": "artifact",
            "mime_type": "application/json",
            "created_at": record.get("created_at"),
        }
        try:
            record = artifacts.get_artifact(
                workspace_id,
                artifact_id,
            )
        except Exception:  # noqa: BLE001 - omit artifacts that cannot be refreshed safely
            continue
        externally_readable = (
            record.get("kind") == "draft"
            or (
                record.get("kind") == "formal"
                and record.get("status") == "released"
                and record.get("release_status") == "released"
            )
        )
        for item in (record.get("files") or []) if externally_readable else []:
            if not isinstance(item, dict) or not (item.get("name") or item.get("filename")):
                continue
            filename = str(item.get("name") or item.get("filename") or "")
            uri = f"{base}/files/{quote(filename, safe='')}"
            entries[uri] = {
                "uri": uri,
                "name": filename,
                "resource_type": "artifact_file",
                "mime_type": str(item.get("media_type") or "application/octet-stream"),
                "created_at": record.get("created_at"),
            }

    try:
        pagination = paginate_resource_entries(
            (
                entry for entry in entries.values()
                if not resource_type or entry["resource_type"] == resource_type
            ),
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        code = str(exc)
        message = (
            "资源列表在分页期间发生变化，请从第一页重新列举"
            if code == "resource_list_changed"
            else "Resource 分页游标无效"
        )
        return _failure(code, message)
    page = pagination["resources"]
    return {
        "success": True,
        "status": "ok",
        "resources": page,
        "next_cursor": pagination["next_cursor"],
        "has_more": pagination["has_more"],
        "snapshot_hash": pagination["snapshot_hash"],
        "resource_uris": [entry["uri"] for entry in page],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def resolve_resource(
    uri: str,
    workspace_id: str | None = None,
) -> tuple[str | bytes, str] | None:
    if workspace_id is not None:
        expected = f"lvke://report-generation/workspaces/{workspace_id}/"
        if not str(uri).startswith(expected):
            return None
    for store in (PREPARATION_STORE, BINDING_STORE, REVISION_STORE):
        record = store.resolve_uri(uri)
        if record is not None and (
            workspace_id is None or str(record.get("workspace_id") or "") == workspace_id
        ):
            return json.dumps(record, ensure_ascii=False, indent=2), "application/json"
    prefix = "lvke://report-generation/workspaces/"
    if not uri.startswith(prefix):
        return None
    parts = uri[len(prefix) :].split("/")
    if len(parts) not in {3, 5} or parts[1] != "artifacts":
        return None
    from lvke_mcp.domains.reports import artifacts

    if len(parts) == 3:
        try:
            record = artifacts.get_artifact(
                parts[0],
                parts[2],
            )
        except Exception:  # noqa: BLE001
            return None
        return json.dumps(record, ensure_ascii=False, indent=2, default=str), "application/json"
    if parts[3] != "files":
        return None
    try:
        resolved = artifacts.read_artifact_download(
            parts[0],
            parts[2],
            unquote(parts[4]),
        )
    except Exception:  # noqa: BLE001
        return None
    return resolved["content"], str(resolved.get("media_type") or "application/octet-stream")


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
