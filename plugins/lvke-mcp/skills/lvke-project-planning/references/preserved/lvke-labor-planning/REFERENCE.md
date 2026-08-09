---
name: lvke-labor-planning
description: "Create reproducible Lvke LaborPlan objects by role category, position, headcount, average annual wage, and welfare rate. Use when Codex must derive staffing, wage, and benefit inputs from a confirmed build scale for finance and report work."
---

# 劳动定员

要求同一 workspace 的 `ProjectContext` 和 confirmed `BuildScaleCase`。按岗位计算，不用行业默认人数或工资静默补齐。

## 工作流

1. 为每个岗位提供类别、唯一名称、工作量、单人班次能力、班次数、覆盖因子、自动化调整、人均年工资、福利率和依据。
2. 调用 `planning_infer_labor_plan`，审阅 ceil 推导轨迹；再调用
   `planning_validate(object_kind='labor_plan', target_id=<labor_plan_id>)`。
3. Codex 提交确认理由，调用
   `planning_confirm(object_kind='labor_plan', target_id=<labor_plan_id>, idempotency_key=..., payload={...})`，
   再以 `planning_get_object(object_type='LaborPlan', object_id=<labor_plan_id>)`
   读取 confirmed revision。
4. 读取 `/labor_plan`、`/cost_items/工资` 和 `/cost_items/福利` ledger。
5. 与 CostDriverSet 合并时检查同名成本冲突；冲突必须由 Codex 明确选择口径并生成新对象。
6. 把 `FinanceInputRevision` ledger 交给财务服务重新校验。

## 门禁

- 空岗位、重复岗位、非正人数、负工资或非法福利率均停止。
- 受控假设必须保留其 evidence track，不能写成真实用工承诺。
- 定员变化时创建新的 LaborPlan 和下游财务版本，不修改已固化对象。
- `planning_create(object_kind='labor_plan', ...)` 仅为兼容入口；
  新正式流程不得直接填总人数绕过工作量推导。

## 聚合入口调用形状

`planning_validate` 只需 `object_kind` 与 `target_id`；`planning_confirm` 与
`planning_create` 还要求判别式 `payload`，其允许字段按分支严格约束
（`additionalProperties: false`）。`workspace_id`、目标 ID 与 `idempotency_key`
由顶层传，不放进 `payload`。读取一律走 `planning_get_object`。
