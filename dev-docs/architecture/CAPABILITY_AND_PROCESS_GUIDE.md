# 绿科 MCP + Skills 能力现状与流程说明

> 审查日期：2026-08-28｜分支 `fix/delivery-honesty-and-skill-coverage`；下列实现仍在未提交工作树，不属于 HEAD `3f053a1`
>
> 事实来源：仓库源码、`tests/fixtures/baseline/`、实跑 pytest 与运行时自省、G1/G2/G3 验收报告。
> 本文的口径以**实测**为准。公开面为 **14 进程 / 173 工具**（零材料 8→10：
> `delivery_generate_template_pack`、`delivery_confirm_formal_promotion`）。
> `MCP_SERVICES.md` 的"已知限制"章节有历史条目，逐条复核见 §7.3。

本文回答三件事：**做成了什么**、**能做到哪一步**、**按什么顺序跑**。

---

## 1. 一句话定位

绿科当前是**「MCP 确定性引擎 + Skills 编排规范」**，不是带前端的可研工作台。

MCP 负责**已经被服务端编码并实际执行**的可复算、可追溯和业务拒绝；Agent 负责理解、
取舍和写作；Skills 负责向 Agent 提供调用顺序、责任边界和停止条件。Skills 本身不会自动
调用 MCP、保存 checkpoint、执行 Review → Retest → Export，或替 Agent 证明其上报的指标真实。

产品**不含**：前端、登录、身份、tenant、角色、RBAC、权限管理、安全审查、专业签审、
语音、协同办公。这些不是"还没做"，是明确的产品边界，多个 Skill 用否定式声明固化了它。

### 实测规模

| 项 | 实测值 | 依据 |
|---|---|---|
| MCP 进程 | **14** | `src/lvke_mcp/testing/server_manifest.py` 硬断言，`!= 14` 即 raise |
| 工具 | **173** | 逐个 `build_server()` 自省，与 `tests/fixtures/baseline/tools-list/` 一致 |
| Resources | **232** | `tests/fixtures/baseline/resources-list/` |
| Skills | **17**（发布 15，2 个 `dev_only`） | `skills/`；`skill_tool_mapping.json` 的 `dev_only_skills` |
| 业务代码 | domains 58,287 行 / servers 44,132 行 | `find … | wc -l` |
| 测试 | 以最近一次全量 pytest 为准，测试绿 ≠ 正式链闭环 | 见本文 §6 与 G3 报告 |

---

## 2. 十四个服务的职责

按数据流分层。每行的"核心对象"是该服务固化的不可变对象。

| 层 | Server | 工具 | 核心对象 | 职责 |
|---|---|---:|---|---|
| 资料 | `lvke-source-files` | 13 | `file_id` / `job_id` / `ups_*` | 受控导入、安全扫描、解析、工作簿检查 |
| 资料 | `lvke-data-acquisition` | 10 | `src_*` / `discovery_*` | 公网搜索、发现、采集、URL 审计、快照 |
| 资料 | `lvke-data-analysis` | 11 | `cset_*` / `evp_*` | 摄入、字段候选、归一化比较、趋势、证据包 |
| 研究 | `lvke-deep-research` | 18 | `drp_*` / `drcp_*` | Agent 主导的 DR 计划、混合来源、checkpoint、引用审计 |
| 规划 | `lvke-project-planning` | 17 | `pctx_*` + 7 类规划对象 | 项目上下文、市场/规模/收入/成本/定员/方案/政策 |
| 财务 | `lvke-finance-model` | 18 | `fsp_*` / `run_*` | FinanceSpec v3、确定性财务模型、BoE、蒙特卡洛 |
| 财务 | `lvke-finance-tables` | 8 | `ftp_*` | 只消费 run_id 的十三表渲染与导出 |
| 财务 | `lvke-asset-acquisition` | 12 | `acqrun_*` | 酒店月度 / 光伏年度收购模型、专用十三表 |
| 交付 | `lvke-report-generation` | 13 | `rprep_*` / `rrv_*` | 报告准备、提案改稿、校验、DOCX |
| 交付 | `lvke-deliverable-review` | 15 | `rvprep_*` / `review_*` | 规则审查、findings 处置、复测、导出、国标适用性 |
| 交付 | `lvke-feasibility-delivery` | 10 | `fdr_*` | 12 阶段编排、stale 传播、checkpoint、发布 |
| 交付 | `lvke-zero-material-delivery` | 10 | `zmr_*` | 一句话 → 受控假设 → 估算预览；拟定模板包与正式晋升 |
| 知识 | `lvke-knowledge-governance` | 6 | `knc_*` / `knrel_*` | 证据化知识候选、独立复核、reviewed-first 发布 |
| 参考 | `lvke-reference` | 12 | — | 档案/模板/政策/统计/地理的只读路由门面 |

