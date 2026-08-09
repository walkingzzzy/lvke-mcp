# MCP 与 Skills 多元化实测报告

> 状态：已完成，技术验收不通过
> 测试日期：2026-08-05（Asia/Shanghai）
> 测试方式：当前 Codex 会话内真实 MCP `tools/call`；不以 pytest、本地函数或静态扫描替代
> 范围：24 个项目 MCP、262 个实时工具、59 个非归档 Skills、`docs` 下 51 份 Markdown
> 排除：权限、认证、授权、安全审查、安全门禁、PDF 内容读取

## 1. 实时基线

- 实时服务数：24。
- 实时工具数：262。
- 修订后最低调用目标：2,620（每工具不少于 10 次）。
- 实际最低可审计调用：30,550。前 147 个工具保留原每工具不少于 200 次的结果（29,400 次），后 115 个工具按降载要求每工具不少于 10 次（1,150 次）。
- 联网来源：仅使用现有 Tavily Hikari。
- 结果分类：`PASS`、`EXPECTED_REJECTION`、`UPSTREAM_FAILURE`、`SKIPPED`。
- `SKIPPED` 不计入每工具 10 次完成数。本轮 262 个工具均完成真实调用，无需以静态扫描替代。

## 2. 当前进度

| 批次 | 已完成工具 | 已计数调用 | 错误/阻断摘要 | 状态 |
|---|---:|---:|---:|---|
| 财务纯函数、环境、统计、地理 | 15 | 3,000 | 0 | 完成 |
| 行业研究 | 2 | 400 | 0 | 完成 |
| 客户资料 | 3 | 600 | 0 | 完成 |
| 专家资料 | 3 | 600 | 0 | 完成 |
| 历史档案 | 6 | 1,200 | 0 | 完成，有业务缺陷 |
| 政策资料 | 3 | 600 | 0 | 完成 |
| 模板管理 | 3 | 600 | 0 | 完成 |
| Excel 结构与公式 | 5 | 1,000 | 0 | 完成 |
| 受控附件与解析生命周期 | 15 | 3,000 | 1 次传输超时 | 完成，有部署缺口 |
| 正式资料采集 | 12 | 2,400 | 400 次确定性内部错误 | 完成，有阻断缺陷 |
| 资料分析与 EvidencePack | 13 | 2,600 | 467 次确定性输出/列表错误 | 完成，有阻断缺陷 |
| Deep Research 状态与质量闭环 | 20 | 4,000 | 160 次输出错误且存在写入副作用 | 完成，有 P0 |
| 项目规划 | 47 | 9,400 | 160 次确定性内部错误 | 完成，有部署/契约缺陷 |
| 财务模型 | 19 | 190 | 版本契约不一致；BoE 被真实资料缺口阻断 | 完成，有阻断 |
| 财务十三表 | 23 | 230 | 10 个 package 均为 partial | 完成，有阻断 |
| 报告生成 | 15 | 150 | preparation 被研究包和绑定冲突阻断 | 完成，有阻断 |
| 交付审查 | 17 | 170 | 标准目录无效；复测仍 incomplete | 完成，有阻断 |
| 知识治理 | 8 | 80 | 缺少合格上游 assessment，正确阻断发布 | 完成 |
| 可研交付编排 | 10 | 100 | formal gate 有效；技术阶段校验偏弱 | 完成，有缺陷 |
| 恒立资产收购 | 12 | 120 | 六档候选已建，均无法确认和运行 | 完成，有阻断 |
| 零材料交付 | 11 | 110 | 保持 preview；Resource 列表不一致 | 完成，有缺陷 |
| **累计** | **262 / 262** | **30,550** | **覆盖率 100%** | **完成，验收不通过** |

30,550 是保守计数，不包含为补足唯一对象、最小复现和正常路径而执行的额外调用，也不包含输出宿主关闭后无法审计的批次。降载后所有网络、财务、XLSX 和导出工具串行执行，其他工具最多两路并发。

## 3. 已确认缺陷

### MCP-P1-001：历史档案的结构与模板段落能力未部署完整

- 服务：`lvke-archive`。
- 受影响工具：`extract_structure`、`get_template_paragraph`；`get_chapter` 也仅有测试样例的个别章节可读。
- 复现：对实时 `search_archive` 返回的 10 个正式 `report_id` 调用 `extract_structure`。
- 实际结果：全部返回 `status=blocked`、`code=lvke-archive.index_unavailable`，提示缺少 `metadata.sqlite` 并要求执行 `scripts/build_archive_index.py`。
- 200 次结果：`extract_structure` 0 PASS / 200 EXPECTED_REJECTION；`get_template_paragraph` 0 PASS / 200 EXPECTED_REJECTION；`get_chapter` 18 PASS / 182 EXPECTED_REJECTION。
- 预期结果：实时档案检索返回的正式对象至少应能提取章节结构；可复用段落应能返回带 locator 与 hash 的内容。
- 影响：`meta-precedent-driven-drafting` 所要求的“先查历史档案再起草”无法稳定执行，九章报告的历史案例复用链不完整。
- 当前处理：按计划冻结受影响链路，不修改代码，继续其他独立服务验收。

