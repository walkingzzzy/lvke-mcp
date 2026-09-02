"""XLSX / CSV 导出与 CSV 导出门禁。"""

from __future__ import annotations

import csv
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from lvke_mcp.adapters.finance_tables_repository import CSV_EXPORT_STORE, PACKAGE_STORE, export_root as _export_root
from lvke_mcp.runtime.storage import require_safe_id, sha256_json
from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    SIM_A_FORMAL,
    validate_finance_run,
    validate_finance_tables_package,
)

from .base import (
    _delivery_count_semantics,
    _delivery_keys,
    _delivery_table_contract_hash,
    _failure,
    _load_run,
    _package_result,
    _require_run_id,
    _scalar_csv_rows,
)

from .render import (
    render,
)


def _resolve_package(
    workspace_id: str,
    run_id: str,
    template_version: str,
    finance_tables_package_id: str,
) -> dict[str, Any]:
    """Bind an existing package when given one; only render as a fallback.

    导出此前无条件重新 ``render()``，而 package payload 内嵌了可变的跨工件门禁
    状态（validation），content hash 会随之漂移：先 tables_render 得到
    ftp_431，再导出就落成 ftp_548，XLSX 绑的是它自己新造的包。给定
    package_id 时必须消费既有包，不得重新渲染。
    """

    package_id = str(finance_tables_package_id or "").strip()
    if not package_id:
        rendered = render(workspace_id, run_id, "structured", template_version)
        return {
            "rendered": rendered,
            "package_id": str(rendered.get("finance_tables_package_id") or ""),
            "reused": False,
        }

    package_id = require_safe_id(package_id, "finance_tables_package_id")
    record = PACKAGE_STORE.get(workspace_id, package_id)
    if record is None:
        return {
            "rendered": _failure(
                "finance_tables_package_not_found",
                "未找到指定 finance_tables_package_id，无法导出",
            ),
            "package_id": "",
            "reused": False,
        }
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    bound_run_id = str(payload.get("run_id") or "")
    if bound_run_id != run_id:
        return {
            "rendered": _failure(
                "finance_tables_package_run_mismatch",
                f"指定 package 绑定 run={bound_run_id}，与请求 run={run_id} 不一致",
            ),
            "package_id": "",
            "reused": False,
        }
    if str(payload.get("evidence_policy") or "") == SIM_A_FORMAL:
        try:
            validate_finance_tables_package(workspace_id, record)
        except FormalLineageError as exc:
            return {
                "rendered": _failure(exc.code, exc.message),
                "package_id": "",
                "reused": False,
            }
    validation = payload.get("validation") or {}
    rendered = {
        **_package_result(record, validation, str(record.get("status") or "ok")),
        "delivery_mode": payload.get("delivery_mode"),
        "draft_only": payload.get("draft_only"),
    }
    return {"rendered": rendered, "package_id": package_id, "reused": True}


def _package_manifest_hash(record: dict[str, Any]) -> str:
    """Hash the package's own 14-table delivery manifest, not the model manifest.

    ``run.manifest_hash`` 描述的是 ModelManifest（口径/政策版本），与「这份工件绑的
    是哪 14 张表」无关，且在 estimate_preview 下常为 None。工件自述来源必须能核对
    表内容，所以取 package payload 里逐表 content_hash 的清单哈希。
    """

    payload = (record or {}).get("payload") or {}
    manifest = payload.get("table_manifest")
    if not isinstance(manifest, list) or not manifest:
        return ""
    digest = [
        {
            "table_id": str(item.get("table_id") or ""),
            "content_hash": str(item.get("content_hash") or ""),
        }
        for item in manifest
        if isinstance(item, dict)
    ]
    return sha256_json(digest)


