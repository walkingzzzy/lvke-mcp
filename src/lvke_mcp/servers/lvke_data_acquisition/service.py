"""Shared acquisition service used by the MCP adapter and DR internals."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from lvke_mcp.adapters.data_acquisition_repository import (
    COLLECTION_STORE,
    DISCOVERY_STORE,
    SEARCH_STORE,
    SOURCE_STORE,
    URL_AUDIT_STORE,
    VISUAL_CAPTURE_STORE,
    resolve_resource as resolve_repository_resource,
)
from lvke_mcp.runtime.storage import (
    paginate_resource_entries,
    utc_now,
)

_ALLOWED_EXTERNAL_EXTRACT_TOOLS = frozenset({
    ("tavily", "tavily_extract"),
    ("tavily-hikari", "tavily_extract"),
})
_EXTERNAL_RECEIPT_SECRET_ENV = "LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET"
_SEARCH_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}")
_SEARCH_RELEVANCE_THRESHOLD = 0.25
_SEARCH_SLOTS = threading.BoundedSemaphore(4)


def _external_receipt_message(
    *, provider: str, provider_tool: str, url: str, retrieved_at: str, content_hash: str
) -> bytes:
    return json.dumps(
        {
            "content_hash": content_hash,
            "provider": provider,
            "provider_tool": provider_tool,
            "retrieved_at": retrieved_at,
            "url": url,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _search_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in _SEARCH_TOKEN_RE.findall(str(value or "")):
        lowered = token.lower()
        tokens.add(lowered)
        if re.fullmatch(r"[\u4e00-\u9fff]{2,}", token):
            # Chinese runs are not split into single characters.  Stable 2/3
            # character n-grams retain compound matching while preventing a
            # generic character such as “游” from making hotel pages relevant.
            for size in (2, 3):
                tokens.update(token[index:index + size] for index in range(len(token) - size + 1))
    return tokens


def _search_relevance(query: str, title: str, summary: str, url: str = "") -> float:
    query_tokens = _search_tokens(query)
    if not query_tokens:
        return 0.0
    result_tokens = _search_tokens(f"{title} {summary}")
    score = len(query_tokens & result_tokens) / len(query_tokens)
    hostname = _candidate_domain(url)
    if hostname.endswith(".gov.cn") or hostname == "gov.cn":
        score = min(1.0, score + 0.12)
    weak_domains = ("trip.com", "ctrip.com", "qunar.com", "booking.com", "hotel")
    if any(token in hostname for token in weak_domains):
        score *= 0.55
    return round(score, 4)


def _search_timeout_seconds(value: float | None) -> float:
    if value is None:
        # MCP 自有配置：仅认 LVKE_WEB_SEARCH_TIMEOUT_SECONDS（hermes config 的
        # web.search_timeout_seconds 不属 MCP 域，独立化后不再读取）。
        try:
            configured = float(os.getenv("LVKE_WEB_SEARCH_TIMEOUT_SECONDS", ""))
        except ValueError:
            configured = None
        value = configured if configured is not None else 30.0
    try:
        return max(0.05, min(float(value), 120.0))
    except (TypeError, ValueError):
        return 30.0


def _bounded_web_search(query: str, limit: int, timeout_seconds: float) -> tuple[str, str | None, float]:
    """Run a blocking provider behind a bounded, daemonized wall-clock guard."""

    from lvke_mcp.domains.research.providers import tavily as tavily_provider

    started = time.monotonic()
    if not _SEARCH_SLOTS.acquire(blocking=False):
        return "busy", None, (time.monotonic() - started) * 1000
    outcome: queue.Queue[tuple[str, str | None]] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            result = asyncio.run(tavily_provider.tavily_search(query, limit))
            outcome.put(("ok", json.dumps(result, ensure_ascii=False)))
        except Exception:  # noqa: BLE001
            outcome.put(("failed", None))
        finally:
            _SEARCH_SLOTS.release()

    worker = threading.Thread(
        target=invoke,
        name="lvke-web-search",
        daemon=True,
    )
    worker.start()
    worker.join(timeout_seconds)
    elapsed_ms = (time.monotonic() - started) * 1000
    if worker.is_alive():
        return "timeout", None, elapsed_ms
    try:
        status, payload = outcome.get_nowait()
    except queue.Empty:
        return "failed", None, elapsed_ms
    return status, payload, elapsed_ms


def search(
    workspace_id: str,
    query: str,
    limit: int = 5,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    try:
        from lvke_mcp.domains.research.providers import tavily as tavily_provider

        provider_requested = (
            "tavily-hikari" if tavily_provider.server_command() else "none"
        )
    except Exception:  # noqa: BLE001
        provider_requested = "auto"
    effective_timeout = _search_timeout_seconds(timeout_seconds)
    provider_status, provider_payload, provider_ms = _bounded_web_search(
        query, limit, effective_timeout
    )
    timing = {
        "provider_ms": round(provider_ms, 2),
        "total_ms": round(provider_ms, 2),
        "timeout_ms": round(effective_timeout * 1000, 2),
        "attempts": 1,
    }
    if provider_status in {"timeout", "busy"}:
        code = "web_search_timeout" if provider_status == "timeout" else "web_search_circuit_busy"
        return {
            "success": False,
            "business_success": False,
            "system_success": True,
            "transport_success": True,
            "status": "upstream_failure",
            "code": code,
            "message": (
                "搜索 provider 超过总时限"
                if provider_status == "timeout"
                else "搜索并发已达上限"
            ),
            "providerRequested": provider_requested,
            "providerUsed": "unknown",
            "fallbackReason": None,
            "timing": timing,
            "warnings": [],
            "blockers": [code],
            "next_actions": ["稍后重试，或缩小查询范围并检查 provider 状态"],
            "resource_uris": [],
            "provider": "search",
            "retryable": True,
            "retry_after": 5,
            "trace_id": hashlib.sha256(f"search:{time.time_ns()}".encode()).hexdigest()[:24],
        }
    try:
        raw = json.loads(provider_payload or "")
    except (TypeError, json.JSONDecodeError):
        # Provider failures are an environment condition.  Do not expose a
        # provider traceback, path, request body, or configuration detail.
        raw = {"success": False, "error": "搜索服务不可用"}
    if not isinstance(raw, dict) or raw.get("success") is False:
        return {
            "success": False,
            "business_success": False,
            "system_success": True,
            "transport_success": True,
            "status": "upstream_failure",
            # Provider error text can contain URLs, account identifiers or
            # configuration detail.  Keep the MCP response operational but
            # deliberately generic; diagnostics remain on stderr.
            "message": "搜索服务不可用或响应无效",
            "warnings": [],
            "blockers": ["web_search_unavailable"],
            "next_actions": ["稍后重试 Tavily，或缩小查询范围"],
            "resource_uris": [],
            "providerRequested": provider_requested,
            "providerUsed": "unknown",
            "fallbackReason": None,
            "timing": timing,
            "provider": "search",
            "retryable": True,
            "retry_after": 5,
            "trace_id": hashlib.sha256(f"search:{time.time_ns()}".encode()).hexdigest()[:24],
        }
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    web = data.get("web", [])
    results = []
    for index, item in enumerate(web if isinstance(web, list) else []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        summary = str(item.get("description") or item.get("summary") or "")
        results.append({
            "title": title,
            "url": str(item.get("url") or ""),
            "summary": summary,
            "provider": str(item.get("provider") or "configured-web-provider"),
            "rank": int(item.get("position") or index + 1),
            "relevance": _search_relevance(query, title, summary, str(item.get("url") or "")),
        })
    provider_used = str(
        raw.get("provider")
        or data.get("provider")
        or next((item["provider"] for item in results if item.get("provider")), "unknown")
    )
    fallback_reason = None
    if (
        provider_requested not in {"", "auto"}
        and provider_used not in {"", "unknown", "configured-web-provider", provider_requested}
    ):
        fallback_reason = "configured_provider_unavailable_or_replaced"
    relevant_count = sum(
        item["relevance"] >= _SEARCH_RELEVANCE_THRESHOLD for item in results
    )
    warnings: list[str] = []
    if fallback_reason:
        warnings.append("search_provider_fallback")
    if not results:
        status = "empty"
        warnings.append("search_results_empty")
    elif relevant_count == 0:
        status = "partial"
        warnings.append("search_results_low_relevance")
    elif relevant_count < len(results):
        status = "partial"
        warnings.append("search_results_include_low_relevance")
    else:
        status = "partial" if fallback_reason else "ok"
    record = SEARCH_STORE.put(
        workspace_id,
        {
            "query": query,
            "limit": limit,
            "results": results,
            "providerRequested": provider_requested,
            "providerUsed": provider_used,
            "fallbackReason": fallback_reason,
            "relevance_threshold": _SEARCH_RELEVANCE_THRESHOLD,
            "relevant_count": relevant_count,
            "timing": timing,
        },
        producer="lvke-data-acquisition.data_search",
        status=status,
    )
    business_success = status == "ok"
    return {
        "success": business_success,
        "business_success": business_success,
        "system_success": True,
        "transport_success": True,
        "status": status,
        "search_set_id": record["object_id"],
        "results": results,
        "providerRequested": provider_requested,
        "providerUsed": provider_used,
        "fallbackReason": fallback_reason,
        "relevance_threshold": _SEARCH_RELEVANCE_THRESHOLD,
        "relevant_count": relevant_count,
        "timing": timing,
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": [],
        "next_actions": (
            ["调整查询词或 provider 后重试；不要把当前搜索摘要作为证据"]
            if status in {"empty", "partial"} and relevant_count == 0
            else ["选择高相关且可信的 URL 后调用 data_fetch 固化原始来源快照"]
        ),
    }


def _canonical_discovery_url(value: str) -> str | None:
    """Return a stable HTTP(S) URL for deduplication without widening fetch scope.

    Discovery deliberately only normalizes presentation-level differences.  It
    does not remove query parameters: doing so could turn a selected public
    record into a different source.  The security decision remains in
    :func:`fetch`, where DNS and metadata/private-network checks are enforced.
    """

    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if scheme not in {"http", "https"} or not hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = hostname
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _candidate_domain(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _matches_domain_rule(hostname: str, rule: str) -> bool:
    normalized = str(rule or "").lower().strip().lstrip(".").rstrip(".")
    return bool(normalized) and (hostname == normalized or hostname.endswith(f".{normalized}"))


# Feasibility-study angles used to expand a base topic into several distinct
# search queries.  A single free web provider (ddgs) caps at ~5 results per
# call, so reaching 30-50 candidates requires issuing multiple angled queries
# and aggregating the de-duplicated union.  These are deterministic suffixes,
# not LLM-generated: the expansion is reproducible and auditable.
_FEASIBILITY_ANGLES: tuple[str, ...] = (
    "",  # the bare topic itself
    "政策 文件",
    "市场 需求 规模",
    "行业 现状 发展",
    "技术 方案 参数",
    "投资 成本 造价",
    "运营 收入 电价 价格",
    "竞品 案例 项目",
    "区域 分布 布局",
    "风险 问题 挑战",
)


def _expand_queries(base_queries: list[str], target_count: int, limit_per_query: int) -> list[str]:
    """Grow base queries into enough angled variants to approach ``target_count``.

    Each base query is combined with feasibility-study angles (policy, market,
    tech, cost, competitors, region…) until there are enough distinct queries
    that, at ``limit_per_query`` results each, could plausibly reach the target.
    De-duplicated while preserving order so the search plan stays stable.
    """

    bases = [str(q).strip() for q in base_queries if str(q).strip()]
    if not bases:
        return []
    per = max(1, int(limit_per_query))
    needed_queries = max(len(bases), -(-max(1, target_count) // per))  # ceil division
    expanded: list[str] = []
    seen: set[str] = set()
    # Round-robin over angles so every base topic gets breadth before depth.
    for angle in _FEASIBILITY_ANGLES:
        for base in bases:
            query = base if not angle else f"{base} {angle}"
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            expanded.append(query)
            if len(expanded) >= needed_queries:
                return expanded
    return expanded


def discover(
    workspace_id: str,
    queries: list[str],
    *,
    limit_per_query: int = 5,
    domain_allowlist: list[str] | None = None,
    domain_denylist: list[str] | None = None,
    target_count: int | None = None,
    auto_expand: bool = False,
    total_timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Create a bounded, immutable set of public-source candidates.

    Search results are discovery metadata only.  A candidate is never evidence
    until an explicit ``data_collect`` selection safely becomes a snapshot.

    ``target_count`` (with ``auto_expand``) grows the caller's base queries into
    several feasibility angles and aggregates the de-duplicated union, so a
    single-provider cap of ~5 results per call can still reach 30-50 candidates.
    Falling short is reported honestly as ``partial`` — never padded.
    """

    allowed = [str(item).lower().strip() for item in (domain_allowlist or []) if str(item).strip()]
    denied = [str(item).lower().strip() for item in (domain_denylist or []) if str(item).strip()]
    base_queries = [str(item) for item in queries]
    effective_queries = base_queries
    if auto_expand and target_count:
        effective_queries = _expand_queries(base_queries, int(target_count), int(limit_per_query))
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    query_results: list[dict[str, Any]] = []
    search_warnings: list[str] = []
    seen_urls: set[str] = set()
    started = time.monotonic()
    deadline = started + max(1.0, min(float(total_timeout_seconds), 300.0))
    budget_exhausted = False

    for query_index, query in enumerate(effective_queries, 1):
        # Stop early once the target is met so we do not spend provider calls
        # (and rate budget) past what the caller asked for.
        if target_count and len(candidates) >= int(target_count):
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            budget_exhausted = True
            break
        result = search(
            workspace_id,
            str(query),
            limit_per_query,
            timeout_seconds=min(remaining, _search_timeout_seconds(None)),
        )
        query_results.append(
            {
                "query": str(query),
                "status": result.get("status"),
                "search_set_id": result.get("search_set_id"),
                "providerRequested": result.get("providerRequested"),
                "providerUsed": result.get("providerUsed"),
                "fallbackReason": result.get("fallbackReason"),
                "timing": result.get("timing"),
                "warnings": list(result.get("warnings") or []),
            }
        )
        search_warnings.extend(str(item) for item in (result.get("warnings") or []))
        if result.get("code") == "web_search_timeout" and time.monotonic() >= deadline:
            budget_exhausted = True
        for item in result.get("results") or []:
            if not isinstance(item, dict):
                continue
            relevance = item.get("relevance")
            if isinstance(relevance, (int, float)) and relevance < _SEARCH_RELEVANCE_THRESHOLD:
                skipped.append({
                    "url": str(item.get("url") or ""),
                    "reason": "low_relevance",
                })
                continue
            normalized_url = _canonical_discovery_url(str(item.get("url") or ""))
            if normalized_url is None:
                skipped.append({"url": str(item.get("url") or ""), "reason": "unsupported_or_invalid_url"})
                continue
            hostname = _candidate_domain(normalized_url)
            if denied and any(_matches_domain_rule(hostname, rule) for rule in denied):
                skipped.append({"url": normalized_url, "reason": "domain_denied"})
                continue
            if allowed and not any(_matches_domain_rule(hostname, rule) for rule in allowed):
                skipped.append({"url": normalized_url, "reason": "domain_not_allowlisted"})
                continue
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            candidates.append(
                {
                    "candidate_id": f"cand_{len(candidates) + 1:03d}",
                    "url": normalized_url,
                    "domain": hostname,
                    "title": str(item.get("title") or ""),
                    "summary": str(item.get("summary") or ""),
                    "provider": str(item.get("provider") or "configured-web-provider"),
                    "rank": int(item.get("rank") or len(candidates) + 1),
                    "relevance": float(relevance) if isinstance(relevance, (int, float)) else 1.0,
                    "query": str(query),
                    "query_index": query_index,
                }
            )

    actual = len(candidates)
    met_target = not target_count or actual >= target_count
    degraded_search = any(item.get("status") != "ok" for item in query_results)
    status = (
        "ok"
        if met_target and candidates and not budget_exhausted and not degraded_search
        else (
            "partial"
            if candidates
            else (
                "upstream_failure"
                if query_results and all(
                    item.get("status") == "upstream_failure" for item in query_results
                )
                else "empty"
            )
        )
    )
    timing = {
        "total_ms": round((time.monotonic() - started) * 1000, 2),
        "timeout_ms": round(max(1.0, min(float(total_timeout_seconds), 300.0)) * 1000, 2),
        "queries_attempted": len(query_results),
        "queries_planned": len(effective_queries),
    }
    payload = {
        "queries": [str(item) for item in queries],
        "effective_queries": effective_queries if auto_expand else None,
        "limit_per_query": limit_per_query,
        "target_count": target_count if target_count else None,
        "actual_count": actual,
        "domain_allowlist": allowed,
        "domain_denylist": denied,
        "query_results": query_results,
        "timing": timing,
        "time_budget_exhausted": budget_exhausted,
        "search_warnings": sorted(set(search_warnings)),
        "candidates": candidates,
        "skipped": skipped,
        "discovery_boundary": "搜索结果仅是待选公开来源；必须经 data_collect 安全抓取后才有 source_snapshot_id。",
    }
    record = DISCOVERY_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-acquisition.data_discover",
        status=status,
    )
    warnings = sorted(set(search_warnings))
    if skipped:
        warnings.append("部分搜索结果因 URL 或域名筛选未进入候选集合")
    if not candidates:
        warnings.append("没有得到可采集的公开来源候选")
    if target_count and actual < target_count:
        warnings.append(f"候选数 {actual} 未达目标 {target_count}；可增加查询角度或启用更多 provider")
    if budget_exhausted:
        warnings.append("搜索总时限已用尽，未继续发起剩余查询")
    return {
        "success": bool(candidates),
        "business_success": bool(candidates) and status == "ok",
        "system_success": True,
        "transport_success": True,
        "status": status,
        "discovery_set_id": record["object_id"],
        "candidates": candidates,
        "actual_count": actual,
        "target_count": target_count if target_count else None,
        "skipped": skipped,
        "timing": timing,
        "time_budget_exhausted": budget_exhausted,
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": ([] if candidates else ["no_discovery_candidates"]),
        "next_actions": (
            ["选择 candidate_id 后调用 data_collect；候选摘要不得直接用作证据"]
            if candidates
            else ["调整查询词、域名筛选或检查 data_provider_status"]
        ),
    }


