---
name: lvke-feasibility-study
description: Orchestrate complete Lvke feasibility-study work across evidence, planning, finance, report, review, and release. Use for end-to-end feasibility delivery or when deciding the next governed domain stage.
---

# Lvke Feasibility Study

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Keep every immutable object boundary and explicit confirmation step.
- For zero-material requests, do not ask for client materials as a prerequisite: run public
  discovery and safe snapshot collection first, then fill only unsupported fields with
  disclosed controlled assumptions.
- Do not claim formal delivery until review and release gates pass.
- Asset-acquisition preview reports may be technically ready while
  `formal_release_eligible=false`; formal artifacts require a qualified
  `formal_candidate` run.
- Build preview reports through `report_prepare` with
  `finance_binding.kind=asset_acquisition`; classify direct preview/process
  formal-artifact calls as `EXPECTED_REJECTION`.
- SIM-A formal orchestration starts with `TemplatePack -> FormalPromotion ->
  ProjectContext`; every completed stage reloads its immutable objects and
  verifies the same promotion. Caller-supplied certification fields never
  establish qualification.
- Unsigned historical SIM-A records fail closed. Rebuild in order:
  `TemplatePack -> FormalPromotion -> SourceFile -> ProjectContext ->
  EvidencePack -> FinanceFactPack -> FinanceSpec -> BoE -> FinanceRun ->
  Tables -> Report -> Review -> Retest -> Release`.

## MCP Tool Mapping

Machine-readable mapping: `src/lvke_mcp/runtime/skill_tool_mapping.json` (`lvke-feasibility-study` entry).

| Tool | Server | Required inputs | Outcome notes |
|------|--------|-----------------|---------------|
| `feasibility_start` / `feasibility_status` | lvke-feasibility-delivery | `workspace_id`, `project_context_id` | binds `delivery_run_id` |
| `feasibility_stage` | lvke-feasibility-delivery | `delivery_run_id`, `stage`, `status`, `idempotency_key` | 每完成一个领域对象必须绑 `input_refs`/`output_refs`/`basis_hash`；`bind_workspace_lineage=true` 可用工作区最新对象补全当前阶段 |
| `feasibility_next_actions` | lvke-feasibility-delivery | `delivery_run_id` | 若工作区已有未绑定对象，会给出带 `output_refs` 的 `feasibility_stage` 调用 |
| `feasibility_validate` | lvke-feasibility-delivery | `delivery_run_id` | `quality_passed=false` ≠ release pass |
| `feasibility_release` | lvke-feasibility-delivery | validated run | blocked without evidence gate |
| `feasibility_resume` | lvke-feasibility-delivery | `checkpoint_id`, `idempotency_key` | creates a NEW immutable run snapshot from a checkpoint; the original run is never mutated |
| `lvke_list_resources` / `lvke_read_resource` | lvke-feasibility-delivery | `domain` / `uri` + explicit `workspace_id` | raw cross-domain Resource access; URIs are never rewritten and cross-workspace reads stay fail-closed |

Knowledge closure (lvke-knowledge-governance): `knowledge_submit_candidate` → `knowledge_create_snapshot` → `knowledge_review_candidate` → `knowledge_publish_release`. `knowledge_list_candidates` / `knowledge_get_candidate` are read-only; only an `accepted` candidate may be published, and a snapshot is content-addressed so it cannot be edited after the fact.

Evidence tracks: `technical_fixture`, `controlled_assumption`, `source_reconstructed`, `formal_evidence`, `sim_a_formal`. Orchestrates domain Skills; do not skip immutable object boundaries between stages.
