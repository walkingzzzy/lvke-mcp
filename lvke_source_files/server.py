"""兼容垫片(MCP_INDEPENDENCE_PLAN §696 过渡期)。

实现已搬移至 ``lvke_mcp.servers.lvke_source_files.server``;本模块只保留
导入面,供 protocol_testkit 等仍引用 ``mcp_servers.lvke_source_files.server``
的入口解析。
"""

from lvke_mcp.servers.lvke_source_files.server import *  # noqa: F401,F403