async def collect(
    workspace_id: str,
    discovery_set_id: str,
    selected_candidate_ids: list[str],
    *,
    content_mode: str = "readable",
) -> dict[str, Any]:
    """Safely fetch only selected candidates from an immutable discovery set."""

    record = DISCOVERY_STORE.get(
        workspace_id,
        discovery_set_id,
    )
    if record is None:
        return _collection_failure("discovery_set_not_found", "未找到数据发现集合")
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    candidates = [item for item in (payload.get("candidates") or []) if isinstance(item, dict)]
    by_id = {str(item.get("candidate_id")): item for item in candidates}
    requested_ids = list(dict.fromkeys(str(item) for item in selected_candidate_ids))
    unknown_ids = [item for item in requested_ids if item not in by_id]
    selected = [by_id[item] for item in requested_ids if item in by_id]
    if not selected:
        return _collection_failure(
            "no_valid_selected_candidates",
            "未选择发现集合中的有效候选来源",
            blockers=["unknown_candidate_id"] if unknown_ids else ["no_selected_candidates"],
        )

    # ``fetch`` is the single outbound path: private/DNS/metadata/secret URL
    # protection cannot be skipped by the batch convenience API.
    fetch_result = await fetch(
        workspace_id,
        [str(item["url"]) for item in selected],
        content_mode=content_mode,
    )
    snapshot_by_url = {
        str(item.get("url")): item
        for item in fetch_result.get("snapshots") or []
        if isinstance(item, dict)
    }
    collected = [
        {
            "candidate_id": str(candidate["candidate_id"]),
            "url": str(candidate["url"]),
            "title": str(candidate.get("title") or ""),
            "snapshot": snapshot_by_url.get(str(candidate["url"]), {"status": "failed", "message": "未返回抓取结果"}),
        }
        for candidate in selected
    ]
    successful_ids = [
        str(item["snapshot"].get("source_snapshot_id"))
        for item in collected
        if isinstance(item.get("snapshot"), dict) and item["snapshot"].get("status") == "ok"
    ]
    status = str(fetch_result.get("status") or "failed")
    if unknown_ids and status == "ok":
        status = "partial"
    collection_payload = {
        "discovery_set_id": discovery_set_id,
        "selected_candidate_ids": requested_ids,
        "unknown_candidate_ids": unknown_ids,
        "collected": collected,
        "source_snapshot_ids": successful_ids,
        "collection_boundary": "仅已选 candidate_id 可被抓取；所有 URL 仍须通过 data_fetch 的安全校验。",
    }
    collection_record = COLLECTION_STORE.put(
        workspace_id,
        collection_payload,
        producer="lvke-data-acquisition.data_collect",
        status=status,
        source_ids=successful_ids,
        basis={
            "discovery_set_id": discovery_set_id,
            "selected_candidate_ids": requested_ids,
            "source_snapshot_ids": successful_ids,
        },
    )
    warnings = list(fetch_result.get("warnings") or [])
    if unknown_ids:
        warnings.append("部分 candidate_id 不属于该 discovery_set，未被抓取")
    return {
        "success": bool(successful_ids),
        "status": status,
        "source_collection_id": collection_record["object_id"],
        "source_snapshot_ids": successful_ids,
        "collected": collected,
        "unknown_candidate_ids": unknown_ids,
        "resource_uris": [collection_record["resource_uri"], *list(fetch_result.get("resource_uris") or [])],
        "warnings": warnings,
        "blockers": [*list(fetch_result.get("blockers") or []), *( ["unknown_candidate_id"] if unknown_ids else [])],
        "next_actions": (
            ["将成功的 source_snapshot_id 交给 analysis_ingest"]
            if successful_ids
            else list(fetch_result.get("next_actions") or ["检查所选 URL 后重试"])
        ),
    }


