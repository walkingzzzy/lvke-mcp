---
name: lvke-finance-spec
description: Prepare, validate, build a Basis of Estimate for, and confirm an evidence-aware FinanceSpec v3. Use when planning objects must become finance inputs, when finance validation reports missing fields, or when source_reconstructed inputs need process-acceptance lineage before a FinanceRun.
---

# FinanceSpec v3

Create the finance contract without calculating indicators.

## Workflow

1. Read confirmed `ProjectContext`, `OptionComparison`, `BuildScaleCase`, `CostDriverSet`, `LaborPlan`, `RevenueDriverSet`, and `EvidencePack` objects.
2. Call `finance_prepare_fact_pack`, then `finance_confirm_fact_pack` when the fact selection is complete.
3. Call `finance_prepare_spec`. Accept historical v1/v2 input only as migration input; require the saved result to report `finance_spec.v3`.
4. Call `finance_validate_spec` before confirmation. Return `missing_inputs` without filling defaults.
5. Call `finance_build_basis_of_estimate`; require method, selection reason, locator, content hash, and evidence classification for every material input.
6. Call `finance_confirm_spec` only after validation and BoE completion.

For `source_reconstructed`, propagate `evidence_policy`, `project_fact_certified=false`, reconstruction records, source IDs, unresolved inputs, and release limitations. Use it only for `process_acceptance`. Never relabel a template or report-derived value as an original project BoE.

Do not calculate IRR/NPV, render tables, write report prose, or invent missing values.
