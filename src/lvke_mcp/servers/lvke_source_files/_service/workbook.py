"""Workbook inspection over the unchanged excel backend."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lvke_mcp.adapters import source_files_repository as source_api
from lvke_mcp.adapters.workbook_inspection import inspect_path

from .envelope import _blocked


_WORKBOOK_OPERATION_TO_HANDLER = {
    "list_sheets": "_tool_list_sheets",
    "read_cells": "_tool_read_xlsx",
    "read_formulas": "_tool_read_formulas",
    "cross_sheet_refs": "_tool_cross_sheet_refs",
    "dependency_tree": "_tool_dependency_tree",
}


_WORKBOOK_RANGE_RE = re.compile(
    r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)"
    r"(?::\$?([A-Za-z]{1,3})\$?([1-9][0-9]*))?$"
)


def _column_index(column: str) -> int:
    index = 0
    for character in column.upper():
        index = index * 26 + ord(character) - ord("A") + 1
    return index


def _parse_workbook_range(value: str) -> tuple[int, int, int, int] | None:
    matched = _WORKBOOK_RANGE_RE.fullmatch(str(value or "").strip())
    if matched is None:
        return None
    start_column, start_row, end_column, end_row = matched.groups()
    row_start = int(start_row)
    column_start = _column_index(start_column)
    row_end = int(end_row or start_row)
    column_end = _column_index(end_column or start_column)
    if row_end < row_start or column_end < column_start:
        return None
    return row_start, column_start, row_end, column_end


def _slice_workbook_result(
    result: dict[str, Any],
    operation: str,
    bounds: tuple[int, int, int, int],
    range_ref: str,
) -> None:
    payload = result.get("data")
    if not isinstance(payload, dict):
        payload = result
    row_start, column_start, row_end, column_end = bounds
    if operation == "read_cells" and isinstance(payload.get("rows"), list):
        rows = payload["rows"][row_start - 1 : row_end]
        selected = [
            list(row[column_start - 1 : column_end]) if isinstance(row, list) else []
            for row in rows
        ]
        payload["rows"] = selected
        payload["row_count"] = len(selected)
        payload["col_count"] = max((len(row) for row in selected), default=0)
    elif operation == "read_formulas" and isinstance(payload.get("cells"), list):
        selected_cells = []
        for item in payload["cells"]:
            if not isinstance(item, dict):
                continue
            cell_bounds = _parse_workbook_range(str(item.get("cell") or ""))
            if cell_bounds is None:
                continue
            row, column, _row_end, _column_end = cell_bounds
            if row_start <= row <= row_end and column_start <= column <= column_end:
                selected_cells.append(item)
        payload["cells"] = selected_cells
        payload["formula_count"] = len(selected_cells)
    payload["selected_range"] = range_ref.replace("$", "").upper()


def inspect_workbook(
    workspace_id: str,
    file_id: str,
    operation: str,
    *,
    sheet: str = "",
    range_ref: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect one governed workbook through the unchanged excel backend."""

    resolved = source_api.resolve_source_workbook_for_review(workspace_id, file_id)
    if not resolved.get("ok"):
        return _blocked(
            str(resolved.get("code") or "source_workbook_not_found"),
            "工作簿不存在、格式不受支持或完整性校验失败",
        )
    handler_name = _WORKBOOK_OPERATION_TO_HANDLER.get(str(operation or ""))
    if handler_name is None:
        return _blocked("workbook_operation_invalid", "未知工作簿检查操作")

    normalized_options = dict(options or {})
    bounds = _parse_workbook_range(range_ref) if range_ref else None
    if range_ref and bounds is None:
        return _blocked("workbook_range_invalid", "range 必须是合法且正向的 Excel A1 范围")
    if range_ref and operation in {"list_sheets", "cross_sheet_refs"}:
        return _blocked("workbook_range_not_applicable", "当前 operation 不接受 range")
    if (
        range_ref
        and operation in {"read_cells", "read_formulas"}
        and any(key in normalized_options for key in ("max_rows", "max_cols"))
    ):
        return _blocked(
            "workbook_range_options_conflict",
            "提供 range 时不要同时设置 options.max_rows/max_cols",
        )
    if operation == "dependency_tree":
        if bounds is None or bounds[0] != bounds[2] or bounds[1] != bounds[3]:
            return _blocked(
                "workbook_dependency_cell_required",
                "dependency_tree 需要 range 指定单个根单元格，例如 A1",
            )

    arguments: dict[str, Any] = {}
    if sheet:
        arguments["sheet"] = sheet
    if operation == "dependency_tree" and range_ref:
        arguments["cell"] = range_ref.split(":", 1)[0].replace("$", "").upper()
    if bounds is not None and operation in {"read_cells", "read_formulas"}:
        arguments["max_rows"] = bounds[2]
        arguments["max_cols"] = bounds[3]
    else:
        for key in ("max_rows", "max_cols", "max_depth"):
            if normalized_options.get(key) is not None:
                arguments[key] = int(normalized_options[key])
    result = inspect_path(
        Path(resolved["path"]),
        operation,
        **arguments,
    )
    if result is None:
        return _blocked("workbook_operation_invalid", "未知工作簿检查操作")
    if isinstance(result, dict):
        if bounds is not None and operation in {"read_cells", "read_formulas"}:
            _slice_workbook_result(result, operation, bounds, range_ref)
        result["workspace_id"] = workspace_id
        result["source_file_id"] = file_id
        result["source_sha256"] = resolved.get("sha256")
        result["source_version"] = resolved.get("source_version")
    return result
