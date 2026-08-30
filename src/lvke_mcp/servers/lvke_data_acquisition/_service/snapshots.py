"""lvke-data-acquisition service 拆分：采集、外部快照导入与安全抓取。"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import time
from urllib.parse import urlsplit
from typing import Any

from lvke_mcp.adapters.data_acquisition_repository import COLLECTION_STORE, SOURCE_STORE
from lvke_mcp.runtime.storage import utc_now

from .constants import (
    _ALLOWED_EXTERNAL_EXTRACT_TOOLS,
    _SEARCH_PROVIDER,
    DISCOVERY_STORE,
    external_receipt_secret,
)
from .urls import _secret_block_reason


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
    if (
        lowered in {"localhost", "metadata.google.internal"}
        or lowered.endswith((".localhost", ".internal", ".local"))
    ):
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
    browser_snapshot = (provider_key, tool_key) == (
        "codex-browser",
        "browser_snapshot",
    )
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
        secret = external_receipt_secret()
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
    formal_use_allowed = bool(receipt_verified and not browser_snapshot)
    content_origin = (
        "codex_browser_snapshot"
        if browser_snapshot
        else "external_mcp_extract"
    )
    payload = {
        "url": str(url).strip(),
        "title": str(title or "").strip(),
        "content": normalized_content,
        "content_mode": "external_extract",
        "content_kind": content_kind,
        "content_origin": content_origin,
        "mime_type": str(mime_type or "text/markdown").strip().lower(),
        "mime_type_source": "external_provider_declared",
        "provider": str(provider).strip(),
        "provider_tool": str(provider_tool).strip(),
        "retrieved_at": str(retrieved_at).strip(),
        "extraction_receipt_verified": receipt_verified,
        "external_content_hash": computed_hash,
        "formal_use_allowed": formal_use_allowed,
        "evidence_policy": "candidate" if not formal_use_allowed else "formal_evidence",
        "evidence_eligibility": "candidate" if not formal_use_allowed else "formal_evidence",
        "project_fact_certified": False,
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
        "formal_use_allowed": formal_use_allowed,
        "evidence_policy": payload["evidence_policy"],
        "project_fact_certified": False,
        "resource_uris": [record["resource_uri"]],
        "warnings": ([
            "Codex 浏览器正文已固化为非正式候选快照；浏览器会话不是证据签发方，禁止自动升级为正式证据"
        ] if browser_snapshot else [
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
    if str(decision.get("code") or "") == "proxy_fake_ip_resolution":
        # 把根因摆在 message 里：粗粒度的「指向私网」会让排查方向系统性跑偏。
        detail = str(decision.get("detail") or "")
        return f"已拦截：{detail or '目标域名解析到代理 fake-ip 段（198.18.0.0/15）'}", decision
    detail = str(decision.get("detail") or "")
    suffix = f"（{detail}）" if detail else ""
    return f"已拦截：URL 指向私网/内部网络地址或无法安全解析{suffix}", decision


def _blocked_next_actions(snapshots: list[dict[str, Any]]) -> list[str]:
    """Surface the security gate's own remediation instead of a generic hint."""

    actions: list[str] = []
    for item in snapshots:
        if item.get("status") != "blocked":
            continue
        decision = item.get("security_decision")
        if not isinstance(decision, dict):
            continue
        for step in decision.get("remediation") or []:
            text = str(step).strip()
            if text and text not in actions:
                actions.append(text)
    return actions or ["移除 URL 中携带的密钥或改用公网可达地址后重试"]


