# G2 真实资料轨验收报告

- **生成时间（UTC）**：2026-08-28T12:45:00Z
- **工作区**：`live-acc-g2-20260828`（与 G1 隔离）
- **引用核验数**：22（`dev-docs/fixtures/g2_v16_citation_urls.json` 全量）
- **build_metadata_complete**：false
- **release_ready**：false
- **资料资格**：partial / blocked（不可回读快照不得解除正式门禁）

## 步骤分类

| 分类 | 数量 |
|------|------|
| PASS | 7 |
| EXPECTED_REJECTION | 1 |
| UPSTREAM_FAILURE | 0 |
| SKIPPED | 0 |

引用审计本身业务成功，但 **22/22 URL 被本地代理 fake-ip 拦截**，无可回读正文。Tavily 搜索探测可用；经 `extraction_provider=tavily` 固化 **1** 条 NDRC 快照。未晋升 SIM-A，controlled_assumption / 拟定模板不得当 EVD-2。

## 链路步骤

| 步骤 | 工具 | 分类 | status | trace_id | object_id | 备注 |
|------|------|------|--------|----------|-----------|------|
| ProviderStatus | `data_provider_status` | PASS | ok | `mcp_74f7e6d910aa4707aad7260c6ec4b104` | tavily-hikari search probed_ok | extract=not_probed |
| CitationAudit-22 | `data_audit_urls` live | PASS | ok | `mcp_8f3e17c08ebb4bdea60fd4ba97d243d6` | urlaudit_fb058f1e74bf80c8fb4e324d | 22 BLOCKED / proxy_fake_ip_resolution |
| UrlAuditRead | `data_get_url_audit` | PASS | ok | `mcp_1cd0f278a8b84014a1a980f19c5dd5c7` | 同上 | 审计≠正文证据 |
| Discovery | `data_discover` | PASS | ok | `mcp_0bfe83e48f924d0da4c79183b7d0f469` | discovery_7ff8bffe27c4f1825c327950 | 2 候选，摘要空 |
| SourceImport | `source_import_content` | PASS | ok | `mcp_fd84b96fc70c424f8457e390759dfa14` | src_a6636d7aeedaca6682361bcd | 受控导入，非正式原件 |
| SourceGet | `source_file_get` | PASS | ok | `mcp_1c6f51d098d64458835bf69644b3fac9` | 同上 | hash 可回读 |
| AnalysisIngest | `analysis_ingest` | PASS | ok | `mcp_1da2038766bf4896a0a5c2545a2bae21` | analysis_7d15dabcc4d70cd1a68173ad | — |
| EvidencePack-recon | `analysis_build_evidence_pack` | EXPECTED_REJECTION | blocked | `mcp_c8b3317631e1481a9e3a913df5ce9bdb` | — | source_reconstruction_invalid |
| Fetch-1 | `data_fetch` tavily | PASS | ok | `a87e955e87c39f8e8dd10ff2` | src_406eab3eb87fcc9645d5eaa0 | 仅 1/22 正文快照 |

## 引用结果

- 可回读快照：1/22（发改委 304 号解读页，Tavily extract + receipt）
- 本地 live 审计：22/22 `safe_public_target=false`（Clash/Surge fake-ip `198.18.0.0/15`）
- 未对 21 条不可达 URL 伪造正文

## G2 退出条件核对

- [x] 隔离 workspace + 受控 import 可回读 hash
- [x] 22 条 URL 均有审计记录（blocked 也记账）
- [x] 资料不足保持 partial；未用金标结果解除正式门禁
- [ ] 22 条均可回读快照
- [ ] 正式 EVD-2 / 真实原件

## 结论

真实资料轨 **partial**。Tavily 搜索可用，本地 URL 直连被代理 fake-ip 阻断。技术金标链、真实资料资格、正式候选和 release 条件分别判定，不相互替代。禁止写成正式发布验收完成。
