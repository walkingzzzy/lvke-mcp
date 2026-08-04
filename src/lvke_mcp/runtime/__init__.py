"""lvke_mcp.runtime —— MCP 自有的通用运行时。

收敛原 ``mcp_servers/_common`` 中的通用能力(config / workspace / storage /
resource / job / transport 等),作为各领域 server 的公共底座。MCP 发行版
自带此层,不读取宿主应用配置或文件系统。
"""
