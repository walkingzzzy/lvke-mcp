# `package_v14_simulated` 整改路线图

> 历史路线图：本文中的 171 工具是 2026-08-19 规划快照。当前实时拓扑为 14 server / 180 tools / 242 Resource，工具 `outputSchema` 覆盖率 180/180；历史目标和验收分母不覆盖原始记录。

目标：在不修改原始 `package_v14_simulated` 文件的前提下，修复交付状态、构建可复现性、文档可读性和 MCP/Skills 验收闭环问题。所有任务以 `findings_v1.json` 的 finding_id 为追踪键。

## 0. 立即止损与发布冻结（T+0～1 天）

| 任务 | 责任角色 | 依赖 | 验收命令/门槛 | 完成定义 |
|---|---|---|---|---|
| R-001 对外和内部状态冻结 | PMO/发布负责人 | 无 | `rg -n "内部发布通过|技术通过" package_v14_simulated/07复测报告`；人工确认四门禁状态 | 所有状态页增加“当前文件系统复核于 2026-08-19；缺失工件则 blocked” |
| R-002 建立独立审查 manifest | 交付 QA | R-001 | `sha256sum`/文件存在性扫描；与 findings JSON 对账 | 不在原包内补文件；审查证据只写入 `dev-docs/reports` |
| R-003 clean-build 环境 | 构建工程师 | 无 | `git status --porcelain --untracked-files=no` 必须为空；重启 14 服务 | 每个 envelope 的 commit/build_time/plugin_version 一致，`build_metadata_complete=true` |

## 1. P0 交付完整性（T+1～3 天）

对应 F-001、F-002、F-009。

1. 用 `build_v16_release_zip.py` 重新生成 S28，生成后执行 `unzip -t`、entry count、路径 allowlist 和 SHA-256；ZIP 缺失时不得写入发布状态。
2. 用 `convert_current_md_to_docx_v16.py` 生成 S32 manifest、S34 governance Markdown、S35 governance DOCX 和 S36 Word ZIP。manifest 必须列出实际存在的每个源文件、目标文件、源/目标 hash、转换时间和工具版本。
3. 重写 `validate_v16_internal_release.py` 的 gate 输出：`calculation_gate`、`artifact_gate`、`evidence_gate`、`release_gate` 分离；artifact gate 失败不能把 calculation gate 伪装成“待执行”。
4. 运行 `finalize_v16_internal_release.py` 和 `refresh_v16_retest_records.py`，状态 JSON、复测报告、目录和 hash 必须同一次 clean build 生成。

验收：

```bash
/opt/miniconda3/bin/python3 package_v14_simulated/05模型脚本与核验/validate_v16_internal_release.py
/opt/miniconda3/bin/python3 package_v14_simulated/05模型脚本与核验/xlsx_static_formula_check_v16.py
unzip -t package_v14_simulated/V1.6内部技术整改版_内部发布包_20260819.zip
```

三条命令均成功，且 `find package_v14_simulated -type f` 与目录/manifest 的声明集合完全相等，才可重新评估内部技术发布。

## 2. DOCX 字体与视觉交付（T+1～4 天）

对应 F-004。

- 在 DOCX 导出层采用可再分发且覆盖 CJK 的字体；写入 `fontTable.xml`、字体关系和授权元数据，不依赖本机 Songti SC。
- 逐页渲染 DOCX→PDF→PNG，执行中文 glyph 覆盖、缺字方框、页眉页脚、表格分页、裁切、重叠、空白页扫描。
- 将渲染 contact sheet、逐页检查摘要和人工签认纳入 S34/S35 或独立 QA 目录；视觉通过不改变 EVD-0/EVD-1 状态。

验收命令：

```bash
/Users/mac/.codex/plugins/cache/openai-primary-runtime/documents/26.818.11542/skills/documents/render_docx.py \
  <docx> --output_dir <render_dir> --emit_pdf
/opt/homebrew/bin/soffice --headless --convert-to pdf --outdir <render_dir> <docx>
```

门槛：所有页面可读、中文无 tofu、无溢出/重叠；OOXML 无修订/批注异常；字体授权元数据可追溯。

## 3. 证据与引用轨（T+2～10 天）

对应 F-006、F-007、F-008、F-013。

1. 配置受信 Tavily 或受控 direct_http provider，先执行 `data_provider_status`，确认 search 和 extract 能力分别可用。
2. 对报告 22 个 URL 执行 `data_audit_urls → data_fetch → source snapshot → analysis ingest/query → analysis_build_evidence_pack`。HTTP 200 仅作为采集成功，不直接授予正式资格；302、412、超时和正文不可读统一进入人工复核。
3. 导入甲方原始底稿，生成 `source_file_id`、`source_snapshot_id`、content hash、页码/单元格 locator 和冲突记录；hash-only 登记不得进入 formal binding。
4. 按 P0-01～P0-24 的责任方补齐真实 A 类材料。每项必须完成真实性、项目对应性、有效期、模型映射、责任签认五步，未完成仍维持 EVD-0/EVD-1。
5. 对统计/参考 seed 数据强制显示 `evidence_eligibility=none`；只有可回读官方来源才能创建 formal EvidencePack。

