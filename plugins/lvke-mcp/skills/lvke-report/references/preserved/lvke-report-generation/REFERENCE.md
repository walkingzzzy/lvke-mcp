---
name: lvke-report-generation
description: "Use when the user asks to generate, assemble, revise, validate, or export a feasibility-study report from immutable evidence, research, financial run, and thirteen-table package IDs. Not for source collection, FinanceSpec preparation, financial calculation, thirteen-table generation, research-only work, or report-engine code."
---

# 研报生成工作流

研报 MCP 只消费上游固化对象、管理修订、校验与 DOCX；不自行联网、运行 DR、计算财务、重做十三表，**也不配置或调用第二个 LLM**。正文由当前 Agent 撰写。

## 篇幅与目录粒度

正式可研的篇幅由**目录粒度**承载，不由单节堆字承载。要写出 20 万字量级的报告，
必须先选对 `report_type`，再逐叶落稿：

| report_type | 结构 | 叶子节数 | 单叶目标 | 整篇量级 |
|---|---|---|---|---|
| `gov10` | 10 章 / 36 节（两级） | 36 | 1,000-1,800 字 | 4-6 万字 |
| `gov10_full` | 10 章 / 37 节 / 144 叶（三级） | 144 | **800-1,500 字** | **约 19-20 万字** |

- 20 万字摊到 `gov10` 的 36 个叶子是每节 5,500 字，远超一个三级小节的合理体量，
  写出来必然是无结构的大段。需要完整篇幅时选 `gov10_full`。
- `report_prepare` 对 `gov10_full` 会把 outline 固化到**全部 191 个层级节点**
  （章 + 节 + 叶），每个叶子都有自己的 `sec_*` ID。**按叶子逐个 propose/apply**，
  不要整章一次性提交：整章在该结构下是 1 万字量级，既难写准也难改。
- `report_validate` 对每个低于 800 字的叶子返回 `leaf_below_target:<标题>:<实际>`
  warning，用它定位“该去加长哪一节”，不要只看章级总数。
- 章级下限（`_CHAPTER_MIN_CHARS`）是**下限不是目标**。达到下限只说明不再阻断，
  不说明篇幅到位；对齐上表的“单叶目标”才是交付体量。
- 篇幅不足要靠**补论证与补层级**解决：把一个论点拆成现状、依据、测算、结论四段，
  或在大纲里增设叶子。不要靠复述前文或套话拉长——`suite_review` 对 ≥80 字的重复
  段落做 sha256 去重并报 `ARTICLE.DUPLICATE.TEMPLATE`。

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
7. 只有现有质量门禁全部通过时，才调用 `report_export_docx(kind="formal_candidate")`。该输出是技术候选工件，不是安全签审或客户验收。

## 状态铁律

- 草稿生成成功、API 成功、单测通过都不等于正式交付。
- DR 为 `partial` 时研报必须显示限制，不能写“研究已完成”。
- 正文财务数字、指标表和十三表必须同源于一个 `run_id`。
- readiness blocker 原样返回；Skill 不绕过质量和证据门禁。
- 不把当前 Agent 的文字再转交给 MCP 内部 LLM；MCP 是工具与工件层，不是第二个写作 Agent。

## 不触发

来源采集与证据包使用证据 Skills；FinanceSpec、模型和十三表各使用对应财务 Skill；只做 DR 使用 `lvke-deep-research`。修改报告引擎代码属于开发任务。
