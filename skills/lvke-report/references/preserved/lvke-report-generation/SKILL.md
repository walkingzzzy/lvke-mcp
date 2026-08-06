---
name: lvke-report-generation
description: "Use when the user asks to generate, assemble, revise, validate, export, or explicitly release a feasibility-study report from immutable evidence, research, financial run, and thirteen-table package IDs. Not for: source collection, FinanceSpec preparation, financial calculation, thirteen-table generation, research-only work, or report-engine code."
---

# 研报生成工作流

研报 MCP 只消费上游固化对象、管理修订、校验与 DOCX；不自行联网、运行 DR、计算财务、重做十三表，**也不配置或调用第二个 LLM**。正文由当前 Agent 撰写。

## 前置对象

- `evidence_pack_id`
- `research_package_id`（草稿可保留 `partial` 限制；正式流程必须绑定通过 `dr_confirm_quality` 的 completed package）
- `run_id`
- `finance_tables_package_id`，且其 `run_id` 必须与正文相同

缺任一对象或绑定不一致时，`report_prepare` fail-closed。不要用占位财务数字绕过。

## 标准顺序

1. `report_prepare` 校验上游对象和 basis，保存 `report_preparation_id`。
2. `report_start` 创建绑定 basis 的 Agent 草稿会话；它不生成正文。
3. 基于 evidence/research 中可追溯的 locator 与同一 `run_id` 撰写正文，再走 `report_propose → report_diff → report_apply`，不得直接覆盖修订。`target_sections` 可使用 preparation 固化的 `sec_*` ID 或精确标题；proposal basis 必须同时保存 ID 与标题。
4. 调用 `report_status` 固化当前正文为 `report_revision_id`。
5. 每次新修订后调用 `report_validate`，检查结构、引用、正文数字、财务绑定、十三表绑定、partial 限制和 readiness。
6. `report_export_docx(kind=draft)` 可生成明显标记的内部复核稿；必须检查 `docx_font_audit` 的 `invalid_locale_font_count=0`，正文/标题东亚字体分别为 `Songti SC` / `Heiti SC`。
7. 只有现有产品门禁全部通过时，才尝试 `formal_candidate`；`report_release` 是唯一 Claude Code `ask` 的终端高风险动作。

## 状态铁律

- 草稿生成成功、API 成功、单测通过都不等于正式交付。
- DR 为 `partial` 时研报必须显示限制，不能写“研究已完成”。
- 正文财务数字、指标表和十三表必须同源于一个 `run_id`。
- readiness blocker 原样返回；Skill 不自动批准、代签或绕过门禁，也不设置层层重复确认。
- 不把当前 Agent 的文字再转交给 MCP 内部 LLM；MCP 是工具与工件层，不是第二个写作 Agent。

## 不触发

来源采集与证据包使用证据 Skills；FinanceSpec、模型和十三表各使用对应财务 Skill；只做 DR 使用 `lvke-deep-research`。修改报告引擎代码属于开发任务。