`lvke-reference` 是**薄路由门面**（`service.py` 174 行自述 thin facade），底层 9 个 seed
服务数据量很小（政策 22 条、POI 68、档案 11 条等）。它的价值是提供锚点，不是权威数据源。

---

## 3. 三条不可让的产品约束

这三条是整个系统的设计轴，读代码时到处会撞见它们。

### 3.1 不可变对象链

任一上游事实变化，必须新建下游对象，**不覆盖**。

```
ProjectContext
  → SourceFileSnapshot / SourceSnapshot → CandidateSet → EvidencePack
  → ResearchPackage
  → MarketSizingCase → OptionComparison → RevenueDriverSet / BuildScaleCase
  → CostDriverSet / LaborPlan
  → FinanceSpec → FinanceRun → FinanceTablesPackage
  → ReportRevision → RubricAssessment → Review → Release / KnowledgeCandidate
```

对象 ID 是**内容寻址**：`{前缀}_{sha256(payload)[:24]}`。同 payload 天然去重，
且能在写库前预算 ID，因此 handler 可以先构建并校验完整响应、把写操作留到最后。

乐观并发靠 `expected_basis_hash`：改上游要带当前 hash，不匹配即拒绝并列出 stale 下游。

### 3.2 证据来源、证据策略与交付模式

“证据轨”“证据策略”和“交付模式”不是同一个字段。`real` 是来源标签，
`formal_evidence` 是资格策略，`formal_release` 是 `delivery_mode`；
`project_delivery` / `process_acceptance` 是 `release_scope`。它们不能互相替代。

| 来源/策略 | 允许用途 | 禁止或限制 |
|---|---|---|
| `formal_evidence` | 满足资格链后可用于正式交付 | 仍须通过自身资格、父级资格、项目事实认证和发布预检 |
| `real` | 描述来源可能是真实资料 | **不自动等于** `formal_evidence`，不能单凭此标签认证项目事实 |
| `source_reconstructed` | 过程验收（`process_acceptance`） | 正式发布；`project_fact_certified` 恒 false |
| `technical_fixture` | 仅 `technical_validation` 用途 | 正式发布；字段须在 `allowed_fields` 内 |
| `controlled_assumption` | 仅 `estimate_preview` | 正式交付（`controlled_assumption_formal_forbidden`） |

唯一判定入口 `src/lvke_mcp/runtime/evidence_qualification.py:85`
`project_fact_may_be_certified()`，三个 AND 条件：证据策略严格等于 `formal_evidence`、
自身资格通过、**每个父级都是 formal 且自身已认证**。合并多父级时保留**最严格**那条。

因此，`formal_release` 不是“只要有 `real` 就允许导出”的快捷开关。`project_delivery` 的正式
资格需要正式证据、项目事实认证和完整对象链；`source_reconstructed` 可以支持
`process_acceptance`，但不能认证项目事实或形成正式客户交付。

### 3.3 口径非法阻断 vs 置信度不足放行

唯一判定入口 `src/lvke_mcp/runtime/quality_severity.py`。这是本分支刚恢复的核心语义：

- **阻断**（基准不可信，继续算只会污染下游）：`project_scale_inconsistent`、
  `controlled_assumption_formal_forbidden`、`reconstruction_records_missing`、
  `preview_cannot_formal_release` 等。
- **质量项**（结果可用但置信度有限，随件披露）：阶段链未走完、证据待补。
  按产品口径，"流程还没走完"是置信度问题，允许产出带完整限制说明的过程验收件。

`success=true` **不等于**正式资格。`partial` / `missing_inputs` / `blocked` /
`incomplete` / `failed` / `upstream_failure` 都不是业务成功。

