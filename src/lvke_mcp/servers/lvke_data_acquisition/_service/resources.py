"""lvke-data-acquisition service 拆分：资源列表、解析与 provider 状态。"""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters.data_acquisition_repository import (
    RESOURCE_STORES,
    resolve_resource as resolve_repository_resource,
)
from lvke_mcp.runtime.storage import paginate_resource_entries, utc_now


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


def list_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    allowed = {kind for _store, kind in RESOURCE_STORES}
    if resource_type and resource_type not in allowed:
        return _resource_failure(
            "resource_type_invalid",
            "未知 Resource 类型过滤条件",
        )
    entries: list[dict[str, Any]] = []
    for store, kind in RESOURCE_STORES:
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


async def provider_status() -> dict[str, Any]:
    # 必须 await 而不是 asyncio.run：本函数由 OfficialStdioServer 在已运行的事件
    # 循环中调用，asyncio.run 会抛 "cannot be called from a running event loop"。
    from lvke_mcp.domains.research.providers import tavily as tavily_provider

    providers = [await tavily_provider.provider_status()]
    available_count = sum(1 for item in providers if item.get("available"))
    status = "ok" if available_count else "blocked"
    # 区分本地配置缺口与上游故障：旧实现对两者都报
    # provider_configuration_missing，把 Tavily 宕机说成少配了环境变量。
    failure_kinds = {
        str(item.get("failure_kind") or "")
        for item in providers
        if not item.get("available")
    } - {""}
    warnings: list[str] = []
    blockers: list[str] = []
    next_actions: list[str] = []
    if not available_count:
        if failure_kinds == {"upstream_unavailable"}:
            warnings.append("Tavily 传输已配置但连通性探测失败，属上游不可用，非本地配置缺口")
            blockers.append("provider_upstream_unavailable")
            next_actions.append("稍后重试；持续失败请检查 Tavily 服务状态或凭据有效性")
        else:
            warnings.append("当前没有可用 Web provider")
            blockers.append("provider_configuration_missing")
            next_actions.append("配置受信 Tavily，或使用受控 direct_http 采集")
    # 受信提取额外依赖 receipt secret：不在这里报出来，data_fetch 会在每次调用
    # 时才 blocked，而本工具是文档指定的前置检查。
    if any(
        item.get("available") and not item.get("formal_extract_ready")
        for item in providers
    ):
        warnings.append(
            "缺少 LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET：搜索可用，但受信提取"
            "（data_fetch 的 auto/tavily 路径）会被 trusted_extract_local_config_gap 阻断"
        )
        next_actions.append("补充 LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET 后重启 server")
    return {
        "success": bool(available_count),
        "transport_success": True,
        "business_success": bool(available_count),
        "completed": bool(available_count),
        "outcome": status,
        "status": status,
        "checked_at": utc_now(),
        "providers": providers,
        # 探测覆盖面要自报：extract 从未被探测，调用方不应把 ok 当作提取健康。
        "probe_coverage": {"search": "probed", "extract": "not_probed"},
        "resource_uris": [],
        "warnings": warnings,
        "blockers": blockers,
        "next_actions": next_actions,
    }