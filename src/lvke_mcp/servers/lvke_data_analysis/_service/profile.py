"""Profile tabular controlled-file documents from cell locators."""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters.data_analysis_repository import PROFILE_STORE

from .envelope import _missing
from .ingest import _documents_from_task
from .numeric_gates import _cell_position, _locator_text


def _column_letters(index: int) -> str:
    """1 → A、27 → AA。与 source_files_repository._spreadsheet_column 同算法。

    刻意不跨层 import 适配器层的私有函数：本模块只需要把 DOCX 行投影成
    A1 形式供画像分组，反向依赖 adapters 会让分析域耦合到资料解析实现。
    """

    value = max(1, int(index))
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def profile_tabular(
    workspace_id: str,
    task_id: str,
    file_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Create an auditable profile from existing controlled-file cell locators."""

    documents = _documents_from_task(
        workspace_id,
        task_id,
    )
    if not documents:
        return _missing("analysis_task_not_found", "没有可画像的分析任务")
    requested = {str(item) for item in (file_ids or []) if str(item)}
    profiles: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for document in documents:
        source_id = str(document.get("source_id") or "")
        if requested and source_id not in requested:
            continue
        if document.get("source_type") != "controlled_file":
            skipped.append({"source_id": source_id, "reason": "not_controlled_tabular_file"})
            continue
        # 两个 parser 各满足一半条件，旧判据 `kind=="cell" and sheet` 的交集为空，
        # 于是任何已正常解析的表格都被判 no_cell_locators：
        #   CSV  → kind="cell"，无 sheet（单表无工作表概念）
        #   XLSX → kind="spreadsheet_cell"，有 sheet
        # 这里按两种真实形状取并集，CSV 归到单一默认表名。
        sheets: dict[str, list[dict[str, Any]]] = {}
        for locator in document.get("locators") or []:
            if not isinstance(locator, dict):
                continue
            if locator.get("kind") == "docx_table_row":
                # DOCX 表格：解析器发的是**行级** locator（带 cells 数组），没有
                # 单元格坐标。此前本函数只认 cell/spreadsheet_cell，于是含表格的
                # DOCX 一律被判 no_cell_locators —— 「我的 DOCX 里明明有表」。
                # 这里把行投影成与 CSV/XLSX 同形的单元格 locator，让下游的表头、
                # 行列数、数值统计逻辑不必为 DOCX 开分支。
                # 不改解析器的行级形状：citation 复核按 table:N:row:M 回指，
                # 改成单元格级会打断已固化 locator 的可解析性。
                cells = locator.get("cells")
                if not isinstance(cells, list) or not cells:
                    continue
                try:
                    table_index = int(locator.get("table") or 0)
                    row_index = int(locator.get("row") or 0)
                except (TypeError, ValueError):
                    continue
                if table_index <= 0 or row_index <= 0:
                    continue
                # 每张表独立成"工作表"，避免多表被合并成一张而算出错误的行列数。
                sheet_name = f"docx_table_{table_index}"
                for column_index, cell_text in enumerate(cells, start=1):
                    text = str(cell_text or "").strip()
                    if not text:
                        continue
                    sheets.setdefault(sheet_name, []).append({
                        # 合成 A1 引用只服务于画像分组，不对外冒充可引用 locator：
                        # 保留原始 locator 串，以免被误当作可回指的证据坐标。
                        "cell": f"{_column_letters(column_index)}{row_index}",
                        "text": text,
                        "kind": "docx_table_cell",
                        "locator": str(locator.get("locator") or ""),
                        "projected_from": "docx_table_row",
                    })
                continue
            if locator.get("kind") not in {"cell", "spreadsheet_cell"}:
                continue
            sheet_name = str(locator.get("sheet") or "").strip()
            if not sheet_name:
                # 无工作表的单表资料（CSV）：用表名占位，保持后续按表分组的形状。
                sheet_name = str(locator.get("table_kind") or "table")
            sheets.setdefault(sheet_name, []).append(locator)
        if not sheets:
            skipped.append({"source_id": source_id, "reason": "no_cell_locators"})
            continue
        for sheet_name, cells in sorted(sheets.items()):
            positioned = [(locator, _cell_position(str(locator.get("cell") or ""))) for locator in cells]
            positioned = [(locator, position) for locator, position in positioned if position is not None]
            if not positioned:
                skipped.append({"source_id": source_id, "reason": "invalid_cell_locators"})
                continue
            max_row = max(position[0] for _, position in positioned)
            max_column = max(position[1] for _, position in positioned)
            first_row = min(position[0] for _, position in positioned)
            headers = [
                str(_locator_text(locator))
                for locator, position in sorted(positioned, key=lambda item: item[1][1])
                if position[0] == first_row and _locator_text(locator)
            ][:100]
            numeric_count = sum(
                1
                for locator, _ in positioned
                if isinstance(locator.get("cached_value", locator.get("display_value")), (int, float))
                and not isinstance(locator.get("cached_value", locator.get("display_value")), bool)
            )
            formula_count = sum(1 for locator, _ in positioned if str(locator.get("formula") or ""))
            profiles.append(
                {
                    "source_id": source_id,
                    "sheet": sheet_name,
                    "observed_row_count": max_row,
                    "observed_column_count": max_column,
                    "observed_cell_count": len(positioned),
                    "first_observed_row": first_row,
                    "headers": headers,
                    "numeric_cell_count": numeric_count,
                    "text_cell_count": len(positioned) - numeric_count,
                    "formula_cell_count": formula_count,
                    "formal_use_allowed": document.get("formal_use_allowed"),
                    "profile_boundary": "统计仅基于已解析的非空 cell locator；不重算公式、不补空白单元格。",
                }
            )
    if not profiles:
        reasons = {item["reason"] for item in skipped}
        unsupported = bool(skipped) and reasons == {"not_controlled_tabular_file"}
        code = "unsupported_input_kind" if unsupported else "insufficient_source_data"
        # P1-007 修复：early-return 失败路径不能返回 status=partial，
        # 因为 _PROFILE_OUTPUT 的 if/then conditional 要求 partial 时必须有
        # data_profile_id / profiles / skipped 三字段，而此路径未写 DataProfile 记录，
        # 无 object_id 可返回。改为 blocked，与 _missing() 的既有模式一致。
        status_value = "blocked"
        return {
            "success": False,
            "transport_success": True,
            "system_success": True,
            "business_success": False,
            "completed": False,
            "outcome": status_value,
            "status": status_value,
            "code": code,
            "message": (
                "纯文本输入不支持表格画像"
                if unsupported
                else "受控表格资料缺少可画像的 cell locator"
            ),
            "capability_scope": "tabular_only",
            "data_completeness": "insufficient_for_tabular_profile",
            "partial_reasons": sorted(reasons or {"no_controlled_tabular_cells"}),
            "resource_uris": [],
            "warnings": ["输入不是已解析受控表格资料"] if skipped else [],
            "blockers": [code],
            "next_actions": ["先摄入已解析 XLSX/CSV 受控资料"],
        }
    status_value = "ok" if not skipped else "partial"
    payload = {
        "analysis_task_id": task_id,
        "requested_file_ids": sorted(requested),
        "profiles": profiles,
        "skipped": skipped,
    }
    record = PROFILE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_profile_tabular",
        status=status_value,
        source_ids=[str(item.get("source_id")) for item in profiles],
        basis={"analysis_task_id": task_id, "file_ids": sorted(requested)},
    )
    complete = status_value == "ok"
    return {
        "success": complete,
        "transport_success": True,
        "system_success": True,
        "business_success": complete,
        "completed": complete,
        "outcome": status_value,
        "status": status_value,
        "data_profile_id": record["object_id"],
        "profiles": profiles,
        "skipped": skipped,
        "resource_uris": [record["resource_uri"]],
        "warnings": (["部分输入不是已解析受控表格资料，未进行画像"] if skipped else []),
        "blockers": [],
        "next_actions": ["使用 locator 审核表头和单元格含义；画像不是财务输入确认"],
    }
