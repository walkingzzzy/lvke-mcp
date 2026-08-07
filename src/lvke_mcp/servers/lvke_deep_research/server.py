"""lvke-deep-research MCP server 入口(stdio)。

把 Lvke 的 Deep Research 证据与工件流程封装成 Agent 可调用的 MCP 工具。

设计要点（对齐《Claude_Code应用化开发方案》§6.1 与深度审查 §4.4）：

- 当前 Agent 负责搜索、研究判断与文字；MCP 负责 brief、来源 locator、
  lineage、partial 状态和 research package。``dr_start`` 不配置或调用第二个
  LLM；Agent 完成研究后用 ``dr_submit`` 固化带引用的发现。
- **硬规则（只收紧不放宽）**：
  · 预算耗尽→partial，绝不假 done（引擎已保证，本层不覆盖 status）。
  · dr_submit 只负责保存 partial；dr_confirm_quality 单独保存质量指标和资料限制
    决策，不把研究文本伪装成独立完成事实。
  · checkpoint 只在真实存在时进资源清单，绝不给死链 URI。
  · 密钥走 env，不进产物。

启动方式::

    python -m lvke_mcp.servers.lvke_deep_research.server
"""

from __future__ import annotations

import json
from typing import Any

from mcp import types

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.runtime.responses import err, ok
from lvke_mcp.runtime.storage import paginate_resource_entries
from lvke_mcp.domains.research import application as package_service

from ._server import SERVER_NAME as SERVER_NAME, SERVER_VERSION as SERVER_VERSION, logger as logger
from ._server.registration import register_all
from ._server.dispatch import (
    _ok_env,
    _err_env,
    _ws,
    _tool_dr_start,
    _tool_dr_status,
    _tool_dr_cancel,
    _tool_dr_get_report,
    _tool_dr_get_evidence,
    _read_scoped_resource,
    _list_scoped_resources,
)


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    register_all(server)
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
