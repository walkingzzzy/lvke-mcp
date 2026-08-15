"""工件读取与受控下载解析。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any



from .artifacts import (
    _check_artifact_consistency,
)

from .base import (
    _hash,
)

from .evidence import (
    _current_evidence_matches_run,
)

from .runs import (
    get_run,
)

from .specs import (
    get_spec,
)

from .store import (
    _artifacts_root,
    _load,
)

from .xlsx import (
    _file_hash,
)


def _artifact_filename_is_safe(filename: str) -> bool:
    value = str(filename or "")
    return bool(
        value
        and value not in {".", ".."}
        and "\x00" not in value
        and "/" not in value
        and "\\" not in value
        and Path(value).name == value
        and not Path(value).is_absolute()
    )


def _artifact_media_type(filename: str) -> str:
    stable_types = {
        ".md": "text/markdown; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    suffix = Path(str(filename or "")).suffix.lower()
    return stable_types.get(
        suffix,
        mimetypes.guess_type(str(filename or ""))[0]
        or "application/octet-stream",
    )


def get_artifact(
    workspace_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    """Return an acquisition artifact only after re-verifying its formal pack."""

    row = dict(
        _load(workspace_id)["artifacts"].get(artifact_id)
        or {}
    )
    if not row:
        return {}
    if row.get("status") != "succeeded":
        return {
            **row,
            "integrity_status": (
                "pending" if row.get("status") in {"queued", "running"} else "failed"
            ),
        }
    run = get_run(
        workspace_id,
        str(row.get("run_id") or ""),
    )

    artifacts_root = _artifacts_root(workspace_id).resolve()
    expected_directory = (artifacts_root / artifact_id).resolve()
    directory = Path(str(row.get("directory") or "")).resolve()
    try:
        expected_directory.relative_to(artifacts_root)
        directory.relative_to(artifacts_root)
    except ValueError:
        return {
            **row, "ok": False, "error": "ARTIFACT_MISMATCH",
            "integrity_status": "invalid_directory",
            "failures": [{"reason": "directory_outside_artifact_root"}],
        }
    if directory != expected_directory or not directory.is_dir():
        return {
            **row, "ok": False, "error": "ARTIFACT_MISMATCH",
            "integrity_status": "invalid_directory",
            "failures": [{"reason": "artifact_directory_binding_mismatch"}],
        }

    failures: list[dict[str, Any]] = []
    spec_row = get_spec(
        workspace_id,
        str(run.get("spec_id") or ""),
    )
    bound_spec = spec_row.get("spec") if isinstance(spec_row, dict) else None
    if not isinstance(bound_spec, dict) or _hash(bound_spec) != run.get("spec_hash"):
        failures.append({"reason": "run_spec_snapshot_mismatch"})
    else:
        evidence_ok, current_evidence = _current_evidence_matches_run(
            workspace_id,
            run,
            bound_spec,
        )
        if not evidence_ok:
            failures.append({
                "reason": "evidence_binding_stale",
                "expected": run.get("evidence_binding_hash"),
                "actual": current_evidence.get("binding_hash"),
                "status": current_evidence.get("status"),
            })
    file_rows: dict[str, dict[str, Any]] = {}
    for item in row.get("files") or []:
        if not isinstance(item, dict):
            failures.append({"reason": "invalid_file_manifest_entry"})
            continue
        name = str(item.get("name") or "")
        if not _artifact_filename_is_safe(name):
            failures.append({"name": name, "reason": "invalid_filename_path"})
            continue
        if name in file_rows:
            failures.append({"name": name, "reason": "duplicate_file_manifest_entry"})
            continue
        file_rows[name] = item
        path = directory / name
        try:
            path.resolve().relative_to(directory)
        except ValueError:
            failures.append({"name": name, "reason": "path_escape"})
            continue
        if path.is_symlink():
            failures.append({"name": name, "reason": "symlink_not_allowed"})
            continue
        if not path.is_file():
            failures.append({"name": name, "reason": "missing"})
            continue
        actual_size = path.stat().st_size
        if actual_size != item.get("size_bytes"):
            failures.append({
                "name": name, "reason": "size_mismatch",
                "expected": item.get("size_bytes"), "actual": actual_size,
            })
        actual_hash = _file_hash(path)
        if actual_hash != item.get("sha256"):
            failures.append({
                "name": name, "reason": "sha256_mismatch", "actual": actual_hash,
            })

    required_names = {
        "资产收购可行性研究报告.md",
        "资产收购可行性研究报告.docx",
        "资产收购财务模型.xlsx",
        "资产收购报告数据.json",
        "附件索引.json",
    }
    for missing_name in sorted(required_names - set(file_rows)):
        failures.append({"name": missing_name, "reason": "manifest_entry_missing"})

    index: dict[str, Any] = {}
    index_path = directory / "附件索引.json"
    if index_path.is_file():
        try:
            loaded_index = json.loads(index_path.read_text(encoding="utf-8"))
            index = loaded_index if isinstance(loaded_index, dict) else {}
        except (OSError, json.JSONDecodeError, TypeError):
            failures.append({"name": index_path.name, "reason": "invalid_json"})
    if not index:
        failures.append({"name": index_path.name, "reason": "index_missing_or_invalid"})
    else:
        for field, expected in (
            ("artifact_id", artifact_id),
            ("run_id", row.get("run_id")),
            ("spec_hash", row.get("spec_hash")),
            ("fact_revision", row.get("fact_revision")),
            ("spec_snapshot_hash", row.get("spec_snapshot_hash")),
            ("evidence_binding_version", row.get("evidence_binding_version")),
            ("evidence_binding_hash", row.get("evidence_binding_hash")),
            ("report_data_hash", row.get("report_data_hash")),
        ):
            if index.get(field) != expected:
                failures.append({
                    "name": index_path.name, "reason": f"index_{field}_mismatch",
                    "expected": expected, "actual": index.get(field),
                })
        indexed_files = {
            str(item.get("name") or ""): item
            for item in (index.get("files") or [])
            if isinstance(item, dict)
        }
        state_files = {
            name: item for name, item in file_rows.items()
            if name != index_path.name
        }
        if indexed_files != state_files:
            failures.append({"name": index_path.name, "reason": "file_manifest_mismatch"})
        index_consistency = index.get("numeric_consistency") or {}
        index_checks = index_consistency.get("checks") or [] if isinstance(index_consistency, dict) else []
        if (
            not isinstance(index_consistency, dict)
            or index_consistency.get("status") != "passed"
            or not index_checks
            or any(not check.get("passed") for check in index_checks if isinstance(check, dict))
            or any(not isinstance(check, dict) for check in index_checks)
        ):
            failures.append({"name": index_path.name, "reason": "numeric_consistency_incomplete"})
        if row.get("numeric_consistency") != "passed" or row.get("consistency_checks") != index_checks:
            failures.append({"name": index_path.name, "reason": "numeric_consistency_state_mismatch"})

    report_data: dict[str, Any] | None = None
    report_data_path = directory / "资产收购报告数据.json"
    if report_data_path.is_file():
        try:
            loaded = json.loads(report_data_path.read_text(encoding="utf-8"))
            report_data = loaded if isinstance(loaded, dict) else None
        except (OSError, json.JSONDecodeError, TypeError):
            report_data = None
    if not isinstance(report_data, dict):
        failures.append({"name": report_data_path.name, "reason": "invalid_json"})
    else:
        embedded_hash = report_data.get("report_data_hash")
        payload = {key: value for key, value in report_data.items() if key != "report_data_hash"}
        calculated_hash = _hash(payload)
        if embedded_hash != calculated_hash or embedded_hash != row.get("report_data_hash"):
            failures.append({
                "name": report_data_path.name, "reason": "report_data_hash_mismatch",
                "expected": row.get("report_data_hash"), "actual": calculated_hash,
            })
        bindings = report_data.get("bindings") or {}
        for field in (
            "run_id", "spec_hash", "input_hash", "model_version",
            "spec_snapshot_hash", "evidence_binding_version", "evidence_binding_hash",
        ):
            if bindings.get(field) != run.get(field):
                failures.append({
                    "name": report_data_path.name,
                    "reason": f"report_binding_{field}_mismatch",
                })
        if str(bindings.get("spec_id") or "") != str(row.get("fact_revision") or ""):
            failures.append({
                "name": report_data_path.name,
                "reason": "report_binding_fact_revision_mismatch",
            })
        if report_data.get("maximum_acceptable_price") != run.get("max_acquisition_price_analysis"):
            failures.append({
                "name": report_data_path.name,
                "reason": "report_maximum_price_mismatch",
            })

    # Re-run the numeric/binding verifier against the current immutable run;
    # trusting only the checks serialized at generation time would miss a
    # stale artifact after an out-of-band run mutation.
    if isinstance(report_data, dict):
        try:
            current_consistency = _check_artifact_consistency(
                run,
                (directory / "资产收购可行性研究报告.md").read_text(encoding="utf-8"),
                directory / "资产收购可行性研究报告.docx",
                directory / "资产收购财务模型.xlsx",
                report_data_path=report_data_path,
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({
                "reason": "numeric_consistency_unverifiable",
                "error_type": type(exc).__name__,
            })
        else:
            if current_consistency.get("status") != "passed":
                failures.append({
                    "reason": "numeric_consistency_failed",
                    "checks": [
                        check for check in (current_consistency.get("checks") or [])
                        if not check.get("passed")
                    ],
                })
            if current_consistency.get("checks") != row.get("consistency_checks"):
                failures.append({"reason": "numeric_consistency_snapshot_mismatch"})

    if failures:
        return {
            **row, "ok": False, "error": "ARTIFACT_MISMATCH",
            "integrity_status": "failed", "failures": failures,
        }
    return {
        **row, "ok": True, "integrity_status": "passed",
        "report_data": report_data,
    }


def list_artifacts(
    workspace_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return artifact jobs newest-first, including their integrity projection."""

    state = _load(workspace_id)
    ids = sorted(
        state["artifacts"],
        key=lambda artifact_id: (
            str(state["artifacts"][artifact_id].get("created_at") or ""),
            artifact_id,
        ),
        reverse=True,
    )
    rows = [
        get_artifact(workspace_id, artifact_id)
        for artifact_id in ids
    ]
    return [
        row for row in rows if row
    ][: max(1, min(int(limit or 50), 10_000))]