### 3.4 能力声明的责任边界

本报告把能力分为三层，避免把流程文字误读成系统强制行为：

1. **服务端硬校验**：具体 MCP handler/domain service 实际拒绝的条件，例如正式证据不足、
   受控假设禁止正式发布、尺度口径非法和 stale 依赖。
2. **编排规范**：Skill 为 Agent 规定的调用顺序、停止条件、重试次数和证据披露方式。
   只有被服务端实现的规则才会在所有调用路径上形成硬门禁。
3. **Agent 自律**：正文写作、阶段是否完整调用、checkpoint 是否保存。
   `citation_coverage`、`usable_source_count`、`query_rounds` 由 `dr_submit` **服务端重算**
   后写入，不采信调用方自报；查不到 SourceSnapshot 或 hash 不匹配不计 usable。

因此，“Skills 覆盖某工具”只表示存在机器可读映射或正文提及，不表示工具一定会被调用，
也不表示所有 MCP 入口都会主动检查 Skill 中的流程规则。

---

## 4. 主流程

### 4.1 完整可研（有甲方资料）

```
① 项目立项
   project_context_create → project_context_validate
   → planning_resolve_industry_skill        ← 决定加载哪个行业 Skill

② 资料入库
   source_import_local_path | source_import_content
   | source_upload_begin → _chunk → _commit（>8MiB 走分块）
   → source_task_status → source_parse_retry（失败时）
   → source_inspect_workbook（Excel 公式与跨表依赖）

③ 公网研究（Tavily 是唯一 provider）
   data_provider_status → data_discover / data_search
   → data_audit_urls → data_fetch / data_collect
   → dr_prepare → dr_start → dr_add_sources → dr_submit → dr_confirm_quality

④ 证据固化
   analysis_ingest → analysis_query → analysis_extract_candidates
   → analysis_compare / _normalize_compare / _compare_benchmark / _financial_trends
   → analysis_build_evidence_pack            ← 产出 evp_*，下游一切数字的依据

⑤ 规划（7 类对象，各自 prepare → compare → validate → confirm）
   planning_prepare(market_case) → planning_confirm
   → planning_prepare(policy_basis) / (option_comparison)
   → planning_create(revenue_drivers) / planning_solve_build_scale
   → planning_calculate_cost_drivers → planning_infer_labor_plan
   （辅助：planning_get_industry_constraints / _get_env_templates / _score_option_comparison）

⑥ 财务
   finance_prepare_fact_pack → finance_confirm_fact_pack
   → finance_prepare_spec → finance_validate_spec
   → finance_build_basis_of_estimate → finance_confirm_spec
   → finance_run_model                       ← 产出 run_*，唯一数字源
   → finance_validate_post_generation
   （旁路：finance_build_balance_sheet / _run_monte_carlo / _calculate）

⑦ 十三表（只消费 run_id，绝不重算）
   tables_render → tables_validate(technical → formal)
   → tables_get_table / _validate_table
   → tables_export_csv / tables_export_xlsx

⑧ 报告（正文由 Agent 写，MCP 只做完整性层）
   report_prepare → report_start → report_list_sections
   → report_propose | report_propose_section
   → report_diff → report_apply               ← 三步不可跳
   → report_validate_section → report_validate
   → report_export_docx(draft → formal_candidate)

⑨ 审查与发布
   review_resolve_standards → review_list_requirements
   → review_attach_requirement_evidence → review_validate_standards
   → review_prepare → review_start → review_list_findings
   → review_disposition_finding → review_retest → review_export
   （评分闭环：review_list_rubrics → review_score_section → review_compare_assessments）
   → feasibility_validate(technical → formal) → feasibility_release

⑩ 知识沉淀（可选）
   knowledge_submit_candidate → knowledge_create_snapshot
   → knowledge_review_candidate → knowledge_publish_release
```

全程用 `feasibility_start` / `feasibility_stage` / `feasibility_status` /
`feasibility_next_actions` 记录阶段；`feasibility_checkpoint` / `_resume` 断点续跑。

**12 个阶段**（`servers/lvke_feasibility_delivery/contracts.py:5`）：
`project → research → market → option → scale → drivers → finance_spec →
finance_run → finance_tables → report → review → released`。
上游重开会把下游全部标 `stale`。

