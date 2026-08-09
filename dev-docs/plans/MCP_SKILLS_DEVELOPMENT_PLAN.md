# MCP 服务与 Skills 开发方案

## 1. 方案边界

本方案只包含：

- `src/lvke_mcp` 中 MCP 服务的能力补齐、编排和契约调整。
- `/Users/mac/Desktop/工程/hubei-lvke/skills` 中 Skills 的补齐、重组和调用流程完善。
- MCP 工具、Resource、对象状态、证据链、财务模型和交付流程测试。

明确不包含：

- Web 前端、桌面前端、交互页面。
- HTTP API、独立后端、数据库服务或其他服务端系统。
- 新增联网搜索 MCP。
- 新增安全审查、认证、授权、权限控制、租户隔离或安全门禁。
- 当前代码中已有的 URL 安全检查不作为本方案的开发目标，也不作为业务验收条件。

联网搜索统一复用现有 `tavily_hikari`，由 `lvke-data-acquisition` 的
`data_search`、`data_discover`、`data_fetch` 和
`data_import_external_snapshot` 调用。

## 2. 当前 MCP 能力盘点

**状态**: 历史文档（Wave 2 拓扑） — 当前为 14 个 server、169 个工具（参见 `server_manifest.py`）。

当前 `src/lvke_mcp/testing/server_manifest.py` 定义 14 个 MCP 服务。Wave 2 时曾有 24 个，Wave 4 压缩后实际工具职责如下：

| 服务 | 当前能力 | 定位 |
|---|---|---|
| `environmental-data` | 空气、水质、监测地点 | 环境基础数据 |
| `excel-bridge` | XLSX 工作表、公式、跨表引用、依赖树 | 文件结构读取 |
| `finance-calc` | IRR、NPV、XIRR、XNPV、盈亏平衡、回收期、敏感性 | 纯函数计算 |
| `industry-research` | 行业报告检索和摘要 | 行业资料索引 |
| `lvke-archive` | 历史报告、章节、相似案例、模板段落 | 历史档案 |
| `lvke-asset-acquisition` | 资产收购 FinanceSpec、模型、场景、价格、表格、导出 | 资产收购项目 |
| `lvke-clients` | 客户和历史项目查询 | 客户资料 |
| `lvke-data-acquisition` | Tavily 搜索、来源发现、正文抓取、快照、URL 审计、Resource | 正式资料采集 |
| `lvke-data-analysis` | 资料摄入、事实候选、归一化、对比、benchmark、EvidencePack | 资料分析 |
| `lvke-deep-research` | 研究计划、来源、事件、checkpoint、resume、partial package | 研究状态管理 |
| `lvke-deliverable-review` | rubric、报告审查、finding、整改复测、标准核验 | 交付审查 |
| `lvke-experts` | 专家和专业领域查询 | 专家索引 |
| `lvke-finance-model` | FinanceSpec、FinanceRun、BoE、资产负债表、Monte Carlo、vendor review | 财务模型 |
| `lvke-finance-tables` | 十三表渲染、校验、导出、Resource | 财务交付表 |
| `lvke-knowledge-governance` | 知识候选、候选查询、不可变快照 | 知识沉淀 |
| `lvke-project-planning` | 项目上下文、市场、收入、规模、成本、定员、方案比选、政策 | 项目前置建模 |
| `lvke-report-generation` | revision、章节提案、diff/apply、校验、DOCX、readiness | 报告工件管理 |
| `lvke-source-files` | 本地/外部资料导入、上传、解析、重试、取消 | 受控附件 |
| `lvke-templates` | 模板目录、模板读取、模板填充 | 模板管理 |
| `lvke-zero-material-delivery` | 零材料受控假设和 estimate preview | 预览交付 |
| `map-geo` | 地理编码、距离、POI | 地理辅助 |
| `policy-search` | 政策搜索、全文、有效性查询 | 政策资料 |
| `statistics-cn` | 统计指标和字典查询 | 统计资料 |

关键事实：

- 13 张附表已经在 `DELIVERY_TABLE_KEYS` 中定义。
- `lvke-finance-tables` 已区分 technical/formal 校验。
- `lvke-data-acquisition` 已经具备 Tavily Hikari 搜索链路，不新增搜索 MCP。
- `lvke-deep-research` 的 `dr_submit` 当前只生成 `partial`，不代表研究完成。
- `lvke-knowledge-governance` 当前没有 Skill 中要求的审核和发布工具。
- `lvke-zero-material-delivery` 是 `estimate_preview`，不能直接作为正式可研交付入口。

## 2.1 当前 Skills 能力分组

当前非 `_archive` 目录共有 58 个 Skill，实际是“领域 SOP + 审查规范”，不是一套完整的可研总流程：

| Skill 分组 | 当前职责 | 已覆盖能力 | 当前缺口 |
|---|---|---|---|
| `project-planning/*` | 项目初始化、市场规模、方案比选、建设规模、成本、定员、收入 | 候选 -> 校验 -> 确认、父对象和证据绑定 | 没有统一的阶段调度和跨对象状态 |
| `research/*`、`source-management/*` | 研究恢复、附件导入、解析和来源读取 | checkpoint、resume、SourceFileSnapshot | 没有研究结果到市场案例的强制回填流程 |
| `data-quality/*` | URL、公共 benchmark 和来源质量 | URL 审计、基准对比、差异解释 | 没有统一正式发布门槛 |
| `financial-modeling/*` | FinanceSpec、FinanceRun、十三表、融资、税、IRR/NPV、情景、敏感性 | 确定性模型和表格交接规范 | 没有客户样本驱动的统一验收流程 |
| `report-drafting/*` | 9 章及影响、运营、选址等章节写作规范 | 分章写作和章节边界 | 没有从项目对象到 9 章的总编排 Skill |
| `doc-review/*` | 引用、合规、一致性、格式、数字、风险、决策可读性 | 多维度章节审查 | 没有统一映射甲方验收矩阵 |
| `industry-context/*` | 农业、能源、制造、文旅、物流、环保、公服、商业等行业规则 | 行业写作和设计约束 | 正式行业参数和财务参数不足 |
| `policy-compliance/*` | 环保、湖北地方、投资审批、土地、采购 | 政策和审批边界 | 没有与 ProjectContext 阶段自动联动 |
| `knowledge-governance/*` | rubric、知识候选、快照治理 | reviewed-first 候选和快照 | Skill 要求的审核/发布工具尚未存在 |
| `meta/*` | 错误恢复、标杆驱动、提案应用、工作区导航 | 编写和工具操作规范 | 没有统领以上 Skill 的可研入口 |

必须新增的不是更多孤立章节 Skill，而是 `lvke-feasibility-study` 总入口；它负责调用现有 Skill，现有 Skill 继续负责各自领域规则。

## 3. 文档需求与能力差距

| 甲方需求 | 当前能力 | 差距 | 开发动作 |
|---|---|---|---|
| 行业规模、区域容量、供需缺口、目标份额、建设规模可量化 | `data-*`、`planning_*`、`lvke-market-sizing` | 工具存在，缺完整串联 | 将研究结果固定映射到 `MarketSizingCase` |
| 研究计划可编辑、可中断、可恢复、来源可核验 | `dr_*`、`lvke-research-recovery` | 有状态模型，完成判定仍是 `partial` | 增加研究质量摘要和正式回填状态 |
| 公开资料来自真实来源 | `tavily_hikari`、`data_fetch`、Source Snapshot | 采集存在，正式证据分类不统一 | 统一来源、locator、hash 和证据轨 |
| 方案比选影响建设内容、规模和投资 | `planning_*option*`、方案比选 Skill | 单项能力存在，非强制阶段 | 纳入交付阶段依赖 |
| 原料、能源、人工、环保进入成本 | 成本、定员、环境模板工具和 Skills | 缺失字段可能在跨服务间丢失 | 强制绑定 FinanceSpec |
| 13 张正式财务附表 | `lvke-finance-model`、`lvke-finance-tables` | 结构基本存在，真实样本验收不足 | 增加客户样本和负面 fixture |
| 非经营性项目资金平衡 | 财务已有部分检查 | 没有独立业务分支 | 增加非经营性输入/结果契约 |
| 恒立酒店资产收购模型 | `lvke-asset-acquisition` 独立路径 | 领域能力存在，未纳入统一交付链 | 完成收购价、总投资、历史经营验收 |
| 9 章报告生成 | report MCP + 章节 Skills | 工件管理存在，缺总编排 | 新增总入口 Skill 并绑定上游对象 |
| 审查、整改、复测 | review MCP + doc-review Skills | 规则存在，甲方矩阵未完全对齐 | 固化统一验收矩阵 |
| 知识审核和发布 | 当前只有候选和快照 | MCP/Skill 契约不完整 | 增加审核、发布和反馈闭环 |

