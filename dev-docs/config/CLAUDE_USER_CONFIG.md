# Claude Code 用户级配置说明

本文档记录本仓库（独立发行版 `lvke-mcp`）的 **14 个 MCP server** 与 **16 个 skill**
如何同步到 Claude Code 的用户级配置，以及后续增删时怎么重新同步。

同步基线时间：2026-08-08（更新至 Wave 4 后的压缩拓扑）。

## 涉及的两个位置

| 内容 | 用户级位置 | 形式 |
|---|---|---|
| MCP server | `~/.claude.json` → `mcpServers` | 14 条 stdio 条目 |
| Skills | `~/.claude/skills/<name>/` | 16 个**实体目录**（非软链接） |

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

## 两个 server 需要额外环境变量（2026-08-08 补齐）

只有这两个 server 的 `env` 超出 `LVKE_MCP_DATA_DIR`。缺任何一项都会让对应链路
在运行时被门禁挡住，而**不是**报配置错误 —— 所以症状看起来像功能缺陷。

### `lvke-data-acquisition`

| 变量 | 值 | 缺失后果 |
|---|---|---|
| `TAVILY_MCP_URL` | `https://tavily.ivanli.cc/mcp` | `provider_configuration_missing` |
| `TAVILY_MCP_BEARER_TOKEN` | 取自 `mcpServers.tavily-hikari.headers.Authorization` | 同上 |
| `LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET` | 非空随机串（`secrets.token_urlsafe(32)`） | `data_fetch` 的 `auto`/`tavily` 路径全部返回 `trusted_extract_local_config_gap`，**无法固化正式来源快照** |
| `LVKE_MCP_TRUSTED_HTTPS_PRIVATE_IP_HOSTS` | `www.whxinzhou.gov.cn,www.gov.cn` | 代理 fake-ip 环境下 `direct_http` 抓取全部被 `proxy_fake_ip_resolution` 拦截 |

receipt secret 是**对称密钥**，签发（`_trusted_tavily_extract`）与校验
（`data_import_external_snapshot`）都在同一进程读同一个变量，因此只要非空且
两侧同值即可；换值会让此前签发的 receipt 失效。

`LVKE_MCP_TRUSTED_HTTPS_PRIVATE_IP_HOSTS` 是**最小授权白名单**：逗号分隔、
**仅 HTTPS 生效**、逐域名精确匹配。它不会放行云 metadata 端点（那条是无条件
拒绝）。只在确实需要 `direct_http` 原始 HTML 时才加域名；正式固化走
`extraction_provider=tavily` 不需要它。

### `lvke-source-files`

| 变量 | 值 | 缺失后果 |
|---|---|---|
| `LVKE_SOURCE_IMPORT_ROOTS` | `/Users/mac/Desktop/lvke-资料` | `source_import_local_path` 报 `local_source_outside_roots` |
| `LVKE_EXTERNAL_CORPUS_ROOT` | `/Users/mac/Desktop/mcp_servers/docs` | `source_external_corpus_resolve` 报 `root_not_configured` |

两者任一非空即可满足 `source_import_local_path`（`LVKE_SOURCE_IMPORT_ROOTS`
非空时提前返回，不读 manifest）。新项目走前者，无需在
`external_corpora.v1.json` 登记项目名。

**不要把 `LVKE_SOURCE_IMPORT_ROOTS` 指向仓库根**：`lvke产出/` 在仓库根内，
会让产出物可被当作输入证据重新导入，污染证据链；仓库根还有 `pkcs11.txt`。
输入目录与产出目录必须物理分离，这也是导入白名单单独建 `~/Desktop/lvke-资料`
的原因。

## Codex 宿主是**另一份**配置（2026-08-08）

同一台机器上两个宿主各读各的文件，`~/.claude.json` 的配置**不会**影响 Codex：

| 宿主 | lvke server 来源 | env 来源 |
|---|---|---|
| Claude Code | `~/.claude.json` 的 `mcpServers` | 同文件的 `env` 对象 |
| Codex | plugin `lvke-mcp@personal` | plugin 内的 `.mcp.json` |

Codex 的 plugin 缓存在
`~/.codex/plugins/cache/personal/lvke-mcp/<version>/`，是仓库
`plugins/lvke-mcp/` 的快照副本。**改配置要改仓库源
`plugins/lvke-mcp/.mcp.json`**，否则重装 plugin 时改动会被覆盖回去。改完把
`.mcp.json` 同步进当前缓存目录即可让运行中的 Codex 生效（重启后）。

`scripts/build_codex_plugin.py` **只重建 skills，不碰 `.mcp.json`** —— 跑它不会
同步 env 配置，这一步必须单独做。

### 密钥用 `*_FILE` 间接持有

`plugins/lvke-mcp/.mcp.json` 随仓库分发，**不得内嵌密钥值**。两个密钥都走文件：

| 环境变量 | 文件 | 权限 |
|---|---|---|
| `TAVILY_MCP_BEARER_TOKEN_FILE` | `~/.lvke/config/tavily_mcp_bearer_token` | 0600 |
| `LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET_FILE` | `~/.lvke/config/external_extract_receipt_secret` | 0600 |

读取实现是 `runtime/config.py` 的 `external_receipt_secret()`：先读直接 env，
为空再读 `*_FILE`，两者都无则返回空字节（调用方须判为本地配置缺口）。它住在
`runtime` 而非 `servers/`，因为签发方在 `servers/`、探测方在 `domains/`，
而 `domains -> servers` 是禁止的层边 —— 两侧必须共用同一实现，否则
`provider_status` 会把 `*_FILE` 部署误报成未配置，与 `data_fetch` 的真实能力矛盾。

两个宿主必须持有**同值**密钥，否则一侧签发的 receipt 在另一侧验不过。当前两侧
都指向同一个 43 字符密钥（Claude 走直接 env，Codex 走 `*_FILE`）。

## 已注册的 14 个 server

所有公开服务（来源：`src/lvke_mcp/testing/server_manifest.py`）：

`lvke-asset-acquisition`、`lvke-data-acquisition`、`lvke-data-analysis`、`lvke-deep-research`、
`lvke-deliverable-review`、`lvke-feasibility-delivery`、`lvke-finance-model`、`lvke-finance-tables`、
`lvke-knowledge-governance`、`lvke-project-planning`、`lvke-reference`、`lvke-report-generation`、
`lvke-source-files`、`lvke-zero-material-delivery`

**已移除**（Wave 3/4 压缩，功能已合并至主服务）：`finance-calc`、`excel-bridge`、`lvke-archive`、
`lvke-templates`、`lvke-clients`、`lvke-experts`、`policy-search`、`statistics-cn`、`industry-research`、
`environmental-data`、`map-geo`

## 与第三方 server 共存

`~/.claude.json` 里另有若干与本仓库无关的 server（如 `git`、`node_repl`、`sequential-thinking`、
`tavily-hikari`、`playwright` 等）。**同步脚本必须原样保留它们**，只增改 lvke 这 14 条。

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
2. 重写 lvke 的 14 条（保留第三方条目），并校验 JSON 可解析
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

2026-08-05 同步前，用户级里的条目仍是**独立化之前**的旧配置（旧仓库路径、已删模块前缀 `mcp_servers.*`、
tenant 残留变量）。2026-08-08 更新后已移除 Wave 3/4 压缩掉的 10 个支撑服务，留下当前拓扑的 14 个。
