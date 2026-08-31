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

## 内联 spec 与 `spec_id` 两路等价，但分组提升只对 flat 生效

`finance_prepare_spec` / `finance_run_model` 既接受内联 `spec`，也接受已固化的
`spec_id`，两路在计算层等价——内联 spec 会被 stamp 并重算 `spec_hash` 后走同一条
规范化。

但计算层只认**扁平顶层键**，业务分组必须提升才读得到。提升范围按 `revenue.model`
分档：

| `revenue.model` | 提升的分组 |
|---|---|
| `flat` | `cost`、`tax`、`revenue` |
| 其余（`tourism` / `product_sales` / `property_sales` / `gov_payment` / `rail_transit` / …） | 仅 `cost`、`tax` |

原因是这两类 `revenue.annual_revenue_wan` 的**性质不同**：flat 模型的驱动就是
`annual_revenue_wan` 本身，不提升就被静默丢弃、达产营收退回"投资额×30%"派生基线
（实测 97,680 变 20,520，利润总额由 +40,614.71 翻成 −35,631，NPV 由 +164,301.87
翻成 −246,330.44，IRR 无解，而 `consistency_ok` 仍为 true）；非 flat 模型另有自己的
量价驱动，其 `annual_revenue_wan` 只是**由那些驱动折算出的回显值**，与显式
`input_revision` 常有正常舍入差（实测 12000.0 vs 12000.3），当输入提升会把舍入差
判成 `candidate_input_conflict` 并 fail-closed。

所以：**非 flat 模型不要靠嵌套 `revenue.annual_revenue_wan` 传营收**，把量价驱动写
进 `revenue` 组、或先 `finance_confirm_spec` 拿 `spec_id` 再运行。分组与
`finance_inputs` 同键不同值一律返回 `candidate_input_conflict`（带 `path` 与
`conflicts_with`），不做静默择一。

Do not calculate IRR/NPV, render tables, write report prose, or invent missing values.