### 4.2 零材料估算预览（一句话）

```
delivery_create_from_sentence  ← 从一句话抽行业/地区/规模/工期
→ delivery_list_assumptions    ← 按敏感度返回 5-10 个待确认参数
→ delivery_confirm_assumptions ← 用户确认后建新 AssumptionPackage（不覆盖旧的）
→ delivery_start → delivery_status → delivery_get_artifacts
```

产物是 `estimate_preview`，**永远不能**升级为正式发布。实跑样例
（`lvke产出/test-explicit-precedence/`）：11 个受控假设登记、总投资 119,894.60 万元、
IRR 6.00、13 张表 CSV + XLSX、技术预估 DOCX。

### 4.3 资产收购（酒店 / 光伏）

```
acquisition_validate_spec → acquisition_save_spec → acquisition_confirm_spec
→ acquisition_run_model
→ acquisition_create_scenario_matrix（≤64 组）/ acquisition_solve_max_price
→ acquisition_render_tables → acquisition_export_tables_csv / _xlsx
→ acquisition_generate_artifact（正式资格不足直接拒绝）
```

判别字段是 `finance_kind="asset_acquisition"` + `asset_type`，**不能**用 `invest_type` 代替。
酒店走月度模型（按实际有效天数）、光伏走年度模型（逐年衰减 + 限电率）。

---

## 5. 已实现且有实证的能力

下表描述“已有代码和样本证据”，不等于 G1/G2/G3 全部退出条件已满足，也不等于正式客户交付
已获放行。G1 报告仍是旧 live 基线；G3 可具备**晋升资料候选**（拟定模板按 `sim_a_formal`
附着），但 `release_ready` 仍为 false，不等于正式客户放行。

| 能力 | 实证 |
|---|---|
| 从结构化资料或一句话产出**估算预览**十三表 + 技术稿 | `lvke产出/` 下 3 套 14 CSV + XLSX + DOCX 实物；DOCX 输入解析限制见 §6.2 |
| 确定性财务计算，零 LLM 参与算术 | IRR/XIRR 自研 Newton + 二分回退；不依赖 numpy_financial |
| 十三表只从同一 run 渲染，16 条勾稽等式校验 | `_finance_model/checks.py`；不过则 `consistency_ok=False` |
| 不可变对象链 + 幂等 + lineage + 乐观并发 | 内容寻址 ID、`expected_basis_hash`、stale 传播 |
| 分级证据与两级交付门禁真实拒绝 | G3 三个正式导出全部业务拒绝（非协议错误） |
| 九章报告 propose → diff → apply → DOCX 草稿 | apply 七步顺序校验；formal 导出 fail-closed |
| 审查 10 个规则包 / 39 条唯一规则 + 7 维 rubric | 三重 verdict 分离（技术/发布/总体） |
| 国标只出**适用性**、绝不出合规结论 | 三个工具的 `compliance_conclusion` 硬编码 `not_determined` |
| NDRC 2023 大纲真实入库 | 3 份官方 PDF、118 条条款、带页码与 text_hash |
| 22 个标准方法包真实物料 | 20 passed / 2 incomplete，带文号、source_url、sha256 |
| 173 工具已登记并具备 baseline/live 自省清单 | 14 个 server 合计 173；G1 金标链 25 步中 21 PASS，不能写成“全量通过” |
| 17 个 Skill 的映射检查覆盖 173/173 工具面 | `make skills` 通过；mapping 已去重并用 Counter 守住 |
| 完整 SSRF 防护（采集链） | 见下 |

**SSRF 防护做得比一般实现细致**（`domains/research/url_safety.py`）：

- 拦截四家云元数据端点及其 IPv4-mapped 变体、整个 link-local 段、
  CGNAT `100.64.0.0/10`（Python `is_private` 不覆盖，显式拦）、RFC 2544 段。
- **先 DNS 解析再逐地址判定**（`:252`），域名解析到私网同样拦——不是只看主机名。
- `cloud_metadata` 分类**不受**私网放行开关影响（`:280`）：即使显式设了
  `LVKE_MCP_ALLOW_PRIVATE_URLS`，元数据端点仍拦。
