---
name: lvke-source-acquisition
description: >
  Public-source discovery and retrieval for Lvke feasibility / general research.
  Mandates multiple queries and independent published sources through the
  single supported provider, Tavily, then fetches or imports snapshots.
  Use when searching policy, market, industry, project, or comparable sources;
  filtering URLs; collecting pages; or preserving source_snapshot_id. Not for:
  controlled-file parsing alone, evidence comparison, FinanceSpec values,
  financial calculations, research lifecycle, or report writing.
---

# 公开来源采集

目标是把公开网页变成可回读的 `source_snapshot_id`，而不是把搜索摘要当作项目事实。

## 硬门禁：Tavily 多查询、多来源

**禁止**「单次 `data_search` / 单次 `tavily_search` → 直接下结论或写数值表 / 交付物」。

未完成下列门槛前，不得宣称来源已充分、不得把摘要当证据写入市场规模/财务/结论：

1. **联网 provider 只有 Tavily**。先调用 `data_provider_status`；不可用时返回 `upstream_failure/partial`，不回退或要求配置其他 provider。
2. **≥3 条互不雷同的查询角**（例：规模/政策/成本/竞品/区域），不得一条 query 打天下。
3. 每个关键结论尽量使用 **≥2 个不同发布主体**的来源交叉验证；同一 provider 不等于同一来源。
4. **合并去重**后保留候选清单；title/summary/answer **不是证据**。
5. **抓取或回灌正文**后再进入整理：优先 `data_fetch` / `data_collect`；外部 Tavily 正文可用 `data_import_external_snapshot` 固化。
6. 需要指标时，把 `source_snapshot_id` 交给 `lvke-evidence-ingestion` → `lvke-evidence-analysis`；口径冲突**并列披露**，禁止取平均凑数。

### 反模式（违反即打回）

- 只搜一次就写 CSV/报告/结论
- 用搜索 snippet 冒充已抓取正文
- 跳过 discover/多查询，直接 `finance_*` 或填经营财务表
- 将多个 Tavily 查询误写成多个 provider

## 工作流

1. **检查 Tavily 状态**：调用 `data_provider_status`。不可用时停止联网结论，保留 `upstream_failure/partial`。

2. **Tavily 发现与聚合**：
   - 单问题补充可用 `data_search`，但单独不足以支撑正式结论。
   - **默认用 `data_discover`**：多条 `queries` + `auto_expand=true` + `target_count=40`（或 ≥30），扩展政策/市场/技术/投资/运营/竞品/区域等角度，跨查询去重。
   - 域名筛选用 `domain_allowlist` / `domain_denylist`（支持通配如 `gov.cn`）
   - title/summary/answer 都只是候选元数据，不是证据。

3. **项目附件**：PDF、Excel、Word 和图片使用 `lvke-source-files` 导入与解析，再将可定位内容交给证据分析。

4. 选定 Tavily 结果后，优先把原 URL 交给 `data_fetch` 形成正式快照。只有确需回灌时才把 URL、title、正文、provider/tool 和 retrieved_at 交给 `data_import_external_snapshot`；不得传入 answer、搜索 snippet 或 research 合成文本。

5. 对 discovery 集合调用 `data_collect(discovery_set_id, selected_candidate_ids)`；用户直接指定 URL 时调用 `data_fetch(extraction_provider="auto")`。两条路径都必须产生 Lvke `source_snapshot_id`。公网 URL 被安全门拦截时原样记 `url_security_blocked`，不得跳过正文固化直接写结论。

6. 只保留成功返回的 `source_snapshot_id`、`source_collection_id` 和 Resource URI。需要整理、查询、比较或证据包时，把 snapshot ID 交给 `lvke-evidence-ingestion`。

## 停止条件

- 私网、含密钥参数、DNS/SSRF 或抓取策略拒绝时，原样说明阻断；不得换 IP、关闭防护或使用 shell 绕过。
- Provider 不可用时调用 `data_provider_status`；将环境阻断与「没有业务资料」区分开。
- 外部搜索 MCP（tavily-hikari）只是公开来源 provider，不是 Lvke MCP Server，也不替代 snapshot/evidence lineage。
- `data_search`、`data_discover` 和外部 Tavily 工具都属于同一个 provider；不得宣称为多 provider 验证。
- `data_collect` 只接受同一 discovery set 内的 candidate；unknown candidate 不得改成任意 URL 抓取。
- 网页值只能是候选依据，绝不自动写入 FinanceSpec、input revision 或报告财务数字。

## 不触发

解析 PDF/XLSX、比较来源、固化 evidence pack、计算 IRR、跑 DR、生成十三表或写研报时，使用相应的运行时 Skill。
端到端「从零联网」编排见 `lvke-tool-coordination`（同样强制多源门禁）。