def _resolve_artifact_download(
    workspace_id: str,
    artifact_id: str,
    filename: str,
) -> dict[str, Any]:
    """Resolve one consistent artifact file after path and hash validation."""

    if not _artifact_filename_is_safe(filename):
        return {"ok": False, "error": "INVALID_FILENAME_PATH"}
    artifact = get_artifact(workspace_id, artifact_id)
    if not artifact:
        return {"ok": False, "error": "ARTIFACT_NOT_FOUND"}
    if artifact.get("status") != "succeeded":
        return {
            "ok": False, "error": "ARTIFACT_NOT_READY",
            "status": artifact.get("status"),
        }
    if artifact.get("integrity_status") != "passed" or not artifact.get("ok", True):
        return {
            "ok": False, "error": "ARTIFACT_MISMATCH",
            "failures": artifact.get("failures") or [],
        }
    run = get_run(
        workspace_id,
        str(artifact.get("run_id") or ""),
    )
    if run.get("status") != "succeeded" or not run.get("consistency_ok"):
        return {"ok": False, "error": "RUN_INCONSISTENT"}

    file_row = next(
        (
            item for item in (artifact.get("files") or [])
            if str(item.get("name") or "") == filename
        ),
        None,
    )
    if not file_row:
        return {"ok": False, "error": "ARTIFACT_FILE_NOT_FOUND"}
    directory = Path(str(artifact.get("directory") or "")).resolve()
    path = (directory / filename).resolve()
    try:
        path.relative_to(directory)
    except ValueError:
        return {"ok": False, "error": "INVALID_FILENAME_PATH"}
    if not path.is_file() or path.is_symlink():
        return {"ok": False, "error": "ARTIFACT_FILE_NOT_FOUND"}
    actual_hash = _file_hash(path)
    if actual_hash != file_row.get("sha256"):
        return {
            "ok": False, "error": "ARTIFACT_MISMATCH",
            "failures": [{
                "name": filename, "reason": "sha256_mismatch",
                "expected": file_row.get("sha256"), "actual": actual_hash,
            }],
        }
    return {
        "ok": True,
        "artifact_id": artifact_id,
        "run_id": artifact.get("run_id"),
        "filename": filename,
        "path": path,
        "size_bytes": path.stat().st_size,
        "sha256": actual_hash,
        "media_type": _artifact_media_type(filename),
        "integrity_status": artifact.get("integrity_status"),
    }


