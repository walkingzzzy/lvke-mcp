"""Canonical source identity and cross-query deduplication."""

from __future__ import annotations

import hashlib
import ipaddress
import posixpath
import re
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .contracts import SearchBatch, SourceRecord
from .metrics import registrable_domain


_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "spm",
}

# 权威度分层的域名规则（既有 deep_research 逻辑原样保留）。
# A：政府/统计/发改/协会/上市公司年报/权威媒体；B：行业门户/研究机构/主流财经媒体；C：其余。
_TIER_A_SUFFIXES: tuple[str, ...] = (".gov.cn", ".gov", ".edu.cn", ".edu", ".org.cn", ".ac.cn")
_TIER_A_KEYWORDS: tuple[str, ...] = (
    "stats.gov", "ndrc", "miit", "mofcom", "customs.gov", "pbc.gov", "csrc",
    "cninfo", "sse.com", "szse.cn", "szse", "xinhua", "people.com", "gov.cn",
)
_TIER_B_KEYWORDS: tuple[str, ...] = (
    "chyxx", "qianzhan", "askci", "chinairn", "iresearch", "analysys", "cbndata",
    "eastmoney", "yicai", "caixin", "21jingji", "stcn", "cs.com", "hexun",
    "sina.com", "sohu.com", "163.com", "ce.cn", "cnstock", "report",
)

# P2：权威域下的转载/自媒体路径信号——命中则不兑现该域名的先验 tier（域名≠权威）。
_TRANSLOAD_PATH_SIGNALS: tuple[str, ...] = (
    "/baijiahao", "baijiahao.", "/dfh/", "/dfh.", "hao.", "/haibao",
    "/author/", "/user/", "/u/", "/people/", "/pl/", "/blog/", "/space/",
    "/zhuanlan", "zhuanlan.", "/column/", "/self/", "/from_media",
    "/wemedia", "/media/", "/thread", "/post/", "/bbs/", "/forum",
)


def _is_self_media_url(url: str) -> bool:
    """判定 URL 是否落在权威域名下的转载/自媒体路径（P2：域名≠内容权威）。"""
    parsed = urlparse(url or "")
    blob = f"{parsed.netloc.lower()}{parsed.path.lower()}"
    return any(sig in blob for sig in _TRANSLOAD_PATH_SIGNALS)


def _source_tier(url: str, *, extract_status: str = "") -> str:
    """按域名给来源打权威度标签：A 权威 / B 公开 / C 一般（域内版）。

    供交叉验证和证据等级参考——C 级仅作线索，不单独支撑结论。
    P2 收敛：命中权威域下的转载路径或正文抓取失败时 A 降 B。
    """
    netloc = urlparse(url or "").netloc.lower().split(":")[0]
    if not netloc:
        return "C"
    tier = "C"
    if any(netloc.endswith(sfx) for sfx in _TIER_A_SUFFIXES):
        tier = "A"
    elif any(kw in netloc for kw in _TIER_A_KEYWORDS):
        tier = "A"
    elif any(kw in netloc for kw in _TIER_B_KEYWORDS):
        tier = "B"
    if tier == "A" and (_is_self_media_url(url) or extract_status == "failed"):
        tier = "B"
    return tier


def canonicalize_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    original_scheme = parsed.scheme.lower()
    if original_scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None and not 1 <= port <= 65535:
        return ""
    hostname = str(parsed.hostname or "").lower().rstrip(".")
    if (
        not hostname
        or re.search(r"[\s\x00-\x1f\x7f]", hostname)
        or hostname in {"localhost", "localhost.localdomain"}
        or hostname.endswith((".localhost", ".local"))
    ):
        return ""
    try:
        literal_address = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal_address = None
    if literal_address is not None and not literal_address.is_global:
        return ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and not (
        (original_scheme == "http" and port == 80)
        or (original_scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    # Preserve the established canonical contract that upgrades public HTTP
    # search results to HTTPS while removing default ports.
    scheme = "https"
    path = posixpath.normpath(parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
        )
    )
    return urlunparse((scheme, host, path, "", query, ""))


def source_id(canonical_url: str) -> str:
    return "src_" + hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]


def _publisher_identity(publisher: str, domain: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(publisher or "").lower())
    # Hostnames and empty publisher labels should collapse to the registrable
    # domain. Named organizations can remain stable across multiple domains.
    if not value or "." in str(publisher or "") or value in domain.replace(".", ""):
        value = domain
    return "pub_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:14]