def _verify_package_tables(record: dict[str, Any]) -> dict[str, Any]:
    """Re-hash the stored tables and compare against the package's own manifest.

    导出必须证明自己搬运的就是主包那 14 张表：逐表重算 content_hash 与
    table_manifest 比对，任一不一致或缺表即 fail-closed，不允许「导出时悄悄换了
    一份表」。
    """

    payload = (record or {}).get("payload") or {}
    tables = payload.get("tables") if isinstance(payload.get("tables"), dict) else {}
    manifest = payload.get("table_manifest")
    if not isinstance(manifest, list) or not manifest:
        return {"valid": False, "failures": ["package_table_manifest_missing"], "verified": 0}
    manifest_codes = [
        str(item.get("table_code") or item.get("table_id") or "")
        for item in manifest
        if isinstance(item, dict)
    ]
    failures: list[str] = []
    if manifest_codes != list(_delivery_keys()):
        failures.append("package_table_manifest_order_or_membership_mismatch")
    expected_contract_hash = _delivery_table_contract_hash()
    if any(
        str(item.get("contract_hash") or "") != expected_contract_hash
        for item in manifest
        if isinstance(item, dict)
    ):
        failures.append("package_table_manifest_contract_hash_mismatch")
    recorded = {
        str(item.get("table_id") or ""): str(item.get("content_hash") or "")
        for item in manifest
        if isinstance(item, dict)
    }
    verified = 0
    for key in _delivery_keys():
        table = tables.get(key)
        if not isinstance(table, dict):
            failures.append(f"package_table_missing:{key}")
            continue
        expected = recorded.get(key)
        if not expected:
            failures.append(f"package_table_unmanifested:{key}")
            continue
        if sha256_json(table) != expected:
            failures.append(f"package_table_content_hash_mismatch:{key}")
            continue
        verified += 1
    return {"valid": not failures, "failures": failures, "verified": verified}


