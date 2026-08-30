# G1 技术金标链验收报告

- **生成时间（UTC）**：2026-08-28T12:45:00Z
- **声明**：本轮为**实时 MCP 对话式验收**（脏工作树 / process）。历史 G1（171 工具，2026-08-21）作废。
- **拓扑口径**：本轮记录的 173 工具是 2026-08-28 验收快照，不代表当前运行时；当前实时拓扑为 14 server / 180 tools / 242 Resource，`outputSchema` 覆盖率 180/180。
- **工作区**：`live-acc-20260828`
- **live_listed**：14 个 `user-lvke-*` ready；Cursor 另注入 `mcp_auth`（不计产品分母）
- **产品分母**：14 服务 / **173** 工具
- **taskSupport**：listed 工具均为 forbidden / 无 MCP Tasks 扩展
- **build_metadata_complete**：false（缺 `build_time`）
- **release_ready**：false（脏树，禁止写成正式发布完成）
- **口径**：dev/process 验收，不是正式发布验收

## 工具覆盖分类（产品 173）

| 分类 | 数量 |
|------|------|
| PASS | 118 |
| EXPECTED_REJECTION | 55 |
| UPSTREAM_FAILURE | 0 |
| SKIPPED | 0 |

说明：173/173 均至少 live 调用一次。上表按本会话 envelope 校正：收购正式工件/缺 artifact/不完整十三表导出、`review_retest`（目标未更新）、`report_export_docx(formal_candidate)`、`feasibility_release(project_delivery)` 记为 `EXPECTED_REJECTION`，不以 remainder 汇总里的 PASS 覆盖这些门禁。

`mcp_auth`（14 个 lvke）：本轮实际返回已认证，**不计入 173**。`user-tavily-hikari` namespace error/timeout，单独记账，不进 173。

## 金标主链

