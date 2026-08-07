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
