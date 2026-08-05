"""excel-bridge MCP server 入口(stdio)。"""

from __future__ import annotations

from pathlib import Path


from lvke_mcp.runtime.logging import get_logger  # noqa: E402
from lvke_mcp.runtime.responses import err, ok  # noqa: E402
from lvke_mcp.runtime.stdio import StdioServer  # noqa: E402
from lvke_mcp.adapters.finance_tables_repository import get_xlsx_package  # noqa: E402
from lvke_mcp.adapters.spreadsheets.reader import pick_backend  # noqa: E402
from lvke_mcp.adapters.spreadsheets.formulas import FormulaBackend, FormulaBackendUnavailable  # noqa: E402

SERVER_NAME = "excel-bridge"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)

_backend = None


def _get_backend():
    global _backend
    if _backend is None:
        _backend = pick_backend()
        logger.info("excel-bridge backend = %s", _backend.name)
    return _backend


def _resolve_path(path: str | None) -> Path | None:
    if not isinstance(path, str) or not path.strip():
        return None
    p = Path(path).expanduser()
    return p if p.exists() and p.is_file() else None


def _resolve_source(args: dict) -> tuple[Path | None, dict, dict | None]:
    """Resolve a local file or a workspace-scoped finance-tables Resource."""

    raw_path = args.get("path")
    raw_uri = args.get("resource_uri")
    has_path = isinstance(raw_path, str) and bool(raw_path.strip())
    has_uri = isinstance(raw_uri, str) and bool(raw_uri.strip())
    if has_path == has_uri:
        return None, {}, err(
            f"{SERVER_NAME}.source_invalid",
            "path 与 resource_uri 必须且只能提供一个",
            field_errors={"source": "exactly_one_required"},
        )
    if has_path:
        path = _resolve_path(raw_path)
        if path is None:
            return None, {}, err(
                f"{SERVER_NAME}.file_not_found",
                "path 必须指向存在的 xls/xlsx 文件",
            )
        return path, {"source_type": "path", "filename": path.name}, None

    workspace_id = str(args.get("workspace_id") or "").strip()
    if not workspace_id:
        return None, {}, err(
            f"{SERVER_NAME}.workspace_id_required",
            "resource_uri 模式必须提供 workspace_id",
            field_errors={"workspace_id": "required"},
        )
    uri = str(raw_uri).strip()
    prefix = "lvke://finance-tables/workspaces/"
    parts = uri[len(prefix):].split("/") if uri.startswith(prefix) else []
    if len(parts) != 4 or parts[1] != "packages" or parts[3] != "xlsx":
        return None, {}, err(
            f"{SERVER_NAME}.resource_uri_invalid",
            "resource_uri 必须是 finance-tables 的 XLSX Resource",
        )
    uri_workspace, package_id = parts[0], parts[2]
    if uri_workspace != workspace_id:
        return None, {}, err(
            f"{SERVER_NAME}.resource_scope_mismatch",
            "Resource 不属于当前工作区",
        )
    resolved = get_xlsx_package(workspace_id, package_id, uri)
    if resolved is None:
        return None, {}, err(
            f"{SERVER_NAME}.resource_not_found",
            "Resource 不存在或不属于当前工作区",
        )
    record, path, canonical_uri = resolved
    payload = record.get("payload") or {}
    return path, {
        "source_type": "resource",
        "resource_uri": canonical_uri,
        "workspace_id": workspace_id,
        "package_id": package_id,
        "run_id": payload.get("run_id"),
        "lineage": {
            "package_content_hash": record.get("content_hash"),
            "table_bundle_hash": payload.get("table_bundle_hash"),
            "template_version": payload.get("template_version"),
        },
    }, None


def _source_properties() -> dict:
    return {
        "path": {"type": "string", "minLength": 1},
        "resource_uri": {"type": "string", "minLength": 1},
        "workspace_id": {"type": "string", "minLength": 1},
    }


def _tool_list_sheets(args: dict) -> dict:
    p, source, failure = _resolve_source(args)
    if failure is not None:
        return failure
    assert p is not None
    try:
        sheets = _get_backend().list_sheets(p)
    except Exception:  # noqa: BLE001
        logger.exception("list_sheets 解析失败")
        return err(
            f"{SERVER_NAME}.read_failed",
            "无法解析 xlsx 文件",
        )
    return ok(
        {**source, "sheets": sheets, "backend": _get_backend().name},
        source=f"{SERVER_NAME}.list_sheets",
    )


def _tool_read_xlsx(args: dict) -> dict:
    p, source, failure = _resolve_source(args)
    if failure is not None:
        return failure
    assert p is not None
    sheet = args.get("sheet")
    max_rows = int(args.get("max_rows") or 200)
    max_cols = int(args.get("max_cols") or 50)
    try:
        result = _get_backend().read_sheet(
            p,
            sheet if isinstance(sheet, str) and sheet else None,
            max_rows,
            max_cols,
        )
    except KeyError:
        return err(f"{SERVER_NAME}.sheet_not_found", f"sheet={sheet} 不存在")
    except Exception:  # noqa: BLE001
        logger.exception("read_xlsx 读取失败")
        return err(
            f"{SERVER_NAME}.read_failed",
            "读取 xlsx 失败",
        )
    return ok(
        {
            **source,
            "sheet": result.sheet,
            "rows": result.rows,
            "row_count": result.row_count,
            "col_count": result.col_count,
            "backend": result.backend,
            "max_rows_applied": max_rows,
            "max_cols_applied": max_cols,
        },
        source=f"{SERVER_NAME}.read_xlsx",
    )


