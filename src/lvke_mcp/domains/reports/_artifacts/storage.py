"""工作区路径布局、state 读写与原子文件落盘。"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock

from lvke_mcp.domains.reports import doc_service
from lvke_mcp.runtime import workspace

from .base import (
    DeliverableArtifactError,
    SCHEMA_VERSION,
    _LOCK,
    _now,
    _validate_artifact_id,
    _validate_workspace_id,
)


def _workspace_root(workspace_id: str) -> Path:
    return doc_service._workspace_root(_validate_workspace_id(workspace_id))  # noqa: SLF001


def _require_workspace(workspace_id: str) -> Path:
    root = _workspace_root(workspace_id)
    if not (root / "workspace_meta.json").is_file():
        raise DeliverableArtifactError("WORKSPACE_NOT_FOUND", "工作区不存在")
    return root


def _service_root(workspace_id: str) -> Path:
    """报告交付工件（DOCX/manifest/basis 快照）的落盘根目录。

    与十三表导出一致，落到仓库 ``lvke产出/``（见
    :func:`lvke_mcp.runtime.workspace.deliverable_dir`）而不是 ``~/.lvke``：
    研报是需要随仓库留存、复核与签审的正式产出，``data_root`` 只放运行时状态。
    ``state.json`` 与 artifacts 目录同根，因此读写两侧自动一致。
    """

    return workspace.deliverable_dir(
        _validate_workspace_id(workspace_id),
        "report",
        "deliverable_artifacts",
    )


def _finance_artifact_root(workspace_id: str, run_id: str) -> Path:
    """财务可读工件目录，与 ``table_pack.default_artifact_dir`` 保持同一路径。

    单独包一层是为了让报告域只依赖一个入口：财务侧改路径时这里跟着变，
    不会出现研报抓不到 XLSX 附件却静默通过的情况。
    """

    from lvke_mcp.domains.finance import table_pack

    return table_pack.default_artifact_dir(
        _validate_workspace_id(workspace_id),
        str(run_id or "unknown"),
    )


def _artifacts_root(workspace_id: str) -> Path:
    return _service_root(workspace_id) / "artifacts"


def _artifact_root(
    workspace_id: str,
    artifact_id: str,
) -> Path:
    return _artifacts_root(workspace_id) / _validate_artifact_id(artifact_id)


def _state_path(workspace_id: str) -> Path:
    return _service_root(workspace_id) / "state.json"


def _empty_state(workspace_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "current": {"draft": "", "formal": ""},
        "created_at": "",
        "updated_at": "",
        "artifacts": {},
        "history": [],
    }


def _strict_read_json(path: Path, *, code: str, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeliverableArtifactError(code, f"{label}不存在") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeliverableArtifactError(
            code,
            f"{label}损坏或不可读",
            details={"error": type(exc).__name__},
        ) from exc


def _read_state(workspace_id: str) -> dict[str, Any]:
    path = _state_path(workspace_id)
    if not path.exists():
        return _empty_state(workspace_id)
    value = _strict_read_json(
        path, code="ARTIFACT_STATE_CORRUPT", label="交付工件状态",
    )
    if not isinstance(value, dict):
        raise DeliverableArtifactError(
            "ARTIFACT_STATE_CORRUPT", "交付工件状态必须是对象",
        )
    if value.get("schema_version") != SCHEMA_VERSION:
        raise DeliverableArtifactError(
            "ARTIFACT_SCHEMA_UNSUPPORTED",
            "交付工件状态 schema 版本不受支持",
            details={"schema_version": value.get("schema_version")},
        )
    if str(value.get("workspace_id") or "") != workspace_id:
        raise DeliverableArtifactError(
            "ARTIFACT_STATE_CORRUPT", "交付工件状态与工作区不匹配",
        )
    artifacts = value.get("artifacts")
    history = value.get("history")
    current = value.get("current")
    if not isinstance(artifacts, dict) or any(
        not isinstance(item, dict) for item in artifacts.values()
    ):
        raise DeliverableArtifactError(
            "ARTIFACT_STATE_CORRUPT", "交付工件索引格式错误",
        )
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        raise DeliverableArtifactError(
            "ARTIFACT_STATE_CORRUPT", "交付工件历史格式错误",
        )
    if not isinstance(current, dict):
        raise DeliverableArtifactError(
            "ARTIFACT_STATE_CORRUPT", "当前交付工件指针格式错误",
        )
    return value


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except (OSError, TypeError, ValueError) as exc:
        raise DeliverableArtifactError(
            "ARTIFACT_STATE_WRITE_FAILED",
            "交付工件状态保存失败",
            details={"path": str(path), "error": type(exc).__name__},
        ) from exc
    finally:
        temp.unlink(missing_ok=True)


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise DeliverableArtifactError(
            "ARTIFACT_GENERATION_FAILED",
            "交付工件文件写入失败",
            details={"filename": path.name, "error": type(exc).__name__},
        ) from exc


def _write_json_file(path: Path, value: Any) -> None:
    payload = json.dumps(
        value, ensure_ascii=False, indent=2, default=str,
    ).encode("utf-8") + b"\n"
    _write_bytes(path, payload)


def load(workspace_id: str, name: str, default: Any = None) -> Any:
    """读工作区根 ``{name}.json`` 键值工件；文件缺失返回 ``default``。"""
    path = _workspace_root(workspace_id) / f"{name}.json"
    if not path.is_file():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def save(workspace_id: str, name: str, obj: Any) -> None:
    """写工作区根 ``{name}.json`` 键值工件（写失败静默降级，不阻断调用链）。"""
    try:
        _write_json_atomic(_workspace_root(workspace_id) / f"{name}.json", obj)
    except DeliverableArtifactError:
        pass


def bind_finance_run(
    workspace_id: str,
    run_id: str,
    *,
    section: str = "",
    fin: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把正文/附表绑定到指定 finance_run_id（P1），写入 ``finance_binding.json``。"""
    if not run_id:
        return {}
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "finance_run_id": str(run_id),
        "section": section or "",
        "bound_at": _now(),
    }
    if isinstance(fin, Mapping):
        # Keep the legacy run fields while allowing a formal acquisition
        # artifact to bind the report to the exact immutable run/spec/fact
        # revision that produced it.  ``None`` is retained intentionally: it
        # makes an incomplete binding observable to the publish gate instead
        # of silently falling back to another revision.
        for key in (
            "input_hash",
            "spec_hash",
            "spec_id",
            "fact_revision",
            "spec_snapshot_hash",
            "evidence_binding_version",
            "evidence_binding_hash",
            "model_version",
            "validation_level",
            "template_version",
            "artifact_id",
            "artifact_job_id",
            "artifact_status",
            "report_data_hash",
            "binding_kind",
            "acquisition_tables_package_id",
            "acquisition_tables_basis_hash",
        ):
            if key in fin:
                payload[key] = fin.get(key)
    save(workspace_id, "finance_binding", payload)
    return payload


@contextmanager
def _state_guard(workspace_id: str):
    root = _service_root(workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    with _LOCK, FileLock(str(root / "state.lock"), timeout=30):
        yield
