---
name: lvke-research-recovery
description: Revise, checkpoint, cancel, resume, and quality-confirm immutable Deep Research work. Use when a research task is partial, blocked, interrupted, cancelled, has source conflicts, needs source changes, or must become eligible for a formal market case without losing lineage.
---

# Research Recovery

Use `dr_get_plan`, `dr_propose_plan_revision`, and `dr_apply_plan_revision` for plan changes. Use `dr_add_sources` or `dr_remove_sources` with source hashes and locators. Use `dr_create_checkpoint` before interruption and `dr_resume` or `dr_continue` afterward.

`dr_submit` always creates a `partial` package. After submission:

1. Inspect the source count, usable source count, query rounds, citation coverage, missing fields, and conflicts.
2. Call `dr_confirm_quality`.
3. Accept material limitations only when they are explicit. For `source_reconstructed`, keep `project_fact_certified=false`.
4. Use only the newly completed package with its `quality_review_id` in MarketSizingCase and delivery stages.

A failed quality confirmation must produce no completed package. Historical invalid or stale reviews are not reusable.
