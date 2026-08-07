"""上游依据快照：正文、证据包、JSON 对象与来源重建基准。"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


from lvke_mcp.domains.reports import doc_service

from .base import (
    DeliverableArtifactError,
    _SAFE_REVISION_ID,
    _bytes_hash,
    _canonical_hash,
)

from .storage import (
    _strict_read_json,
    _workspace_root,
)


def _document_snapshot(workspace_id: str) -> tuple[dict[str, Any], str, dict[str, Any]]:
    root = _workspace_root(workspace_id)
    meta_path = root / "workspace_meta.json"
    if not meta_path.is_file():
        raise DeliverableArtifactError("WORKSPACE_NOT_FOUND", "工作区不存在")
    meta = _strict_read_json(
        meta_path, code="WORKSPACE_STATE_CORRUPT", label="工作区元数据",
    )
    if not isinstance(meta, dict):
        raise DeliverableArtifactError(
            "WORKSPACE_STATE_CORRUPT", "工作区元数据必须是对象",
        )
    revision_id = str(meta.get("current_revision_id") or "")
    if not _SAFE_REVISION_ID.fullmatch(revision_id):
        raise DeliverableArtifactError(
            "DOCUMENT_REVISION_INVALID", "当前文档修订 id 缺失或不合法",
        )
    # 正文路径必须复用写入侧的同一函数（doc_service._revision_dir），
    # 不要在这里重新拼 root / "revisions"：修订正文已迁到交付物根，
    # 两边各自拼路径会造成写得进、读不到（DOCUMENT_REVISION_NOT_FOUND）。
    report_path = doc_service._revision_dir(workspace_id, revision_id) / "report.md"  # noqa: SLF001
    try:
        content = report_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DeliverableArtifactError(
            "DOCUMENT_REVISION_NOT_FOUND", "当前文档修订正文不存在",
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise DeliverableArtifactError(
            "DOCUMENT_REVISION_UNREADABLE", "当前文档修订正文不可读",
        ) from exc
    metadata_material = {
        "title": str(meta.get("title") or ""),
        "report_type": str(meta.get("report_type") or ""),
        "doc_kind": str(meta.get("doc_kind") or ""),
        "requirement": copy.deepcopy(meta.get("requirement") or {}),
        "cover": copy.deepcopy(meta.get("cover") or {}),
        "current_revision_id": revision_id,
        "workspace_version": meta.get("workspace_version"),
    }
    snapshot = {
        "revision_id": revision_id,
        "content_hash": _bytes_hash(content.encode("utf-8")),
        "content_bytes": len(content.encode("utf-8")),
        "metadata_hash": _canonical_hash(metadata_material),
    }
    return snapshot, content, meta


def _evidence_pack_snapshot(workspace_id: str) -> tuple[dict[str, Any], Any]:
    """MCP 等价于 hermes ``evidence_pack.json`` 工件：读数据链 EVIDENCE_STORE。

    快照 hash 用最新记录的服务端固化 ``content_hash``（内容变化即变化）；
    存储不可用或空按缺失处理（present=False，进入 basis 指纹）。
    """
    try:
        from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE

        records = EVIDENCE_STORE.list(workspace_id) or []
    except Exception as exc:  # noqa: BLE001 - 数据链不可用按缺失处理
        return {
            "present": False,
            "hash": _canonical_hash({
                "artifact": "evidence_pack",
                "present": False,
                "error": type(exc).__name__,
            }),
        }, None
    records = [r for r in records if isinstance(r, dict)]
    if not records:
        return {
            "present": False,
            "hash": _canonical_hash({"artifact": "evidence_pack", "present": False}),
        }, None
    latest = sorted(
        records,
        key=lambda row: str(row.get("created_at") or ""),
        reverse=True,
    )[0]
    record = dict(latest)
    hash_value = str(record.get("content_hash") or "")
    if not hash_value:
        hash_value = _canonical_hash(record)
    payload = record.get("payload")
    return (
        {"present": True, "hash": hash_value},
        payload if isinstance(payload, dict) else {},
    )


def _json_snapshot(
    workspace_id: str,
    name: str,
) -> tuple[dict[str, Any], Any]:
    if name == "evidence_pack":
        return _evidence_pack_snapshot(workspace_id)
    path = _workspace_root(workspace_id) / f"{name}.json"
    if not path.exists():
        return {
            "present": False,
            "hash": _canonical_hash({"artifact": name, "present": False}),
        }, None
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        try:
            raw = path.read_bytes()
        except OSError:
            raw = b""
        return {
            "present": True,
            "hash": _bytes_hash(raw),
            "error": f"{name}_corrupt:{type(exc).__name__}",
        }, None
    return {"present": True, "hash": _canonical_hash(value)}, value


def _source_basis_snapshot(workspace_id: str) -> dict[str, Any]:
    """MCP 等价于 hermes ``source_files_api.source_basis_snapshot``。

    读 ``lvke_data_acquisition.SOURCE_STORE`` 的 source_snapshot 记录，
    构造同 schema（source_basis.v1 / files / jobs / hash）的稳定快照；
    存储不可用时 fail-closed（error 进入 basis 指纹）。
    """
    unavailable = ""
    try:
        from lvke_mcp.adapters.data_acquisition_repository import SOURCE_STORE

        records = SOURCE_STORE.list(workspace_id) or []
    except Exception as exc:  # noqa: BLE001 - 资料链不可用 fail-closed
        records = []
        unavailable = type(exc).__name__
    files = []
    for row in records:
        if not isinstance(row, dict):
            continue
        files.append({
            "object_id": str(row.get("object_id") or ""),
            "content_hash": str(row.get("content_hash") or ""),
            "basis_hash": str(row.get("basis_hash") or ""),
        })
    files.sort(key=lambda item: str(item.get("object_id") or ""))
    material = {
        "schema_version": "source_basis.v1",
        "workspace_id": workspace_id,
        "files": files,
        "jobs": [],
    }
    if unavailable:
        material["error"] = unavailable
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return {
        **material,
        "file_count": len(files),
        "job_count": 0,
        "hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
    }


def _fresh_readiness(workspace_id: str) -> dict[str, Any]:
    try:
        from lvke_mcp.domains.reports import readiness as report_readiness

        value = report_readiness.build_readiness(workspace_id, persist=False)
    except Exception as exc:  # noqa: BLE001 - captured as a fail-closed basis error
        return {
            "workspace_id": workspace_id,
            "publishable": False,
            "blocking_issues": ["readiness_compute_failed"],
            "blockers": [{
                "code": "readiness_compute_failed",
                "message": f"发布就绪度计算失败: {type(exc).__name__}",
            }],
            "error": type(exc).__name__,
        }
    if not isinstance(value, dict):
        return {
            "workspace_id": workspace_id,
            "publishable": False,
            "blocking_issues": ["readiness_invalid_result"],
            "blockers": [{
                "code": "readiness_invalid_result",
                "message": "发布就绪度计算结果格式错误",
            }],
            "error": "invalid_result",
        }
    return value


def _load_finance_run(workspace_id: str, run_id: str) -> dict[str, Any] | None:
    if not run_id or run_id.startswith("acqrun_"):
        return None
    try:
        from lvke_mcp.domains.finance import run_store

        value = run_store.load_run(workspace_id, run_id)
    except Exception as exc:  # noqa: BLE001 - error is bound into the basis
        return {"_load_error": type(exc).__name__, "run_id": run_id}
    return value if isinstance(value, dict) and value else None
