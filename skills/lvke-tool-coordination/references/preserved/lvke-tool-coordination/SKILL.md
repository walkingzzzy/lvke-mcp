---
name: lvke-tool-coordination
description: >
  Coordinate multiple MCP services for end-to-end Lvke tasks. Hard-requires
  multiple Tavily queries and independent published sources before conclusions or numeric tables: Tavily discovery
  with multiple queries and independent sources, then fetch/import, ingest/extract,
  finance model, tables/report. Use when the user asks 从零开始, 联网搜索,
  查找数据, 研究报告套件, or lacks complete inputs. Progressive fallback on
  errors; never single-search-then-deliver. Also coordinates immutable object
  lineage, typed next actions, evidence eligibility, and Codex recovery after
  MCP restarts; MCP never writes the final report narrative. Also use for MCP
  一句话生成十三表与研究报告、全量测试、对话式工具测试、重启后复测与验收；这些验收必须
  在当前对话真实调用 MCP，禁止用 pytest、测试脚本或历史结果替代。
---

# 工具协调 Skill

目标是协调多个 MCP 服务完成端到端任务，从联网搜索到财务报告生成，充分利用所有可用工具。

## 一句话交付入口

用户用一句话要求从现有资料生成十三表与报告时，必须完整读取并执行 [[references/one-sentence-delivery.md]]。不得新增总控 MCP，不得将 Codex 的意图解释与正文撰写转移给 MCP。

## 硬门禁：MCP 验收必须是对话式真实调用

当用户要求 MCP 全量测试、对话式工具测试、重启后复测、确认 MCP 是否还有问题，或要求用项目生成十三表和研究报告进行验收时：

1. 进入“实时 MCP 对话式验收”模式，并同时使用 `lvke-mcp-conversational-acceptance`。
2. 直接读取重启后的服务清单和各服务 `tools/list`，以实时工具数为唯一覆盖分母。
3. 在当前 Codex 对话中逐个真实调用 MCP；不得用 pytest、focused/smoke/golden runner、测试脚本、HTTP 路由测试、代码扫描或历史调用记录替代。
4. 用户未明确要求时，不运行 pytest；不得让开发回归阻塞真实 MCP 主链。
5. 代码必须先冻结再请求重启。重启后若继续修改 MCP 代码，本轮结果立即失效，修复后重新冻结并创建全新 workspace/run/package/revision/review。
6. 未真实调用的工具不得标记 PASS。只使用 `PASS`、`EXPECTED_REJECTION`、`UPSTREAM_FAILURE`、`SKIPPED`，并记录输入、耗时、状态、trace 和 lineage。
7. 验收必须实际导出并核验恰好 **14** 张正式附表的 XLSX、**15** 个 CSV（14 表 + 数据血缘）和中文可见的 DOCX；张数以实时 `engine_delivery_count` 为准，「十三表」只是业务惯称；测试通过数量不是交付证据。
8. 本产品没有登录、身份、tenant、角色、RBAC、权限管理或安全签审；不得在验收中增加这些步骤。

14 个服务必须共享同一个构建身份：`build_commit`、`build_time` 和完整
`plugin_version`。源码 checkout 中只要 metadata commit 不是当前 HEAD 或
tracked worktree 脏，必须返回 `build_metadata_complete=false`，不得冒用旧
build time。插件生成、Skill inventory、cachebuster 和冻结提交完成后，才
写入精确构建 metadata。

用户说“只回答问题”时，不调用工具，也不继续后台测试。范围扩张必须先获得用户明确授权。

## Agent Coordination Contract

MCP 是确定性数据、计算、版本和门禁基础设施，Codex 是意图、选择、冲突解释、章节写作和恢复的协调者。每次正式 MCP 调用返回的 `coordination` 字段使用 `agent-coordination.v1`，记录：

