---
name: lvke-zero-material-delivery
description: 零材料交付（一句话 → 自动公开检索 → 来源快照/EvidencePack → 字段级受控假设 → 研报预览）的 delivery_* 工具。
platforms: [linux, macos, windows]
metadata:
  conditions:
    tools_any:
      - delivery_create_from_sentence
      - delivery_start
      - delivery_status
---

# 零材料交付编排

用户只给一句话、**没有任何项目资料**时走这条路线：系统不得先要求用户补交
原始资料，而是先按项目地区、行业和报告目标自动检索公开来源。成功抓取的来源
进入不可变快照和 EvidencePack；某个字段检索不到、来源不可用或无法确定时，才
按字段生成带检索记录、依据、范围和限制的 `controlled_assumption`。最终仍只产出
`estimate_preview`，不会把公开资料或受控假设升级成项目事实。

> 与 [one-sentence-delivery.md](../lvke-tool-coordination/references/one-sentence-delivery.md) 的关系：
> 那份合同描述的是"**有**项目资料"的情形——先
> `source_external_corpus_resolve` 定位本地资料、由 Codex 逐步编排各领域 MCP。
> 本文件描述的是"**无**项目资料"的情形，编排由 `lvke-zero-material-delivery`
> 服务端状态机完成。两条路线的判据是有没有可导入的项目资料，不是偏好；
> 有资料时不要用本路线，因为受控假设会盖掉真实项目事实。

## 标准调用顺序

```text
1. delivery_create_from_sentence(workspace_id, sentence, idempotency_key
       [, report_profile_id | template_set_id])
      → DeliveryIntent + 初始 DeliveryRun；同时**选定并冻结报告配置**，
        按该配置的 required_fields 返回结构化 missing_inputs；
        行业歧义或配置无唯一匹配时阻断
2. delivery_start(workspace_id, delivery_run_id, idempotency_key)
      → 自动执行公开来源 discovery/collect/analysis_ingest，固化来源快照和
        EvidencePack；无可用来源的字段进入受控假设回退；然后串 preview
        财务/十三表/报告准备，并**自动执行技术验收**
3. delivery_list_assumptions(workspace_id, assumption_package_id, limit=5..10)
      → 按敏感度排序的待确认关键参数
4. delivery_confirm_assumptions(workspace_id, assumption_package_id,
       confirmations, idempotency_key [, skip_fields])
      → 产出新的 AssumptionPackage 与 DeliveryRun，内部自动重算；
        skip_fields 显式登记跳过项并进入交付限制
5. 七域责任人（独立上下文）逐领域做内部验收：
   review_submit_assessment(..., review_id, review_package_id, dimension)
   review_confirm_dimension(..., role_declaration, review_statement)
      → review_id / review_package_id 从 delivery_status 的
        acceptance.technical 读取
6. delivery_generate_template_pack(workspace_id, delivery_run_id, idempotency_key
       [, report_profile_id | template_set_id]
       [, confirmed_assumption_package_id])
      → 按适用标准需求生成拟定 MD/JSON 模板包；
        只产出 estimate_preview，两段验收均标 pending。
        confirmed_assumption_package_id 是乐观并发断言：确认动作会**新建**
        AssumptionPackage，声明的快照与运行当前绑定不一致时报
        confirmed_assumption_package_stale，不会按你没看过的那份答案生成
7. delivery_confirm_formal_promotion(..., responsible_party, confirmation_note)
      → **先查分级验收门禁**：技术与内部七域都通过才受理；
        然后服务端预览 promotion identity/hash，经私有 promotion-only 路径导入
        精确 SourceFile 集合，复核后持久化 FormalPromotion；
        返回 project_context_create → feasibility_start
8. delivery_status / delivery_get_artifacts
      → 只看 domain_status、usable、release_grade、acceptance 三段状态
```

## 报告配置化

报告正文、章节树与数据槽位由版本化配置决定，不写在 Python 里：

- 配置位于 `config/report_profiles/`，`manifest.v1.json` 是路由表。
- 选择规则：按 industry_code / project_type / transaction_structure /
  asset_type / report_type 确定性筛选 → 取最高 priority → **必须唯一**。
  零命中、同优先级冲突、显式指定不存在或已停用都阻断，**不套用通用模板**。
- 用户可用 `report_profile_id` 或 `template_set_id` 覆盖系统推荐；两者指向
  不同配置时报 `report_profile_request_ambiguous`。
- 每次运行冻结 `template_set_id`、版本、`content_hash` 与匹配理由。历史运行
  因此可重放；配置升级只影响新运行，旧记录保留原 hash 不重渲染。
- 运行中途配置被改动会报 `report_profile_hash_drifted`——不允许用新配置续算
  同一个 run。

## 分级验收

`delivery_status` / `delivery_get_artifacts` 返回 `acceptance` 三段：

| 段 | 由谁产生 | 状态 |
|---|---|---|
| `technical` | 系统自动（review 的 process_acceptance + feasibility 的 technical + 本域组件/hash/谱系检查） | `not_started` / `in_progress` / `passed` / `passed_with_limitations` / `failed` / `blocked` |
| `internal` | **人工**按七域确认后聚合 | `not_started` / `pending` / `in_progress` / `passed` / `passed_with_limitations` / `blocked` |
| `formal` | 资格状态，非动作 | `blocked` / `eligible` / `promoted` / `project_delivery` |

