"""Concurrent full-text extraction for Deep Research sources."""

from __future__ import annotations

import asyncio
import hashlib
import html
import http.client
import ipaddress
import inspect
import io
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse, urlunparse


from .contracts import ExtractRecord, SourceRecord
from .provider_executor import (
    SyncProviderCallCancelled,
    SyncProviderCallTimeout,
    SyncProviderIsolationUnavailable,
    SyncProviderProcess,
    SyncProviderProcessTerminated,
)
from .safety import redact_sensitive_text, sanitize_untrusted_text
from .source_normalizer import enrich_source_metadata


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_NAVIGATION_RE = re.compile(
    r"^(\u9996\u9875|\u5bfc\u822a|\u767b\u5f55|\u6ce8\u518c|\u8054\u7cfb\u6211\u4eec|\u7f51\u7ad9\u5730\u56fe|\u7248\u6743|\u9690\u79c1|\u4e0a\u4e00\u7bc7|\u4e0b\u4e00\u7bc7)(\s|$)",
    re.IGNORECASE,
)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5


def _default_fetch_headers() -> dict[str, str]:
    """Primary research fetcher identity (kept for audit stability)."""

    return {
        "User-Agent": "Mozilla/5.0 DeepResearch/2.0",
        "Accept": (
            "text/html,application/xhtml+xml,application/pdf,"
            "text/plain;q=0.8,*/*;q=0.1"
        ),
        "Connection": "close",
    }


def _fetch_header_profiles() -> list[dict[str, str]]:
    """Ordered header profiles for public gov/WAF resilience.

    Some provincial gateways reject the research UA or browser chrome with
    HTTP 412 / TLS EOF, but still serve the same document to common
    search-engine crawlers. Profiles are tried only after the primary identity
    fails with a retryable transport/gateway signal; TLS hostname checks and
    public-IP pinning are never weakened.
    """

    return [
        _default_fetch_headers(),
        {
            # Empirically unblocks fgw.hubei.gov.cn body pages that SERP already
            # surfaced with matching quantitative snippets (e.g. 本地配套率 40％).
            "User-Agent": (
                "Mozilla/5.0 (compatible; Baiduspider/2.0; "
                "+http://www.baidu.com/search/spider.html)"
            ),
            "Accept": "*/*",
            "Connection": "close",
        },
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    ]


_HEADER_RETRY_STATUSES = {
    403,
    412,
    502,
    503,
    520,
    521,
    522,
    523,
    524,
}


class UnsafePublicURLError(ValueError):
    """Raised when a local network fetch would cross the public-network boundary."""


@dataclass(frozen=True)
class PublicHTTPRead:
    """One bounded response fetched through a DNS-pinned public connection."""

    data: bytes
    final_url: str
    headers: dict[str, str]
    peer_ip: str
    redirect_chain: tuple[str, ...]


class _ResearchHTMLParser(HTMLParser):
    """Extract visible prose without executing or retaining active content."""

    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    _IGNORED_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._title_depth = 0
        self._parts: list[str] = []
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        del attrs
        normalized = tag.lower()
        if normalized in self._IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if normalized == "title":
            self._title_depth += 1
        if normalized in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self._IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if normalized == "title":
            self._title_depth = max(0, self._title_depth - 1)
        if normalized in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = str(data or "")
        self._parts.append(value)
        if self._title_depth:
            self._title_parts.append(value)

    @property
    def text(self) -> str:
        return "".join(self._parts)

    @property
    def title(self) -> str:
        return _SPACE_RE.sub(" ", "".join(self._title_parts)).strip()


class ExtractionCache:
    def __init__(self, ttl_seconds: float = 1800.0) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._values: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            value = self._values.get(key)
            if not value:
                return None
            expires_at, payload = value
            if expires_at < time.time():
                self._values.pop(key, None)
                return None
            return dict(payload)

    def set(self, key: str, value: ExtractRecord) -> None:
        if self.ttl_seconds <= 0 or value.status != "ok":
            return
        with self._lock:
            self._values[key] = (time.time() + self.ttl_seconds, value.to_dict())


_DEFAULT_EXTRACTION_CACHE = ExtractionCache()


def clean_extracted_text(value: str) -> str:
    """Normalize provider output without treating navigation as evidence."""

    text = html.unescape(str(value or ""))
    if "<" in text and ">" in text:
        text = _TAG_RE.sub(" ", text)
    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.replace("\u00a0", " ").splitlines():
        line = _SPACE_RE.sub(" ", raw).strip()
        if len(line) < 12 or _NAVIGATION_RE.match(line):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines).strip()


