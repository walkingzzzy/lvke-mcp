"""lvke-data-acquisition service 拆分：搜索 provider、相关性与 data_search。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import re
import threading
import time
from typing import Any

from lvke_mcp.adapters.data_acquisition_repository import SEARCH_STORE

from .constants import (
    _SEARCH_RELEVANCE_THRESHOLD,
    _SEARCH_PROVIDER,
    _SEARCH_SLOTS,
    _SEARCH_TOKEN_RE,
)
from .urls import _canonical_discovery_url, _candidate_domain, _secret_block_reason


def _canonical_search_provider(value: Any, requested: str) -> str:
    """Expose the configured Tavily identity instead of adapter placeholders."""

    provider = str(value or "").strip().lower().replace("_", "-")
    requested = str(requested or "").strip().lower().replace("_", "-")
    if requested == _SEARCH_PROVIDER and provider in {
        "",
        "auto",
        "unknown",
        "configured-web-provider",
        "tavily",
    }:
        return _SEARCH_PROVIDER
    return provider or requested or "unknown"


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
            "tavily-hikari" if tavily_provider.configured_transport() else "none"
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
    # NEW-P2-B 修复：provider 可能返回站内跳转的相对 URL（形如
    # ``/goto?url=<opaque>``）。这类 URL 缺少 scheme 与 host，调用方无法访问，
    # 也无法作为 locator 固化。这里复用 discover 的同一道规范化过滤，把不可用
    # URL 归入 skipped 而不是伪装成可用结果。
    skipped_urls: list[dict[str, str]] = []
    for index, item in enumerate(web if isinstance(web, list) else []):
        if not isinstance(item, dict):
            continue
        raw_url = str(item.get("url") or "")
        canonical_url = _canonical_discovery_url(raw_url)
        if canonical_url is None:
            skipped_urls.append({
                "url": raw_url,
                "reason": "unsupported_or_relative_url",
            })
            continue
        title = str(item.get("title") or "")
        summary = str(item.get("description") or item.get("summary") or "")
        results.append({
            "title": title,
            "url": canonical_url,
            "summary": summary,
            "provider": _canonical_search_provider(item.get("provider"), provider_requested),
            "rank": int(item.get("position") or index + 1),
            "relevance": _search_relevance(query, title, summary, canonical_url),
        })
    provider_used = _canonical_search_provider(
        raw.get("provider")
        or data.get("provider")
        or next((item["provider"] for item in results if item.get("provider")), ""),
        provider_requested,
    )
    fallback_reason = None
    if (
        provider_requested not in {"", "auto"}
        and provider_used not in {"", "unknown", provider_requested}
    ):
        fallback_reason = "configured_provider_unavailable_or_replaced"
    relevant_count = sum(
        item["relevance"] >= _SEARCH_RELEVANCE_THRESHOLD for item in results
    )
    warnings: list[str] = []
    if fallback_reason:
        warnings.append("search_provider_fallback")
    if skipped_urls:
        # 让调用方看到「provider 返回了结果但 URL 不可用」，而不是静默变少。
        warnings.append("search_results_dropped_unusable_url")
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
        status = "partial" if (fallback_reason or skipped_urls) else "ok"
    record = SEARCH_STORE.put(
        workspace_id,
        {
            "query": query,
            "limit": limit,
            "results": results,
            "skipped": skipped_urls,
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
        "skipped": skipped_urls,
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