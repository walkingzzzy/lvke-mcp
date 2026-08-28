# `package_v14_simulated` 与 MCP/Skills 深度功能审查报告

版本：v1　审查日期：2026-08-19　事实源：当前文件系统、重启后实时 MCP、插件 Skills 清单

## 管理层摘要

本次审查覆盖 `package_v14_simulated` 全部 63 个文件（37 Markdown、2 XLSX、1 DOCX、7 JSON、15 Python），并对工作树源码、`plugins/lvke-mcp/skills`、重启后的 MCP 运行时分别核对。结论不是单一分数，而是四道独立门禁：

| 门禁 | 结论 | 依据 |
|---|---|---|
| 内部技术发布 | **当前不通过，需补齐交付工件并重新复测** | 交付目录声明的 ZIP、Word manifest、治理标准等 5 项文件不存在；包内校验器退出码 1 |
| 技术候选工件 | **有条件：模型可作为技术候选，DOCX 不宜直接候选交付** | XLSX 静态公式检查通过，3,871 个公式无缓存错误；实际 DOCX 存在中文缺字/页脚碎片 |
| 正式证据资格 | **阻断** | P0 矩阵明确 EVD-0 20 项、EVD-1 4 项、EVD-2 0 项；甲方底稿只有包外 hash 登记 |
| 对外使用 | **禁止** | 主报告自身声明不得用于报批、融资、审计或外部事实证明；主体、土地、订单、报价、许可和资金均未完成一手核验 |

最重要的矛盾是“声明状态”和“可回读事实”不一致：`V1.6最终包检查与哈希.json` 写明内部发布通过、99 项 ZIP、37 份 Word 转换和 38 份 DOCX/PDF，但当前目录缺少这些工件。该问题属于 P0 交付完整性问题，不是格式瑕疵。包内 `validate_v16_internal_release.py` 已复现失败：治理标准、Word manifest、内部 ZIP 三项不通过；因此不能沿用 JSON 中的历史“内部发布通过”结论。

财务模型的技术计算相对扎实：37 个工作表、3,871 个公式、无外链、无缓存公式错误，静态检查退出码 0；缓存结果为 FIRR 12.5762%、FNPV 5,711.41 万元、回收期 7.9428 年，四个敏感性情景和 DSCR/三表勾稽均有记录。但这些是 SIM-A/controlled assumption 测算，不是项目可行性或融资可得性证明。主模型为自筹 100%、专项资金 0%、债务 0%；12,000 万元资本金和 8,000 万元债务仅在独立融资压力情景中出现，报告对此隔离是正确做法。

## 事实基线与完整性

登记表以 `00交付目录_V1.6内部技术整改版.md:34-42` 为声明源，以当前文件系统为事实源。声明存在但实际缺失的项目为：

1. `V1.6内部技术整改版_内部发布包_20260819.zip`（S28）；
2. `08可编辑Word版/Word转换清单_V1.6.json`（S32）；
3. `09验收与治理说明/研报AI编制、技术验证与人工验收标准_V1.6.md`（S34）；
4. `08可编辑Word版/09验收与治理说明/研报AI编制、技术验证与人工验收标准_V1.6.docx`（S35）；
5. `V1.6可编辑Word版本_20260819.zip`（S36）。

`V1.6最终包检查与哈希.json:10-60` 仍引用上述缺失文件，并声称 ZIP entry 99、Word conversion 37、DOCX/PDF 38。现存文件的 workbook、主报告、P0 矩阵、SIM-A registry、SIM-A DOCX、甲方登记和复测报告 hash 与 JSON 中对应 hash 一致；Word manifest hash 指向不存在的文件，不能视为可验证 hash。

脚本层仍保留历史路径和本机绝对路径：`05模型脚本与核验/register_client_material_v16.py:24`、`refresh_v16_retest_records.py:21` 指向 `/Users/mac/Downloads/...`；`build_v16_internal_remediation.py:36-41` 使用 V1.5/V1.3 历史源。历史映射本身可用于追溯，但必须与当前状态扫描隔离，并禁止在干净构建中依赖本机路径。

## 主报告、证据与财务审查

主报告包含九章、附录 C-H、P0 映射、57 字段映射、模型结果和替换流程。边界披露是本包的主要优点：报告 `:7, 17-19, 97, 208, 464, 497-522, 1037-1165` 多次明确 SIM-A 不是合同、批复、报价、资金证明、订单、许可证或实测记录，并把低空收入置为 0 或后置。P0 矩阵 `:3-4, 8-31` 明确 EVD-0/EVD-1/EVD-2 状态、责任方、未来材料、模型单元和关闭流程；没有发现 SIM-A 被直接升格为 EVD-2 的文本证据。

