---
name: lvke-market-sizing
description: Create, compare, validate, and explicitly confirm traceable MarketSizingCase objects. Use when market capacity, regional demand, supply-demand gaps, target share, or target volume must be derived from multiple EvidencePack-backed methods before scale, revenue, finance, or report work.
---

# Market Sizing

1. Require a validated `ProjectContext`, a completed quality-confirmed `ResearchPackage`, and an `EvidencePack`.
2. Build at least two independent methods with consistent period, region, unit, market size, target share, and target volume.
3. Pass locator objects directly from the EvidencePack when available. Legacy locator strings are accepted, but do not reserialize objects with ad hoc spacing.
4. Call `planning_prepare(object_kind="market_case")`, `planning_compare(object_kind="market_case")`, and `planning_validate(object_kind="market_case")`.
5. Explicitly choose one path and list every rejected candidate in `planning_confirm(object_kind="market_case")`. Do not average alternatives.
6. Pass the confirmed case to option comparison, build scale, and revenue drivers.

Reject search summaries as evidence. Preserve `source_reconstructed` and `project_fact_certified=false`; a partial research package without `dr_confirm_quality` cannot support a formal market case.
