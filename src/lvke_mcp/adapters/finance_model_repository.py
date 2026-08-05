"""Persistence for finance-model specifications and analysis artifacts."""

from lvke_mcp.runtime.storage import JSONArtifactStore

SPEC_STORE = JSONArtifactStore("finance-model", "specs", "fsp", "specs")
IDEMPOTENCY_STORE = JSONArtifactStore(
    "finance-model", "idempotency", "fidem", "idempotency"
)
BALANCE_SHEET_STORE = JSONArtifactStore(
    "finance-model", "balance-sheets", "fbs", "balance-sheets"
)
MONTE_CARLO_STORE = JSONArtifactStore(
    "finance-model", "monte-carlo", "fmc", "monte-carlo"
)
BASIS_OF_ESTIMATE_STORE = JSONArtifactStore(
    "finance-model", "basis-of-estimate", "fboe", "basis-of-estimate"
)
FACT_PACK_STORE = JSONArtifactStore(
    "finance-model", "fact-packs", "ffp", "fact-packs"
)