### MCP-P0-002：正式资料采集未连接到可用的 Tavily Hikari

- 服务：`lvke-data-acquisition`。
- 受影响工具：`data_provider_status`、`data_search`、`data_discover`、`data_fetch`，并向下阻断 `data_collect`。
- 对照证据：同一会话中直接调用 Tavily Hikari 能返回真实政府和文旅来源；Lvke 侧没有使用到该可用 provider。
- 200 次结果：`data_provider_status` 200/200 `internal_error`；`data_search` 200/200 `upstream_failure`，`providerRequested=none`、`providerUsed=unknown`；`data_discover` 在补足有效 schema 调用后 200/200 `upstream_failure`；`data_fetch` 为 100 次 `tavily_extract_unavailable` 和 100 次 direct HTTP 阻断。
- 影响：无法通过正式采集服务创建真实 DiscoverySet，`data_collect` 的 200 次调用只能验证 `discovery_set_not_found` 前置阻断；市场研究、EvidencePack 和 formal lineage 的在线来源主链无法成立。
- 当前处理：冻结该依赖链，不修改实现；使用 Tavily 真实正文经 `data_import_external_snapshot` 导入 160 个不可变 SourceSnapshot，仅用于继续验证不依赖搜索连接的下游能力，不将其冒充 `data_search/data_discover` 成功。

### MCP-P1-003：资料采集 Resource 列表工具确定性内部错误

- 工具：`lvke-data-acquisition.data_list_resources`。
- 复现：在含 160 个 SourceSnapshot、128 个 UrlAudit、160 个 VisualSourceCapture 的真实 workspace 中，分别按资源类型和分页大小调用。
- 结果：200/200 返回 `status=failed`、`code=lvke-data-acquisition.internal_error`；额外单次原始响应复核结果一致。
- 对照：`data_read_resource` 160/160 可读取真实 SourceSnapshot，40 个不存在 URI 正确返回 `resource_not_found`；因此对象存储和单对象读取可用，列表路径单独失效。
- 影响：调用方无法枚举采集对象和过程记录，恢复流程与 Resource 审计不完整。

### MCP-P1-004：本地真实资料导入依赖未配置

- 服务：`lvke-source-files`。
- 工具：`source_external_corpus_resolve`、`source_import_local_path`。
- 结果：两工具各 200 次均返回 `external_corpus_unavailable`；运行时未配置 `LVKE_EXTERNAL_CORPUS_ROOT` 和 `LVKE_SOURCE_IMPORT_ROOTS`，manifest 不可读。
- 影响：崇阳、潜山和恒立原始文件无法通过预定的本地路径 MCP 接口导入。`source_import_content` 可用，但不能把手工内容导入等同于真实本地资料路径验收。

### MCP-P2-005：采集 URL 状态判定在不同工具间不一致

- 对象：同一公开 URL `https://www.gov.cn/`。
- `data_audit_urls(audit_mode=live)`：可返回 `LIVE`，并生成不可变 UrlAudit。
- `data_fetch(provider=direct_http)`：同一运行环境解析到 `198.18.0.10` 后返回 blocked。
- 影响：URL 审计结论不能可靠预测后续正文抓取是否可执行。该项仅记录网络目标分类契约不一致，不属于权限或安全审查测试。

### MCP-P1-006：候选事实无匹配时违反自身输出契约

- 工具：`lvke-data-analysis.analysis_extract_candidates`。
- 输入：本轮真实 AnalysisTask，字段既包含可命中的“非化石能源消费比重”，也包含真实来源中不存在的投资、收入和需求指标。
- 结果：160 个真实任务中 53 次成功产生 CandidateSet；107 次应返回 `missing_fields/partial` 的场景返回 `status=failed`、`code=lvke-data-analysis.invalid_tool_output`。40 个不存在任务 ID 正确阻断。
- 最小复现：对真实任务提取 `definitely_absent_metric/不存在指标/万元`，稳定得到相同 `invalid_tool_output`。
- 影响：资料缺口这一正常业务结果无法结构化传播，EvidencePack、研究质量确认和 formal gate 会收到系统级失败而不是可处理的 missing field。

### MCP-P1-007：真实 XLSX 表格画像违反自身输出契约

- 工具：`lvke-data-analysis.analysis_profile_tabular`。
- 真实输入：甲方“投资类项目经济计算表（产品出售一）.xlsx”，103,185 bytes；通过 SourceFile MCP 导入、解析成功，再由 `analysis_ingest` 建立同 workspace 的真实 AnalysisTask。
- 结果：160/160 正常画像调用全部返回 `status=failed`、`code=lvke-data-analysis.invalid_tool_output`；额外最小复现一致。此前 160 个 Web Snapshot 输入正确返回 `unsupported_input_kind`，40 个不存在任务正确阻断。
- 影响：无法形成真实 workbook 的表头、尺寸、数值/公式统计，甲方模板到 BoE 的表格分析链缺失。

