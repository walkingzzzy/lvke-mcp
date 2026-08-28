"""Direct-content and local-path source import, staging and commit."""

from __future__ import annotations

import base64
import binascii
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from lvke_mcp.adapters import source_files_repository as source_api
from lvke_mcp.servers.lvke_source_files.external_corpora import (
    ExternalCorpusError,
    configured_import_root_diagnostics,
    configured_import_roots,
    resolve_project_corpora,
)

from .constants import DIRECT_CONTENT_LIMIT
from .envelope import _blocked, _envelope, _from_source_exception
from .paths import _analysis_uri, _file_uri, _job_uri, _source_root


def _decode_content(content_base64: str, *, limit: int) -> tuple[bytes | None, dict[str, Any] | None]:
    try:
        raw = base64.b64decode(content_base64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return None, _blocked("content_encoding_invalid", "content_base64 不是合法 Base64")
    if not raw:
        return None, _blocked("source_empty", "空文件不能作为原始证据")
    if len(raw) > limit:
        return None, _blocked(
            "source_content_too_large",
            "单次内容超过允许上限",
            field_errors={"/content_base64": {"max_decoded_bytes": limit}},
        )
    return raw, None


def _stage_bytes(workspace_id: str, raw: bytes) -> Path:
    staging = _source_root(workspace_id) / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / f"mcp-{uuid.uuid4().hex}.part"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    visible = dict(record)
    for key in (
        "request_id",
        "revision_observed_by",
        "upload_identity_hash",
        "worker_token",
        "_worker_control",
        # 服务器绝对路径属于内部布局，工具说明已承诺不外泄；
        # 调用方要定位内容一律走 resource_uri，不需要文件系统路径。
        "path",
    ):
        visible.pop(key, None)
    return visible


def _commit_and_parse(
    workspace_id: str,
    staged_path: Path,
    *,
    original_filename: str,
    declared_mime: str,
    idempotency_key: str,
    expected_sha256: str = "",
    expected_size: int | None = None,
    parse_immediately: bool = True,
    evidence_policy: str = "",
    evidence_origin: str = "",
    project_fact_certified: bool = False,
) -> dict[str, Any]:
    try:
        record = source_api.commit_staged_source_file(
            workspace_id,
            staged_path,
            original_filename,
            declared_mime,
            idempotency_key=idempotency_key,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            evidence_policy=evidence_policy,
            evidence_origin=evidence_origin,
            project_fact_certified=project_fact_certified,
        )
    except source_api.SourceFileError as exc:
        return _from_source_exception(exc)
    file_id = str(record.get("file_id") or "")
    job_id = str(record.get("parse_job_id") or "")
    state = source_api._load_state(workspace_id)  # noqa: SLF001
    current_job = state["jobs"].get(job_id, {})
    if parse_immediately and current_job.get("status") == "queued" and job_id:
        source_api.parse_source_file(workspace_id, job_id)
        state = source_api._load_state(workspace_id)  # noqa: SLF001
    stored = source_api._require_source_record_from_state(state, workspace_id, file_id)
    job = source_api._public_parse_job(state["jobs"].get(job_id, {}))  # noqa: SLF001
    parse_status = str(job.get("status") or "queued")
    status = "partial" if parse_status in {"partial", "failed"} else "ok"
    success = status == "ok"
    warnings = []
    if parse_status == "partial":
        warnings.append("原始资料已固化，但解析结果为 partial，不具备自动正式证据资格")
    elif parse_status == "failed":
        warnings.append("原始资料已固化，但解析失败，可调用 source_parse_retry")
    resource_uris = [_file_uri(workspace_id, file_id), _job_uri(workspace_id, job_id)]
    if parse_status in {"succeeded", "partial"}:
        resource_uris.append(_analysis_uri(workspace_id, file_id))
    return _envelope(
        success=success,
        status=status,
        code="source_parse_incomplete" if not success else "",
        resource_uris=resource_uris,
        warnings=warnings,
        next_actions=[
            "读取 source_file_get 检查安全扫描、解析状态与 formal_use 决策",
            "对候选 evidence locator 完成人工复核后再绑定正式财务输入",
        ],
        file_id=file_id,
        parse_job_id=job_id,
        source_file=_public_record(stored),
        parse_job=job,
        idempotent_replay=bool(record.get("idempotent_replay")),
        evidence_eligibility=str(stored.get("evidence_policy") or "candidate"),
        formal_evidence_candidate=bool(
            stored.get("project_fact_certified")
            and str(stored.get("evidence_policy") or "") in {"formal_evidence", "sim_a_formal"}
        ),
        evidence_policy=str(stored.get("evidence_policy") or "candidate"),
        evidence_origin=str(stored.get("evidence_origin") or ""),
        project_fact_certified=bool(stored.get("project_fact_certified")),
        lineage={
            "source_sha256": stored.get("sha256"),
            "source_version": stored.get("version"),
            "parse_job_id": job_id,
            "evidence_policy": stored.get("evidence_policy") or "candidate",
            "evidence_origin": stored.get("evidence_origin") or "",
        },
    )


def import_content(
    workspace_id: str,
    *,
    original_filename: str,
    declared_mime: str,
    content_base64: str,
    idempotency_key: str,
    expected_sha256: str = "",
    parse_immediately: bool = True,
    evidence_policy: str = "",
    evidence_origin: str = "",
    project_fact_certified: bool = False,
) -> dict[str, Any]:
    raw, error = _decode_content(content_base64, limit=DIRECT_CONTENT_LIMIT)
    if error:
        return error
    assert raw is not None
    staged = _stage_bytes(workspace_id, raw)
    return _commit_and_parse(
        workspace_id,
        staged,
        original_filename=original_filename,
        declared_mime=declared_mime,
        idempotency_key=idempotency_key,
        expected_sha256=expected_sha256,
        expected_size=len(raw),
        parse_immediately=parse_immediately,
        evidence_policy=evidence_policy,
        evidence_origin=evidence_origin,
        project_fact_certified=project_fact_certified,
    )


def _configured_import_roots() -> list[Path]:
    return list(configured_import_roots())


def resolve_external_corpus(project_name: str) -> dict[str, Any]:
    """Resolve one registered project without importing or mutating source data."""

    _NEXT_ACTIONS: dict[str, list[str]] = {
        "root_not_configured": [
            "配置环境变量 LVKE_EXTERNAL_CORPUS_ROOT 指向外部语料根目录",
        ],
        "root_not_found": [
            "检查 LVKE_EXTERNAL_CORPUS_ROOT 路径是否存在并已挂载",
        ],
        "manifest_invalid": [
            "检查 config/external_corpora.v1.json 文件完整性，确认 schema_version 正确",
        ],
        "corpus_missing": [
            "检查 LVKE_EXTERNAL_CORPUS_ROOT 下的语料目录和 marker 文件是否存在",
        ],
        "project_not_registered": [
            "如需走语料登记路径：将项目名称和别名加入 config/external_corpora.v1.json",
            "如需直接导入本地文件（推荐）：改用 source_import_local_path，"
            "只需设置 LVKE_SOURCE_IMPORT_ROOTS 指向资料目录，无需登记项目名称",
        ],
        "project_ambiguous": [
            "项目名称匹配了多条路由，请在 config/external_corpora.v1.json 中消除别名冲突",
        ],
    }

    try:
        resolved = resolve_project_corpora(project_name)
    except ExternalCorpusError as exc:
        reason = getattr(exc, "reason", "") or "unknown"
        result = _blocked(
            "external_corpus_unavailable",
            str(exc),
            next_actions=_NEXT_ACTIONS.get(reason, [
                "配置 LVKE_EXTERNAL_CORPUS_ROOT 并核对 config/external_corpora.v1.json",
            ]),
        )
        result["detail"] = reason
        return result
    return _envelope(
        success=True,
        status="ok",
        evidence_eligibility="none",
        **resolved,
    )


def _resolve_local_source(local_path: str) -> tuple[Path | None, dict[str, Any] | None]:
    if str(os.getenv("LVKE_MCP_TRANSPORT") or "stdio").lower() not in {"stdio", "local"}:
        return None, _blocked(
            "local_path_transport_forbidden",
            "本地路径导入仅允许 stdio/local MCP transport",
        )
    raw_path = Path(str(local_path or ""))
    if not raw_path.is_absolute():
        return None, _blocked("local_path_invalid", "local_path 必须是绝对路径")
    try:
        roots = _configured_import_roots()
    except ExternalCorpusError as exc:
        reason = getattr(exc, "reason", "") or "unknown"
        result = _blocked(
            "external_corpus_unavailable",
            str(exc),
            next_actions=[
                "配置 LVKE_SOURCE_IMPORT_ROOTS，或配置 LVKE_EXTERNAL_CORPUS_ROOT 并修复资料 marker",
            ] if reason == "import_roots_invalid" else [
                "配置 LVKE_SOURCE_IMPORT_ROOTS 或 LVKE_EXTERNAL_CORPUS_ROOT",
            ],
        )
        result["detail"] = reason
        return None, result
    if not roots:
        return None, _blocked(
            "local_import_roots_unconfigured",
            "未配置可用的本地资料导入根，拒绝本地路径导入",
        )
    try:
        path_stat = raw_path.lstat()
        resolved = raw_path.resolve(strict=True)
    except OSError:
        return None, _blocked("local_source_not_found", "本地源文件不存在")
    if raw_path.is_symlink() or not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
        return None, _blocked(
            "local_source_unsafe",
            "本地源必须是允许目录内的单链接普通文件",
        )
    if not any(resolved == root or root in resolved.parents for root in roots):
        return None, _blocked("local_source_outside_roots", "本地源文件不在允许导入目录内")
    return resolved, None


def import_local_path(
    workspace_id: str,
    *,
    local_path: str,
    original_filename: str,
    declared_mime: str,
    idempotency_key: str,
    expected_sha256: str = "",
    parse_immediately: bool = True,
) -> dict[str, Any]:
    source, error = _resolve_local_source(local_path)
    if error:
        return error
    assert source is not None
    staging = _source_root(workspace_id) / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"mcp-local-{uuid.uuid4().hex}.part"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        with os.fdopen(descriptor, "rb") as source_stream, staged.open("xb") as target:
            current = os.fstat(source_stream.fileno())
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                return _blocked("local_source_unsafe", "本地源文件状态已变化，拒绝导入")
            shutil.copyfileobj(source_stream, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        result = _commit_and_parse(
            workspace_id,
            staged,
            original_filename=original_filename,
            declared_mime=declared_mime,
            idempotency_key=idempotency_key,
            expected_sha256=expected_sha256,
            expected_size=source.stat().st_size,
            parse_immediately=parse_immediately,
        )
        diagnostics = configured_import_root_diagnostics()
        invalid_roots = list(diagnostics.get("invalid_roots") or [])
        if invalid_roots:
            result["import_root_diagnostics"] = {
                "source": diagnostics.get("source"),
                "ignored_invalid_roots": invalid_roots,
            }
            result.setdefault("warnings", []).append(
                f"已忽略 {len(invalid_roots)} 个无效本地导入根"
            )
        return result
    finally:
        staged.unlink(missing_ok=True)
