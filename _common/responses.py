"""兼容垫片:搬移到 ``lvke_mcp.runtime.responses`` 的模块在本层只保留导入面。

MCP_INDEPENDENCE_PLAN §29.4 纵向切片期间,既有领域 server 仍从
``mcp_servers._common.responses`` 导入;切完即删本垫片。
"""

from lvke_mcp.runtime.responses import *  # noqa: F401,F403
