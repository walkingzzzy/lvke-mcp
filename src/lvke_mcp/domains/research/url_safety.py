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

# 198.18.0.0/15（RFC 2544 基准测试段）：Python 的 is_private 覆盖它，所以公网域名
# 在 Clash/Surge 等 fake-ip 代理下会被判成 "private"。仍然拦截——本进程无法区分
# 「代理伪造地址」和「真有服务跑在该段」——但单列一个分类,让错误响应能说出根因,
# 而不是把代理配置问题伪装成「URL 指向内网」。放行需显式白名单或改走受信提取。
_PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")

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


def is_always_blocked_address(value: str) -> bool:
    """True 表示该地址属于云 metadata / 凭据端点，无条件拒绝。

    单一权威名单：Gate B(extractor) 复用本函数而不复制常量，否则两处名单会漂移，
    而漂移的那一侧就是 SSRF 缺口 —— 主机名白名单绝不能放行 metadata 端点。
    """

    try:
        ip = ipaddress.ip_address(str(value or "").split("%", 1)[0])
    except ValueError:
        return False
    candidate = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
    for checked in {ip, candidate} - {None}:
        if checked in _ALWAYS_BLOCKED_IPS or any(
            checked in net for net in _ALWAYS_BLOCKED_NETWORKS
        ):
            return True
    return False


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
    if checked in _PROXY_FAKE_IP_NETWORK:
        # 仍不放行，但与真实内网地址区分开，供调用方定位代理 fake-ip 场景。
        return "proxy_fake_ip"
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
    blocking = [
        item for item in addresses if not item.get("allowed")
    ]
    fake_ip_blocked = bool(blocking) and all(
        item.get("classification") == "proxy_fake_ip" for item in blocking
    )
    if base["allowed"]:
        base["code"] = (
            "allowed_exact_https_proxy_host"
            if allow_exact_proxy_host
            else "allowed_public"
        )
    elif any(item.get("classification") == "cloud_metadata" for item in blocking):
        # 云 metadata 命中必须自报家门：调用方据此知道这是无条件拒绝，
        # 不存在任何白名单或开关能放行。
        base["code"] = "cloud_metadata_resolution"
        base["detail"] = f"{hostname} 解析到云 metadata 端点 " + "、".join(
            str(item.get("address")) for item in blocking
            if item.get("classification") == "cloud_metadata"
        ) + "；无条件拒绝，不受任何放行开关影响。"
    elif fake_ip_blocked:
        # 唯一阻断原因是代理 fake-ip 段：这是本机代理配置问题，不是目标站点内网。
        base["code"] = "proxy_fake_ip_resolution"
        base["detail"] = (
            f"{hostname} 解析到 "
            + "、".join(str(item.get("address")) for item in blocking)
            + "（198.18.0.0/15，RFC 2544 基准测试段）；通常是本机代理 fake-ip 映射，"
            "而非目标站点位于内网。本地直连无法校验真实公网地址，因此仍然拦截。"
        )
        base["remediation"] = [
            "改用 extraction_provider=tavily（受信提取由 provider 侧出网，不经本地 DNS）",
            f"或将 {hostname} 加入 LVKE_MCP_TRUSTED_HTTPS_PRIVATE_IP_HOSTS（仅 HTTPS 生效）",
            "或关闭代理 fake-ip 模式（Clash/Surge 改 redir-host），使 DNS 返回真实公网 IP",
        ]
    else:
        base["code"] = "private_or_internal_resolution"
        if blocking:
            base["detail"] = f"{hostname} 解析到 " + "、".join(
                f"{item.get('address')}({item.get('classification')})"
                for item in blocking
            )
    return base


async def async_url_safety_decision(url: str) -> dict[str, object]:
    return await asyncio.to_thread(url_safety_decision, url)