| 步骤 | 工具 | 分类 | status | object_id | trace_id | 备注 |
|------|------|------|--------|-----------|----------|------|
| ProjectContext | `project_context_create` | PASS | ok | pctx_5ea6492152812bc25b037758 | `mcp_70acb48c8365467d95bab0dfbd1236da` | build_metadata_complete=false |
| SourceSnapshot | `source_import_content` | PASS | ok | src_c0245c7ae10e8b5d3f84ef48 | `mcp_23f6a77186f5468db606676b8c25fc66` | sha256=c0245c7a… |
| DiscoverySet | `data_discover` | PASS | ok | discovery_e4453ed81b17c44786c57f50 | `mcp_4a6075da755f4ae390e386e58c250c00` | 候选 0，合法空结果 |
| CandidateSet | `analysis_ingest` | PASS | ok | analysis_77c7b6e5f7fe81cba134bdf1 | `mcp_0be5220dee7e4586b29c066ced760173` | — |
| DeliveryRun | `feasibility_start` | PASS | ok | fdr_e6d3545578a879e6e558e77a | `mcp_cdfa33d7fff84f2eadb563d6ca5d1011` | preview / controlled_assumption |
| IndustryConstraints | `planning_get_industry_constraints` | PASS | ok | tourism_catering | `mcp_fe606daf7362495aae23c98fba07c3d0` | — |
| ResearchBrief | `dr_prepare` | PASS | ok | — | `mcp_e7ef41d232724ae5a1c23a125c185806` | — |
| FinanceSpec | `finance_prepare_spec` | PASS | partial | fsp_f40fa6606b2fdaed41edebcf | `mcp_82b38219f7b54179a5445df60fb45264` | 缺投资/收入 |
| EvidencePack-recon | `analysis_build_evidence_pack` | EXPECTED_REJECTION | blocked | — | `mcp_f335c0c886ac4fa2a2ff124c32ff618b` | source_reconstruction_invalid |
| EvidencePack | `analysis_build_evidence_pack` | PASS | ok | evp_8543f6ff8801fb209a2c5aae | `mcp_507fcebab89e41789482b3b2b9f54a17` | controlled_assumption |
| FinanceSpecConfirmed | `finance_confirm_spec` | PASS | partial | fsp_880ea4b9c1b568e52f43f10e | `mcp_376fab9eb9fe4c8188d87f5f95be1fda` | — |
| FinanceRun | `finance_run_model` | PASS | ok | run_d3f3548a60b4 | `mcp_c1bee367fc894abfa073eee7478ecfb9` | consistency_ok=true |
| FinanceTablesPackage | `tables_render` | PASS | partial | ftp_f903b9df8fe94f04e4893ad6 | `mcp_f9a517fa1247407fade40ebd3d88da71` | 13 表 |
| TablesExportCsv | `tables_export_csv` | PASS | partial | ftp_f903b9df8fe94f04e4893ad6 | `mcp_b5bbff912ae74824a66cfe5df27e046c` | technical；csv_integrity 13/13 |
| TablesValidate | `tables_validate` | PASS | partial | run_d3f3548a60b4 | `mcp_ce8b1ac0fd554a0083ecaca104ac1d41` | technical |
| ReviewPrepare | `review_prepare` | PASS | ok | rvprep_d677298b93b32f743d6ae957 | `mcp_e19c2e1d5f7847ebaa13b71d29877ea3` | process_acceptance |
| Review | `review_start` | PASS | partial | review_b23bcb2bda1ce9614d4bad3ab7d0efd7 | `mcp_c23f02ba24d840608b733a611f6d6c59` | verdict=fail；P1 阻断 findings |
| ReviewFindings | `review_list_findings` | PASS | ok | 4 findings | `mcp_29f69db7dd1840cfb7c9a7dfa380f56c` | 元数据 / 流动资金 |
| ReportPreparation | `report_prepare` | PASS | partial | rprep_bad2c9c588d2be77deea4d2b | `mcp_fa85e777e357443a9f4b1b14e649e400` | research_package_required |
| ReportRevisionDraft | `report_start` | PASS | ok | rrv_96ed6993d5eaeb6b23604b0f | `mcp_28c4dd090905471e8245a44570535a0f` | — |
| ReportRevision | `report_status` | PASS | ok | rrv_e1fae9610a1296a535e222d6 | `mcp_929ad701e76744e286899b0ab50dae48` | — |
| ReportPropose | `report_propose` | PASS | ok | prop_b0afc82c5b655c25 | `mcp_0b05cb6263554eed8b4d6b608c5a2476` | — |
| ReportDiff | `report_diff` | PASS | ok | prop_b0afc82c5b655c25 | `mcp_c46c858979fe47058f147e3a3dd6cf9a` | — |
| ReportApply | `report_apply` | PASS | ok | rrv_54024237cf2af99189709af8 | `mcp_a88aae0232ff46768eed21e2484d5bf8` | enforce_structure=false |
| ReviewRetest | `review_retest` | EXPECTED_REJECTION | blocked | — | `mcp_6bd43715e23148e983964fcfbdff956b` | retest_target_not_newer |
| ReviewExport-json | `review_export` | PASS | ok | rvexp_315f4d7f7fe4603659dc3e0c | `mcp_accdc0b3ba634f348f64088ba5b58609` | 过程 JSON |
| FeasibilityValidate | `feasibility_validate` | PASS | partial | fdr_e6d3545578a879e6e558e77a | `mcp_f7f875f9fb854fd0ba2219a1f70e1a5c` | technical passed；质量未过 |

### Preview 正式三探针（亦作 G3 preview）

| 工具 | 分类 | status | code | trace_id |
|------|------|--------|------|----------|
| `report_export_docx` kind=formal_candidate | EXPECTED_REJECTION | blocked | FORMAL_ARTIFACT_QUALIFICATION_REQUIRED | `mcp_74e836c72a234832b3f2d6e12cf0e041` |
| `review_export` formats=docx | EXPECTED_REJECTION | blocked | FORMAL_ARTIFACT_QUALIFICATION_REQUIRED | `mcp_2195179cc558448f9d7b9e249eba1c8b` |
| `feasibility_release` project_delivery | EXPECTED_REJECTION | blocked | controlled_assumption_formal_forbidden | `mcp_4007bc83517d4eaba9631bcd204b54d3` |

### 收购双轨

