---
name: lvke-cost-drivers
description: Create and confirm immutable CostDriverSet objects with closed investment and auditable operating-cost items. Use when a confirmed BuildScaleCase must become investment, raw-material, energy, environmental, maintenance, and other FinanceSpec cost inputs.
---

# Cost Drivers

1. Require confirmed project, option, and build-scale objects.
2. Call `planning_prepare(object_kind="cost_drivers")` with a closed investment breakdown and evidence bindings in `payload`.
3. When `annual_amount_wan` is absent, provide `annual_quantity`, `unit_consumption`, and `unit_price_yuan`; do not rely on defaults.
4. Treat `annual_quantity` as the cost calculation quantity. Treat `design_capacity` only as engineering capacity; never use it implicitly in the amount formula.
5. Include raw materials, fuel/energy, environmental operation, labor-linked cost, maintenance, insurance, lease, sales, and management items as applicable.
6. Call `planning_calculate_cost_drivers`, `planning_validate(object_kind="cost_drivers")`, then `planning_confirm(object_kind="cost_drivers")`.

Keep missing quantities, consumption, prices, and evidence as blockers. Do not reverse-engineer an item split from a total.

## `conversion_to_wan` 是乘数，不是进率

它把 `annual_quantity × unit_consumption × unit_price_yuan` 的乘积**乘**成万元，
不是"1 万元 = 10000 元"的进率：

| 单价计价单位 | `conversion_to_wan` | 含义 |
|---|---|---|
| 元（`unit_price_yuan` 常态） | `0.0001` | 等于 ÷10000；省略时的默认值 |
| 已按万元计价 | `1` | 不再换算 |

填 `10000` 会把金额放大 1 亿倍，且 schema 上有 `exclusiveMinimum:0` /
`maximum:1`，该值会被直接拒绝。

**方向填反会被成本量级对账拦住。** `planning_validate(object_kind="cost_drivers")`
比较年运营成本合计与总投资（建设投资 + 建设期利息 + 流动资金），比值超过 **100 倍**
时判口径非法并阻断，返回 `project_scale_inconsistent:annual_operating_cost_vs_total_investment`
+ 字段级 `cost_magnitude_implausible`（带实际比值与阈值）。这条不是置信度提示，
不能放行。碰到它先查两处：`conversion_to_wan` 方向是否用反、`unit_price_yuan`
的单位是否真的是元。
