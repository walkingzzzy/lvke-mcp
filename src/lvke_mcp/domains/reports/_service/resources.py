"""报告域 Resource 列举与解析。"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, unquote

from lvke_mcp.adapters.report_repository import (
    BINDING_STORE,
    PREPARATION_STORE,
    REVISION_STORE,
)
from lvke_mcp.runtime.storage import paginate_resource_entries


from .base import (
    _failure,
)


def list_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    allowed_types = {"preparation", "job", "revision", "artifact", "artifact_file"}
    if resource_type and resource_type not in allowed_types:
        return _failure("resource_type_invalid", "未知 Resource 类型过滤条件")
    entries: dict[str, dict[str, Any]] = {}
    for store, kind in (
        (PREPARATION_STORE, "preparation"),
        (BINDING_STORE, "job"),
        (REVISION_STORE, "revision"),
    ):
        for record in store.list(workspace_id):
            uri = str(record.get("resource_uri") or "")
            if uri:
                entries[uri] = {
                    "uri": uri,
                    "name": str(record.get("object_id") or ""),
                    "resource_type": kind,
                    "mime_type": "application/json",
                    "created_at": record.get("created_at"),
                }

    from lvke_mcp.domains.reports import artifacts

    try:
        artifact_records = artifacts.list_artifacts(
            workspace_id,
            limit=200,
        )
    except Exception:  # noqa: BLE001 - an uninitialized workspace simply has no artifacts
        artifact_records = []
    for record in artifact_records:
        artifact_id = str(record.get("artifact_id") or "")
        base = f"lvke://report-generation/workspaces/{workspace_id}/artifacts/{artifact_id}"
        entries[base] = {
            "uri": base,
            "name": artifact_id,
            "resource_type": "artifact",
            "mime_type": "application/json",
            "created_at": record.get("created_at"),
        }
        try:
            record = artifacts.get_artifact(
                workspace_id,
                artifact_id,
            )
        except Exception:  # noqa: BLE001 - omit artifacts that cannot be refreshed safely
            continue
        externally_readable = (
            record.get("status") == "succeeded"
            and record.get("integrity_status") == "passed"
            and bool(record.get("current", True))
        )
        for item in (record.get("files") or []) if externally_readable else []:
            if not isinstance(item, dict) or not (item.get("name") or item.get("filename")):
                continue
            filename = str(item.get("name") or item.get("filename") or "")
            uri = f"{base}/files/{quote(filename, safe='')}"
            entries[uri] = {
                "uri": uri,
                "name": filename,
                "resource_type": "artifact_file",
                "mime_type": str(item.get("media_type") or "application/octet-stream"),
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
    workspace_id: str | None = None,
) -> tuple[str | bytes, str] | None:
    if workspace_id is not None:
        expected = f"lvke://report-generation/workspaces/{workspace_id}/"
        if not str(uri).startswith(expected):
            return None
    for store in (PREPARATION_STORE, BINDING_STORE, REVISION_STORE):
        record = store.resolve_uri(uri)
        if record is not None and (
            workspace_id is None or str(record.get("workspace_id") or "") == workspace_id
        ):
            return json.dumps(record, ensure_ascii=False, indent=2), "application/json"
    prefix = "lvke://report-generation/workspaces/"
    if not uri.startswith(prefix):
        return None
    parts = uri[len(prefix) :].split("/")
    if len(parts) not in {3, 5} or parts[1] != "artifacts":
        return None
    from lvke_mcp.domains.reports import artifacts

    if len(parts) == 3:
        try:
            record = artifacts.get_artifact(
                parts[0],
                parts[2],
            )
        except Exception:  # noqa: BLE001
            return None
        return json.dumps(record, ensure_ascii=False, indent=2, default=str), "application/json"
    if parts[3] != "files":
        return None
    try:
        resolved = artifacts.read_artifact_download(
            parts[0],
            parts[2],
            unquote(parts[4]),
        )
    except Exception:  # noqa: BLE001
        return None
    return resolved["content"], str(resolved.get("media_type") or "application/octet-stream")
