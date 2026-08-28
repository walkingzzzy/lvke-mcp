# G1 技术金标链验收报告

- **生成时间（UTC）**：2026-08-21T02:13:55Z
- **工作区**：`g1-golden-20260821`
- **工具分母**：171（预期 171，14 服务 live `tools/list`）
- **金标链步骤数**：25
- **build_metadata_complete**：False

## 工具覆盖分类

| 分类 | 数量 |
|------|------|
| PASS | 29 |
| EXPECTED_REJECTION | 124 |
| UPSTREAM_FAILURE | 18 |

## 金标链步骤分类

| 分类 | 数量 |
|------|------|
| PASS | 21 |
| EXPECTED_REJECTION | 3 |
| UPSTREAM_FAILURE | 1 |

## 探测异常明细

- UPSTREAM_FAILURE: `lvke-asset-acquisition.acquisition_validate_spec`
- UPSTREAM_FAILURE: `lvke-asset-acquisition.acquisition_save_spec`
- UPSTREAM_FAILURE: `lvke-data-acquisition.data_discover`
- UPSTREAM_FAILURE: `lvke-data-acquisition.data_search`
- UPSTREAM_FAILURE: `lvke-deep-research.dr_add_sources`
- UPSTREAM_FAILURE: `lvke-deep-research.dr_resume`
- UPSTREAM_FAILURE: `lvke-deliverable-review.review_disposition_finding`
- UPSTREAM_FAILURE: `lvke-deliverable-review.review_retest`
- UPSTREAM_FAILURE: `lvke-deliverable-review.review_attach_requirement_evidence`
- UPSTREAM_FAILURE: `lvke-finance-model.finance_build_basis_of_estimate`
- UPSTREAM_FAILURE: `lvke-finance-model.finance_read_analysis_resource`
- UPSTREAM_FAILURE: `lvke-knowledge-governance.knowledge_submit_candidate`
- UPSTREAM_FAILURE: `lvke-project-planning.planning_confirm`
- UPSTREAM_FAILURE: `lvke-project-planning.planning_prepare`
- UPSTREAM_FAILURE: `lvke-project-planning.planning_create`
- UPSTREAM_FAILURE: `lvke-reference.reference_get`
- UPSTREAM_FAILURE: `lvke-source-files.source_upload_begin`
- UPSTREAM_FAILURE: `lvke-zero-material-delivery.delivery_transition`
- PROTOCOL_ERROR: `lvke-asset-acquisition.acquisition_validate_spec`
- PROTOCOL_ERROR: `lvke-asset-acquisition.acquisition_save_spec`
- PROTOCOL_ERROR: `lvke-deep-research.dr_add_sources`
- PROTOCOL_ERROR: `lvke-deep-research.dr_resume`
- PROTOCOL_ERROR: `lvke-deliverable-review.review_disposition_finding`
- PROTOCOL_ERROR: `lvke-deliverable-review.review_retest`
- PROTOCOL_ERROR: `lvke-deliverable-review.review_attach_requirement_evidence`
- PROTOCOL_ERROR: `lvke-finance-model.finance_build_basis_of_estimate`
- PROTOCOL_ERROR: `lvke-finance-model.finance_read_analysis_resource`
- PROTOCOL_ERROR: `lvke-knowledge-governance.knowledge_submit_candidate`
- PROTOCOL_ERROR: `lvke-project-planning.planning_confirm`
- PROTOCOL_ERROR: `lvke-project-planning.planning_prepare`
- PROTOCOL_ERROR: `lvke-project-planning.planning_create`
- PROTOCOL_ERROR: `lvke-reference.reference_get`
- PROTOCOL_ERROR: `lvke-source-files.source_upload_begin`
- PROTOCOL_ERROR: `lvke-zero-material-delivery.delivery_transition`

## Synthetic 金标链（stdio 脚本）

