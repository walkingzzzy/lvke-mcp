"""lvke-templates MCP server 入口(stdio)。

启动方式::

    python -m lvke_mcp.servers.lvke_templates.server
"""

from __future__ import annotations

from pathlib import Path


from lvke_mcp.runtime.logging import get_logger  # noqa: E402
from lvke_mcp.runtime.responses import err, ok  # noqa: E402
from lvke_mcp.runtime.stdio import StdioServer  # noqa: E402
from lvke_mcp.domains.templates.catalog import (  # noqa: E402
    TEMPLATES,
    filter_by_category,
    list_categories,
)
from lvke_mcp.servers.lvke_templates.filler import fill_template  # noqa: E402

SERVER_NAME = "lvke-templates"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)


def _tool_list_templates(args: dict) -> dict:
    category = args.get("category")
    if category is not None and not isinstance(category, str):
        return err(f"{SERVER_NAME}.invalid_argument", "category 必须是字符串或省略")
    items = filter_by_category(category)
    return ok(
        {
            "count": len(items),
            "categories": list_categories(),
            "items": [
                {
                    "template_id": tpl["template_id"],
                    "category": tpl["category"],
                    "name": tpl["name"],
                    "description": tpl["description"],
                    "column_count": len(tpl["columns"]),
                    "predefined_rows": len(tpl.get("rows") or []),
                }
                for tpl in items
            ],
        },
        source=f"{SERVER_NAME}.list_templates",
    )


def _tool_get_template(args: dict) -> dict:
    tid = args.get("template_id")
    if not isinstance(tid, str) or not tid:
        return err(f"{SERVER_NAME}.invalid_argument", "template_id 必须是非空字符串")
    tpl = TEMPLATES.get(tid)
    if tpl is None:
        return err(
            f"{SERVER_NAME}.not_found",
            f"未找到 template_id={tid}",
            detail={"available": sorted(TEMPLATES.keys())},
        )
    return ok(tpl, source=f"{SERVER_NAME}.get_template")


def _tool_fill_template(args: dict) -> dict:
    tid = args.get("template_id")
    data = args.get("data")
    if not isinstance(tid, str) or not tid:
        return err(f"{SERVER_NAME}.invalid_argument", "template_id 必须是非空字符串")
    if not isinstance(data, dict):
        return err(f"{SERVER_NAME}.invalid_argument", "data 必须是对象")
    tpl = TEMPLATES.get(tid)
    if tpl is None:
        return err(
            f"{SERVER_NAME}.not_found",
            f"未找到 template_id={tid}",
        )
    result = fill_template(tpl, data)
    return ok(
        {
            "template_id": tid,
            "markdown": result.markdown,
            "warnings": result.warnings,
            "rows_used": result.rows_used,
        },
        source=f"{SERVER_NAME}.fill_template",
    )


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="list_templates",
        description="按分类列出所有制式表格模板。category 省略时返回全部。",
        input_schema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "可选分类:investment-estimation / financing / income-statement / cashflow / sensitivity / key-indicators / risk-matrix / regulatory-schedule",
                },
            },
        },
        handler=_tool_list_templates,
    )
    server.register_tool(
        name="get_template",
        description="按 template_id 拿单张模板的结构定义(列 / 预定义行 / 口径)。",
        input_schema={
            "type": "object",
            "properties": {"template_id": {"type": "string"}},
            "required": ["template_id"],
        },
        handler=_tool_get_template,
    )
    server.register_tool(
        name="fill_template",
        description=(
            "把业务数据填入模板,返回填充后的 markdown 表 + 校验告警。"
            "静态行模板的 data 形如 {<row.key>: {<col.key>: value}};"
            "动态行模板的 data 形如 {rows: [{<col.key>: value}, ...]}。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
                "data": {"type": "object"},
            },
            "required": ["template_id", "data"],
        },
        handler=_tool_fill_template,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