## 4. 目标结构

### 4.1 可研交付编排 MCP

新增 `lvke-feasibility-delivery`，只负责跨现有 MCP 的项目状态和对象关联，不重复实现搜索、财务或报告计算。

阶段：

```text
project
-> research
-> market
-> option
-> scale
-> drivers
-> finance_spec
-> finance_run
-> finance_tables
-> report
-> review
-> released
```

核心对象：

- `FeasibilityDeliveryRun`
- `StageRecord`
- `StageBinding`
- `GateResult`
- `DeliveryCheckpoint`

核心工具：

- `feasibility_start`
- `feasibility_status`
- `feasibility_stage`
- `feasibility_next_actions`
- `feasibility_checkpoint`
- `feasibility_resume`
- `feasibility_validate`
- `feasibility_release`

每个阶段记录输入对象、输出对象、basis hash、状态和下一步动作。上游对象变化后，下游对象标记为 `stale`，不得继续发布。

### 4.2 总入口 Skill

新增 `lvke-feasibility-study`，作为可研业务入口，调用现有 Skills，不重复实现领域规则。

固定调用顺序：

```text
lvke-project-initialization
-> lvke-research-recovery
-> lvke-market-sizing
-> lvke-option-comparison
-> lvke-build-scale
-> lvke-cost-drivers
-> lvke-labor-planning
-> lvke-revenue-drivers
-> lvke-finance-spec
-> lvke-finance-modeling
-> lvke-finance-tables
-> report-drafting
-> doc-review
-> lvke-knowledge-governance
```

联网搜索阶段只调用现有 `data_discover`、`data_search`、`data_fetch`、`data_collect` 和 `data_import_external_snapshot`。

## 5. 端到端业务流程

### 5.1 调用角色

本方案只有两类调用角色：

1. `lvke-feasibility-study` Skill：负责理解用户目标、选择阶段、调用 MCP、解释结果和决定是否继续。
2. 现有 MCP 服务：负责确定性计算、对象持久化、状态返回和 Resource 读写，不负责自主调用其他 MCP，也不负责生成自然语言报告。

调用方始终保留当前 `workspace_id`、最新 `delivery_run_id`、各阶段对象 ID 和 `basis_hash`。任何阶段返回 `blocked` 或 `partial` 时，Skill 必须停止向后推进，先执行该阶段的 `next_actions`。

### 5.2 阶段流程、输入、调用和输出

| 阶段 | 入口条件 | 主要 MCP 调用 | 阶段输出 | 完成条件 |
|---|---|---|---|---|
| 1. 项目初始化 | 用户提供项目描述 | `project_context_create`、`project_context_validate` | `ProjectContext`、适用性清单 | 项目类型、行业、区域、阶段和报告类型明确 |
| 2. 资料接收 | 有本地附件或已有资料 | `source_import_*`、`source_parse_status` | `SourceFileSnapshot` | 文件解析状态为可用或明确为 partial |
| 3. 深度研究准备 | 项目上下文有效 | `dr_prepare`、`dr_start` | `ResearchPlanRevision`、子问题和预算 | 计划覆盖行业、区域、供需、价格、竞争和政策 |
| 4. 深度研究执行 | 研究计划已确认 | `data_discover`、`data_search`、`data_fetch`、`data_collect`、`dr_list_events` | `SourceSnapshot`、`EvidencePack`、研究报告 | 每个关键结论都有来源和 locator；没有来源的内容只能标记 partial |
| 5. 市场规模 | 研究资料可用 | `analysis_extract_candidates`、`analysis_build_evidence_pack`、`planning_prepare_market_case`、`planning_validate_market_case`、`planning_confirm_market_case` | `MarketSizingCase` | 至少一个可复算方法、目标份额、区域容量和选择理由已确认 |
| 6. 方案比选 | 市场案例已确认 | `planning_prepare_option_comparison`、`planning_score_option_comparison`、`planning_confirm_option_comparison` | `OptionComparison` | 建筑、工艺、设备或运营方案有候选、评分、舍弃理由 |
| 7. 建设规模和要素 | 市场/方案已确认 | `planning_solve_build_scale`、`planning_confirm_build_scale`、`planning_create_cost_drivers`、`planning_confirm_cost_drivers`、`planning_create_labor_plan`、`planning_confirm_labor_plan`、`planning_confirm_revenue_drivers` | `BuildScaleCase`、`CostDriverSet`、`LaborPlan`、`RevenueDriverSet` | 产能、面积、投资强度、原料、能源、人工、环保、收入驱动闭合 |
| 8. 财务规范 | 上游驱动已确认 | `finance_prepare_spec`、`finance_validate_spec`、`finance_build_basis_of_estimate`、`finance_confirm_spec` | `FinanceSpec`、`BasisOfEstimate` | 正式模式所有关键输入有正式证据或明确人工确认 |
| 9. 财务运行 | FinanceSpec 已确认 | `finance_run_model`、`finance_get_run` | `FinanceRun` | 一致性通过，现金流、税费、借款、还款和指标可读 |
| 10. 十三表 | FinanceRun 可用 | `tables_render`、`tables_validate`、`tables_export_xlsx`、`tables_export_csv` | `FinanceTablesPackage` | 13 张表均来自同一 `run_id`，formal 校验通过 |
| 11. 报告和审查 | 财务表和证据可用 | `report_prepare`、`report_propose_section`、`report_apply`、`report_validate`、`review_start`、`review_retest` | `ReportRevision`、`ReviewRun`、findings | 9 章引用同一上游对象，整改项已复测或被明确接受 |
| 12. 知识和发布 | 审查通过 | `knowledge_submit_candidate`、`knowledge_review_candidate`、`knowledge_publish_release`、`feasibility_release` | `KnowledgeRelease`、正式交付包 | 所有阶段完成，不得存在 partial/stale/blocker |

### 5.3 研究阶段的实际循环

研究不是一次搜索，而是以下循环，循环由 Skill 执行：

```text
dr_prepare
-> 用户确认/修订计划
-> dr_start
-> data_discover(auto_expand=true, target_count=N)
-> 选择候选 URL
-> data_collect 或 data_fetch
-> analysis_ingest
-> analysis_extract_candidates
-> analysis_build_evidence_pack
-> 检查缺失字段、冲突和来源覆盖
-> dr_list_events / dr_create_checkpoint
-> 补充查询或 dr_continue
-> 形成研究结论
-> 绑定 MarketSizingCase
```

`tavily_hikari` 只负责搜索和正文提取；研究结论、数值候选、证据等级和市场字段映射由现有分析 MCP 与 Skill 完成。搜索摘要不能直接进入正式 FinanceSpec。

### 5.4 财务阶段的实际循环

