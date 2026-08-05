"""Persistence for report preparations, task bindings, and revisions."""

from lvke_mcp.runtime.storage import JSONArtifactStore

PREPARATION_STORE = JSONArtifactStore(
    "report-generation", "preparations", "rprep", "preparations"
)
BINDING_STORE = JSONArtifactStore(
    "report-generation", "task_bindings", "rjob", "jobs"
)
REVISION_STORE = JSONArtifactStore(
    "report-generation", "revisions", "rrv", "revisions"
)