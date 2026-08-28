---
name: lvke-error-recovery
description: Recover Lvke work from locks, provider failures, stale objects, proposal conflicts, finance inconsistency, or interrupted jobs. Use when a governed workflow is blocked, cancelled, stale, or resumable.
---

# Lvke Error Recovery

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Diagnose before retrying and use checkpoint/resume where available.
- Do not loop on the same failure or hide a blocker by changing acceptance criteria.

## MCP Tool Mapping

Recovery Skill — use `trace_id`, `blockers`, and `next_actions` from any MCP envelope. Cross-service resume: `feasibility_checkpoint`, `dr_resume`, `source_parse_retry`.

Required inputs: `workspace_id`, failing `trace_id`. Never treat `invalid_tool_output` as retryable without fixing the handler/schema mismatch first.

