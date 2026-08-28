---
name: lvke-zero-material-delivery
description: 零材料交付（一句话 → 受控假设 → 十三表与报告）的 8 个 delivery_* 工具：意图建立、假设确认、编排执行、状态查询、工件读取与取消恢复。
platforms: [linux, macos, windows]
metadata:
  conditions:
    tools_any:
      - delivery_create_from_sentence
      - delivery_start
      - delivery_status
---

# 零材料交付编排

用户只给一句话、**没有任何项目资料**时走这条路线：由服务端按行业场景生成
受控假设种子，产出带完整限制说明的估算预览件。

> 与 [one-sentence-delivery.md](../lvke-tool-coordination/references/one-sentence-delivery.md) 的关系：
> 那份合同描述的是"**有**项目资料"的情形——先
> `source_external_corpus_resolve` 定位本地资料、由 Codex 逐步编排各领域 MCP。
> 本文件描述的是"**无**项目资料"的情形，编排由 `lvke-zero-material-delivery`
> 服务端状态机完成。两条路线的判据是有没有可导入的项目资料，不是偏好；
> 有资料时不要用本路线，因为受控假设会盖掉真实项目事实。

## 标准调用顺序

```text
1. delivery_create_from_sentence(workspace_id, sentence, idempotency_key)
      → DeliveryIntent + 初始 DeliveryRun；行业歧义时返回结构化 missing_inputs
2. delivery_list_assumptions(workspace_id, assumption_package_id, limit=5..10)
      → 按敏感度排序的待确认关键参数
3. delivery_confirm_assumptions(workspace_id, assumption_package_id,
       confirmations, idempotency_key)
      → 产出新的 AssumptionPackage 与 DeliveryRun，不覆盖旧版本
4. delivery_start(workspace_id, delivery_run_id, idempotency_key)
      → 执行编排（研究 / 规划 / 财务 / 十三表 / 报告准备）
5. delivery_status(workspace_id, delivery_run_id)
      → 阶段、进度、blockers、恢复令牌
6. delivery_get_artifacts(workspace_id, delivery_run_id)
      → 逐工件的 usable / validation_status / release_grade
```

辅助入口：

| 工具 | 必填 | 用途 |
|---|---|---|
| `delivery_get` | `workspace_id`, `object_id` | 按不可变 ID 读 Intent / AssumptionPackage / DeliveryRun |
| `delivery_transition` | `+ operation`, `delivery_run_id`, `idempotency_key` | `cancel` 取消；`resume` 从已取消运行建恢复快照 |

## 读状态时的三个坑

**`query_success` 不是交付状态。** 它只表示这次查询本身成功；真实状态看
`domain_status` / `delivery_state`。

**Resource URI 可读 ≠ 可交付。** `delivery_get_artifacts` 里每个工件自带
`usable`、`validation_status`、`release_grade`；`usable=false` 的工件禁止按交付物
对外引用，哪怕它的 URI 能正常读出内容。

**顶层 `blockers` 为空不代表没问题。** 阶段链未走完属质量项，会出现在
`quality_issues` 与 `release_limitations` 而非 `blockers`（详见
`lvke-delivery-guardrails`）。但**口径非法**会进 `blockers` 并阻断：规模对账不
一致、重建来源缺记录、受控假设走正式发布。

## 门禁

- 本路线默认且只应产出 `estimate_preview`。受控假设不是项目事实，
  `project_fact_certified` 恒为 false，不可用于正式项目交付。
- 每个阶段生成新的不可变对象，上游重开会把下游标记为 stale，不原地改写。
- 写类工具都要 `idempotency_key`；同键换内容返回 `idempotency_conflict`，
  这时应换新键，而不是反复重试同一调用。
- 一句话里没有项目名称时必须停下询问一次，不得用聊天历史猜测。

## 反例

❌ 手上有项目资料却走本路线 → 受控假设会盖掉真实项目事实
❌ 拿 `query_success=true` 当交付成功 → 那只是查询成功
❌ 见到工件 URI 就当交付物引用 → 先看 `usable` 与 `release_grade`
❌ 跳过 `delivery_confirm_assumptions` 直接 start → 关键参数停在未确认状态
