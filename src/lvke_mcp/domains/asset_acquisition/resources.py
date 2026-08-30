"""Workspace-scoped Resource projection for asset acquisition objects."""

from __future__ import annotations

import base64
import json
from typing import Any

from lvke_mcp.domains.asset_acquisition import backend, tables
from lvke_mcp.runtime.storage import paginate_resource_entries, require_safe_id


def _uri(workspace_id: str, segment: str, object_id: str) -> str:
    return (
        f"lvke://asset-acquisition/workspaces/{require_safe_id(workspace_id, 'workspace_id')}/"
        f"{require_safe_id(segment, 'segment')}/{require_safe_id(object_id, 'object_id')}"
    )


def _ok(data: dict[str, Any], *, uris: list[str] | None = None) -> dict[str, Any]:
    return {
        "success": True,
        "system_success": True,
        "transport_success": True,
        "business_success": True,
        "status": "ok",
        **data,
        "resource_uris": list(uris or []),
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def _blocked(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "success": False,
        "system_success": True,
        "transport_success": True,
        "business_success": False,
        "status": "blocked",
        "code": code,
        "message": message,
        **details,
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [],
    }


def resolve_resource(uri: str) -> tuple[str | bytes, str] | None:
    table_resource = tables.resolve_resource(uri)
    if table_resource is not None:
        return table_resource
    prefix = "lvke://asset-acquisition/workspaces/"
    if not uri.startswith(prefix):
        return None
    parts = uri[len(prefix):].split("/")
    if len(parts) != 3:
        return None
    workspace_id, segment, object_id = parts
    try:
        require_safe_id(workspace_id, "workspace_id")
        require_safe_id(object_id, "object_id")
    except ValueError:
        return None
    if segment == "specs":
        record = backend.get_spec(workspace_id, object_id)
    elif segment == "runs":
        record = backend.get_run(workspace_id, object_id)
    elif segment == "artifacts":
        record = backend.get_artifact(workspace_id, object_id)
    elif segment == "scenario-matrices":
        record = next((
            backend.get_scenario_matrix(workspace_id, str(row.get("run_id") or ""), object_id)
            for row in backend.list_runs(workspace_id, limit=10_000)
            if any(
                item.get("matrix_id") == object_id
                for item in backend.list_scenario_matrices(
                    workspace_id,
                    str(row.get("run_id") or ""),
                )
            )
        ), {})
    else:
        return None
    if not record:
        return None
    return json.dumps(record, ensure_ascii=False, indent=2, default=str), "application/json"


def list_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 25,
) -> dict[str, Any]:
    workspace_id = require_safe_id(workspace_id, "workspace_id")
    allowed_types = {
        "", "spec", "run", "scenario_matrix", "artifact", "table_package",
        "xlsx", "csv", "manifest",
    }
    if resource_type not in allowed_types:
        return _blocked("RESOURCE_TYPE_INVALID", "未知资产收购 Resource 类型")
    entries: list[dict[str, Any]] = []

    def add(uri: str, kind: str, name: str, updated_at: Any = None) -> None:
        if not resource_type or resource_type == kind:
            entries.append({
                "uri": uri,
                "resource_type": kind,
                "name": name,
                "updated_at": updated_at,
            })

    for row in backend.list_specs(workspace_id, limit=10_000):
        spec_id = str(row.get("spec_id") or "")
        if spec_id:
            add(_uri(workspace_id, "specs", spec_id), "spec", spec_id, row.get("created_at"))
    for row in backend.list_runs(workspace_id, limit=10_000):
        run_id = str(row.get("run_id") or "")
        if not run_id:
            continue
        add(_uri(workspace_id, "runs", run_id), "run", run_id, row.get("created_at"))
        for matrix in backend.list_scenario_matrices(workspace_id, run_id):
            matrix_id = str(matrix.get("matrix_id") or "")
            if matrix_id:
                add(
                    _uri(workspace_id, "scenario-matrices", matrix_id),
                    "scenario_matrix",
                    matrix_id,
                    matrix.get("created_at"),
                )
    for artifact in backend.list_artifacts(workspace_id, limit=10_000):
        artifact_id = str(artifact.get("artifact_id") or "")
        if artifact_id:
            add(
                _uri(workspace_id, "artifacts", artifact_id),
                "artifact",
                artifact_id,
                artifact.get("updated_at") or artifact.get("created_at"),
            )
    for package in tables.PACKAGE_STORE.list(workspace_id):
        package_id = str(package.get("object_id") or "")
        package_uri = str(package.get("resource_uri") or "")
        if not package_id or not package_uri:
            continue
        add(package_uri, "table_package", package_id, package.get("created_at"))
        xlsx_uri = package_uri + "/xlsx"
        if tables.resolve_resource(xlsx_uri) is not None:
            add(xlsx_uri, "xlsx", f"{package_id}.xlsx", package.get("created_at"))
        xlsx_manifest_uri = package_uri + "/xlsx/manifest"
        if tables.resolve_resource(xlsx_manifest_uri) is not None:
            add(
                xlsx_manifest_uri,
                "manifest",
                f"{package_id}.xlsx.manifest.json",
                package.get("created_at"),
            )
        payload = package.get("payload") or {}
        definitions, _columns, _required = tables._table_contract(  # noqa: SLF001
            str(payload.get("asset_type") or "hotel_lease")
        )
        supplemental = [
            key
            for key in ("monthly_income_statement", "monthly_balance_sheet")
            if (payload.get("tables") or {}).get(key)
        ]
        for key in [*(key for key, _label in definitions), *supplemental]:
            csv_uri = package_uri + f"/csv/{key}"
            if tables.resolve_resource(csv_uri) is not None:
                add(csv_uri, "csv", f"{key}.csv", package.get("created_at"))
        csv_manifest_uri = package_uri + "/csv/manifest"
        if tables.resolve_resource(csv_manifest_uri) is not None:
            add(
                csv_manifest_uri,
                "manifest",
                f"{package_id}.csv.manifest.json",
                package.get("created_at"),
            )
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        code = str(exc) or "resource_cursor_invalid"
        return _blocked(code, "资产收购 Resource 游标无效")
    rows = page["resources"]
    return _ok(
        {**page, "resources": rows},
        uris=[str(row.get("uri") or "") for row in rows],
    )