- `stage`、`input_object_ids`、`output_object_ids`、`expected_output_types` 和 `lineage`；
- `quality_state`（`ok`、`partial`、`missing_inputs`、`blocked`、`incomplete`、`failed`、`upstream_failure`）；
- `evidence_eligibility`（`formal_evidence`、`technical_fixture`、`controlled_assumption`、`estimate_preview`、`candidate`、`selected_fact`）；
- 结构化 `next_actions`、`retry_policy` 和 `resume_token`。

`success=true` 只表示业务状态为 `ok/accepted`。`system_success=true` 只表示处理器完成了响应，不能把 `partial` 或 `blocked` 当成成功。Codex 重启后必须从 workspace Resource 重新读取这些对象，不能依赖聊天上下文。

不可变对象链为：`ProjectContext → SourceFileSnapshot/SourceSnapshot → CandidateSet → EvidencePack → ResearchPackage → MarketSizingCase → OptionComparison → RevenueDriverSet/BuildScaleCase → CostDriverSet/LaborPlan → FinanceSpec → FinanceRun → FinanceTablesPackage → ReportRevision → RubricAssessment → Review → Artifact/KnowledgeCandidate`。任何事实、假设或选定口径变化都创建新的下游对象；禁止覆盖旧 run、package 或 revision。

Codex 不得自行计算 IRR/NPV/税费或把搜索摘要当证据；MCP 不得调用隐藏 LLM、替 Codex 撰写最终正文或静默选择冲突候选。技术夹具和受控假设永远不能升级为正式证据。

## 硬门禁：Tavily 多查询、多发布主体来源未完成不得落结论

与 `lvke-source-acquisition` 一致。端到端任务**必须先过阶段1+2**，禁止：

- 单次搜索 → 直接写 CSV / 十三表 / 报告结论
- 跳过 discover / 多查询，用摘要冒充证据
- 未交叉验证就取平均或虚构投资额填模型

**阶段1通过标准（全部满足）**：

| 项 | 最低要求 |
|---|---|
| 联网 provider | 仅 Tavily；不要求或回退到其他 provider |
| 查询角 | ≥3 条不同 query（规模/政策/成本等） |
| 来源独立性 | 每个关键结论尽量由 ≥2 个不同发布主体的来源交叉验证 |
| 候选量 | 合并去重后尽量 ≥20；`data_discover` 宜 `target_count≥30` + `auto_expand=true` |
| 记录 | 写明 Tavily 查询、候选条数、来源主体和 `data_provider_status` |

**阶段2通过标准**：对入选 URL 完成正文固化（`data_fetch`/`data_collect` 或外部 extract → `data_import_external_snapshot`）。仅摘要时必须标注「摘要非证据」，且**不得**写入可复算财务数字。

## 触发条件

使用本 Skill 的场景：
- 用户要求"从零开始"、"联网搜索"、"查找数据"、"研究报告套件"
- 用户未提供完整的财务参数
- 需要从公开资料收集投资/收入/成本数据
- 需要处理 PDF/Excel/Word 文档

## 工具协调策略

### 阶段0：项目初始化与行业 Skill 路由

```
project_context_create → project_context_validate
  → planning_resolve_industry_skill
```

- 先用 `lvke-project-initialization` 固化项目类型、地区、交易结构、资产类型和证据轨。
- 行业路由只读取不可变 ProjectContext；无匹配或歧义时阻断，不使用通用行业默认值。
- 用户附件先走 `lvke-source-files` 的安全导入、扫描和解析；“已解析”不等于“已采信”。
- ProjectContext 改变时根据 stale 清单重建下游对象，不沿用旧 run/package/revision。

### 阶段1：Tavily 多查询搜索（强制，不可跳过）

**目标**: 获取足够候选（建议合并去重后 ≥20），并覆盖多个研究角

**工具选择**（同属 Tavily provider，可任选其一或组合，不要求双通道）:
1. **tavily-hikari**（已配置时可用）
   ```
   tavily_search(query, search_depth="advanced", include_raw_content="markdown")
   ```
2. **lvke-data-acquisition**
   ```
   data_discover(workspace_id, queries=[...≥3条...], auto_expand=true, target_count=40)
   ```
   单条 `data_search` **不能**单独作为阶段1完成标志。

