# Claude Code 用户级配置说明

本文档记录本仓库（独立发行版 `lvke-mcp`）的 **24 个 MCP server** 与 **29 个 skill**
如何同步到 Claude Code 的用户级配置，以及后续增删时怎么重新同步。

同步基线时间：2026-08-05。

## 涉及的两个位置

| 内容 | 用户级位置 | 形式 |
|---|---|---|
| MCP server | `~/.claude.json` → `mcpServers` | 24 条 stdio 条目 |
| Skills | `~/.claude/skills/<name>/` | 29 个**实体目录**（非软链接） |

用户级配置对所有项目生效；本仓库内的 `.claude/skills/` 是这些 skill 的唯一内容来源。

## MCP server 条目形态

`~/.claude.json` 中每个 lvke server 均为：

```json
{
  "command": "/opt/miniconda3/envs/lvke-mcp/bin/python",
  "args": ["-m", "lvke_mcp.servers.<dir_name>.server"],
  "env": { "LVKE_MCP_DATA_DIR": "/Users/mac/.lvke" },
  "type": "stdio"
}
```

要点：

- **命名两套，不可互换**：条目名取自 `server.py` 里的 `SERVER_NAME`（连字符，
  如 `lvke-finance-model`）；`args` 里的模块路径用目录名（下划线，
  如 `lvke_finance_model`）。
- **直接用 conda 环境 `lvke-mcp` 的解释器**（`/opt/miniconda3/envs/lvke-mcp/bin/python`），
  不经 `uv run`、也不经 `conda run`，因此不依赖工作目录、不需要 activate。
  本项目用 conda 管理环境，仓库内不再有 `.venv`。
- **只认 `LVKE_MCP_*` 环境变量**（`MCP_INDEPENDENCE_PLAN` §6.1）。运行时读取
  `LVKE_MCP_DATA_DIR`（缺省 `~/.lvke`）、`LVKE_MCP_CONFIG_DIR`、`LVKE_MCP_TEMP_DIR`、
  `LVKE_MCP_PROFILE`（`core`/`formal`，缺省 `core`）、`LVKE_MCP_LOG_LEVEL`，
  以及可选的 `TAVILY_API_KEY`。
- `scaffold` 是参考 demo，**故意不注册**。

## 已注册的 24 个 server

正式服务（13）：`lvke-project-planning`、`lvke-source-files`、`lvke-data-acquisition`、
`lvke-data-analysis`、`lvke-finance-model`、`lvke-deep-research`、`lvke-finance-tables`、
`lvke-report-generation`、`lvke-asset-acquisition`、`lvke-deliverable-review`、
`lvke-knowledge-governance`、`lvke-feasibility-delivery`、`lvke-zero-material-delivery`

支撑服务（11）：`finance-calc`、`excel-bridge`、`lvke-archive`、`lvke-templates`、
`lvke-clients`、`lvke-experts`、`policy-search`、`statistics-cn`、`industry-research`、
`environmental-data`、`map-geo`

## 与第三方 server 共存

`~/.claude.json` 里另有 8 个与本仓库无关的 server：`context7`、`playwright`、`git`、
`node_repl`、`sequential-thinking`、`fathomsearch`、`tavily-hikari`、`mcp-web-search`。
**同步脚本必须原样保留它们**，只增改 lvke 这 24 条。

## Skills 同步方式

按「目录内存在 `SKILL.md`」筛选 `.claude/skills/*`，整份 `cp -R` 到
`~/.claude/skills/<name>/`。复制前先删掉同名软链接，否则会写穿到软链接目标里。

> **背景**：同步前 `~/.claude/skills/` 是 78 条指向 `~/.codex/skills/lvke-dev-*`、
> `lvke-product-*` 的符号链接，且抽查目标目录内**没有 `SKILL.md`**（空目录），
> 即那批用户级 skill 实际失效。Codex 侧命名带前缀且有重复段，与本仓库名字对不上，
> 按名字比较交集为 0 —— 容易误判成「两套不同的 skill」。

`.claude/skills-archive/` 是历史归档，不参与同步。

## 重新同步

server 或 skill 增删后，按上面两节的规则重新生成即可。建议顺序：

1. 备份：`cp ~/.claude.json ~/.claude.json.bak.$(date +%Y%m%d%H%M%S)`
2. 重写 lvke 的 24 条（保留第三方条目），并校验 JSON 可解析
3. 重新复制 skill 目录
4. **重启 Claude Code 会话** —— MCP server 与 skills 只在会话启动时加载

## 验证

冒烟检查单个 server 能否完成 MCP 握手：

```bash
cd /Users/mac/Desktop/mcp_servers
LVKE_MCP_DATA_DIR="$HOME/.lvke" /opt/miniconda3/envs/lvke-mcp/bin/python - <<'PY'
import json, subprocess, sys
req = {"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-06-18","capabilities":{},
                 "clientInfo":{"name":"smoke","version":"0"}}}
p = subprocess.Popen([sys.executable,"-m","lvke_mcp.servers.lvke_finance_model.server"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True)
out,_ = p.communicate(json.dumps(req)+"\n", timeout=25)
print(json.loads(out.splitlines()[0])["result"]["serverInfo"])
PY
```

⚠️ **不要用 shell 的 `timeout`**：本机（macOS/zsh）没有该命令，管道会静默返回空输出
且退出码为 0，看起来像 server 无响应，实则是 `timeout: command not found`。
需要 shell 超时请 `brew install coreutils` 用 `gtimeout`。

已验证：`lvke-feasibility-delivery` v0.1.0、`lvke-finance-model` v0.3.0 均可完成
`initialize` 握手。

## 历史坑位

2026-08-05 同步前，用户级里的 23 条 lvke 条目仍是**独立化之前**的旧配置：

```json
{ "command": "uv",
  "args": ["run","--directory","/Users/mac/Desktop/工程/hubei-lvke",
           "python","-m","mcp_servers.statistics_cn.server"] }
```

问题有三：指向旧仓库路径、模块前缀是已删除的 `mcp_servers.*`、并带
`LVKE_MCP_TENANT_ID` / `LVKE_API_PROFILE` 等已被 commit `88984ab` 清除的 tenant 残留。
这些条目全部无法启动，且 `lvke-feasibility-delivery` 整条缺失。改写后已无残留。
