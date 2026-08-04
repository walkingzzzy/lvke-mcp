"""URL 安全工具 —— MCP 自有实现（自 agent/redact._PREFIX_RE 与 tools/url_safety 裁剪）。

仅保留被引安全子集：密钥前缀探测（``_PREFIX_RE``）、URL 请求规范化
（``normalize_url_for_request``）与 SSRF 判定（``url_safety_decision`` /
``async_url_safety_decision``）。行为与 hermes 侧一致，不读任何宿主配置；
私网放行开关仅认 ``LVKE_MCP_ALLOW_PRIVATE_URLS`` 环境变量。
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

# ── 密钥前缀模式（agent.redact._PREFIX_PATTERNS 原样复制）──
_PREFIX_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{10,}",           # OpenAI / OpenRouter / Anthropic (sk-ant-*)
    r"ghp_[A-Za-z0-9]{10,}",            # GitHub PAT (classic)
    r"github_pat_[A-Za-z0-9_]{10,}",    # GitHub PAT (fine-grained)
    r"gho_[A-Za-z0-9]{10,}",            # GitHub OAuth access token
    r"ghu_[A-Za-z0-9]{10,}",            # GitHub user-to-server token
    r"ghs_[A-Za-z0-9]{10,}",            # GitHub server-to-server token
    r"ghr_[A-Za-z0-9]{10,}",            # GitHub refresh token
    r"xox[baprs]-[A-Za-z0-9-]{10,}",    # Slack tokens
    r"AIza[A-Za-z0-9_-]{30,}",          # Google API keys
    r"pplx-[A-Za-z0-9]{10,}",           # Perplexity
    r"fal_[A-Za-z0-9_-]{10,}",          # Fal.ai
    r"fc-[A-Za-z0-9]{10,}",             # Firecrawl
    r"bb_live_[A-Za-z0-9_-]{10,}",      # BrowserBase
    r"gAAAA[A-Za-z0-9_=-]{20,}",        # Codex encrypted tokens
    r"AKIA[A-Z0-9]{16}",                # AWS Access Key ID
    r"sk_live_[A-Za-z0-9]{10,}",        # Stripe secret key (live)
    r"sk_test_[A-Za-z0-9]{10,}",        # Stripe secret key (test)
    r"rk_live_[A-Za-z0-9]{10,}",        # Stripe restricted key
    r"SG\.[A-Za-z0-9_-]{10,}",          # SendGrid API key
    r"hf_[A-Za-z0-9]{10,}",             # HuggingFace token
    r"r8_[A-Za-z0-9]{10,}",             # Replicate API token
    r"npm_[A-Za-z0-9]{10,}",            # npm access token
    r"pypi-[A-Za-z0-9_-]{10,}",         # PyPI API token
    r"dop_v1_[A-Za-z0-9]{10,}",         # DigitalOcean PAT
    r"doo_v1_[A-Za-z0-9]{10,}",         # DigitalOcean OAuth
    r"am_[A-Za-z0-9_-]{10,}",           # AgentMail API key
    r"sk_[A-Za-z0-9_]{10,}",            # ElevenLabs TTS key (sk_ underscore, not sk- dash)
    r"tvly-[A-Za-z0-9]{10,}",           # Tavily search API key
    r"exa_[A-Za-z0-9]{10,}",            # Exa search API key
    r"gsk_[A-Za-z0-9]{10,}",            # Groq Cloud API key
    r"syt_[A-Za-z0-9]{10,}",            # Matrix access token
    r"retaindb_[A-Za-z0-9]{10,}",       # RetainDB API key
    r"hsk-[A-Za-z0-9]{10,}",            # Hindsight API key
    r"mem0_[A-Za-z0-9]{10,}",           # Mem0 Platform API key
    r"brv_[A-Za-z0-9]{10,}",            # ByteRover API key
    r"xai-[A-Za-z0-9]{30,}",            # xAI (Grok) API key
    r"ntn_[A-Za-z0-9]{10,}",            # Notion internal integration token
]

# Compile known prefix patterns into one alternation
_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(_PREFIX_PATTERNS) + r")(?![A-Za-z0-9_-])"
)


def normalize_url_for_request(url: str) -> str:
    """Return an ASCII-safe HTTP URL for MCP-owned URL tools.

    Browsers and HTTP clients expect URIs, but users and models often provide
    IRIs such as ``https://wttr.in/Köln``.  Preserve URL syntax and existing
    percent escapes while encoding non-ASCII host/path/query/fragment text.
    This is intentionally for URL tool inputs only; arbitrary shell commands
    must not be rewritten.
    """
    if not isinstance(url, str):
        return url

    raw = url.strip()
    if not raw:
        return raw

    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw

    if parsed.scheme.lower() not in {"http", "https"}:
        return raw

    netloc = parsed.netloc
    hostname = parsed.hostname
    if hostname:
        try:
            ascii_host = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            ascii_host = hostname
        if ascii_host != hostname:
            netloc = netloc.replace(hostname, ascii_host, 1)

    path = quote(parsed.path, safe="/%:@!$&'()*+,;=")
    query = quote(parsed.query, safe="/%:@!$&'()*+,;=?")
    fragment = quote(parsed.fragment, safe="/%:@!$&'()*+,;=?")

    return urlunsplit((parsed.scheme, netloc, path, query, fragment))


# ── SSRF 判定（tools/url_safety.py 原样复制，去掉 hermes config 依赖）──

# 云 metadata 端点主机名：无论 IP 解析结果或放行开关，永远拦截。
_BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
})

# 云 metadata / 凭据端点 IP 与网段（SSRF 头号目标）；含 IPv4-mapped IPv6 变体。
_ALWAYS_BLOCKED_IPS = frozenset({
    ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure/DO/Oracle metadata
    ipaddress.ip_address("169.254.170.2"),     # AWS ECS task metadata
    ipaddress.ip_address("169.254.169.253"),   # Azure IMDS wire server
    ipaddress.ip_address("fd00:ec2::254"),     # AWS metadata (IPv6)
    ipaddress.ip_address("100.100.100.200"),   # Alibaba Cloud metadata
    ipaddress.ip_address("::ffff:169.254.169.254"),
    ipaddress.ip_address("::ffff:169.254.170.2"),
    ipaddress.ip_address("::ffff:169.254.169.253"),
    ipaddress.ip_address("::ffff:100.100.100.200"),
})
_ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),          # 整个 link-local 段
    ipaddress.ip_network("::ffff:169.254.0.0/112"),  # IPv4-mapped link-local 段
)

# 100.64.0.0/10（CGNAT，RFC 6598）：ipaddress 的 is_private 不覆盖，必须显式拦截。
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# 精确 HTTPS 主机名白名单：允许解析到私网/benchmark 段（刻意收窄）。
_TRUSTED_PRIVATE_IP_HOSTS = frozenset({
    "multimedia.nt.qq.com.cn",
})

_allow_private_resolved = False
_cached_allow_private: bool = False


def _global_allow_private_urls() -> bool:
    """MCP 自有私网放行开关（默认拦截；仅认 LVKE_MCP_ALLOW_PRIVATE_URLS）。"""
    global _allow_private_resolved, _cached_allow_private
    if _allow_private_resolved:
        return _cached_allow_private
    _allow_private_resolved = True
    _cached_allow_private = False  # 安全默认
    env_val = os.getenv("LVKE_MCP_ALLOW_PRIVATE_URLS", "").strip().lower()
    if env_val in {"true", "1", "yes"}:
        _cached_allow_private = True
    return _cached_allow_private


def _configured_trusted_private_ip_hosts() -> frozenset[str]:
    """精确主机名白名单：内置集 + ``LVKE_MCP_TRUSTED_HTTPS_PRIVATE_IP_HOSTS``。"""
    hosts = set(_TRUSTED_PRIVATE_IP_HOSTS)
    hosts.update(
        item.strip().lower().rstrip(".")
        for item in os.getenv("LVKE_MCP_TRUSTED_HTTPS_PRIVATE_IP_HOSTS", "").split(",")
        if item.strip()
    )
    return frozenset(hosts)


def _allows_private_ip_resolution(hostname: str, scheme: str) -> bool:
    return scheme == "https" and hostname in _configured_trusted_private_ip_hosts()


def _ip_classification(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    candidate = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
    checked = candidate or ip
    if ip in _ALWAYS_BLOCKED_IPS or any(ip in net for net in _ALWAYS_BLOCKED_NETWORKS):
        return "cloud_metadata"
    if checked.is_loopback:
        return "loopback"
    if checked.is_link_local:
        return "link_local"
    if checked in _CGNAT_NETWORK:
        return "cgnat"
    if checked.is_private:
        return "private"
    if checked.is_multicast:
        return "multicast"
    if checked.is_unspecified:
        return "unspecified"
    if checked.is_reserved:
        return "reserved"
    return "public"


def url_safety_decision(url: str) -> dict[str, object]:
    """返回可审计的 DNS 分类结果，不暴露解析器内部实现。"""
    normalized = normalize_url_for_request(str(url or ""))
    try:
        parsed = urlparse(normalized)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = (parsed.scheme or "").strip().lower()
    except Exception as exc:
        return {
            "allowed": False, "code": "url_parse_failed", "hostname": "",
            "scheme": "", "addresses": [], "detail": type(exc).__name__,
        }
    base: dict[str, object] = {
        "allowed": False,
        "code": "url_rejected",
        "hostname": hostname,
        "scheme": scheme,
        "addresses": [],
        "redirect_hop": 0,
    }
    if scheme not in {"http", "https"}:
        base["code"] = "unsupported_scheme"
        return base
    if not hostname:
        base["code"] = "hostname_missing"
        return base
    if hostname in _BLOCKED_HOSTNAMES:
        base["code"] = "cloud_metadata_hostname"
        return base
    try:
        addr_info = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except socket.gaierror:
        base["code"] = "dns_resolution_failed"
        return base
    except Exception as exc:
        base["code"] = "dns_resolution_error"
        base["detail"] = type(exc).__name__
        return base

    allow_all_private = _global_allow_private_urls()
    allow_exact_proxy_host = _allows_private_ip_resolution(hostname, scheme)
    addresses: list[dict[str, object]] = []
    blocked = False
    for family, _, _, _, sockaddr in addr_info:
        ip_text = str(sockaddr[0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            addresses.append({
                "address": ip_text, "family": int(family),
                "classification": "unparseable", "allowed": False,
            })
            blocked = True
            continue
        classification = _ip_classification(ip)
        address_allowed = classification == "public"
        if classification != "cloud_metadata" and (
            allow_all_private or allow_exact_proxy_host
        ):
            address_allowed = True
        addresses.append({
            "address": str(ip), "family": int(family),
            "classification": classification, "allowed": address_allowed,
        })
        blocked = blocked or not address_allowed
    base["addresses"] = addresses
    base["proxy_hostname_allowlisted"] = allow_exact_proxy_host
    base["global_private_override"] = allow_all_private
    base["allowed"] = bool(addresses) and not blocked
    base["code"] = (
        "allowed_exact_https_proxy_host"
        if base["allowed"] and allow_exact_proxy_host
        else "allowed_public"
        if base["allowed"]
        else "private_or_internal_resolution"
    )
    return base


async def async_url_safety_decision(url: str) -> dict[str, object]:
    return await asyncio.to_thread(url_safety_decision, url)