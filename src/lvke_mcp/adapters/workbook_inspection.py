"""Transport-free workbook inspection shared by source and legacy adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lvke_mcp.adapters.spreadsheets.formulas import (
    FormulaBackend,
    FormulaBackendUnavailable,
)
from lvke_mcp.adapters.spreadsheets.reader import pick_backend
from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.responses import err, ok

SOURCE_NAME = "excel-bridge"
logger = get_logger(SOURCE_NAME)
_backend = None


def _get_backend():
    global _backend
    if _backend is None:
        _backend = pick_backend()
        logger.info("workbook inspection backend = %s", _backend.name)
    return _backend


def _source(path: Path) -> dict[str, Any]:
    return {"source_type": "path", "filename": path.name}


def _list_sheets(path: Path) -> dict[str, Any]:
    try:
        sheets = _get_backend().list_sheets(path)
    except Exception:  # noqa: BLE001
        logger.exception("list_sheets 解析失败")
        return err(f"{SOURCE_NAME}.read_failed", "无法解析 xlsx 文件")
    return ok(
        {**_source(path), "sheets": sheets, "backend": _get_backend().name},
        source=f"{SOURCE_NAME}.list_sheets",
    )


def _read_cells(
    path: Path,
    *,
    sheet: str = "",
    max_rows: int | None = None,
    max_cols: int | None = None,
) -> dict[str, Any]:
    row_limit = int(max_rows or 200)
    column_limit = int(max_cols or 50)
    try:
        result = _get_backend().read_sheet(
            path,
            sheet or None,
            row_limit,
            column_limit,
        )
    except KeyError:
        return err(f"{SOURCE_NAME}.sheet_not_found", f"sheet={sheet} 不存在")
    except Exception:  # noqa: BLE001
        logger.exception("read_xlsx 读取失败")
        return err(f"{SOURCE_NAME}.read_failed", "读取 xlsx 失败")
    return ok(
        {
            **_source(path),
            "sheet": result.sheet,
            "rows": result.rows,
            "row_count": result.row_count,
            "col_count": result.col_count,
            "backend": result.backend,
            "max_rows_applied": row_limit,
            "max_cols_applied": column_limit,
        },
        source=f"{SOURCE_NAME}.read_xlsx",
    )


def _read_formulas(
    path: Path,
    *,
    sheet: str,
    max_rows: int | None = None,
    max_cols: int | None = None,
) -> dict[str, Any]:
    if not sheet.strip():
        return err(f"{SOURCE_NAME}.bad_args", "read_formulas 需要 sheet 名")
    backend = None
    try:
        backend = FormulaBackend(str(path))
        data = backend.read_formulas(
            sheet,
            max_rows=int(max_rows or 500),
            max_cols=int(max_cols or 60),
        )
    except FormulaBackendUnavailable:
        logger.exception("read_formulas 后端不可用")
        return err(
            f"{SOURCE_NAME}.openpyxl_unavailable",
            "公式解析需要 openpyxl，当前环境不可用",
        )
    except KeyError:
        return err(f"{SOURCE_NAME}.sheet_not_found", f"sheet={sheet} 不存在")
    except Exception:  # noqa: BLE001
        logger.exception("read_formulas 读取失败")
        return err(f"{SOURCE_NAME}.read_failed", "读取公式失败")
    finally:
        if backend is not None:
            backend.close()
    data.update(_source(path))
    return ok(data, source=f"{SOURCE_NAME}.read_formulas")


def _cross_sheet_refs(path: Path) -> dict[str, Any]:
    backend = None
    try:
        backend = FormulaBackend(str(path))
        data = backend.cross_sheet_refs()
    except FormulaBackendUnavailable:
        logger.exception("cross_sheet_refs 后端不可用")
        return err(
            f"{SOURCE_NAME}.openpyxl_unavailable",
            "公式解析需要 openpyxl，当前环境不可用",
        )
    except Exception:  # noqa: BLE001
        logger.exception("cross_sheet_refs 解析失败")
        return err(f"{SOURCE_NAME}.read_failed", "解析跨表引用失败")
    finally:
        if backend is not None:
            backend.close()
    data.update(_source(path))
    return ok(data, source=f"{SOURCE_NAME}.cross_sheet_refs")


def _dependency_tree(
    path: Path,
    *,
    sheet: str,
    cell: str,
    max_depth: int | None = None,
) -> dict[str, Any]:
    if not sheet.strip() or not cell.strip():
        return err(
            f"{SOURCE_NAME}.bad_args",
            "dependency_tree 需要 sheet 与 cell（如 K25）",
        )
    backend = None
    try:
        backend = FormulaBackend(str(path))
        if sheet not in backend.sheet_names():
            return err(f"{SOURCE_NAME}.sheet_not_found", f"sheet={sheet} 不存在")
        tree = backend.dependency_tree(sheet, cell, max_depth=int(max_depth or 6))
    except FormulaBackendUnavailable:
        logger.exception("dependency_tree 后端不可用")
        return err(
            f"{SOURCE_NAME}.openpyxl_unavailable",
            "公式解析需要 openpyxl，当前环境不可用",
        )
    except Exception:  # noqa: BLE001
        logger.exception("dependency_tree 构建失败")
        return err(f"{SOURCE_NAME}.read_failed", "构建依赖树失败")
    finally:
        if backend is not None:
            backend.close()
    return ok(
        {**_source(path), "root": f"{sheet}!{cell}", "tree": tree},
        source=f"{SOURCE_NAME}.dependency_tree",
    )


def inspect_path(
    path: Path,
    operation: str,
    *,
    sheet: str = "",
    cell: str = "",
    max_rows: int | None = None,
    max_cols: int | None = None,
    max_depth: int | None = None,
) -> dict[str, Any] | None:
    if operation == "list_sheets":
        return _list_sheets(path)
    if operation == "read_cells":
        return _read_cells(
            path,
            sheet=sheet,
            max_rows=max_rows,
            max_cols=max_cols,
        )
    if operation == "read_formulas":
        return _read_formulas(
            path,
            sheet=sheet,
            max_rows=max_rows,
            max_cols=max_cols,
        )
    if operation == "cross_sheet_refs":
        return _cross_sheet_refs(path)
    if operation == "dependency_tree":
        return _dependency_tree(
            path,
            sheet=sheet,
            cell=cell,
            max_depth=max_depth,
        )
    return None
