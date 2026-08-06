# 用户级配置环境变量补充（5.4）

**状态**: 部分完成 — 目标 3 个 server 在当前 `~/.claude.json` 中不存在，已记录所需配置供手动同步时使用。

## 背景

方案 5.4 要求为 3 个 lvke server 补充环境变量以修复 P0-002（Tavily）、P1-001（档案索引）、P1-004（外部语料）。
但当前 `~/.claude.json` 只有 14 个 server，其中 6 个已用新仓库路径（`environmental-data`、`statistics-cn`、
`excel-bridge`、`industry-research`、`policy-search`、`finance-calc`），均非本轮目标 server。

记忆文档 `claude-user-level-mcp-sync.md` 提到 24 个 lvke server 应存在于用户级配置，但实际缺失。
完整同步属于独立的用户级配置维护任务，不在本次 20 缺陷修复范围内（方案 §II 明确「先出详细方案，再按域实施」，
且「禁区」中未授权新增或删除已有 MCP server 注册）。

## 所需配置

当 24 个 lvke server 被同步进 `~/.claude.json` 后，为以下 3 个 server 的 `env` 对象补充环境变量：

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

## `~/.codex/config.toml` 同步

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

- `CLAUDE_USER_CONFIG.md` — 24 个 lvke server 的完整注册规则
- `claude-user-level-mcp-sync.md` (memory) — 历史旧配置清除过程
- `MCP_DEFECT_FIX_PLAN.md` §5.4 — 本条目原始需求
