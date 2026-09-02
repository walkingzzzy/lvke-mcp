"""Workspace-scoped dispatcher for all governed Lvke resources.

The registry deliberately delegates to each domain's existing list/read path.
It changes only the public MCP routing surface; resource bytes, object records,
URIs and workspace checks remain owned by the original domain service.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any
from urllib.parse import urlparse


DOMAINS = (
    "source-files",
    "data-acquisition",
    "data-analysis",
    "deep-research",
    "deliverable-review",
    "feasibility-delivery",
    "finance-tables",
    "knowledge-governance",
    "project-planning",
    "report-generation",
    "zero-material-delivery",
    "asset-acquisition",
    # QualityDiagnostic is a cross-domain governed resource.  It is exposed
    # under its own URI authority so consumers can read the immutable
    # diagnostic object returned by any producer (EvidencePack, FinanceRun,
    # tables or report revision).
    "quality-diagnostics",
)

_URI_DOMAIN_ALIASES = {
    "source-reconstructed": "data-acquisition",
}


def _module(name: str):
    return import_module(name)


def _blocked(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "business_success": False,
        "system_success": True,
        "transport_success": True,
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
    domain: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Delegate one scoped list request without changing domain records."""

    if domain not in DOMAINS:
        return _blocked("resource_domain_invalid", "未知 Resource 领域")
    if domain == "source-files":
        service = _module("lvke_mcp.servers.lvke_source_files.service")
        result = service.list_resources(workspace_id, cursor=cursor, limit=limit)
        if resource_type and result.get("success"):
            rows = [
                row for row in result.get("resources", [])
                if str(row.get("resource_type") or "") == resource_type
            ]
            result = {**result, "resources": rows, "resource_uris": [row["uri"] for row in rows]}
        return result
    if domain == "data-acquisition":
        service = _module("lvke_mcp.servers.lvke_data_acquisition.service")
        return service.list_resources(
            workspace_id, resource_type=resource_type, cursor=cursor, limit=limit
        )
    if domain == "data-analysis":
        service = _module("lvke_mcp.servers.lvke_data_analysis.service")
        return service.list_resources(
            workspace_id, resource_type=resource_type, cursor=cursor, limit=limit
        )
    if domain == "deep-research":
        server = _module("lvke_mcp.servers.lvke_deep_research.server")
        return server._list_scoped_resources(  # noqa: SLF001
            workspace_id, resource_type=resource_type, cursor=cursor, limit=limit
        )
    if domain == "deliverable-review":
        service = _module("lvke_mcp.servers.lvke_deliverable_review.service")
        return service.list_resources({
            "workspace_id": workspace_id,
            "resource_type": resource_type,
            "cursor": cursor,
            "limit": limit,
        })
    if domain == "feasibility-delivery":
        service = _module("lvke_mcp.servers.lvke_feasibility_delivery.service")
        return service.list_resources({
            "workspace_id": workspace_id,
            "resource_type": resource_type,
            "cursor": cursor,
            "limit": limit,
        })
    if domain == "finance-tables":
        service = _module("lvke_mcp.domains.finance.tables_service")
        return service.list_resources(
            workspace_id, resource_type=resource_type, cursor=cursor, limit=limit
        )
    if domain == "knowledge-governance":
        service = _module("lvke_mcp.servers.lvke_knowledge_governance.service")
        return service.list_resources({
            "workspace_id": workspace_id,
            "resource_type": resource_type,
            "cursor": cursor,
            "limit": limit,
        })
    if domain == "project-planning":
        service = _module("lvke_mcp.servers.lvke_project_planning.service")
        return service.list_resources(
            workspace_id, resource_type=resource_type, cursor=cursor, limit=limit
        )
    if domain == "report-generation":
        service = _module("lvke_mcp.domains.reports.application")
        return service.list_resources(
            workspace_id, resource_type=resource_type, cursor=cursor, limit=limit
        )
    if domain == "asset-acquisition":
        service = _module("lvke_mcp.servers.lvke_feasibility_delivery.service")
        return service.list_asset_resources(
            workspace_id,
            resource_type=resource_type,
            cursor=cursor,
            limit=limit,
        )
    if domain == "quality-diagnostics":
        from lvke_mcp.adapters.quality_diagnostic_repository import (
            QUALITY_DIAGNOSTIC_STORE,
        )
        from lvke_mcp.runtime.storage import paginate_resource_entries

        rows = []
        for record in QUALITY_DIAGNOSTIC_STORE.list(workspace_id):
            payload = record.get("payload") or {}
            object_type = "QualityDiagnostic"
            if resource_type and resource_type not in {object_type, "quality_diagnostic"}:
                continue
            rows.append({
                "uri": record.get("resource_uri"),
                "name": f"{object_type} {record.get('object_id')}",
                "mime_type": "application/json",
                "resource_type": object_type,
                "object_type": object_type,
                "content_hash": record.get("content_hash"),
                "basis_hash": record.get("basis_hash"),
                "target_type": payload.get("target_type"),
                "target_id": payload.get("target_id"),
            })
        page = paginate_resource_entries(rows, cursor=cursor, limit=limit)
        return {
            "success": True,
            "business_success": True,
            "system_success": True,
            "transport_success": True,
            "status": "ok",
            "resources": page["resources"],
            "items": page["items"],
            "total": page["total"],
            "next_cursor": page["next_cursor"],
            "has_more": page["has_more"],
            "resource_uris": [item["uri"] for item in page["items"] if item.get("uri")],
            "warnings": [],
            "blockers": [],
            "next_actions": [],
        }
    service = _module("lvke_mcp.servers.lvke_zero_material_delivery.service")
    return service.list_resources({
        "workspace_id": workspace_id,
        "resource_type": resource_type,
        "cursor": cursor,
        "limit": limit,
    })