- 重定向逐跳回到循环顶部重新解析校验，有跳数上限，保留完整 `redirect_chain` 供审计
  （`domains/research/extractor.py:898-908`）。
- IP pinning 防 DNS rebinding：解析出的地址被钉住，连接后再验证对端 IP
  （`_request_pinned_once` + `_validate_connected_peer`）。

---

## 6. 现在做不到什么

### 6.1 正式发布仍未闭环

G3（2026-08-28）live MCP：七档都晋升并跑到 FinanceRun；文旅做到审查 JSON，
正式 DOCX / FDR `feasibility_release` 未过；房地产财务后未走完报告/审查。
脚本级游乐园/房地产完整链不能替代本轮 live。未晋升 preview 金标链三探针仍为
`EXPECTED_REJECTION`。`formal_candidate_eligible=true` 只表示 5 项拟定模板已按
`sim_a_formal` 附着，**不是真实原件 EVD-2**。
DOCX 侧是字体/glyph + soffice **转换探测**，不是逐页中文/裁切/表格验收。

`release_ready` **仍为 false**，原因不只是构建元数据：
脏工作树、缺失 `build_time`，以及预检的 artifact/evidence 关口。
preview 链必须继续因未晋升 SIM-A 被拒；晋升链与 preview 现走同一套
`run_release_preflight`（`sim_a_formal` 计 EVD-2，分母=适用项动态数）。
要过发布关必须 clean checkout + `--release` 构建 + 正式工件齐备。

### 6.2 明确的能力缺口

| 缺口 | 状态 | 说明 |
|---|---|---|
| 零材料正式验收 | **晋升新链已通，正式发布未过** | 七档晋升 + FinanceRun；游乐园/房地产完整链脚本绿。零材料 `zmr_*` 仍不原地升级。拟定模板 ≠ 真实原件。`release_ready` 仍为 false |
| DOCX 资料解析 | **已补** | `_parse_bytes` 抽取段落与表格 locator |
| 零材料 DOCX 表格 | **已接** | `|` 行走 `docx.append_markdown_pipe_table` |
| 豁免终态 | **已补** | `approve_waiver` → `waived`；P0 仍不可豁免 |
| professional 规则 | **已入库** | `config/review_rule_sources/{finance-report-core,accounting-tax-core,hotel-mining-core}.json` |
| 月度时间轴 | **已实现（按月循环，无淡旺季）** | `timeline.mode=monthly` 以年驱动数为输入按 `monthly_periods` 循环；期间覆盖值优先，否则年内均分（末月补差）。税与亏损按月滚动，十三表年汇总取自月度。不做淡旺季/工作日日历 |
| 双倍余额递减 / 税会分离 | **已实现** | 逐年 DDB；税务折旧与暂时性差异入利润表；DTA/DTL 入资产负债表。未声明所得税率时递延税为 0，不默认 25% |
| 收购 VAT / 亏损结转 / 15 表 | **已实现（集成测试，不进七档 G3）** | 月度/年度/光伏引擎均滚动资产负债表；`consistency_ok` 按投影勾稽而非 issues 列表；VAT/附加税有税率则非零 |
| `JobRepository` | **已删除** | `runtime/jobs.py` 已移除 |
| FinanceRun / gen_task / readiness / consistency_ok | **已 fail-closed** | 持久化失败不得 `ok=true` 且无 `run_id`；run 存储异常记 `readiness_upstream_unavailable`，不再降级为 warning |
| 房地产 / 墓地档 | **已补** | profile + 墓地 industry 路由 |
| DR 引用覆盖率 | **已实现** | `dr_submit` 固化服务端重算的 coverage/usable/`query_rounds`；`query_rounds` 只计账本 `data_search`/`data_discover`；查不到 SourceSnapshot 或 hash 不匹配不计 usable；publishers/angles 数不了则 `missing_inputs` |
| G3 分母与晋升计数 | **脚本可复述，正式发布未过** | 分母=5。七档晋升到 FinanceRun；完整链只跑游乐园+房地产。soffice 转换探测 ≠ 逐页视觉。`release_ready` 仍为 false |
| 未晋升 preview 正式导出 | **仍拒绝** | `EXPECTED_REJECTION` + `controlled_assumption_formal_forbidden` / `FORMAL_ARTIFACT_QUALIFICATION_REQUIRED` |

