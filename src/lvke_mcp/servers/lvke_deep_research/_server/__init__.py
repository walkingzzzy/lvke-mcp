"""lvke-deep-research server 实现包（纯拆分，无业务逻辑变更）。

``server.py`` 保持门面：``build_server()``/``main()``/常量与稳定符号 re-export。
本包只承载被拆出的 schema、annotations、dispatch 与注册代码。

服务名/版本/logger 放在包 ``__init__`` 而不是 ``server.py``，是为了让
``dispatch.py`` 与 ``registration.py`` 能引用它们而不反向 import 门面模块
（否则 ``server.py`` → ``registration`` → ``server.py`` 形成循环）。
"""

from __future__ import annotations

from lvke_mcp.runtime.logging import get_logger

SERVER_NAME = "lvke-deep-research"
SERVER_VERSION = "0.3.0"
logger = get_logger(SERVER_NAME)

__all__ = ["SERVER_NAME", "SERVER_VERSION", "logger"]
