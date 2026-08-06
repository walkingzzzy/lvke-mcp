---
name: lvke-source-acquisition
description: >
  Public-source discovery and retrieval for Lvke feasibility / general research.
  Mandates multi-source search (never a single query then conclude): parallel
  Tavily / data_discover, then fetch or import snapshots.
  Use when searching policy, market, industry, project, or comparable sources;
  filtering URLs; collecting pages; or preserving source_snapshot_id. Not for:
  controlled-file parsing alone, evidence comparison, FinanceSpec values,
  financial calculations, research lifecycle, or report writing.
---

# 公开来源采集

目标是把公开网页变成可回读的 `source_snapshot_id`，而不是把搜索摘要当作项目事实。

## 硬门禁：必须多源搜索

**禁止**「单次 `data_search` / 单次 `tavily_search` → 直接下结论或写数值表 / 交付物」。

未完成下列门槛前，不得宣称来源已充分、不得把摘要当证据写入市场规模/财务/结论：

1. **≥2 个独立搜索通道**并行（可用则全开；不可用须调用 `data_provider_status` 并写明缺口）：
   - `tavily-hikari.tavily_search`（优先 `search_depth=advanced`，可加 `include_domains`）
   - `lvke-data-acquisition.data_discover`（独立 provider 聚合；**多查询** + `auto_expand=true`，目标 `target_count≥30`）
2. **≥3 条互不雷同的查询角**（例：规模/政策/成本/竞品/区域），不得一条 query 打天下。
3. **合并去重**后保留候选清单；title/summary/answer **不是证据**。
4. **抓取或回灌正文**后再进入整理：优先 `data_fetch(extraction_provider="auto")`，由服务端受信 Tavily extract 生成 receipt；不可用时同 URL 回退 `direct_http`。外部 Tavily extract → `data_import_external_snapshot` 仅是候选，除非 receipt 可由服务端验证。
5. 需要指标时，把 `source_snapshot_id` 交给 `lvke-evidence-ingestion` → `lvke-evidence-analysis`；口径冲突**并列披露**，禁止取平均凑数。

### 反模式（违反即打回）

- 只搜一次就写 CSV/报告/结论
- 用搜索 snippet 冒充已抓取正文
- 跳过 discover/多通道，直接 `finance_*` 或填经营财务表
- 一个 provider 噪声大仍不换通道、不去重、不登记 issue

## 工作流

1. **高质量公开来源发现（多源并行，不可省）**：按可用性和质量调用：
   - `tavily_hikari.tavily_search` — **最高质量**聚合搜索，支持 `search_depth=advanced`、时间范围、`include_domains` 筛选
   - 通用联网搜索不再调用内置 Web Search；Tavily 失败时必须返回 `upstream_failure/partial`，不得静默降级。
   
   当前会话未显示这些 MCP 时，如实说明需重启加载 provider，不得声称已调用。

2. **Lvke 内置搜索与聚合（与上一步并行，不可替代上一步）**：
   - 单问题补充可用 `data_search`（limit 最多 100，但单 provider 如 ddgs 实际上限常很低）——**单独不够**
   - **默认用 `data_discover`**：多条 `queries` + `auto_expand=true` + `target_count=40`（或 ≥30），扩展政策/市场/技术/投资/运营/竞品/区域等角度，跨查询去重。不足目标时返回 `partial` 并如实报告实际条数，不伪造。
   - 域名筛选用 `domain_allowlist` / `domain_denylist`（支持通配如 `gov.cn`）
   - title/summary/answer 都只是候选元数据，不是证据。

3. **文档解析与结构化**（PDF/Excel/Word等）：
   - `markitdown-mcp` — PDF/Excel/Word/PPT → Markdown 转换，支持 OCR，保留表格结构
   - `oxidize-pdf` — PDF 深度解析，表格提取为 CSV/JSON，适合数据密集型文档
   
   解析结果必须经 `data_import_external_snapshot` 固化为 Lvke 不可变快照，拿到 `source_snapshot_id`。

4. 选定 Tavily 等外部 MCP 结果后，优先把原 URL 交给 `data_fetch(extraction_provider="auto")` 形成服务端 receipt 或 direct-HTTP 正式快照。只有确需回灌时才把 URL、title、正文、provider/tool 和 retrieved_at 交给 `data_import_external_snapshot`；无服务端可验证 receipt 时 `formal_use_allowed=false`。只允许 `extracted_full_text` / `raw_content`，不得传入 answer、搜索 snippet 或 research 合成文本。

5. 对 discovery 集合调用 `data_collect(discovery_set_id, selected_candidate_ids)`；用户直接指定 URL 时调用 `data_fetch(extraction_provider="auto")`。两条路径都必须产生 Lvke `source_snapshot_id`。公网 URL 被安全门拦截时原样记 `url_security_blocked`，不得跳过正文固化直接写结论。

6. 只保留成功返回的 `source_snapshot_id`、`source_collection_id` 和 Resource URI。需要整理、查询、比较或证据包时，把 snapshot ID 交给 `lvke-evidence-ingestion`。

## 停止条件

- 私网、含密钥参数、DNS/SSRF 或抓取策略拒绝时，原样说明阻断；不得换 IP、关闭防护或使用 shell 绕过。
- Provider 不可用时调用 `data_provider_status`；将环境阻断与「没有业务资料」区分开。
- 外部搜索 MCP（tavily-hikari）只是公开来源 provider，不是 Lvke MCP Server，也不替代 snapshot/evidence lineage。
- Tavily 和 `data_discover` 是两个独立发现通道；若任一不可用，登记 provider 状态和资料缺口，不得回退到已注销的内置 Web Search。
- 文档处理 MCP（markitdown-mcp/oxidize-pdf）解析结果必须经 `data_import_external_snapshot` 固化，不得直接当证据。
- `data_collect` 只接受同一 discovery set 内的 candidate；unknown candidate 不得改成任意 URL 抓取。
- 网页值只能是候选依据，绝不自动写入 FinanceSpec、input revision 或报告财务数字。

## 不触发

解析 PDF/XLSX、比较来源、固化 evidence pack、计算 IRR、跑 DR、生成十三表或写研报时，使用相应的运行时 Skill。
端到端「从零联网」编排见 `lvke-tool-coordination`（同样强制多源门禁）。
