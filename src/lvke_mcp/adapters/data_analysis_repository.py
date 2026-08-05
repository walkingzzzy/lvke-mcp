"""Persistent stores shared by data-analysis producers and object consumers."""

from lvke_mcp.runtime.storage import JSONArtifactStore

INGEST_STORE = JSONArtifactStore("data-analysis", "ingest_tasks", "analysis", "tasks")
EVIDENCE_STORE = JSONArtifactStore(
    "data-analysis", "evidence_packs", "evp", "evidence-packs"
)
CANDIDATE_STORE = JSONArtifactStore(
    "data-analysis", "candidate_sets", "cset", "candidate-sets"
)
PROFILE_STORE = JSONArtifactStore(
    "data-analysis", "data_profiles", "profile", "profiles"
)
NORMALIZED_COMPARE_STORE = JSONArtifactStore(
    "data-analysis", "normalized_comparisons", "ncmp", "normalized-comparisons"
)
FINANCIAL_TREND_STORE = JSONArtifactStore(
    "data-analysis", "financial_trends", "ftrend", "financial-trends"
)
BENCHMARK_COMPARISON_STORE = JSONArtifactStore(
    "data-analysis", "benchmark_comparisons", "bench", "benchmark-comparisons"
)

RESOURCE_STORES = (
    (INGEST_STORE, "task"),
    (EVIDENCE_STORE, "evidence_pack"),
    (CANDIDATE_STORE, "candidate_set"),
    (PROFILE_STORE, "profile"),
    (NORMALIZED_COMPARE_STORE, "normalized_comparison"),
    (FINANCIAL_TREND_STORE, "financial_trend"),
    (BENCHMARK_COMPARISON_STORE, "benchmark_comparison"),
)


def resolve_resource(uri: str, workspace_id: str) -> dict | None:
    expected = f"lvke://data-analysis/workspaces/{workspace_id}/"
    if not str(uri).startswith(expected):
        return None
    for store, _kind in RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None:
            return record
    return None