```text
ProjectContext + MarketSizingCase + OptionComparison
-> BuildScaleCase + CostDriverSet + LaborPlan + RevenueDriverSet
-> finance_prepare_spec(mode=review_candidate)
-> finance_validate_spec
-> finance_build_basis_of_estimate
-> 人工/调用方确认 FinanceSpec 和 BoE
-> finance_run_model
-> finance_get_run
-> tables_render
-> tables_validate(technical)
-> tables_validate(formal)
-> tables_export_xlsx/csv
```

`estimate_preview` 可以使用受控假设，但必须在输出中标记 `preview_only=true`；`review_candidate` 不得静默使用行业默认值、技术 fixture 或没有 locator 的数字。

## 6. 新增可研交付 MCP 的详细功能

新增目录：`src/lvke_mcp/servers/lvke_feasibility_delivery/`。

### 6.1 状态对象

`FeasibilityDeliveryRun` 是不可变快照，每次阶段更新产生新的快照，字段至少包括：

```json
{
  "delivery_run_id": "fdr_...",
  "root_run_id": "fdr_...",
  "parent_run_id": "fdr_...",
  "workspace_id": "...",
  "delivery_mode": "estimate_preview|review_candidate|formal_release",
  "status": "in_progress|partial|blocked|completed|stale|released",
  "current_stage": "research",
  "project_context_id": "...",
  "stages": {
    "project": {"status": "completed", "input_refs": [], "output_refs": []},
    "research": {"status": "in_progress", "input_refs": [], "output_refs": []}
  },
  "next_actions": [],
  "basis_hash": "sha256:..."
}
```

每个 `StageRecord` 必须记录 `status`、`input_refs`、`output_refs`、`basis_hash`、`warnings`、`blockers`、`next_actions` 和 `updated_from_run_id`。

### 6.2 工具契约

| 工具 | 用途 | 必填输入 | 输出 |
|---|---|---|---|
| `feasibility_start` | 创建新的可研交付运行 | `workspace_id`、`delivery_mode`、`idempotency_key` | 初始 `delivery_run_id`、阶段清单 |
| `feasibility_status` | 读取当前快照 | `workspace_id`、`delivery_run_id` | 当前阶段、所有阶段状态、对象绑定 |
| `feasibility_stage` | 写入阶段结果快照 | `workspace_id`、`delivery_run_id`、`stage`、`status`、`idempotency_key` | 新 `delivery_run_id`、失效下游列表 |
| `feasibility_next_actions` | 根据状态生成下一动作 | `workspace_id`、`delivery_run_id` | 可执行工具名、缺失输入和阻塞原因 |
| `feasibility_checkpoint` | 保存恢复点 | `workspace_id`、`delivery_run_id`、`reason` | checkpoint ID、快照 ID |
| `feasibility_resume` | 从恢复点产生新快照 | `workspace_id`、`checkpoint_id`、`idempotency_key` | 新 `delivery_run_id`、继承的阶段状态 |
| `feasibility_validate` | 校验阶段顺序和发布条件 | `workspace_id`、`delivery_run_id`、`scope` | `technical` 或 `formal` 结果、blockers |
| `feasibility_release` | 将已通过 formal 校验的快照标记 released | `workspace_id`、`delivery_run_id`、`idempotency_key` | release ID、交付对象清单 |

阶段更新规则：

1. 只能写入当前快照的下一个状态，不直接修改历史对象。
2. 完成早期阶段时，后续已完成阶段全部标记 `stale`。
3. `partial` 只能停留在当前阶段，不能推动依赖它的正式阶段。
4. `blocked` 必须返回具体 blocker 和修复动作。
5. `formal_release` 必须要求所有阶段 `completed`，不得存在 `partial`、`stale` 或 `blocked`。

### 6.3 MCP Resource

新增 Resource 类型：

- `FeasibilityDeliveryRun`
- `FeasibilityStageRecord`
- `FeasibilityCheckpoint`
- `FeasibilityRelease`

Resource 只返回对象快照和引用，不重新计算市场、财务或报告内容。

## 7. 总入口 Skill 的详细功能

新增：`/Users/mac/Desktop/工程/hubei-lvke/skills/project-planning/lvke-feasibility-study/SKILL.md`。

Skill 必须包含以下行为：

1. 读取或创建 `ProjectContext`。
2. 调用 `feasibility_start` 创建交付运行。
3. 按阶段表逐步调用 MCP，不允许跳过方案比选直接运行正式财务。
4. 每个写操作后读取返回对象和 `basis_hash`。
5. 遇到 `blocked` 时停止并展示 `blockers`、`missing_inputs` 和 `next_actions`。
6. 遇到 `partial` 时只允许继续补资料、补研究或生成预览，不允许正式发布。
7. 研究结论必须先进入 EvidencePack，再进入 MarketSizingCase。
8. 财务数字只读取 FinanceRun/FinanceTables，不在 Skill 内重新计算。
9. 报告生成按内部建模顺序准备数据，最终输出按发改委 9 章顺序组织。
10. 交付前按 technical -> formal 顺序调用校验工具。

Skill 输出必须固定包含：

- 当前阶段。
- 已完成阶段。
- 当前对象 ID 和 basis hash。
- 当前阻塞项。
- 下一步工具调用。
- 是否为 preview、partial 或 formal。

## 8. 现有 MCP 的具体补齐

### 8.1 `lvke-deep-research`

- 增加结构化研究质量摘要：查询轮次、来源数量、有效来源数量、缺失字段、冲突字段、覆盖率。
- 增加研究输出到 `MarketSizingCase` 的字段映射记录。
- 保留 `dr_submit` 的 `partial` 语义；新增正式完成动作时必须带独立审查结果，不把 Agent 文本直接标记为 done。

### 8.2 `lvke-knowledge-governance`

增加两个缺失工具：

- `knowledge_review_candidate`：读取候选和证据，写入 `accepted|rejected|needs_revision` 审核快照。
- `knowledge_publish_release`：只接收 `accepted` 候选，写入不可变 `KnowledgeRelease`。

审核结果必须记录候选 ID、审查结论、理由、证据 hash、rubric assessment ID 和发布时间。Skill 文档同步使用这两个真实工具名。

### 8.3 `lvke-project-planning`

补充显式 lineage 字段：

```text
research_package_id
evidence_pack_id
market_case_id
option_comparison_id
build_scale_case_id
cost_driver_set_id
labor_plan_id
revenue_driver_set_id
```

下游创建工具必须拒绝缺失父对象 ID 的正式模式请求。

### 8.4 `lvke-finance-model` 和 `lvke-finance-tables`

- 正式模式禁止无来源默认输入。
- `FinanceSpec`、`BasisOfEstimate`、`FinanceRun`、`FinanceTablesPackage` 必须共享可核对的父对象和 hash。
- 13 表只能从指定 `run_id` 渲染，不能接收散乱数字重新计算。
- 增加非经营性项目资金平衡结果字段。
- 增加恒立酒店收购价、资产包、历史经营、混合经营收入池的 fixture。

### 8.5 `lvke-report-generation` 和 `lvke-deliverable-review`

- 报告章节保存 `upstream_refs`，至少绑定 ProjectContext、EvidencePack、FinanceRun 和 FinanceTablesPackage。
- 第 3 章必须能追溯市场规模和建设规模；第 4 章必须能追溯方案比选；第 5/6 章必须能追溯投资和财务运行。
- 审查矩阵必须覆盖市场论证、方案比选、原料/能源/人工/环保成本、13 表、资金平衡和 citation locator。

## 9. 失败和恢复流程

