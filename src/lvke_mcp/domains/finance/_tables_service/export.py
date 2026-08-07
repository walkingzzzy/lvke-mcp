"""XLSX / CSV 导出与 CSV 导出门禁。"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any

from lvke_mcp.adapters.finance_tables_repository import CSV_EXPORT_STORE, PACKAGE_STORE, export_root as _export_root
from lvke_mcp.runtime.storage import require_safe_id, sha256_json

from .base import (
    _delivery_keys,
    _failure,
    _load_run,
    _require_run_id,
    _scalar_csv_rows,
)

from .render import (
    render,
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
        # 交付物落盘绝对路径：调用方据此直接打开文件，不必反解 lvke:// URI。
        "deliverable_path": str(path),
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
    # 与 export_xlsx 对称：CSV 成功写出绝不单独抬升正式资格，须 package 门禁通过
    # 且本次导出的逐文件深度审查（_validate_csv_export）也通过。
    csv_formal_ready = bool(rendered.get("validation_complete")) and bool(
        csv_integrity.get("valid")
    )
    csv_blockers = list(rendered.get("blockers") or [])
    if rendered.get("validation_complete") and not csv_integrity.get("valid"):
        csv_blockers.append("csv_delivery_quality_not_formal")
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
        # 交付物落盘绝对目录：14 个 CSV（13 表 + 血缘表）都在此目录下。
        "deliverable_path": str(directory),
        "blockers": csv_blockers,
        "validation_complete": csv_formal_ready,
        "delivery_mode": "formal" if csv_formal_ready else "draft",
        "draft_only": not csv_formal_ready,
        "resource_uris": [
            *rendered.get("resource_uris", []),
            export_manifest["resource_uri"],
            *csv_uris,
            lineage_uri,
        ],
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
