# 绿科 MCP + Skills 能力现状与流程说明

> 审查日期：2026-08-29｜审查基线：分支 `fix/delivery-honesty-and-skill-coverage` 的当前未提交工作树。
> 本轮结论只按实际执行结果更新；代码存在或静态检查通过不单独计为已实现能力。
>
> 事实来源：仓库源码、`tests/fixtures/baseline/`、实跑 pytest 与运行时自省、G1/G2/G3 验收报告。
> 本文的口径以**实测**为准。公开面为 **14 进程 / 180 工具**（七域审查新增 7 个工具；零材料 8→10：
> `delivery_generate_template_pack`、`delivery_confirm_formal_promotion`）。
> `MCP_SERVICES.md` 的"已知限制"章节有历史条目，逐条复核见 §7.3。

本文回答三件事：**做成了什么**、**能做到哪一步**、**按什么顺序跑**。

---

## 1. 一句话定位

绿科当前是**「MCP 确定性引擎 + Skills 编排规范」**，不是带前端的可研工作台。

**本轮结论：锁定计划中的服务端 P0 已实现并通过目标集成测试。** 公开导入和入口参数
不能再自报 `sim_a_formal`；FormalPromotion 已成为 SIM-A 正式链的强制父对象；月度驱动、
月度导出和 DR 确定性片段绑定均有正负测试。2026-08-29 本地完整成功链已真实到达
`Review → Retest → Export → Release`。冻结重启后的 live 全工具验收与本轮最终全量测试
结果见 §7.2 的最终记录，不能由这段结论代替。

MCP 负责**已经被服务端编码并实际执行**的可复算、可追溯和业务拒绝；Agent 负责理解、
取舍和写作；Skills 负责向 Agent 提供调用顺序、责任边界和停止条件。Skills 本身不会自动
调用 MCP、保存 checkpoint、执行 Review → Retest → Export，或替 Agent 证明其上报的指标真实。

产品**不含**：前端、登录、身份、tenant、角色、RBAC、权限管理、安全审查、专业签审、
语音、协同办公。这些不是"还没做"，是明确的产品边界，多个 Skill 用否定式声明固化了它。

### 实测规模

| 项 | 实测值 | 依据 |
|---|---|---|
| MCP 进程 | **14** | `src/lvke_mcp/testing/server_manifest.py` 硬断言，`!= 14` 即 raise |
| 工具 | **180** | 逐个 `build_server()` 自省；baseline 在接口冻结后同步 |
| Resources | **242** | 2026-08-29 对 14 个 stdio 服务逐一执行 `initialize/resources/list` 的 live 结果 |
| Skills | **18**（发布 16，2 个 `dev_only`） | `skills/`；`skill_tool_mapping.json` 的 `dev_only_skills` |
| 业务代码 | domains 61,026 行 / servers 48,420 行 | `rg --files … | xargs wc -l` |
| 测试 | **530 passed、1163 subtests passed** | 2026-08-29 最终全量 pytest；测试绿仍不等于真实客户材料验收 |

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
| 交付 | `lvke-deliverable-review` | 22 | `rvprep_*` / `review_*` / `rvpkg_*` / `rvassess_*` / `rvdos_*` | 七域套件审查、findings 处置、两阶段复测、导出、国标适用性 |
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

一般性本地资格入口是 `runtime/evidence_qualification.py::project_fact_may_be_certified()`；
它支持 `formal_evidence` 与 `sim_a_formal` 的父级认证判断，但**不替代**正式 promotion
校验。SIM-A 正式边界统一调用 `runtime/formal_promotion.py`，重算 TemplatePack、
FormalPromotion、SourceFile、payload、basis、内容和父对象绑定，并比较精确文件集合。

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
delivery_create_from_sentence  ← 抽行业/地区/规模/工期；同时选定并冻结报告配置，
                                 按配置的 required_fields 返回结构化 missing_inputs
→ delivery_list_assumptions    ← 按敏感度返回 5-10 个待确认参数
→ delivery_confirm_assumptions ← 确认或 skip_fields 显式跳过，建新
                                 AssumptionPackage（不覆盖旧的）并自动重算