| 情况 | MCP 返回 | Skill 行为 |
|---|---|---|
| 项目类型或行业缺失 | `blocked/project_context_incomplete` | 补齐 ProjectContext，不创建正式对象 |
| Tavily Hikari 不可用 | `upstream_failure` | 保留研究计划，切换到补资料/恢复，不把搜索摘要当证据 |
| 研究来源不足 | `partial/research_coverage_incomplete` | 生成补查问题，调用 `dr_continue` |
| 市场候选冲突 | `blocked/market_case_conflict` | 显示冲突和候选，不自动平均或选择 |
| 方案未确认 | `blocked/option_comparison_required` | 先完成方案比选再进规模/财务 |
| FinanceSpec 缺关键来源 | `blocked/formal_basis_missing` | 转回资料或人工确认，不运行 formal FinanceRun |
| FinanceRun 勾稽失败 | `blocked/finance_consistency_failed` | 保留失败 run，修订输入后创建新 run |
| 十三表缺失或跨表不一致 | `blocked/tables_validation_failed` | 不导出 formal package，先修 run |
| 报告审查有 blocker | `blocked/review_findings_open` | 走 report propose -> diff -> apply -> retest |
| 上游对象发生变化 | `stale/downstream_invalidated` | 从变更阶段重新执行，不使用旧下游对象 |

## 10. 开发阶段、交付物和测试

### 阶段一：契约冻结

交付物：需求矩阵、阶段枚举、对象 lineage、状态转移表、工具 JSON Schema。

测试：所有非法阶段跳转、缺父对象、重复幂等键、partial 推进 formal 的请求都必须有确定性 blocker。

### 阶段二：编排 MCP 与总入口 Skill

交付物：`lvke-feasibility-delivery` 服务、`lvke-feasibility-study` Skill、Resource 读取和 checkpoint/resume。

测试：从 project 创建到 finance_spec 的最小流程；修改 market 阶段后验证后续阶段变为 stale；恢复后历史快照不被改写。

### 阶段三：研究和证据闭环

交付物：Tavily Hikari 调用说明、SourceSnapshot -> EvidencePack -> MarketSizingCase 映射、研究质量摘要。

测试：搜索摘要不能入模、无 locator 不能 formal、partial 研究不能 release、补查后可继续。

### 阶段四：财务样本验收

交付物：产品销售、厂房出租、墓地售卖、房地产、文旅、恒立酒店正式 fixture 和负面 fixture。

测试：建设期利息、分年提款还款、流动资金、税前/税后现金流、IRR、DSCR、资产负债平衡、十三表勾稽、非经营性资金平衡。

### 阶段五：报告、审查和知识闭环

交付物：9 章上游绑定、审查矩阵、整改复测、KnowledgeCandidate 审核和 KnowledgeRelease 发布。

测试：报告数字只能来自同一 FinanceRun；缺引用、缺市场链、缺方案比选、缺财务表时不能 release；审核拒绝不能生成 KnowledgeRelease。

## 11. 正式验收标准

1. 所有阶段完成，没有 `partial`、`stale` 或未处理 blocker。
2. 市场结论可追溯到 EvidencePack 和来源 locator。
3. 建设规模、投资、成本、收入和财务模型存在明确对象链。
4. 十三张附表来自同一 `run_id`，并通过结构、期间、公式和跨表校验。
5. 报告正文、技术经济指标、附表和审查结果使用同一版本数据。
6. 受控假设只能生成预览，不得进入正式发布。
7. 两份已验收报告和恒立酒店案例通过端到端回归。
8. Skill 中引用的每个工具名都能在当前 MCP manifest 中找到。
9. 不新增联网搜索 MCP；搜索只复用 Tavily Hikari。
10. 不增加安全审查、权限控制、认证授权或相关门禁。

## 12. 明确非目标

- 不开发前端工作台。
- 不开发 HTTP/REST/GraphQL 服务。
- 不开发新的搜索、浏览器或联网采集 MCP。
- 不将 MCP 改造成自主运行的 LLM Agent。
- 不在 Skill 内复制财务计算、IRR/NPV、税费或十三表公式。
- 不把技术 fixture、搜索摘要、受控假设或报告反推值升级为正式证据。
- 不增加安全审查、权限控制、认证授权、租户管理和相关测试。

## 13. 实施级总流程

前面的章节定义了目标能力，本节定义调用方实际执行时的逐步流程。流程中的“调用方”是承载 Skill 的 Agent；MCP 服务之间不互相调用，所有跨服务编排都由 Skill 顺序完成。

### 13.1 一次完整运行的时序

```text
Agent
  |
  | 1. project_context_create / validate
  v
lvke-project-planning  ---> ProjectContext
  |
  | 2. feasibility_start
  v
lvke-feasibility-delivery ---> FeasibilityDeliveryRun(stage=project)
  |
  | 3. source_import_* / dr_prepare / dr_start
  v
lvke-source-files + lvke-deep-research
  |
  | 4. data_discover -> data_fetch/collect -> analysis_ingest
  |    -> analysis_extract_candidates -> analysis_build_evidence_pack
  v
EvidencePack + ResearchPackage(partial/done)
  |
  | 5. planning_prepare_market_case -> validate -> confirm
  v
MarketSizingCase
  |
  | 6. option -> scale -> cost/labor/revenue
  v
OptionComparison + BuildScaleCase + CostDriverSet + LaborPlan + RevenueDriverSet
  |
  | 7. finance_prepare_spec -> validate -> BoE -> confirm -> run
  v
FinanceSpec + BasisOfEstimate + FinanceRun
  |
  | 8. tables_render -> technical validate -> formal validate -> export
  v
FinanceTablesPackage
  |
  | 9. report_prepare -> section propose/apply -> report_validate
  |    -> review_prepare/start -> findings -> retest
  v
ReportRevision + ReviewRun
  |
  | 10. knowledge candidate -> review -> publish
  |     -> feasibility_validate(formal) -> feasibility_release
  v
Formal release
```

### 13.2 每一步的固定动作

每次 MCP 调用必须按以下四步执行，不能只调用写工具而不读取结果：

1. Skill 组装 `workspace_id`、父对象 ID、当前 `basis_hash` 和 `idempotency_key`。
2. 调用写工具；保留完整 envelope，不仅提取自然语言字段。
3. 如果返回 `resource_uris`，立即调用对应的 `*_read_resource` 或 `*_get_*` 读取快照，保存 `object_id`、`basis_hash`、`content_hash`。
4. 调用 `feasibility_stage` 写入阶段绑定；根据 `status` 决定继续、补料、回退或停止。

### 13.3 状态分支

| MCP 返回 | Skill 必须做的事 | 禁止做的事 |
|---|---|---|
| `completed`/`done` | 固化对象引用，进入依赖阶段 | 用自然语言结果代替对象引用 |
| `partial` | 保存当前快照，生成缺口清单，允许补查或预览 | 将其写成完成并进入 formal 财务或发布 |
| `blocked` | 展示 `code`、`blockers`、`next_actions`，停在当前阶段 | 重复发送相同请求或自动猜值 |
| `stale` | 找到产生变化的上游阶段，从该阶段重跑 | 继续读取旧下游对象 |
| `upstream_failure` | 保存 checkpoint，等待恢复或切换到已有资料 | 把搜索摘要、缓存文本当正式证据 |

### 13.4 三种模式的边界

| 模式 | 可用输入 | 可生成 | 不可生成 |
|---|---|---|---|
| `estimate_preview` | controlled assumption、technical fixture、已有资料 | 预览 FinanceSpec、预览 FinanceRun、缺口报告 | formal 表包、正式报告、release |
| `review_candidate` | 已确认对象、可追溯候选、待人工确认 BoE | 候选 FinanceRun、technical 表包、待审报告 | 没有证据链的正式数值 |
| `formal_release` | 全部父对象完成、正式证据、formal 校验通过 | 13 表、9 章报告、审查结果、知识发布 | partial/stale/blocker 状态下的任何发布 |

## 14. MCP 功能实施说明

### 14.1 新增 `lvke-feasibility-delivery`

#### 代码落点

```text
src/lvke_mcp/servers/lvke_feasibility_delivery/__init__.py
src/lvke_mcp/servers/lvke_feasibility_delivery/server.py
src/lvke_mcp/servers/lvke_feasibility_delivery/service.py
src/lvke_mcp/servers/lvke_feasibility_delivery/contracts.py
src/lvke_mcp/servers/lvke_feasibility_delivery/store.py
tests/integration/test_feasibility_delivery.py
```