**协调逻辑**:
- 多条 Tavily 查询的 URL 合并去重；不得把两个调用入口描述成两个 provider
- 噪声大时收紧 query / `domain_allowlist`，换查询角度补搜，登记 issue
- 保留 discovery_set_id / search 结果供阶段2选用
- Tavily 失败时返回 `upstream_failure/partial`，不得调用已注销的内置 Web Search。

**示例**:
```
查询主题: "中国冷链物流市场规模"

并行:
  tavily_search("中国冷链物流 需求总量 亿吨 2024 2025", search_depth="advanced")
  data_discover([规模, 政策, 成本], auto_expand=true, target_count=40)

合并去重 → 再进入阶段2抓取/回灌
```
---

### 阶段2：渐进式内容提取

**目标**: 获取完整页面内容（Markdown格式）

**工具链**（按顺序尝试，任一成功即停止）:

1. **tavily-hikari.tavily_extract**
   - 获取正文后必须调用 `data_import_external_snapshot`回灌固化。

2. **lvke-data-acquisition.data_fetch** (完整，带来源追溯)
   ```
   data_fetch(workspace_id, urls, content_mode="readable", extraction_provider="auto")
   ```
   - 优点：完整内容，固化快照，带 lineage
   - 缺点：可能遇到网络策略限制

3. **playwright 浏览器** (动态网页，最强)
   ```
   browser_navigate(url)
   browser_snapshot(format="markdown")
   ```
   - 优点：处理动态网页，绕过反爬
   - 缺点：慢，资源消耗大

4. **回灌兜底**（抓取被安全门拦截时）
   - `tavily_extract` / search 的 `include_raw_content` → `data_import_external_snapshot`
   - `auto` 只使用受信 Tavily receipt 或同 URL 的受控 direct HTTP；外部回灌无 receipt 时只作候选
   - 不得因 `data_fetch` 失败而跳过正文、直接写结论

5. **摘要兜底**（所有正文路径失败）
   - 仅允许保留在血缘表，标注「摘要非证据」
   - **禁止**用摘要填市场规模数值或财务测算输入

**协调逻辑**:
```python
for url in top_urls[:3]:
    try:
        content = tavily_extract(url)
        snapshot = data_import_external_snapshot(content)
        break
    except UpstreamError:
        try:
            snapshot = lvke_data_acquisition.data_fetch(url)
            break
        except BlockedError:
            try:
                content = playwright.fetch(url)
                snapshot = data_import_external_snapshot(content)
                break
            except Error:
                # 全部失败：记 url_fetch_failed，不得用 search_snippet 当正文
                snapshot = None
```

---

### 阶段3：文档处理（按格式分发）

**目标**: 将 PDF/Excel/Word 转换为结构化数据

**工具映射**:

| 文档格式 | 主工具 | 备用工具 | 用途 |
|---|---|---|---|
| PDF（表格） | oxidize-pdf | markitdown-mcp | 提取表格→CSV/JSON |
| PDF（文本） | markitdown-mcp | - | 转换→Markdown |
| Excel | markitdown-mcp | - | 转换→Markdown表格 |
| Word | markitdown-mcp | - | 转换→Markdown |

**协调逻辑**:
```
if file_type == "pdf":
    if contains_tables:
        oxidize-pdf → tables.json
    else:
        markitdown-mcp → text.md

elif file_type in ["xlsx", "xls"]:
    markitdown-mcp → tables.md

elif file_type in ["docx", "doc"]:
    markitdown-mcp → text.md
```

**示例**:
```
输入: 某光伏项目可研报告.pdf
  ↓
oxidize-pdf.extract_tables()
  ↓
输出: 
  - 投资估算表.json
  - 融资方案表.json
  - 收入预测表.json
```

---

### 阶段4：数据结构化提取

**目标**: 从非结构化文本中提取财务参数

**工具**: lvke-data-analysis

**步骤**:
1. **摄入原始内容**
   ```
   analysis_ingest(workspace_id, source_snapshot_ids)
   ```

