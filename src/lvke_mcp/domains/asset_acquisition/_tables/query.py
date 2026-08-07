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
            return (target.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") if target.is_file() else None
        if len(parts) == 5 and parts[1] == "table-packages" and parts[3] == "csv":
            package_id = require_safe_id(parts[2], "package_id")
            key = require_safe_id(parts[4], "table_key")
            package = PACKAGE_STORE.get(workspace_id, package_id)
            payload = dict((package or {}).get("payload") or {})
            definitions, _columns, _required = _table_contract(
                str(payload.get("asset_type") or "hotel_lease")
            )
            if key not in dict(definitions):
                return None
            target = _export_root(workspace_id) / "csv" / package_id / f"{key}.csv"
            return (target.read_bytes(), "text/csv; charset=utf-8") if target.is_file() else None
    except (ValueError, IndexError):
        return None
    return None


def _result(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") or {}
    integrity = payload.get("integrity") or {}
    blockers = list(integrity.get("blockers") or [])
    warnings = list(integrity.get("warnings") or [])
    return {
        "success": True,
        "status": "ok" if integrity.get("status") == "passed" else "partial",
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
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": blockers,
        "next_actions": [] if blockers else ["使用 package_id 导出 CSV/XLSX 或绑定资产收购报告"],
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