`server.py` 只注册 MCP 工具和 Resource；状态机、快照和幂等写入分别放在 `service.py`、`contracts.py`、`store.py`，不把状态规则写进 handler 闭包。

#### 对象和字段

`FeasibilityDeliveryRun` 至少包含：

```json
{
  "delivery_run_id": "fdr_...",
  "root_run_id": "fdr_...",
  "parent_run_id": null,
  "workspace_id": "demo",
  "delivery_mode": "review_candidate",
  "status": "in_progress",
  "current_stage": "project",
  "project_context_id": "pc_...",
  "stages": {
    "project": {
      "status": "completed",
      "input_refs": [],
      "output_refs": ["pc_..."],
      "basis_hash": "sha256:...",
      "warnings": [],
      "blockers": [],
      "next_actions": []
    }
  },
  "lineage": {},
  "next_actions": [],
  "basis_hash": "sha256:..."
}
```

阶段枚举固定为 `project/research/market/option/scale/drivers/finance_spec/finance_run/finance_tables/report/review/released`。`StageRecord` 的 `input_refs` 和 `output_refs` 只能保存对象 ID 或 Resource URI，不保存大段报告正文。

#### 工具行为

| 工具 | 实现行为 | 关键校验 | 成功返回 |
|---|---|---|---|
| `feasibility_start` | 建立根快照和 12 个阶段记录 | `delivery_mode`、`idempotency_key` 必填；同键同请求重放 | `delivery_run_id`、`current_stage`、`next_actions` |
| `feasibility_status` | 读取指定快照 | workspace 和 run 必须匹配 | 完整阶段状态和 lineage |
| `feasibility_stage` | 生成子快照，不修改父快照 | 只能更新当前阶段；输入对象和 basis hash 必须存在 | 新 run、stale 下游列表 |
| `feasibility_next_actions` | 根据当前 blocker 生成可执行动作 | 不改变状态 | 工具名、参数来源、缺口和原因 |
| `feasibility_checkpoint` | 保存当前快照引用和原因 | 当前 run 必须存在 | checkpoint ID、run ID |
| `feasibility_resume` | 从 checkpoint 复制为新根/子运行 | checkpoint 必须可读 | 新 run 和继承状态 |
| `feasibility_validate` | 按 scope 检查状态、lineage、模式 | `technical` 可有 partial；`formal` 不得有 partial/stale/blocker | `passed`、findings、blockers |
| `feasibility_release` | 固化 release 快照 | 仅 `formal`，所有阶段 completed | release ID、所有交付对象 URI |

#### 状态转移测试表

```text
project in_progress -> project completed -> research in_progress
research partial -> research in_progress (补查) / checkpoint
research completed -> market in_progress
market completed -> option in_progress
option completed -> scale in_progress
scale completed -> drivers in_progress
drivers completed -> finance_spec in_progress
finance_spec completed -> finance_run in_progress
finance_run completed -> finance_tables in_progress
finance_tables completed -> report in_progress
report completed -> review in_progress
review blocked -> report in_progress (整改)
review completed -> released in_progress
released -> released (只读；上游变化时新 run 标记 stale)
```

### 14.2 `lvke-project-planning` 补齐对象绑定

在现有 `src/lvke_mcp/domains/project_planning/application.py` 的对象 payload 增加 `parent_refs` 和 `lineage`，不改变既有计算函数。以下正式模式请求必须拒绝缺少父对象的调用：

| 对象 | 必须的父引用 |
|---|---|
| `MarketSizingCase` | `project_context_id`、`evidence_pack_id` |
| `OptionComparison` | `project_context_id`、`market_case_id` |
| `BuildScaleCase` | `market_case_id`、`option_comparison_id` |
| `CostDriverSet` | `build_scale_case_id`、`option_comparison_id` |
| `LaborPlan` | `project_context_id`、`build_scale_case_id` |
| `RevenueDriverSet` | `market_case_id`、`build_scale_case_id` |

实现顺序：先在 contracts schema 中增加可选字段，再在 formal 分支增加 required 校验，最后在 `project_context_get`/`planning_get_object` 返回 lineage。preview 分支保留现有受控假设语义。

### 14.3 `lvke-deep-research` 研究闭环

当前真实流程是 `dr_prepare -> dr_start -> Agent 调用 data_* -> dr_submit -> dr_status/dr_get_evidence/dr_get_bundle`，其中 `dr_submit` 仍然是 `partial`。开发不改变这一语义，只增加 `quality_summary` 和 `market_field_bindings`：

```json
{
  "quality_summary": {
    "query_rounds": 2,
    "source_count": 12,
    "usable_source_count": 8,
    "citation_coverage": 0.86,
    "missing_fields": ["区域有效需求"],
    "conflicts": []
  },
  "market_field_bindings": [
    {"field": "regional_capacity", "candidate_id": "fc_1", "locator": "page:12"}
  ]
}
```

Skill 只有在 `dr_get_evidence`、`analysis_build_evidence_pack` 和人工确认均存在时，才可调用 `planning_prepare_market_case`。不新增第二个 LLM 或搜索 MCP。

### 14.4 `lvke-knowledge-governance` 审核发布

在现有 `server.py`、`service.py` 和 `_RESOURCE_STORES` 增加两个工具及两类对象：

```text
knowledge_review_candidate
输入：workspace_id, candidate_id, decision, reason, rubric_assessment_id, idempotency_key
decision：accepted | rejected | needs_revision
输出：review_id, candidate_id, decision, evidence_hash, basis_hash

knowledge_publish_release
输入：workspace_id, candidate_id, review_id, idempotency_key
前置：review.decision == accepted 且候选 evidence 校验通过
输出：release_id, resource_uri, candidate_id, review_id
```

`rejected` 和 `needs_revision` 只能返回审核快照，不能生成 `KnowledgeRelease`。对应 Skill 必须先读取候选和 rubric，再调用审核工具；不得把 `knowledge_create_snapshot` 当作发布动作。

### 14.5 财务 MCP 的真实调用契约

当前实际工具名为 `finance_prepare_spec`、`finance_validate_spec`、`finance_build_basis_of_estimate`、`finance_confirm_spec`、`finance_run_model`、`finance_get_run`、`finance_render_tables`、`finance_generate_package`；十三表服务实际工具名为 `tables_render`、`tables_validate`、`tables_export_xlsx`、`tables_export_csv`。

开发要求：

1. `finance_prepare_spec` 接收 `mode` 和上游 lineage；`estimate_preview` 可以存在 controlled assumption，`review_candidate/formal_release` 缺关键 evidence 时返回 `formal_basis_missing`。
2. `finance_build_basis_of_estimate` 输出每个参数的 `value/unit/source_ref/locator/evidence_track`，不能只输出合计数。
3. `finance_run_model` 只能接收已确认 `finance_spec_id`；不得从 Skill 传入散乱的收入、成本或现金流数字。
4. `finance_get_run` 返回 `finance_spec_id`、`basis_of_estimate_id`、`input_hash`、`run_id`、`model_version`。
5. `tables_render` 只接受 `run_id`；`tables_validate(technical)` 校验表名、期间、公式和 manifest，`tables_validate(formal)` 追加正式门禁。
6. `tables_export_*` 只能在 formal 校验通过后由总入口 Skill 调用；输出必须含 `run_id` 和表包 hash。

### 14.6 报告和审查功能

报告 MCP 的每个章节 revision 增加：

```json
{
  "chapter_no": 3,
  "upstream_refs": {
    "project_context_id": "pc_...",
    "evidence_pack_id": "ep_...",
    "market_case_id": "msc_...",
    "finance_run_id": "fr_...",
    "finance_tables_package_id": "ftp_..."
  },
  "citation_locators": ["lvke://...#page=12"],
  "basis_hash": "sha256:..."
}
```