def resolve_artifact_download(
    workspace_id: str,
    artifact_id: str,
    filename: str,
) -> dict[str, Any]:
    """Resolve a complete acquisition artifact after integrity validation."""

    return _resolve_artifact_download(
        workspace_id,
        artifact_id,
        filename,
    )


def resolve_artifact_candidate_download(
    workspace_id: str,
    artifact_id: str,
    filename: str,
) -> dict[str, Any]:
    """Compatibility alias for integrity-validated artifact resolution."""

    return _resolve_artifact_download(
        workspace_id,
        artifact_id,
        filename,
    )


def _read_resolved_artifact(
    workspace_id: str,
    artifact_id: str,
    resolved: dict[str, Any],
) -> dict[str, Any]:
    if not resolved.get("ok"):
        return resolved
    path = resolved.get("path")
    try:
        content = path.read_bytes() if isinstance(path, Path) else b""
    except OSError:
        return {"ok": False, "error": "ARTIFACT_FILE_NOT_FOUND"}
    digest = hashlib.sha256(content).hexdigest()
    if digest != resolved.get("sha256") or len(content) != resolved.get("size_bytes"):
        get_artifact(workspace_id, artifact_id)
        return {
            "ok": False,
            "error": "ARTIFACT_MISMATCH",
            "failures": [{
                "name": resolved.get("filename"),
                "reason": "content_changed_after_validation",
            }],
        }
    return {**resolved, "content": content}


def read_artifact_download(
    workspace_id: str,
    artifact_id: str,
    filename: str,
) -> dict[str, Any]:
    return _read_resolved_artifact(
        workspace_id,
        artifact_id,
        resolve_artifact_download(
            workspace_id,
            artifact_id,
            filename,
        ),
    )


def read_artifact_candidate_download(
    workspace_id: str,
    artifact_id: str,
    filename: str,
) -> dict[str, Any]:
    return _read_resolved_artifact(
        workspace_id,
        artifact_id,
        resolve_artifact_candidate_download(
            workspace_id,
            artifact_id,
            filename,
        ),
    )
