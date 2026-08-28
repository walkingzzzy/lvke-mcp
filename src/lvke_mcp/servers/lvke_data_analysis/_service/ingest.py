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
        "content_origin": str(payload.get("content_origin") or ""),
        "provider": str(payload.get("provider") or ""),
        "provider_tool": str(payload.get("provider_tool") or ""),
        "evidence_policy": str(payload.get("evidence_policy") or "candidate"),
        "project_fact_certified": False,
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
        "evidence_policy": str(record.get("evidence_policy") or "candidate"),
        "evidence_origin": str(record.get("evidence_origin") or ""),
        "project_fact_certified": bool(record.get("project_fact_certified")),
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
    documents = [item for item in (payload.get("documents") or []) if isinstance(item, dict)]
    stats = _indexed_stats(documents)
    failures = payload.get("failures") or []
    warnings: list[str] = []
    # 「摄入成功」不等于「检索得到」：文档数非零但可检索字符为 0 时必须说明，
    # 否则调用方会把 analysis_query 的空结果误判为"来源里确实没这个词"。
    if documents and stats["indexed_char_count"] <= 0:
        warnings.append(
            "已摄入文档但可检索正文为 0 字符：analysis_query 必然返回空结果，"
            "请检查来源快照 payload.content 或文件解析产物"
        )
    elif stats["empty_content_source_ids"]:
        warnings.append(
            "部分来源无可检索正文："
            + "、".join(stats["empty_content_source_ids"][:5])
        )
    return {
        "success": True,
        "status": str(payload.get("status") or record.get("status") or "partial"),
        "analysis_task_id": task_id,
        "document_count": len(documents),
        **stats,
        "failures": failures,
        "failure_reasons": sorted({str(item.get("reason") or "") for item in failures if isinstance(item, dict)}),
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": [],
        "next_actions": ["任务为 partial 时不要描述为完整解析"],
    }


_CJK_RANGE = "一-鿿"
_CJK_PATTERN = re.compile(f"[{_CJK_RANGE}]+")
_ASCII_PATTERN = re.compile(r"[0-9A-Za-z_][0-9A-Za-z_.%-]*")
# 中文检索停用词：单字虚词做 n-gram 会把命中稀释成噪声。
_CJK_STOPWORDS = frozenset("的了和与及或在是对为以及其中还有个之")


def _cjk_ngrams(run: str, max_n: int = 4) -> list[str]:
    """Deterministically split a CJK run into 1..max_n grams.

    中文没有空格，正则 ``[\\w\\u4e00-\\u9fff]+`` 会把"城市轨道交通客运量与票价"整段
    当成一个 token，于是文档里明明有"客运量""票价"也算 0 命中。这里不引入分词词典
    （不可复现、不可审计），改用确定性 n-gram：所有 1~4 字连续子串都作为候选词，
    命中计数按最长匹配优先加权。
    """

    grams: list[str] = []
    length = len(run)
    for size in range(min(max_n, length), 0, -1):
        for start in range(length - size + 1):
            gram = run[start : start + size]
            if size == 1 and gram in _CJK_STOPWORDS:
                continue
            grams.append(gram)
    return grams


def tokenize_query(query_text: str) -> dict[str, Any]:
    """Build ASCII words + CJK n-grams, keeping the raw phrases for fallback."""

    text = str(query_text or "")
    ascii_terms = [m.group(0).lower() for m in _ASCII_PATTERN.finditer(text)]
    cjk_runs = [m.group(0) for m in _CJK_PATTERN.finditer(text)]
    cjk_terms: list[str] = []
    for run in cjk_runs:
        cjk_terms.extend(_cjk_ngrams(run))
    # 去重但保序，让长 gram 先于短 gram 参与打分。
    seen: set[str] = set()
    terms: list[str] = []
    for term in [*ascii_terms, *cjk_terms]:
        if term and term not in seen:
            seen.add(term)
            terms.append(term)
    return {
        "terms": terms,
        "ascii_terms": ascii_terms,
        "cjk_runs": cjk_runs,
        "phrases": [run for run in cjk_runs if len(run) > 1],
    }