def _tool_read_formulas(args: dict) -> dict:
    p, source, failure = _resolve_source(args)
    if failure is not None:
        return failure
    assert p is not None
    sheet = args.get("sheet")
    if not isinstance(sheet, str) or not sheet.strip():
        return err(f"{SERVER_NAME}.bad_args", "read_formulas 需要 sheet 名")
    max_rows = int(args.get("max_rows") or 500)
    max_cols = int(args.get("max_cols") or 60)
    fb = None
    try:
        fb = FormulaBackend(str(p))
        data = fb.read_formulas(sheet, max_rows=max_rows, max_cols=max_cols)
    except FormulaBackendUnavailable:
        logger.exception("read_formulas 后端不可用")
        return err(f"{SERVER_NAME}.openpyxl_unavailable",
                   "公式解析需要 openpyxl，当前环境不可用")
    except KeyError:
        return err(f"{SERVER_NAME}.sheet_not_found", f"sheet={sheet} 不存在")
    except Exception:  # noqa: BLE001
        logger.exception("read_formulas 读取失败")
        return err(f"{SERVER_NAME}.read_failed", "读取公式失败",
                   )
    finally:
        if fb is not None:
            fb.close()
    data.update(source)
    return ok(data, source=f"{SERVER_NAME}.read_formulas")


def _tool_cross_sheet_refs(args: dict) -> dict:
    p, source, failure = _resolve_source(args)
    if failure is not None:
        return failure
    assert p is not None
    fb = None
    try:
        fb = FormulaBackend(str(p))
        data = fb.cross_sheet_refs()
    except FormulaBackendUnavailable:
        logger.exception("cross_sheet_refs 后端不可用")
        return err(f"{SERVER_NAME}.openpyxl_unavailable",
                   "公式解析需要 openpyxl，当前环境不可用")
    except Exception:  # noqa: BLE001
        logger.exception("cross_sheet_refs 解析失败")
        return err(f"{SERVER_NAME}.read_failed", "解析跨表引用失败",
                   )
    finally:
        if fb is not None:
            fb.close()
    data.update(source)
    return ok(data, source=f"{SERVER_NAME}.cross_sheet_refs")


def _tool_dependency_tree(args: dict) -> dict:
    p, source, failure = _resolve_source(args)
    if failure is not None:
        return failure
    assert p is not None
    sheet = args.get("sheet")
    cell = args.get("cell")
    if not isinstance(sheet, str) or not sheet.strip() or not isinstance(cell, str) or not cell.strip():
        return err(f"{SERVER_NAME}.bad_args", "dependency_tree 需要 sheet 与 cell（如 K25）")
    max_depth = int(args.get("max_depth") or 6)
    fb = None
    try:
        fb = FormulaBackend(str(p))
        if sheet not in fb.sheet_names():
            return err(f"{SERVER_NAME}.sheet_not_found", f"sheet={sheet} 不存在")
        tree = fb.dependency_tree(sheet, cell, max_depth=max_depth)
    except FormulaBackendUnavailable:
        logger.exception("dependency_tree 后端不可用")
        return err(f"{SERVER_NAME}.openpyxl_unavailable",
                   "公式解析需要 openpyxl，当前环境不可用")
    except Exception:  # noqa: BLE001
        logger.exception("dependency_tree 构建失败")
        return err(f"{SERVER_NAME}.read_failed", "构建依赖树失败",
                   )
    finally:
        if fb is not None:
            fb.close()
    return ok({**source, "root": f"{sheet}!{cell}", "tree": tree},
              source=f"{SERVER_NAME}.dependency_tree")


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="list_sheets",
        description="列出 xlsx 文件的所有 sheet 名。",
        input_schema={
            "type": "object",
            "properties": _source_properties(),
        },
        handler=_tool_list_sheets,
    )
    server.register_tool(
        name="read_xlsx",
        description=(
            "读取指定 sheet 的内容,默认前 200 行 / 前 50 列。"
            "返回二维数组与后端类型(openpyxl / stdlib-zip-xml)。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                **_source_properties(),
                "sheet": {"type": "string"},
                "max_rows": {"type": "integer", "default": 200},
                "max_cols": {"type": "integer", "default": 50},
            },
        },
        handler=_tool_read_xlsx,
    )
    server.register_tool(
        name="read_formulas",
        description=(
            "抽取指定 sheet 的公式单元格（公式文本 + 缓存值 + 引用），"
            "用 data_only=False 读公式（read_xlsx 只读缓存值、读不到公式）。需 openpyxl。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                **_source_properties(),
                "sheet": {"type": "string"},
                "max_rows": {"type": "integer", "default": 500},
                "max_cols": {"type": "integer", "default": 60},
            },
            "required": ["sheet"],
        },
        handler=_tool_read_formulas,
    )
    server.register_tool(
        name="cross_sheet_refs",
        description="汇总整个工作簿的跨表引用（from→to 明细 + 引用矩阵 + 总数）。需 openpyxl。",
        input_schema={
            "type": "object",
            "properties": _source_properties(),
        },
        handler=_tool_cross_sheet_refs,
    )
    server.register_tool(
        name="dependency_tree",
        description="对 sheet!cell 递归追公式依赖树（跨表，带环保护与深度上限）。需 openpyxl。",
        input_schema={
            "type": "object",
            "properties": {
                **_source_properties(),
                "sheet": {"type": "string"},
                "cell": {"type": "string"},
                "max_depth": {"type": "integer", "default": 6},
            },
            "required": ["sheet", "cell"],
        },
        handler=_tool_dependency_tree,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
