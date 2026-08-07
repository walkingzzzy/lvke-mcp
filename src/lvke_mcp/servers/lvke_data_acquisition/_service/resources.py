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
    available_count = sum(1 for item in providers if item["available"])
    status = "ok" if available_count else "blocked"
    return {
        "success": bool(available_count),
        "transport_success": True,
        "business_success": bool(available_count),
        "completed": bool(available_count),
        "outcome": status,
        "status": status,
        "checked_at": utc_now(),
        "providers": providers,
        "resource_uris": [],
        "warnings": [] if available_count else ["当前没有可用 Web provider"],
        "blockers": [] if available_count else ["provider_configuration_missing"],
        "next_actions": [] if available_count else ["配置受信 Tavily，或使用受控 direct_http 采集"],
    }