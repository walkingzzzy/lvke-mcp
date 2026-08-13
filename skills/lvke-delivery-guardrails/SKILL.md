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
- P0A verification does not complete P0B. Keep P0B pending until real dual-track
  approval data is provided, and never record a passing build while work is skipped.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.
