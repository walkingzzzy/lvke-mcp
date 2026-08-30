"""package 读取、记录读取与 Resource 解析；结果信封。"""

from __future__ import annotations

import csv
import json
from typing import Any


from lvke_mcp.runtime.storage import require_safe_id

from .columns import (
    PACKAGE_STORE,
    _export_root,
)

from .rows import (
    _table_contract,
)


def get_package(
    workspace_id: str,
    package_id: str,
) -> dict[str, Any]:
    record = PACKAGE_STORE.get(workspace_id, package_id)
    return _blocked("TABLE_PACKAGE_NOT_FOUND", "未找到收购十三表 package") if record is None else _result(record)


def get_package_record(
    workspace_id: str,
    package_id: str,
) -> dict[str, Any] | None:
    """Return the immutable record for a table package."""

    return PACKAGE_STORE.get(workspace_id, package_id)


def resolve_resource(
    uri: str,
) -> tuple[str | bytes, str] | None:
    record = PACKAGE_STORE.resolve_uri(uri)
    if record is not None:
        return json.dumps(record, ensure_ascii=False, indent=2), "application/json"
    prefix = "lvke://asset-acquisition/workspaces/"
    if not uri.startswith(prefix):
        return None
    parts = uri[len(prefix):].split("/")
    try:
        workspace_id = require_safe_id(parts[0], "workspace_id")
        if len(parts) == 4 and parts[1] == "table-packages" and parts[3] == "xlsx":
            package_id = require_safe_id(parts[2], "package_id")
            target = _export_root(workspace_id) / "xlsx" / f"{package_id}.xlsx"
            if not target.is_file():
                target = _export_root(workspace_id) / "xlsx" / f"{package_id}.technical.xlsx"
            return (target.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") if target.is_file() else None
        if len(parts) == 5 and parts[1] == "table-packages" and parts[3] == "xlsx" and parts[4] == "manifest":
            package_id = require_safe_id(parts[2], "package_id")
            target = _export_root(workspace_id) / "xlsx" / f"{package_id}.xlsx.manifest.json"
            if not target.is_file():
                target = _export_root(workspace_id) / "xlsx" / f"{package_id}.technical.xlsx.manifest.json"
            return (target.read_text(encoding="utf-8"), "application/json") if target.is_file() else None
        if len(parts) == 5 and parts[1] == "table-packages" and parts[3] == "csv":
            package_id = require_safe_id(parts[2], "package_id")
            key = require_safe_id(parts[4], "table_key")
            if key == "manifest":
                target = _export_root(workspace_id) / "csv" / package_id / "manifest.json"
                return (target.read_text(encoding="utf-8"), "application/json") if target.is_file() else None
            package = PACKAGE_STORE.get(workspace_id, package_id)
            payload = dict((package or {}).get("payload") or {})
            definitions, _columns, _required = _table_contract(
                str(payload.get("asset_type") or "hotel_lease")
            )
            if key not in {*dict(definitions), "monthly_income_statement", "monthly_balance_sheet"}:
                return None
            target = _export_root(workspace_id) / "csv" / package_id / f"{key}.csv"
            return (target.read_bytes(), "text/csv; charset=utf-8") if target.is_file() else None
    except (ValueError, IndexError):
        return None
    return None


#: 文件内标记：脱离 MCP 响应单看 CSV/XLSX 的人也必须知道它能不能正式使用。
#: 通用财务域已有同类机制（domains/finance/_tables_service/export.py）。
_CSV_PREVIEW_BANNER = (
    "【技术预览·不可正式使用】本文件由受控假设或未认证项目事实导出，"
    "未通过正式收购交付门禁，仅供过程验收与结构核对；不得作为对外交付物或投资决策依据。"
)

_XLSX_PREVIEW_BANNER = (
    "【估算预览】仅供过程验收使用，不得作为正式投资决策依据。"
)


def _release_grade(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Return ``(release_grade, reasons)`` derived from the package itself.

    口径全部取自 package 已固化的字段，不新增参数：调用方无法通过省略参数
    把预览件"提级"成正式件。
    """

    reasons: list[str] = []
    policy = str(payload.get("evidence_policy") or "formal_evidence")
    if policy != "formal_evidence":
        reasons.append(f"evidence_policy={policy}")
    if str(payload.get("delivery_mode") or "") == "estimate_preview":
        reasons.append("delivery_mode=estimate_preview")
    if not payload.get("project_fact_certified"):
        reasons.append("project_fact_not_certified")
    reasons.extend(
        f"release_limitation:{item}"
        for item in (payload.get("release_limitations") or [])
        if str(item)
    )
    reasons.extend(
        f"unresolved_input:{item}"
        for item in (payload.get("unresolved_inputs") or [])
        if str(item)
    )
    grade = "technical_preview" if reasons else "formal_candidate"
    return grade, sorted(set(reasons))


def _result(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") or {}
    integrity = payload.get("integrity") or {}
    blockers = list(integrity.get("blockers") or [])
    warnings = list(integrity.get("warnings") or [])
    # 等级必须在这里就带上：_result 是 render / get_package / list_tables /
    # export 共用的构造器，只在 export 层补标记的话，直接消费或绑定 table
    # package 的调用方（例如 report_prepare 的 finance_binding）看到的仍是
    # 一个不带任何限制说明的 status=ok，从而把预览件当正式件用。
    grade, grade_reasons = _release_grade(payload)
    preview = grade == "technical_preview"
    if preview:
        warnings = [
            *warnings,
            f"技术预览：{_CSV_PREVIEW_BANNER}",
            *(f"限制：{item}" for item in grade_reasons),
        ]
    return {
        "success": True,
        "status": "partial" if (preview or integrity.get("status") != "passed") else "ok",
        "object_id": record["object_id"],
        "acquisition_tables_package_id": record["object_id"],
        "run_id": payload.get("run_id"),
        "spec_hash": payload.get("spec_hash"),
        "input_hash": payload.get("input_hash"),
        "model_version": payload.get("model_version"),
        "evidence_binding_hash": payload.get("evidence_binding_hash"),
        "table_manifest": payload.get("table_manifest") or [],
        "formula_lineage": payload.get("formula_lineage") or [],
        "integrity": integrity,
        "release_grade": grade,
        "technical_preview": preview,
        "formal_usable": not preview,
        "release_limitations": grade_reasons,
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": blockers,
        "next_actions": (
            []
            if blockers
            else [
                "补齐项目事实证据并通过正式校验后方可正式使用；"
                "当前仅可作为过程验收件导出 CSV/XLSX",
            ]
            if preview
            else ["使用 package_id 导出 CSV/XLSX 或绑定资产收购报告"]
        ),
    }


def _failure(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "status": "failed", "code": code, "message": message, "resource_uris": [], "warnings": [], "blockers": [code], "next_actions": []}


def _blocked(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False, "transport_success": True,
        "business_success": False, "completed": False, "outcome": "blocked",
        "status": "blocked", "code": code, "message": message,
        "resource_uris": [], "warnings": [], "blockers": [code], "next_actions": [],
    }

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "PACKAGE_STORE",
    "_CSV_PREVIEW_BANNER",
    "_XLSX_PREVIEW_BANNER",
    "_blocked",
    "_export_root",
    "_failure",
    "_release_grade",
    "_result",
    "_table_contract",
    "csv",
    "get_package",
    "get_package_record",
    "json",
    "require_safe_id",
    "resolve_resource",
]
