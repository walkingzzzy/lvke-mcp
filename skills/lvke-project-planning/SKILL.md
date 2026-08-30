---
name: lvke-project-planning
description: Create, validate, compare, confirm, and read immutable Lvke planning objects. Use for project context, market sizing, revenue, scale, options, costs, labor, policy basis, or industry constraints.
---

# Lvke Project Planning

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Do not auto-select candidates or bypass prepare/validate/confirm governance.
- Use planning_get_object for consolidated reads; creation capabilities remain explicit.
- A SIM-A ProjectContext accepts only `promotion_id`; policy, origin,
  certification and promoted files are rederived by the service. Market,
  option, scale, cost, labor and revenue objects must retain that same verified
  promotion at every immutable boundary.
- Historical `sim_a_formal` objects without signed promotion ancestry are not
  reusable. Rebuild from TemplatePack and create new object IDs/hashes.

## MCP Tool Mapping

Machine-readable mapping: `src/lvke_mcp/runtime/skill_tool_mapping.json` (`lvke-project-planning` entry).

| Tool | Server | Required inputs | Outcome notes |
|------|--------|-----------------|---------------|
| `project_context_create` | lvke-project-planning | `workspace_id`, `context` | returns `context_id` |
| `project_context_list` | lvke-project-planning | `workspace_id` | paginated revisions; use it to find the current context instead of guessing an id |
| `planning_prepare` → `planning_confirm` | lvke-project-planning | confirmed objects | `missing_inputs` = EXPECTED_REJECTION |
| `planning_solve_build_scale` | lvke-project-planning | project context | binds BuildScaleCase |
| `planning_get_env_templates` | lvke-project-planning | `project_type`, `pollutant_types` | field templates for environmental cost plans; a template is neither a compliance verdict nor formal evidence |

Evidence tracks: `technical_fixture`, `controlled_assumption`, `formal_evidence`. Planning outputs feed finance via `propose_from_project`.
