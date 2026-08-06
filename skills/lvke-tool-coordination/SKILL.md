---
name: lvke-tool-coordination
description: Coordinate Lvke MCP services, workspace navigation, precedent-first drafting, and multi-stage tool calls. Use when a task spans multiple Lvke domains or needs a resumable call sequence.
---

# Lvke Tool Coordination

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Use the compressed tool names from dev-docs/config/mcp-compression-migration.json.
- Preserve workspace scope, object IDs, lineage, and checkpoint/resume behavior.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.