| 步骤 | 工具 | 分类 | 状态 | object_id | trace_id | 备注 |
|------|------|------|------|-----------|----------|------|
| ProjectContext | `project_context_create` | PASS | ok | pctx_5ea6492152812bc25b037758 | `mcp_87667c0bf6b24c52…` | — |
| SourceSnapshot | `source_import_content` | PASS | ok | src_994993b01e2a63e38960e3ac | `mcp_689e7fbffd5043cf…` | — |
| DiscoverySet | `data_discover` | UPSTREAM_FAILURE | upstream_failure | discovery_6d5b35f298c055eff28a735b | `mcp_20906e4f076540c5…` | candidate summaries ≠ evidence |
| CandidateSet | `analysis_ingest` | PASS | ok | analysis_71af3ed0d8aee6d8031978b8 | `mcp_2998ca4a93f64d9d…` | — |
| EvidencePack | `analysis_build_evidence_pack` | EXPECTED_REJECTION | blocked | — | `mcp_24bd3c0714c648b4…` | — |
| ResearchPackage | `dr_prepare` | PASS | ok | — | `mcp_6648ebd496f94a5d…` | — |
| DeliveryRun | `feasibility_start` | PASS | ok | fdr_5d13b0f8a9771bea7de0554e | `mcp_1479d11f24054f43…` | — |
| IndustryConstraints | `planning_get_industry_constraints` | PASS | ok | — | `mcp_e08a1b040170489a…` | planning chain entry without market_case dependency |
| FinanceSpec | `finance_prepare_spec` | PASS | partial | fsp_fe5d2d6f3b015c06b9d1ab90 | `mcp_7448092ed442490f…` | — |
| FinanceSpecConfirmed | `finance_confirm_spec` | PASS | partial | fsp_43d65d5c3c601711497f63c7 | `mcp_43ffa2aabe814087…` | — |
| FinanceRun | `finance_run_model` | PASS | ok | run_27d443009f15 | `mcp_5356b6918fc8478f…` | — |
| FinanceTablesPackage | `tables_render` | PASS | partial | run_27d443009f15 | `mcp_34bc19d581c04cc7…` | validation_complete may be false for estimate_preview |
| TablesExportCsv | `tables_export_csv` | EXPECTED_REJECTION | blocked | — | `mcp_5025705e91564ee1…` | technical scope process artifact |
| ReviewPrepare | `review_prepare` | PASS | ok | rvprep_e3cbe1e063b4a7d29fd9d617 | `mcp_c2d977aa0c4c4654…` | — |
| Review | `review_start` | PASS | partial | review_8a4a5dd64b953f220a747d9af667df51 | `mcp_b44cf5a620db42ca…` | — |
| ReviewFindings | `review_list_findings` | PASS | ok | review_8a4a5dd64b953f220a747d9af667df51 | `mcp_9451a020500f4223…` | — |
| ReportPreparation | `report_prepare` | PASS | partial | rprep_136c142a287c55ddf096564f | `mcp_70989a721864425f…` | — |
| ReportRevisionDraft | `report_start` | PASS | ok | rrv_950346e08d4cec13260f6ed6 | `mcp_261081bf3c314f3a…` | — |
| ReportRevision | `report_status` | PASS | ok | rrv_9e878effb1535486dbef10f3 | `mcp_2166d88cbae54ee1…` | — |
| ReportPropose | `report_propose` | PASS | ok | prop_f9ac3b157c4912a9 | `mcp_0fe2c36c5b25485c…` | — |
| ReportDiff | `report_diff` | PASS | ok | prop_f9ac3b157c4912a9 | `mcp_65a4970abff24a36…` | — |
| ReportApply | `report_apply` | PASS | ok | rrv_f1a29b7540fd58c370107421 | `mcp_9525a5f44d044b01…` | — |
| ReviewRetest | `review_retest` | EXPECTED_REJECTION | blocked | — | `mcp_9dcaa03da1434706…` | formal export blocked without EVD-2 |
| ReviewExport | `review_export` | PASS | ok | review_8a4a5dd64b953f220a747d9af667df51 | `mcp_08d22f0099d64917…` | formal DOCX requires EVD-2 — process JSON only |
| FeasibilityValidate | `feasibility_validate` | PASS | partial | fdr_5d13b0f8a9771bea7de0554e | `mcp_034f7bb1dc824eff…` | — |

## G1 退出条件核对

- [x] 171 工具实时调用
- [x] 金标链 ≥20 步（含 evidence/planning/report/review）
- [ ] 工具探测无协议错误（-32602）：16 项
- [ ] 金标链无 PROTOCOL_ERROR/UPSTREAM_FAILURE（21/25 PASS）
- [ ] 工具探测无 UPSTREAM_FAILURE
- [ ] `build_metadata_complete=true`（须 clean checkout + `--release` 构建）

详细 trace：`dev-docs/reports/G1_LIVE_ACCEPTANCE_20260821.json`