def _collection_failure(
    code: str,
    message: str,
    *,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "status": "failed",
        "code": code,
        "message": message,
        "resource_uris": [],
        "warnings": [],
        "blockers": blockers or [code],
        "next_actions": [],
    }


def _secret_block_reason(url: str) -> str | None:
    """复用 agent.redact 的密钥前缀模式逐 URL 检查，命中返回诚实拦截原因。

    与 web_extract_tool 主路径的拦截口径保持一致：对原始 URL、百分号解码后
    的 URL 以及规范化后的 URL 各查一遍，防止 %73k- 之类的编码绕过。返回的
    原因只说明「疑似携带密钥」，不回显命中的密钥值。
    """
    from urllib.parse import unquote

    from lvke_mcp.domains.research.url_safety import _PREFIX_RE, normalize_url_for_request

    text = str(url or "")
    normalized = normalize_url_for_request(text)
    for candidate in (text, unquote(text), normalized, unquote(normalized)):
        if _PREFIX_RE.search(candidate):
            return "已拦截：URL 疑似携带 API 密钥或令牌，禁止在 URL 中传递密钥"
    return None


def _external_snapshot_url_block_reason(url: str) -> str | None:
    """Validate provenance URLs without performing a second network request.

    External MCP extraction is useful precisely when the local process cannot
    safely resolve/fetch a public URL.  Import therefore does not perform DNS
    resolution, but it still rejects credentials, metadata endpoints and
    literal private/special-use IP addresses.  The original URL is provenance,
    never an instruction to fetch from the local network.
    """

    secret_reason = _secret_block_reason(url)
    if secret_reason is not None:
        return secret_reason
    try:
        parsed = urlsplit(str(url or "").strip())
        hostname = (parsed.hostname or "").rstrip(".")
        port = parsed.port
    except ValueError:
        return "已拦截：来源 URL 无效"
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return "已拦截：来源 URL 必须是有效的 HTTP(S) 公网地址"
    if parsed.username or parsed.password:
        return "已拦截：来源 URL 不得嵌入凭据"
    if port is not None and not (1 <= port <= 65535):
        return "已拦截：来源 URL 端口无效"
    lowered = hostname.lower()
    if lowered in {"localhost", "metadata.google.internal"} or lowered.endswith(".localhost"):
        return "已拦截：来源 URL 指向内部或云 metadata 地址"
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return None
    if not address.is_global:
        return "已拦截：来源 URL 指向非公网 IP 地址"
    return None