def relevant_chunks(content: str, *, hints: Iterable[str] = (), max_chunks: int = 12) -> list[str]:
    """Return bounded passages, prioritizing query terms and quantitative text."""

    paragraphs = [
        part.strip()
        for part in re.split(r"\n{1,}|(?<=[\u3002\uff01\uff1f.!?])\s+", content)
        if len(part.strip()) >= 40
    ]
    tokens = {
        token.lower()
        for hint in hints
        for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", str(hint))
        if len(token) >= 2
    }

    def score(item: tuple[int, str]) -> tuple[int, int, int]:
        index, paragraph = item
        low = paragraph.lower()
        return (
            sum(1 for token in tokens if token in low),
            1 if re.search(r"\d", paragraph) else 0,
            -index,
        )

    ranked = sorted(enumerate(paragraphs), key=score, reverse=True)
    selected = sorted(ranked[: max(1, int(max_chunks))], key=lambda item: item[0])
    chunks: list[str] = []
    for _, paragraph in selected:
        if len(paragraph) > 1200:
            paragraph = paragraph[:1200].rsplit("\u3002", 1)[0] or paragraph[:1200]
        chunks.append(paragraph)
    return chunks


def _resolve_hostname(host: str, port: int) -> tuple[str, ...]:
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(
        dict.fromkeys(
            str(sockaddr[0]).split("%", 1)[0]
            for _family, _type, _proto, _canonname, sockaddr in records
            if sockaddr
        )
    )


def _normalized_address(value: str) -> str:
    address = ipaddress.ip_address(str(value or "").split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(_normalized_address(value))
    except ValueError:
        return False
    # Clash/Surge "fake-ip" often maps public hosts to 198.18.0.0/15 (RFC 2544
    # benchmarking range). Python marks these non-global, which blocked *all*
    # extractions on single-machine proxy setups (audit: 0 extracts on gov.cn).
    # Allow when DR_ALLOW_PROXY_DNS is not "0" (default allow for 单机).
    import os as _os

    if _os.environ.get("DR_ALLOW_PROXY_DNS", "1").strip() != "0":
        try:
            if address in ipaddress.ip_network("198.18.0.0/15"):
                return True
        except Exception:
            pass
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_multicast
        and not address.is_unspecified
    )


def _safe_public_url(
    url: str,
    *,
    resolver: Callable[[str, int], Iterable[str]] | None = None,
) -> bool:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    # Userinfo can smuggle credentials into an outbound request and malformed
    # ports must fail closed for both hostnames and literal IP addresses.
    if parsed.username is not None or parsed.password is not None:
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    if not 1 <= int(port) <= 65535:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if (
        host in {"localhost", "localhost.localdomain"}
        or host.endswith((".localhost", ".local"))
    ):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            addresses = tuple((resolver or _resolve_hostname)(host, port))
        except (OSError, TypeError, ValueError):
            return False
        return bool(addresses) and all(_public_address(value) for value in addresses)
    return _public_address(str(address))


def _resolve_public_target(
    url: str,
    *,
    resolver: Callable[[str, int], Iterable[str]] | None = None,
) -> tuple[str, int, tuple[str, ...]]:
    """Resolve one URL once and return only a fully public address set.

    The returned addresses are the exact values used by the socket connection.
    Keeping resolution and connection in one data flow closes the classic
    validate-then-resolve DNS rebinding window.
    """

    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafePublicURLError("unsafe_url: non-public HTTP(S) target")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafePublicURLError("unsafe_url: URL userinfo is not allowed")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafePublicURLError("unsafe_url: invalid target port") from exc
    if not 1 <= int(port) <= 65535:
        raise UnsafePublicURLError("unsafe_url: invalid target port")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".localhost", ".local")
    ):
        raise UnsafePublicURLError("unsafe_url: local hostname is not allowed")
    try:
        literal = _normalized_address(host)
    except ValueError:
        try:
            raw_addresses = tuple((resolver or _resolve_hostname)(host, int(port)))
        except (OSError, TypeError, ValueError) as exc:
            raise UnsafePublicURLError("unsafe_url: hostname resolution failed") from exc
    else:
        raw_addresses = (literal,)
    try:
        addresses = tuple(
            dict.fromkeys(_normalized_address(value) for value in raw_addresses)
        )
    except ValueError as exc:
        raise UnsafePublicURLError("unsafe_url: invalid DNS address") from exc
    if not addresses or not all(_public_address(value) for value in addresses):
        raise UnsafePublicURLError("unsafe_url: DNS target is not wholly public")
    return host, int(port), addresses


def _validate_connected_peer(sock: socket.socket, expected_ip: str) -> str:
    try:
        peer = _normalized_address(str(sock.getpeername()[0]))
        expected = _normalized_address(expected_ip)
    except (OSError, TypeError, ValueError, IndexError) as exc:
        raise UnsafePublicURLError("unsafe_url: connected peer could not be verified") from exc
    if peer != expected or not _public_address(peer):
        raise UnsafePublicURLError(
            "unsafe_url: connected peer differs from the validated public address"
        )
    return peer


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that never performs a second hostname lookup."""

    def __init__(self, host: str, port: int, address: str, *, timeout: float) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._validated_address = address
        self.connected_peer = ""

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )
        try:
            self.connected_peer = _validate_connected_peer(
                sock,
                self._validated_address,
            )
            self.sock = sock
        except Exception:
            sock.close()
            raise


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS connection pinned to an IP while preserving hostname verification."""

    def __init__(
        self,
        host: str,
        port: int,
        address: str,
        *,
        timeout: float,
        context: ssl.SSLContext | None = None,
    ) -> None:
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._validated_address = address
        self.connected_peer = ""

    def connect(self) -> None:
        raw_sock = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )
        try:
            _validate_connected_peer(raw_sock, self._validated_address)
            # ``server_hostname`` deliberately remains the original URL host:
            # certificate validation and SNI must not be weakened by IP pinning.
            tls_sock = self._context.wrap_socket(raw_sock, server_hostname=self.host)
            self.connected_peer = _validate_connected_peer(
                tls_sock,
                self._validated_address,
            )
            self.sock = tls_sock
        except Exception:
            raw_sock.close()
            raise


