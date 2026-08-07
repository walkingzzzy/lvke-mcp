"""package 读取、表注册表与单表读取/校验。"""

from __future__ import annotations

import csv
from typing import Any

from lvke_mcp.adapters.finance_tables_repository import CSV_EXPORT_STORE, PACKAGE_STORE
from lvke_mcp.runtime.storage import sha256_json

from .base import (
    _delivery_keys,
    _failure,
    _package_result,
    _scalar_csv_rows,
    _structured_table_quality,
)

from .export import (
    _validate_csv_export,
)


def get_package(
    workspace_id: str,
    package_id: str,
) -> dict[str, Any]:
    record = PACKAGE_STORE.get(workspace_id, package_id)
    if record is None:
        return _failure("package_not_found", "未找到十三表包")
    payload = record.get("payload") or {}
    validation = payload.get("validation") or {}
    result = _package_result(record, validation, str(record.get("status") or "partial"))
    exports = sorted(
        (
            item for item in CSV_EXPORT_STORE.list(workspace_id)
            if str((item.get("payload") or {}).get("package_id") or "") == package_id
        ),
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    if not exports:
        return result
    export = exports[0]
    integrity = _validate_csv_export(
        workspace_id,
        record,
        export,
    )
    result.update({
        "csv_manifest_id": export["object_id"],
        "csv_manifest_resource": export["resource_uri"],
        "csv_manifest_hash": export["content_hash"],
        "csv_manifest": (export.get("payload") or {}).get("tables") or [],
        "csv_integrity": integrity,
        "resource_uris": [*result["resource_uris"], export["resource_uri"]],
    })
    if not integrity["valid"]:
        result["status"] = "partial"
        result["validation_complete"] = False
        result["blockers"] = [*result["blockers"], "csv_manifest_integrity_failed"]
        result["warnings"] = [*result["warnings"], "CSV 导出文件与不可变 manifest 不一致"]
        result["next_actions"] = ["重新从同一 run_id 导出 CSV，不得继续使用已篡改文件"]
    return result


def table_registry() -> tuple[dict[str, Any], ...]:
    """Return the single authoritative registry used by core and alias tools.

    ``alias_tool`` intentionally keeps reporting the 13 round-one tool names that
    were retired in favour of ``tables_get_table``. It is round-one migration
    metadata, locked by
    ``test_mcp_compression.py::test_removed_table_aliases_are_exact_registry_routes``.
    Renaming the field (e.g. to ``canonical_call``) changes a public response
    shape, so it needs its own decision plus test/baseline/manifest updates --
    never a drive-by cleanup.
    """

    from lvke_mcp.domains.finance.table_render import _TABLE_SPECS

    alias_overrides = {
        # Keep the full namespaced MCP identifier below client truncation
        # limits while retaining the stable business table_id and Resource URI.
        "interest-during-construction": "tables_get_construction_interest",
    }

    return tuple(
        {
            "table_id": table_id,
            "delivery_no": str((_TABLE_SPECS.get(table_id) or {}).get("delivery_no") or ""),
            "title": str((_TABLE_SPECS.get(table_id) or {}).get("title") or table_id),
            "alias_tool": alias_overrides.get(
                table_id, f"tables_get_{table_id.replace('-', '_')}"
            ),
        }
        for table_id in _delivery_keys()
    )


def _package_for_table(
    workspace_id: str,
    package_id: str,
    expected_run_id: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        record = PACKAGE_STORE.get(workspace_id, package_id)
    except ValueError:
        record = None
    if record is None:
        return None, _failure("package_not_found", "未找到十三表包")
    run_id = str((record.get("payload") or {}).get("run_id") or "")
    if expected_run_id and expected_run_id != run_id:
        return None, _failure("package_run_mismatch", "十三表包与 expected_run_id 不一致")
    return record, None


def list_tables(
    workspace_id: str,
    package_id: str,
    expected_run_id: str = "",
) -> dict[str, Any]:
    record, failure = _package_for_table(
        workspace_id, package_id, expected_run_id
    )
    if failure is not None:
        return failure
    assert record is not None
    payload = record.get("payload") or {}
    tables = payload.get("tables") if isinstance(payload.get("tables"), dict) else {}
    base_uri = str(record.get("resource_uri") or "")
    entries = [
        {
            **item,
            "available": item["table_id"] in tables,
            "resource_uri": f"{base_uri}/tables/{item['table_id']}",
        }
        for item in table_registry()
    ]
    return {
        "success": True,
        "status": "ok",
        "finance_tables_package_id": package_id,
        "run_id": payload.get("run_id"),
        "tables": entries,
        "validation_complete": False,
        "resource_uris": [item["resource_uri"] for item in entries],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def get_table(
    workspace_id: str,
    package_id: str,
    table_id: str,
    format_name: str = "structured",
    expected_run_id: str = "",
) -> dict[str, Any]:
    valid_ids = _delivery_keys()
    if table_id not in valid_ids:
        failed = _failure("table_id_invalid", "未知 table_id")
        failed["valid_table_ids"] = list(valid_ids)
        return failed
    if format_name not in {"structured", "markdown", "csv"}:
        return _failure("table_format_invalid", "format 必须为 structured、markdown 或 csv")
    record, failure = _package_for_table(
        workspace_id, package_id, expected_run_id
    )
    if failure is not None:
        return failure
    assert record is not None
    payload = record.get("payload") or {}
    table = (payload.get("tables") or {}).get(table_id)
    if not isinstance(table, dict):
        return _failure("table_not_found", "十三表包中没有该表")
    from lvke_mcp.domains.finance.table_render import structured_table_to_md

    if format_name == "markdown":
        content: Any = structured_table_to_md(table)
    elif format_name == "csv":
        headers, rows = _scalar_csv_rows(table)
        if not headers:
            return _failure("table_csv_unavailable", "该表无法安全转换为 CSV")
        from io import StringIO

        buffer = StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(headers)
        writer.writerows(rows)
        content = buffer.getvalue()
    else:
        content = table
    resource_uri = f"{record['resource_uri']}/tables/{table_id}"
    return {
        "success": True,
        "status": "ok",
        "finance_tables_package_id": package_id,
        "run_id": payload.get("run_id"),
        "table_id": table_id,
        "format": format_name,
        "content": content,
        "content_hash": sha256_json(table),
        "lineage": {
            "package_content_hash": record.get("content_hash"),
            "table_bundle_hash": payload.get("table_bundle_hash"),
            "template_version": payload.get("template_version"),
        },
        "validation_complete": False,
        "resource_uris": [resource_uri],
        "warnings": ["单表读取不代表整包或正式交付通过"],
        "blockers": [],
        "next_actions": [],
    }


def validate_table(
    workspace_id: str,
    package_id: str,
    table_id: str,
    expected_run_id: str = "",
) -> dict[str, Any]:
    fetched = get_table(
        workspace_id,
        package_id,
        table_id,
        "structured",
        expected_run_id,
    )
    if fetched.get("status") != "ok":
        return fetched
    quality = _structured_table_quality(fetched["content"])
    return {
        **fetched,
        "status": "ok" if quality["valid"] else "partial",
        "validation": quality,
        "validation_complete": False,
        "warnings": [*quality["warnings"], "局部校验不能替代整包勾稽与统一交付审查"],
        "blockers": quality["blockers"],
    }
