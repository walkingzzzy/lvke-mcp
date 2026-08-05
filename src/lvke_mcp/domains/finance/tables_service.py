"""Thirteen-table views that consume immutable finance run IDs only."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from lvke_mcp.adapters.finance_tables_repository import (
    CSV_EXPORT_STORE,
    PACKAGE_STORE,
    export_root as _export_root,
    xlsx_path_from_uri,
)
from lvke_mcp.runtime.storage import (
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
)
from lvke_mcp.domains.finance import tables_application

_load_run = tables_application.get_run
_delivery_assessment = tables_application.delivery_assessment
_delivery_keys = tables_application.delivery_keys
_structured_delivery_tables = tables_application.structured_delivery_tables
_structured_table_quality = tables_application.structured_table_quality
_validate_render = tables_application.validate_render


def _require_run_id(run_id: str) -> dict[str, Any] | None:
    """十三表只消费固化 run_id；缺 run_id 一律拒绝，绝不回退到「最新 run」。"""
    if not str(run_id or "").strip():
        return _failure("run_id_required", "缺少 run_id；十三表只消费固化 run，不做兜底选取")
    return None


def render(
    workspace_id: str,
    run_id: str,
    format_name: str = "structured",
    template_version: str = "",
) -> dict[str, Any]:
    from lvke_mcp.domains.finance.run_service import render_workspace_finance_tables

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
    structured_tables = _structured_delivery_tables(
        workspace_id,
        run_id,
        data,
    )
    validated_data = {**data, "tables": structured_tables}
    validation = _delivery_assessment(
        workspace_id,
        run_id,
        validated_data,
    )
    if "finance_run_consistency_failed" in validation.get("blockers", []):
        return _failure(
            "finance_run_consistency_failed",
            "指定 run 的不可变质量审计未通过，禁止生成十三表 package",
        )
    payload = {
        "run_id": run_id,
        "template_version": data.get("template_version"),
        "table_bundle_hash": data.get("table_bundle_hash"),
        "table_manifest": data.get("table_manifest") or [],
        "tables": structured_tables,
        "validation": validation,
        "validation_complete": bool(validation["validation_complete"]),
        "delivery_mode": "formal" if validation["validation_complete"] else "draft",
        "draft_only": not bool(validation["validation_complete"]),
        "xlsx_available": False,
    }
    status = "ok" if validation["valid"] else "partial"
    record = PACKAGE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-finance-tables.tables_render",
        status=status,
        source_ids=[run_id],
        basis={"run_id": run_id, "table_bundle_hash": data.get("table_bundle_hash")},
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


def export_xlsx(
    workspace_id: str,
    run_id: str,
    template_version: str = "",
) -> dict[str, Any]:
    rejected = _require_run_id(run_id)
    if rejected is not None:
        return rejected
    run = _load_run(workspace_id, run_id)
    if not run.get("available"):
        return _failure("run_unavailable", "指定 run 不可用，无法导出 XLSX")
    rendered = render(
        workspace_id,
        run_id,
        "structured",
        template_version,
    )
    package_id = str(rendered.get("finance_tables_package_id") or "")
    if not package_id:
        return rendered
    from lvke_mcp.adapters.spreadsheets.finance_export import export_finance_workbook

    directory = _export_root(workspace_id, "xlsx")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{require_safe_id(package_id, 'package_id')}.xlsx"
    try:
        exported = export_finance_workbook(run, path, model_version=str(run.get("model_version") or ""), run_id=run_id)
    except Exception:  # noqa: BLE001
        return _failure("xlsx_export_failed", "XLSX 导出失败")
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    xlsx_uri = PACKAGE_STORE.uri(
        workspace_id,
        package_id,
    ) + "/xlsx"
    validation = rendered.get("validation") or {}
    export_quality = exported.get("delivery_quality") or {}
    # XLSX 成功写出绝不单独抬升正式资格：须门禁通过且本次导出深度审查也通过。
    formal_ready = bool(validation.get("validation_complete")) and bool(export_quality.get("validation_complete"))
    blockers = list(rendered.get("blockers") or [])
    if validation.get("validation_complete") and not export_quality.get("validation_complete"):
        blockers.append("xlsx_delivery_quality_not_formal")
    # 权威工件已落 lvke 存储；best-effort 追加一份项目文件夹镜像副本（失败不影响权威写盘）。
    # 注：历史 mirror_artifact 已删除，镜像能力由 MCP 自有域
    # （domains/reports.artifact_mirror.mirror_file）承接，此处不再引用。
    mirror_path = None
    result = {
        **rendered,
        "xlsx_resource": xlsx_uri,
        "xlsx_hash": digest,
        "xlsx_validation": export_quality,
        "resource_uris": [*rendered.get("resource_uris", []), xlsx_uri],
        "blockers": blockers,
        "validation_complete": formal_ready,
        "delivery_mode": "formal" if formal_ready else "draft",
        "draft_only": not formal_ready,
    }
    if mirror_path:
        result["project_mirror_path"] = str(mirror_path)
    return result


def export_csv(
    workspace_id: str,
    run_id: str,
    template_version: str = "",
) -> dict[str, Any]:
    """Export the immutable structured delivery tables as native CSV, never JSON cells."""
    rejected = _require_run_id(run_id)
    if rejected is not None:
        return rejected
    rendered = render(
        workspace_id,
        run_id,
        "structured",
        template_version,
    )
    package_id = str(rendered.get("finance_tables_package_id") or "")
    if not package_id:
        return rendered
    validation = dict(rendered.get("validation") or {})
    if not validation.get("valid"):
        return _failure("tables_validation_failed", "十三表列级完整性或结构质量校验未通过，禁止导出 CSV")
    record = PACKAGE_STORE.get(workspace_id, package_id)
    payload = dict((record or {}).get("payload") or {})
    tables = dict(payload.get("tables") or {})
    directory = _export_root(
        workspace_id,
        "csv",
    ) / require_safe_id(package_id, "package_id")
    directory.mkdir(parents=True, exist_ok=True)
    csv_uris: list[str] = []
    csv_hashes: dict[str, str] = {}
    csv_manifest: list[dict[str, Any]] = []
    run = _load_run(workspace_id, run_id)
    for key in _delivery_keys():
        headers, rows = _scalar_csv_rows(tables.get(key))
        if not headers or not rows:
            return _failure("tables_validation_failed", f"表 {key} 无可导出的标量表头或数据行")
        target = directory / f"{key}.csv"
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(headers)
            writer.writerows(rows)
        csv_uris.append(
            PACKAGE_STORE.uri(
                workspace_id,
                package_id,
            ) + f"/csv/{key}"
        )
        csv_hashes[key] = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        csv_manifest.append({
            "table_id": key,
            "run_id": run_id,
            "package_id": package_id,
            "content_hash": csv_hashes[key],
            "model_version": str(run.get("model_version") or ""),
            "row_count": len(rows),
            "column_count": len(headers),
            "resource_uri": csv_uris[-1],
        })
    lineage_path = directory / "00_数据血缘.csv"
    lineage_headers = [
        "表格标识", "运行编号", "表包编号", "内容哈希", "模型版本",
        "行数", "列数", "资源标识",
    ]
    with lineage_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(lineage_headers)
        for item in csv_manifest:
            writer.writerow([
                item["table_id"], item["run_id"], item["package_id"],
                item["content_hash"], item["model_version"], item["row_count"],
                item["column_count"], item["resource_uri"],
            ])
    lineage_uri = PACKAGE_STORE.uri(
        workspace_id,
        package_id,
    ) + "/csv/00-lineage"
    lineage_hash = "sha256:" + hashlib.sha256(lineage_path.read_bytes()).hexdigest()
    export_manifest = CSV_EXPORT_STORE.put(
        workspace_id,
        {
            "schema_version": "finance_tables_csv_manifest.v1",
            "workspace_id": workspace_id,
            "run_id": run_id,
            "package_id": package_id,
            "model_version": str(run.get("model_version") or ""),
            "tables": csv_manifest,
            "lineage": {
                "resource_uri": lineage_uri,
                "content_hash": lineage_hash,
                "row_count": len(csv_manifest),
                "column_count": len(lineage_headers),
            },
        },
        producer="lvke-finance-tables.tables_export_csv",
        source_ids=[run_id, package_id],
        basis={
            "run_id": run_id,
            "package_id": package_id,
            "csv_hashes": csv_hashes,
            "lineage_hash": lineage_hash,
        },
    )
    csv_integrity = _validate_csv_export(
        workspace_id,
        PACKAGE_STORE.get(workspace_id, package_id) or {},
        export_manifest,
    )
    return {
        **rendered,
        "csv_resource_uris": csv_uris,
        "csv_hashes": csv_hashes,
        "csv_manifest": csv_manifest,
        "csv_manifest_id": export_manifest["object_id"],
        "csv_manifest_resource": export_manifest["resource_uri"],
        "csv_manifest_hash": export_manifest["content_hash"],
        "csv_integrity": csv_integrity,
        "csv_lineage_resource": lineage_uri,
        "csv_lineage_hash": lineage_hash,
        "delivery_mode": "formal" if rendered.get("validation_complete") else "draft",
        "draft_only": not bool(rendered.get("validation_complete")),
        "resource_uris": [
            *rendered.get("resource_uris", []),
            export_manifest["resource_uri"],
            *csv_uris,
            lineage_uri,
        ],
    }


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
    """Return the single authoritative registry used by core and alias tools."""

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


def _validate_csv_export(
    workspace_id: str,
    package_record: dict[str, Any],
    export_record: dict[str, Any],
) -> dict[str, Any]:
    """Verify every exported CSV against its immutable workspace manifest."""

    package_id = str(package_record.get("object_id") or "")
    package_payload = package_record.get("payload") or {}
    manifest = export_record.get("payload") or {}
    failures: list[str] = []
    expected_manifest_hash = sha256_json(manifest)
    if export_record.get("content_hash") != expected_manifest_hash:
        failures.append("manifest_content_hash_mismatch")
    expected_object_id = (
        f"{CSV_EXPORT_STORE.id_prefix}_"
        f"{expected_manifest_hash.removeprefix('sha256:')[:24]}"
    )
    if str(export_record.get("object_id") or "") != expected_object_id:
        failures.append("manifest_object_id_mismatch")
    if str(export_record.get("workspace_id") or "") != workspace_id:
        failures.append("manifest_workspace_mismatch")
    if str(manifest.get("workspace_id") or "") != workspace_id:
        failures.append("manifest_payload_workspace_mismatch")
    if str(manifest.get("package_id") or "") != package_id:
        failures.append("manifest_package_mismatch")
    if str(manifest.get("run_id") or "") != str(package_payload.get("run_id") or ""):
        failures.append("manifest_run_mismatch")

    directory = _export_root(
        workspace_id,
        "csv",
    ) / require_safe_id(package_id, "package_id")
    verified = 0
    tables = manifest.get("tables") if isinstance(manifest.get("tables"), list) else []
    for item in tables:
        if not isinstance(item, dict):
            failures.append("manifest_table_entry_invalid")
            continue
        try:
            table_id = require_safe_id(str(item.get("table_id") or ""), "table_id")
        except ValueError:
            failures.append("manifest_table_id_invalid")
            continue
        if table_id not in _delivery_keys():
            failures.append(f"manifest_unknown_table:{table_id}")
            continue
        path = directory / f"{table_id}.csv"
        if not path.is_file():
            failures.append(f"csv_missing:{table_id}")
            continue
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != str(item.get("content_hash") or ""):
            failures.append(f"csv_hash_mismatch:{table_id}")
            continue
        verified += 1
    if len(tables) != len(_delivery_keys()):
        failures.append("manifest_table_count_mismatch")

    lineage = manifest.get("lineage") if isinstance(manifest.get("lineage"), dict) else {}
    lineage_path = directory / "00_数据血缘.csv"
    if not lineage_path.is_file():
        failures.append("lineage_missing")
    else:
        digest = "sha256:" + hashlib.sha256(lineage_path.read_bytes()).hexdigest()
        if digest != str(lineage.get("content_hash") or ""):
            failures.append("lineage_hash_mismatch")
    return {
        "valid": not failures,
        "status": "passed" if not failures else "failed",
        "verified_table_count": verified,
        "expected_table_count": len(_delivery_keys()),
        "failures": failures,
    }


def csv_path_from_uri(
    uri: str,
) -> Path | None:
    prefix = "lvke://finance-tables/workspaces/"
    if not uri.startswith(prefix):
        return None
    parts = uri[len(prefix):].split("/")
    if len(parts) != 5 or parts[1] != "packages" or parts[3] != "csv":
        return None
    try:
        workspace_id = require_safe_id(parts[0], "workspace_id")
        package_id = require_safe_id(parts[2], "package_id")
        key = require_safe_id(parts[4], "table_key")
    except ValueError:
        return None
    if key == "00-lineage":
        path = _export_root(
            workspace_id,
            "csv",
        ) / package_id / "00_数据血缘.csv"
        return path if path.is_file() else None
    if key not in _delivery_keys():
        return None
    path = _export_root(
        workspace_id,
        "csv",
    ) / package_id / f"{key}.csv"
    return path if path.is_file() else None


def list_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """List only resources addressable inside the explicit workspace scope."""

    allowed_types = {"package", "csv_manifest", "csv", "xlsx"}
    if resource_type and resource_type not in allowed_types:
        return _failure("resource_type_invalid", "未知 Resource 类型过滤条件")
    entries: dict[str, dict[str, Any]] = {}

    for record in PACKAGE_STORE.list(workspace_id):
        package_id = str(record.get("object_id") or "")
        uri = str(record.get("resource_uri") or "")
        if uri:
            entries[uri] = {
                "uri": uri,
                "name": package_id,
                "resource_type": "package",
                "mime_type": "application/json",
                "created_at": record.get("created_at"),
            }
        xlsx_uri = f"{uri}/xlsx"
        if uri and xlsx_path_from_uri(xlsx_uri) is not None:
            entries[xlsx_uri] = {
                "uri": xlsx_uri,
                "name": f"{package_id}.xlsx",
                "resource_type": "xlsx",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "created_at": record.get("created_at"),
            }

    for record in CSV_EXPORT_STORE.list(workspace_id):
        uri = str(record.get("resource_uri") or "")
        payload = record.get("payload") or {}
        if uri:
            entries[uri] = {
                "uri": uri,
                "name": str(record.get("object_id") or ""),
                "resource_type": "csv_manifest",
                "mime_type": "application/json",
                "created_at": record.get("created_at"),
            }
        for item in payload.get("tables") or []:
            if not isinstance(item, dict):
                continue
            csv_uri = str(item.get("resource_uri") or "")
            if csv_uri and csv_path_from_uri(csv_uri) is not None:
                entries[csv_uri] = {
                    "uri": csv_uri,
                    "name": f"{item.get('table_id')}.csv",
                    "resource_type": "csv",
                    "mime_type": "text/csv; charset=utf-8",
                    "created_at": record.get("created_at"),
                }
        lineage_uri = str((payload.get("lineage") or {}).get("resource_uri") or "")
        if lineage_uri and csv_path_from_uri(lineage_uri) is not None:
            entries[lineage_uri] = {
                "uri": lineage_uri,
                "name": "00_数据血缘.csv",
                "resource_type": "csv",
                "mime_type": "text/csv; charset=utf-8",
                "created_at": record.get("created_at"),
            }

    try:
        pagination = paginate_resource_entries(
            (
                entry for entry in entries.values()
                if not resource_type or entry["resource_type"] == resource_type
            ),
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        code = str(exc)
        message = (
            "资源列表在分页期间发生变化，请从第一页重新列举"
            if code == "resource_list_changed"
            else "Resource 分页游标无效"
        )
        return _failure(code, message)
    page = pagination["resources"]
    return {
        "success": True,
        "status": "ok",
        "validation_complete": False,
        "resources": page,
        "next_cursor": pagination["next_cursor"],
        "has_more": pagination["has_more"],
        "snapshot_hash": pagination["snapshot_hash"],
        "resource_uris": [entry["uri"] for entry in page],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def resolve_resource(
    uri: str,
    workspace_id: str,
) -> tuple[str | bytes, str] | None:
    prefix = f"lvke://finance-tables/workspaces/{require_safe_id(workspace_id, 'workspace_id')}/"
    if not str(uri).startswith(prefix):
        return None
    if "/tables/" in uri:
        parts = uri.removeprefix(prefix).split("/")
        if len(parts) != 4 or parts[0] != "packages" or parts[2] != "tables":
            return None
        result = get_table(
            workspace_id,
            parts[1],
            parts[3],
            "structured",
        )
        if result.get("status") != "ok":
            return None
        return json.dumps(result, ensure_ascii=False, indent=2), "application/json"
    if uri.endswith("/xlsx"):
        path = xlsx_path_from_uri(uri)
        return None if path is None else (path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if "/csv/" in uri:
        path = csv_path_from_uri(uri)
        return None if path is None else (path.read_bytes(), "text/csv; charset=utf-8")
    record = CSV_EXPORT_STORE.resolve_uri(uri) or PACKAGE_STORE.resolve_uri(uri)
    if record is None or str(record.get("workspace_id") or "") != workspace_id:
        return None
    return json.dumps(record, ensure_ascii=False, indent=2), "application/json"


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


def _formal_delivery_gate(
    workspace_id: str,
    run_id: str,
) -> dict[str, Any]:
    return tables_application.formal_delivery_gate(workspace_id, run_id)


def _delivery_assessment(
    workspace_id: str,
    run_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    return tables_application.delivery_assessment(workspace_id, run_id, data)


def _validate_render(data: dict[str, Any]) -> dict[str, Any]:
    return tables_application.validate_render(data)


def _delivery_keys() -> tuple[str, ...]:
    return tables_application.delivery_keys()


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
    business_success = status == "ok"
    return {
        "success": business_success,
        "transport_success": True,
        "business_success": business_success,
        "completed": business_success,
        "outcome": status,
        "status": status,
        "finance_tables_package_id": record["object_id"],
        "run_id": payload.get("run_id"),
        "table_manifest": payload.get("table_manifest") or [],
        "validation": validation,
        "validation_complete": bool(payload.get("validation_complete", False)),
        "resource_uris": [record["resource_uri"]],
        "warnings": validation.get("warnings") or [],
        "blockers": validation.get("blockers") or [],
        "next_actions": ["将 package_id 与同一 run_id 一起绑定到研报修订"],
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