def read_resource(workspace_id: str, uri: str) -> dict[str, Any]:
    workspace_id = require_safe_id(workspace_id, "workspace_id")
    uri = str(uri or "")
    prefix = "lvke://asset-acquisition/workspaces/"
    if not uri.startswith(prefix):
        return _blocked("INVALID_URI", "不是资产收购 Resource URI", uri=uri)
    parts = uri[len(prefix):].split("/")
    if len(parts) < 3:
        return _blocked("MALFORMED_URI", "资产收购 Resource URI 格式错误", uri=uri)
    try:
        uri_workspace = require_safe_id(parts[0], "uri_workspace_id")
    except ValueError:
        return _blocked("MALFORMED_URI", "资产收购 Resource URI 格式错误", uri=uri)
    if uri_workspace != workspace_id:
        return _blocked(
            "RESOURCE_WORKSPACE_MISMATCH",
            "Resource URI 与显式 workspace_id 不一致",
            uri=uri,
        )
    resolved = resolve_resource(uri)
    if resolved is None:
        return _blocked("NOT_FOUND", "资产收购 Resource 不存在", uri=uri)
    content, mime_type = resolved
    encoded = isinstance(content, bytes)
    return _ok(
        {
            "uri": uri,
            "mime_type": mime_type,
            "mimeType": mime_type,
            "content_encoding": "base64" if encoded else "utf-8",
            "content": base64.b64encode(content).decode("ascii") if encoded else content,
        },
        uris=[uri],
    )