### MCP-P1-008：资料分析 Resource 列表工具确定性内部错误

- 工具：`lvke-data-analysis.analysis_list_resources`。
- 复现 workspace：包含 160 个 AnalysisTask、53 个 CandidateSet、160 个 EvidencePack、200 个 normalized comparison、200 个 trend 和 200 个 benchmark comparison。
- 结果：200/200 返回 `lvke-data-analysis.internal_error`。
- 对照：从 973 个真实 Resource URI 池抽取 160 个执行 `analysis_read_resource`，160/160 成功；40 个不存在 URI 正确返回 `resource_not_found`。
- 影响：对象可以按 URI 读取但不能枚举，任务恢复和结果盘点不完整。

### MCP-P0-009：Deep Research 将来源重建错误提升为正式事实，且质量失败存在写入副作用

- 服务：`lvke-deep-research`。
- 受影响工具：`dr_submit`、`dr_confirm_quality`、`dr_get_bundle`、`dr_read_resource`。
- 输入证据：160 个真实 SourceSnapshot；每条 citation 显式携带 `evidence_policy=source_reconstructed`、真实 `source_uri`、`content_hash` 和 locator，并声明 `原始BoE` unresolved、仅用于 `process_acceptance`。
- `dr_submit` 结果：160 个不可变 ResearchPackage 正确保持工具状态 `partial`，但包顶层被写为 `evidence_policy=formal_evidence`、`project_fact_certified=true`；citation 内仍是 `source_reconstructed`，形成直接自相矛盾。
- `dr_confirm_quality` 结果：160/160 对真实 partial 包返回 `status=failed`、`code=lvke-deep-research.invalid_tool_output`；40 个不存在包正确返回 `research_package_not_found`。
- 非原子副作用：尽管 160 次质量确认均向调用方报告失败，`dr_list_resources` 随后列出 160 个 QualityReview，且 `dr_get_bundle` 返回 74 个 ok、86 个 partial。读取一个 completed 包确认 producer 为 `lvke-deep-research.dr_confirm_quality`，包状态为 `completed`、`quality_review_status=accepted_with_limitations`，同时错误保留 `formal_evidence/project_fact_certified=true`。
- 影响：`source_reconstructed` 可以在调用失败的情况下被提升为 completed 和项目事实已认证，直接违反“只允许 process_acceptance、project_fact_certified=false”的固定业务口径；formal gate 若消费该对象会得到错误资格。
- 定性：业务证据等级、lineage 和状态原子性缺陷，不是权限、安全或身份审查问题。

### MCP-P2-010：Deep Research Resource 列表无分页且单次返回过大

- 工具：`dr_list_resources`。
- 结果：真实 workspace 单次返回 6,720 条 Resource；首个 200 次大并发批次导致调用宿主关闭 stdout，统计作废。改为 5 路小并发后，80 次非空 workspace 和 120 次空 workspace 均成功。
- 资源构成：160 checkpoint、1,920 event、2,880 package/artifact、160 plan proposal、960 plan revision、160 quality review、480 session。
- 影响：随着研究轮次增加，恢复和枚举调用的响应体线性膨胀，容易造成客户端内存或传输失败。

### MCP-P1-011：行业约束与行业 Skill 解析在真实 ProjectContext 上不可用

- 服务：`lvke-project-planning`。
- `planning_get_industry_constraints`：对 160 个已验证 ProjectContext 全部返回 `lvke-project-planning.internal_error`；40 个不存在 ID 正确阻断。
- `planning_resolve_industry_skill`：对同一批 160 个对象全部返回 `industry_skill_manifest_unavailable`；40 个不存在 ID 正确阻断。
- 覆盖项目：制造、文旅、酒店、房地产、公共服务，以及 new build、扩建、改造、收购和运营租赁。
- 影响：`lvke-build-scale` 规定的版本化行业参数前置不可执行；总入口也无法根据 ProjectContext 选择唯一行业 Skill。

### SKILL-P1-012：MarketSizing locator 契约未在实时 schema 或 Skill 中表达

- 对象链：同 workspace 的真实 SourceSnapshot → CandidateSet → 完整 `source_reconstructed` EvidencePack → MarketSizingCase。
- 实际要求：planning 只接受对 EvidencePack locator 对象执行 Python `json.dumps(ensure_ascii=False, sort_keys=True)` 后的完整字符串，包括默认分隔空格。
- 反例：`web:body/paragraph:1`、原始 URL、普通 `JSON.stringify`、无空格的排序 JSON 均返回 `evidence_binding_locator_mismatch`；对应 160 次正常业务候选全部被阻断。
- 对照：改用精确 Python canonical string 后，160/160 MarketSizingCase 创建、比较、校验、确认和回读成功。
- Skill 差距：`lvke-market-sizing` 只要求“locator 与 content hash”，没有说明 locator 对象到字符串的规范；实时 input schema 也只声明 string。
- 影响：调用者无法仅凭 MCP schema 和 Skill 构造可用的真实证据绑定，必须知道服务端序列化实现细节。

