"""Profile tabular controlled-file documents from cell locators."""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters.data_analysis_repository import PROFILE_STORE

from .envelope import _missing
from .ingest import _documents_from_task
from .numeric_gates import _cell_position, _locator_text


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
        sheets: dict[str, list[dict[str, Any]]] = {}
        for locator in document.get("locators") or []:
            if isinstance(locator, dict) and locator.get("kind") == "cell" and locator.get("sheet"):
                sheets.setdefault(str(locator["sheet"]), []).append(locator)
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
