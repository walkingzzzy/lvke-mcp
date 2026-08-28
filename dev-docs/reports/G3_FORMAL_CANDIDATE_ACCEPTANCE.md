# G3 正式候选验收报告

- **生成时间（UTC）**：2026-08-28T12:45:00Z
- **工作区**：preview=`live-acc-20260828`；晋升=`live-acc-g3-20260828`
- **P0 EVD-2 计数**：5 / 5（`review_list_requirements` 适用项；generic 分母=5，不是历史 24）
- **formal_candidate_eligible**：true（拟定 `sim_a_formal` 已附着，**不是真实原件 EVD-2**）
- **release_ready**：false（脏树、缺 `build_time`、正式工件未过门禁）
- **build_metadata_complete**：false
- **docx_visual_acceptance**：false（无 soffice 逐页中文/裁切/表格验收）
- **收购 15 表**：未进七档

`sim_a_formal` ≠ 真实原件。`formal_candidate_eligible` ≠ `release_ready`。禁止伪造签章、文号、流水、批复与检测/审计结论。

## Preview 三探针（未晋升金标链）

| 工具 | 分类 | status | code | trace_id |
|------|------|--------|------|----------|
| `review_export` docx | EXPECTED_REJECTION | blocked | FORMAL_ARTIFACT_QUALIFICATION_REQUIRED | `mcp_2195179cc558448f9d7b9e249eba1c8b` |
| `report_export_docx` formal_candidate | EXPECTED_REJECTION | blocked | FORMAL_ARTIFACT_QUALIFICATION_REQUIRED | `mcp_74e836c72a234832b3f2d6e12cf0e041` |
| `feasibility_release` project_delivery | EXPECTED_REJECTION | blocked | controlled_assumption_formal_forbidden | `mcp_4007bc83517d4eaba9631bcd204b54d3` |

## 七档晋升链（live MCP，workspace `live-acc-g3-20260828`）

适用需求：`REQ-MARKET-LINEAGE` / `REQ-OPTION-BEFORE-SCALE` / `REQ-FINANCE-13-TABLES` / `REQ-REPORT-9-CHAPTERS` / `REQ-NONOPERATING-BALANCE`。排除收购 `REQ-ACQUISITION-SCENARIOS`。`standard_applicability_id=stdapp_05af94a30b7d41bdd75de44e`。

| 档 | 完整链 | FinanceRun | 十三表 | 报告/审查闭环 | 正式 DOCX | FDR release | 视觉 |
|----|--------|------------|--------|---------------|-----------|-------------|------|
| tourism_catering | 部分 | run_b61abd4e1e30 | ftp_6831d326f47c77d04a3972fd | 报告+审查 JSON；retest 拒 | 拒 FORMAL_ARTIFACT… | 未执行（审批失败） | 未做逐页 |
| real_estate | 否 | run_6c95a9496650 | ftp_faf6de1cd2fd5618c33b3d36 | 财务后未走完报告/审查 | 未跑 | 未跑 | 未做 |
| manufacturing | 否 | run_7fbc6fe360cb | ftp_e854f8e5190a3fa13e2660f8 | — | — | — | — |
| environment_utilities | 否 | run_fb3d2c9c401a | ftp_eadea4d8fae8b3d4616293fd | — | — | — | — |
| park_infrastructure | 否 | run_b0b4934e00dd | ftp_c3fab991589794cfe17ea42a | — | — | — | — |
| urban_rail_transit | 否 | run_34db3c2e23e2 | ftp_c23bd6d25e57b7e791baf60a | — | — | — | — |
| cemetery_funeral | 否 | run_83bc88fd249d | ftp_8c7f8c89117cdc1ad32332c3 | — | — | — | — |

七档均到 FinanceRun + 十三表；多数 run 带 `missing_input:wc_turnover.inventory`。零材料晋升只返回 `next_actions`（`project_context_create` / `feasibility_start`），未对 `zmr_*` 调 `feasibility_release`。

`review_list_requirements` 仍可能显示 `pending_evidence`：文件已按 `sim_a_formal` 附着，但 MarketSizingCase / FinanceRun 等对象未写入该 applicability。

## Release 预检口径

同一套 `run_release_preflight`：未晋升 SIM-A 拒；`sim_a_formal` 计 EVD-2，分母=适用项 5。脏树下 `release_ready` 必须仍为 false。本轮未宣称预检脚本级 `release_ready=true`。

## G3 退出条件核对

- [x] preview 三探针 EXPECTED_REJECTION
- [x] 七档晋升到 FinanceRun（live）
- [ ] 两档完整 Review→Export→release（文旅做到审查 JSON，正式导出/release 未过；房地产财务后未走完）
- [x] EVD-2 分母=5，拟定模板已附着
- [x] `release_ready=false`
- [ ] 逐页视觉验收

## 结论

正式候选过程：preview 拒绝符合门禁；七档拟定晋升可走到 FinanceRun。文旅完整链未关闭正式导出与 FDR release。`formal_candidate_eligible` 不等于可发布。

技术金标链、真实资料资格、正式候选和 release 条件分别判定，不相互替代。禁止写成「正式发布验收完成」。