### SKILL-P2-013：成本计算字段和 flat review 资格表述与实时行为不一致

- 成本字段：`planning_prepare_cost_drivers` schema 同时提供 `design_capacity` 和 `annual_quantity`；Skill 要求优先提供“数量、单耗、单价”。首轮 200 个成本草稿使用 design capacity 计算电力，prepare 全部成功，但 calculate 200/200 返回 `/operating_cost_items/1 calculation_inputs_required`。改为显式 `annual_quantity` 后 160/160 正常计算。
- flat 收入：Skill 写明 `flat` 进入 review candidate 必须绑定“正式原始资料”；实时候选流程对 40 个 `source_reconstructed` binding 的 `flat + review_candidate` 全部 validate ok。兼容 create 对 40 个完全无 binding 的同类输入正确返回 `flat_revenue_formal_evidence_required`。
- 影响：调用方无法判断 `source_reconstructed` 是否满足 review candidate；`design_capacity` 的实际计算用途也不明确。需要统一 MCP schema、业务校验和 Skill 文案。

## 4. 来源与采集详细结果

| 工具/链路 | 调用结果 | 结论 |
|---|---|---|
| SourceFile 导入、读取、上传、取消、重试 | 每工具不少于 200 次；另以 160 个不同内容对象验证 cancel/retry 均 160/160 成功 | 生命周期主路径可用 |
| `source_list_resources` | 199 成功，1 次约 300 秒传输超时 | 可用但存在长尾稳定性风险，待定点复测 |
| `data_import_external_snapshot` | 160 成功；40 个空正文输入在 schema 层拒绝 | 可固化外部真实正文 |
| `data_audit_urls` | 128 ok、72 partial；p95 27.8 秒，最大 60.8 秒 | live 审计可用但耗时高 |
| `data_get_url_audit` | 160 真实对象成功、40 不存在 ID 正确阻断 | 通过 |
| `data_read_resource` | 160 真实 URI 成功、40 不存在 URI 正确阻断 | 通过 |
| `data_capture_source_view` | 160 真实 PNG + SourceSnapshot 绑定成功、40 缺失快照正确阻断 | 通过 |
| `data_get_visual_capture` | 160 真实对象成功、40 不存在 ID 正确阻断 | 通过 |
| `data_collect` | 160 次业务层 `discovery_set_not_found`，40 次空选择输入拒绝 | 被上游 DiscoverySet 缺失阻断 |

## 5. 初步性能观察

- 基础查询与纯函数工具 p95 约 0.05–0.21 秒。
- `excel-bridge.read_formulas` p95 约 1.73 秒。
- `excel-bridge.dependency_tree` p95 约 1.73 秒。
- `excel-bridge.cross_sheet_refs` p95 约 2.68 秒，最大约 4.01 秒。
- 五类甲方 XLSX（产品出售一/二、厂房出租、墓地售卖、房地产）均能真实读取 sheet、公式和跨表引用；不存在文件按业务阻断返回，未出现协议错误。

## 6. 资料分析详细结果

| 工具/场景 | 结果 | 结论 |
|---|---|---|
| `analysis_ingest` | 160 个同 workspace SourceSnapshot 成功；200 个跨 workspace 引用正确阻断 | workspace 归属约束明确；正常路径可用 |
| `analysis_status` / `analysis_query` | 各 160 个真实任务成功、40 个缺失任务阻断；查询可返回 locator 与 `formal_use_allowed=false` | 通过 |
| `analysis_compare` | 170 ok、30 partial；冲突不被强行合并 | 通过 |
| `analysis_normalize_compare` | 80 ok、120 partial；200 个不可变 comparison 均生成 | 不可比单位保持 partial |
| `analysis_financial_trends` | 40 ok、160 partial；零基期、未知期间和缺基期写入 issues | 诚实输出，未伪造趋势 |
| `analysis_compare_benchmark` | 160 可比成功、40 口径不兼容 partial | 通过 |
| `analysis_list_unit_rules` | 200/200 ok，每次返回 10 条确定性规则 | 通过 |
| `analysis_build_evidence_pack` | 160 个 `source_reconstructed` pack 生成并保持 partial；40 个缺重建记录正确阻断 | 证据等级可传播，缺字段未被隐藏 |
| `analysis_extract_candidates` | 53 ok、107 `invalid_tool_output`、40 缺任务阻断 | 阻断缺陷，见 MCP-P1-006 |
| `analysis_profile_tabular` | 真实 XLSX 正常路径 160/160 `invalid_tool_output` | 阻断缺陷，见 MCP-P1-007 |
| `analysis_list_resources` | 200/200 `internal_error` | 阻断缺陷，见 MCP-P1-008 |

## 7. Deep Research 详细结果

