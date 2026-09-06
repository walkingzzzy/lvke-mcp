"""run 加载别名、manifest/quality 别名、模板版本与交付评估原语、结果信封。"""

from __future__ import annotations

from typing import Any

from lvke_mcp.domains.finance import tables_application
from lvke_mcp.runtime.quality_severity import (
    aggregate_quality_status,
    classify_quality,
)


_load_run = tables_application.get_run


_structured_table_manifest = tables_application.structured_table_manifest


_structured_table_quality = tables_application.structured_table_quality


def _require_run_id(run_id: str) -> dict[str, Any] | None:
    """十三表只消费固化 run_id；缺 run_id 一律拒绝，绝不回退到「最新 run」。"""
    if not str(run_id or "").strip():
        return _failure("run_id_required", "缺少 run_id；十三表只消费固化 run，不做兜底选取")
    return None


def _check_template_version(template_version: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """可选 template_version 是版本钉住断言：不认识/不一致就报错，绝不静默忽略。

    模板版本在 run 固化时已确定（run_service.TEMPLATE_VERSION），表服务不做版本
    转换；调用方声明的版本只用于防止「按旧版模板口径消费新版 run」的静默漂移。
    """
    requested = str(template_version or "").strip()
    if not requested:
        return None
    actual = str(data.get("template_version") or "")
    if requested != actual:
        return _failure(
            "template_version_mismatch",
            f"请求模板版本 {requested} 与 run 固化版本 {actual} 不一致；表服务不做版本转换，须换用匹配 run",
        )
    return None


def _delivery_assessment(
    workspace_id: str,
    run_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    return tables_application.delivery_assessment(workspace_id, run_id, data)


def _delivery_keys() -> tuple[str, ...]:
    return tables_application.delivery_keys()


def _delivery_count_semantics() -> dict[str, int]:
    return tables_application.delivery_count_semantics()


def _delivery_table_contract_hash() -> str:
    return tables_application.delivery_table_contract_hash()


def _structured_delivery_tables(
    workspace_id: str,
    run_id: str,
    rendered: dict[str, Any],
) -> dict[str, Any]:
    return tables_application.structured_delivery_tables(
        workspace_id,
        run_id,
        rendered,
    )


def _scalar_csv_rows(table: Any) -> tuple[list[str], list[list[Any]]]:
    if not isinstance(table, dict):
        return [], []
    columns = [column for column in (table.get("columns") or []) if isinstance(column, dict)]
    headers = [str(column.get("label") or column.get("key") or "") for column in columns]
    rows: list[list[Any]] = []
    for row in table.get("rows") or []:
        if not isinstance(row, list) or len(row) != len(columns):
            return [], []
        scalar = []
        for value in row:
            if isinstance(value, (dict, list)):
                return [], []
            scalar.append("" if value is None else value)
        rows.append(scalar)
    return headers, rows


def _package_result(record: dict[str, Any], validation: dict[str, Any], status: str) -> dict[str, Any]:
    payload = record.get("payload") or {}
    quality_issues = [str(item) for item in validation.get("blockers") or []]
    effective_status = "partial" if quality_issues or status == "partial" else "ok"
    material_conflict = any(
        classify_quality(code).get("material_conflict") is True
        for code in quality_issues
    )
    diagnostic_ids = [
        str(item)
        for item in (validation.get("diagnostic_ids") or [])
        if str(item)
    ]
    return {
        "success": True,
        "transport_success": True,
        "business_success": True,
        "completed": True,
        "outcome": effective_status,
        "status": effective_status,
        "finance_tables_package_id": record["object_id"],
        "run_id": payload.get("run_id"),
        # 与 run_id 同级透出，让 package / CSV / XLSX 的消费方都能反查 confirmed Spec
        "spec_id": payload.get("spec_id"),
        "spec_hash": payload.get("spec_hash"),
        "evidence_policy": payload.get("evidence_policy"),
        "evidence_origin": payload.get("evidence_origin"),
        "project_fact_certified": bool(payload.get("project_fact_certified", False)),
        "formal_promotion": payload.get("formal_promotion"),
        "table_contract_hash": payload.get("table_contract_hash"),
        "engine_delivery_count": payload.get("engine_delivery_count"),
        "reference_source_sheet_count": payload.get("reference_source_sheet_count"),
        "review_workbook_sheet_count": payload.get("review_workbook_sheet_count"),
        "table_manifest": payload.get("table_manifest") or [],
        "validation": validation,
        "validation_complete": bool(payload.get("validation_complete", False)),
        "resource_uris": [record["resource_uri"]],
        "warnings": [
            *list(validation.get("warnings") or []),
            *(f"质量提示：{item}" for item in quality_issues),
        ],
        "blockers": [],
        "quality_issues": quality_issues,
        # 技术验收阶段统一诊断信封（§1-§5）：success/business_success 不再
        # 隐含“数据质量通过”或“可直接绑定正式报告”。
        "operation_status": "completed",
        "diagnostic_available": True,
        "quality_status": aggregate_quality_status(quality_issues),
        "uncertainties": list(validation.get("uncertainties") or []),
        "diagnostic_only": False,
        "human_confirmation_required": False,
        "formal_report_allowed": True,
        "bindable_to_report": True,
        "diagnostic_ids": diagnostic_ids,
        "next_actions": (
            [
                "已生成表包；包含财务数据质量冲突，已在质量诊断中保留。"
            ]
            if material_conflict
            else [
                "已生成表包；质量发现已在结果中保留。"
            ]
        ),
    }


def _failure(code: str, message: str, *, system_error: bool = False) -> dict[str, Any]:
    """Return an actionable business block without pretending MCP failed."""

    return {
        "success": False,
        "transport_success": not system_error,
        "business_success": False,
        "completed": False,
        "outcome": "failed" if system_error else "blocked",
        "status": "failed" if system_error else "blocked",
        "code": code,
        "message": message,
        "validation_complete": False,
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [],
    }
