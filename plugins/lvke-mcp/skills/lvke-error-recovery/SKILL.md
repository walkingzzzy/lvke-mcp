---
name: lvke-error-recovery
description: Recover Lvke work from locks, provider failures, stale objects, proposal conflicts, finance inconsistency, or interrupted jobs. Use when a governed workflow is blocked, cancelled, stale, or resumable.
---

# Lvke Error Recovery

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `REFERENCE.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Diagnose before retrying and use checkpoint/resume where available.
- Do not loop on the same failure or hide a blocker by changing acceptance criteria.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.

