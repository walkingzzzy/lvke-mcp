# 绿科 MCP 服务集合（独立发行版 `lvke-mcp`）

本目录是**独立的 MCP（Model Context Protocol）服务发行版**，包名 `lvke-mcp`，
有自己的 `pyproject.toml`、`src/` 布局和自有 runtime（`src/lvke_mcp/runtime/`）。

**独立边界（MCP_INDEPENDENCE_PLAN §6.1、§10）：** MCP 服务不读取、不调用、不依赖
Hermes（`hermes_cli` / `tools` / 根 pyproject）的任何代码、配置、环境变量或文件系统路径。
MCP 运行所需的配置（数据目录、临时目录、profile、Tavily key）全部来自 `LVKE_MCP_*`
自有环境变量，缺省落在 `~/.lvke/`。**不读取任何 `HERMES_*` 环境变量。** 本目录内的
业务实现是 MCP 自有的领域代码，不是 Hermes 的复用层。

## 迁移状态（2026-08-04）

- **WP-01（独立 pyproject + runtime 骨架）已完成构建**：`mcp_servers/pyproject.toml`、
  `src/lvke_mcp/runtime/`（10 个直接搬移模块 + `config.py` / `jobs.py` 两个新模块）、
  参考 server `src/lvke_mcp/servers/scaffold/`。验收标准（§29.3）：scaffold server 在
  未安装 Hermes 且不依赖当前仓库根目录虚拟环境的干净 venv 中完成 `initialize`、
  `tools/list`、`call_tool`、`resources/list`、`resources/read`。
- **纵向切片（§29.4）进行中**：既有领域 server 仍以 `mcp_servers/lvke_*/server.py`
  形式存在，按 §29.4 顺序（source-files → data-acquisition → data-analysis →
  deep-research → project-planning → finance-model → finance-tables →
  report-generation → asset-acquisition → deliverable-review → knowledge-governance →
  support）逐个搬移进 `src/lvke_mcp/` 并为 `_common` 建立兼容垫片。
- **独立性扫描升级为 AST v2（2026-08-04）**：`scripts/independence_scan.py` 改用
  `ast` 解析，只统计真实 import / 动态加载 / `HERMES_*` 环境变量读取，不再把
  docstring、注释、字符串字面量计为依赖。当前结果：
  `forward（MCP → 外部）= 0`，即 `src/lvke_mcp` 不调用任何其他项目代码；
  `reverse（Hermes → MCP）= 25 处`，均为 Hermes 侧对旧垫片入口的进程内 import，
  属于 Hermes 侧待迁移项（迁移到标准 MCP transport 后删除垫片）。

## 正式业务工具面（十一服务）

| Server | 核心对象 | 职责 |
|---|---|---|
| `lvke-project-planning` | `project_context_id` / `input_applicability_id` | 不可变项目上下文、输入适用性、修订与下游失效 |
| `lvke-source-files` | `file_id` / `parse_job_id` / `upload_id` | 受控内容/本地/分块导入、安全扫描、解析恢复与 Resource |
| `lvke-data-acquisition` | `discovery_set_id` / `source_collection_id` / `source_snapshot_id` | 搜索、去重发现、受控批量采集、原始来源快照 |
| `lvke-data-analysis` | `candidate_set_id` / `data_profile_id` / `evidence_pack_id` | 摄入、检索、字段候选、表格画像、显式归一化比较、冲突与证据包 |
| `lvke-finance-model` | `spec_id` / `run_id` | 通用可研 FinanceSpec 确认与确定性财务计算 |
| `lvke-deep-research` | `ResearchPlanRevision` / `checkpoint_id` / `research_package_id` | Agent 主导的 DR 计划、混合来源、可恢复 checkpoint、质量与引用审计 |
| `lvke-finance-tables` | `finance_tables_package_id` | 只消费 run_id 的十三表、XLSX 与 lineage |
| `lvke-report-generation` | `report_revision_id/artifact_id` | 研报草稿、修订、校验、DOCX 与内部发布 |
| `lvke-asset-acquisition` | `spec_id` / `acqrun_*` / `acquisition_tables_package_id` | 月度资产收购模型、治理、工件、专用十三表与发布 |
| `lvke-deliverable-review` | `review_preparation_id` / `review_id` / `finding_id` | 财务表、研报与联合交付包的统一审查、整改复测、签审和正式固化 |
| `lvke-knowledge-governance` | `candidate_id` / `review_id` / `release_id` | 证据化知识候选、独立复核和 reviewed-first 发布 |

## 支撑与兼容服务（11 个）

`finance-calc`、`excel-bridge`、`lvke-archive`、`lvke-templates`、`lvke-clients`、
`lvke-experts`、`policy-search`、`statistics-cn`、`industry-research`、
`environmental-data`、`map-geo`。

这些服务允许在开发环境独立调用和验真，但不替代正式 `spec_id`、`run_id`、
evidence package、tables package 或报告发布治理。

## 目录结构

