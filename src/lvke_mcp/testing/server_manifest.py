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
    ServerSpec("lvke-asset-acquisition", "lvke_mcp.servers.lvke_asset_acquisition.server", "acquisition_get_run", {"workspace_id": WORKSPACE, "run_id": "acqrun_missing"}),
    ServerSpec("lvke-data-acquisition", "lvke_mcp.servers.lvke_data_acquisition.server", "data_provider_status", {}),
    ServerSpec("lvke-data-analysis", "lvke_mcp.servers.lvke_data_analysis.server", "analysis_status", {"workspace_id": WORKSPACE, "analysis_task_id": "analysis_missing"}),
    ServerSpec("lvke-deep-research", "lvke_mcp.servers.lvke_deep_research.server", "dr_status", {"workspace_id": WORKSPACE, "task_id": "drtask_missing"}),
    ServerSpec("lvke-deliverable-review", "lvke_mcp.servers.lvke_deliverable_review.server", "review_get", {"workspace_id": WORKSPACE, "review_id": "review_missing"}),
    ServerSpec("lvke-feasibility-delivery", "lvke_mcp.servers.lvke_feasibility_delivery.server", "feasibility_status", {"workspace_id": WORKSPACE, "delivery_run_id": "fdr_missing"}),
    ServerSpec("lvke-finance-model", "lvke_mcp.servers.lvke_finance_model.server", "finance_get_run", {"workspace_id": WORKSPACE}),
    ServerSpec("lvke-finance-tables", "lvke_mcp.servers.lvke_finance_tables.server", "tables_validate", {"workspace_id": WORKSPACE, "run_id": "run_missing"}),
    ServerSpec("lvke-knowledge-governance", "lvke_mcp.servers.lvke_knowledge_governance.server", "knowledge_list_candidates", {"workspace_id": WORKSPACE}),
    ServerSpec("lvke-project-planning", "lvke_mcp.servers.lvke_project_planning.server", "project_context_list", {"workspace_id": WORKSPACE}),
    ServerSpec("lvke-reference", "lvke_mcp.servers.lvke_reference.server", "reference_list", {"dataset": "statistics_dictionaries"}),
    ServerSpec("lvke-report-generation", "lvke_mcp.servers.lvke_report_generation.server", "report_get_readiness", {"workspace_id": WORKSPACE}),
    ServerSpec("lvke-source-files", "lvke_mcp.servers.lvke_source_files.server", "source_file_list", {"workspace_id": WORKSPACE}),
    ServerSpec("lvke-zero-material-delivery", "lvke_mcp.servers.lvke_zero_material_delivery.server", "delivery_status", {"workspace_id": WORKSPACE, "delivery_run_id": "zmr_missing"}),
)

SERVER_BY_NAME = {spec.name: spec for spec in SERVER_SPECS}
SERVER_BY_MODULE = {spec.module: spec for spec in SERVER_SPECS}

if len(SERVER_SPECS) != 14 or len(SERVER_BY_NAME) != 14 or len(SERVER_BY_MODULE) != 14:
    raise RuntimeError("server manifest must contain 14 unique names and modules")
