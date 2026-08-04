"""兼容垫片(MCP_INDEPENDENCE_PLAN §696 过渡期)。

实现已搬移至 ``lvke_mcp.servers.lvke_source_files.service``;本模块只保留
导入面,供 lvke_data_acquisition 等仍从 ``mcp_servers.lvke_source_files``
解析的消费方使用。
"""

from lvke_mcp.servers.lvke_source_files.service import *  # noqa: F401,F403