```
mcp_servers/
├── pyproject.toml            # 独立发行版 lvke-mcp
├── README.md                 # 本文档
├── MCP_INDEPENDENCE_PLAN.md  # 独立化开发方案
├── src/lvke_mcp/
│   ├── runtime/              # MCP 自有运行时（config/workspace/storage/resources/
│   │                         #  jobs/transport/stdio/errors/responses/schemas/logging）
│   ├── contracts/            # 领域契约（pydantic / JSON Schema），随切片填充
│   ├── domains/              # 领域业务层，随切片迁入
│   └── servers/
│       ├── scaffold/         # 参考 server（无 sys.path hack，零 Hermes 依赖）
│       └── (纵向切片后各领域 server)
├── _common/                  # 领域 server 的共享底座（切片期间经垫片维持运行）
├── lvke_project_planning/    # 既有领域 server（§29.4 迁移进行中）
├── lvke_source_files/
├── lvke_data_acquisition/
├── lvke_data_analysis/
├── lvke_finance_model/
├── lvke_deep_research/
├── lvke_finance_tables/
├── lvke_report_generation/
├── lvke_asset_acquisition/
├── lvke_deliverable_review/
├── lvke_knowledge_governance/
├── finance_calc/             # 历史内部计算实现，不作为正式财务旁路
├── excel_bridge/             # 十三表 MCP 复用的导出实现
├── lvke_archive/             # 历史可选能力，不默认暴露
├── lvke_templates/           # 历史模板实现，不默认暴露
└── tests/
```

## 构建与安装

```bash
cd mcp_servers
python -m venv .venv
.venv/bin/pip install -e .
```

## 单独运行一个 server（调试用）

scaffold（参考 server，运行后会通过 stdio 接收 JSON-RPC）：

```bash
cd mcp_servers
.venv/bin/lvke-mcp-scaffold
# 或
.venv/bin/python -m lvke_mcp.servers.scaffold.server
```

既有领域 server（纵向切片完成前）：

```bash
.venv/bin/python -m mcp_servers.lvke_data_acquisition.server
.venv/bin/python -m mcp_servers.lvke_finance_model.server
# …其余 lvke_* server 同形
```

## 在客户端中启用

MCP 发行版自身不依赖任何客户端运行。任何 MCP 客户端（Codex、Claude Desktop、Hermes
等）通过 stdio 子进程方式启动本目录下的 server 即可；客户端侧的注册方式是客户端自己的
配置行为，不属于本发行版。`optional-mcps/` 下的 manifest 是各 server 的注册描述，
供客户端直接引用。

## 共用约定

1. **响应格式**：旧工具保留 `success/data/source` 兼容包装；十一个正式服务使用明确字段，
   并至少返回 `success/status/resource_uris/warnings/blockers/next_actions`。
2. **错误码命名**：`<server-name>.<error-tag>`，如 `mcp_lvke_archive.not_found`。
3. **日志**：使用 stderr（避免污染 stdio 协议），统一前缀 `[mcp-<server>]`。
4. **本地对象**：MCP 交换对象存放于工作区 `mcp_objects/`，ID/URI 安全校验、原子写入且
   内容不可变。
5. **测试**：正式服务协议矩阵在 `tests/mcp_servers/test_protocol_compliance.py`，
   stdio 连通性由 `_common/smoke_test.py` 覆盖，领域边界在各 `test_lvke_*.py`、
   资产收购契约和统一审查契约测试。
6. **Agent 协调契约**：正式 SDK Server 和 stdio fallback 都在响应中附加
   `coordination.contract_version=agent-coordination.v1`。其中包含当前阶段、输入/输出对象
   ID、质量状态、证据资格、结构化 `next_actions`、重试策略、恢复令牌和 lineage。该字段只帮助
   Codex 在 MCP 重启、分支或失败后继续协调；它不把 MCP 变成工作流引擎，也不生成报告正文。
7. **职责边界**：MCP 负责来源固化、确定性计算、十三表、版本和门禁；Codex 负责意图理解、
   候选选择、冲突解释、章节写作和补资料循环；Skills 负责调用顺序和停止条件。`partial`、
   `missing_inputs`、`blocked`、`incomplete`、`failed` 和 `upstream_failure` 均不是业务成功。
8. **MCP 2.0 协议面**：共用 runtime 同时支持 2024-11-05 至 2025-11-25 的 legacy
   initialize 握手和 2026-07-28 的逐请求 `_meta` envelope。`server/discover`、
   `tools/list` 和 `resources/list` 只返回 private cache hint。规范 `input_required`
   仅向 modern 客户端透传，legacy 客户端收到可操作的 blocked 结果。Tasks 仅在
   服务显式注册持久化 `TaskAdapter` 后启用；现有短同步工具均为
   `taskSupport=forbidden`。stdio 仍是生产默认，Streamable HTTP 只通过共享适配器显式构建。

## 开发新 MCP server

参考 `src/lvke_mcp/servers/scaffold/`：复制其 `server.py` 到 `lvke_mcp/servers/<new-name>/`
后修改 `SERVER_NAME` / `SERVER_VERSION` 并注册具体工具。scaffold 的 `build_server()`
返回 `lvke_mcp.runtime.transport.OfficialStdioServer`，`main()` 调 `serve_forever()`。