→ delivery_start               ← 串研究/财务/十三表/报告，并自动执行技术验收
→ delivery_status / delivery_get_artifacts   ← 读 acceptance 三段状态
```

产物是 `estimate_preview`。实跑样例（`lvke产出/test-explicit-precedence/`）：
11 个受控假设登记、总投资 119,894.60 万元、IRR 6.00、13 张表 CSV + XLSX、技术预估 DOCX。

**`feasibility_validation_id` 预览阶段恒空（已确认设计，非漏项）。** 零材料预览链
不创建 `fdr_*`——可研交付运行由晋升后的 `project_context_create` →
`feasibility_start` 建立。预览阶段没有可校验的可研交付对象，因此该字段留空而不
伪造；也刻意不为"让校验看起来跑过"而建一个永不 release 的半成品 `fdr_*`。预览阶段
的覆盖面由 review 七域 `process_acceptance` 加本域五个确定性域保证；feasibility 的
`technical` 校验在晋升后的正式链上真正执行。**读到空值的正确结论是"处于预览阶段"，
不是"校验被跳过"。**

**晋升为显式两步（与原方案的已确认偏差）。** 七域确认齐全后
`formal=eligible`，仍需显式调用 `delivery_confirm_formal_promotion`。原方案写
"自动创建 FormalPromotion"，但晋升会把受控假设产物转成 `sim_a_formal` 正式证据
并落盘 SourceFile，是难以回退的对外动作；且 `responsible_party` /
`confirmation_note` 是必填责任声明，系统代填等于替人签署。门禁强度不变：晋升
入口仍重读两段验收，未通过即 `formal_promotion_acceptance_required`。

**报告配置化。** 正文与章节树来自 `config/report_profiles/`（`manifest.v1.json`
是路由表），Python 只解析、选择、渲染、校验。选择按 industry_code /
project_type / transaction_structure / asset_type / report_type 确定性筛选后取
最高 priority 且**必须唯一**；零命中或同优先级冲突一律阻断，**不套用通用模板**。
调用方可用 `report_profile_id` / `template_set_id` 覆盖。每次运行冻结
`template_set_id`、版本、`content_hash` 与匹配理由，历史运行可重放；配置升级只
影响新运行，旧记录不重渲染。配置的 `required_fields` 必须被某个章节槽位引用，
否则加载期即报 `report_profile_required_field_unused`。

**分级验收。** `acceptance` 三段职责严格分离：

| 段 | 由谁产生 | 说明 |
|---|---|---|
| `technical` | 系统自动 | review 的 `process_acceptance` + 本域组件/hash/谱系检查；按正文结构、研究证据、财务模型、十三表、交付谱系五域出结果。feasibility 的 `technical` 校验只在晋升后的正式链上生效（见下） |
| `internal` | **人工**按七域确认后聚合 | 判据是确认记录真的存在，不是 review 的 `role_confirmed`（后者在 quick profile 下退化成"有 Assessment 即已确认"） |
| `formal` | 资格状态，非动作 | `blocked` / `eligible` / `promoted` / `project_delivery` |

技术验收未通过时内部验收不可能通过；两段都通过才受理
`delivery_confirm_formal_promotion`，否则返回
`formal_promotion_acceptance_required` 并在 `acceptance_blockers` 给出根因。
**关键必填字段未回答**时技术预览照常生成，但 `formal` 阻断并逐条列出
`required_field_unanswered:<字段>`；用户显式跳过的非关键字段只记
`required_field_skipped:<字段>` 作披露，不阻断。
晋升后证据政策转 `sim_a_formal`，但 `evidence_origin=sim_a_template` 必须留在
lineage、manifest 与 `release_limitations` 中。

内部验收走 `review_mode="external"` + `process_acceptance`：`internal` 模式会把
证据轨强制改写成 `sim_a_formal` 并要求 promotion 谱系已存在，而内部验收发生在
Promotion **之前**。因此零材料域不读 review 的 `release_verdict`（它在 external
下恒含 `external_review_release_forbidden`），只读七域结果与确认记录。
零材料套件必然缺 `base_data` 角色，compliance 恒为结构性 incomplete——按限制项
披露，不伪装成完整套件合规。

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

下表描述“已有代码和样本证据”，不等于真实客户材料已经完成专业签审，也不等于仓库构建
预检已经放行。G1/G2 报告仍是旧 live 基线；本轮 G3 隔离工作区目标链已经使用正式
promotion 完成 `Report → Review → Retest → Export → Release`，但拟定模板仍不是客户原件，
仓库级 `release_ready` 也仍受 clean checkout、构建元数据和发布工件关口约束。

| 能力 | 实证 |
|---|---|
| 从结构化资料或一句话产出**估算预览**十三表 + 技术稿 | `lvke产出/` 下 3 套 14 CSV + XLSX + DOCX 实物；DOCX 输入解析限制见 §6.2 |
| 确定性财务计算，零 LLM 参与算术 | IRR/XIRR 自研 Newton + 二分回退；不依赖 numpy_financial |
| 十三表只从同一 run 渲染，16 条勾稽等式校验 | `_finance_model/checks.py`；不过则 `consistency_ok=False` |
| 不可变对象、幂等与正式 lineage | 内容寻址 ID、`expected_basis_hash`、stale 传播；SIM-A 从 FormalPromotion 到 Release 在各正式边界重验，见 §6.1 |
| 分级证据与部分交付门禁真实拒绝 | 受控假设/未晋升 preview 的正式导出会拒绝；公开 import/context/feasibility 不能自报 `sim_a_formal` 资格 |
| 九章报告 propose → diff → apply → DOCX 草稿 | apply 七步顺序校验；formal 导出 fail-closed |
| 审查 10 个规则包 / 39 条唯一规则 + 7 维 rubric | 三重 verdict 分离（技术/发布/总体） |
| 国标只出**适用性**、绝不出合规结论 | 三个工具的 `compliance_conclusion` 硬编码 `not_determined` |
| NDRC 2023 大纲真实入库 | 3 份官方 PDF、118 条条款、带页码与 text_hash |
| 22 个标准方法包真实物料 | 20 passed / 2 incomplete，带文号、source_url、sha256 |
| 180 工具已登记并具备 baseline/live 自省清单 | 14 个 server 合计 180；G1 历史金标链不覆盖本轮新增七域工具，不能写成“全量通过” |
| 18 个 Skill 的映射检查覆盖 180/180 工具面 | 发布 16、dev-only 2；`make skills` 通过；mapping 已去重并用 Counter 守住 |
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

## 6. 已解决问题、限制与风险

### 6.1 正式 promotion 主链

2026-08-29 使用隔离数据根实跑文旅完整链，结果同时满足：`ok=true`、
`finance_run_ok=true`、`tables_ok=true`、`report_export_ok=true`、
`review_retest_export=true`、`release_ok=true`。这证明拟定模板 promotion 测试链在当前代码
下可以完成正式对象闭环；它仍不把拟定模板描述为真实原件，也不替代真实项目材料验收。
DOCX 的 soffice PDF/PNG 仍只是转换探测，不是逐页中文、裁切、表格或分页视觉验收。

### 6.1.1 已解决：公开资格伪造与 FormalPromotion 旁路

公开 `source_import_content` schema 已移除 `evidence_policy`、`evidence_origin`、
`project_fact_certified` 和 `promotion_id`；普通导入固定为非正式候选。私有 promotion-only
路径先验证 TemplatePack 并预览确定性 SourceFile/Promotion identity，导入后复核精确文件
集合、工作区与 hash，只有完全一致才持久化 FormalPromotion。

正式不可变链现在按以下父级重验：

`FormalPromotion → SourceFile → ProjectContext → EvidencePack → FinanceFactPack →
FinanceSpec → BasisOfEstimate → FinanceRun → FinanceTablesPackage → Report → Review →
Retest → Release`。

测试覆盖公开伪造、缺 promotion、父 Spec 篡改、ResearchPackage 历史无签名和完整成功链。
规划域 Market/Option/Scale/Cost/Labor/Revenue 也持久化并重验同一规范 promotion。

历史 `sim_a_formal` 若缺 promotion/basis/父级 hash，统一失败关闭，不自动回填。受控重建
必须创建全新的 TemplatePack、Promotion 和全部下游对象；旧对象保留但不可正式复用。

未晋升 preview 仍必须拒绝正式导出。仓库级 `release_ready` 还受 clean checkout、
`build_time` 和发布工件证据关口约束；业务链 Release 成功不自动等于仓库构建可发布。

### 6.2 明确的能力缺口

| 缺口 | 状态 | 说明 |
|---|---|---|
| 零材料正式验收 | **已实现且目标链通过** | 文旅完整链已到 Report/Review/Retest/Export/Release；零材料 `zmr_*` 不原地升级。拟定模板仍不等于真实原件 |
| DOCX 资料解析 | **已补** | `_parse_bytes` 抽取段落与表格 locator |
| 零材料 DOCX 表格 | **已接** | `|` 行走 `docx.append_markdown_pipe_table` |
| 豁免终态 | **已补** | `approve_waiver` → `waived`；P0 仍不可豁免 |
| professional 规则 | **已入库** | `config/review_rule_sources/{finance-report-core,accounting-tax-core,hotel-mining-core}.json` |
| 月度时间轴与驱动 | **已实现且集成测试通过** | ADR、occupancy、ancillary、payroll、utilities、consumables、maintenance、owner OPEX 支持显式月值、季节性×年度值和旧年度确定性展开；支持运营/工作日日历、年度 reconciliation、月度 P&L/BS 与 CSV/XLSX manifest |
| 双倍余额递减 / 税会分离 | **已实现** | 逐年 DDB；税务折旧与暂时性差异入利润表；DTA/DTL 入资产负债表。未声明所得税率时递延税为 0，不默认 25% |
| 收购 VAT / 亏损结转 / 15 表 | **已实现（集成测试，不进七档 G3）** | 月度/年度/光伏引擎均滚动资产负债表；`consistency_ok` 按投影勾稽而非 issues 列表；VAT/附加税有税率则非零 |
| `JobRepository` | **已删除** | `runtime/jobs.py` 已移除 |
| FinanceRun / gen_task / readiness / consistency_ok | **已 fail-closed** | 持久化失败不得 `ok=true` 且无 `run_id`；run 存储异常记 `readiness_upstream_unavailable`，不再降级为 warning |
| 房地产 / 墓地档 | **已补** | profile + 墓地 industry 路由 |
| DR 引用与片段绑定 | **已实现且正负测试通过** | PDF 页/offset、CSV 单元格/行列、DOCX 段落、文本 offset、web/stored locator 均校验 workspace、整源 hash、实际 fragment 与 fragment hash；语义支持仍留给 Agent/manual Review |
| `sim_a_formal` 服务端资格 | **已实现且负向测试通过** | 公开 import/context/feasibility 不能自报提升；资格由同工作区 FormalPromotion 父对象推导 |
| FormalPromotion 强制血缘 | **已实现且完整链通过** | TemplatePack、Promotion、SourceFile 集合及各下游对象在正式边界重验；混合、篡改、跨工作区、历史无签名失败关闭 |
| `evidence_origin` 全链传递 | **已实现** | SourceFile、Evidence/Research/Planning/Finance/Report/Review/Retest/Export/Release 持久化规范 origin 与 promotion metadata |
| G3 分母与晋升计数 | **目标完整链通过** | 分母=5；本轮文旅隔离工作区完整链 `ok=true`。soffice 转换探测仍不等于逐页视觉，仓库 `release_ready` 另受构建元数据约束 |
| 未晋升 preview 正式导出 | **仍拒绝** | `EXPECTED_REJECTION` + `controlled_assumption_formal_forbidden` / `FORMAL_ARTIFACT_QUALIFICATION_REQUIRED` |

持久化失败在正式路径中是 blocker；任何未获得持久化 ID、缺少规范 promotion 元数据或
无法重算父级 hash 的对象都不得进入正式发布链。这里验证的是本地不可变 JSON 存储语义，
不外推为数据库高可用、跨进程事务或灾备能力。

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

2. **180/180 覆盖的真实含义**：校验器认两条通路——mapping JSON 声明 **或** SKILL.md 正文出现。
   部分工具可能只有 mapping、正文提及不足。数字成立，但不等于每个工具都有教程。

3. **本轮已统一仓库 Skills 并同步插件树**：插件发布 16 个父 Skill，`make skills`
   已验证 canonical `skills/`、插件副本和工具映射一致。插件构建不等于安装；运行中客户端
   是否已加载新 Skill 仍须在目标环境单独确认，旧会话可能需要重启。

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

### 6.6 本轮问题与证据矩阵

| 锁定问题 | 状态 | 服务端/测试证据 |
|---|---|---|
| 公开 SourceFile import 可自报正式资格 | **已关闭** | MCP schema 不再暴露资格字段；Python 兼容参数即使传入 `sim_a_formal/true` 也被忽略并落为 candidate；`test_public_source_import_cannot_forge_formal_qualification` |
| ProjectContext / feasibility 可由调用方提升资格 | **已关闭** | SIM-A context 只接受并重验同工作区 `promotion_id`；feasibility 从 context 推导；无签名伪造测试返回 `formal_promotion_required` / `formal_project_context_required` |
| promotion 文件可缺失、混合、改绑或跨工作区拼接 | **已关闭** | TemplatePack、Promotion 和 SourceFile 精确集合/hash/工作区重验；`test_promotion_rejects_cross_workspace_inexact_and_rebound_file_sets` |
| 下游只看标签，不重验正式父级 | **已关闭** | Evidence/Research/Planning/Finance/Report/Review/Retest/Export/Release 均持久化规范谱系并在正式边界重验；父 FinanceSpec 篡改后 `validate_finance_run` 失败 |
| 历史无签名 `sim_a_formal` 被继续复用 | **已关闭** | 缺 promotion/basis/父级 hash 失败关闭；`test_unsigned_historical_research_package_fails_closed`；不自动回填 |
| 月度财务只有时间轴，没有真实经营驱动/导出 | **已关闭** | 八类经营驱动、运营日历、三档优先级、年度 reconciliation、月度 P&L/BS、17 CSV/17 sheet XLSX 与 manifest；`test_p4_monthly_tax_bs.py`、`test_acquisition_monthly_exports.py` |
| DR locator/hash 不能绑定实际片段 | **已关闭** | PDF、CSV、DOCX、文本、web snapshot、xls/xlsx stored cell locator 均解析实际片段并重算 hash；越界、篡改和跨工作区反例通过 |
| Skills 与插件副本描述旧流程 | **已关闭** | canonical `skills/` 与插件复制内容已同步；`make skills` 严格映射通过，180/180 工具覆盖 |
| 拟定模板是否等于客户原件 | **仍受限，非代码缺陷** | FormalPromotion 证明受控拟定模板链，不证明公章、批复、银行流水、审计/检测结论真实；模板接口保持空值并要求后续替换 |
| DOCX 是否完成逐页视觉验收 | **仍受限** | 字体嵌入、OFL、glyph 和 soffice PDF/PNG 转换探测通过；未完成人工逐页中文可见性、裁切、空白页、表格和分页检查 |
| 外部 Tavily 与真实客户材料是否可用 | **未由本轮本地验收证明** | G1/G2 沿用旧 live 记录；上游不可用仍应记 `UPSTREAM_FAILURE`，不回退摘要或其他 provider 冒充证据 |

### 6.7 研报套件七域审查

本轮在既有 `lvke-deliverable-review` 内新增 7 个工具，将服务面扩展为 22 个工具；没有新增职责重复的 MCP Server。完整架构、流程图、状态机和能力边界见 [RESEARCH_REPORT_REVIEW_ARCHITECTURE.md](RESEARCH_REPORT_REVIEW_ARCHITECTURE.md)。

| 问题 | 当前状态 | 实现与证据 |
|---|---|---|
| 只能生成研报套件，不能审查套件 | **已实现七域审查闭环** | `ReviewPackageDraft/ReviewPackage/ReviewAssessment/ReviewDimensionResult/ReviewDossier`；每域结果独立内容寻址并由 Dossier 绑定；MCP 确定性检查 + 7 个独立 Agent Assessment + 服务端 verdict |
| 外部文件与内部正式对象混为一谈 | **已关闭** | `internal/external` 分流；内审逐件重验同一 FormalPromotion，外审永久 `external_review_release_forbidden` |
| 缺少五类材料仍能宣称完整审查 | **已关闭** | report/source/base-data/model/tables 精确角色门禁；缺任一类只能专项结论 |
| Agent 可提交任意 verdict/check | **已关闭** | 只接受登记的 semantic `check_id`；服务端校验独立 `reviewer_context_id`、coverage、locator/hash，并自行计算 verdict |
| 低置信度 OCR 可直接支撑结论 | **已关闭** | 必须通过 `review_confirm_extraction` 重验 source/locator/fragment；确认提取不等于确认语义 |
| P0/P1 可由说明文字绕过 | **已关闭** | P0 永不豁免；P1 要求范围、影响、补偿措施、责任人、到期日、失效条件、精确证据 |
| 整改说明可直接关闭问题 | **已关闭** | ReviewPackage retest 两阶段；子审查重新提交 Assessment、确认并 finalize 前，父 finding 保持 pending |
| 审查导出缺乏矩阵和审计包 | **已实现** | JSON 完整审计状态；XLSX findings + 七域矩阵 + 标准快照 + audit manifest；DOCX/PDF/locator 定位版 |

仍受限：MCP 不暗中调用模型；语义支持、法规实质适用、逐页视觉和业务合理性依赖独立 Agent/专业人员。XLSM 只静态识别、不执行宏。标准包覆盖不等于覆盖所有国家、湖北、行业和项目特例；无法确认效力或适用性时必须输出 `evidence_incomplete/professional_determination_required`，不得写成合规批准。

### 6.8 部署与配置风险

| 风险 | 位置 | 影响与处置 |
|---|---|---|
| **插件配置依赖环境变量** | `plugins/lvke-mcp/.mcp.json` | 已去掉本机绝对 Python/数据路径，改为 `python -m …` 与 `${LVKE_MCP_DATA_DIR}` 等变量。目标机仍须提供这些变量，wheel 构建成功不代表开箱即用。 |
| **本机 Cursor 配置存在凭据风险** | `.cursor/mcp.json` | 配置含 Tavily Bearer 凭据，虽被 `.gitignore` 排除仍属本机暴露面；应立即轮换，并改用环境变量或权限为 `0600` 的凭据文件。本文不记录具体 token。 |
| **工具数人工维护漂移** | 文档面 | 已对齐 **180**；发布说明仍应从 manifest 生成，不能只靠手工数字。 |

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
| G3 正式候选 | `scripts/g3_formal_candidate_acceptance.py` | 2026-08-29 最终隔离工作区：`ok`、finance run、tables、report export、review/retest/export、release 全部为 true；分母 5/5；soffice 仅为转换探测 |
| 发布预检 | `scripts/release_preflight.py` | 四关口：计算 / 工件 / 证据 / 发布 |

2026-08-29 最终实测记录：

- 全量 `pytest -q`：**530 passed、1163 subtests passed**，耗时 662.66 秒。
- 七域审查与依赖边界目标组：**9 passed**；SIM-A FormalPromotion 正式链：**10 passed**，耗时 268.20 秒。
- `compileall -q src tests scripts`、`git diff --check`、Python API 兼容门禁和依赖边界门禁均通过；无新增 import cycle。
- `make skills` 通过严格 tool mapping 和插件同步检查。
- 14 个 stdio 服务逐一 `initialize/tools/list/resources/list` 成功：**180 tools、242 resources**；全部 `taskSupport=forbidden`。
- live stdio 五文件外审链已完成 `ReviewPackage → Review → 7 Assessment/确认 → ReviewDossier.v2`，总体 `pass`；外审仍明确不可进入 Lvke Release。
- `source_import_content` live schema 不含 `evidence_policy`、`evidence_origin`、`project_fact_certified`、promotion authority、secret 或 token 字段。
- 全新隔离数据根的 promotion 正式链再次到 Release；关键失败关闭选择集 **6 passed、4 subtests passed**，另有跨工作区/不精确集合/改绑专项 **2 passed**。

上述结果证明当前本地实现和已覆盖正式链闭环，不替代真实客户材料、外部 provider、专业签审
或逐页视觉验收。旧的 2026-08-28 测试数字不再作为当前工作树证据。

**本地测试全绿不替代 live MCP 对话式验收。** 修改代码后必须重新启动 stdio 服务，
至少检查 `initialize`、`tools/list` 和代表性正式调用；外部 Tavily 与真实客户材料仍需独立验收。

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
6. promotion 缺失、无签名、混合、篡改、跨工作区或精确文件集合不一致 → 立即失败关闭，
   不按标签或调用方字段降级放行。
7. 历史 `sim_a_formal` 对象缺少可重算谱系 → 不自动回填；按
   `TemplatePack → FormalPromotion → SourceFile → ProjectContext → EvidencePack →
   FinanceFactPack → FinanceSpec → BasisOfEstimate → FinanceRun → FinanceTablesPackage →
   Report → Review → Retest → Release` 创建全新不可变链。
8. DR locator 未知、越界、非唯一，source/hash/workspace 不匹配，或 fragment 被篡改 →
   停止引用固化；不能把语义支持判断伪装成确定性 locator 校验。
9. Tavily 不可用 → 记 `UPSTREAM_FAILURE`，不回退其他搜索引擎，不把摘要当证据。
10. 同一失败重试两次未过 → 停下说明根因，不循环。

完成态必须写成五档之一，不许缩成"已完成"：

1. 已实现且真实样本通过（唯一可称"完成"）
2. 已实现但真实样本未通过
3. 部分实现
4. 尚未实现
5. 资料不足无法判定

**当前主链的准确表述**：隔离工作区测试链已经证明拟定模板经 FormalPromotion 后可完成
`SourceFile → ProjectContext → EvidencePack → FinanceFactPack → FinanceSpec → BoE →
FinanceRun → FinanceTablesPackage → Research/Planning → Report → Review → Retest → Export →
Release`，并且未晋升、无签名、篡改、混合和跨工作区对象失败关闭。它证明本地服务端正式
谱系和业务 Release 闭环，不证明拟定模板是真实客户原件，不替代专业签审、外部数据可用性、
DOCX 逐页视觉验收或仓库构建发布预检。
