"""lvke-data-acquisition service 拆分：候选发现（data_discover）。"""

from __future__ import annotations

import time
from typing import Any

from lvke_mcp.adapters.data_acquisition_repository import DISCOVERY_STORE

from .constants import _SEARCH_RELEVANCE_THRESHOLD
from .searching import _canonical_search_provider, _search_timeout_seconds, search
from .urls import _canonical_discovery_url, _candidate_domain, _matches_domain_rule


# Feasibility-study angles used to expand a base topic into several distinct
# search queries.  Tavily is the only search provider; reaching 30-50 distinct
# candidates still requires multiple query angles and a de-duplicated union.
# These deterministic suffixes are reproducible and auditable.
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
                    "provider": _canonical_search_provider(
                        item.get("provider"), "tavily-hikari"
                    ),
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