"""Resource 列举与解析。"""

from __future__ import annotations

import csv
import json
from typing import Any

from lvke_mcp.adapters.finance_tables_repository import CSV_EXPORT_STORE, PACKAGE_STORE, xlsx_path_from_uri
from lvke_mcp.runtime.storage import paginate_resource_entries, require_safe_id

from .base import (
    _failure,
)

from .export import (
    csv_path_from_uri,
)

from .query import (
    get_table,
)


def list_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """List only resources addressable inside the explicit workspace scope."""

    allowed_types = {"package", "csv_manifest", "csv", "xlsx"}
    if resource_type and resource_type not in allowed_types:
        return _failure("resource_type_invalid", "未知 Resource 类型过滤条件")
    entries: dict[str, dict[str, Any]] = {}

    for record in PACKAGE_STORE.list(workspace_id):
        package_id = str(record.get("object_id") or "")
        uri = str(record.get("resource_uri") or "")
        if uri:
            entries[uri] = {
                "uri": uri,
                "name": package_id,
                "resource_type": "package",
                "mime_type": "application/json",
                "created_at": record.get("created_at"),
            }
        for resource_suffix, filename_suffix in (
            ("xlsx", ".xlsx"),
            ("xlsx-technical", ".technical.xlsx"),
        ):
            xlsx_uri = f"{uri}/{resource_suffix}"
            if uri and xlsx_path_from_uri(xlsx_uri) is not None:
                entries[xlsx_uri] = {
                    "uri": xlsx_uri,
                    "name": f"{package_id}{filename_suffix}",
                    "resource_type": "xlsx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "created_at": record.get("created_at"),
                }

    for record in CSV_EXPORT_STORE.list(workspace_id):
        uri = str(record.get("resource_uri") or "")
        payload = record.get("payload") or {}
        if uri:
            entries[uri] = {
                "uri": uri,
                "name": str(record.get("object_id") or ""),
                "resource_type": "csv_manifest",
                "mime_type": "application/json",
                "created_at": record.get("created_at"),
            }
        for item in payload.get("tables") or []:
            if not isinstance(item, dict):
                continue
            csv_uri = str(item.get("resource_uri") or "")
            if csv_uri and csv_path_from_uri(csv_uri) is not None:
                entries[csv_uri] = {
                    "uri": csv_uri,
                    "name": f"{item.get('table_id')}.csv",
                    "resource_type": "csv",
                    "mime_type": "text/csv; charset=utf-8",
                    "created_at": record.get("created_at"),
                }
        lineage_uri = str((payload.get("lineage") or {}).get("resource_uri") or "")
        if lineage_uri and csv_path_from_uri(lineage_uri) is not None:
            entries[lineage_uri] = {
                "uri": lineage_uri,
                "name": "00_数据血缘.csv",
                "resource_type": "csv",
                "mime_type": "text/csv; charset=utf-8",
                "created_at": record.get("created_at"),
            }

    try:
        pagination = paginate_resource_entries(
            (
                entry for entry in entries.values()
                if not resource_type or entry["resource_type"] == resource_type
            ),
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        code = str(exc)
        message = (
            "资源列表在分页期间发生变化，请从第一页重新列举"
            if code == "resource_list_changed"
            else "Resource 分页游标无效"
        )
        return _failure(code, message)
    page = pagination["resources"]
    return {
        "success": True,
        "status": "ok",
        "validation_complete": False,
        "resources": page,
        "next_cursor": pagination["next_cursor"],
        "has_more": pagination["has_more"],
        "snapshot_hash": pagination["snapshot_hash"],
        "resource_uris": [entry["uri"] for entry in page],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def resolve_resource(
    uri: str,
    workspace_id: str,
) -> tuple[str | bytes, str] | None:
    prefix = f"lvke://finance-tables/workspaces/{require_safe_id(workspace_id, 'workspace_id')}/"
    if not str(uri).startswith(prefix):
        return None
    if "/tables/" in uri:
        parts = uri.removeprefix(prefix).split("/")
        if len(parts) != 4 or parts[0] != "packages" or parts[2] != "tables":
            return None
        result = get_table(
            workspace_id,
            parts[1],
            parts[3],
            "structured",
        )
        if result.get("status") != "ok":
            return None
        return json.dumps(result, ensure_ascii=False, indent=2), "application/json"
    if uri.endswith(("/xlsx", "/xlsx-technical")):
        path = xlsx_path_from_uri(uri)
        return None if path is None else (path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if "/csv/" in uri:
        path = csv_path_from_uri(uri)
        return None if path is None else (path.read_bytes(), "text/csv; charset=utf-8")
    record = CSV_EXPORT_STORE.resolve_uri(uri) or PACKAGE_STORE.resolve_uri(uri)
    if record is None or str(record.get("workspace_id") or "") != workspace_id:
        return None
    return json.dumps(record, ensure_ascii=False, indent=2), "application/json"
