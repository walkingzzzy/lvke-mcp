---
name: lvke-delivery-guardrails
description: Enforce Lvke delivery discipline and proposal/apply boundaries. Use whenever changing delivery code, declaring completion, applying report revisions, or deciding whether release is permitted.
---

# Lvke Delivery Guardrails

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Preserve evidence, deterministic validation, quality review, revision, and lineage boundaries.
- Do not add authentication, roles, tenant isolation, RBAC, permission management, or security signoff.
- Report blockers honestly and distinguish technical completion from formal acceptance.
- Treat preview/process formal-artifact refusal as `EXPECTED_REJECTION`, not an
  MCP transport failure; route preview reporting through
  `report_prepare(finance_binding.kind=asset_acquisition)`.
- `sim_a_formal` 允许正式发布；对外可不标「拟定」，但 lineage 必须保留
  `evidence_origin`。仍禁止伪造签章、文号、流水、批复号与检测/审计结论。
- `sim_a_formal` is granted only by a verified FormalPromotion chain. Missing,
  mixed, tampered, cross-workspace or unsigned historical ancestry is a hard
  stop; never repair it by copying labels or backfilling hashes.
- P0A verification does not complete P0B. Keep P0B pending until real dual-track
  approval data is provided, and never record a passing build while work is skipped.

## MCP Tool Mapping

Machine-readable mapping: `src/lvke_mcp/runtime/skill_tool_mapping.json` (`lvke-delivery-guardrails` entry).

| Tool | Server | Purpose |
|------|--------|---------|
| `report_propose` / `report_diff` / `report_apply` | lvke-report-generation | governed revision boundary |

Distinguish technical completion (`process_acceptance`) from formal release (`project_delivery`). `idempotency_conflict` = EXPECTED_REJECTION.