章节 Skill 的写作流程固定为：读取上游 Resource -> `report_propose_section` -> 读取 diff -> `report_apply` -> `report_validate_section`。总入口 Skill 只在 9 个章节均有 revision 且 `report_get_readiness` 通过后启动 `review_start`。审查发现统一经 `review_disposition_finding` 处理，再由 `review_retest` 复测；未关闭的 blocker 不得进入 release。

## 15. `lvke-feasibility-study` Skill 的可执行算法

新增文件：`/Users/mac/Desktop/工程/hubei-lvke/skills/project-planning/lvke-feasibility-study/SKILL.md`。Skill 不实现任何财务公式、搜索逻辑或对象存储，只实现调度和结果解释。

### 15.1 输入和输出

输入至少包括 `workspace_id`、项目名称、项目类型、行业、区域、目标、已有材料路径和 `delivery_mode`。输出固定为：

```text
delivery_run_id
current_stage
completed_stages[]
object_refs{object_type: id/resource_uri}
basis_hashes{}
status
blockers[]
missing_inputs[]
next_actions[{tool, arguments, reason}]
preview_only
```

### 15.2 伪代码

```text
context = project_context_get(workspace_id) or project_context_create(input)
project_context_validate(context)
run = feasibility_start(workspace_id, mode, idempotency_key)

for stage in STAGES:
    status = feasibility_status(run)
    if status has stale:
        run = feasibility_resume(from stage)
    if status.current_stage != stage:
        feasibility_stage(run, stage, completed/blocked)

    result = execute_stage(stage, context, status.lineage)
    persist_result_and_basis(result)
    feasibility_stage(run, stage, result.status, result.refs)

    if result.status == blocked:
        checkpoint = feasibility_checkpoint(run, reason=result.code)
        return blocked_response(checkpoint, result)
    if result.status == partial:
        return partial_response(next_actions(result))

technical = feasibility_validate(run, scope="technical")
formal = feasibility_validate(run, scope="formal")
if formal.passed:
    return feasibility_release(run)
return formal.blockers
```

### 15.3 阶段函数的具体动作

| 阶段函数 | 必须调用 | 产出绑定 | 不满足时回退 |
|---|---|---|---|
| `execute_project` | `project_context_create/validate` | `project_context_id` | 补项目类型、行业、区域 |
| `execute_research` | `source_*`、`dr_*`、`data_*`、`analysis_*` | `research_package_id`、`evidence_pack_id` | `dr_continue` 或 checkpoint |
| `execute_market` | `planning_prepare_market_case`、`planning_validate_market_case`、`planning_confirm_market_case` | `market_case_id` | 回研究阶段 |
| `execute_option` | `planning_prepare_option_comparison`、`planning_score_option_comparison`、`planning_confirm_option_comparison` | `option_comparison_id` | 补方案、重新评分 |
| `execute_drivers` | scale/cost/labor/revenue planning 工具 | 四类 driver ID | 回方案或市场阶段 |
| `execute_finance` | finance spec/BoE/run 工具 | `finance_spec_id`、`run_id` | 回 drivers 或补证据 |
| `execute_tables` | `tables_render`、两次 `tables_validate`、export | `finance_tables_package_id` | 回 finance run |
| `execute_report_review` | report 工具、9 章 Skill、review 工具 | `report_revision_id`、`review_run_id` | 回章节 diff/apply |
| `execute_knowledge_release` | candidate -> review -> publish | `knowledge_release_id` | 保留 candidate，不能发布 |

## 16. 文件级开发任务和依赖顺序

| 编号 | 文件/目录 | 任务 | 依赖 | 完成定义 |
|---|---|---|---|---|
| T01 | `contracts/`、`testing/server_manifest.py` | 冻结 stage、mode、envelope、lineage schema；登记新服务 | 无 | schema 可被测试读取，manifest 含 probe |
| T02 | `lvke_feasibility_delivery/` | 实现 store、快照、状态转移、8 个工具、4 类 Resource | T01 | 最小 project->research 流程可运行 |
| T03 | `tests/integration/test_feasibility_delivery.py` | 状态转移、幂等、stale、checkpoint/resume、formal gate | T02 | 正面和负面用例全部通过 |
| T04 | `lvke_project_planning` | 写入 parent_refs、lineage 和 formal 缺父校验 | T01 | market->finance 的对象链可读 |
| T05 | `lvke_deep_research`、`lvke_data_analysis` | quality_summary、field binding、EvidencePack 映射 | T04 | partial 研究不能创建 formal market case |
| T06 | `lvke_knowledge_governance` | review/publish 工具、资源、状态测试 | T01 | rejected/needs_revision 无法发布 |
| T07 | `lvke_finance_model`、`lvke_finance_tables` | mode、BoE 明细、run lineage、13 表 formal gate | T04 | 6 类样本通过同一 run_id 勾稽 |
| T08 | `lvke_report_generation`、`lvke_deliverable_review` | 9 章 refs、citation locator、finding->retest | T07 | 数字/引用/审查版本一致 |
| T09 | `skills/project-planning/lvke-feasibility-study` | 写入调度算法、分支输出和工具清单 | T02-T08 | Skill 清单中的每个工具在 manifest 中存在 |
| T10 | 端到端 fixture 和 CI | 产品、厂租、墓地、房地产、文旅、恒立酒店 | T03-T09 | 正式验收脚本一次跑通 |

依赖关系为 `T01 -> T02/T04/T06 -> T03/T05/T07 -> T08 -> T09 -> T10`。任何阶段未通过，不提前开始依赖阶段的正式实现；可以并行编写不依赖代码的 fixture 和 schema 文档。

## 17. 测试和验收的可执行清单

### 17.1 MCP 协议层

对 `server_manifest.py` 中全部服务运行现有 smoke；新服务必须加入 `SERVER_SPECS`、probe tool 和 probe arguments。新增服务还必须覆盖 `tools/list`、`resources/list`、未知工具、非法 schema、重复 idempotency key、Resource 读取。

### 17.2 业务流程层

```text
P01 正常：project -> research -> market -> option -> finance_spec
P02 中断：dr_create_checkpoint -> dr_resume -> delivery_resume
P03 研究不足：dr_submit(partial) -> market 被拒绝 -> dr_continue
P04 上游变化：修改 market -> option/scale/finance/report 全部 stale
P05 财务失败：finance_run -> balance/勾稽失败 -> 保留失败 run，不生成表包
P06 表格失败：technical 通过、formal 失败 -> 不允许 export
P07 报告整改：finding -> propose -> diff -> apply -> retest
P08 知识拒绝：candidate -> rejected -> publish blocked
P09 预览隔离：controlled_assumption -> preview_only=true -> release blocked
P10 恒立酒店：收购范围、收购价、历史经营、混合收入池、13 表、报告全链路
```

### 17.3 数值和对象一致性

- `FinanceSpec.input_hash == BasisOfEstimate.input_hash`。
- `FinanceRun.finance_spec_id` 必须是已确认版本。
- 13 张表的每一张 `run_id` 相同，表间合计和资产负债表勾稽通过。
- 报告第 5、6 章的投资、收入、成本、IRR、DSCR 只能来自同一个 `FinanceRun`。
- 任何 citation locator、evidence pack、parent ref 缺失都必须返回结构化 blocker。

### 17.4 直接执行命令

```bash
conda run -n lvke-mcp python -m lvke_mcp.testing.smoke_test
conda run -n lvke-mcp python -m unittest discover -s tests/integration -p 'test_*.py' -q
conda run -n lvke-mcp python -m compileall -q src tests
python /Users/mac/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /Users/mac/Desktop/工程/hubei-lvke/skills/project-planning/lvke-feasibility-study
```

以上命令只验证 MCP、Resource、对象状态、证据链、财务和交付流程；不包含前端、HTTP 服务、权限或安全审查测试。