def _normalize_published_at(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    match = re.search(
        r"(?<!\d)((?:19|20)\d{2})[\-/.\u5e74]([01]?\d)[\-/.\u6708]([0-3]?\d)(?:\u65e5)?",
        text,
    )
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return ""
    year = re.fullmatch(r"(?:19|20)\d{2}", text)
    return year.group(0) if year else ""


def _inferred_source_type(host: str, raw: dict) -> str:
    explicit = str(raw.get("source_type") or "").strip().lower()
    if explicit:
        return explicit
    if host.endswith((".gov.cn", ".gov")):
        return "primary"
    if any(
        token in host
        for token in ("cninfo.com.cn", "sse.com.cn", "szse.cn")
    ):
        return "filing"
    if host.endswith((".edu.cn", ".edu", ".ac.cn")):
        return "academic"
    if host.endswith((".org.cn", ".org")):
        return "association"
    return "web"


def _governed_tier(url: str, raw: dict, source_type: str) -> str:
    """Keep authority and primary provenance separate.

    The legacy helper treats every ``.org.cn`` and ``.edu.cn`` host as A.
    That is too broad for a completion gate, and the evidence metric currently
    treats every A source as primary.  Until document-level provenance is
    confirmed, academic, association and media pages therefore remain B.
    """

    tier = _source_tier(url)
    if tier == "A" and source_type not in {"primary", "filing"}:
        return "B"
    return tier


def _source_metadata(hit_url: str, raw: dict) -> dict[str, str]:
    canonical = canonicalize_url(hit_url)
    domain = registrable_domain(canonical)
    host = urlparse(canonical).netloc
    publisher = str(
        raw.get("publisher")
        or raw.get("source")
        or raw.get("organization")
        or host
    ).strip()
    path = urlparse(canonical).path.lower()
    content_type = str(raw.get("content_type") or "").lower()
    if not content_type:
        content_type = "application/pdf" if path.endswith(".pdf") else "text/html"
    source_type = _inferred_source_type(host, raw)
    return {
        "publisher": publisher,
        "publisher_id": _publisher_identity(publisher, domain),
        "registrable_domain": domain,
        "published_at": _normalize_published_at(
            raw.get("published_at")
            or raw.get("published_date")
            or raw.get("date")
        ),
        "updated_at": _normalize_published_at(
            raw.get("updated_at")
            or raw.get("modified_at")
            or raw.get("dateModified")
        ),
        "language": str(raw.get("language") or "").strip(),
        "content_type": content_type,
        "source_type": source_type,
        "original_url": str(raw.get("original_url") or "").strip(),
        "archive_url": str(raw.get("archive_url") or "").strip(),
        "url_checked_at": str(raw.get("url_checked_at") or "").strip(),
    }


def enrich_source_metadata(
    source: SourceRecord,
    *,
    content: str = "",
    title: str = "",
    metadata: dict | None = None,
) -> SourceRecord:
    """Enrich a source from extracted document metadata and labelled text.

    Only explicit metadata fields or labelled publisher/date lines are used;
    the function deliberately avoids guessing an institution from arbitrary
    prose.  It is safe to call while replaying an existing offline snapshot.
    """

    data = dict(metadata or {})
    if title and not source.title:
        source.title = str(title).strip()
    host = urlparse(source.canonical_url or source.url).netloc.lower()
    domain = source.registrable_domain or registrable_domain(
        source.canonical_url or source.url
    )
    publisher_candidates = (
        data.get("publisher"),
        data.get("site_name"),
        data.get("siteName"),
        data.get("organization"),
        data.get("institution"),
    )
    publisher = next(
        (
            re.sub(r"\s+", " ", str(value)).strip()[:100]
            for value in publisher_candidates
            if str(value or "").strip()
        ),
        "",
    )
    if not publisher:
        labelled = re.search(
            r"(?:^|\n)\s*(?:来源|发布机构|主办单位|主管单位|作者单位)\s*[:：]\s*([^\n|]{2,80})",
            str(content or "")[:6000],
        )
        if labelled:
            publisher = re.sub(r"\s+", " ", labelled.group(1)).strip(" -_")[:100]
    current_is_host = (
        not source.publisher
        or "." in source.publisher
        or source.publisher.lower() in {host, domain}
    )
    if publisher and current_is_host:
        source.publisher = publisher
        source.publisher_id = _publisher_identity(publisher, domain)

    if not source.published_at:
        date_candidates = (
            data.get("published_at"),
            data.get("published_date"),
            data.get("datePublished"),
            data.get("date"),
            data.get("created_at"),
        )
        source.published_at = next(
            (
                normalized
                for value in date_candidates
                if (normalized := _normalize_published_at(value))
            ),
            "",
        )
        if not source.published_at:
            labelled_date = re.search(
                r"(?:发布时间|发布日期|成文日期)\s*[:：]?\s*"
                r"((?:19|20)\d{2}[\-/.\u5e74][01]?\d[\-/.\u6708][0-3]?\d日?)",
                str(content or "")[:8000],
            )
            if labelled_date:
                source.published_at = _normalize_published_at(labelled_date.group(1))

    if not source.updated_at:
        updated_candidates = (
            data.get("updated_at"),
            data.get("modified_at"),
            data.get("dateModified"),
        )
        source.updated_at = next(
            (
                normalized
                for value in updated_candidates
                if (normalized := _normalize_published_at(value))
            ),
            "",
        )
        if not source.updated_at:
            labelled_update = re.search(
                r"(?:更新日期|修订日期|最后更新)\s*[:：]?\s*"
                r"((?:19|20)\d{2}[\-/.\u5e74][01]?\d[\-/.\u6708][0-3]?\d日?)",
                str(content or "")[:8000],
            )
            if labelled_update:
                source.updated_at = _normalize_published_at(
                    labelled_update.group(1)
                )

    if not source.language:
        explicit_language = str(
            data.get("language")
            or data.get("lang")
            or data.get("content_language")
            or ""
        ).strip()
        if explicit_language:
            source.language = explicit_language[:24]
        else:
            sample = str(content or "")[:4000]
            chinese = len(re.findall(r"[\u4e00-\u9fff]", sample))
            latin = len(re.findall(r"[A-Za-z]", sample))
            if chinese or latin:
                source.language = "zh-CN" if chinese >= latin / 2 else "en"
    return source


class SourceNormalizer:
    def merge(
        self,
        batches: list[SearchBatch],
        existing: dict[str, SourceRecord] | None = None,
    ) -> dict[str, SourceRecord]:
        result = dict(existing or {})
        for batch in batches:
            if batch.status != "ok":
                continue
            for hit in batch.hits:
                canonical = canonicalize_url(hit.url)
                if not canonical:
                    continue
                sid = source_id(canonical)
                current = result.get(sid)
                metadata = _source_metadata(hit.url, hit.raw)
                if current is None:
                    current = SourceRecord(
                        source_id=sid,
                        canonical_url=canonical,
                        url=hit.url,
                        title=hit.title,
                        publisher=metadata["publisher"],
                        publisher_id=metadata["publisher_id"],
                        registrable_domain=metadata["registrable_domain"],
                        snippet=hit.snippet,
                        tier=_governed_tier(
                            hit.url,
                            hit.raw,
                            metadata["source_type"],
                        ),
                        source_type=metadata["source_type"],
                        language=metadata["language"],
                        published_at=metadata["published_at"],
                        updated_at=metadata["updated_at"],
                        content_type=metadata["content_type"],
                        original_url=metadata["original_url"],
                        archive_url=metadata["archive_url"],
                        url_checked_at=metadata["url_checked_at"],
                        query_ids=[batch.query_id],
                        plan_node_ids=[batch.plan_node_id],
                    )
                    result[sid] = current
                    continue
                if batch.query_id not in current.query_ids:
                    current.query_ids.append(batch.query_id)
                if batch.plan_node_id not in current.plan_node_ids:
                    current.plan_node_ids.append(batch.plan_node_id)
                if not current.title and hit.title:
                    current.title = hit.title
                if len(hit.snippet) > len(current.snippet):
                    current.snippet = hit.snippet
                for field_name in (
                    "publisher",
                    "publisher_id",
                    "published_at",
                    "updated_at",
                    "language",
                    "content_type",
                    "original_url",
                    "archive_url",
                    "url_checked_at",
                ):
                    if not getattr(current, field_name) and metadata.get(field_name):
                        setattr(current, field_name, metadata[field_name])
        return result