| 工具/链路 | 结果 | 结论 |
|---|---|---|
| `dr_prepare` / `dr_start` | 首轮各 200 次完成；重复业务载荷复用内容寻址 task，另建 160 个显式唯一主题后得到 160 个唯一 task_id | 创建可用；重复 payload 会共享状态对象 |
| `dr_get_plan` / `propose` / `apply` | 160 个唯一任务正常链全部成功；错 basis 与并发 stale 分别返回 `basis_hash_conflict` | 不可变修订和 stale 检测可用 |
| `dr_add_sources` / `dr_remove_sources` | 各 160 正常成功、40 错 basis 阻断；来源带 URI/hash/locator/evidence track | 计划来源修订可用 |
| `dr_list_events` | 137 正常成功、23 非法 cursor 阻断、40 缺任务阻断；返回 549 条结构化事件 | 通过 |
| `dr_create_checkpoint` / `dr_resume` | 各 160 成功、40 错 basis 或篡改 token 阻断 | 恢复链可用 |
| `dr_status` | 160 resumed task 均为 `agent_collecting`，40 缺任务阻断 | 通过 |
| `dr_submit` | 160 个 partial 包生成；40 个空正文/引用在 schema 层拒绝 | partial 语义保持，但证据等级错误见 P0-009 |
| `dr_confirm_quality` | 160/160 `invalid_tool_output`，但服务端产生 QualityReview 和 completed 包 | P0，非原子状态写入 |
| `dr_get_report` / `dr_get_evidence` | 各 160 个 partial 对象可读、40 缺任务阻断 | partial 读取可用 |
| `dr_get_bundle` | 74 ok、86 partial、40 缺任务阻断；160 个包均可定位 | 状态受质量确认副作用污染 |
| `dr_continue` | 160 个 partial 任务续研成功、40 legacy/missing 任务阻断；`quality_thresholds_relaxed` 始终 false | 通过 |
| `dr_cancel` | 160 个续研任务取消成功、40 缺任务阻断 | 通过 |
| `dr_list_resources` | 80 个非空列表和 120 个空列表成功；真实列表 6,720 条 | 可用但缺分页 |
| `dr_read_resource` | 重建 URI 池后 160 真实 Resource 成功、40 缺 URI 阻断 | 通过 |

## 8. 项目规划详细结果

| 工具/链路 | 结果 | 结论 |
|---|---|---|
| `project_context_create` | 200/200 创建草稿 | 不完整对象仍可保存为 draft |
| `project_context_validate` | 160 完整对象 ok、40 不完整对象 `missing_inputs` | 通过，无隐式默认值 |
| `project_context_get/revise/list` | 各 160 正常成功、40 缺 ID/错 hash/坏 cursor 阻断 | revision 与 stale basis 可用 |
| `planning_get_env_templates` | 160 正常成功、40 空污染物集合输入拒绝 | 模板可用，不提升证据资格 |
| 行业约束/Skill 解析 | 160 internal error；160 manifest unavailable | 见 MCP-P1-011 |
| `planning_prepare_market_case` | 初始 200 次含 20 search-summary 草稿、160 locator/track 阻断、20 缺 pack；补充 canonical locator 后 160/160 成功 | 主链可用但契约不可发现 |
| 市场 compare/validate/confirm/get | 各 160 正常成功、40 缺对象或错误选择阻断 | 通过；额外 20 search-summary 草稿在 validate 全部 `market_case_invalid` |
| 方案 prepare/validate/score | 各 160 正常成功、40 权重不闭合或缺对象阻断 | 权重、强制约束和确定性评分可用 |
| 新旧方案确认入口 | 各 160 正常成功、40 非法选择拒绝 | 兼容入口行为一致 |
| 方案回读 | 160 confirmed 对象成功、40 缺对象阻断 | 通过 |
| 建设规模 solve/validate/confirm/get | 各 160 正常成功、40 超市场上限、规划约束或缺对象阻断 | 市场上限、容积率、密度和绿地约束可用 |
| 建设规模兼容 create | 160 正常成功、40 返回 `build_capacity_exceeds_selected_market` | 未绕过市场需求上限 |
| 成本 prepare/calculate | 首轮 200 prepare ok、200 calculate missing input；补充 `annual_quantity` 后 160/160 正常计算 | 字段契约有歧义，计算本身可复算 |
| 成本 validate/confirm/get | 各 160 正常成功、40 缺对象阻断 | 投资和成本 ledger 可用 |
| 成本兼容 create | 160 闭合成功、40 返回 `investment_breakdown_inconsistent` | 通过 |
| 劳动定员 infer/validate/confirm/get | 各 160 正常成功、40 零因子、缺对象阻断 | 工作量、班次、覆盖、工资福利可复算 |
| 劳动定员兼容 create | 160 正常成功、40 零人数输入拒绝 | 通过 |
| 收入 prepare/compare/validate/confirm/get | 产品销售、房地产销售、文旅、政府付费、flat 五模型各覆盖；正常对象均可确认回读 | 模型展开可用；flat 资格冲突见 SKILL-P2-013 |
| 收入兼容 create | 160 preview 成功、40 无证据 flat review 返回 `flat_revenue_formal_evidence_required` | 无证据门禁有效 |
| PolicyBasis prepare/confirm/get | 200 个草稿；160 有效政策确认成功、40 过期政策选择拒绝；回读 160 成功 | 通过 |
| planning Resource list/read | list 160 正常、40 坏 cursor；从 2,880 URI 池读取 160 成功、40 缺 URI 阻断 | 通过 |
| `planning_get_object` | 160 个五类对象成功、40 类型/ID 错配阻断 | 通过 |

