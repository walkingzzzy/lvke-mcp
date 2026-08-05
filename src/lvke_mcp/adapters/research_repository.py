"""Persistent stores for deep-research packages and execution state."""

from lvke_mcp.runtime.storage import JSONArtifactStore

PACKAGE_STORE = JSONArtifactStore(
    "deep-research", "packages", "drp", "packages"
)
QUALITY_REVIEW_STORE = JSONArtifactStore(
    "deep-research", "quality-reviews", "drq", "quality-reviews"
)
AGENT_SESSION_STORE = JSONArtifactStore(
    "deep-research", "agent-sessions", "drs", "sessions"
)
IDEMPOTENCY_STORE = JSONArtifactStore(
    "deep-research", "idempotency", "dridem", "idempotency"
)
AGENT_TRANSITION_STORE = JSONArtifactStore(
    "deep-research", "agent-transitions", "drstate", "transitions"
)
PLAN_STORE = JSONArtifactStore(
    "deep-research", "plan-revisions", "drplan", "plan-revisions"
)
PLAN_PROPOSAL_STORE = JSONArtifactStore(
    "deep-research", "plan-proposals", "drpp", "plan-proposals"
)
EVENT_STORE = JSONArtifactStore(
    "deep-research", "events", "drevent", "events"
)
CHECKPOINT_STORE = JSONArtifactStore(
    "deep-research", "checkpoints", "drcp", "checkpoints"
)