def domain_from_uri(uri: str) -> str:
    """Resolve the owning dispatcher domain without inspecting workspace data."""

    parsed = urlparse(uri)
    if parsed.scheme != "lvke":
        return ""
    authority = parsed.netloc
    domain = _URI_DOMAIN_ALIASES.get(authority, authority)
    return domain if domain in DOMAINS else ""


def read_resource(workspace_id: str, uri: str) -> dict[str, Any]:
    """Delegate one scoped read and preserve the original result body/bytes."""

    domain = domain_from_uri(uri)
    if domain not in DOMAINS:
        return _blocked("resource_domain_invalid", "无法从 URI 识别 Resource 领域")
    if domain == "quality-diagnostics":
        # QualityDiagnostic records are persisted in a shared immutable store,
        # not in the feasibility-delivery RESOURCE_STORES collection.  Route
        # them explicitly here so the URI emitted by producers is actually
        # readable through the cross-service resolver.
        from lvke_mcp.adapters.quality_diagnostic_repository import (
            QUALITY_DIAGNOSTIC_STORE,
        )

        record = QUALITY_DIAGNOSTIC_STORE.resolve_uri(uri)
        if record is None or str(record.get("workspace_id") or "") != str(workspace_id):
            return _blocked("resource_not_found", "QualityDiagnostic 不存在或不属于当前工作区")
        return {
            "success": True,
            "business_success": True,
            "system_success": True,
            "transport_success": True,
            "status": "ok",
            "object_type": "QualityDiagnostic",
            "resource": record,
            "resource_uris": [uri],
            "warnings": [],
            "blockers": [],
            "next_actions": [],
        }
    if domain == "source-files":
        service = _module("lvke_mcp.servers.lvke_source_files.service")
        return service.read_resource(workspace_id, uri)
    if domain == "data-acquisition":
        server = _module("lvke_mcp.servers.lvke_data_acquisition.server")
        return server._read_resource({"workspace_id": workspace_id, "uri": uri})  # noqa: SLF001
    if domain == "data-analysis":
        server = _module("lvke_mcp.servers.lvke_data_analysis.server")
        return server._read_resource({"workspace_id": workspace_id, "uri": uri})  # noqa: SLF001
    if domain == "deep-research":
        server = _module("lvke_mcp.servers.lvke_deep_research.server")
        return server._read_scoped_resource(workspace_id, uri)  # noqa: SLF001
    if domain == "deliverable-review":
        service = _module("lvke_mcp.servers.lvke_deliverable_review.service")
        return service.read_resource({"workspace_id": workspace_id, "uri": uri})
    if domain == "feasibility-delivery":
        service = _module("lvke_mcp.servers.lvke_feasibility_delivery.service")
        return service.read_resource({"workspace_id": workspace_id, "uri": uri})
    if domain == "finance-tables":
        server = _module("lvke_mcp.servers.lvke_finance_tables.server")
        return server._read_scoped_resource(workspace_id, uri)  # noqa: SLF001
    if domain == "knowledge-governance":
        service = _module("lvke_mcp.servers.lvke_knowledge_governance.service")
        return service.read_resource({"workspace_id": workspace_id, "uri": uri})
    if domain == "project-planning":
        service = _module("lvke_mcp.servers.lvke_project_planning.service")
        return service.read_resource(workspace_id, uri)
    if domain == "report-generation":
        server = _module("lvke_mcp.servers.lvke_report_generation.server")
        return server._read_scoped_resource(workspace_id, uri)  # noqa: SLF001
    if domain == "asset-acquisition":
        service = _module("lvke_mcp.servers.lvke_feasibility_delivery.service")
        return service.read_asset_resource(workspace_id, uri)
    service = _module("lvke_mcp.servers.lvke_zero_material_delivery.service")
    return service.read_resource({"workspace_id": workspace_id, "uri": uri})


def get_review(workspace_id: str, review_id: str) -> dict[str, Any]:
    """Read the existing deliverable-review projection through the registry.

    The registry is the shared runtime boundary for compressed MCP processes.
    It preserves the review service's envelope and freshness checks while
    keeping aggregate services independent from another MCP server package.
    """

    service = _module("lvke_mcp.servers.lvke_deliverable_review.service")
    return service.get_review({"workspace_id": workspace_id, "review_id": review_id})


def get_knowledge_candidate(workspace_id: str, candidate_id: str) -> dict[str, Any]:
    """Read the existing knowledge-governance candidate projection."""

    service = _module("lvke_mcp.servers.lvke_knowledge_governance.service")
    return service.get_candidate({"workspace_id": workspace_id, "candidate_id": candidate_id})
