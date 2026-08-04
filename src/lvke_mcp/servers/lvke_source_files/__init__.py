"""Governed source-file MCP service (lvke_mcp-owned, MCP_INDEPENDENCE_PLAN §29.4).

直接搬移自 ``mcp_servers/lvke_source_files``:server.py / service.py /
backend.py 保持领域逻辑不变,仅改写导入路径为 ``lvke_mcp.*``;external_corpora
的仓库根路径基准替换为 MCP 配置目录基准(部署路径调整,非领域逻辑改动)。
"""