上述两项持久化问题意味着：当前可以声明“有 checkpoint/run 存储路径”，但不能无条件声明
“异步任务和审计持久化已达到正式可靠性”。正式路径应把持久化失败作为 blocker；任何未获得
持久化 ID 的预览结果也不得进入正式发布链。

### 6.3 报告校验响应

`report_validate` 现在把真实 blockers 返回给调用方，并在存在财务绑定阻断时
将 `formal_release_eligible` 置为 false。这不等于正式发布已通过。

### 6.4 Skill 层的能力边界与治理落差

Skill 文档是 Agent 的操作规范，不是运行时执行器。它不会自动：

- 调用 MCP 工具或检查是否完成全部阶段；
- 保存或恢复 checkpoint；
- 阻止聊天中编造未固化数字；
- 保证 Review → Retest → Export 闭环；
- 完成 DOCX 逐页视觉验收。

具体治理落差：

1. **`skill_tool_mapping.json` 重复项已删除**，校验器会拒绝重复 skill 条目。
   这不等于每个工具都有 Agent 教程。

2. **173/173 覆盖的真实含义**：校验器认两条通路——mapping JSON 声明 **或** SKILL.md 正文出现。
   部分工具可能只有 mapping、正文提及不足。数字成立，但不等于每个工具都有教程。

3. **本轮已统一仓库 Skills 并同步本机**：`make plugin` 后 15 个父 Skill 已写入
   `~/.codex/skills`；`codex plugin add lvke-mcp@personal` 现为
   `installed, enabled`。Cursor 会话若在安装前启动，仍可能读到旧 Skill，需要新开会话。

### 6.5 DR 质量指标已改为服务端重算

`dr_submit` / `dr_confirm_quality` 写入的 `citation_coverage`、`usable_source_count`、
`query_rounds` 以服务端按 locator/hash 重算为准；Agent 自报不一致记 mismatch。
`query_rounds` 只计账本工作区的 `data_search` / `data_discover`。已知 SOURCE_STORE
快照时，查不到 SourceSnapshot 或 hash 不匹配都不计 usable。`project_delivery` 的 publishers/angles 能从已固化
sources/events 数的就服务端数，数不了记 `missing_inputs`。

补充一处相关设计：`dr_submit` 硬编码 `status="partial"` + `project_fact_certified: False`，
limitations 第一条固定为"正文由调用 Agent 撰写，尚无独立 DR 质量审计"
（`_service/agent_lifecycle.py:445-456`）。Agent 写的研究不能自我认证，必须经
`dr_confirm_quality` 独立环节。这与知识治理域"承认自己没验证 hash 所以永不认证项目事实"
是同一原则。

### 6.6 部署与配置风险

| 风险 | 位置 | 影响与处置 |
|---|---|---|
| **插件配置依赖环境变量** | `plugins/lvke-mcp/.mcp.json` | 已去掉本机绝对 Python/数据路径，改为 `python -m …` 与 `${LVKE_MCP_DATA_DIR}` 等变量。目标机仍须提供这些变量，wheel 构建成功不代表开箱即用。 |
| **本机 Cursor 配置存在凭据风险** | `.cursor/mcp.json` | 配置含 Tavily Bearer 凭据，虽被 `.gitignore` 排除仍属本机暴露面；应立即轮换，并改用环境变量或权限为 `0600` 的凭据文件。本文不记录具体 token。 |
| **工具数人工维护漂移** | 文档面 | 已对齐 **173**；发布说明仍应从 manifest 生成，不能只靠手工数字。 |

---

## 7. 使用与验证

### 7.1 环境

必须用 conda 环境 `lvke-mcp`。base 环境的 pytest import 不到 `src`。

```bash
conda run -n lvke-mcp python -m pytest -q      # 或 make test
make skills                                     # Skill 与真实工具面双向校验
make plugin                                     # 重建 Codex 插件 skills 树
make verify                                     # 提交前全套
```

