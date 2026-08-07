"""lvke-data-acquisition service 拆分：URL 规范化、域名规则与密钥检测。

这些工具被搜索、发现、外部快照导入、抓取与审计共用，放独立模块避免
子模块间互相依赖形成环。
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


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