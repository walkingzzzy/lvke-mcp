"""工件目录构建、docx 元信息写入与落盘文件校验。"""

from __future__ import annotations

import copy
import io
import json
import mimetypes
import os
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any



from .base import (
    DeliverableArtifactError,
    MANIFEST_SCHEMA_VERSION,
    _canonical_hash,
    _file_hash,
    _now,
)

from .storage import (
    _artifact_root,
    _artifacts_root,
    _write_bytes,
    _write_json_file,
)

from .support_files import (
    _collect_support_files,
    _is_relative_to,
    _path_has_symlink,
)


def _set_docx_metadata(
    data: bytes,
    *,
    title: str,
    subject: str,
    keywords: Sequence[str],
    comments: str,
) -> bytes:
    try:
        from docx import Document

        document = Document(io.BytesIO(data))
        properties = document.core_properties
        properties.title = str(title or "可行性研究报告")[:255]
        properties.subject = str(subject or "")[:255]
        properties.keywords = ", ".join(str(item) for item in keywords)[:255]
        properties.comments = str(comments or "")[:255]
        output = io.BytesIO()
        document.save(output)
        return output.getvalue()
    except Exception as exc:  # noqa: BLE001 - metadata is part of the contract
        raise DeliverableArtifactError(
            "DOCX_METADATA_WRITE_FAILED",
            "DOCX 元数据写入失败",
            details={"error": type(exc).__name__},
        ) from exc


def _file_entry(path: Path, root: Path, *, role: str) -> dict[str, Any]:
    digest, size = _file_hash(path)
    relative = path.relative_to(root).as_posix()
    return {
        "name": relative,
        "role": role,
        "sha256": digest,
        "size_bytes": size,
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    }


def _build_artifact_directory(
    workspace_id: str,
    artifact_id: str,
    *,
    kind: str,
    docx_bytes: bytes,
    basis: dict[str, Any],
    blocker_summary: dict[str, Any],
    context: dict[str, Any],
    docx_font_audit: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    artifacts_root = _artifacts_root(workspace_id)
    artifacts_root.mkdir(parents=True, exist_ok=True)
    final_root = _artifact_root(workspace_id, artifact_id)
    temp_root = artifacts_root / f".{artifact_id}.{uuid.uuid4().hex}.tmp"
    renamed = False
    if final_root.exists():
        raise DeliverableArtifactError(
            "ARTIFACT_ALREADY_EXISTS", "交付工件目录已存在",
        )
    try:
        temp_root.mkdir(parents=False, exist_ok=False)
        report_path = temp_root / "report.docx"
        _write_bytes(report_path, docx_bytes)
        basis_path = temp_root / "basis_snapshot.json"
        _write_json_file(basis_path, basis)

        payload_files = [
            _file_entry(report_path, temp_root, role="report_docx"),
            _file_entry(basis_path, temp_root, role="basis_snapshot"),
        ]
        support_warnings: list[dict[str, Any]] = []
        if kind == "formal":
            support_files, support_warnings = _collect_support_files(
                workspace_id, temp_root, basis, context,
            )
            payload_files.extend(support_files)

        finance_basis = basis.get("finance") or {}
        finance_run = finance_basis.get("run_snapshot") or {}
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "workspace_id": workspace_id,
            "kind": kind,
            "template_version": basis.get("template_version"),
            "basis_fingerprint": basis.get("fingerprint"),
            "document": copy.deepcopy(basis.get("document") or {}),
            "workspace_version": basis.get("workspace_version"),
            "finance": {
                "run_id": finance_basis.get("run_id"),
                "run_kind": finance_basis.get("run_kind"),
                "run_hash": finance_basis.get("run_hash"),
                "binding_hash": (finance_basis.get("binding") or {}).get("hash"),
                "input_hash": finance_run.get("input_hash"),
                "spec_hash": finance_run.get("spec_hash"),
                "table_bundle_hash": finance_run.get("table_bundle_hash"),
                "manifest_hash": finance_run.get("manifest_hash"),
                "model_version": finance_run.get("model_version"),
                "template_version": finance_run.get("template_version"),
                "publish_gate_hash": _canonical_hash(
                    finance_basis.get("publish_gate") or {}
                ),
            },
            "governed_snapshots": copy.deepcopy(basis.get("artifacts") or {}),
            "appendix_file_snapshots": copy.deepcopy(
                basis.get("appendix_files") or []
            ),
            "blocker_summary": copy.deepcopy(blocker_summary),
            "support_file_warnings": copy.deepcopy(support_warnings),
            "created_at": _now(),
            "payload_files": copy.deepcopy(payload_files),
            "docx_font_audit": copy.deepcopy(docx_font_audit or {}),
        }
        manifest_path = temp_root / "manifest.json"
        _write_json_file(manifest_path, manifest)
        manifest_entry = _file_entry(manifest_path, temp_root, role="manifest")
        index = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "workspace_id": workspace_id,
            "kind": kind,
            "basis_fingerprint": basis.get("fingerprint"),
            "files": [*copy.deepcopy(payload_files), copy.deepcopy(manifest_entry)],
        }
        index_path = temp_root / "index.json"
        _write_json_file(index_path, index)
        index_entry = _file_entry(index_path, temp_root, role="index")
        files = [*payload_files, manifest_entry, index_entry]

        failures = _verify_files(temp_root, files)
        if failures:
            raise DeliverableArtifactError(
                "ARTIFACT_INTEGRITY_FAILED",
                "交付工件生成后的完整性校验失败",
                details={"failures": failures},
            )
        os.replace(temp_root, final_root)
        renamed = True
        failures = _verify_files(final_root, files)
        if failures:
            raise DeliverableArtifactError(
                "ARTIFACT_INTEGRITY_FAILED",
                "交付工件原子落盘后的完整性校验失败",
                details={"failures": failures},
            )
        return files, support_warnings
    except DeliverableArtifactError:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)
        if renamed and final_root.exists():
            shutil.rmtree(final_root, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - normalize generation failures
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)
        if renamed and final_root.exists():
            shutil.rmtree(final_root, ignore_errors=True)
        raise DeliverableArtifactError(
            "ARTIFACT_GENERATION_FAILED",
            "交付工件生成失败",
            details={"error": type(exc).__name__},
        ) from exc