`tests/conftest.py` 会在 `LVKE_MCP_DATA_DIR` 未设置时指向临时目录，真实数据根
`~/.lvke` 不被污染。刻意**不**钉 `LVKE_DELIVERABLE_DIR`——那会切断交付根联动。

### 7.2 验收分层

| 关口 | 脚本 | 当前状态 |
|---|---|---|
| G1 技术金标链 | `scripts/g1_live_acceptance.py` | 25 步 21 PASS、1 项 `UPSTREAM_FAILURE`、3 项 `EXPECTED_REJECTION`；另有 16 项协议错误和 18 项工具探测 `UPSTREAM_FAILURE` |
| G2 真实资料轨 | `scripts/g2_evidence_acceptance.py` | 28 步 25 PASS、1 项 `UPSTREAM_FAILURE`、2 项 `EXPECTED_REJECTION`；22 条 URL 有审计记录，但“全部可回读快照”退出条件未通过 |
| G3 正式候选 | `scripts/g3_formal_candidate_acceptance.py` | 2026-08-28：七档 FinanceRun PASS；游乐园/房地产完整链脚本绿；soffice 转换探测不是逐页视觉；preview 三探针仍 `EXPECTED_REJECTION`；`release_ready=false`。本轮没有冻结重启后的 live 对话式 G1/G2 |
| 发布预检 | `scripts/release_preflight.py` | 四关口：计算 / 工件 / 证据 / 发布 |

**本地测试全绿不替代 live MCP 对话式验收。** 修改代码后必须冻结、重启一次，
再用真实 MCP 调用验收——当前会话仍跑旧代码。

### 7.3 文档滞后清单

`dev-docs/architecture/MCP_SERVICES.md`（2026-08-07，169 工具口径）的"已知限制"章节：

**已过期**
- 第 4 条「PDF 无内容读取、无 OCR」→ 已有 `pypdf==6.1.1` 主依赖 + `deep-research-ocr`
  extras；`_parse_bytes` 有真实文本层解析，无文本层时诚实报 `needs_ocr`。
- 第 7 条「`domains/review` 是空壳」→ 该目录已删除。
- 第 8 条「`_common/` 是待清理垫片」→ `servers/_common/` 已不存在。
- 第 11 条「两份审查配置不存在导致成套能力空转」→ **归因错误**。`review_standards.lock.json`
  确实不存在，但代码走物料回退（`rules.py:372`），而物料是真实的：22 个 PKG-STD 包、
  20 个 gate passed、每个带官方文号与 sha256。不是"空转"。
  （`review_rule_sources/` 已入库，professional 规则可触发「待专业核验」。）

**已关闭**：第 12 条 `JobRepository` 已删除；第 13 条豁免已有 `approve_waiver → waived` 终态。

---

## 8. 给 Agent 的停止条件

任何一条命中就必须停下报告，不许绕过：

1. `blockers` 非空 → 先解决，不改验收标准掩盖。
2. `missing_inputs` → 补输入，**不顶默认值**。城轨等行业的必填字段没有通用默认值，
   顶上默认值算出的 IRR/DSCR 与该项目无关。
3. `project_scale_inconsistent` → 投资额与行业量级不符，不建 run、不渲表。
4. 证据轨不允许当前用途 → 换轨或补证据，不能把受控假设/夹具标成正式。
5. `FORMAL_ARTIFACT_QUALIFICATION_REQUIRED` → 这是**预期拒绝**，不是失败。
6. Tavily 不可用 → 记 `UPSTREAM_FAILURE`，不回退其他搜索引擎，不把摘要当证据。
7. 同一失败重试两次未过 → 停下说明根因，不循环。

完成态必须写成五档之一，不许缩成"已完成"：

1. 已实现且真实样本通过（唯一可称"完成"）
2. 已实现但真实样本未通过
3. 部分实现
4. 尚未实现
5. 资料不足无法判定

**当前主链的准确表述**：拟定正式轨（`sim_a_formal`）可在新链上跑通 FinanceRun、九章导出、
审查复测导出与 `feasibility_release`（G3 脚本、游乐园/房地产两档）。
未晋升 preview 金标链仍被正确拒绝。**`release_ready` 仍为 false**（脏工作树、缺失
`build_time`，以及 artifact/evidence 关口）。本轮没有冻结重启后的 live 对话式 G1/G2。
