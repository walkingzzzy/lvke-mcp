"""CSV / XLSX 导出与导出前置校验。"""

from __future__ import annotations

import csv
import hashlib
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from lvke_mcp.runtime.storage import require_safe_id

from .columns import (
    PACKAGE_STORE,
    _export_root,
)

from .query import (
    _blocked,
    _result,
)

from .render import (
    _package,
)

from .rows import (
    _rows,
    _table_contract,
)


def _ensure_exportable(payload: dict[str, Any]) -> dict[str, Any] | None:
    if (payload.get("integrity") or {}).get("status") != "passed":
        return _blocked("TABLE_PACKAGE_INCOMPLETE", "收购十三表列级完整性或勾稽校验未通过")
    return None


def _export_cell(field: str, value: Any) -> Any:
    if field == "dynamic_payback_status":
        return {"recovered": "已回收", "not_recovered": "未回收"}.get(value, value)
    return "" if value is None else value


def export_csv(
    workspace_id: str,
    package_id: str,
) -> dict[str, Any]:
    record, payload = _package(
        workspace_id,
        package_id,
    )
    if record is None:
        return _blocked("TABLE_PACKAGE_NOT_FOUND", "未找到收购十三表 package")
    blocked = _ensure_exportable(payload)
    if blocked is not None:
        return blocked
    definitions, columns_by_key, _required = _table_contract(
        str(payload.get("asset_type") or "hotel_lease")
    )
    directory = (
        _export_root(workspace_id)
        / "csv"
        / require_safe_id(package_id, "package_id")
    )
    directory.mkdir(parents=True, exist_ok=True)
    uris: list[str] = []
    hashes: dict[str, str] = {}
    for key, _name in definitions:
        target = directory / f"{key}.csv"
        columns = columns_by_key[key]
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow([label for _field, label in columns])
            for row in _rows((payload.get("tables") or {}).get(key)):
                writer.writerow([_export_cell(field, row.get(field)) for field, _label in columns])
        hashes[key] = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        uris.append(
            PACKAGE_STORE.uri(workspace_id, package_id)
            + f"/csv/{key}"
        )
    result = _result(record)
    result.update({
        "csv_resource_uris": uris,
        "csv_hashes": hashes,
        # 交付物落盘绝对目录：收购各表 CSV 均在此目录下。
        "deliverable_path": str(directory),
        "resource_uris": [*result["resource_uris"], *uris],
    })
    return result


def export_xlsx(
    workspace_id: str,
    package_id: str,
) -> dict[str, Any]:
    record, payload = _package(
        workspace_id,
        package_id,
    )
    if record is None:
        return _blocked("TABLE_PACKAGE_NOT_FOUND", "未找到收购十三表 package")
    blocked = _ensure_exportable(payload)
    if blocked is not None:
        return blocked
    definitions, columns_by_key, _required = _table_contract(
        str(payload.get("asset_type") or "hotel_lease")
    )
    directory = _export_root(workspace_id) / "xlsx"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{require_safe_id(package_id, 'package_id')}.xlsx"
    workbook = Workbook()
    workbook.remove(workbook.active)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    thin_gray = Side(style="thin", color="D9E2F3")

    for index, (key, name) in enumerate(definitions, 1):
        sheet = workbook.create_sheet(title=f"{index:02d}-{name}"[:31])
        columns = columns_by_key[key]
        sheet.append([label for _field, label in columns])
        for row in _rows((payload.get("tables") or {}).get(key)):
            sheet.append([_export_cell(field, row.get(field)) for field, _label in columns])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        for column_index, (field, _label) in enumerate(columns, 1):
            if field.endswith("_wan"):
                for cell in sheet.iter_cols(min_col=column_index, max_col=column_index, min_row=2):
                    for item in cell:
                        item.number_format = '#,##0.00;[Red](#,##0.00);-'
            elif field in {"financing_ratio", "residual_rate", "occupancy", "target_irr"}:
                for cell in sheet.iter_cols(min_col=column_index, max_col=column_index, min_row=2):
                    for item in cell:
                        item.number_format = '0.0%'
        for column_cells in sheet.columns:
            maximum = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(maximum + 2, 10), 32)

    for sheet in workbook.worksheets:
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(bottom=thin_gray)
        sheet.row_dimensions[1].height = 24
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="center")
        for column_cells in sheet.columns:
            maximum = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(maximum + 2, 10), 36)
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.save(target)
    digest = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    uri = PACKAGE_STORE.uri(workspace_id, package_id) + "/xlsx"
    result = _result(record)
    result.update({
        "xlsx_resource_uri": uri,
        "xlsx_hash": digest,
        # 交付物落盘绝对路径。
        "deliverable_path": str(target),
        "resource_uris": [*result["resource_uris"], uri],
    })
    return result
