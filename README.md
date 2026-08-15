# 绿科 MCP 服务集合（独立发行版 `lvke-mcp`）

本目录是**独立的 MCP（Model Context Protocol）服务发行版**，包名 `lvke-mcp`，
有自己的 `pyproject.toml`、`src/` 布局和自有 runtime（`src/lvke_mcp/runtime/`）。

**独立边界（MCP_INDEPENDENCE_PLAN §6.1、§10）：** MCP 服务不读取、不调用、不依赖
Hermes（`hermes_cli` / `tools` / 根 pyproject）的任何代码、配置、环境变量或文件系统路径。
MCP 运行所需的配置（数据目录、临时目录、profile、Tavily key）全部来自 `LVKE_MCP_*`
自有环境变量，缺省落在 `~/.lvke/`。**不读取任何 `HERMES_*` 环境变量。** 本目录内的
业务实现是 MCP 自有的领域代码，不是 Hermes 的复用层。

## 架构状态（2026-08-06）

- **WP-01（独立 pyproject + runtime 骨架）已完成构建**：`mcp_servers/pyproject.toml`、
  `src/lvke_mcp/runtime/`（10 个直接搬移模块 + `config.py` / `jobs.py` 两个新模块）、
  参考 server `src/lvke_mcp/servers/scaffold/`。验收标准（§29.3）：scaffold server 在
  未安装 Hermes 且不依赖当前仓库根目录虚拟环境的干净 venv 中完成 `initialize`、
  `tools/list`、`call_tool`、`resources/list`、`resources/read`。
- **纵向切片（§29.4）已完成**：全部正式服务和支撑服务均由
  `src/lvke_mcp/servers/` 提供入口，业务实现位于 `domains/`，基础设施实现位于
  `adapters/` 和 `runtime/`。仓库根目录下的旧 server 兼容包已删除。
- **独立性扫描升级为 AST v2（2026-08-04）**：`scripts/independence_scan.py` 改用
  `ast` 解析，只统计真实 import / 动态加载 / `HERMES_*` 环境变量读取，不再把
  docstring、注释、字符串字面量计为依赖。当前结果：
  `forward（MCP → 外部）= 0`，即 `src/lvke_mcp` 不调用任何其他项目代码；
  新发行版只扫描和运行 `src/lvke_mcp` 自有代码，不保留旧顶层导入入口。

## 核心业务工具面（十三个领域服务）

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
| `lvke-feasibility-delivery` | `delivery_run_id` / `checkpoint_id` / `release_id` | 可研项目阶段编排、对象 lineage、stale、checkpoint/resume、technical/formal 校验和交付发布 |
| `lvke-zero-material-delivery` | `delivery_run_id` / `preview_id` | 零材料受控假设与 `estimate_preview` 预览交付，不作为正式可研发布入口 |

## 聚合参考服务

第 14 个进程 `lvke-reference` 原样路由档案、模板、客户、专家、政策、统计、
行业、环境和地图实现。`finance-calc` 的七类纯计算由
`lvke-finance-model.finance_calculate` 路由；`excel-bridge` 的五类工作簿检查由
`lvke-source-files.source_inspect_workbook` 路由。旧实现模块继续作为内部库存在，
种子数据、档案索引、公式和返回业务字段不迁移。

公开面固定为 **14 个 Lvke MCP 进程、169 个工具**。完整输入/输出 schema 仍由服务端
执行；`tools/list` 只发布紧凑输入投影，大型完整 schema 通过
`lvke://schemas/<server>/<tool>/input` Resource 按需读取。紧凑投影始终保留顶层参数、
必填项、容器类型及数组元素类型，只省略可按 Resource 读取的深层结构。以下稳定别名不依赖工具名：
`finance-spec-v3`、`asset-acquisition-spec`、`review-target`、
`review-finding-disposition`、`report-preparation`、`project-planning-candidate`，以及
`project-planning-validate/confirm/prepare/create`，
均位于 `lvke://schemas/` 下。85 项旧工具迁移关系见
[`dev-docs/config/mcp-compression-migration.json`](dev-docs/config/mcp-compression-migration.json)；
第二轮 32 项迁移见
[`dev-docs/config/mcp-compression-migration-v2.json`](dev-docs/config/mcp-compression-migration-v2.json)。

