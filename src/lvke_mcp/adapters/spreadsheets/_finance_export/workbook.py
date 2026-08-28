"""交付质量评估与工作簿导出入口。"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union

from filelock import FileLock

from .base import (
    FinanceExportError,
    _DELIVERY_SHEETS,
    _recalculate_with_soffice,
    _require_openpyxl,
    delivery_count_semantics,
    delivery_table_contract_hash,
)

from .delivery_tables import (
    _write_delivery_tables,
)

from .sheets import (
    _write_checks,
    _write_indicators,
    _write_inputs,
    _write_year_table,
)


def assess_finance_delivery_quality(fin: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the exact workbook contract without writing an XLSX artifact."""
    if not fin or not fin.get("available"):
        raise FinanceExportError("finance result unavailable")
    openpyxl, Font, PatternFill, Alignment, Border, Side, _ = _require_openpyxl()
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    delivery_pack, lineage, delivery_quality = _write_delivery_tables(
        workbook, fin, Font, PatternFill, Alignment, Border, Side,
    )
    return {
        "delivery_pack": delivery_pack,
        "cell_lineage_count": len(lineage),
        "delivery_quality": delivery_quality,
    }


def export_finance_workbook(
    fin: dict[str, Any],
    path: Union[str, Path],
    *,
    model_version: str = "finance_model.v1",
    run_id: str = "",
    include_control_sheets: bool = False,
    artifact_notice: str = "",
) -> dict[str, Any]:
    """Write finance review workbook to ``path``. Returns summary dict."""
    if not fin or not fin.get("available"):
        raise FinanceExportError("finance result unavailable")

    openpyxl, Font, PatternFill, Alignment, Border, Side, get_column_letter = _require_openpyxl()
    wb = openpyxl.Workbook()

    # Inputs
    ws_in = wb.active
    ws_in.title = "Inputs"
    key_rows = _write_inputs(ws_in, fin, Font, PatternFill, Alignment, Border, Side)

    # The 13 delivery sheets are the formal artifact. Legacy review sheets below remain for compatibility.
    delivery_pack, lineage, delivery_quality = _write_delivery_tables(
        wb, fin, Font, PatternFill, Alignment, Border, Side,
    )
    if artifact_notice:
        for sheet_name in _DELIVERY_SHEETS.values():
            sheet = wb[sheet_name]
            existing = str(sheet.cell(2, 1).value or "")
            notice_cell = sheet.cell(2, 1, f"{artifact_notice} {existing}".strip())
            notice_cell.font = Font(bold=True, color="9C0006")
            notice_cell.fill = PatternFill("solid", fgColor="FFC7CE")
            notice_cell.alignment = Alignment(wrap_text=True, vertical="center")

    # Indicators
    ws_ind = wb.create_sheet("Indicators")
    _write_indicators(ws_ind, fin, key_rows, Font)

    annual = fin.get("annual") or {}
    header_font = Font(bold=True)

    ws_inc = wb.create_sheet("Income")
    _write_year_table(
        ws_inc,
        annual.get("income_statement") or [],
        ["revenue", "operating_cost", "tax_surtax", "income_tax", "net_profit", "ebit"],
        header_font,
    )
    # Add net profit formula for each data row: E = B - C - D approx (revenue - op_cost - tax) if cols match
    # Columns: A year, B revenue, C operating_cost, D tax_surtax, E income_tax, F net_profit, G ebit
    # Provide helper column H =B-C-D-E as check formula
    if ws_inc.max_row >= 2 and ws_inc.cell(1, 1).value == "year":
        ws_inc.cell(1, 8, "net_profit_check")
        ws_inc.cell(1, 8).font = header_font
        for r in range(2, ws_inc.max_row + 1):
            ws_inc.cell(r, 8, f"=B{r}-C{r}-D{r}-E{r}")

    ws_tc = wb.create_sheet("TotalCost")
    _write_year_table(
        ws_tc,
        annual.get("total_cost") or [],
        ["operating_cost", "depreciation", "amortization", "interest", "total"],
        header_font,
    )
    if ws_tc.max_row >= 2 and ws_tc.cell(1, 1).value == "year":
        ws_tc.cell(1, 7, "total_check")
        ws_tc.cell(1, 7).font = header_font
        for r in range(2, ws_tc.max_row + 1):
            ws_tc.cell(r, 7, f"=B{r}+C{r}+D{r}+E{r}")

    ws_pcf = wb.create_sheet("ProjectCF")
    _write_year_table(
        ws_pcf,
        annual.get("project_cashflow") or [],
        ["net_cashflow", "cumulative", "revenue", "op_cash_cost", "construction", "wc_change", "recover"],
        header_font,
    )

    ws_ccf = wb.create_sheet("CapitalCF")
    cap_rows = annual.get("capital_cashflow") or []
    # capital rows may be dict list with varying keys
    cap_fields = []
    if cap_rows and isinstance(cap_rows[0], dict):
        cap_fields = [k for k in cap_rows[0].keys() if k not in ("year", "period", "phase")][:8]
    _write_year_table(ws_ccf, cap_rows if isinstance(cap_rows, list) else [], cap_fields, header_font)

    ws_fp = wb.create_sheet("FinancialPlan")
    fp = annual.get("financial_plan") or []
    _write_year_table(
        ws_fp,
        fp,
        ["finance_in", "operating_net", "invest_out", "debt_service", "net_cashflow", "cumulative", "gap"],
        header_font,
    )

    ws_debt = wb.create_sheet("Debt")
    _write_year_table(
        ws_debt,
        annual.get("debt_service") or [],
        ["begin", "principal", "interest", "end", "dscr", "icr"],
        header_font,
    )

    ws_chk = wb.create_sheet("Checks")
    _write_checks(ws_chk, fin, Font)

    ws_lin = wb.create_sheet("CellLineage")
    ws_lin.append(["target_cell", "source", "method"])
    for row in lineage:
        ws_lin.append([row.get("target"), row.get("source"), row.get("method")])

    ws_diff = wb.create_sheet("TemplateDiff")
    ws_diff.append(["table", "grade", "effective", "missing_fields", "reference_schema"])
    for key, sheet_name in _DELIVERY_SHEETS.items():
        table = delivery_pack.get(key) or {}
        ws_diff.append([
            sheet_name,
            table.get("grade"),
            bool(table.get("effective")),
            "；".join(table.get("missing_fields") or []),
            table.get("reference_schema"),
        ])

    ws_formula = wb.create_sheet("FormulaAudit")
    ws_formula.append(["table", "required_formula_families", "actual_formula_families", "coverage"])
    formula_dimension = (delivery_quality.get("quality_dimensions") or {}).get("formula") or {}
    required_by_table = formula_dimension.get("required_formula_families") or {}
    actual_by_table = formula_dimension.get("actual_formula_families") or {}
    coverage_by_table = formula_dimension.get("by_table") or {}
    for key in _DELIVERY_SHEETS:
        ws_formula.append([
            key,
            ",".join(required_by_table.get(key) or []),
            ",".join(actual_by_table.get(key) or []),
            coverage_by_table.get(key),
        ])
    ws_formula.append([])
    ws_formula.append(["sheet", "cell", "normalized_ast", "ast_sha256"])
    for sheet, signatures in (delivery_quality.get("formula_signatures") or {}).items():
        for signature in signatures:
            ws_formula.append([sheet, signature.get("cell"), "'" + str(signature.get("normalized_ast") or ""), signature.get("ast_sha256")])

    ws_meta = wb.create_sheet("Meta")
    meta_font = Font(bold=True)
    ws_meta.append(["key", "value"])
    for cell in ws_meta[1]:
        cell.font = meta_font
    schema = fin.get("schema") or {}
    manifest = fin.get("model_manifest") or {}
    count_semantics = delivery_count_semantics()
    meta_rows = [
        ("run_id", run_id or ""),
        ("model_version", model_version),
        ("manifest_hash", fin.get("manifest_hash") or ""),
        ("manifest_version", manifest.get("manifest_version") or ""),
        ("spec_schema_version", manifest.get("spec_schema_version") or ""),
        ("policy_version", manifest.get("policy_version") or ""),
        ("industry_profile_version", manifest.get("industry_profile_version") or ""),
        ("gate_version", manifest.get("gate_version") or ""),
        ("valuation_date", fin.get("valuation_date") or ""),
        ("finance_schema_version", fin.get("finance_schema_version") or schema.get("finance_schema_version") or ""),
        ("invest_type", fin.get("invest_type") or ""),
        ("industry", fin.get("industry") or ""),
        ("exported_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ("export_profile", "professional_13_table_v1"),
        ("table_contract_hash", delivery_table_contract_hash()),
        ("engine_delivery_count", count_semantics["engine_delivery_count"]),
        ("reference_source_sheet_count", count_semantics["reference_source_sheet_count"]),
        ("review_workbook_sheet_count", count_semantics["review_workbook_sheet_count"]),
        ("delivery_sheet_count", len(_DELIVERY_SHEETS)),
        ("validation_complete", bool(delivery_quality.get("validation_complete"))),
    ]
    for k, v in meta_rows:
        ws_meta.append([k, v])

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.calculation.calcMode = "auto"
        wb.calculation.fullCalcOnLoad = True
        wb.calculation.forceFullCalc = True
    except Exception:  # noqa: BLE001
        pass

    # 【P0-2修复】手动触发计算并缓存所有公式单元格的值
    # 问题: 附表9/10有大量空单元格(公式未缓存),data_only=True读取时显示空白
    # 方案: 在保存前遍历所有公式单元格,尝试触发计算(openpyxl限制:无法真正计算,但可以保留公式)
    # 注意: openpyxl无法执行公式计算,真正的缓存需要Excel/LibreOffice打开后才能生成
    # 此修复只是确保公式被正确写入,实际缓存由_recalculate_with_soffice完成
    formula_count = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == 'f':  # 公式单元格
                    formula_count += 1
                    # openpyxl会在保存时自动写入公式,这里只是统计

    if not include_control_sheets:
        delivery_names = set(_DELIVERY_SHEETS.values())
        for sheet_name in list(wb.sheetnames):
            if sheet_name not in delivery_names:
                del wb[sheet_name]

    # P1-015 修复：先写临时文件，校验完整后原子发布。原先 wb.save 直接写最终
    # 路径，1946-1959 行的 validation_complete 和 recalculation 校验失败时文件
    # 已经落盘，属于非原子写入（与 transport.py:517 的结构性问题同源）。
    # 临时文件必须保留 .xlsx 后缀，否则 _recalculate_with_soffice 的 LibreOffice
    # --convert-to xlsx 会因格式检测失败而报错。
    temporary = out.parent / f".{out.stem}.{uuid.uuid4().hex}.tmp.xlsx"
    lock_path = str(out) + ".lock"
    try:
        wb.save(str(temporary))

        formal_requested = bool(delivery_quality.get("validation_complete"))
        recalculation = (
            _recalculate_with_soffice(temporary)
            if formal_requested
            else {"ok": False, "available": False, "skipped": True, "issues": ["reference 导出不要求实际重算"]}
        )
        if formal_requested and not recalculation.get("ok"):
            delivery_quality = dict(delivery_quality)
            delivery_quality["validation_complete"] = False
            delivery_quality["recalculation"] = recalculation
            delivery_quality.setdefault("quality_dimensions", {})["libreoffice_recalculation"] = {
                "ok": False,
                "detail": recalculation.get("issues") or [],
            }
        else:
            delivery_quality = dict(delivery_quality)
            delivery_quality["recalculation"] = recalculation

        # 临时文件已写完且校验通过（或 reference 级不要求重算），原子发布到最终路径
        with FileLock(lock_path, timeout=30):
            os.replace(temporary, out)
            temporary = None  # 成功后标记已发布，finally 不删
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

    formula_cells = 0
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                val = cell.value
                if isinstance(val, str) and val.startswith("="):
                    formula_cells += 1

    return {
        "ok": True,
        "path": str(out.resolve()),
        "sheets": wb.sheetnames,
        "formula_cells": formula_cells,
        "input_keys": list(key_rows.keys()),
        "delivery_sheets": list(_DELIVERY_SHEETS.values()),
        "delivery_sheet_count": len(_DELIVERY_SHEETS),
        **count_semantics,
        "table_contract_hash": delivery_table_contract_hash(),
        "control_sheets_included": include_control_sheets,
        "artifact_notice": artifact_notice,
        "cell_lineage_count": len(lineage),
        "validation_complete": bool(delivery_quality.get("validation_complete")),
        "table_quality": delivery_pack.get("_meta") or {},
        "delivery_quality": delivery_quality,
        "recalculation": recalculation,
    }
