---
name: lvke-cost-drivers
description: Create and confirm immutable CostDriverSet objects with closed investment and auditable operating-cost items. Use when a confirmed BuildScaleCase must become investment, raw-material, energy, environmental, maintenance, and other FinanceSpec cost inputs.
---

# Cost Drivers

1. Require confirmed project, option, and build-scale objects.
2. Call `planning_prepare_cost_drivers` with a closed investment breakdown and evidence bindings.
3. When `annual_amount_wan` is absent, provide `annual_quantity`, `unit_consumption`, and `unit_price_yuan`; do not rely on defaults.
4. Treat `annual_quantity` as the cost calculation quantity. Treat `design_capacity` only as engineering capacity; never use it implicitly in the amount formula.
5. Include raw materials, fuel/energy, environmental operation, labor-linked cost, maintenance, insurance, lease, sales, and management items as applicable.
6. Call `planning_calculate_cost_drivers`, `planning_validate_cost_drivers`, then `planning_confirm_cost_drivers`.

Keep missing quantities, consumption, prices, and evidence as blockers. Do not reverse-engineer an item split from a total.