## 18. 迭代计划和每轮产出

### 第 1 轮：契约和最小闭环

目标是让 `project -> research(partial) -> checkpoint/resume -> market blocked` 可运行。产出 T01-T03；不追求完整报告和财务样本。验收重点是状态快照、父子引用和幂等行为。

### 第 2 轮：研究到市场

目标是让 Tavily Hikari 的现有链路完成 `discover/search/fetch/collect -> analysis -> EvidencePack -> MarketSizingCase`。产出 T04-T05；验收重点是 locator、missing_fields、冲突和 partial 语义。

### 第 3 轮：方案、要素和财务

目标是打通方案比选、规模、成本、定员、收入、FinanceSpec、BoE、FinanceRun。产出 T07；先使用产品销售和厂房出租 fixture，再扩展其他业态。

### 第 4 轮：十三表和恒立酒店

目标是将统一 `run_id` 交给 `lvke-finance-tables`，完成技术校验、正式校验、导出和恒立酒店收购特例。验收重点是建设期利息、分年借款还款、流动资金、混合收入池和资产负债平衡。

### 第 5 轮：报告、审查、知识和总入口

目标是完成 9 章绑定、整改复测、候选审核发布和 `lvke-feasibility-study` Skill。产出 T06、T08-T10；只有该轮完成后才验收 `formal_release`。

每轮结束必须留下：变更文件清单、工具契约快照、正面/负面测试结果、未完成 blocker、下一轮输入。不提交与本方案无关的前端、服务端或安全模块。

## 19. 开发完成定义

本方案对应的开发只有在以下条件同时满足时才算完成：

1. 新 MCP 已加入 manifest，stdio smoke 和 Resource 读取通过。
2. 总入口 Skill 的每一个调用名、输入字段和输出字段都能在当前代码或本次变更中找到。
3. 任一阶段都可以通过 `status/next_actions/checkpoint/resume` 继续，不靠隐含上下文恢复。
4. 上游对象变化会使下游变为 stale，旧快照仍可读取但不能发布。
5. `partial`、`controlled_assumption`、`technical_fixture` 不会进入正式 FinanceRun、正式表包或 release。
6. 研究证据、市场案例、建设规模、成本/收入驱动、FinanceSpec、FinanceRun、13 表和报告章节可以沿 lineage 双向追溯。
7. 六类财务 fixture 和恒立酒店 fixture 通过正面、负面及回归测试。
8. 知识候选必须经过审核才能发布，发布结果具备不可变 Resource。
9. 文档、Skill、MCP 实现和测试对工具名称、状态名称、模式名称保持一致。
10. 全过程不新增前端、HTTP/独立后端、联网搜索 MCP、安全审查、认证、授权、权限控制或租户隔离。

## 20. 可直接照抄的调用样例

以下样例不是新的 API 设计，而是把当前 MCP envelope 和拟新增编排工具组合成可测试的调用顺序。正式实现时，字段名称以各服务的 JSON Schema 为最终准则。

### 20.1 创建项目和交付运行

```json
{
  "tool": "project_context_create",
  "arguments": {
    "workspace_id": "demo-product-sales",
    "context": {
      "project_name": "湖北某农产品加工项目",
      "industry_code": "agriculture_food",
      "project_type": "new_build",
      "region": {"province": "湖北省", "city": "咸宁市"},
      "objective": "形成正式可研报告和十三张财务附表",
      "report_type": "feasibility_study"
    },
    "idempotency_key": "pc-demo-product-sales-v1"
  }
}
```

读取成功响应中的 `project_context_id` 和 `basis_hash` 后，再执行：

```json
{
  "tool": "feasibility_start",
  "arguments": {
    "workspace_id": "demo-product-sales",
    "project_context_id": "pc_01",
    "delivery_mode": "review_candidate",
    "idempotency_key": "fdr-demo-product-sales-v1"
  }
}
```

预期结果是 `status=in_progress`、`current_stage=project`。Skill 随后调用 `feasibility_stage`，将 `project_context_id` 写入 `project.output_refs`，再推进到 `research`。如果项目上下文缺行业或区域，预期响应如下，且不得创建 market 对象：

```json
{
  "success": false,
  "status": "blocked",
  "code": "project_context_incomplete",
  "blockers": ["industry_code_required", "region_required"],
  "next_actions": ["调用 project_context_revise 补齐字段"]
}
```

### 20.2 研究和证据采集

```json
{
  "tool": "dr_prepare",
  "arguments": {
    "workspace_id": "demo-product-sales",
    "topic": "湖北农产品加工区域供需和产能缺口",
    "industry": "agriculture_food",
    "region": "湖北省咸宁市",
    "profile": "deep_standard"
  }
}
```

Skill 读取计划后调用 `dr_start`，然后只使用现有采集链：

```text
data_discover(target_count=10)
-> data_search(补充缺口问题)
-> data_fetch(选定 URL)
-> data_collect(批量固化 Source Snapshot)
-> analysis_ingest(snapshot)
-> analysis_extract_candidates(fields=[regional_capacity, demand_gap, price])
-> analysis_build_evidence_pack(selected_source_ids, candidate_ids)
```

当 `analysis_build_evidence_pack` 返回 `missing_fields` 或 `conflicts` 时，Skill 将其转换为新的 `dr_continue.supplemental_questions`。当 `dr_submit` 返回 `status=partial` 时，交付运行保持在 `research=partial`，`feasibility_validate(scope=formal)` 必须返回 `research_incomplete`。

### 20.3 市场、方案和规模

只有拿到 `evidence_pack_id` 后才允许：

```json
{
  "tool": "planning_prepare_market_case",
  "arguments": {
    "workspace_id": "demo-product-sales",
    "project_context_id": "pc_01",
    "evidence_pack_id": "ep_01",
    "candidates": [{
      "candidate_id": "market-bottom-up-01",
      "method": "bottom_up",
      "market_size": 120000,
      "unit": "吨/年",
      "period": "2025",
      "region": "咸宁市",
      "target_share": 0.12,
      "evidence_bindings": [{
        "source_id": "src_01",
        "source_type": "web_snapshot",
        "content_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "locator": "page:12",
        "evidence_track": "real"
      }]
    }],
    "idempotency_key": "market-demo-product-sales-v1"
  }
}
```

随后必须按 `prepare -> validate -> confirm` 完成市场案例，再按 `prepare_option_comparison -> score -> confirm` 完成方案比选。`planning_solve_build_scale` 的输入只能引用已确认 `market_case_id` 和 `option_comparison_id`；Skill 不得把用户口述的“建设 10 万吨”直接传给财务服务。

### 20.4 财务和十三表

```json
{
  "tool": "finance_prepare_spec",
  "arguments": {
    "workspace_id": "demo-product-sales",
    "strategy": "propose_from_project",
    "input_revision": {
      "project_context_id": "pc_01",
      "market_case_id": "msc_01",
      "option_comparison_id": "opt_01",
      "build_scale_case_id": "bsc_01",
      "cost_driver_set_id": "cds_01",
      "labor_plan_id": "lp_01",
      "revenue_driver_set_id": "rds_01"
    },
    "evidence_pack_ids": ["ep_01"]
  }
}
```

上面的 `input_revision` 是本方案要求补齐的父对象绑定字段；当前版本如果 schema 尚未接收全部字段，应先按当前 `finance_input_schema()` 的字段映射适配，并在 T07 中统一收口。确认 FinanceSpec 和 BoE 后，后续调用只能传对象 ID：

```text
finance_validate_spec(finance_spec_id)
-> finance_build_basis_of_estimate(finance_spec_id)
-> finance_confirm_spec(finance_spec_id, expected_basis_hash)
-> finance_run_model(finance_spec_id)
-> finance_get_run(run_id)
-> tables_render(run_id)
-> tables_validate(run_id, validation_scope="technical")
-> tables_validate(run_id, validation_scope="formal")
-> tables_export_xlsx(run_id)
```

