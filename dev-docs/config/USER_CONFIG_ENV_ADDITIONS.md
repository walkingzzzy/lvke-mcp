# 用户级配置环境变量补充（5.4）

**状态**: 历史文档 — 记录了 Wave 2 后 24 服务拓扑的环境变量需求。当前为 14 服务；具体配置见 `CLAUDE_USER_CONFIG.md`（2026-08-08）。

## 背景

方案 5.4 要求为 3 个 lvke server 补充环境变量以修复 P0-002（Tavily）、P1-001（档案索引）、P1-004（外部语料）。
当前 14 服务拓扑中 `lvke-archive` 已合并至 `lvke-reference`。

## 所需配置（14 服务拓扑）

当 14 个 lvke server 同步进 `~/.claude.json` 后，为以下 server 的 `env` 对象补充环境变量：

### `lvke-data-acquisition`

```json
{
  "command": "/opt/miniconda3/envs/lvke-mcp/bin/python",
  "args": ["-m", "lvke_mcp.servers.lvke_data_acquisition.server"],
  "env": {
    "LVKE_MCP_DATA_DIR": "/Users/mac/.lvke",
    "TAVILY_MCP_URL": "https://tavily.ivanli.cc/mcp",
    "TAVILY_MCP_BEARER_TOKEN": "<从 tavily-hikari 的 headers.Authorization 取值>",
    "LVKE_EXTERNAL_CORPUS_ROOT": "/Users/mac/Desktop/mcp_servers/docs"
  },
  "type": "stdio"
}
```

- `TAVILY_MCP_URL`: 从当前 `~/.claude.json` 的 `mcpServers.tavily-hikari.url` 取值（当前为 `https://tavily.ivanli.cc/mcp`）
- `TAVILY_MCP_BEARER_TOKEN`: 从 `mcpServers.tavily-hikari.headers.Authorization` 取值（当前已设置，形如 `Bearer tvly-...`）
- `LVKE_EXTERNAL_CORPUS_ROOT`: 指向本仓库 `docs/` 目录的绝对路径

### `lvke-source-files`

```json
{
  "command": "/opt/miniconda3/envs/lvke-mcp/bin/python",
  "args": ["-m", "lvke_mcp.servers.lvke_source_files.server"],
  "env": {
    "LVKE_MCP_DATA_DIR": "/Users/mac/.lvke",
    "LVKE_EXTERNAL_CORPUS_ROOT": "/Users/mac/Desktop/mcp_servers/docs"
  },
  "type": "stdio"
}
```

### `lvke-archive`

```json
{
  "command": "/opt/miniconda3/envs/lvke-mcp/bin/python",
  "args": ["-m", "lvke_mcp.servers.lvke_archive.server"],
  "env": {
    "LVKE_MCP_DATA_DIR": "/Users/mac/.lvke",
    "LVKE_ARCHIVE_DATA_DIR": "/Users/mac/.lvke/archive_index"
  },
  "type": "stdio"
}
```

## 产出目录配置说明（LVKE_DELIVERABLE_DIR）

研报修订、十三表 XLSX/CSV、DOCX 工件的输出路径由 `deliverable_root()` 决定，
三个分支按优先级依次：

| 优先级 | 条件 | 实际产出根 |
|---|---|---|
| 1 | `LVKE_DELIVERABLE_DIR` 已设置 | 直接使用该路径 |
| 2 | `LVKE_MCP_DATA_DIR` 已设置（而 `LVKE_DELIVERABLE_DIR` 未设） | `<LVKE_MCP_DATA_DIR>/lvke产出` |
| 3 | 两者均未设置 | 仓库根 `/Users/mac/Desktop/mcp_servers/lvke产出` |

当前 `~/.claude.json` 配置了 `LVKE_MCP_DATA_DIR=/Users/mac/.lvke`，所以默认产出落到
`/Users/mac/.lvke/lvke产出`（分支 2）。