def _score_document(content: str, tokens: dict[str, Any]) -> tuple[float, int, str]:
    """Score by weighted term hits; fall back to shrinking-substring match.

    返回 ``(score, first_hit_offset, match_mode)``。先按 n-gram 加权计数（长词权重高）；
    全落空时做子串回退：把原短语从两端逐步缩短再找，命中即记 ``substring_fallback``，
    绝不静默返回空结果。
    """

    lowered = content.lower()
    score = 0.0
    offsets: list[int] = []
    for term in tokens["terms"]:
        count = lowered.count(term)
        if count:
            # 长词更有判别力：权重取字符长度，避免单字噪声压过完整词。
            score += count * len(term)
            offsets.append(lowered.find(term))
    if score > 0:
        return score, min(offsets, default=0), "tokenized"

    for phrase in tokens["phrases"]:
        lowered_phrase = phrase.lower()
        for size in range(len(lowered_phrase) - 1, 1, -1):
            for start in range(len(lowered_phrase) - size + 1):
                fragment = lowered_phrase[start : start + size]
                position = lowered.find(fragment)
                if position >= 0:
                    return float(size), position, "substring_fallback"
    return 0.0, 0, "none"


def _indexed_stats(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Report how much text is actually searchable, per source type."""

    indexed_char_count = 0
    indexed_cjk_char_count = 0
    empty: list[str] = []
    by_source_type: dict[str, int] = {}
    for doc in documents:
        content = str(doc.get("content") or "")
        indexed_char_count += len(content)
        indexed_cjk_char_count += sum(1 for ch in content if "一" <= ch <= "鿿")
        source_type = str(doc.get("source_type") or "unknown")
        by_source_type[source_type] = by_source_type.get(source_type, 0) + len(content)
        if not content.strip():
            empty.append(str(doc.get("source_id") or ""))
    return {
        "indexed_char_count": indexed_char_count,
        "indexed_cjk_char_count": indexed_cjk_char_count,
        "indexed_document_count": len([d for d in documents if str(d.get("content") or "").strip()]),
        "empty_content_source_ids": empty,
        "indexed_char_count_by_source_type": by_source_type,
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
    tokens = tokenize_query(query_text)
    stats = _indexed_stats(documents)
    hits = []
    for doc in documents:
        content = str(doc.get("content") or "")
        score, first, match_mode = _score_document(content, tokens)
        if score <= 0:
            continue
        start = max(0, first - 160)
        snippet = content[start : start + 640]
        hits.append(
            {
                "source_id": doc.get("source_id"),
                "title": doc.get("title"),
                "score": score,
                "match_mode": match_mode,
                "snippet": snippet,
                "locators": doc.get("locators") or [],
                "formal_use_allowed": doc.get("formal_use_allowed"),
            }
        )
    hits.sort(key=lambda item: (-float(item["score"]), str(item["source_id"])))
    warnings: list[str] = []
    if not hits:
        # 空结果必须能自证原因：是根本没索引到正文，还是索引了但确实没这个词。
        if stats["indexed_char_count"] <= 0:
            warnings.append(
                "未找到匹配证据：已摄入文档的可检索正文为 0 字符，"
                "请先确认来源快照 payload.content 或文件解析产物非空"
            )
        else:
            warnings.append(
                f"未找到匹配证据：已索引 {stats['indexed_char_count']} 字符"
                f"（其中中文 {stats['indexed_cjk_char_count']} 字）中不含该查询词"
            )
    return {
        "success": True,
        "status": "ok",
        "hits": hits[:limit],
        "query_terms": tokens["terms"][:50],
        "query_term_count": len(tokens["terms"]),
        **stats,
        "resource_uris": [],
        "warnings": warnings,
        "blockers": [],
        "next_actions": ["核对 locator 与来源资格；不要把命中自动视为已采信事实"],
    }