统一 Resource 入口为 `lvke-feasibility-delivery.lvke_list_resources` 和
`lvke_read_resource`。`asset-acquisition` 域可分页列举并读取 spec、run、scenario
matrix、工件、十三表 package、XLSX 和 13 个 CSV；URI 中的 workspace 必须与显式
`workspace_id` 一致，二进制内容以 base64 返回。

工作簿聚合契约为 `source_inspect_workbook(workspace_id, file_id, operation,
sheet?, range?, options?)`；旧路径调用须先 `source_import_local_path`。地图聚合入口
公开 `geo_query(..., limit?)`，距离矩阵的确定性模式固定为
`haversine_with_highway_estimate`，不伪装成在线导航路线。
规划行业路由清单显式覆盖能源前缀和 `solar_power` 资产；合法 ProjectContext
不得因缺少 route 而回退到 generic 行业。

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
│       └── <domain>/         # 14 个公开领域与参考 server
├── skills/                   # Skill 源码；Codex 插件发布 15 个非前端父 Skill
├── plugins/lvke-mcp/         # Codex 插件：14 个 MCP + 15 个 Skill
├── scripts/                  # 独立性扫描与基线工具
└── tests/
```

## 构建与安装

本项目使用 **conda** 管理环境（不用 venv）。环境名 `lvke-mcp`：

```bash
cd mcp_servers
conda create -y -n lvke-mcp python=3.13
conda run -n lvke-mcp python -m pip install -e .
conda run -n lvke-mcp python -m pip install pytest
```

> conda 环境默认会把用户级 `~/.local/lib/pythonX.Y/site-packages` 挂进
> `sys.path` 且优先级高于环境自身，导致外部包 shadow 本项目的 exact-pin 依赖。
> 本环境已放置 `zzz-no-user-site.pth` 将其摘除，保证与旧 venv 同等隔离；
> 重建环境后需要重新放置，见「重建环境」。

## 单独运行一个 server（调试用）

scaffold（参考 server，运行后会通过 stdio 接收 JSON-RPC）：

```bash
cd mcp_servers
conda run -n lvke-mcp lvke-mcp-scaffold
# 或
conda run -n lvke-mcp python -m lvke_mcp.servers.scaffold.server
```

领域 server：

```bash
conda run -n lvke-mcp python -m lvke_mcp.servers.lvke_data_acquisition.server
conda run -n lvke-mcp python -m lvke_mcp.servers.lvke_finance_model.server
# 其余 server 使用 lvke_mcp.servers.<server>.server
```

## 重建环境

删除并重建后，必须重新隔离 user site，否则 `~/.local` 里的包会 shadow
本项目 exact-pin 依赖（曾导致 `starlette` 未真正装进环境）：

```bash
SP="$(conda run -n lvke-mcp python -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
printf '%s\n' 'import site, sys; sys.path[:] = [p for p in sys.path if p != getattr(site, "USER_SITE", None)]' \
  > "$SP/zzz-no-user-site.pth"
# 自检：应输出 none
conda run -n lvke-mcp python -c "import sys;print([p for p in sys.path if '/.local/lib/python' in p] or 'none')"
```

## 在 Codex 中启用

使用 `plugins/lvke-mcp/` 中的 Codex 插件发布 14 个 stdio MCP 和 15 个非前端 Skill。
完整安装、更新、范围和验证说明见
[`dev-docs/config/CODEX_USER_CONFIG.md`](dev-docs/config/CODEX_USER_CONFIG.md)。

本产品只有 MCP + Codex Skills；Tavily 是唯一联网 provider。产品不提供前端、语音、
协同办公、登录、身份、tenant、角色、RBAC、权限管理、安全审查或专业签审。

## 共用约定

1. **响应格式**：内部旧 handler 保留 `success/data/source` 包装；正式服务使用明确字段，
   并至少返回 `success/status/resource_uris/warnings/blockers/next_actions`。
2. **错误码命名**：`<server-name>.<error-tag>`，如 `mcp_lvke_archive.not_found`。
3. **日志**：使用 stderr（避免污染 stdio 协议），统一前缀 `[mcp-<server>]`。
4. **本地对象**：MCP 交换对象存放于工作区 `mcp_objects/`，ID/URI 安全校验、原子写入且
   内容不可变。
5. **测试**：外部行为基线位于 `tests/fixtures/baseline/`，协议测试工具和 stdio smoke
   驱动位于 `src/lvke_mcp/testing/`。
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