def _safe_relative_name(filename: str) -> str:
    value = str(filename or "").strip()
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise DeliverableArtifactError(
            "INVALID_FILENAME_PATH", "下载文件名不合法",
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DeliverableArtifactError(
            "INVALID_FILENAME_PATH", "下载文件名不合法",
        )
    return path.as_posix()


def _verify_files(root: Path, files: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return [{"code": "ARTIFACT_DIRECTORY_MISSING", "path": str(root)}]
    seen: set[str] = set()
    for entry in files:
        try:
            name = _safe_relative_name(str(entry.get("name") or ""))
        except DeliverableArtifactError:
            failures.append({"code": "INVALID_FILENAME_PATH", "name": entry.get("name")})
            continue
        if name in seen:
            failures.append({"code": "DUPLICATE_FILE_ENTRY", "name": name})
            continue
        seen.add(name)
        path = root.joinpath(*PurePosixPath(name).parts)
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            failures.append({"code": "ARTIFACT_FILE_MISSING", "name": name})
            continue
        if (
            not _is_relative_to(resolved, resolved_root)
            or _path_has_symlink(path, root)
            or not resolved.is_file()
        ):
            failures.append({"code": "ARTIFACT_FILE_PATH_UNSAFE", "name": name})
            continue
        try:
            actual_hash, actual_size = _file_hash(resolved)
        except DeliverableArtifactError as exc:
            failures.append({"code": exc.code, "name": name})
            continue
        if actual_hash != entry.get("sha256"):
            failures.append({
                "code": "ARTIFACT_FILE_HASH_MISMATCH",
                "name": name,
                "expected": entry.get("sha256"),
                "actual": actual_hash,
            })
        if actual_size != entry.get("size_bytes"):
            failures.append({
                "code": "ARTIFACT_FILE_SIZE_MISMATCH",
                "name": name,
                "expected": entry.get("size_bytes"),
                "actual": actual_size,
            })
    try:
        actual_names = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        }
    except OSError:
        actual_names = set()
    unexpected = sorted(actual_names - seen)
    for name in unexpected:
        failures.append({"code": "UNINDEXED_ARTIFACT_FILE", "name": name})
    by_role = {str(entry.get("role") or ""): entry for entry in files}
    manifest_entry = by_role.get("manifest")
    index_entry = by_role.get("index")
    if not isinstance(manifest_entry, dict) or not isinstance(index_entry, dict):
        failures.append({"code": "ARTIFACT_INDEX_REQUIRED"})
        return failures
    try:
        manifest_path = root.joinpath(
            *PurePosixPath(str(manifest_entry["name"])).parts
        )
        index_path = root.joinpath(*PurePosixPath(str(index_entry["name"])).parts)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        failures.append({
            "code": "ARTIFACT_INDEX_UNREADABLE",
            "error": type(exc).__name__,
        })
        return failures
    expected_payload = [
        copy.deepcopy(entry)
        for entry in files
        if entry.get("role") not in {"manifest", "index"}
    ]
    expected_index = [
        copy.deepcopy(entry) for entry in files if entry.get("role") != "index"
    ]
    if not isinstance(manifest, dict) or _canonical_hash(
        manifest.get("payload_files") or []
    ) != _canonical_hash(expected_payload):
        failures.append({"code": "ARTIFACT_MANIFEST_FILE_INDEX_MISMATCH"})
    if not isinstance(index, dict) or _canonical_hash(
        index.get("files") or []
    ) != _canonical_hash(expected_index):
        failures.append({"code": "ARTIFACT_INDEX_FILE_LIST_MISMATCH"})
    if isinstance(manifest, dict) and isinstance(index, dict):
        for field in (
            "artifact_id", "workspace_id", "kind", "basis_fingerprint",
        ):
            if manifest.get(field) != index.get(field):
                failures.append({
                    "code": "ARTIFACT_MANIFEST_INDEX_METADATA_MISMATCH",
                    "field": field,
                })
    return failures