async def _trusted_tavily_extract(
    urls: list[str],
    output_format: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Use the server-owned Tavily adapter and sign a receipt for each body.

    返回 ``(results, diagnosis)``。``diagnosis`` 为 None 表示成功；否则是失败
    原因码。调用方必须据此区分**本地配置缺口**与**真实上游故障**——两者的
    status、retryable 和处置动作完全不同，混为一谈会把本地部署问题伪装成
    第三方故障，让运维查错方向跑偏（NEW-P1-A）。
    """

    secret = external_receipt_secret()
    if not secret:
        # 本地配置缺口：尚未发起任何网络调用，不能归因于 Tavily。
        return [], "receipt_secret_unconfigured"
    try:
        from lvke_mcp.domains.research.providers import tavily as tavily_provider

        if not tavily_provider.configured_transport():
            # 同样是本地配置缺口：provider 传输层未配置。
            return [], "provider_transport_unconfigured"
        extracted = await tavily_provider.tavily_extract(urls, output_format)
        if not extracted:
            return [], "provider_returned_empty"
    except Exception:  # noqa: BLE001
        return [], "provider_call_failed"
    results: list[dict[str, Any]] = []
    for item in extracted if isinstance(extracted, list) else []:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized["provider"] = _SEARCH_PROVIDER
        content = str(item.get("content") or item.get("raw_content") or "")
        if content:
            # Tavily's native extract response calls the body ``raw_content``;
            # normalize it before the common fetch path validates the payload.
            normalized["content"] = content
            retrieved_at = utc_now()
            content_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
            receipt = {
                "provider": _SEARCH_PROVIDER,
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
    return results, None


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
    diagnosis: str | None = None
    if checked_urls:
        if extraction_provider in {"auto", "tavily"}:
            results, diagnosis = await _trusted_tavily_extract(checked_urls, output_format)
            trusted_tavily = bool(results)
        if not results and extraction_provider in {"auto", "tavily"}:
            # NEW-P1-A 修复：本地配置缺口不应归因为 upstream_failure。
            if diagnosis in {"receipt_secret_unconfigured", "provider_transport_unconfigured"}:
                return {
                    "success": False,
                    "business_success": False,
                    "system_success": True,
                    "transport_success": True,
                    "status": "blocked",
                    "code": "trusted_extract_local_config_gap",
                    "message": f"受信 Tavily 提取被本地配置阻断（{diagnosis}）；非上游 Tavily 故障",
                    "diagnosis": diagnosis,
                    "provider": _SEARCH_PROVIDER,
                    "retryable": False,
                    "trace_id": hashlib.sha256(f"fetch:{time.time_ns()}".encode()).hexdigest()[:24],
                    "resource_uris": [],
                    "warnings": [],
                    "blockers": ["trusted_extract_local_config_gap"],
                    "next_actions": [
                        "补充 LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET 到 .env 或运行时环境",
                        "检查 tavily provider 传输配置（API key 等）",
                        "或显式选择 extraction_provider=direct_http 绕过受信层",
                    ],
                }
            # 其他情况（provider_returned_empty / provider_call_failed）才归为上游故障。
            return {
                "success": False,
                "business_success": False,
                "system_success": True,
                "transport_success": True,
                "status": "upstream_failure",
                "code": "tavily_extract_unavailable",
                "message": f"受信 Tavily 正文提取当前不可用（{diagnosis}）",
                "diagnosis": diagnosis,
                "provider": _SEARCH_PROVIDER,
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
                            **( {"security_decision": security_decision} if security_decision else {}),
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
    # Provider 可能只回传部分 URL（如 404 链接被 Tavily 整条省略），
    # 未回传的 URL 此前不会进入本循环，导致批量响应缺少其逐项结果、
    # 来源缺失不可审计。这里先记录已回传集合，循环后与 checked_urls 对账。
    reported_urls: set[str] = set()
    for item in results:
        url = str(item.get("url") or "")
        reported_urls.add(url)
        error = str(item.get("error") or "")
        content = str(item.get("content") or "")
        if error or not content:
            if error.lower().startswith("blocked:"):
                provider_blocked += 1
                snapshots.append({
                    "url": url, "status": "blocked", "message": error,
                    **( {"security_decision": item["security_decision"]}
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
            "provider": str(
                item.get("provider")
                or (_SEARCH_PROVIDER if trusted_tavily else "direct_http")
            ),
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
    blocked_urls = {str(item.get("url") or "") for item in blocked_snapshots}
    for url_text in checked_urls:
        if url_text in reported_urls or url_text in blocked_urls:
            continue
        failures += 1
        snapshots.append({
            "url": url_text,
            "status": "failed",
            "code": "provider_omitted_url",
            "message": (
                "提取服务未返回该 URL 的任何结果"
                + (f"（诊断：{diagnosis}）" if diagnosis else "")
                + "；常见原因为链接已失效（HTTP 404）或被源站拒绝"
            ),
        })
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
                # 拦截时优先回传安全门给出的针对性 remediation（如代理 fake-ip
                # 场景要改走受信提取），而不是笼统的「改用公网可达地址」。
                _blocked_next_actions(snapshots)
                if blocked
                else ["稍后重试 Tavily，或显式选择 direct_http"]
            )
        ),
        "provider": _SEARCH_PROVIDER if trusted_tavily else "direct_http",
        "retryable": bool(failures and not succeeded),
        "retry_after": 5 if failures and not succeeded else None,
        "trace_id": hashlib.sha256(f"fetch:{time.time_ns()}".encode()).hexdigest()[:24],
    }

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "COLLECTION_STORE",
    "DISCOVERY_STORE",
    "SOURCE_STORE",
    "_ALLOWED_EXTERNAL_EXTRACT_TOOLS",
    "_SEARCH_PROVIDER",
    "_blocked_next_actions",
    "_collection_failure",
    "_external_receipt_message",
    "_external_snapshot_url_block_reason",
    "_network_safety_decision",
    "_secret_block_reason",
    "_trusted_tavily_extract",
    "collect",
    "external_receipt_secret",
    "fetch",
    "hashlib",
    "hmac",
    "import_external_snapshot",
    "ipaddress",
    "json",
    "os",
    "time",
    "urlsplit",
    "utc_now",
]
