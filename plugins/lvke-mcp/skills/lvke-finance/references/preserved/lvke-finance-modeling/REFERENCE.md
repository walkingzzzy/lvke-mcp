---
name: lvke-finance-modeling
description: Run and inspect Lvke's deterministic finance model from a confirmed FinanceSpec v3 and Basis of Estimate. Use for FinanceRun creation, cash flow, debt service, tax, IRR, NPV, payback, balance, scenario, and consistency results with immutable lineage.
---

# Deterministic Finance Modeling

## Workflow

1. Require a confirmed `spec_id`, its `spec_hash`, and the associated BoE. Redirect missing or unconfirmed inputs to `lvke-finance-spec`.
2. Call `finance_run_model` once for the selected spec and mode.
3. Read `finance_get_run(view=checks)` and `finance_get_run(view=full)`.
4. Confirm the run succeeded, uses the expected `spec_id` and input hash, and contains no open consistency blocker.
5. For operating projects, inspect project and equity cash flows, IRR/NPV, payback, DSCR and ICR. For non-operating projects, report funding balance and do not fabricate an IRR.
6. Pass the immutable `run_id` to `lvke-finance-tables`; do not pass loose financial numbers.

Keep `estimate_preview`, `review_candidate`, and `process_acceptance` distinct. A `source_reconstructed` run remains `project_fact_certified=false`. Read all report numbers from this run; never recompute or substitute workbook values in prose.
