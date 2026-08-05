"""Persistence for project-planning immutable objects."""

from lvke_mcp.runtime.storage import JSONArtifactStore

PROJECT_CONTEXT_STORE = JSONArtifactStore(
    "project-planning", "project_contexts", "pctx", "project-contexts"
)
INPUT_APPLICABILITY_STORE = JSONArtifactStore(
    "project-planning", "input_applicability", "iapp", "input-applicability"
)
MARKET_CASE_STORE = JSONArtifactStore(
    "project-planning", "market_cases", "mkt", "market-cases"
)
REVENUE_DRIVER_STORE = JSONArtifactStore(
    "project-planning", "revenue_drivers", "revdrv", "revenue-drivers"
)
BUILD_SCALE_STORE = JSONArtifactStore(
    "project-planning", "build_scale_cases", "scale", "build-scale-cases"
)
COST_DRIVER_STORE = JSONArtifactStore(
    "project-planning", "cost_drivers", "costdrv", "cost-drivers"
)
LABOR_PLAN_STORE = JSONArtifactStore(
    "project-planning", "labor_plans", "labor", "labor-plans"
)
OPTION_COMPARISON_STORE = JSONArtifactStore(
    "project-planning", "option_comparisons", "optcmp", "option-comparisons"
)
POLICY_BASIS_STORE = JSONArtifactStore(
    "project-planning", "policy_bases", "policy", "policy-bases"
)
IDEMPOTENCY_STORE = JSONArtifactStore(
    "project-planning", "idempotency", "idem", "idempotency"
)

RESOURCE_STORES = (
    (PROJECT_CONTEXT_STORE, "ProjectContext"),
    (INPUT_APPLICABILITY_STORE, "InputApplicability"),
    (MARKET_CASE_STORE, "MarketSizingCase"),
    (REVENUE_DRIVER_STORE, "RevenueDriverSet"),
    (BUILD_SCALE_STORE, "BuildScaleCase"),
    (COST_DRIVER_STORE, "CostDriverSet"),
    (LABOR_PLAN_STORE, "LaborPlan"),
    (OPTION_COMPARISON_STORE, "OptionComparison"),
    (POLICY_BASIS_STORE, "PolicyBasis"),
)


def get_record(workspace_id: str, object_id: str) -> dict | None:
    for store, _kind in RESOURCE_STORES:
        record = store.get(workspace_id, object_id)
        if record is not None:
            return record
    return None