## 9. 财务模型与十三表结果

### 9.1 财务模型

- 19 个工具各完成不少于 10 次真实调用。10 个正常 FinanceSpec 均成功确认并生成 FinanceRun，余额表和 Monte Carlo 均可回读。
- 代表性 FinanceRun：`run_0583bd77df56`、`run_dbb403c6a5f4`、`run_67e55bf0507c`、`run_621a2e61018e`、`run_4239aa336522`、`run_906301e917df`、`run_16e0480d4a57`、`run_e7416906b139`、`run_0573e62da457`、`run_a33eac0e7f76`。
- 10 次 BoE 构建均到达业务处理器，并诚实返回 `major_input_basis_missing`、`source_object_not_bound`；没有静默默认值。
- 五个真实甲方 XLSX 模板各复核两次，均建立 vendor reference，但因 sheet mapping 和不可重算公式停在 review。房地产模板另缺 `depreciation_years`。

### 9.2 十三表

- 23 个工具各完成不少于 10 次真实调用；10 个 FinanceTablesPackage 均真实生成，但状态为 `partial`。
- 代表性 package：`ftp_71b0e64686b4c4557d7a71e1`。
- 13 个单表读取工具均 10/10 成功，证明单表渲染和 Resource 回读可用。
- 整包 technical/formal 校验被 `investment_quantity_indicator`、`working_capital_reconciled`、`supporting_schedules_formula_driven` 阻断；formal 还缺有效 BoE/package 证据结构。
- CSV 导出正确阻断。XLSX 导出返回 `success=false/status=partial`，但仍写出可读 Resource，不能作为正式交付工件。

### MCP-P1-014：FinanceSpec 版本在同一服务内不一致

- `finance_prepare_spec` 接受并保存 `version=finance-spec.v3`。
- 同一对象随后被 `finance_validate_spec` 和 `finance_confirm_spec` 以 unsupported version 拒绝。
- 省略 version 后由服务端默认即可继续，说明 prepare、validate、confirm 的契约版本集合不一致。
- 影响：调用方按 prepare 的成功响应持久化对象后，无法可靠完成确认。

### MCP-P1-015：十三表整包无法正式校验，失败导出仍写入工件

- 10 个真实 package 均无法通过 technical/formal 整包校验，不能形成 13 CSV + XLSX 的正式包。
- `tables_export_xlsx` 在业务失败时仍写入 Resource，响应状态与持久化副作用不原子。
- 影响：调用方若只检查 Resource 是否存在，可能误把 partial XLSX 当作正式交付；甲方“同一 FinanceRun 的完整十三表”未满足。

## 10. 报告、审查、知识与总编排结果

### 10.1 报告生成

- 15 个工具各完成不少于 10 次真实调用。
- `report_prepare` 对同时提供 legacy ID 与 typed binding 的输入返回 `ambiguous_finance_binding`，并因缺合格 ResearchPackage 返回 `research_package_required`。
- 后续 start、九章 propose/diff/apply/validate 和 DOCX export 均正确拒绝 blocked 或不存在的上游对象。
- 因此本轮没有生成可宣称完成的崇阳、潜山九章修订版，也没有合格 DOCX 实例。

### 10.2 交付审查

- 17 个工具各完成不少于 10 次真实调用。
- rubric 列表可用；`review_resolve_standards` 10/10 返回 `standard_catalog_invalid`。
- 以真实 FinanceRun 建立的 review preparation 成功，review run 产生两个 blocker finding 和 `PKG-STD-011`。
- finding 读取、列表、处置、导出和 retest 可执行；复测仍为 `overall_verdict=incomplete`。重复复测正确返回 `retest_target_not_newer`。

### 10.3 知识治理

- 8 个工具各完成不少于 10 次真实调用。
- 空 workspace 的列表行为正常；候选提交因不存在的 `rubric_assessment_id` 正确阻断。
- snapshot、review、publish、read 对不存在或未通过候选均正确拒绝，未观察到绕过 reviewed-first 业务规则的发布。

### 10.4 可研交付编排

- 10 个工具各完成不少于 10 次真实调用；start、status、checkpoint、resume、Resource read 均可用。
- `feasibility_next_actions` 返回结构化 `tool/arguments/reason` 和 `missing_inputs`，满足动作契约。
- formal validation 能识别缺阶段、缺对象类型、非法引用、错 run 和未完成状态；未通过 formal validate 时 release 正确阻断。
- `project_delivery + source_reconstructed` 稳定返回 `project_fact_evidence_missing`，符合固定业务口径。

