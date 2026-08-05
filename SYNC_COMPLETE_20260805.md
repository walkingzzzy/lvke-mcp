# 用户级配置同步完成（2026-08-05）

**状态**: 已完成 — 24 个 MCP server + 29 个 skill 已同步到 Claude Code 用户级配置。

## 已同步内容

### ~/.claude.json

- **32 个 MCP server**：8 个第三方（保留原样）+ 24 个 lvke
- **lvke server 形态**：
  - `command`: `/Users/mac/Desktop/mcp_servers/.venv/bin/python`（字面值，不追踪符号链接）
  - `args`: `["-m", "lvke_mcp.servers.<module>.server"]`
  - `env.LVKE_MCP_DATA_DIR`: `/Users/mac/.lvke`
- **特殊环境变量**：
  - `lvke-data-acquisition`: 已配置 `TAVILY_MCP_URL`、`TAVILY_MCP_BEARER_TOKEN`（从 tavily-hikari 复制）、`LVKE_EXTERNAL_CORPUS_ROOT`
  - `lvke-source-files`: 已配置 `LVKE_EXTERNAL_CORPUS_ROOT=/Users/mac/Desktop/mcp_servers/docs`
  - `lvke-archive`: 已配置 `LVKE_ARCHIVE_DATA_DIR=/Users/mac/.lvke/archive_index`
- **清理完成**：无 hubei-lvke 残留、无 mcp_servers.* 旧模块前缀、无 tenant 变量

### ~/.claude/skills

- **29 个 skill** 已作为真实目录复制（非软链接）
- **关键更新已同步**：
  - `lvke-cost-drivers`: 含 P2-013 成本口径说明（`engineering capacity`）
  - `lvke-market-sizing`: 含 P1-012 locator 规范（`ad hoc spacing`）
  - 11 个 skill 的 SKILL.md 已更新，2 个新增 `agents/openai.yaml`
- **内容一致性**：29 个 skill 的 SKILL.md 与仓库逐字节一致（SHA256 校验通过）
- **软链接保留**：52 个指向 `~/.codex/skills/*` 的历史软链接未清理。名字与本仓库 29 个
  skill 无交集，可安全共存。注意它们是**两跳链接**
  （`~/.claude/skills` → `~/.codex/skills` → `hubei-lvke/skills`），
  其中 51 个最终解析到 `/Users/mac/Desktop/工程/hubei-lvke`，
  即这批用户级 skill 仍依赖旧仓库存在；旧仓库一旦删除会同时失效。

### ~/.codex/config.toml

- **未改动**（按用户要求）
- 仍保留 6 个业务 server 的旧路径（`/Users/mac/Desktop/工程/hubei-lvke`）
- TOML 可解析，所有非 mcp_servers 段完整保留

## 验证结果

### MCP server 握手测试

```
✓ 24/24 lvke server 完成 initialize 握手
  - environmental-data, excel-bridge, finance-calc, industry-research
  - lvke-archive, lvke-asset-acquisition, lvke-clients, lvke-data-acquisition
  - lvke-data-analysis, lvke-deep-research, lvke-deliverable-review, lvke-experts
  - lvke-feasibility-delivery, lvke-finance-model, lvke-finance-tables
  - lvke-knowledge-governance, lvke-project-planning, lvke-report-generation
  - lvke-source-files, lvke-templates, lvke-zero-material-delivery
  - map-geo, policy-search, statistics-cn
```

### 环境变量功能验证

| 验证项 | 结果 |
|---|---|
| Tavily provider 可用性 | `available: true`, `transport: streamable_http`（修复前为 blocked） |
| 外部语料根目录 | 解析到 `/Users/mac/Desktop/mcp_servers/docs`，6 个子目录 |
| 四个语料完整解析 | client_reports, finance_templates, hengli_acquisition, research_standards 全部可解析 |
| 档案索引 sqlite | 可查询，51 reports / 2982 chunks / 51 indicators |

## 备份位置

| 文件 | 备份路径 |
|---|---|
| ~/.claude.json | ~/.claude.json.bak.20260805232608 (71724 bytes) |
| ~/.codex/config.toml | ~/.codex/config.toml.bak.20260805232608 (4741 bytes) |

## 生效方式

**必须重启 Claude Code 会话** — MCP server 与 skills 配置只在会话启动时加载。
当前会话启动时的配置是旧路径，运行时不会自动重载。

## 关联

- 本次同步使 **USER_CONFIG_ENV_ADDITIONS.md** 中记录的 3 个 server 环境变量生效
- 修复了 **P0-002**（Tavily provider_status 可在事件循环内调用，且 provider 可用）
- 修复了 **P1-004**（外部语料配置可加载）
- 修复了 **P1-001**（档案索引可被脚本构建并查询）
- 同步了 **P1-012**（locator 归一化规则）与 **P2-013**（成本口径说明）的 skill 文档
- 方案 **MCP_DEFECT_FIX_PLAN.md** §5.4（用户级配置）全部完成

## 下次同步

增删 server 或 skill 后，按 **CLAUDE_USER_CONFIG.md** 规则重新生成即可。
建议先备份，校验 JSON/TOML 可解析，重启会话后冒烟测试握手。
