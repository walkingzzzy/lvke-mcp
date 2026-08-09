# Codex 用户级部署说明

本项目通过 `lvke-mcp` Codex 插件发布 **14 个 MCP server** 和 **14 个 Codex Skill**。`lvke-frontend` 与 `lvke-desktop` 不属于产品能力，不进入插件。

## 产品边界

- 交付形态是 MCP + Codex Skills，没有前端。
- Tavily 是唯一联网 provider，不要求 Exa、Firecrawl、ddgs 或其他 provider。
- 不提供语音或协同办公。
- 不提供登录、身份、tenant、角色、RBAC、权限管理、安全审查或专业签审。
- `workspace_id` 仅用于本地业务对象的数据命名空间，不是权限边界。

## 插件内容

- 插件源码：`plugins/lvke-mcp/`
- MCP 配置：`plugins/lvke-mcp/.mcp.json`
- Skill 源码：仓库根 `skills/`
- 用户级 marketplace：`~/.agents/plugins/marketplace.json`
- 用户级插件入口：`~/plugins/lvke-mcp`

插件中的 14 个 MCP 名称来自 `src/lvke_mcp/testing/server_manifest.py`。每个进程使用 `/opt/miniconda3/envs/lvke-mcp/bin/python -m lvke_mcp.servers.<module>.server` 启动。

## 安装与更新

1. 验证插件：

   ```bash
   python3 /Users/mac/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/lvke-mcp
   ```

2. 从 personal marketplace 安装或刷新：

   ```bash
   codex plugin add lvke-mcp@personal
   ```

3. 重新启动 Codex 任务，使新的 MCP 和 Skills 进入会话。

插件更新时先运行 `python3 scripts/build_codex_plugin.py`。构建器只发布 14 个父 Skill，并把嵌套专家资料打包为普通 `REFERENCE.md`，避免 Codex 将其重复注册为额外 Skill。之后运行 plugin-creator 的 cachebuster 工具并重新安装。

## Tavily 凭据

`lvke-data-acquisition` 只连接 Tavily。插件保存非敏感的 MCP URL，Bearer token 只从以下用户级位置之一读取：

- 环境变量 `TAVILY_MCP_BEARER_TOKEN`
- `/Users/mac/.lvke/config/tavily_mcp_bearer_token`

凭据文件可保存原始 token 或完整的 `Bearer ...` 值；运行时会规范化前缀。token 不得写入仓库、Skill、插件清单、日志或测试报告。

## 验证

```bash
codex plugin list
codex mcp list
```

验收必须看到 14 个 `lvke-*` MCP 和 14 个非前端 Lvke Skill。MCP 源码测试通过不等于 Codex 已发现这些能力；必须在新任务中完成一次真实 `tools/list` 和代表性业务调用。