### MCP-P1-016：审查标准目录部署无效

- `review_resolve_standards` 10/10 返回 `standard_catalog_invalid`。
- 影响：审查可以产生 finding 和复测，但无法证明使用了可解析的甲方/行业标准集合，正式 review 无法闭环。

### MCP-P1-017：交付编排跨服务对象解析不一致

- `feasibility_stage` 可把不存在的 ProjectContext URI 或 SourceSnapshot URI登记为 completed project 输出，技术校验仍可成功。
- formal gate 最终能阻断，但同时把其他服务中真实存在的 SourceSnapshot URI报告为 not found。
- 影响：技术阶段会积累无效绑定，且真实跨服务 Resource 不能稳定进入 formal lineage；formal gate 虽未被绕过，但主链不可完成。

## 11. 恒立资产收购与零材料结果

### 11.1 恒立酒店六档

- 12 个工具各完成不少于 10 次真实调用。
- 已按 2000、2200、2400、2600、2800、3000 六档创建独立候选 FinanceSpec；候选校验均成功且明确 `formal_valid=false`。
- 所有确认调用均返回 `SPEC_VALIDATION_FAILED`。未解决项包括经营模式、交易类型、资产范围、购买价分摊、税费、历史报表绑定和交易各方。
- 下游 run、十三表和 artifact 工具分别正确返回 `SPEC_NOT_CONFIRMED`、`RUN_NOT_FOUND`、`TABLE_PACKAGE_NOT_FOUND` 或 `ARTIFACT_NOT_FOUND`。
- 结论：六档价格候选矩阵已验证，但六档独立 FinanceRun、表包、报告和 process-acceptance release 均未形成。

### 11.2 零材料交付

- 11 个工具各完成不少于 10 次真实调用。
- create/start 正确产生 controlled-assumption preview；产品和文旅可路由，墓地和房地产返回 `missing_route`。
- start/confirm 始终保持 partial，并列出 evidence、research、planning blockers；没有进入 formal release。
- cancel/resume 和特定 run 的 artifact listing 可用。

### MCP-P1-018：恒立缺少保留未决事实的流程验收路径

- 当前服务要求先解决经营模式、交易类型、资产边界、PPA、税费等项目事实，才能确认 spec 和运行六档模型。
- 固定验收口径要求这些事项保持 `unresolved_inputs`、`business_decision_status=not_selected`，同时允许 `source_reconstructed/process_acceptance` 验证技术对象链。
- 影响：真实资料边界与现行确认契约互相锁死，无法完成恒立六档 process acceptance。

### MCP-P2-019：零材料 Resource 列表与单对象读取不一致

- `delivery_list_resources` 对已知 intent/run 的各资源类型均返回空列表。
- 同一批已知 intent/run URI 可直接读取。
- 影响：恢复和盘点无法通过列表发现已有 preview 对象。

## 12. Skills 核验

- 使用 `find ... -name SKILL.md -not -path '*/_archive/*'` 得到 59 个非归档 Skill。
- 59/59 均可解析 YAML frontmatter，均有非空 `name` 和 `description`，名称无重复；35 个文件使用 CRLF，仅属文本格式差异。
- Skills 是调用说明，不是独立可调用工具。本轮没有虚构“Skill 运行次数”，而是将每个 Skill 的正常、阻断、恢复要求映射到对应 MCP 的真实调用：规划对象正常创建与确认、错误父对象/状态阻断、Deep Research checkpoint/resume、报告 blocked flow、review retest、编排 checkpoint/resume。
- 总入口 `lvke-feasibility-study` 的顺序与业务目标一致，但在真实运行中被 Tavily 接入、Deep Research 证据升级、十三表整包、报告 ResearchPackage、审查标准目录和跨服务 resolver 依次阻断。
- 九个章节 Skill 的结构存在，但没有合格 ReportPreparation，不能把结构存在或标题扫描算作九章报告验收。

已确认的 Skill 契约问题除 `SKILL-P1-012`、`SKILL-P2-013` 外还有：

### SKILL-P1-020：财务与总入口 Skill 描述的正式链在当前部署中不可执行

- `lvke-finance-tables` 要求 technical 通过后导出 XLSX、formal 通过后导出 13 CSV；实时 10 个 package 均在固定语义项阻断。
- `lvke-feasibility-study` 要求 quality-confirmed ResearchPackage、完整表包、九章 revision、closed review 后 release；实时依赖链分别在 P0-002、P0-009、P1-015、P1-016、P1-017 阻断。
- 这是 Skill 目标与部署能力的可执行性差距，不表示应降低 Skill 的正式口径。

## 13. 甲方需求覆盖矩阵

