# G2 真实资料轨验收报告

- **生成时间（UTC）**：2026-08-21T01:52:19Z
- **工作区**：`restart-g2-20260821-a`
- **引用核验数**：22（V1.6 主报告全量 URL）
- **含 trace_id 步骤**：28/28
- **build_metadata_complete**：False

## 步骤分类

| 分类 | 数量 |
|------|------|
| PASS | 25 |
| EXPECTED_REJECTION | 2 |
| UPSTREAM_FAILURE | 1 |
| SKIPPED | 0 |

## 链路步骤

| 步骤 | 工具 | 分类 | status | trace_id | object_id | 备注 |
|------|------|------|--------|----------|-----------|------|
| SourceImport | `source_import_content` | PASS | ok | `mcp_3ed9011cf277…` | src_e86129309ac710d3bc7ce24e | — |
| SourceSnapshot | `source_file_get` | PASS | ok | `mcp_b81d46eee3a8…` | src_e86129309ac710d3bc7ce24e | — |
| AnalysisIngest | `analysis_ingest` | PASS | ok | `mcp_e896800c8b6c…` | analysis_1a16682a3e57f2c0e952e7cc | — |
| EvidencePack | `analysis_build_evidence_pack` | EXPECTED_REJECTION | blocked | `mcp_8cbe2ebe12b7…` | — | — |
| ProviderStatus | `data_provider_status` | EXPECTED_REJECTION | blocked | `mcp_f5a2db8a6a37…` | — | — |
| DataDiscover | `data_discover` | UPSTREAM_FAILURE | upstream_failure | `mcp_d181ff4713b1…` | discovery_4292465d0a14ac045da93f8d | network/provider dependent |
| CitationAudit-01 | `data_audit_urls` | PASS | ok | `mcp_e9193340a388…` | — | http://nyj.xianning.gov.cn/xxgk/zc/zcwj/ |
| CitationAudit-02 | `data_audit_urls` | PASS | ok | `mcp_b0016919fa07…` | — | http://tjj.hubei.gov.cn/tjsj/tjgb/ndtjgb |
| CitationAudit-03 | `data_audit_urls` | PASS | ok | `mcp_9010cf817dfd…` | — | http://wlj.xianning.gov.cn/xxgk/fdzdgknr |
| CitationAudit-04 | `data_audit_urls` | PASS | ok | `mcp_54f26671a787…` | — | https://czj.sh.gov.cn/zys_8908/zcfg_8983 |
| CitationAudit-05 | `data_audit_urls` | PASS | ok | `mcp_5fae2b491891…` | — | https://fgk.chinatax.gov.cn/zcfgk/c10000 |
| CitationAudit-06 | `data_audit_urls` | PASS | ok | `mcp_11522d9b06bd…` | — | https://fgw.hubei.gov.cn/fbjd/zc/gfwj/gf |
| CitationAudit-07 | `data_audit_urls` | PASS | ok | `mcp_5ff516f177ac…` | — | https://gdzqfy.gov.cn/flwk/62.html |
| CitationAudit-08 | `data_audit_urls` | PASS | ok | `mcp_f16a02698ca7…` | — | https://gdzqfy.gov.cn/flwk/62.html?page= |
| CitationAudit-09 | `data_audit_urls` | PASS | ok | `mcp_f3d11bb59a86…` | — | https://jcs.moa.gov.cn/gzdt/202306/t2023 |
| CitationAudit-10 | `data_audit_urls` | PASS | ok | `mcp_1c72f9a00d01…` | — | https://nyt.hubei.gov.cn/zfxxgk/fdzdgknr |
| CitationAudit-11 | `data_audit_urls` | PASS | ok | `mcp_966ead21eb2e…` | — | https://www.cac.gov.cn/2023-07/13/c_1690 |
| CitationAudit-12 | `data_audit_urls` | PASS | ok | `mcp_ba1913cd33c8…` | — | https://www.cac.gov.cn/2025-03/14/c_1743 |
| CitationAudit-13 | `data_audit_urls` | PASS | ok | `mcp_eb10bc614cba…` | — | https://www.mee.gov.cn/ywgz/fgbz/fl/2019 |
| CitationAudit-14 | `data_audit_urls` | PASS | ok | `mcp_db13a89217c5…` | — | https://www.mee.gov.cn/zcwj/gwywj/202307 |
| CitationAudit-15 | `data_audit_urls` | PASS | ok | `mcp_3698b95616c2…` | — | https://www.moj.gov.cn/pub/sfbgw/flfggz/ |
| CitationAudit-16 | `data_audit_urls` | PASS | ok | `mcp_76cd8dfe3698…` | — | https://www.moj.gov.cn/pub/sfbgw/jgsz/jg |
| CitationAudit-17 | `data_audit_urls` | PASS | ok | `mcp_cd6167991d7a…` | — | https://www.moj.gov.cn/pub/sfbgw/zwgkztz |
| CitationAudit-18 | `data_audit_urls` | PASS | ok | `mcp_ec427763765e…` | — | https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/ |
| CitationAudit-19 | `data_audit_urls` | PASS | ok | `mcp_aa72b18625e6…` | — | https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/ |
| CitationAudit-20 | `data_audit_urls` | PASS | ok | `mcp_2183ced7933a…` | — | https://www.npc.gov.cn/npc/c2/c30834/202 |
| CitationAudit-21 | `data_audit_urls` | PASS | ok | `mcp_e011c6a20339…` | — | https://www.shandan.gov.cn/zfxxgk/xzzfxg |
| CitationAudit-22 | `data_audit_urls` | PASS | ok | `mcp_a365b435f48c…` | — | https://zfxxgk.ndrc.gov.cn/web/iteminfo. |

## G2 退出条件核对

- [x] 受控 import 可回读且含 trace
- [x] 无 PROTOCOL_ERROR（-32602）
- [x] 核心 import/ingest 步骤 PASS
- [ ] 22 条引用全部可回读快照或标记 unresolved（需联网 provider）

详细 trace：`dev-docs/reports/G2_EVIDENCE_ACCEPTANCE_20260821.json`