如果 BoE 中任何关键数字缺 `source_ref` 或 `locator`，`finance_validate_spec` 返回 `formal_basis_missing`；Skill 只能回到资料或人工确认，不得通过 Skill 填入默认值。若 technical 通过但 formal 失败，不能调用 export，必须保留失败 package 供定位。

### 20.5 报告、审查和发布

9 个章节 Skill 逐章执行：

```text
report_prepare(project_context_id, finance_run_id, evidence_pack_id)
-> report_propose_section(chapter_no, upstream_refs)
-> report_diff(revision_id)
-> report_apply(revision_id, expected_basis_hash)
-> report_validate_section(revision_id)
```

全部章节通过后执行：

```text
report_get_readiness
-> review_prepare
-> review_start
-> review_list_findings
-> review_disposition_finding
-> review_retest
-> knowledge_submit_candidate
-> knowledge_review_candidate
-> knowledge_publish_release
-> feasibility_validate(scope="formal")
-> feasibility_release
```

任何 finding 的 `status=open`、任何章节缺 `upstream_refs`、任何关键句子缺 `citation_locator`，都只能返回 blocker。`feasibility_release` 的输出必须列出最终 `FinanceRun`、十三表 package、报告 revision、review run 和知识 release 的 URI，供调用方保存交付清单。

## 21. 已有能力、待补能力和明确不做的能力

| 类型 | 现在已有 | 本方案补齐 | 明确不做 |
|---|---|---|---|
| 联网研究 | `tavily_hikari`、`data_discover/search/fetch/collect` | 研究质量摘要和证据回填 | 新建搜索 MCP、第二个联网代理 |
| 研究状态 | `dr_prepare/start/submit/status/checkpoint/resume` | 质量字段和市场字段绑定 | 把 `partial` 改成假完成 |
| 项目建模 | project、market、option、scale、cost、labor、revenue 工具 | parent_refs/lineage 强制绑定 | 在 Skill 内重算领域对象 |
| 财务模型 | FinanceSpec、BoE、FinanceRun、资产负债表和 Monte Carlo | 模式边界、来源明细、样本验收 | 在 Skill 内实现 IRR/NPV/税费/十三表公式 |
| 财务表 | `tables_render/validate/export` | 同一 run_id 和正式门禁 | 接收散乱数字重新生成表 |
| 报告审查 | report MCP、review MCP、章节 Skills | 9 章绑定和验收矩阵 | 前端编辑器或 HTTP 报告服务 |
| 知识治理 | candidate、snapshot | review、publish release | 未审候选直接写长期知识 |
| 总流程 | 各领域 Skill 独立可用 | `lvke-feasibility-delivery` + `lvke-feasibility-study` | MCP 自主调用其他 MCP、独立后端、权限控制 |

## 22. 当前实施状态

| 方案任务 | 状态 | 已落地内容 |
|---|---|---|
| 可研交付编排 MCP | 已完成第一版 | `src/lvke_mcp/servers/lvke_feasibility_delivery`；运行快照、阶段推进、stale、checkpoint/resume、technical/formal 校验、release、Resource |
| MCP manifest 和协议验证 | 已完成 | manifest 扩展为 24 个服务；全量 stdio smoke 通过 |
| 总入口 Skill | 已完成第一版 | `skills/project-planning/lvke-feasibility-study/SKILL.md`；已通过 `skill-creator` validator |
| 知识候选审核 | 已完成第一版 | `knowledge_review_candidate`，支持 accepted/rejected/needs_revision |
| 知识发布 | 已完成第一版 | `knowledge_publish_release`，只有 accepted 审核才能生成 KnowledgeRelease |
| Deep Research 质量字段 | 已完成第一版 | `quality_summary`、`market_field_bindings` 随 `dr_submit` 固化；`partial` 语义保持不变 |
| 项目规划 lineage | 已有能力并纳入编排 | Market/Option/Scale/Cost/Labor/Revenue 对象已保存 parent IDs 和 lineage |
| 报告上游绑定 | 已有能力并纳入编排 | report preparation、FinanceRun、EvidencePack、ResearchPackage 和 readiness 已有绑定检查 |
| 财务和十三表 | 已完成技术样本验收 | `test_finance_fixture_acceptance.py` 通过产品销售、厂房出租、墓地/存量销售、房地产、文旅和非经营性资金平衡样本；`inventory_sales` 已补齐有限存量去化与存货成本展开；正式候选仍需真实 BoE 和甲方模板资料 |
| 9 章报告和审查矩阵 | 已完成最小串联验收 | `test_mcp_delivery_chain.py` 验证 FinanceRun -> 十三表 package -> report preparation/revision -> delivery lineage；完整 9 章正文和 review findings 仍需真实报告样本 |

本次实现没有新增联网搜索 MCP，也没有开发前端、HTTP/独立后端、认证授权、权限控制或安全门禁。当前本地验证统一使用 conda 环境 `lvke-mcp`（`/opt/miniconda3/envs/lvke-mcp/bin/python`）：协议 smoke、core golden、编译检查、Skill 校验和新增业务回归均已执行。独立性扫描结果为 `forward=0`、`internal_architecture=0`；旧扫描规则对 `reviewed_at`/`released_at` 时间字段的语义告警不属于外部调用或权限控制。

### 22.1 本轮新增实现与验证

- `revenue_models._scheduled` 为 `inventory_sales` 输出有限存量的逐年 `absorption`，财务内核据此结转开发存货成本并完成流动资金勾稽。
- `lvke-feasibility-delivery` 的 completed 阶段现在强制记录 `basis_hash`；formal 校验同时检查每个完成阶段的输入引用、输出引用和 basis hash。
- 新增 `tests/integration/test_finance_fixture_acceptance.py`、`test_deep_research_quality.py` 和 `test_mcp_delivery_chain.py`。
- 新增测试覆盖研究质量字段持久化、同一 `run_id` 的十三表和报告绑定、交付 lineage、恒立酒店六档只读基准轨边界和非经营性资金平衡。

本轮样本仍明确标记为 `technical_fixture` 或 `estimate_preview`；它们证明 MCP 契约和计算链可重复，不替代甲方正式证据、人工确认的 BoE、真实模板样本或最终 formal release。

### 22.2 来源重建正式验收实现

甲方不再提供原始经济测算 Excel，因此本轮正式验收采用独立的
`source_reconstructed` 证据轨。真实盖章报告、5 份财务模板、恒立 6 份历史
`.xls` 报表和恒立底稿均以只读 manifest 保存路径、SHA-256、定位、重建方法和
限制；原件不覆盖，修订报告单独保存为两份完整 9 章 Markdown。

来源重建记录必须包含：

```text
reconstruction_id
source_uri
content_hash
locator
source_kind
method
original_formula_available
limitations
```

`source_reconstructed` 可以进入 FinanceSpec/BoE、十三表、报告和 review 的流程
验收，但输出始终带 `project_fact_certified=false`。交付运行新增
`evidence_policy`、`release_scope`、`reconstructed_source_ids`、
`unresolved_inputs` 和 `release_limitations`：

- `formal_release(process_acceptance)` 在所有阶段、lineage、technical/formal 表校验和审查复测完成后可以通过。
- `formal_release(project_delivery)` 在来源重建轨固定返回 `project_fact_evidence_missing`。
- `controlled_assumption` 进入 formal 路径固定阻断；不产生隐式默认值。

验收资产和回归位于 `tests/fixtures/source_reconstructed/` 与
`tests/integration/test_source_reconstructed_acceptance.py`，覆盖报告 hash、模板
sheet/公式、恒立历史 `.xls` 读取、六档价格独立性、9 章完整性、process acceptance
release 和 project delivery 负面路径。该路径不新增联网搜索、前端、HTTP/独立后端、
认证授权、权限控制或安全审查。