2. **提取字段候选**
   ```
   analysis_extract_candidates(
       workspace_id, 
       analysis_task_id,
       field_specs=[
           {field: "总投资", expected_unit: "万元"},
           {field: "年收入", expected_unit: "万元"},
           {field: "建设期", expected_unit: "月"},
           ...
       ]
   )
   ```

3. **比较验证**（如有多来源）
   ```
   analysis_compare(observations=[...])
   ```

4. **固化证据包**
   ```
   analysis_build_evidence_pack(
       workspace_id,
       analysis_task_id,
       selected_source_ids,
       fact_candidates
   )
   ```

**输出**: evidence_pack_id（带完整数据 lineage）

---

### 阶段4.5：Deep Research（Codex 研究、MCP 固化）

```
dr_prepare(workspace_id, topic, objective, known_materials)
  → dr_start(workspace_id, research_brief, analysis_inputs, idempotency_key)
  → Codex 读取 EvidencePack 并撰写带 locator 的研究发现
  → dr_submit(workspace_id, task_id, findings, citations)
  → dr_get_report / dr_get_evidence / dr_get_bundle
```

Deep Research MCP 不调用内置 LLM。`partial` 可以通过 `dr_continue` 续研，取消使用独立任务调用 `dr_cancel`；任何研究数字都不能绕开 EvidencePack 直接进入 FinanceSpec。

计划变更、中断或重启恢复使用 `lvke-research-recovery`：`dr_get_plan → dr_propose_plan_revision → dr_apply_plan_revision → dr_create_checkpoint → dr_resume`。来源增删保留不可变事件和原始资格，不暴露 chain-of-thought。

---

### 阶段4.75：市场、收入、规模、成本与定员规划

```
planning_prepare(object_kind=market_case) → planning_compare(object_kind=market_case)
  → planning_validate(object_kind=market_case) → planning_confirm(object_kind=market_case)
  → planning_prepare(object_kind=policy_basis) → planning_confirm(object_kind=policy_basis)
  → planning_prepare(object_kind=option_comparison) → planning_validate(object_kind=option_comparison)
  → planning_score_option_comparison → planning_confirm(object_kind=option_comparison)
  → planning_prepare(object_kind=revenue_drivers) → planning_compare(object_kind=revenue_drivers)
  → planning_validate(object_kind=revenue_drivers) → planning_confirm(object_kind=revenue_drivers)
  → planning_get_industry_constraints → planning_solve_build_scale
  → planning_validate(object_kind=build_scale) → planning_confirm(object_kind=build_scale)
  → planning_prepare(object_kind=cost_drivers) → planning_calculate_cost_drivers
  → planning_validate(object_kind=cost_drivers) → planning_confirm(object_kind=cost_drivers)
  → planning_infer_labor_plan → planning_validate(object_kind=labor_plan)
  → planning_confirm(object_kind=labor_plan)
```

- 市场规模至少使用两条证据支持的独立路径；由 Codex 选择，不取平均。
- RevenueDriverSet 只把 `/revenue` 写入 `FinanceSpec` ledger，并复用财务收入模型。
- BuildScaleCase 必须同时满足已选市场需求、用地、产能、容积率、覆盖率和绿地率。
- OptionComparison 的得分由 MCP 复算，最终选择必须由 Codex/人员给出理由并列出全部未选方案。
- CostDriverSet 与 LaborPlan 只产生 `FinanceInputRevision` ledger；投资不闭合、成本明细不足或岗位不可复算时停止。
- 以上对象继承 ProjectContext 的 evidence track。受控假设可用于 estimate preview，但不得升级为正式证据。

---

### 阶段5：财务建模（MCP 确定性真源）

**目标**: 生成企业级十三表

**唯一正式工具链**：

```
finance_prepare_spec(workspace_id, input_revision, evidence_pack_ids, mode)
  → finance_validate_spec(workspace_id, spec_id)
  → finance_confirm_spec(workspace_id, spec_id, idempotency_key)
  → review candidate 时 finance_build_basis_of_estimate(workspace_id, spec_id, ...)
  → finance_run_model(workspace_id, spec_id, basis_of_estimate_id, mode,
                      idempotency_key)
  → finance_get_run(workspace_id, run_id)
```

