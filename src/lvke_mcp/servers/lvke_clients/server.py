"""lvke-clients MCP server 入口(stdio)。"""

from __future__ import annotations

import os
from pathlib import Path


from lvke_mcp.runtime.logging import get_logger  # noqa: E402
from lvke_mcp.runtime.responses import err, ok  # noqa: E402
from lvke_mcp.runtime.stdio import StdioServer  # noqa: E402
from lvke_mcp.servers.lvke_clients.storage import ClientStorage  # noqa: E402

SERVER_NAME = "lvke-clients"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)


def resolve_data_dir() -> Path:
    env_dir = os.environ.get("LVKE_CLIENTS_DATA_DIR", "").strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.exists():
            return p
        logger.warning("LVKE_CLIENTS_DATA_DIR 指向不存在的目录:%s,回退", env_dir)
    base = Path(__file__).resolve().parent
    data_dir = base / "data"
    if (data_dir / "clients.json").exists():
        return data_dir
    return base / "seed"


_storage: ClientStorage | None = None


def _get_storage() -> ClientStorage:
    global _storage
    if _storage is None:
        d = resolve_data_dir()
        logger.info("使用客户数据目录:%s", d)
        _storage = ClientStorage(data_dir=d)
    return _storage


def _tool_search_clients(args: dict) -> dict:
    out = _get_storage().search(
        keyword=args.get("keyword"),
        industry=args.get("industry"),
        region=args.get("region"),
        limit=int(args.get("limit") or 20),
    )
    return ok(
        {"count": len(out), "items": out},
        source=f"{SERVER_NAME}.search_clients",
    )


def _tool_get_client(args: dict) -> dict:
    client_id = args.get("client_id")
    name = args.get("name")
    if not client_id and not name:
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "client_id 与 name 至少传一个",
        )
    rec = _get_storage().get(client_id=client_id, name=name)
    if rec is None:
        return err(
            f"{SERVER_NAME}.not_found",
            f"未找到客户 client_id={client_id}, name={name}",
        )
    return ok(rec, source=f"{SERVER_NAME}.get_client")


def _tool_list_projects(args: dict) -> dict:
    client_id = args.get("client_id")
    name = args.get("name")
    if not client_id and not name:
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "client_id 与 name 至少传一个",
        )
    projects = _get_storage().projects_of(client_id=client_id, name=name)
    if projects is None:
        return err(
            f"{SERVER_NAME}.not_found",
            f"未找到客户 client_id={client_id}, name={name}",
        )
    return ok(
        {"count": len(projects), "items": projects},
        source=f"{SERVER_NAME}.list_history_projects",
    )


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="search_clients",
        description="按关键词/行业/地区检索绿科客户档案。",
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "industry": {"type": "string"},
                "region": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=_tool_search_clients,
    )
    server.register_tool(
        name="get_client",
        description="按 client_id 或 name 取单客户档案。",
        input_schema={
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "name": {"type": "string"},
            },
        },
        handler=_tool_get_client,
    )
    server.register_tool(
        name="list_history_projects",
        description="列出该客户的历史合作项目。",
        input_schema={
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "name": {"type": "string"},
            },
        },
        handler=_tool_list_projects,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
