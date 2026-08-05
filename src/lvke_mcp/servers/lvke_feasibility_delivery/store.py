"""Immutable stores used by the feasibility delivery orchestration layer."""

from __future__ import annotations

from lvke_mcp.runtime.storage import JSONArtifactStore

RUN_STORE = JSONArtifactStore(
    "feasibility-delivery", "runs", "fdr", "runs"
)
CHECKPOINT_STORE = JSONArtifactStore(
    "feasibility-delivery", "checkpoints", "fdc", "checkpoints"
)
RELEASE_STORE = JSONArtifactStore(
    "feasibility-delivery", "releases", "fdrp", "releases"
)
IDEMPOTENCY_STORE = JSONArtifactStore(
    "feasibility-delivery", "idempotency", "fdi", "idempotency"
)

RESOURCE_STORES = (
    (RUN_STORE, "FeasibilityDeliveryRun"),
    (CHECKPOINT_STORE, "FeasibilityCheckpoint"),
    (RELEASE_STORE, "FeasibilityRelease"),
)
