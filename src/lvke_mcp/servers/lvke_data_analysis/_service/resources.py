"""Resource list and resolution surface for data-analysis MCP resources."""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters.data_analysis_repository import (
    RESOURCE_STORES,
    resolve_resource as resolve_repository_resource,
)
from lvke_mcp.runtime.storage import paginate_resource_entries

from .envelope import _missing


def list_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    allowed = {kind for _store, kind in RESOURCE_STORES}
    if resource_type and resource_type not in allowed:
        return _missing("resource_type_invalid", "未知 Resource 类型过滤条件")
    entries = []
    for store, kind in RESOURCE_STORES:
        if resource_type and kind != resource_type:
            continue
        for record in store.list(workspace_id):
            uri = str(record.get("resource_uri") or "")
            if uri:
                entries.append({
                    "uri": uri,
                    "name": str(record.get("object_id") or ""),
                    "resource_type": kind,
                    "mime_type": "application/json",
                    "created_at": record.get("created_at"),
                })
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _missing(str(exc), "Resource 分页游标无效或列表已变化")
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