def import_external_snapshot(
    workspace_id: str,
    *,
    url: str,
    title: str,
    content: str,
    provider: str,
    provider_tool: str,
    retrieved_at: str,
    content_kind: str,
    mime_type: str = "text/markdown",
    extraction_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist externally extracted source text as an immutable Lvke snapshot.

    This is an adapter boundary for high-quality remote extraction MCPs such as
    Tavily Hikari.  Only source text/raw content may cross it.  Search snippets,
    generated answers and research synthesis are deliberately invalid kinds so
    they cannot silently become evidence.
    """

    reason = _external_snapshot_url_block_reason(url)
    if reason is not None:
        return {
            "success": False,
            "status": "blocked",
            "message": reason,
            "resource_uris": [],
            "warnings": [],
            "blockers": ["external_snapshot_url_blocked"],
            "next_actions": ["仅传入不含密钥的 HTTP(S) 公网来源 URL"],
        }
    if content_kind not in {"extracted_full_text", "raw_content"}:
        return {
            "success": False,
            "status": "blocked",
            "message": "外部内容必须是选定 URL 的提取正文或原始内容",
            "resource_uris": [],
            "warnings": [],
            "blockers": ["external_content_kind_not_evidence_source"],
            "next_actions": ["调用外部 extract 工具取得选定 URL 正文；不得传入搜索摘要或生成答案"],
        }
    # Existing adapters have emitted both ``tavily_hikari`` and
    # ``tavily-hikari``.  Normalize the separator before applying the strict
    # extract-tool allowlist; this does not broaden the provider set.
    provider_key = str(provider or "").strip().lower().replace("_", "-")
    tool_key = str(provider_tool or "").strip().lower()
    if (provider_key, tool_key) not in _ALLOWED_EXTERNAL_EXTRACT_TOOLS:
        return {
            "success": False,
            "status": "blocked",
            "code": "external_provider_tool_not_extract",
            "message": "外部内容必须来自受支持的正文 extract 工具，搜索或 research 输出不能作为正文快照",
            "resource_uris": [],
            "warnings": [],
            "blockers": ["external_provider_tool_not_extract"],
            "next_actions": ["对选定 URL 调用受支持的 extract 工具后重试"],
        }
    normalized_content = str(content or "").strip()
    if not normalized_content:
        return {
            "success": False,
            "status": "failed",
            "message": "外部提取内容为空",
            "resource_uris": [],
            "warnings": [],
            "blockers": ["external_content_empty"],
            "next_actions": ["检查外部 extract 结果后重试"],
        }
    receipt = extraction_receipt if isinstance(extraction_receipt, dict) else {}
    computed_hash = "sha256:" + hashlib.sha256(
        normalized_content.encode("utf-8")
    ).hexdigest()
    receipt_consistent = bool(receipt) and all((
        str(receipt.get("provider") or "").strip().lower().replace("_", "-")
        == provider_key,
        str(receipt.get("provider_tool") or "").strip().lower() == tool_key,
        str(receipt.get("url") or "").strip() == str(url).strip(),
        str(receipt.get("retrieved_at") or "").strip() == str(retrieved_at).strip(),
        str(receipt.get("content_hash") or "") == computed_hash,
    ))
    if receipt and not receipt_consistent:
        return {
            "success": False,
            "status": "blocked",
            "code": "external_extraction_receipt_mismatch",
            "message": "外部提取回执与 provider、URL 或正文哈希不一致",
            "resource_uris": [],
            "warnings": [],
            "blockers": ["external_extraction_receipt_mismatch"],
            "next_actions": ["重新提取正文并传入与本次内容一致的回执"],
        }
    receipt_verified = False
    if receipt:
        secret = os.getenv(_EXTERNAL_RECEIPT_SECRET_ENV, "").encode("utf-8")
        supplied_signature = str(receipt.get("signature") or "").strip().lower()
        if secret and supplied_signature:
            expected_signature = "hmac-sha256:" + hmac.new(
                secret,
                _external_receipt_message(
                    provider=provider_key,
                    provider_tool=tool_key,
                    url=str(url).strip(),
                    retrieved_at=str(retrieved_at).strip(),
                    content_hash=computed_hash,
                ),
                hashlib.sha256,
            ).hexdigest()
            receipt_verified = hmac.compare_digest(supplied_signature, expected_signature)
        if not receipt_verified:
            return {
                "success": False,
                "status": "blocked",
                "code": "external_extraction_receipt_unverifiable",
                "message": "外部提取回执缺少可验证签名、服务端验证密钥未配置或签名无效",
                "resource_uris": [],
                "warnings": [],
                "blockers": ["external_extraction_receipt_unverifiable"],
                "next_actions": ["由受信 extract adapter 使用服务端共享密钥签发完整回执后重试"],
            }
    payload = {
        "url": str(url).strip(),
        "title": str(title or "").strip(),
        "content": normalized_content,
        "content_mode": "external_extract",
        "content_kind": content_kind,
        "content_origin": "external_mcp_extract",
        "mime_type": str(mime_type or "text/markdown").strip().lower(),
        "mime_type_source": "external_provider_declared",
        "provider": str(provider).strip(),
        "provider_tool": str(provider_tool).strip(),
        "retrieved_at": str(retrieved_at).strip(),
        "extraction_receipt_verified": receipt_verified,
        "external_content_hash": computed_hash,
        "formal_use_allowed": receipt_verified,
        "evidence_boundary": "外部 MCP 提取正文已固化为候选来源快照；未经整理与核验不是已采信事实或财务输入。",
    }
    record = SOURCE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-acquisition.data_import_external_snapshot",
        basis={
            "url": payload["url"],
            "content": payload["content"],
            "provider": payload["provider"],
            "provider_tool": payload["provider_tool"],
            "retrieved_at": payload["retrieved_at"],
        },
    )
    return {
        "success": True,
        "status": "ok",
        "source_snapshot_id": record["object_id"],
        "content_hash": record["content_hash"],
        "source_url": payload["url"],
        "content_origin": payload["content_origin"],
        "provider": payload["provider"],
        "provider_tool": payload["provider_tool"],
        "retrieved_at": payload["retrieved_at"],
        "external_content_hash": computed_hash,
        "extraction_receipt_verified": receipt_verified,
        "formal_use_allowed": receipt_verified,
        "resource_uris": [record["resource_uri"]],
        "warnings": ([
            "该快照只有调用方声明的 extract 来源，缺少可验证回执；仅可作为 unverified_external_text 候选，禁止正式使用"
        ] if not receipt_verified else [
            "外部提取回执已核对；仍需进入 analysis_ingest 并核对 locator/来源资格"
        ]),
        "blockers": [],
        "next_actions": ["将 source_snapshot_id 交给 analysis_ingest；不得将外部 answer/摘要写入 FinanceSpec"],
    }


async def _network_safety_decision(url: str) -> tuple[str | None, dict[str, Any]]:
    """复用域内 url_safety 的私网与云 metadata 判定，命中返回诚实拦截原因。

    云 metadata 端点先走 ``is_always_blocked_url`` 无条件拒绝，不受
    ``security.allow_private_urls`` 配置影响；其余私网/内网地址按
    ``async_url_safety_decision`` 的统一口径处理（DNS 失败等异常 fail-closed）。
    """
    from lvke_mcp.domains.research.url_safety import async_url_safety_decision

    decision = await async_url_safety_decision(str(url or ""))
    if decision.get("allowed"):
        return None, decision
    classifications = {
        str(item.get("classification") or "")
        for item in decision.get("addresses", [])
        if isinstance(item, dict)
    }
    if "cloud_metadata" in classifications or str(decision.get("code") or "").startswith(
        "cloud_metadata"
    ):
        return "已拦截：URL 指向云 metadata 端点，无条件拒绝", decision
    return "已拦截：URL 指向私网/内部网络地址或无法安全解析", decision


async def _trusted_tavily_extract(
    urls: list[str],
    output_format: str,
) -> list[dict[str, Any]]:
    """Use the server-owned Tavily adapter and sign a receipt for each body."""

    secret = os.getenv(_EXTERNAL_RECEIPT_SECRET_ENV, "").encode("utf-8")
    if not secret:
        return []
    try:
        from lvke_mcp.domains.research.providers import tavily as tavily_provider

        if not tavily_provider.server_command():
            return []
        extracted = await tavily_provider.tavily_extract(urls, output_format)
        if not extracted:
            return []
    except Exception:  # noqa: BLE001
        return []
    results: list[dict[str, Any]] = []
    for item in extracted if isinstance(extracted, list) else []:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized["provider"] = "tavily"
        content = str(item.get("content") or item.get("raw_content") or "")
        if content:
            # Tavily's native extract response calls the body ``raw_content``;
            # normalize it before the common fetch path validates the payload.
            normalized["content"] = content
            retrieved_at = utc_now()
            content_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
            receipt = {
                "provider": "tavily",
                "provider_tool": "tavily_extract",
                "url": str(item.get("url") or ""),
                "retrieved_at": retrieved_at,
                "content_hash": content_hash,
            }
            receipt["signature"] = "hmac-sha256:" + hmac.new(
                secret,
                _external_receipt_message(**receipt),
                hashlib.sha256,
            ).hexdigest()
            normalized["extraction_receipt"] = receipt
            normalized["extraction_receipt_verified"] = True
        results.append(normalized)
    return results


async def fetch(
    workspace_id: str,
    urls: list[str],
    *,
    content_mode: str = "readable",
    extraction_provider: str = "auto",
) -> dict[str, Any]:
    extraction_provider = str(extraction_provider or "auto").strip().lower()
    if extraction_provider not in {"auto", "tavily", "direct_http"}:
        return {
            "success": False,
            "business_success": False,
            "system_success": True,
            "transport_success": True,
            "status": "blocked",
            "code": "extraction_provider_invalid",
            "message": "extraction_provider 仅支持 auto、tavily 或 direct_http",
            "resource_uris": [],
            "warnings": [],
            "blockers": ["extraction_provider_invalid"],
            "next_actions": ["选择受支持的正文提取路径"],
        }

    output_format = "html" if content_mode == "raw" else "markdown"

    # ── 采集 MCP 自身的兜底安全门（入口层）──
    # web_extract_tool 主路径自带密钥与 SSRF 拦截，但含密钥 URL 会让它整体
    # 返回 {success:false}，历史实现会把整批 URL 无条件转入 direct-HTTP
    # 回退。这里在入口逐 URL 复查密钥模式，命中者生成诚实 blocked 条目，
    # 绝不进入任何抓取路径（含回退）。
    blocked_snapshots: list[dict[str, Any]] = []
    checked_urls: list[str] = []
    for raw_url in urls:
        url_text = str(raw_url or "")
        reason = _secret_block_reason(url_text)
        if reason is not None:
            blocked_snapshots.append(
                {"url": url_text, "status": "blocked", "message": reason}
            )
        else:
            checked_urls.append(url_text)

    used_direct_fallback = False
    trusted_tavily = False
    results: list[dict[str, Any]] = []
    if checked_urls:
        if extraction_provider in {"auto", "tavily"}:
            results = await _trusted_tavily_extract(checked_urls, output_format)
            trusted_tavily = bool(results)
        if not results and extraction_provider in {"auto", "tavily"}:
            return {
                "success": False,
                "business_success": False,
                "system_success": True,
                "transport_success": True,
                "status": "upstream_failure",
                "code": "tavily_extract_unavailable",
                "message": "受信 Tavily 正文提取当前不可用",
                "provider": "tavily",
                "retryable": True,
                "retry_after": 5,
                "trace_id": hashlib.sha256(f"fetch:{time.time_ns()}".encode()).hexdigest()[:24],
                "resource_uris": [],
                "warnings": [],
                "blockers": ["tavily_extract_unavailable"],
                "next_actions": ["稍后重试 Tavily；如确需受控直连，必须显式选择 direct_http 并保持非 Tavily 证据轨"],
            }
        if not results and extraction_provider == "direct_http":
            # ── 兜底安全门（回退层）──
            # 进入 SourceExtractor 直连回退前，逐 URL 再过一遍密钥模式与
            # tools/url_safety 的私网/云 metadata 判定；命中者拿到诚实
            # blocked 条目，绝不交给回退抓取。
            fallback_urls: list[str] = []
            for url_text in checked_urls:
                reason = _secret_block_reason(url_text)
                security_decision: dict[str, Any] = {}
                if reason is None:
                    reason, security_decision = await _network_safety_decision(url_text)
                if reason is not None:
                    blocked_snapshots.append(
                        {
                            "url": url_text, "status": "blocked", "message": reason,
                            **({"security_decision": security_decision} if security_decision else {}),
                        }
                    )
                else:
                    fallback_urls.append(url_text)
            if fallback_urls:
                # Reuse the DR extractor's hardened free direct-HTTP path.  This
                # keeps acquisition and DR on one extraction implementation and
                # lets a clean local install fetch public pages even when only a
                # search provider is configured.  SourceExtractor retains DNS
                # rebinding/SSRF checks, redirect validation, content
                # sanitization and bounded timeouts.
                from lvke_mcp.domains.research.contracts import SourceRecord
                from lvke_mcp.domains.research.extractor import SourceExtractor

                extractor = SourceExtractor(
                    providers=[],
                    enable_cache=True,
                    enable_direct_http_fallback=True,
                )
                records = [
                    SourceRecord(
                        source_id=f"mcp_fetch_{index}",
                        canonical_url=url,
                        url=url,
                    )
                    for index, url in enumerate(fallback_urls, 1)
                ]
                extracted = await extractor.extract_many(
                    records, concurrency=min(4, len(records))
                )
                results = [
                    {
                        "url": item.url,
                        "title": item.title,
                        "content": item.content,
                        "error": item.error_message if item.status != "ok" else None,
                        "provider": "direct-http",
                        # 尽力透传 direct-HTTP 抓取拿到的真实 Content-Type；
                        # 拿不到时留空，由下游按 content_mode 保留推断值。
                        "content_type": (
                            str(item.metadata.get("content_type") or "")
                            if isinstance(item.metadata, dict)
                            else ""
                        ),
                        "security_decision": {
                            "allowed": item.status == "ok",
                            "code": "direct_http_pinned" if item.status == "ok" else "direct_http_blocked",
                            "hostname": urlsplit(item.url).hostname or "",
                            "addresses": ([{
                                "address": str(item.metadata.get("connected_peer_ip") or ""),
                                "classification": "public",
                                "allowed": True,
                            }] if isinstance(item.metadata, dict) and item.metadata.get("connected_peer_ip") else []),
                            "final_url": str(item.metadata.get("final_url") or item.url)
                            if isinstance(item.metadata, dict) else item.url,
                            "redirect_chain": list(item.metadata.get("redirect_chain") or [])
                            if isinstance(item.metadata, dict) else [],
                        },
                    }
                    for item in extracted
                ]
                used_direct_fallback = True

    snapshots: list[dict[str, Any]] = list(blocked_snapshots)
    failures = 0
    provider_blocked = 0
    inferred_mime = "text/html" if content_mode == "raw" else "text/markdown"
    for item in results:
        url = str(item.get("url") or "")
        error = str(item.get("error") or "")
        content = str(item.get("content") or "")
        if error or not content:
            if error.lower().startswith("blocked:"):
                provider_blocked += 1
                snapshots.append({
                    "url": url, "status": "blocked", "message": error,
                    **({"security_decision": item["security_decision"]}
                       if item.get("security_decision") else {}),
                })
            else:
                failures += 1
                snapshots.append(
                    {"url": url, "status": "failed", "message": error or "内容为空"}
                )
            continue
        # mime_type 尽力取真实 HTTP Content-Type（去掉 charset 等参数）；
        # 没有真实值时保留按 content_mode 推断的值，并如实标注来源。
        header_mime = str(item.get("content_type") or "").split(";", 1)[0].strip().lower()
        mime_type = header_mime or inferred_mime
        mime_type_source = "http_header" if header_mime else "inferred_from_content_mode"
        payload = {
            "url": url,
            "title": str(item.get("title") or ""),
            "content": content,
            "content_mode": content_mode,
            "mime_type": mime_type,
            "mime_type_source": mime_type_source,
            "provider": str(item.get("provider") or "configured-web-provider"),
            "extraction_provider": "tavily" if trusted_tavily else "direct_http",
            "extraction_receipt": item.get("extraction_receipt"),
            "extraction_receipt_verified": bool(item.get("extraction_receipt_verified")),
            "formal_use_allowed": bool(item.get("extraction_receipt_verified")),
            "evidence_track": "formal_candidate" if item.get("extraction_receipt_verified") else "technical_fixture",
            "security_decision": item.get("security_decision") or {
                "allowed": True,
                "code": "validated_by_configured_provider",
                "hostname": urlsplit(url).hostname or "",
                "addresses": [],
                "redirect_chain": [],
            },
        }
        record = SOURCE_STORE.put(
            workspace_id,
            payload,
            producer="lvke-data-acquisition.data_fetch",
        )
        snapshots.append(
            {
                "url": url,
                "status": "ok",
                "source_snapshot_id": record["object_id"],
                "content_hash": record["content_hash"],
                "fetched_at": record["created_at"],
                "mime_type": payload["mime_type"],
                "mime_type_source": payload["mime_type_source"],
                "security_decision": payload["security_decision"],
                "resource_uri": record["resource_uri"],
                "extraction_provider": payload["extraction_provider"],
                "extraction_receipt_verified": payload["extraction_receipt_verified"],
                "formal_use_allowed": payload["formal_use_allowed"],
            }
        )
    blocked = len(blocked_snapshots) + provider_blocked
    succeeded = len(snapshots) - failures - blocked
    if succeeded and not failures and not blocked:
        status = "ok"
    elif succeeded:
        status = "partial"
    elif blocked and not failures:
        status = "blocked"
    else:
        status = "upstream_failure"
    blockers: list[str] = []
    if blocked:
        blockers.append("url_security_blocked")
    if failures and not succeeded:
        blockers.append("all_urls_failed")
    return {
        "success": succeeded > 0,
        "business_success": status == "ok",
        "system_success": True,
        "transport_success": True,
        "transport_ok": True,
        "status": status,
        "resolved_count": len(checked_urls),
        "succeeded_count": succeeded,
        "blocked_count": blocked,
        "failed_count": failures,
        "snapshots": snapshots,
        "resource_uris": [
            item["resource_uri"] for item in snapshots if item.get("resource_uri")
        ],
        "warnings": (
            ([f"{failures} 个 URL 未能固化"] if failures else [])
            + ([f"{blocked} 个 URL 被采集安全门拦截"] if blocked else [])
            + (["受信 Tavily 不可用，已使用同 URL 的受控 direct-HTTP 路径"] if used_direct_fallback and extraction_provider == "auto" else [])
            + (["direct-HTTP 回退输出可读正文，不保留原始 HTML"] if used_direct_fallback and content_mode == "raw" else [])
        ),
        "blockers": blockers,
        "next_actions": (
            ["将 source_snapshot_id 交给 analysis_ingest"]
            if succeeded
            else (
                ["移除 URL 中携带的密钥或改用公网可达地址后重试"]
                if blocked
                else ["稍后重试 Tavily，或显式选择 direct_http"]
            )
        ),
        "provider": "tavily" if trusted_tavily else "direct_http",
        "retryable": bool(failures and not succeeded),
        "retry_after": 5 if failures and not succeeded else None,
        "trace_id": hashlib.sha256(f"fetch:{time.time_ns()}".encode()).hexdigest()[:24],
    }


def _audit_display_url(url: str) -> str:
    """Return an audit-safe URL without persisting credentials or secret query values."""

    value = str(url or "").strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    if _secret_block_reason(value) is not None or parsed.username or parsed.password:
        hostname = (parsed.hostname or "unknown").lower().rstrip(".")
        return f"{parsed.scheme or 'unknown'}://{hostname}/<redacted>"
    return value


def audit_urls(
    workspace_id: str,
    urls: list[str],
    *,
    audit_mode: str = "safety",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Persist a bounded URL safety/live audit without collecting page content."""

    if audit_mode not in {"safety", "live"}:
        return _resource_failure("url_audit_mode_invalid", "audit_mode 必须为 safety 或 live")
    if not urls:
        return _resource_failure("url_audit_urls_required", "至少需要一个待审计 URL")
    from lvke_mcp.domains.research.extractor import _safe_public_url, _verify_public_url

    bounded_timeout = max(0.1, min(float(timeout_seconds), 30.0))
    checked_at = utc_now()
    results: list[dict[str, Any]] = []
    for index, raw_url in enumerate(urls):
        url = str(raw_url or "").strip()
        display_url = _audit_display_url(url)
        secret_reason = _secret_block_reason(url)
        if secret_reason is not None:
            results.append(
                {
                    "url_index": index,
                    "url": display_url,
                    "canonical_url": None,
                    "url_status": "BLOCKED",
                    "safe_public_target": False,
                    "reason_code": "url_contains_sensitive_value",
                    "checked_at": checked_at,
                }
            )
            continue
        safe = _safe_public_url(url)
        canonical = _canonical_discovery_url(url)
        if not safe:
            results.append(
                {
                    "url_index": index,
                    "url": display_url,
                    "canonical_url": canonical,
                    "url_status": "BLOCKED",
                    "safe_public_target": False,
                    "reason_code": "unsafe_public_url",
                    "checked_at": checked_at,
                }
            )
            continue
        verification: dict[str, Any] = {
            "url_status": "SAFE",
            "url_checked_at": checked_at,
        }
        if audit_mode == "live":
            verification = _verify_public_url(url, timeout=bounded_timeout)
        results.append(
            {
                "url_index": index,
                "url": display_url,
                "canonical_url": canonical,
                "url_status": str(verification.get("url_status") or "UNKNOWN"),
                "safe_public_target": True,
                "reason_code": "",
                "checked_at": str(verification.get("url_checked_at") or checked_at),
                "final_url": str(verification.get("final_url") or ""),
                "peer_ip": str(verification.get("peer_ip") or ""),
            }
        )
    unknown_count = sum(item["url_status"] == "UNKNOWN" for item in results)
    blocked_count = sum(item["url_status"] == "BLOCKED" for item in results)
    status = "partial" if unknown_count else "ok"
    payload = {
        "object_type": "UrlAudit",
        "audit_mode": audit_mode,
        "checked_at": checked_at,
        "timeout_seconds": bounded_timeout,
        "results": results,
        "blocked_count": blocked_count,
        "unknown_count": unknown_count,
        "evidence_boundary": "URL 审计只证明检查时点的安全性/可达性，不固化网页正文，也不授予证据资格。",
    }
    record = URL_AUDIT_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-acquisition.data_audit_urls",
        status=status,
        basis={
            "audit_mode": audit_mode,
            "urls": [item["url"] for item in results],
            "results": results,
        },
    )
    return {
        "success": status == "ok",
        "business_success": status == "ok",
        "system_success": True,
        "transport_success": True,
        "status": status,
        "url_audit_id": record["object_id"],
        "results": results,
        "blocked_count": blocked_count,
        "unknown_count": unknown_count,
        "resource_uris": [record["resource_uri"]],
        "warnings": (["部分 URL 可达性无法判定"] if unknown_count else []),
        "blockers": [],
        "next_actions": [
            "只对 safe_public_target=true 的 URL 执行浏览器查看或正文采集",
            "需要证据时调用 data_fetch/data_import_external_snapshot 固化正文",
        ],
    }