- 技术验收按五个域出结果：正文结构、研究证据、财务模型、十三表、交付谱系。
- 内部验收判据是**确认记录真的存在**，不是 review 的 `role_confirmed`
  （后者在 quick profile 下退化成"有 Assessment 即已确认"）。
- 技术验收未通过时内部验收不可能通过，正式候选也不可能生成。
- 七域齐全只到 `formal=eligible`，**不会自动晋升**：晋升是难以回退的对外动作，
  且 `responsible_party` / `confirmation_note` 必须由人提交，系统不代签。
- 技术审查没跑起来（审查域不可用、`review_id` 为空、verdict 缺失）一律
  fail-closed 进阻断项，不当成"没发现问题"。但 `technical_verdict` 在零材料
  external 过程验收下**恒为 incomplete**（缺 base_data、external 禁发布、
  内部验收尚未 finalize 三条结构性原因），这三条按披露处理；出现任何其它原因
  才阻断并给出 `review_incomplete_reason:<原因>`。
- `acceptance.technical.feasibility_validation_id` 预览阶段**恒为空**：`fdr_*`
  由晋升后的 `feasibility_start` 创建，预览阶段没有可校验对象。空值表示
  "处于预览阶段"，**不是**"校验被跳过"，不要据此判交付不合格。
- `delivery_status` / `delivery_get_artifacts` 的 `blockers` 与 `delivery_state`
  已包含技术验收阻断项；`technical_preview_ready` 在验收阻断时恒为 false。
- 限制项只能被"接受"，不能被内部确认清除。
- 关键必填字段未回答：技术预览照常生成，但 `formal` 阻断并逐条给出
  `required_field_unanswered:<字段>`。显式跳过的非关键字段只作披露
  （`required_field_skipped:<字段>`），不阻断。
- 零材料套件必然缺 `base_data` 角色（客户零资料），compliance 因此恒为
  结构性 incomplete：按限制项披露，不伪装成完整套件合规。

## 零材料资料策略

- 默认检索政策、统计、市场、技术、投资、运营、风险和反证等角度；搜索摘要不是证据。
- 只有选定 URL 经安全抓取并形成不可变 `source_snapshot_id` 后，才能进入 EvidencePack。
- 来源存在但没有明确支持某个项目字段时，不强行填入模型；该字段登记为
  `controlled_assumption`，并保留检索角度、未找到原因、区间、置信度和替换条件。
- Tavily 不可用时，返回 `upstream_failure`/`blocked` 的检索诊断，同时继续生成带
  `zero_material_public_search_fallback` 限制的技术预览；不得伪造“已联网核验”。
- 有来源也不等于项目事实认证；正式 FinanceSpec、正式报告和 Release 仍需各自的
  lineage、证据资格和人工/专业确认。
- 用户只确认行业、地区、项目边界和关键假设即可，不应被要求先提供甲方资料才能开始零材料预览。

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

- 未晋升预览只应产出 `estimate_preview`，`release_grade=technical_preview`。
  调用 `feasibility_release` / `report_export_docx(kind=formal)` 必须仍是
  `EXPECTED_REJECTION`。
- `delivery_confirm_formal_promotion` 在技术或内部验收未通过时返回
  `formal_promotion_acceptance_required`，并在 `acceptance_blockers` 里给出根因。
  这不是可绕过的提示：门禁读的是 DeliveryRun 上固化的 acceptance 并回 review 域
  取最新确认，不接受调用方自报状态。
- 晋升成功后证据政策转 `sim_a_formal`，但 `evidence_origin=sim_a_template` 必须
  保留在 lineage、manifest 与 `release_limitations` 中；文档可不显示"拟定"水印，
  模拟来源不得从谱系里消失。
- 正式验收必须生成拟定模板包并确认晋升，然后**新建** `pctx_*` / `fdr_*` /
  `evp_*` / `run_*`，禁止把 `zmr_*` 原地升级。
- `project_delivery` 仍需通过既有正式 review/feasibility 门禁，不能由
  "生成模板包"或"内部验收"直接写入。
- 公开 source import 无权声明正式资格。缺失、额外、混合或 hash 不一致时 promotion
  整体失败；历史无签名 SIM-A 不自动回填，只能全链重建。
- `sim_a_formal` 允许正式发布；对外 DOCX/XLSX 不写「拟定」水印。lineage /
  Resource JSON 必须保留 `evidence_origin=sim_a_template`。
- 禁止编造签章、公章、正式文号、银行流水、批复号、检测/审计结论。
- 每个阶段生成新的不可变对象，上游重开会把下游标记为 stale，不原地改写。
- 写类工具都要 `idempotency_key`；同键换内容返回 `idempotency_conflict`，
  这时应换新键，而不是反复重试同一调用。
- 一句话里没有项目名称时必须停下询问一次，不得用聊天历史猜测。

## 反例

❌ 手上有项目资料却走本路线 → 受控假设会盖掉真实项目事实
❌ 拿 `query_success=true` 当交付成功 → 那只是查询成功
❌ 见到工件 URI 就当交付物引用 → 先看 `usable` 与 `release_grade`
❌ 跳过 `delivery_confirm_assumptions` 直接 start → 关键参数停在未确认状态
