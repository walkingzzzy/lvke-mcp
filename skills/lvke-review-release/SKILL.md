---
name: lvke-review-release
description: Review Lvke deliverables for evidence, consistency, numerics, format, policy, risk, decision support, and knowledge quality. Use for findings, remediation, deterministic rescoring, retest, comparison, or review export. This workflow has no authentication, role, permission, or security-signoff step.
---

# Lvke Review Release

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

For a five-material research-report suite or seven-domain review, route to the sibling `lvke-research-report-review` Skill. This Skill retains legacy deliverable finding/retest/release orchestration.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Keep review, remediation, retest, scoring, evidence, and export deterministic and traceable.
- Never infer external approval or legal/professional signoff from a technical quality pass.
- Formal delivery requires `Review -> remediation evidence -> Retest -> Export
  -> Release`. Revalidate canonical promotion metadata and immutable parent
  hashes at prepare, start, retest and export; a track label cannot upgrade a review.
- Retest creates a new immutable child revision and preserves the original
  promotion while binding exact remediation evidence. Export manifests retain
  origin and promotion metadata and must pass record/file integrity checks.

## MCP Tool Mapping

Machine-readable mapping: `src/lvke_mcp/runtime/skill_tool_mapping.json` (`lvke-review-release` entry).

| Tool | Server | Required inputs | Outcome notes |
|------|--------|-----------------|---------------|
| `review_prepare` | lvke-deliverable-review | `target: {target_type, target_id}` | not `target_kind` |
| `review_start` → `review_list_findings` | lvke-deliverable-review | `review_preparation_id` | sync quick mode for G1 |
| `review_get_finding` | lvke-deliverable-review | `review_id`, `finding_id` | full evidence, recalculation trace, standard basis and disposition history for one finding |
| `review_resolve_standards` → `review_list_requirements` | lvke-deliverable-review | `project_context`, `facilities` / `standard_applicability_id` | applicability only, never a compliance verdict |
| `review_attach_requirement_evidence` | lvke-deliverable-review | `requirement_id`, `resource_uri`, `locator`, `content_hash` | accepts only already-immutable data-acquisition / data-analysis Resources; free text is refused |
| `review_validate_standards` | lvke-deliverable-review | `standard_applicability_id` | returns pending / fixture-satisfied / attached-awaiting-review; **never** returns "project complies with the national standard" |
| `review_retest` → `review_export` | lvke-deliverable-review | remediation evidence | formal export needs EVD-2 or `sim_a_formal` |

Evidence tracks: formal export accepts `formal_evidence` or promoted `sim_a_formal`. Unpromoted SIM-A / controlled_assumption remains `process_acceptance` only. `process_acceptance` vs `project_delivery` via `project_context.review_purpose`.