仍需整改的审查结论：

- 交付状态 JSON 与当前文件系统冲突，导致复测记录不可作为当前发布证明；
- 主报告引用 22 个 URL。联网抽查结果为 11 个 HTTP 200、2 个重定向、1 个 HTTP 412、8 个超时/000；网络失败只应标记“待人工复核”，不能据此判定引用有效或无效；
- `reference`/`statistics` 服务能返回数据，但 seed 元数据明确为演示近似值，`evidence_eligibility=none`，不能进入正式证据链；
- 风险章节覆盖土地、规划、工艺、设备、环保、食品、文旅和低空等复合风险，属于内容强项，但风险关闭仍依赖 P0 一手资料，不能替代审批或实测文件；
- 实施主体名称、股权、财务、授信、团队与业绩均按拟用名称/SIM-A 处理，包外资料只有 hash 登记，原文不可回读。

## XLSX 深审结论

`02财务模型/咸安区低空经济农文旅融合发展项目经济评价十三张附表（V1.6内部技术整改版）.xlsx`：37 sheets、3,871 formulas、无 external links；`xlsx_static_formula_check_v16.py` 退出码 0。缓存指标如下：

| 指标 | 缓存值 | 资格解释 |
|---|---:|---|
| Base FIRR | 12.5762% | SIM-A 测算，不是已验证项目收益 |
| FNPV | 5,711.41 万元 | SIM-A 测算，不是正式财务结论 |
| 静态回收期 | 7.9428 年 | SIM-A 测算，不是融资承诺 |
| Prudent FIRR | 4.0023% | 压力情景技术结果 |
| Pressure FIRR | N/M | 压力情景不可测，需保留该阻断 |
| Optimistic FIRR | 17.1441% | 乐观情景技术结果 |

主报告、独立复算 JSON 与 workbook 关键结果一致，且 12,000/8,000 万元融资假设没有进入主模型。需要将“技术通过”持续限制为算术、结构和勾稽通过，不得转述为“项目可行”“融资已落实”或“正式证据已具备”。

## DOCX 与交付可读性

实际存在的 `SIM-A拟定材料合集_V1.6.docx` 可被 OOXML 解包并由 LibreOffice 转换为 24 页 PNG/PDF，无空白页；但 `word/fontTable.xml` 仅列出 Symbol、Times New Roman、Cambria、MS 明朝、Calibri、MS Gothic、Courier、Arial，未嵌入字体；styles 使用 `Songti SC`。逐页检查发现中文标题/正文和表头出现 tofu/空方框，页脚反复出现碎片化 `SIM-A`/`V1.6`，版面空白过大。该工件可作为内部结构审阅稿，但不能作为可读的技术候选交付，必须重新生成并做 CJK glyph、授权元数据、页级渲染和人工签认。

## MCP/Skills 能力对比

### 三份基线

| 基线 | 观察结果 | 评价 |
|---|---|---|
| 工作树源码 | `server_manifest.py` 固定 14 个服务；源码包含项目规划、研究、源文件、财务、十三表、报告、审查、治理、交付和恢复域 | 业务覆盖面完整，但当前 tracked worktree dirty |
| 插件 Skills | `plugins/lvke-mcp/skills` 恰为 15 个，与 `src/lvke_mcp/runtime/skill_inventory.json` 15 个完全一致 | 产品清单与插件清单无数量漂移；根目录另有开发用 `lvke-desktop`/`lvke-frontend`，不应算产品 Skill |
| 重启后运行时 | 14 服务、171 工具、28 Resource；所有工具 `taskSupport=forbidden`；171 个工具均未发布 `outputSchema` | 覆盖分母已冻结为实时 `tools/list`；输出契约强度和 metadata 仍不足 |

运行时所有代表性 envelope 都含 `trace_id`、input/content/basis hash 字段、stage、quality/evidence 状态、lineage 和 next_actions；但所有服务返回 `build_metadata_incomplete`，missing `build_time`。原因是 `runtime/build_metadata.py` 在 tracked worktree dirty 时主动清空构建时间；源码中的 `build_metadata.json`（2a70… commit、2026-08-16 build_time）与运行时 commit（46a64…）发生漂移。

### 需求到能力矩阵