| 甲方目标 | 实测证据 | 状态 |
|---|---|---|
| 市场规模、区域容量、供需缺口到建设规模 | canonical locator 下 MarketSizingCase 160/160 可创建、比较、校验、确认；建设规模上限有效 | 部分满足，locator 契约不可发现，在线研究上游失败 |
| 研究计划、暂停、恢复、来源增删、质量确认 | plan revision、add/remove、checkpoint/resume 可用 | 不满足；质量确认返回失败却写入 completed，并错误认证重建事实 |
| 真实公开来源与 Tavily Hikari | Tavily 本身可用，Lvke `data_search/discover/fetch` 不可用 | 不满足 |
| 来源 locator/hash/EvidencePack/BoE/report lineage | SourceSnapshot、EvidencePack 和 planning binding 可验证 | 部分满足；BoE、跨服务 resolver 和 report binding 未闭环 |
| 方案比选先于规模、投资和财务 | option prepare/score/validate/confirm 与 scale 父子链可用 | 基础满足；总交付 formal 对象链未完成 |
| 原料、能源、环保、人工进入成本 | CostDriver、LaborPlan 可复算；真实模板可读取公式 | 部分满足；`annual_quantity` 契约歧义，vendor recalculation 被阻断 |
| 产品销售、厂房出租、墓地、房地产、文旅、非经营性分流 | 规划收入模型和五类真实 XLSX 均覆盖 | 部分满足；模板正式接模、墓地/房地产零材料 route 未完成 |
| 建设期利息、贷款、流动资金、税费、IRR/NPV、DSCR/ICR | 10 个 FinanceRun、余额表和 Monte Carlo 成功 | 技术计算可用；正式 BoE 输入资格未满足 |
| 同一 FinanceRun 的十三表 | 13 个单表均可读取 | 不满足；整包 technical/formal 均失败，13 CSV 未交付 |
| 崇阳、潜山完整九章修订报告 | report 工具均已实调并正确阻断无效上游 | 不满足；无真实九章 revision 和合格 DOCX |
| finding 整改与复测 | finding 读取、处置、retest 可用 | 部分满足；标准目录无效，最终仍 incomplete |
| 恒立六档收购价与独立 lineage | 六档独立候选 spec 已创建 | 不满足；无 confirmed spec、run、表包、报告或 release |
| `source_reconstructed/process_acceptance` formal release | formal gate 能识别证据范围和缺对象 | 不满足；没有真实对象链 release 实例 |
| `project_delivery` 缺原始 BoE 时阻断 | 返回 `project_fact_evidence_missing` | 满足 |
| 零材料仅预览 | controlled assumption 始终 partial/preview | 满足 |
| 知识审核后发布 | 缺合格 assessment/candidate 时 publish 正确阻断 | 门禁行为满足；无合格知识 release 实例 |

## 14. 综合缺陷与优先级

| 优先级 | 数量 | 代表问题 |
|---|---:|---|
| P0 | 2 | Tavily 正式采集链不可用；Deep Research 错误提升证据等级且失败写入状态 |
| P1 | 14 | Resource 列表、XLSX profile、候选输出、行业约束、FinanceSpec 版本、十三表、标准目录、跨服务 resolver、恒立流程等 |
| P2 | 4 | URL 判定差异、超大研究列表、成本字段/flat 资格表述、零材料列表发现 |

建议修复顺序：

1. 先修复 P0-009 的证据等级与事务原子性，防止错误事实资格进入任何下游。
2. 接通 `lvke-data-acquisition` 到现有 Tavily Hikari，并修复 acquisition/analysis Resource 列表与候选输出。
3. 统一跨服务对象 resolver、FinanceSpec 版本和 BoE 证据绑定。
4. 修复十三表三项语义校验和失败导出副作用，再打通 ReportPreparation。
5. 修复标准目录，完成九章 propose/diff/apply/validate、finding disposition 和 retest。
6. 为恒立增加保留未决事实的 `source_reconstructed/process_acceptance` 路径，不替项目选择经营模式、交易结构或收购价。

## 15. 最终结论

**状态**: 历史文档（Wave 2 拓扑） — 本轮测试针对当时的 24 服务、262 工具。Wave 4 后为 14 服务、169 工具。

本轮已完成 24 个 MCP 服务、262 个实时工具的真实对话式调用（Wave 2 拓扑），262/262 均达到修订后的每工具不少于 10 次要求；最低可审计调用为 30,550 次。59 个非归档 Skills 的结构核验完成，并与实时 MCP 正常、阻断和恢复场景进行了契约对照。

**技术验收不通过。** 当前仍有本地 P0/P1、主链阻断、十三表整包失败、报告链未生成、恒立六档未运行以及 formal process release 实例缺失。不能宣称两份报告、恒立六档、完整九章、正式十三表或真实 formal release 已完成。

本轮未测试或新增权限、认证、授权、安全审查、安全门禁；未新增联网搜索 MCP；未用 pytest、测试脚本、本地函数或静态扫描替代真实 MCP 调用。因存在 P0/P1 且验收未通过，不创建“全部测试通过”Git commit，也不 push。