`estimate_preview` 可明确使用受控假设，但不得提升为正式证据或 review candidate。BoE 缺失不阻止 estimate preview；`technical_fixture` BoE 只能得到 `technical_ready` 并绑定 estimate preview，不得得到 `formal_ready`。review candidate 和 formal tables 必须由全部 `formal_evidence` 的 BoE 解锁。相同幂等键不同载荷必须停止并处理冲突。

本地 Python 引擎只作为 MCP Server 内部的共享实现，不能作为 Codex 的旁路回退。MCP 失败时，Codex 必须依据 `missing_inputs`、`blocked` 或 `upstream_failure` 进行补料、重试或明确降级；没有 `run_id` 就不能生成正式十三表或绑定报告数字。

---

### 阶段6：企业级验证

**目标**：验证五道确定性门禁。

1. `finance_get_run` 的 `consistency_ok=true`，投资、成本、工资、税费和现金流均无 blocker。
2. `tables_render` 只消费该 `run_id`，随后 `tables_validate` 整包通过。
3. `tables_list_tables` 返回恰好 **14** 张正式交付表（附表1-10 + 附表11 财务计划现金流量表）；指标、计算、校验、血缘和审计页不计入交付表。
4. 14 个交付表静态别名与 `tables_get_table` 的内容 hash、`run_id`、`package_id` 和 lineage 一致，单表读取不得触发重算。
5. XLSX、15 个 CSV（14 表 + 数据血缘）与 Resource 可读；单表局部验证不得授予 `formal_delivery_ready`。

---

### 阶段7：十三表与 Codex 报告修订

**目标**：从同一 `run_id` 生成十三表，由 Codex 基于固化证据撰写报告正文。

**工具**: lvke-finance-tables + lvke-report-generation

**步骤**:
1. **渲染十三表**
   ```
   tables_render(workspace_id, run_id, format="structured")
   ```

2. **导出 XLSX**
   ```
   tables_export_xlsx(workspace_id, run_id)
   ```

3. **准备报告并固化初始 revision**
   ```
   report_prepare(workspace_id, evidence_pack_ids, research_package_ids,
                  finance_binding{run_id, finance_tables_package_id}, outline)
   report_start(workspace_id, report_preparation_id)
   report_status(workspace_id, task_id)
   report_list_sections(workspace_id, report_revision_id)
   ```

4. **Codex 撰写和修订正文**
   ```
   report_propose_section(workspace_id, report_revision_id, section_id,
                          proposed_content, summary, basis)
   report_diff(workspace_id, proposal_id)
   report_apply(workspace_id, proposal_id)
   report_validate_section(workspace_id, new_report_revision_id, section_id)
   ```

5. **整篇校验与技术稿导出**
   ```
   report_validate(workspace_id, report_revision_id)
   report_get_readiness(workspace_id, report_revision_id)
   report_export_docx(workspace_id, report_revision_id)
   ```

章节内容只能由 Codex 提案；MCP 不调用隐藏 LLM。章节局部通过不替代整篇 validate/readiness，DOCX 导出也不等于正式发布。

---

### 阶段8：统一质量审查（正式交付必经）

**目标**: 对财务十三表 / 研报做统一质量审查；**导出 ≠ 交付**，未解决关键 finding 不得宣称完成。

**工具**: lvke-deliverable-review（`review_prepare / review_start / review_get / review_list_findings / review_disposition_finding / review_retest / review_export`）

**关键约束**（与代码一致，勿放宽）:
- P0/P1 finding 必须显式处置，P0 不可豁免。
- `review_retest` 必须指向新目标并提供整改证据。
- `review_export` 只导出审查报告，不授予身份、角色、权限、专业签审或客户验收状态。

