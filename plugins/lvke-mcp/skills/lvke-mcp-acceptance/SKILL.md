---
name: lvke-mcp-acceptance
description: Run live conversational acceptance for the 14 Lvke MCP services after code freeze and one restart. Use for MCP full testing, compressed-tool migration validation, or post-restart acceptance.
---

# Lvke Mcp Acceptance

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `REFERENCE.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Freeze code, restart once, then use real MCP calls rather than direct Python handlers.
- Do not claim formal acceptance until every required live call and gate check passes.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.