def get_url_audit(
    workspace_id: str,
    url_audit_id: str,
) -> dict[str, Any]:
    record = URL_AUDIT_STORE.get(workspace_id, url_audit_id)
    if record is None:
        return _resource_failure("url_audit_not_found", "URL 审计不存在或不属于当前作用域")
    return {
        "success": True,
        "business_success": True,
        "system_success": True,
        "transport_success": True,
        "status": "ok",
        "url_audit_id": url_audit_id,
        "url_audit": record["payload"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "resource_uris": [record["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def capture_source_view(
    workspace_id: str,
    *,
    source_snapshot_id: str,
    image_file_id: str,
    url: str,
    viewport: dict[str, Any],
    captured_at: str,
    image_content_hash: str = "",
    page_title: str = "",
) -> dict[str, Any]:
    """Bind a governed screenshot to a source snapshot without operating a browser."""

    snapshot = SOURCE_STORE.get(
        workspace_id, source_snapshot_id
    )
    if snapshot is None:
        return _resource_failure(
            "source_snapshot_not_found", "来源快照不存在或不属于当前作用域"
        )
    snapshot_payload = snapshot.get("payload") or {}
    if _canonical_discovery_url(str(snapshot_payload.get("url") or "")) != _canonical_discovery_url(url):
        return _resource_failure(
            "visual_capture_url_mismatch", "截图 URL 与来源快照 URL 不一致"
        )
    try:
        parsed_time = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
    except ValueError:
        return _resource_failure("visual_capture_time_invalid", "captured_at 不是合法 ISO-8601 时间")
    if parsed_time.tzinfo is None:
        return _resource_failure("visual_capture_time_invalid", "captured_at 必须包含时区")
    width = viewport.get("width")
    height = viewport.get("height")
    scale = viewport.get("device_scale_factor", 1)
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or not 1 <= width <= 10000
        or not 1 <= height <= 10000
        or isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not 0.1 <= float(scale) <= 10
    ):
        return _resource_failure("visual_capture_viewport_invalid", "viewport 尺寸或缩放比例无效")
    from lvke_mcp.adapters import source_files_repository

    try:
        _state, file_record = source_files_repository._require_source_record(
            workspace_id, image_file_id
        )
    except source_files_repository.SourceFileError:
        return _resource_failure(
            "visual_capture_file_not_found", "截图文件不存在或不属于当前作用域"
        )
    filename = str(file_record.get("original_filename") or "")
    file_format = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    scan = file_record.get("security_scan") or {}
    if file_format not in {"png", "jpg", "jpeg"} or not scan.get("type_verified"):
        return _resource_failure(
            "visual_capture_file_type_invalid", "截图必须是已通过类型校验的 PNG/JPEG 原始资料"
        )
    actual_hash = "sha256:" + str(file_record.get("sha256") or "").lower().removeprefix("sha256:")
    supplied_hash = str(image_content_hash or actual_hash).lower()
    if not supplied_hash.startswith("sha256:"):
        supplied_hash = "sha256:" + supplied_hash
    if supplied_hash != actual_hash:
        return _resource_failure("visual_capture_hash_mismatch", "截图内容哈希与已固化文件不一致")
    normalized_viewport = {
        "width": width,
        "height": height,
        "device_scale_factor": float(scale),
    }
    payload = {
        "object_type": "VisualSourceCapture",
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_content_hash": snapshot["content_hash"],
        "image_file_id": image_file_id,
        "image_content_hash": actual_hash,
        "url": _audit_display_url(url),
        "page_title": str(page_title or ""),
        "viewport": normalized_viewport,
        "captured_at": parsed_time.isoformat(),
        "evidence_track": "candidate",
        "formal_use_allowed": False,
        "capture_method": "external_browser_supplied",
        "evidence_boundary": "截图只辅助复核页面状态；正式事实仍须绑定正文快照 locator 和内容哈希。",
    }
    record = VISUAL_CAPTURE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-acquisition.data_capture_source_view",
        source_ids=[source_snapshot_id, image_file_id],
        basis={
            "source_snapshot_content_hash": snapshot["content_hash"],
            "image_content_hash": actual_hash,
            "url": payload["url"],
            "viewport": normalized_viewport,
            "captured_at": payload["captured_at"],
        },
    )
    return {
        "success": True,
        "business_success": True,
        "system_success": True,
        "transport_success": True,
        "status": "ok",
        "visual_capture_id": record["object_id"],
        "visual_capture": payload,
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "formal_use_allowed": False,
        "resource_uris": [
            record["resource_uri"],
            snapshot["resource_uri"],
            f"lvke://source-files/workspaces/{workspace_id}/files/{image_file_id}",
        ],
        "warnings": ["视觉捕获不会自动升级证据资格"],
        "blockers": [],
        "next_actions": ["使用正文 snapshot locator 核对截图中的事实陈述"],
    }


def get_visual_capture(
    workspace_id: str,
    visual_capture_id: str,
) -> dict[str, Any]:
    record = VISUAL_CAPTURE_STORE.get(
        workspace_id, visual_capture_id
    )
    if record is None:
        return _resource_failure(
            "visual_capture_not_found", "视觉来源捕获不存在或不属于当前作用域"
        )
    return {
        "success": True,
        "business_success": True,
        "system_success": True,
        "transport_success": True,
        "status": "ok",
        "visual_capture_id": visual_capture_id,
        "visual_capture": record["payload"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "formal_use_allowed": False,
        "resource_uris": [record["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
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
        return _resource_failure(
            "resource_type_invalid",
            "未知 Resource 类型过滤条件",
        )
    entries: list[dict[str, Any]] = []
    for store, kind in _RESOURCE_STORES:
        if resource_type and kind != resource_type:
            continue
        for record in store.list(workspace_id):
            uri = str(record.get("resource_uri") or "")
            if uri:
                entries.append(
                    {
                        "uri": uri,
                        "name": str(record.get("object_id") or ""),
                        "resource_type": kind,
                        "mime_type": "application/json",
                        "created_at": record.get("created_at"),
                    }
                )
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _resource_failure(
            str(exc),
            "Resource 分页游标无效或列表已变化",
        )
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
    return resolve_repository_resource(uri, workspace_id)


def _resource_failure(code: str, message: str) -> dict[str, Any]:
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


def provider_status() -> dict[str, Any]:
    import asyncio

    from lvke_mcp.domains.research.providers import tavily as tavily_provider

    providers = [asyncio.run(tavily_provider.provider_status())]
    available_count = sum(1 for item in providers if item["available"])
    status = "ok" if available_count else "blocked"
    return {
        "success": bool(available_count),
        "transport_success": True,
        "business_success": bool(available_count),
        "completed": bool(available_count),
        "outcome": status,
        "status": status,
        "checked_at": utc_now(),
        "providers": providers,
        "resource_uris": [],
        "warnings": [] if available_count else ["当前没有可用 Web provider"],
        "blockers": [] if available_count else ["provider_configuration_missing"],
        "next_actions": [] if available_count else ["配置受信 Tavily，或使用受控 direct_http 采集"],
    }
