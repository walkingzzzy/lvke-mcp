"""兼容垫片(MCP_INDEPENDENCE_PLAN §696 过渡期)。

实现已搬移至 ``lvke_mcp.servers.lvke_source_files.external_corpora``;本模块
只保留导入面,供 source-files service 在搬移完成前仍从 ``mcp_servers._common``
解析。
"""

from lvke_mcp.servers.lvke_source_files.external_corpora import *  # noqa: F401,F403
