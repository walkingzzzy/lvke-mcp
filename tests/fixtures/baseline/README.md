# MCP 外部行为基线（阶段0 冻结产物）

由 `mcp_servers/scripts/freeze_baseline.py` 生成。走 stdio MCP 完整握手抓取**线协议**响应，
作为独立化版本「外部行为」的机器可对照基准。

## 冻结方式

每个 server 以 `.venv/bin/python -m mcp_servers.{server}.server` 启动，依次发送：

1. `initialize`（protocolVersion = **2025-11-25**）→ `notifications/initialized`
2. `tools/list` → `resources/list`

把完整 JSON 固化到 `tools-list/`、`resources-list/`、`contracts/`。

## 关键事实

- **协议版本必须用 `2025-11-25`**。`official_server.py` 的严格中间件只握手
  `('2024-11-05','2025-03-26','2025-06-18','2025-11-25')`；`2026-07-28`（MODERN）在
  initialize 阶段即被拒绝（error -32602）。基线冻结必须对齐正式客户端实际能用的版本。
- **`resources/list` 全部返回 0 条**（23/23）。Resource 表面是**动态**的：`lvke://` URI
  在 `resources/read` 时按 workspace 作用域解析，不做枚举。因此资源侧的外部契约 =
  URI scheme + read 行为，而非静态清单。工具的 `annotations`/`outputSchema` 才是主要对照面。

## 统计（2026-08-03 冻结）

| server | tools | resources |
|---|---|---|
| environmental_data | 3 | 0 |
| excel_bridge | 5 | 0 |
| finance_calc | 7 | 0 |
| industry_research | 2 | 0 |
| lvke_archive | 6 | 0 |
| lvke_asset_acquisition | 16 | 0 |
| lvke_clients | 3 | 0 |
| lvke_data_acquisition | 12 | 0 |
| lvke_data_analysis | 13 | 0 |
| lvke_deep_research | 19 | 0 |
| lvke_deliverable_review | 19 | 0 |
| lvke_experts | 3 | 0 |
| lvke_finance_model | 16 | 0 |
| lvke_finance_tables | 23 | 0 |
| lvke_knowledge_governance | 7 | 0 |
| lvke_project_planning | 47 | 0 |
| lvke_report_generation | 16 | 0 |
| lvke_source_files | 15 | 0 |
| lvke_templates | 3 | 0 |
| lvke_zero_material_delivery | 11 | 0 |
| map_geo | 3 | 0 |
| policy_search | 3 | 0 |
| statistics_cn | 2 | 0 |

合计 **254 tools**。契约基线在 `contracts/{server}.json`：每 tool 的
name/description/inputSchema/outputSchema/annotations/taskSupport。

## 对照用法

独立化后重新跑 `freeze_baseline.py`，用
`contracts/` + `tools-list/` 与旧版本 diff，即得外部行为差异。工具 `status` 变化、
`outputSchema` 形状变化、tool 增删都会在此暴露。
