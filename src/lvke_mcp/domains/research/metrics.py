"""Metric helpers used by research and report gates."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse

from .contracts import ResearchMetrics, SearchBatch, SourceRecord


def registrable_domain(url: str) -> str:
    """Return a conservative registrable-domain approximation.

    The legacy engine has a richer helper for Chinese public suffixes.  This
    local implementation keeps the metrics module independent; the source
    normalizer will replace it with the canonical project helper.
    """

    host = urlparse(str(url or "")).netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in host.split(".") if part]
    if len(parts) <= 2:
        return host
    multi = {"gov.cn", "org.cn", "edu.cn", "com.cn", "net.cn", "ac.cn", "co.uk"}
    suffix2 = ".".join(parts[-2:])
    if suffix2 in multi and len(parts) >= 3:
        return ".".join(parts[-3:])
    return suffix2


def collect_search_metrics(
    batches: Iterable[SearchBatch],
    sources: Iterable[SourceRecord],
    *,
    round_no: int,
) -> ResearchMetrics:
    batch_list = list(batches)
    source_list = list(sources)
    domains = {
        source.registrable_domain or registrable_domain(source.canonical_url or source.url)
        for source in source_list
    }
    domains.discard("")
    failed = sum(1 for batch in batch_list if batch.status not in {"ok", "zero_result"})
    extracted = sum(1 for source in source_list if source.extract_status == "ok")
    return ResearchMetrics(
        round_no=round_no,
        search_calls=len(batch_list),
        failed_search_calls=failed,
        raw_hits=sum(len(batch.hits) for batch in batch_list),
        unique_candidates=len(source_list),
        extracted_sources=extracted,
        independent_domains=len(domains),
    )

