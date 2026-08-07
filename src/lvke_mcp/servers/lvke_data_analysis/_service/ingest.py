"""Snapshot/file ingestion, task status and keyword query over ingested docs."""

from __future__ import annotations

import re
from typing import Any

from lvke_mcp.adapters.data_acquisition_repository import SOURCE_STORE
from lvke_mcp.adapters.data_analysis_repository import INGEST_STORE

from .envelope import _missing


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
    from lvke_mcp.adapters import source_files_repository as source

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
        item for item in locators if item.get("low_confidence") is True
    ]
    deterministic_status = str(
        analysis.get("deterministic_status")
        or record.get("deterministic_status")
        or "pending"
    ).lower()
    formal_use_allowed = bool(
        str(record.get("extract_status") or record.get("status") or "") == "succeeded"
        and deterministic_status == "succeeded"
        and (not ocr_used or ocr_status != "failed")
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
        "validation_status": deterministic_status,
        "formal_use_allowed": formal_use_allowed,
        "formal_use_decision": formal_use_decision,
        "ocr_formal_use_decision": str(
            analysis.get("ocr_formal_use_decision")
            or formal_use_decision
        ).lower(),
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