def _request_target(url: str) -> str:
    from urllib.parse import quote

    parsed = urlparse(url)
    # Encode non-ASCII paths (e.g. Chinese Wikipedia) for HTTP request-line.
    raw_path = parsed.path or "/"
    path = quote(raw_path, safe="/%:@-._~!$&'()*+,;=")
    if parsed.query:
        # Keep already-encoded query; only encode if raw non-ascii appears
        q = parsed.query
        try:
            q.encode("ascii")
        except UnicodeEncodeError:
            q = quote(q, safe="=&%:@-._~!$'()*+,;")
        return f"{path}?{q}"
    return path


def _host_header(host: str, port: int, scheme: str) -> str:
    rendered = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    return rendered if port == default_port else f"{rendered}:{port}"



def _replace_url_host(url: str, new_host: str) -> str:
    """Return ``url`` with only the hostname swapped, keeping port/path/query."""

    parsed = urlparse(str(url or ""))
    if not parsed.scheme or not parsed.hostname or not new_host:
        return ""
    hostname = str(new_host).lower().rstrip(".")
    if not hostname or hostname == str(parsed.hostname or "").lower().rstrip("."):
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if parsed.username is not None or parsed.password is not None:
        # Userinfo is rejected by the public reader; keep helper pure and empty.
        return ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        host = f"{host}:{port}"
    return urlunparse(
        (parsed.scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def _host_fetch_candidates(url: str) -> list[str]:
    """Return primary URL plus one apex/www sibling for flaky gov TLS terminations.

    Canonical source identity still strips ``www.`` elsewhere; this only widens
    the *fetch* attempt surface when the apex host EOF/handshake fails while the
    ``www`` twin serves the same document.
    """

    primary = str(url or "").strip()
    if not primary:
        return []
    candidates = [primary]
    parsed = urlparse(primary)
    host = str(parsed.hostname or "").lower().rstrip(".")
    if not host:
        return candidates
    # Skip raw IP literals; SNI/host twin tricks do not apply.
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return candidates
    except ValueError:
        pass
    if host.startswith("www."):
        sibling = host[4:]
    else:
        sibling = f"www.{host}"
    alt = _replace_url_host(primary, sibling)
    if alt and alt not in candidates:
        candidates.append(alt)
    return candidates


def _request_pinned_once(
    url: str,
    *,
    host: str,
    port: int,
    addresses: tuple[str, ...],
    timeout: float,
    max_bytes: int,
    method: str,
) -> tuple[bytes, int, dict[str, str], str]:
    """Try validated addresses without ever handing the hostname to connect()."""

    parsed = urlparse(url)
    last_error: Exception | None = None
    for address in addresses:
        try:
            profiles = _fetch_header_profiles()
            last_status_error: HTTPError | None = None
            for profile in profiles:
                # Rebuild connection per profile so a half-open socket from a
                # prior 412/EOF cannot poison the next UA attempt.
                connection = (
                    _PinnedHTTPSConnection(
                        host,
                        port,
                        address,
                        timeout=timeout,
                    )
                    if parsed.scheme == "https"
                    else _PinnedHTTPConnection(
                        host,
                        port,
                        address,
                        timeout=timeout,
                    )
                )
                try:
                    request_headers = {
                        "Host": _host_header(host, port, parsed.scheme),
                        **profile,
                    }
                    connection.request(
                        method,
                        _request_target(url),
                        headers=request_headers,
                    )
                    response = connection.getresponse()
                    headers = {
                        str(key).lower(): str(value)
                        for key, value in response.getheaders()
                    }
                    status = int(response.status or 0)
                    data = b"" if method == "HEAD" else response.read(max_bytes + 1)
                    peer = connection.connected_peer
                    if not peer:
                        raise UnsafePublicURLError(
                            "unsafe_url: connected peer was not recorded"
                        )
                    if status < 400 or status in _REDIRECT_STATUSES:
                        return data, status, headers, peer
                    if status in _HEADER_RETRY_STATUSES:
                        last_status_error = HTTPError(
                            url,
                            status,
                            "HTTP request failed",
                            headers,
                            None,
                        )
                        continue
                    raise HTTPError(
                        url,
                        status,
                        "HTTP request failed",
                        headers,
                        None,
                    )
                except UnsafePublicURLError:
                    raise
                except HTTPError as exc:
                    if int(getattr(exc, "code", 0) or 0) in _HEADER_RETRY_STATUSES:
                        last_status_error = exc
                        continue
                    raise
                except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                    last_error = exc
                    continue
                finally:
                    connection.close()
            if last_status_error is not None:
                raise last_status_error
        except UnsafePublicURLError:
            raise
        except HTTPError as exc:
            if int(getattr(exc, "code", 0) or 0) in _HEADER_RETRY_STATUSES:
                last_error = exc
                continue
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise UnsafePublicURLError("unsafe_url: no validated public address available")


def _read_public_url(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    method: str = "GET",
    resolver: Callable[[str, int], Iterable[str]] | None = None,
) -> PublicHTTPRead:
    """Read one URL through a public-IP-pinned connection on every hop.

    When the primary host fails with transport errors (or a small set of
    gateway/WAF HTTP codes), retry once against the apex/www sibling host.
    Identity/canonicalization remains apex-stripped elsewhere; this only widens
    fetch success for dual-terminated government sites.
    """

    candidates = _host_fetch_candidates(url)
    if not candidates:
        raise UnsafePublicURLError("unsafe_url: empty URL")

    last_error: Exception | None = None
    for candidate in candidates:
        current = candidate
        redirect_chain: list[str] = []
        try:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                host, port, addresses = _resolve_public_target(
                    current,
                    resolver=resolver,
                )
                data, status, headers, peer_ip = _request_pinned_once(
                    current,
                    host=host,
                    port=port,
                    addresses=addresses,
                    timeout=timeout,
                    max_bytes=max_bytes,
                    method=method,
                )
                if status in _REDIRECT_STATUSES:
                    location = str(headers.get("location") or "").strip()
                    if not location:
                        raise UnsafePublicURLError(
                            "unsafe_url: redirect missing Location"
                        )
                    if redirect_count >= _MAX_REDIRECTS:
                        raise UnsafePublicURLError(
                            "unsafe_url: redirect limit exceeded"
                        )
                    redirect_chain.append(current)
                    current = urljoin(current, location)
                    continue
                if status >= 400:
                    raise HTTPError(
                        current, status, "HTTP request failed", headers, None
                    )
                if candidate != str(url or "") and str(url or ""):
                    # Sibling host is a fetch detour; keep original request URL
                    # visible in the redirect chain for audit.
                    if str(url or "") not in redirect_chain:
                        redirect_chain = [str(url or ""), *redirect_chain]
                return PublicHTTPRead(
                    data=data,
                    final_url=current,
                    headers=headers,
                    peer_ip=peer_ip,
                    redirect_chain=tuple(redirect_chain),
                )
            raise UnsafePublicURLError("unsafe_url: redirect limit exceeded")
        except UnsafePublicURLError:
            # Safety failures must not hop to another host blindly.
            raise
        except HTTPError as exc:
            last_error = exc
            # Retry sibling host only for gateway/WAF-ish codes that often
            # differ between apex and www terminations.
            if int(getattr(exc, "code", 0) or 0) not in {
                403,
                412,
                502,
                503,
                520,
                521,
                522,
                523,
                524,
            }:
                raise
            continue
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise UnsafePublicURLError("unsafe_url: no fetch candidate succeeded")


def _verify_public_url(
    url: str,
    *,
    timeout: float,
    resolver: Callable[[str, int], Iterable[str]] | None = None,
) -> dict[str, str]:
    """Perform a bounded, redirect-safe HEAD verification.

    This deliberately does not call the legacy ``deep_research._verify_url``:
    that compatibility helper follows redirects without revalidating the
    target and therefore cannot be used on untrusted research URLs.
    """

    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not _safe_public_url(url, resolver=resolver):
        return {
            "url_status": "BLOCKED",
            "archive_url": "",
            "url_checked_at": checked_at,
        }
    try:
        response = _read_public_url(
            url,
            timeout=timeout,
            max_bytes=0,
            method="HEAD",
            resolver=resolver,
        )
        return {
            "url_status": "LIVE",
            "archive_url": "",
            "url_checked_at": checked_at,
            "final_url": response.final_url,
            "peer_ip": response.peer_ip,
        }
    except UnsafePublicURLError:
        status = "BLOCKED"
    except HTTPError as exc:
        if exc.code in {400, 404, 410}:
            status = "DEAD"
        elif exc.code in {401, 403, 451}:
            status = "BLOCKED"
        else:
            status = "UNKNOWN"
    except Exception:  # network errors are not proof that a public URL is dead
        status = "UNKNOWN"
    return {
        "url_status": status,
        "archive_url": "",
        "url_checked_at": checked_at,
    }


def extract_pdf_bytes(data: bytes) -> tuple[str, list[dict[str, object]]]:
    """Extract page-preserving PDF text for auditable quote locations."""

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[dict[str, object]] = []
    combined: list[str] = []
    offset = 0
    for page_no, page in enumerate(reader.pages, 1):
        text = clean_extracted_text(page.extract_text() or "")
        if not text:
            continue
        start = offset
        combined.append(text)
        offset += len(text) + 1
        pages.append(
            {
                "page": page_no,
                "text": text,
                "start_offset": start,
                "end_offset": offset - 1,
            }
        )
    return "\n".join(combined).strip(), pages


def extract_pdf_tables(data: bytes, *, max_tables: int = 50) -> list[dict[str, object]]:
    """Extract bounded page-aware PDF tables without executing document code.

    ``pdfplumber`` is optional: table extraction enriches quant claims but must
    not hard-fail whole-document PDF text extraction on free single-machine
    environments where only ``pypdf`` is installed.
    """

    try:
        import pdfplumber
    except ImportError:
        return []

    tables: list[dict[str, object]] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as document:
            for page_no, page in enumerate(document.pages, 1):
                for table in page.extract_tables() or []:
                    rows = [
                        [str(cell or "").strip() for cell in row]
                        for row in table[:200]
                        if row
                    ]
                    if rows:
                        tables.append({"page": page_no, "rows": rows})
                    if len(tables) >= max_tables:
                        return tables
    except Exception:
        # Table parse failures are non-fatal; text/pages already extracted.
        return tables
    return tables


def extract_pdf_ocr(
    data: bytes,
    *,
    max_pages: int = 20,
) -> tuple[str, list[dict[str, object]]]:
    """OCR scanned PDF pages with bounded rendering and page provenance."""

    try:
        import fitz
        import numpy as np
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            'scanned PDF OCR requires pip install -e ".[deep-research-ocr]"'
        ) from exc

    engine = RapidOCR()
    document = fitz.open(stream=data, filetype="pdf")
    pages: list[dict[str, object]] = []
    combined: list[str] = []
    offset = 0
    try:
        for page_index in range(min(len(document), max(1, int(max_pages)))):
            page = document[page_index]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
            result, _ = engine(np.asarray(image))
            lines = [
                str(item[1]).strip()
                for item in (result or [])
                if len(item) >= 3 and str(item[1]).strip() and float(item[2]) >= 0.45
            ]
            text = clean_extracted_text("\n".join(lines))
            if not text:
                continue
            start = offset
            combined.append(text)
            offset += len(text) + 1
            pages.append(
                {
                    "page": page_index + 1,
                    "text": text,
                    "start_offset": start,
                    "end_offset": offset - 1,
                    "ocr": True,
                }
            )
    finally:
        document.close()
    return "\n".join(combined).strip(), pages


def _url_status_for_error(message: str) -> str:
    low = str(message or "").lower()
    if any(
        token in low
        for token in ("unsafe_url", "non-public", "private address", "loopback")
    ):
        return "BLOCKED"
    if any(token in low for token in ("404", "410", "not found", "gone")):
        return "DEAD"
    if any(
        token in low
        for token in ("401", "403", "forbidden", "login", "paywall", "robots", "captcha")
    ):
        return "BLOCKED"
    return "UNKNOWN"


def _decode_http_text(data: bytes, content_type: str) -> str:
    declared = ""
    match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.IGNORECASE)
    if match:
        declared = match.group(1).strip()
    encodings = list(dict.fromkeys(item for item in (declared, "utf-8", "gb18030") if item))
    candidates: list[tuple[int, str]] = []
    for encoding in encodings:
        try:
            decoded = data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        candidates.append((decoded.count("\ufffd"), decoded))
    if candidates:
        return min(candidates, key=lambda item: item[0])[1]
    return data.decode("utf-8", errors="replace")