def export_xlsx(
    workspace_id: str,
    run_id: str,
    template_version: str = "",
    finance_tables_package_id: str = "",
    validation_scope: str = "formal",
) -> dict[str, Any]:
    rejected = _require_run_id(run_id)
    if rejected is not None:
        return rejected
    scope = str(validation_scope or "formal").strip().lower()
    if scope not in {"technical", "formal"}:
        return _failure(
            "validation_scope_invalid",
            "validation_scope 必须为 technical 或 formal",
        )
    technical_preview = scope == "technical"
    run = _load_run(workspace_id, run_id)
    if not run.get("available"):
        return _failure("run_unavailable", "指定 run 不可用，无法导出 XLSX")
    canonical_lineage: dict[str, Any] = {}
    if str(run.get("evidence_policy") or "") == SIM_A_FORMAL:
        try:
            canonical_lineage = validate_finance_run(workspace_id, run_id)
        except FormalLineageError as exc:
            return _failure(exc.code, exc.message)
    resolved = _resolve_package(
        workspace_id, run_id, template_version, finance_tables_package_id
    )
    rendered = resolved["rendered"]
    package_id = resolved["package_id"]
    if not package_id:
        return rendered
    package_record = PACKAGE_STORE.get(workspace_id, package_id) or {}
    integrity = _verify_package_tables(package_record)
    if not integrity["valid"]:
        return _failure(
            "finance_tables_package_content_mismatch",
            "包内十三表内容哈希与自带清单不一致，禁止导出：" + "、".join(integrity["failures"][:5]),
        )
    from lvke_mcp.adapters.spreadsheets.finance_export import export_finance_workbook

    validation = dict(rendered.get("validation") or {})
    package_formal_ready = bool(
        validation.get("valid") and rendered.get("validation_complete")
    )
    artifact_notice = (
        _XLSX_TECHNICAL_PREVIEW_BANNER
        if technical_preview
        else "" if package_formal_ready
        else _XLSX_FORMAL_CANDIDATE_BANNER
    )
    directory = _export_root(workspace_id, "xlsx")
    directory.mkdir(parents=True, exist_ok=True)
    filename_suffix = ".technical.xlsx" if technical_preview else ".xlsx"
    path = directory / (
        f"{require_safe_id(package_id, 'package_id')}{filename_suffix}"
    )
    try:
        exported = export_finance_workbook(
            run,
            path,
            model_version=str(run.get("model_version") or ""),
            run_id=run_id,
            artifact_notice=artifact_notice,
        )
    except Exception:  # noqa: BLE001
        return _failure("xlsx_export_failed", "XLSX 导出失败")
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    xlsx_uri = PACKAGE_STORE.uri(
        workspace_id,
        package_id,
    ) + ("/xlsx-technical" if technical_preview else "/xlsx")
    export_quality = dict(exported.get("delivery_quality") or {})
    if technical_preview:
        export_quality.update({
            "validation_complete": False,
            "release_grade": "technical_preview",
            "not_for_formal_use": True,
        })
    quality_issues = [str(item) for item in rendered.get("quality_issues") or []]
    if not package_formal_ready:
        quality_issues.append("xlsx_formal_quality_incomplete")
    if not export_quality.get("validation_complete"):
        quality_issues.append("xlsx_delivery_quality_incomplete")
    formal_ready = bool(
        not technical_preview
        and package_formal_ready
        and export_quality.get("validation_complete")
    )
    release_limitations = sorted(set(quality_issues))
    blockers: list[str] = []

    # 权威工件已落 lvke 存储；best-effort 追加一份项目文件夹镜像副本（失败不影响权威写盘）。
    # 注：历史 mirror_artifact 已删除，镜像能力由 MCP 自有域
    # （domains/reports.artifact_mirror.mirror_file）承接，此处不再引用。
    mirror_path = None
    result = {
        **rendered,
        "validation_scope": scope,
        "technical_preview": technical_preview,
        "release_grade": "technical_preview" if technical_preview else (
            "formal" if formal_ready else "formal_candidate"
        ),
        "not_for_formal_use": not formal_ready,
        "release_eligible": formal_ready,
        "release_limitations": release_limitations,
        "in_file_marking": artifact_notice,
        "xlsx_resource": xlsx_uri,
        "xlsx_hash": digest,
        "xlsx_validation": export_quality,
        # 交付物必须自述来源包：此前只靠文件名约定，无法核对 XLSX 与主包同源。
        "source_package_id": package_id,
        "source_run_id": run_id,
        "source_package_reused": bool(resolved["reused"]),
        # manifest_hash 是「这 14 张表」的清单哈希，用于核对工件与主包同源；
        # ModelManifest 的哈希另走 model_manifest_hash，两者语义不可混用。
        "manifest_hash": _package_manifest_hash(package_record),
        "model_manifest_hash": str(exported.get("manifest_hash") or ""),
        "package_table_integrity": integrity,
        "model_version": str(run.get("model_version") or ""),
        **canonical_lineage,
        # 交付物落盘绝对路径：调用方据此直接打开文件，不必反解 lvke:// URI。
        "deliverable_path": str(path),
        "resource_uris": [*rendered.get("resource_uris", []), xlsx_uri],
        "blockers": blockers,
        "quality_issues": sorted(set(quality_issues)),
        "warnings": [
            *list(rendered.get("warnings") or []),
            *(f"质量提示：{item}" for item in sorted(set(quality_issues))),
        ],
        "validation_complete": formal_ready,
        "delivery_mode": "formal" if formal_ready else "draft",
        "draft_only": not formal_ready,
    }
    if mirror_path:
        result["project_mirror_path"] = str(mirror_path)
    return result


_XLSX_TECHNICAL_PREVIEW_BANNER = (
    "【估算预览】仅供过程验收使用，不得作为正式投资决策依据。"
)

_XLSX_FORMAL_CANDIDATE_BANNER = (
    "【正式候选·含限制】本文件由 validation_scope=formal 导出，"
    "但十三表尚未通过完整正式交付门禁；"
    "不得作为对外正式交付物或最终投资决策依据。"
)


