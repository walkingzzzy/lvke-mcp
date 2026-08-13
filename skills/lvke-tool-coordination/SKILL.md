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
- Treat preview/process acquisition artifact rejection as `EXPECTED_REJECTION`;
  route the restricted report through `report_prepare` with
  `finance_binding.kind=asset_acquisition`.
- Require one shared complete build identity across all 14 services after code
  freeze; stale metadata or a dirty tracked checkout is incomplete.
- Use restarted live `tools/list` as the sole coverage denominator, call every
  listed tool at least once, and keep every tool at `taskSupport=forbidden`.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.