**目标类型选择**（审查对象是甲方原始 xlsx 时尤其注意）:
- 结构化对象：`finance_run` / `finance_tables_package` / `report_revision` / `acquisition_run` 等，均按 `workspace_id` 数据命名空间读取。
- 甲方上传的 xlsx：使用 `finance_xlsx_source` + `source_file_id` 绑定不可变来源；不要把裸路径当成正式证据。

**主链**:
```
review_prepare(workspace_id, target{target_type, ...}, rule_pack, standards)
  → review_preparation_id
review_start(workspace_id, review_preparation_id, mode="deep", deployment_mode="enforced")
  → review_id + findings
review_list_findings(...)                    # 逐条处置
review_disposition_finding(...)              # P0 不可豁免
review_retest(...)                           # 修复后带证据重测
review_export(review_id, formats=[...])       # 导出质量审查结果
feasibility_release(delivery_run_id, ...)    # 创建技术交付记录，不代表客户验收
```

章节质量改进使用 `review_list_rubrics → review_score_section → Codex propose/diff/apply → review_score_section → review_compare_assessments`。评分只生成确定性建议，不修改正文。详见 `lvke-review-release` 的 section quality improvement 子技能。

可复用经验使用 `lvke-knowledge-governance`：通过 rubric 且证据完整的内容才能 `knowledge_submit_candidate`；`knowledge_review_candidate` 记录内容质量决定，不认证人员或角色。只有 accepted 候选可发布为 reviewed knowledge。禁止 Agent 直接编辑正式 Skill 或长期记忆。

---


## 完整工作流示例

### 场景：某企业想投资建设50MW屋顶光伏项目

```
用户: "从零开始，帮我生成一个50MW屋顶光伏项目的财务报告"

→ 触发 lvke-tool-coordination

阶段1: Tavily 多查询、多发布主体来源搜索
  tavily_search("50MW屋顶光伏投资成本", search_depth="advanced") → 候选
  lvke-data-acquisition.data_discover([
      "50MW屋顶光伏投资成本",
      "屋顶光伏发电收入",
      "光伏运维成本"
  ], auto_expand=true) → 32条
  
  合并去重 → 25条结果

阶段2: 内容提取
  选取前5条权威来源
  for url in top_5_urls:
      try tavily_extract() → data_import_external_snapshot()
      → 成功3条，失败2条
      
  失败的2条:
      try lvke-data-acquisition.data_fetch()
      → 成功1条，失败1条（网络限制）
      
  失败的1条:
      仅保留搜索摘要作为检索线索，标记「摘要非证据」和 upstream limitation
      不得进入 evidence pack、财务模型或正式报告

阶段3: 数据提取
  lvke-data-analysis.analysis_ingest(4个快照)
  lvke-data-analysis.analysis_extract_candidates([
      {field: "单位投资", expected_unit: "元/W"},
      {field: "年发电量", expected_unit: "万kWh"},
      {field: "综合电价", expected_unit: "元/kWh"},
      {field: "运维成本", expected_unit: "元/kW·年"}
  ])
  
  提取结果:
    - 单位投资: 2.8元/W（4个来源一致）
    - 年发电量: 1200小时（湖北地区）
    - 综合电价: 0.65元/kWh（自用为主）
    - 运维成本: 来自已固化来源或明确受控假设

阶段4: 参数选择
  Codex 显式选择候选事实和口径，并固化 EvidencePack；
  不在对话中旁路计算总投资、收入或运维费。

阶段5: 财务建模
  finance_prepare_spec(...) → validate → confirm
    → finance_run_model(..., idempotency_key="稳定且仅用于本次逻辑运行的键")
  MCP 失败时依据 next_actions 补料或重试，不得从对话旁路调用本地引擎。
  
  生成:
    - 14张正式交付表；质量、公式、血缘和审计元数据独立返回，不进入交付工作簿
    - IRR: 9.87%
    - NPV: 6245万元
    - 回收期: 10.2年

阶段6: 企业级验证
  门禁1: ✅ 14张正式交付表齐全
  门禁2: ⚠️ 简化参数（未提供 InvestmentBreakdown）
  门禁3: ✅ 25年现金流
  门禁4: ✅ IRR/NPV已计算
  门禁5: ⚠️ 待完整验证
  
  总体: 3/5通过

阶段7: 报告生成
  tables_render() → Markdown格式
  tables_export_xlsx() → Excel格式
  
  交付:
    - 财务测算报告.md
    - 财务测算十三表.xlsx
    - 数据来源说明.md（含 lineage）

阶段8: 统一质量审查
  review_prepare(target{target_type:"finance_tables_package", ...})
    → review_preparation_id
  review_start(mode="deep", deployment_mode="enforced")
    → review_id + findings
  逐条处置 findings（P0 不可豁免）→ [review_retest 带证据]*
  review_export(review_id, formats=["json", "markdown"])
```