# 技术预览 CSV 的文件内标记：必须写在文件第一行，让脱离 MCP 响应单看文件的人
# 也知道它不可正式使用。不是注释符（CSV 无注释语法），而是一整行显式声明。
_CSV_TECHNICAL_PREVIEW_BANNER = (
    "【技术预览·不可正式使用】本文件由 validation_scope=technical 导出，"
    "未通过正式财务交付门禁，仅供过程验收与结构核对；不得作为对外交付物或决策依据。"
)

_CSV_FORMAL_CANDIDATE_BANNER = (
    "【正式候选·含限制】本文件由 validation_scope=formal 导出，"
    "但十三表尚未通过完整正式交付门禁；"
    "不得作为对外正式交付物或最终投资决策依据。"
)


def export_csv(
    workspace_id: str,
    run_id: str,
    template_version: str = "",
    finance_tables_package_id: str = "",
    validation_scope: str = "formal",
) -> dict[str, Any]:
    """Export the immutable structured delivery tables as native CSV, never JSON cells.

    ``validation_scope``：
    - ``formal``（默认）：正式门禁不过就拒绝导出，绝不放宽。
    - ``technical``：允许在正式门禁未过时产出过程验收文件，但每个 CSV 第一行写入
      不可正式使用标记，且响应里 ``validation_complete=False``、
      ``release_grade=technical_preview``。这不是降低门禁，是给过程验收一个诚实
      的独立出口，避免有人删门禁换取文件。
    """

    rejected = _require_run_id(run_id)
    if rejected is not None:
        return rejected
    scope = str(validation_scope or "formal").strip().lower()
    if scope not in {"technical", "formal"}:
        return _failure(
            "validation_scope_invalid",
            "validation_scope 必须为 technical 或 formal",
        )
    technical_preview = scope == "technical"
    resolved = _resolve_package(
        workspace_id, run_id, template_version, finance_tables_package_id
    )
    rendered = resolved["rendered"]
    package_id = resolved["package_id"]
    if not package_id:
        return rendered
    validation = dict(rendered.get("validation") or {})
    package_formal_ready = bool(
        validation.get("valid") and rendered.get("validation_complete")
    )
    csv_header_mark = (
        _CSV_TECHNICAL_PREVIEW_BANNER
        if technical_preview
        else "" if package_formal_ready
        else _CSV_FORMAL_CANDIDATE_BANNER
    )
    record = PACKAGE_STORE.get(workspace_id, package_id)
    package_integrity = _verify_package_tables(record or {})
    if not package_integrity["valid"]:
        return _failure(
            "finance_tables_package_content_mismatch",
            "包内十三表内容哈希与自带清单不一致，禁止导出："
            + "、".join(package_integrity["failures"][:5]),
        )
    payload = dict((record or {}).get("payload") or {})
    tables = dict(payload.get("tables") or {})
    package_manifest = [
        item
        for item in (payload.get("table_manifest") or [])
        if isinstance(item, dict)
    ]
    package_manifest_by_key = {
        str(item.get("table_code") or item.get("table_id") or ""): item
        for item in package_manifest
    }
    package_contract_hash = str(
        payload.get("table_contract_hash")
        or next(
            (
                item.get("contract_hash")
                for item in package_manifest
                if item.get("contract_hash")
            ),
            "",
        )
    )
    count_semantics = _delivery_count_semantics()
    directory = _export_root(
        workspace_id,
        "csv",
    ) / require_safe_id(package_id, "package_id")
    csv_uris: list[str] = []
    csv_hashes: dict[str, str] = {}
    csv_manifest: list[dict[str, Any]] = []
    run = _load_run(workspace_id, run_id)
    canonical_lineage: dict[str, Any] = {}
    if str(run.get("evidence_policy") or "") == SIM_A_FORMAL:
        try:
            canonical_lineage = validate_finance_run(workspace_id, run_id)
            validate_finance_tables_package(workspace_id, record or {})
        except FormalLineageError as exc:
            return _failure(exc.code, exc.message)
    # Atomic publication: validate all scalar inputs before touching the final
    # directory, then build every CSV and lineage file in a sibling temporary
    # directory.  A failed table or write therefore leaves no partial readable
    # deliverable under the package URI.
    prepared: list[tuple[str, list[str], list[list[Any]]]] = []
    for key in _delivery_keys():
        headers, rows = _scalar_csv_rows(tables.get(key))
        if not headers or not rows:
            return _failure("tables_validation_failed", f"表 {key} 无可导出的标量表头或数据行")
        prepared.append((key, headers, rows))

    csv_root = directory.parent
    csv_root.mkdir(parents=True, exist_ok=True)
    temporary_dir: Path | None = None
    reuse_existing = False
    if directory.is_dir():
        # Deterministic package exports are idempotent.  Reuse a complete
        # existing directory; never overwrite it with a partially built retry.
        expected = {f"{key}.csv" for key, _headers, _rows in prepared} | {"00_数据血缘.csv"}
        if not expected.issubset({item.name for item in directory.iterdir()}):
            return _failure("csv_export_incomplete_existing", "检测到不完整的既有 CSV 导出目录，拒绝覆盖")
        reuse_existing = True
    else:
        temporary_dir = Path(tempfile.mkdtemp(prefix=f".{directory.name}.", dir=csv_root))

    build_dir = directory if temporary_dir is None else temporary_dir
    try:
        for key, headers, rows in prepared:
            target = build_dir / f"{key}.csv"
            if not reuse_existing:
                with target.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle, lineterminator="\r\n")
                    if technical_preview or csv_header_mark:
                        writer.writerow([csv_header_mark])
                    writer.writerow(headers)
                    writer.writerows(rows)
            csv_uris.append(
                PACKAGE_STORE.uri(workspace_id, package_id) + f"/csv/{key}"
            )
            csv_hashes[key] = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
            source_contract = package_manifest_by_key.get(key, {})
            csv_manifest.append({
                "table_code": key,
                "table_id": key,
                "delivery_no": source_contract.get("delivery_no"),
                "title": source_contract.get("title"),
                "order": source_contract.get("order"),
                "table_contract_hash": source_contract.get("contract_hash"),
                "run_id": run_id,
                "package_id": package_id,
                "content_hash": csv_hashes[key],
                "model_version": str(run.get("model_version") or ""),
                **canonical_lineage,
                "row_count": len(rows),
                "column_count": len(headers),
                "resource_uri": csv_uris[-1],
            })
        lineage_path = build_dir / "00_数据血缘.csv"
        lineage_headers = [
            "表格标识", "运行编号", "表包编号", "内容哈希", "模型版本",
            "行数", "列数", "资源标识",
        ]
        if not reuse_existing:
            with lineage_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\r\n")
                if technical_preview or csv_header_mark:
                    writer.writerow([csv_header_mark])
                writer.writerow(lineage_headers)
                for item in csv_manifest:
                    writer.writerow([
                        item["table_id"], item["run_id"], item["package_id"],
                        item["content_hash"], item["model_version"], item["row_count"],
                        item["column_count"], item["resource_uri"],
                    ])
        if temporary_dir is not None:
            os.replace(temporary_dir, directory)
            temporary_dir = None
    except Exception:
        if temporary_dir is not None:
            import shutil
            shutil.rmtree(temporary_dir, ignore_errors=True)
        return _failure("csv_export_failed", "CSV 导出失败，未发布部分工件")
    lineage_path = directory / "00_数据血缘.csv"
    lineage_headers = [
        "表格标识", "运行编号", "表包编号", "内容哈希", "模型版本",
        "行数", "列数", "资源标识",
    ]
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
            "validation_scope": scope,
            "release_grade": "technical_preview" if technical_preview else "formal_candidate",
            "model_version": str(run.get("model_version") or ""),
            "table_contract_hash": package_contract_hash,
            **count_semantics,
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
            "formal_promotion": canonical_lineage.get("formal_promotion"),
        },
    )
    csv_integrity = _validate_csv_export(
        workspace_id,
        PACKAGE_STORE.get(workspace_id, package_id) or {},
        export_manifest,
    )
    quality_issues = [str(item) for item in rendered.get("quality_issues") or []]
    if not package_formal_ready:
        quality_issues.append("csv_formal_quality_incomplete")
    if not csv_integrity.get("valid"):
        quality_issues.extend(str(item) for item in csv_integrity.get("failures") or [])
    csv_formal_ready = bool(
        not technical_preview
        and package_formal_ready
        and csv_integrity.get("valid", False)
    )
    release_limitations = sorted(set(quality_issues))
    csv_blockers: list[str] = []
    return {
        **rendered,
        "validation_scope": scope,
        "technical_preview": technical_preview,
        "release_grade": "technical_preview" if technical_preview else (
            "formal" if csv_formal_ready else "formal_candidate"
        ),
        "not_for_formal_use": not csv_formal_ready,
        "release_eligible": csv_formal_ready,
        "release_limitations": release_limitations,
        "in_file_marking": csv_header_mark,
        "csv_resource_uris": csv_uris,
        "csv_hashes": csv_hashes,
        "csv_manifest": csv_manifest,
        "csv_manifest_id": export_manifest["object_id"],
        "csv_manifest_resource": export_manifest["resource_uri"],
        "csv_manifest_hash": export_manifest["content_hash"],
        "csv_integrity": csv_integrity,
        "source_package_id": package_id,
        "source_run_id": run_id,
        "source_package_reused": bool(resolved["reused"]),
        "manifest_hash": _package_manifest_hash(record or {}),
        "package_table_integrity": package_integrity,
        **canonical_lineage,
        "csv_lineage_resource": lineage_uri,
        "csv_lineage_hash": lineage_hash,
        # 交付物落盘绝对目录：14 个 CSV（13 表 + 血缘表）都在此目录下。
        "deliverable_path": str(directory),
        "blockers": csv_blockers,
        "quality_issues": sorted(set(quality_issues)),
        "warnings": [
            *list(rendered.get("warnings") or []),
            *(f"质量提示：{item}" for item in sorted(set(quality_issues))),
        ],
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
    table_codes = [
        str(item.get("table_code") or item.get("table_id") or "")
        for item in tables
        if isinstance(item, dict)
    ]
    if table_codes != list(_delivery_keys()):
        failures.append("manifest_table_order_or_membership_mismatch")
    package_manifest = [
        item
        for item in (package_payload.get("table_manifest") or [])
        if isinstance(item, dict)
    ]
    package_contract_hash = str(
        package_payload.get("table_contract_hash")
        or next(
            (
                item.get("contract_hash")
                for item in package_manifest
                if item.get("contract_hash")
            ),
            "",
        )
    )
    if str(manifest.get("table_contract_hash") or "") != package_contract_hash:
        failures.append("manifest_table_contract_hash_mismatch")
    if any(
        str(item.get("table_contract_hash") or "") != package_contract_hash
        for item in tables
        if isinstance(item, dict)
    ):
        failures.append("manifest_table_entry_contract_hash_mismatch")
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

    # 标记必须真在文件里，不能只在 MCP 响应里：文件会脱离响应单独流转。
    if str(manifest.get("validation_scope") or "") == "technical":
        unmarked = [
            path.name
            for path in sorted(directory.glob("*.csv"))
            if _CSV_TECHNICAL_PREVIEW_BANNER not in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[:1]
        ]
        if unmarked:
            failures.append("technical_preview_marking_missing:" + ",".join(unmarked[:3]))
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