验收：每条高影响引用有 URL audit、原文快照、locator、hash、适用性结论；任何 search summary、proxy data、unreadable URL、SIM-A 或 controlled assumption 均无法调用 formal promotion。

## 4. 财务与十三表分轨（T+3～8 天）

对应 F-008、F-009。

- 保留当前 XLSX 技术结果作为 technical preview，明确主模型自筹 100% 与融资压力情景隔离。
- 对 57 个模型字段建立 `fact_path → source snapshot/evidence_id → workbook cell → report locator` 映射；输入变化时让 FinanceRun、FinanceTablesPackage、ReportRevision 和 Review 自动 stale。
- 依次执行 `finance_prepare_fact_pack → finance_confirm_fact_pack → finance_prepare_spec → finance_confirm_spec → finance_run_model → finance_validate_post_generation → tables_render → tables_validate → tables_export_*`。
- formal scope 下缺任何正式证据、绑定 hash、三表/DSCR/敏感性检查或责任签认，必须返回 expected rejection，且不得生成新的正式路径。

验收：FIRR/FNPV/回收期与独立复算一致；压力情景保留 N/M；任何报告数字都能反向到同一 run/hash/lineage；SIM-A 不能升级 EVD-2。

## 5. 报告、审查和治理闭环（T+5～12 天）

对应 F-005、F-014。

执行顺序固定为：

`report_prepare → report_start → report_status → report_propose → report_diff → report_apply → report_validate → report_export_docx → review_prepare → review_start → review_list_findings → review_disposition_finding → review_retest → review_export`。

要求：

- `report_apply` 前必须存在可回读 diff；
- report、finance tables、review 的 basis hash/run_id 必须一致；
- Review findings 关闭必须绑定新目标版本和复测结果；
- export 只消费通过门禁的不可变对象，旧 revision 自动 stale；
- 生成 DOCX 的可读性门禁与正式证据门禁分开，但任何一项失败都不得标记 external-use eligible。

验收：从一个 synthetic/controlled-assumption workspace 完成全链，所有结果保留 trace_id、resource URI、input/content/basis hash 和 lineage；随后用缺证据/旧 hash 场景验证拒绝。

## 6. MCP API 与 Skills 发布治理（T+5～14 天）

对应 F-003、F-011、F-012。

- 运行时继续以重启后 `tools/list` 作为唯一分母；每次构建自动比较 14 服务、工具数、schema hash、annotations 和 `taskSupport`。
- 为当时 171 个工具发布统一 `outputSchema` 或 envelope.v2 schema URI，使用 PASS/EXPECTED_REJECTION 两类响应做 schema 校验；当前已扩展为 180/180 工具具备 `outputSchema`。
- `scripts/write_skill_inventory.py` 在构建时比较插件目录、inventory、打包清单；根目录 `lvke-desktop`/`lvke-frontend` 显式标记 dev-only，不计入产品 Skills。
- build metadata 只允许 clean build 写入；dirty worktree 必须在启动/发布门禁中阻断，而不是用历史 JSON 状态掩盖。

验收（历史）：插件 Skills 15/15 与 inventory 15/15；14 服务实时启动；171 tools 的 schema/annotations/taskSupport 与 baseline 一致或有显式版本变更说明。当前实时验收分母为 180 tools / 242 Resource。

## 7. 里程碑与退出条件

| 里程碑 | 退出条件 | 可发布范围 |
|---|---|---|
| M0 事实冻结 | findings、manifest、四门禁状态完成 | 仅审查产物 |
| M1 交付修复 | S28/S32/S34/S35/S36 存在且 hash 一致，validator 0 | 可重新评估内部技术发布 |
| M2 模型/文档候选 | XLSX、DOCX 技术/视觉门禁通过，run/hash/lineage 一致 | 技术候选工件，不是正式证据 |
| M3 真实资料闭环 | P0 一手材料、引用快照、责任签认、formal EvidencePack 完成 | 可评估正式证据资格 |
| M4 双轨发布 | Review→Retest→Export 通过；clean build metadata 完整；对外边界签审 | 才可评估对外使用 |

## 复测留存清单

- `tools/list` 原始 JSON、14 服务 initialize 和代表性 tools/call envelope；
- 完整 synthetic chain 每步输入摘要、时间、耗时、状态、trace/resource/hash/lineage；
- validator、xlsx static check、独立财务复算、DOCX OOXML/字体/逐页渲染输出；
- 高影响引用 URL audit、原文快照、locator 和人工复核结论；
- 每条 P0/P1 finding 的处置、责任方、证据、验收测试和 definition of done；
- 最终状态 JSON 与交付目录/文件系统/哈希的同一构建证明。
