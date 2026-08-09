---
name: lvke-revenue-drivers
description: Create, compare, validate, and confirm immutable RevenueDriverSet objects from a confirmed market case. Use when market demand must become product sales, property sales, tourism, government-payment, or explicitly evidenced flat revenue for FinanceSpec.
---

# Revenue Drivers

1. Require a confirmed `MarketSizingCase`; do not create unconstrained demand.
2. Call `planning_prepare(object_kind="revenue_drivers")` with one or more named candidates and full operating-year curves in `payload`.
3. Use registered models: `product_sales`, `property_sales`, `tourism`, `gov_payment`, or `flat`.
4. Use `flat` in `review_candidate` only with source ID, hash, locator, and evidence track. For `source_reconstructed`, also require reconstruction ID, source URI/kind, method, and limitations.
5. Call `planning_compare(object_kind="revenue_drivers")` and `planning_validate(object_kind="revenue_drivers")`.
6. Explicitly select one candidate and list every rejection in `planning_confirm(object_kind="revenue_drivers")`.

Source-reconstructed flat revenue may support `process_acceptance` while remaining `project_fact_certified=false`; it cannot qualify a `project_delivery` release.
