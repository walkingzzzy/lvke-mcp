# G3 正式候选验收报告

- **生成时间（UTC）**：2026-08-21T01:51:42Z
- **工作区**：`restart-g3-20260821-a`
- **P0 EVD-2 计数**：0 / 24
- **formal_candidate_eligible**：False
- **build_metadata_complete**：False

## Release Preflight 四关口

### release_preflight
- status: **blocked**

## 正式导出探测（须业务层 EXPECTED_REJECTION，非 -32602）

| 工具 | 分类 | status | code | trace_id | protocol |
|------|------|--------|------|----------|----------|
| `review_export` | EXPECTED_REJECTION | blocked | FORMAL_ARTIFACT_QUALIFICATION_REQUIRED | `mcp_79e86a899db4…` | — |
| `report_export_docx` | EXPECTED_REJECTION | blocked | FORMAL_ARTIFACT_QUALIFICATION_REQUIRED | `mcp_aa6c6d9725c4…` | — |
| `feasibility_release` | EXPECTED_REJECTION | blocked | FORMAL_ARTIFACT_QUALIFICATION_REQUIRED | `mcp_2d2f6f73e317…` | — |

## G3 退出条件核对

- [ ] 24 项 P0 全部 EVD-2
- [x] formal export 使用合法参数且业务拒绝
- [x] 无意外 PASS（process 级导出允许时记为 P1 缺口：无）
- [x] release_preflight 阻断 SIM-A/EVD-0 包
- [ ] DOCX 字体/glyph/逐页 PNG 验收
- [ ] Review → Retest → Export 完整闭环（真实 EVD-2 资料）

详细 trace：`dev-docs/reports/G3_FORMAL_CANDIDATE_20260821.json`
