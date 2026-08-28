---
name: lvke-feasibility-study
description: Orchestrate a complete Lvke feasibility-study object chain across existing MCP services and project Skills. Use for end-to-end project research, market sizing, option selection, scale, drivers, FinanceSpec, FinanceRun, thirteen tables, nine-chapter report, review remediation, and process or project release.
---

# Feasibility Study Orchestration

## Fixed sequence

`project -> research -> market -> option -> scale -> drivers -> finance_spec -> finance_run -> finance_tables -> report -> review -> released`

1. Create and validate ProjectContext, then call `feasibility_start` with its real object ID.
2. Acquire public sources only through existing `data_discover`, `data_search`, `data_fetch`, `data_collect`, and `data_import_external_snapshot`. Use source-files for current-repository `docs` attachments.
3. Build EvidencePack; run Deep Research and require `dr_confirm_quality` after `dr_submit`.
4. Run `lvke-market-sizing`, option comparison, build scale, cost, labor, and revenue Skills in order.
5. Run `lvke-finance-spec`, `lvke-finance-modeling`, and `lvke-finance-tables` from one lineage.
6. Generate all nine chapters. For every chapter call `report_propose_section -> report_diff -> report_apply -> report_validate_section` and bind project, evidence, run, table package, locators, and upstream hashes.
7. Run `review_prepare -> review_start -> review_list_findings`. Remediate with the report proposal flow, disposition each finding, and call `review_retest`.
8. After each domain step, call `feasibility_stage(status=completed)` with real input/output refs and the output basis hash. If the object already exists in the workspace, `feasibility_stage(..., bind_workspace_lineage=true)` or `feasibility_next_actions` can fill the current stage. Use `feasibility_next_actions` for recovery.
9. Call `feasibility_validate(scope=formal)` before `feasibility_release`.

For `source_reconstructed`, set `release_scope=process_acceptance`, propagate all reconstruction metadata, and keep `project_fact_certified=false`. `project_delivery` must return `project_fact_evidence_missing`. Never use controlled assumptions or technical fixtures in formal release.

For asset acquisition previews, bind the report chain with
`report_prepare.finance_binding.kind=asset_acquisition`. A technically valid
preview/process-acceptance report is restricted and must expose
`formal_release_eligible=false`; it cannot be used to create a formal
acquisition artifact. Formal acquisition artifacts require a separate
`formal_candidate` run that passes the artifact qualification gate.

Knowledge governance is required only when the run binds a knowledge candidate. Then require accepted review and a KnowledgeRelease before delivery release.