---

## 错误处理与回退

### 搜索失败
- **问题**: Tavily 超时、provider 调用失败或无结果
- **恢复**: 改写查询、收紧官方域、重试 Tavily，并检查 `data_provider_status`
- **兜底**: 保留 `upstream_failure/partial` 并要求用户提供数据；不得回退到内置 Web Search

### 内容提取失败
- **问题**: URL 被阻止（网络策略）
- **回退**: 尝试其他提取工具（playwright）
- **兜底**: 搜索摘要只能保留为检索线索，并明确标记降级；不得进入 evidence pack、财务模型或正式报告。无法取得正文时登记为 capability/upstream limitation

### MCP 服务错误
- **问题**: finance_run_model 返回系统失败
- **恢复**: 依据 `trace_id/retryable/next_actions` 处理，记录本地实现问题
- **说明**: 不得从 Codex 对话旁路执行财务引擎

### 数据缺失
- **问题**: 提取不到关键参数
- **处理**: 返回 `missing_inputs`，由 Codex 补充来源或显式受控假设
- **禁止**: 静默使用行业默认值或把估算值提升为正式证据

---

## 质量保证

### 数据来源审查
每个数据点必须标注来源：
- **一级**: 用户确认、合同文件
- **二级**: 公开可比（搜索结果、行业报告）
- **三级**: 受控假设（仅 `estimate_preview`，不得作为正式证据）

### 多来源验证
当有多个数据来源时：
1. 比较数值、期间、口径（总额 vs 市场规模预测等）
2. 口径不同或数值冲突：**并列披露**，登记冲突表；**禁止取平均**
3. 选定主口径须写明依据（如「采用中物联官方运行数据」）；未裁决不得进入正式财务输入

### 企业级门禁
所有财务输出必须通过5道门禁，不通过的明确告知。

---

## 不触发

- 用户已提供完整 FinanceSpec（直接调用 lvke-finance-modeling）
- 仅需要渲染十三表（直接调用 lvke-finance-tables）
- 仅需要搜索数据（直接调用搜索服务）

本 Skill 专注于**端到端任务协调**，单一工具任务使用对应的专门 Skill。

---

## 相关 Skills

- [[lvke-market-sizing]] - 多路径市场规模测算与明确选择
- [[lvke-revenue-drivers]] - 将市场需求映射为收入驱动
- [[lvke-build-scale]] - 产能、用地和规划约束下的建设规模
- [[lvke-cost-drivers]] - 投资闭合与经营成本驱动
- [[lvke-labor-planning]] - 岗位定员、工资和福利测算
- [[lvke-finance-spec]] - 准备 FinanceSpec
- [[lvke-finance-modeling]] - 运行财务模型
- [[lvke-finance-tables]] - 渲染十三表
- [[lvke-source-evidence]] - 数据来源与证据包
- [[lvke-project-initialization]] - 固化项目上下文与输入适用性
- [[lvke-source-files]] - 受控附件、安全扫描与解析 Resource
- [[lvke-quality-benchmarking]] - URL 审计和兼容口径基准比对
- [[lvke-research-recovery]] - Deep Research 计划修订、checkpoint 与恢复
- [[lvke-knowledge-governance]] - rubric 改进与 reviewed-first 知识发布