def _extract_visible_html(data: bytes, content_type: str) -> tuple[str, str]:
    decoded = _decode_http_text(data, content_type)
    parser = _ResearchHTMLParser()
    parser.feed(decoded)
    parser.close()
    return clean_extracted_text(parser.text), parser.title


class SourceExtractor:
    """Extract each source through available providers with bounded fallback."""

    def __init__(
        self,
        providers: Iterable[Any] | None = None,
        *,
        call_timeout_seconds: float = 45.0,
        pdf_download_timeout_seconds: float = 35.0,
        enable_cache: bool | None = None,
        cache: ExtractionCache | None = None,
        dns_resolver: Callable[[str, int], Iterable[str]] | None = None,
        enable_direct_http_fallback: bool = True,
        isolate_sync_providers: bool = False,
    ) -> None:
        discovered = providers is None
        self._providers = list(providers) if providers is not None else self._discover_providers()
        self.call_timeout_seconds = max(0.001, float(call_timeout_seconds))
        self.pdf_download_timeout_seconds = max(0.1, float(pdf_download_timeout_seconds))
        cache_enabled = discovered if enable_cache is None else bool(enable_cache)
        self.cache = (cache or _DEFAULT_EXTRACTION_CACHE) if cache_enabled else None
        self.dns_resolver = dns_resolver
        self.enable_direct_http_fallback = bool(enable_direct_http_fallback)
        self.isolate_sync_providers = bool(isolate_sync_providers)
        self._sync_executors: dict[int, SyncProviderProcess] = {}
        self._sync_executor_workers = 1

    @staticmethod
    def _discover_providers() -> list[Any]:
        # MCP 独立化：不再从 agent/tools 侧加载 provider 注册表；域内只提供
        # tavily 函数式 provider（domains/research/providers/tavily.py），
        # 提取统一走 direct-HTTP 降级路径（enable_direct_http_fallback）。
        return []

    async def extract_many(
        self,
        sources: list[SourceRecord],
        *,
        concurrency: int = 4,
        max_calls: int | None = None,
        hints_by_node: dict[str, str] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[ExtractRecord]:
        limit = len(sources) if max_calls is None else max(0, min(len(sources), int(max_calls)))
        semaphore = asyncio.Semaphore(max(1, int(concurrency)))

        async def one(source: SourceRecord) -> ExtractRecord:
            async with semaphore:
                if should_cancel and should_cancel():
                    return ExtractRecord(
                        source.source_id,
                        source.url,
                        status="blocked",
                        error_code="cancelled",
                    )
                result = await self._extract_one(
                    source,
                    hints_by_node or {},
                    should_cancel=should_cancel,
                )
                source.extract_status = result.status
                if result.status == "ok":
                    source.url_status = "LIVE"
                    source.url_checked_at = source.url_checked_at or time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(),
                    )
                    enrich_source_metadata(
                        source,
                        content=result.content,
                        title=result.title,
                        metadata=result.metadata,
                    )
                else:
                    source.url_status = _url_status_for_error(
                        result.error_message or result.error_code
                    )
                    if source.url_status == "UNKNOWN":
                        verified = await asyncio.to_thread(
                            _verify_public_url,
                            source.url,
                            timeout=2.0,
                            resolver=self.dns_resolver,
                        )
                        source.url_status = str(
                            verified.get("url_status") or "UNKNOWN"
                        )
                        source.archive_url = str(
                            verified.get("archive_url") or ""
                        )
                        source.url_checked_at = str(
                            verified.get("url_checked_at") or ""
                        )
                if result.content_hash:
                    source.content_hash = result.content_hash
                return result

        self._sync_executor_workers = max(1, int(concurrency))
        try:
            first = list(
                await asyncio.gather(*(one(source) for source in sources[:limit]))
            )
            # One retry for high-value gov/PDF failures (free path often flaky).
            retry_sources: list[SourceRecord] = []
            by_id = {s.source_id: s for s in sources[:limit]}
            for record in first:
                if record.status == "ok":
                    continue
                source = by_id.get(record.source_id)
                if source is None:
                    continue
                domain = str(source.registrable_domain or "").lower()
                url = str(source.url or "").lower()
                ctype = str(source.content_type or "").lower()
                if (
                    "gov.cn" in domain
                    or "gov.cn" in url
                    or "stats.gov" in domain
                    or ctype == "application/pdf"
                    or url.endswith(".pdf")
                    or str(source.source_id).startswith(("src_govwl_", "src_stats_"))
                ):
                    # reset status for retry attempt
                    source.extract_status = "not_run"
                    retry_sources.append(source)
            if retry_sources:
                # Slightly more patience on retry.
                old_pdf_timeout = self.pdf_download_timeout_seconds
                old_call_timeout = self.call_timeout_seconds
                try:
                    self.pdf_download_timeout_seconds = max(old_pdf_timeout, 50.0)
                    self.call_timeout_seconds = max(old_call_timeout, 55.0)
                    second = list(
                        await asyncio.gather(*(one(source) for source in retry_sources))
                    )
                finally:
                    self.pdf_download_timeout_seconds = old_pdf_timeout
                    self.call_timeout_seconds = old_call_timeout
                second_by_id = {item.source_id: item for item in second}
                first = [
                    second_by_id.get(item.source_id, item)
                    if item.status != "ok"
                    else item
                    for item in first
                ]
            return first
        finally:
            executors = list(self._sync_executors.values())
            self._sync_executors.clear()
            if executors:
                await asyncio.gather(
                    *(executor.aclose() for executor in executors),
                    return_exceptions=True,
                )

    async def _extract_one(
        self,
        source: SourceRecord,
        hints_by_node: dict[str, str],
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ExtractRecord:
        cache_key = source.canonical_url or source.url
        cached = self.cache.get(cache_key) if self.cache else None
        if cached:
            cached["source_id"] = source.source_id
            cached["url"] = source.url
            cached.setdefault("metadata", {})["cache_hit"] = True
            return ExtractRecord(**cached)
        if not _safe_public_url(source.url, resolver=self.dns_resolver):
            return ExtractRecord(
                source.source_id,
                source.url,
                status="blocked",
                error_code="unsafe_url",
                error_message="URL is not an allowed public HTTP(S) target",
            )
        errors: list[str] = []
        # Free single-machine path: direct HTTP extract is more reliable than
        # dead paid extract APIs (e.g. Tavily 432). Prefer it when no extract
        # providers, or when DR_EXTRACT_DIRECT_FIRST=1.
        import os as _os

        direct_first = (
            not self._providers
            or _os.environ.get("DR_EXTRACT_DIRECT_FIRST", "1").strip() != "0"
        )
        if direct_first and self.enable_direct_http_fallback:
            try:
                if source.content_type == "application/pdf" or urlparse(source.url).path.lower().endswith(".pdf"):
                    result = await asyncio.wait_for(
                        asyncio.to_thread(self._extract_pdf_url, source, hints_by_node),
                        timeout=self.pdf_download_timeout_seconds + 5.0,
                    )
                else:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(self._extract_html_url, source, hints_by_node),
                        timeout=self.pdf_download_timeout_seconds + 5.0,
                    )
                if self.cache:
                    self.cache.set(cache_key, result)
                return result
            except UnsafePublicURLError as exc:
                return ExtractRecord(
                    source.source_id,
                    source.url,
                    status="blocked",
                    error_code="unsafe_url",
                    error_message=str(exc),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"direct_http_first: {exc}")
        for provider in self._providers:
            if should_cancel and should_cancel():
                return ExtractRecord(
                    source.source_id,
                    source.url,
                    status="blocked",
                    error_code="cancelled",
                    error_message="Research cancellation requested",
                )
            is_available = getattr(provider, "is_available", None)
            if callable(is_available):
                try:
                    if not is_available():
                        errors.append(f"{provider.name}: provider_unavailable")
                        continue
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{provider.name}: availability_check_failed: {exc}")
                    continue
            try:
                kwargs = {"include_raw": True, "extract_depth": "advanced"}
                if inspect.iscoroutinefunction(provider.extract):
                    call = provider.extract([source.url], **kwargs)
                    payload = await asyncio.wait_for(
                        call,
                        timeout=self.call_timeout_seconds,
                    )
                elif self.isolate_sync_providers:
                    key = id(provider)
                    executor = self._sync_executors.get(key)
                    if executor is None:
                        executor = SyncProviderProcess(
                            provider,
                            max_workers=self._sync_executor_workers,
                        )
                        self._sync_executors[key] = executor
                    payload = await executor.call(
                        "extract",
                        [source.url],
                        timeout=self.call_timeout_seconds,
                        should_cancel=should_cancel,
                        **kwargs,
                    )
                else:
                    call = asyncio.to_thread(provider.extract, [source.url], **kwargs)
                    payload = await asyncio.wait_for(
                        call,
                        timeout=self.call_timeout_seconds,
                    )
                documents = payload.get("data", []) if isinstance(payload, dict) else payload
                document = next(
                    (item for item in (documents or []) if isinstance(item, dict)),
                    None,
                )
                if not document or document.get("error"):
                    errors.append(str((document or {}).get("error") or "empty extraction"))
                    continue
                metadata = dict(document.get("metadata") or {})
                returned_urls = [
                    str(value).strip()
                    for value in (
                        document.get("url"),
                        metadata.get("final_url"),
                        metadata.get("sourceURL"),
                    )
                    if str(value or "").strip()
                ]
                if not returned_urls:
                    return ExtractRecord(
                        source.source_id,
                        source.url,
                        status="blocked",
                        error_code="unsafe_url",
                        error_message=(
                            "unsafe_url: extraction provider omitted the final URL"
                        ),
                    )
                if any(
                    not _safe_public_url(value, resolver=self.dns_resolver)
                    for value in returned_urls
                ):
                    return ExtractRecord(
                        source.source_id,
                        source.url,
                        status="blocked",
                        error_code="unsafe_url",
                        error_message=(
                            "unsafe_url: extraction provider returned a non-public target"
                        ),
                    )
                content = clean_extracted_text(
                    str(document.get("raw_content") or document.get("content") or "")
                )
                content, safety_findings = sanitize_untrusted_text(content)
                if len(content) < 80:
                    errors.append("extracted content shorter than 80 characters")
                    continue
                hints = [hints_by_node.get(node_id, "") for node_id in source.plan_node_ids]
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                result = ExtractRecord(
                    source_id=source.source_id,
                    url=source.url,
                    status="ok",
                    title=str(document.get("title") or source.title),
                    content=content,
                    chunks=relevant_chunks(content, hints=hints),
                    content_hash=digest,
                    metadata={
                        "provider": provider.name,
                        "safety_findings": safety_findings,
                        **metadata,
                    },
                )
                if self.cache:
                    self.cache.set(cache_key, result)
                return result
            except SyncProviderCallCancelled as exc:
                return ExtractRecord(
                    source.source_id,
                    source.url,
                    status="blocked",
                    error_code="cancelled",
                    error_message=str(exc),
                )
            except SyncProviderCallTimeout as exc:
                errors.append(f"{provider.name}: provider_timeout: {exc}")
            except SyncProviderIsolationUnavailable as exc:
                errors.append(f"{provider.name}: provider_process_isolation_unavailable: {exc}")
            except SyncProviderProcessTerminated as exc:
                errors.append(f"{provider.name}: provider_process_terminated: {exc}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{provider.name}: {exc}")
        if should_cancel and should_cancel():
            return ExtractRecord(
                source.source_id,
                source.url,
                status="blocked",
                error_code="cancelled",
                error_message="Research cancellation requested",
            )
        if not direct_first and (
            source.content_type == "application/pdf"
            or urlparse(source.url).path.lower().endswith(".pdf")
        ):
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._extract_pdf_url, source, hints_by_node),
                    timeout=self.pdf_download_timeout_seconds + 5.0,
                )
                if self.cache:
                    self.cache.set(cache_key, result)
                return result
            except UnsafePublicURLError as exc:
                return ExtractRecord(
                    source.source_id,
                    source.url,
                    status="blocked",
                    error_code="unsafe_url",
                    error_message=str(exc),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"pdf_fallback: {exc}")
        elif not direct_first and self.enable_direct_http_fallback:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._extract_html_url, source, hints_by_node),
                    timeout=self.pdf_download_timeout_seconds + 5.0,
                )
                if self.cache:
                    self.cache.set(cache_key, result)
                return result
            except UnsafePublicURLError as exc:
                return ExtractRecord(
                    source.source_id,
                    source.url,
                    status="blocked",
                    error_code="unsafe_url",
                    error_message=str(exc),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"direct_http_fallback: {exc}")
        if not self._providers and not errors:
            errors.append("No available extraction provider")
        message = "; ".join(errors)[:500]
        message, _ = redact_sensitive_text(message)
        status = "blocked" if _url_status_for_error(message) == "BLOCKED" else "failed"
        return ExtractRecord(
            source.source_id,
            source.url,
            status=status,
            error_code="extract_blocked" if status == "blocked" else "extract_failed",
            error_message=message,
        )

    def _extract_pdf_url(
        self,
        source: SourceRecord,
        hints_by_node: dict[str, str],
    ) -> ExtractRecord:
        response = _read_public_url(
            source.url,
            timeout=self.pdf_download_timeout_seconds,
            max_bytes=25 * 1024 * 1024,
            resolver=self.dns_resolver,
        )
        data = response.data
        if len(data) > 25 * 1024 * 1024:
            raise ValueError("PDF exceeds 25 MiB extraction limit")
        content, pages = extract_pdf_bytes(data)
        ocr_used = False
        if len(content) < 80:
            content, pages = extract_pdf_ocr(data)
            ocr_used = True
        tables = extract_pdf_tables(data)
        content, safety_findings = sanitize_untrusted_text(content)
        if len(content) < 80:
            raise ValueError("PDF text extraction returned insufficient content")
        hints = [hints_by_node.get(node_id, "") for node_id in source.plan_node_ids]
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ExtractRecord(
            source_id=source.source_id,
            url=source.url,
            status="ok",
            title=source.title,
            content=content,
            chunks=relevant_chunks(content, hints=hints),
            content_hash=digest,
            metadata={
                "provider": "pypdf",
                "final_url": response.final_url,
                "connected_peer_ip": response.peer_ip,
                "redirect_chain": list(response.redirect_chain),
                "content_type": "application/pdf",
                "pages": pages,
                "page_count": len(pages),
                "tables": tables,
                "table_count": len(tables),
                "ocr_used": ocr_used,
                "safety_findings": safety_findings,
            },
        )

    def _extract_html_url(
        self,
        source: SourceRecord,
        hints_by_node: dict[str, str],
    ) -> ExtractRecord:
        response = _read_public_url(
            source.url,
            timeout=self.pdf_download_timeout_seconds,
            max_bytes=5 * 1024 * 1024,
            resolver=self.dns_resolver,
        )
        if len(response.data) > 5 * 1024 * 1024:
            raise ValueError("HTML response exceeds 5 MiB extraction limit")
        content_type = str(response.headers.get("content-type") or "").lower()
        if content_type and not any(
            allowed in content_type
            for allowed in ("text/html", "application/xhtml+xml", "text/plain")
        ):
            raise ValueError(f"unsupported direct extraction content type: {content_type[:80]}")
        content, html_title = _extract_visible_html(response.data, content_type)
        content, safety_findings = sanitize_untrusted_text(content)
        if len(content) < 80:
            raise ValueError("direct HTML extraction returned insufficient content")
        hints = [hints_by_node.get(node_id, "") for node_id in source.plan_node_ids]
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ExtractRecord(
            source_id=source.source_id,
            url=source.url,
            status="ok",
            title=html_title or source.title,
            content=content,
            chunks=relevant_chunks(content, hints=hints),
            content_hash=digest,
            metadata={
                "provider": "direct_http_pinned",
                "final_url": response.final_url,
                "connected_peer_ip": response.peer_ip,
                "redirect_chain": list(response.redirect_chain),
                "content_type": content_type,
                "safety_findings": safety_findings,
            },
        )
