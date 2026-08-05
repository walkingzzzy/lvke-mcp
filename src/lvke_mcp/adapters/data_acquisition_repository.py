"""Persistent stores shared by data-acquisition producers and consumers."""

from lvke_mcp.runtime.storage import JSONArtifactStore

SOURCE_STORE = JSONArtifactStore(
    "data-acquisition", "source_snapshots", "src", "sources"
)
SEARCH_STORE = JSONArtifactStore(
    "data-acquisition", "search_sets", "search", "search-sets"
)
DISCOVERY_STORE = JSONArtifactStore(
    "data-acquisition", "discovery_sets", "discovery", "discovery-sets"
)
COLLECTION_STORE = JSONArtifactStore(
    "data-acquisition", "source_collections", "collection", "collections"
)
URL_AUDIT_STORE = JSONArtifactStore(
    "data-acquisition", "url_audits", "urlaudit", "url-audits"
)
VISUAL_CAPTURE_STORE = JSONArtifactStore(
    "data-acquisition", "visual_captures", "vcap", "visual-captures"
)

RESOURCE_STORES = (
    (SOURCE_STORE, "source_snapshot"),
    (SEARCH_STORE, "search_set"),
    (DISCOVERY_STORE, "discovery_set"),
    (COLLECTION_STORE, "source_collection"),
    (URL_AUDIT_STORE, "url_audit"),
    (VISUAL_CAPTURE_STORE, "visual_capture"),
)


def resolve_resource(uri: str, workspace_id: str) -> dict | None:
    expected = f"lvke://data-acquisition/workspaces/{workspace_id}/"
    if not str(uri).startswith(expected):
        return None
    for store, _kind in RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None:
            return record
    return None