| 步骤 | 工具 | 分类 | code / 备注 | trace_id |
|------|------|------|-------------|----------|
| Validate | `acquisition_validate_spec` | PASS | preview_eligible；formal_valid=false | `mcp_d43e5c3d910849d28cbcf8bef822a226` |
| Save/Confirm/Run | `acquisition_*` | PASS | acqrun_24802e215de244938ff9a237d05eb242；consistency_ok=false | `mcp_edef17985a8f42169571972e75d1b3c0` |
| Formal artifact | `acquisition_generate_artifact` | EXPECTED_REJECTION | FORMAL_ARTIFACT_QUALIFICATION_REQUIRED | `mcp_c30c3965456b4210a6128849d6f65a6f` |
| Tables | `acquisition_render_tables` | PASS | 15 表；BS 缺必填字段；formal_usable=false | `mcp_b20037434d74424bb7c9cc5f789c0825` |
| Restricted report | `report_prepare` kind=asset_acquisition | PASS | partial；acquisition_tables_integrity_failed | `mcp_5722503a61014d72985edf880553df69` |

十三表核验（通用可研 `run_d3f3548a60b4`）：同一 run_id；CSV 13 张 hash 齐；`csv_integrity.valid=true`。`validation_complete=false`（estimate_preview）。未做 soffice 逐页视觉验收。

## 主链阻断点

- 审查 P1 阻断：`PROJECT.METADATA.COMPLETE`、`FIN.WORKING_CAPITAL.DRIVER`（专业核验 findings 另计）
- 报告正式导出 / FDR 项目交付 / 收购正式工件：资格门禁拒绝（预期）
- 收购资产负债表缺 `cash_wan` 等必填列；`consistency_ok=false` 不得当业务成功
- `build_metadata_complete=false`

## 15 个父 Skill 路由（本轮至少一条）

| Skill | 覆盖 | 结果 |
|------|------|------|
| lvke-mcp-acceptance | 分母 173、四分类、envelope、脏树 | 遵守；非正式发布 |
| lvke-delivery-guardrails | preview 三探针拒绝；未伪造签章文号 | EXPECTED_REJECTION |
| lvke-feasibility-study | start/status/validate；release 分轨 | 技术 validate partial；正式 release 拒 |
| lvke-finance | spec→run→tables；未改公式凑绿 | consistency_ok=true（通用）；审查仍 fail |
| lvke-project-planning | context + tourism_catering 约束 | PASS |
| lvke-urban-rail-transit | `planning_get_industry_constraints` | missing_inputs（8 个城轨字段）；EXPECTED_REJECTION |
| lvke-report | propose→diff→apply；正式 DOCX 拒 | 过程 PASS；正式 EXPECTED_REJECTION |
| lvke-research | dr_* 启动/提交；假 snapshot 不计 | 质量确认 research_quality_failed |
| lvke-source-evidence | import + hash；hash-only / 重建轨拒 | G1/G2 重建 pack 拒 |
| lvke-review-release | resolve/list/attach/validate | attach 错 hash 拒；conclusion=not_determined |
| lvke-tool-coordination | 零材料 10 工具 + 晋升 next_actions | 未对 zmr 做 feasibility_release |
| lvke-local-verify | 只记 live，不替 G1 | 本报告 |
| lvke-api-contract | envelope / 缺参 -32602 | finance_validate_spec 等 |
| lvke-backend | 幂等 replay / conflict | 零材料同键不同 payload → idempotency_conflict |
| lvke-error-recovery | blocker 诚实 | 门禁 code 原样记录 |

交叉 Skill 用主链契约/envelope/错误恢复记账，无独立教程级覆盖。

## G1 退出条件核对

- [x] 173 工具实时调用（历史快照；live_listed vs 产品 173 已分栏）
- [x] 金标链走到表 / 报告 / 审查 / 过程导出
- [x] 十三表 CSV 13/13 hash + lineage
- [x] 收购正式工件 EXPECTED_REJECTION + 受限 report_prepare
- [ ] 技术验收通过：否（本地 P1 findings、收购 BS 缺字段、validation_complete=false）
- [ ] `build_metadata_complete=true`
- [ ] `release_ready=true`

## 结论

在本轮实时工具、场景和边界覆盖范围内，未发现阻断性的本地 P0。存在本地 P1（审查元数据/流动资金、收购资产负债表缺列）。技术金标链、真实资料资格、正式候选和 release 条件分别判定，不相互替代。

本轮是 **dev/process 验收**。禁止写成「全部成功」或「正式发布验收完成」。