| 项目需求域 | MCP/Skill 实现 | 实时/证据审查结论 |
|---|---|---|
| 项目上下文、初始化、行业路由 | `project_context_create/validate/revise`、planning Skill | **可用**；合成链成功，controlled_assumption 被保留并生成 InputApplicability |
| 市场、政策、统计、深研 | data acquisition、deep research、reference、research Skill | **部分可用**；搜索候选不等于证据；当前 Tavily 未配置，22 条外部引用不能自动完成高影响核验 |
| 源文件导入、解析、证据提取 | source-files、data-analysis、source-evidence Skill | **可用但 fail-closed**；source snapshot/hash/locator/evidence pack 设计完整，缺源时不能继续 |
| 市场规模、收入、规模、成本、劳动力、方案比选 | project-planning 全对象工具与 Skill | **接口完整、链路未全验收**；需 confirmed 上游对象和证据 Pack，合成输入不能升级正式资格 |
| FinanceSpec、FinanceRun、IRR/NPV、敏感性、DSCR | finance-model、finance Skill | **技术能力强**；模型确定性、输入确认、post-generation validation 存在；运行时空 workspace 只能按契约阻断 |
| 十三表和 CSV/XLSX | finance-tables、finance-tables Skill | **门禁正确但依赖 run**；formal export 在缺失 run 时返回 `run_unavailable`，未写出路径，符合预期拒绝 |
| 报告 propose → diff → apply → DOCX | report-generation、report Skill | **流程接口完整**；空 workspace readiness/revision 阻断；实际 SIM-A DOCX 字体/渲染不合格 |
| Finding → disposition → retest → export | deliverable-review、review-release Skill | **接口完整、未形成真实对象闭环**；空 workspace `review_not_found`，不能用静态测试冒充验收 |
| 知识候选 → 审核 → snapshot → publish | knowledge-governance、knowledge governance 路由 | **能力存在**；候选状态和 evidence 绑定需以真实工作区复测 |
| 可研交付阶段、checkpoint、release | feasibility-delivery、feasibility-study/deivery-guardrails Skill | **状态机存在**；没有有效上游对象时 `delivery_run_not_found`，当前包发布工件缺失使 release gate 不能通过 |
| 幂等、hash、Resource、lineage、恢复 | runtime + error-recovery/tool-coordination Skills | **数据链设计较强，构建元数据不完整**；旧对象失效/重开需在真实修改后再验收 |
| SIM-A/controlled assumption/formal evidence 隔离 | evidence qualification、finance/source/report/review gates | **核心强项**；FactPack confirm 对空域输入返回 missing_inputs，formal tables 缺 run 返回 expected rejection；仍需以全金标链复测证明无旁路 |

## 实时 MCP 金标轨结论

重启后以实时 `tools/list` 为唯一分母完成覆盖核对。14 个服务的代表性调用均记录为 `PASS`（协议/结构化 envelope）或 `EXPECTED_REJECTION`（业务前置缺失）；没有把业务阻断误判为系统故障。合成 workspace `reviewgolden` 的链路结果：

`project_context_create PASS → project_context_validate PASS → finance_prepare_fact_pack PARTIAL (confirmation required) → finance_confirm_fact_pack MISSING_INPUTS → tables_export_xlsx EXPECTED_REJECTION (run_unavailable)`。

该轨证明了 controlled_assumption 会保留为非正式资格，不能证明真实资料轨已完成。由于本次工作树存在未提交源码改动且没有可重建的完整 FinanceSpec/ResearchPackage/ReportRevision，未将“全链完成”冒充 PASS；剩余节点应在干净构建、受控 provider 和真实工具对象上复测。

## 外部引用核验

报告共 22 个 URL。直接联网可达性结果：11 个 HTTP 200、2 个 HTTP 302、1 个 HTTP 412、8 个超时/000。HTTP 200 只说明 URL 可达，不证明文号、版本、项目适用性和正文一致；302/412/000 均应进入人工复核队列。MCP 当前 data provider status 明确 `TAVILY` 未配置，正式引用核验不能由当前运行时自动完成。

## 发布建议

1. 立即冻结并标记当前状态为“技术候选草稿/正式证据阻断”，撤销历史 JSON 中与当前文件系统不一致的“内部发布通过”。
2. 补齐 S28/S32/S34/S35/S36，重建索引、哈希、ZIP manifest、Word 转换清单和治理标准；在 clean build 中重新运行 validator。
3. 重新生成嵌入合法 CJK 字体的 DOCX，逐页渲染并由人工签认。
4. 在受信 provider 配置后完成 22 条引用审计；所有搜索摘要、代理数据、不可回读 URL 保持非正式资格。
5. 用冻结代码重启一次，完成完整合成金标链和 formal rejection 场景；再执行 Review → Retest → Export。
6. 只有 P0 一手材料完成真实性、项目对应性、有效期、模型映射和责任签认，且报告、模型、十三表、复测 hash 一致后，才评估 EVD-2 或对外使用。

详细证据、工具调用和任务卡见 [findings JSON](./package_v14_simulated_findings_v1.json) 与 [整改路线图](./package_v14_simulated_remediation_roadmap_v1.md)。
