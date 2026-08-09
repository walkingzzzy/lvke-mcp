---
name: lvke-industry-context
description: Resolve the primary project industry route and read versioned planning constraints from ProjectContext. Use before market, option, scale, cost, revenue, finance, or acquisition work when industry_code, asset_type, project_type, or transaction structure determines the applicable business workflow.
---

# Industry Context

1. Require a validated immutable `ProjectContext`.
2. Call `planning_resolve_industry_skill` and require one unambiguous route.
3. Call `planning_get_industry_constraints` where deterministic planning parameters exist.
4. Treat packaged industry parameters as `technical_fixture` until bound to project evidence; never silently promote them to project facts.
5. Route hotel acquisition, cultural tourism, real estate, manufacturing, public service, and generic projects to their corresponding planning and finance workflows.

If no route exists, revise the ProjectContext or packaged route manifest. Do not silently use a generic industry default.