如需明确落在仓库根（便于复核入库），在相应 server 的 `env` 对象中添加：

```json
"LVKE_DELIVERABLE_DIR": "/Users/mac/Desktop/mcp_servers/lvke产出"
```

代码来源：`src/lvke_mcp/runtime/workspace.py:44` `deliverable_root()`。

## 来源导入路径选择

导入项目原始资料有两条路径，选择原则如下：

| 场景 | 工具 | 前提 |
|---|---|---|
| **新项目**，资料在本机某目录 | `source_import_local_path` | 设置 `LVKE_SOURCE_IMPORT_ROOTS` 指向资料目录 |
| **预置样本项目**（崇阳香苑、潜山森林公园、恒立酒店），资料已在 `LVKE_EXTERNAL_CORPUS_ROOT` 下 | `source_external_corpus_resolve` | 项目名称必须预先登记在 `external_corpora.v1.json` |

`source_external_corpus_resolve` 是**预置样本路由**，不是通用新项目入口。新项目**不应修改** `external_corpora.v1.json`，应直接使用 `source_import_local_path`。

`external_corpus_unavailable` 错误的 `detail` 字段（代码 `d8f8f81` 后新增）会说明具体原因：

- `root_not_configured` — 检查 `LVKE_EXTERNAL_CORPUS_ROOT` 环境变量
- `project_not_registered` — 改用 `source_import_local_path`（推荐）或将项目加入 manifest
- `corpus_missing` — 检查 `LVKE_EXTERNAL_CORPUS_ROOT` 下的目录和 marker 文件



`~/.codex/config.toml` 当前有 6 个 lvke 相关 server（`environmental-data`、`excel-bridge`、
`finance-calc`、`industry-research`、`policy-search`、`statistics-cn`），均使用旧形式
`command = "uv"` + `args = ["run", "--directory", "/Users/mac/Desktop/工程/hubei-lvke", ...]`。

当同步这 6 个到新仓库路径时，改为：

```toml
[mcp_servers.environmental-data]
command = "/opt/miniconda3/envs/lvke-mcp/bin/python"
args = ["-m", "lvke_mcp.servers.environmental_data.server"]
type = "stdio"

[mcp_servers.environmental-data.env]
LVKE_MCP_DATA_DIR = "/Users/mac/.lvke"
```

对于 `lvke-data-acquisition`、`lvke-source-files`、`lvke-archive`，按上面的 env 条目补全。

## 生效时机

**改完必须重启 Claude Code 会话** — MCP server 配置只在会话启动时加载。

## 验证

冒烟检查单个 server（需先同步到用户级配置）：

```bash
cd /Users/mac/Desktop/mcp_servers
LVKE_MCP_DATA_DIR="$HOME/.lvke" \
LVKE_EXTERNAL_CORPUS_ROOT="$(pwd)/docs" \
/opt/miniconda3/envs/lvke-mcp/bin/python - <<'PY'
import json, subprocess, sys
req = {"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-06-18","capabilities":{},
                 "clientInfo":{"name":"smoke","version":"0"}}}
p = subprocess.Popen([sys.executable,"-m","lvke_mcp.servers.lvke_source_files.server"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True)
out,_ = p.communicate(json.dumps(req)+"\n", timeout=25)
print(json.loads(out.splitlines()[0])["result"]["serverInfo"])
PY
```

## 凭据安全

Tavily bearer token 已存在于 `~/.claude.json` 的 `tavily-hikari` 条目中，本次配置只是将同一凭据复制到
`lvke-data-acquisition.env.TAVILY_MCP_BEARER_TOKEN`。**凭据不写入仓库、日志或测试报告**（方案 §II 禁区）。

## 参考

- `CLAUDE_USER_CONFIG.md` — 14 个 lvke server 的完整注册规则（2026-08-08 基线）
- `claude-user-level-mcp-sync.md` (memory) — 历史旧配置清除过程
- `MCP_DEFECT_FIX_PLAN.md` §5.4 — 本条目原始需求
