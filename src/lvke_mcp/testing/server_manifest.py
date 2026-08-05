"""Authoritative manifest for the independently runnable MCP servers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServerSpec:
    name: str
    module: str
    probe_tool: str
    probe_arguments: dict[str, Any]


WORKSPACE = "mcp-acceptance-empty"
SERVER_SPECS: tuple[ServerSpec, ...] = (
    ServerSpec("environmental-data", "lvke_mcp.servers.environmental_data.server", "list_monitored_locations", {}),
    ServerSpec("excel-bridge", "lvke_mcp.servers.excel_bridge.server", "list_sheets", {"path": "/dev/null"}),
    ServerSpec("finance-calc", "lvke_mcp.servers.finance_calc.server", "calc_irr", {"cashflows": [-1000, 600, 600]}),
    ServerSpec("industry-research", "lvke_mcp.servers.industry_research.server", "search_report", {"industry": "汽车制造"}),
    ServerSpec("lvke-archive", "lvke_mcp.servers.lvke_archive.server", "search_archive", {"keyword": "光伏", "limit": 3}),
    ServerSpec("lvke-asset-acquisition", "lvke_mcp.servers.lvke_asset_acquisition.server", "acquisition_get_run", {"workspace_id": WORKSPACE, "run_id": "acqrun_missing"}),
    ServerSpec("lvke-clients", "lvke_mcp.servers.lvke_clients.server", "search_clients", {"industry": "光伏"}),
    ServerSpec("lvke-data-acquisition", "lvke_mcp.servers.lvke_data_acquisition.server", "data_provider_status", {}),
    ServerSpec("lvke-data-analysis", "lvke_mcp.servers.lvke_data_analysis.server", "analysis_status", {"workspace_id": WORKSPACE, "analysis_task_id": "analysis_missing"}),
    ServerSpec("lvke-deep-research", "lvke_mcp.servers.lvke_deep_research.server", "dr_status", {"workspace_id": WORKSPACE, "task_id": "drtask_missing"}),
    ServerSpec("lvke-deliverable-review", "lvke_mcp.servers.lvke_deliverable_review.server", "review_get", {"workspace_id": WORKSPACE, "review_id": "review_missing"}),
    ServerSpec("lvke-feasibility-delivery", "lvke_mcp.servers.lvke_feasibility_delivery.server", "feasibility_status", {"workspace_id": WORKSPACE, "delivery_run_id": "fdr_missing"}),
    ServerSpec("lvke-experts", "lvke_mcp.servers.lvke_experts.server", "list_specialties", {}),
    ServerSpec("lvke-finance-model", "lvke_mcp.servers.lvke_finance_model.server", "finance_get_run", {"workspace_id": WORKSPACE}),
    ServerSpec("lvke-finance-tables", "lvke_mcp.servers.lvke_finance_tables.server", "tables_validate", {"workspace_id": WORKSPACE, "run_id": "run_missing"}),
    ServerSpec("lvke-knowledge-governance", "lvke_mcp.servers.lvke_knowledge_governance.server", "knowledge_list_candidates", {"workspace_id": WORKSPACE}),
    ServerSpec("lvke-project-planning", "lvke_mcp.servers.lvke_project_planning.server", "project_context_list", {"workspace_id": WORKSPACE}),
    ServerSpec("lvke-report-generation", "lvke_mcp.servers.lvke_report_generation.server", "report_get_readiness", {"workspace_id": WORKSPACE}),
    ServerSpec("lvke-source-files", "lvke_mcp.servers.lvke_source_files.server", "source_file_list", {"workspace_id": WORKSPACE}),
    ServerSpec("lvke-templates", "lvke_mcp.servers.lvke_templates.server", "list_templates", {}),
    ServerSpec("lvke-zero-material-delivery", "lvke_mcp.servers.lvke_zero_material_delivery.server", "delivery_list_resources", {"workspace_id": WORKSPACE}),
    ServerSpec("map-geo", "lvke_mcp.servers.map_geo.server", "geocode", {"address": "武汉天河国际机场"}),
    ServerSpec("policy-search", "lvke_mcp.servers.policy_search.server", "search_policy", {"keyword": "长江"}),
    ServerSpec("statistics-cn", "lvke_mcp.servers.statistics_cn.server", "list_dictionaries", {}),
)

SERVER_BY_NAME = {spec.name: spec for spec in SERVER_SPECS}
SERVER_BY_MODULE = {spec.module: spec for spec in SERVER_SPECS}

if len(SERVER_SPECS) != 24 or len(SERVER_BY_NAME) != 24 or len(SERVER_BY_MODULE) != 24:
    raise RuntimeError("server manifest must contain 24 unique names and modules")
