---
name: lvke-deep-research
description: >
  Independent deep research on policy, market, industry, technology, risks,
  or comparable projects. Mandates multi-source public search before drafting
  findings (via lvke-source-acquisition). Use when planning, starting,
  continuing, checking, or packaging a research run. Preserves partial,
  quality, citation, budget, and checkpoint semantics. Not for: source
  fetching alone, FinanceSpec or financial calculations, report assembly,
  or DR engine code and tests.
---


# Deep Research 工作流

DR 是独立研究体系，不是最终可研报告生成器，也不是公开来源采集或财务数据工具。当前 Agent 负责检索、判断和研究文字；DR MCP 负责研究会话、来源 locator、状态、lineage 与 research package，不运行第二个 LLM。

## 硬门禁：多源搜索

依赖公开网资料时，**必须先完成** `lvke-source-acquisition` 多源门禁（≥2 通道、≥3 查询角、正文固化或显式阻断），再 `dr_submit`。

禁止：单次搜索摘要 → 直接写研究发现 / 提交 `report_md`。

## 标准顺序

```text
dr_prepare
→ 如有必要在对话中补充澄清
→ dr_start
→ dr_get_plan
→ 多源采集（lvke-source-acquisition）→ snapshots / evidence pack
→ dr_add_sources（显式 source_type / evidence_track / locator / hash）
→ 基于带 locator 的来源撰写研究发现
→ dr_submit(report_md, citations, evidence_pack_ids/source_snapshot_ids)
→ dr_confirm_quality(query_rounds, usable_source_count, citation_coverage, missing_fields, conflicts)
→ dr_status / dr_get_bundle
→ research_package_id + Resources
```

## Tavily Hikari 边界

- 可在 `dr_prepare` 前用 `tavily_hikari.tavily_search`/`tavily_extract` 补充高质量公开候选，但必须与 `data_discover` 的独立 provider 聚合至少再开一路，且正文须经 `data_import_external_snapshot → analysis_ingest`。
- Tavily 不可用时保留 `upstream_failure/partial`；不得调用已注销的内置 Web Search 作隐式回退。
- `tavily_research` 生成文本不能直接变成 Lvke `research_package_id`；需要带来源 locator 经 `dr_submit` 固化。
- Tavily 不改变 Lvke DR 的预算、partial、quality gate、citation audit 和 continue lineage 语义。

## 状态铁律

- Agent 提交的研究固定为 `partial`，不能说成 `done`；没有独立质量审计不得自称完成。
- `dr_confirm_quality` 失败时不得产生 completed package；通过或明确接受资料限制后，只使用返回的新 `research_package_id`。
- `source_reconstructed` 质量确认始终保持 `project_fact_certified=false`。
- `done` 仍须同时满足质量门和引用审计；Skill 不降低阈值。
- `dr_continue` 创建有 lineage 的续研会话，不修改原任务状态，也不放宽门槛。
- 计划修订必须经 `dr_propose_plan_revision → dr_apply_plan_revision`；过期 basis 不得应用。
- 来源排除只通过 `dr_remove_sources` 写入新 revision 和 event，不删除 snapshot 或旧 revision。
- 中断或重启前用 `dr_create_checkpoint`；`dr_resume` 只接受有效签名 token，并创建新 task 保留 lineage。
- `dr_list_events` 只返回可续读的结构化状态，不请求、保存或暴露 chain-of-thought。
- bundle 中的报告、来源、证据、质量、引用审计和 checkpoint 按需读 Resource，不一次塞入对话。
- DR 中出现的财务数字不能进入正式财务章；财务唯一真源是 `run_id`。
- 普通研究与继续研究不设置重复人工批准。网络与工具权限服从 Claude Code 配置；密钥、私网 URL 与 prompt injection 安全门不可绕过。

## 不触发

只需找/抓公开网页时使用 `lvke-source-acquisition`；来源资料整理和 evidence pack 使用证据 Skills；财务与研报分别使用对应 Skill。修改 DR 引擎代码、门槛或测试属于开发任务，用 `.claude/skills/` 下的后端/验收类开发 Skill，不使用本运行时 Skill。
