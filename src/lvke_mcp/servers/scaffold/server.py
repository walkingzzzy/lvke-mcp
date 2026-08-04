"""scaffold MCP server (lvke-mcp 参考实现)。

这是 lvke_mcp 发行版的参考 server:直接 import ``lvke_mcp.runtime.*``,
不含任何 ``sys.path`` 注入或对宿主应用的依赖。复制本文件到
``lvke_mcp/servers/<new-name>/server.py`` 并修改:

1. ``SERVER_NAME`` —— 改成新 server 的短名
2. ``SERVER_VERSION`` —— 起始版本
3. 注册具体工具(``register_tool`` 调用)
"""

from __future__ import annotations

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.responses import err, ok
from lvke_mcp.runtime.transport import OfficialStdioServer

SERVER_NAME = "scaffold"
SERVER_VERSION = "0.1.0"

logger = get_logger(SERVER_NAME)


def _tool_echo(args: dict) -> dict:
    """示例工具:把传入的 ``message`` 原样回显。"""

    msg = args.get("message")
    if not isinstance(msg, str):
        return err(f"{SERVER_NAME}.invalid_argument", "message 必须是字符串")
    return ok({"echoed": msg}, source=f"{SERVER_NAME}.echo")


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="echo",
        description="示例工具:原样回显 message 参数。",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "任意字符串"},
            },
            "required": ["message"],
        },
        handler=_tool_echo,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
