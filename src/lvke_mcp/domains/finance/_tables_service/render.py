"""十三表渲染与整包校验。"""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters.finance_tables_repository import PACKAGE_STORE
from lvke_mcp.domains.finance import tables_application

from .base import (
    _check_template_version,
    _delivery_assessment,
    _failure,
    _load_run,
    _package_result,
    _require_run_id,
    _structured_delivery_tables,
    _structured_table_manifest,
)


def render(
    workspace_id: str,
    run_id: str,
    format_name: str = "structured",
    template_version: str = "",
    *,
    load_run: Any = None,
    delivery_assessment: Any = None,
    structured_delivery_tables: Any = None,
) -> dict[str, Any]:
    """渲染十三表并固化 package。

    三个 ``tables_application`` 薄委托做成仅关键字注入点，默认回落到 ``base``。
    门面 ``tables_service.render`` 显式把**门面自身**的同名属性传进来，因此
    ``patch.object(tables_service, "_load_run", ...)`` 这类替换仍然生效
    （tests/integration/test_report_finance_regressions.py），而实现包不需要反过来
    import 门面——那会造出实现包 → 门面的反向依赖边和真的新循环。

    对应 `dev-docs/plans/MODULARIZATION_PLAN.md` §5.1「会被 monkeypatch 的模块级状态不依赖普通
    re-export，改用明确的 state owner 或兼容代理」。
    """
    from lvke_mcp.domains.finance.run_service import render_workspace_finance_tables

    _load = load_run or _load_run
    _assess = delivery_assessment or _delivery_assessment
    _structured = structured_delivery_tables or _structured_delivery_tables

    rejected = _require_run_id(run_id)
    if rejected is not None:
        return rejected
    data = render_workspace_finance_tables(
        workspace_id,
        run_id=run_id,
        format=format_name,
        include_control_tables=True,
    )
    if not data.get("ok"):
        return _failure(str(data.get("error") or "render_failed"), str(data.get("message") or "十三表渲染失败"))
    version_error = _check_template_version(template_version, data)
    if version_error is not None:
        return version_error
    structured_tables = _structured(
        workspace_id,
        run_id,
        data,
    )
    table_manifest = _structured_table_manifest(
        run_id,
        str(data.get("template_version") or ""),
        structured_tables,
    )
    validated_data = {
        **data,
        "tables": structured_tables,
        "table_manifest": table_manifest,
    }
    source_run = _load(workspace_id, run_id)
    validation = _assess(
        workspace_id,
        run_id,
        validated_data,
    )
    quality_issues = [str(item) for item in validation.get("blockers") or []]
    payload = {
        "run_id": run_id,
        # 十三表/CSV/XLSX 都从这个 package 派生，必须能自证绑定的是哪一个
        # confirmed Spec，否则脱离 MCP 响应单看工件时无法反查口径。
        "spec_id": str(source_run.get("spec_id") or ""),
        "spec_hash": str(source_run.get("spec_hash") or ""),
        "template_version": data.get("template_version"),
        "table_bundle_hash": data.get("table_bundle_hash"),
        "table_contract_hash": validation.get("table_contract_hash"),
        "engine_delivery_count": validation.get("engine_delivery_count"),
        "reference_source_sheet_count": validation.get("reference_source_sheet_count"),
        "review_workbook_sheet_count": validation.get("review_workbook_sheet_count"),
        "table_manifest": table_manifest,
        "tables": structured_tables,
        "validation": validation,
        "quality_issues": quality_issues,
        "validation_complete": bool(validation["validation_complete"]),
        "delivery_mode": "formal" if validation["validation_complete"] else "draft",
        "draft_only": not bool(validation["validation_complete"]),
        "viability_status": str(source_run.get("viability_status") or "not_assessed"),
        "viability_issues": list(source_run.get("viability_issues") or []),
        "integrity_status": str(source_run.get("integrity_status") or ("passed" if source_run.get("consistency_ok") else "failed")),
        "xlsx_available": False,
        "evidence_policy": str(source_run.get("evidence_policy") or "formal_evidence"),
        "project_fact_certified": bool(source_run.get("project_fact_certified", False)),
        "reconstruction_records": list(source_run.get("reconstruction_records") or []),
        "reconstructed_source_ids": list(source_run.get("reconstructed_source_ids") or []),
        "unresolved_inputs": list(source_run.get("unresolved_inputs") or []),
        "release_limitations": list(source_run.get("release_limitations") or []),
    }
    status = "ok" if validation["valid"] else "partial"
    record = PACKAGE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-finance-tables.tables_render",
        status=status,
        source_ids=[run_id],
        basis={
            "run_id": run_id,
            "spec_id": payload["spec_id"],
            "spec_hash": payload["spec_hash"],
            "table_bundle_hash": data.get("table_bundle_hash"),
        },
    )
    result = _package_result(record, validation, status)
    result.update({
        "delivery_mode": payload["delivery_mode"],
        "draft_only": payload["draft_only"],
    })
    return result


def validate(
    workspace_id: str,
    run_id: str,
    *,
    validation_scope: str = "formal",
) -> dict[str, Any]:
    return tables_application.validate_tables(
        workspace_id,
        run_id,
        validation_scope=validation_scope,
    )
