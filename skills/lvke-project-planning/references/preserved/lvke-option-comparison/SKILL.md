---
name: lvke-option-comparison
description: "Prepare, score, review, and explicitly confirm immutable Lvke OptionComparison objects for equipment, building, process, site, or operating-model choices. Use when Codex must compare technical alternatives without delegating the final judgment to MCP."
---

# 方案比选

要求同一 workspace 的 `ProjectContext`，并尽量绑定已固化的市场、规模、成本或证据对象。MCP 只确定性复算得分；Codex 或真实决策人负责选择和说明理由。

## 工作流

1. 选择 `equipment`、`building`、`process`、`site` 或 `operating_model`。
2. 定义唯一指标 ID、权重、单位和 `higher_is_better/lower_is_better`；权重合计必须为 1。
3. 每个方案提供全部指标值、逐指标证据定位和强制约束结果。
4. 调用
   `planning_prepare(object_kind='option_comparison', project_context_id=..., idempotency_key=..., payload={...})`
   → `planning_validate(object_kind='option_comparison', target_id=<option_comparison_id>)`
   → `planning_score_option_comparison`，审阅 min-max 归一化、加权贡献、可行性与排名。
5. 由 Codex/人员选择一个可行方案，写明取舍理由，并列出全部未选方案。
6. 调用
   `planning_confirm(object_kind='option_comparison', target_id=<option_comparison_id>, idempotency_key=..., payload={...})`
   生成新的 confirmed `OptionComparison`，再用
   `planning_get_object(object_type='OptionComparison', object_id=<option_comparison_id>)`
   回读；候选对象保持只读。

## 聚合入口调用形状

`planning_validate` 只需 `object_kind` 与 `target_id`。`planning_prepare` 与
`planning_confirm` 还要求判别式 `payload`，其允许字段按 `object_kind` 分支严格约束
（`additionalProperties: false`）；`workspace_id`、`project_context_id`/`target_id`
与 `idempotency_key` 由顶层传，不放进 `payload`。

`option_comparison` 分支的确认在服务端仍沿用历史 operation 命名，调用方无需关心，
但同一 `idempotency_key` 重放时以该命名空间为准。

## 门禁

- 搜索摘要不得作为指标证据；每个数值必须有 locator 和 content hash。
- 未通过强制约束的方案不得被选中。
- 得分领先只是计算结果，不是自动决策；允许有理由地选择非最高分方案。
- 不取平均、不隐式合并方案、不把技术比选写成已审批设计结论。
- 指标、权重、证据或选择变化时创建新对象，不覆盖旧对象。
