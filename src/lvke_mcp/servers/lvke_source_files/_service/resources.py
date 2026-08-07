"""MCP Resource listing and reads for files, analyses and parse jobs."""

from __future__ import annotations

import json
from typing import Any

from lvke_mcp.adapters import source_files_repository as source_api
from lvke_mcp.runtime.storage import (
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
)

from .constants import DOMAIN
from .envelope import _blocked, _envelope, _from_source_exception
from .imports import _public_record
from .paths import _analysis_uri, _file_uri, _job_uri


def list_resources(
    workspace_id: str,
    *,
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    state = source_api._load_state(workspace_id)  # noqa: SLF001
    entries: list[dict[str, Any]] = []
    visible_files: set[str] = set()
    for record in state["files"].values():
        file_id = str(record["file_id"])
        visible_files.add(file_id)
        public = _public_record(record)
        entries.append(
            {
                "uri": _file_uri(workspace_id, file_id),
                "name": file_id,
                "resource_type": "SourceFile",
                "mime_type": "application/json",
                "content_hash": sha256_json(public),
            }
        )
        analysis = source_api._load_analysis(workspace_id, file_id)  # noqa: SLF001
        if analysis:
            entries.append(
                {
                    "uri": _analysis_uri(workspace_id, file_id),
                    "name": f"{file_id}-analysis",
                    "resource_type": "SourceAnalysis",
                    "mime_type": "application/json",
                    "content_hash": sha256_json(analysis),
                }
            )
    for job in state["jobs"].values():
        if str(job.get("file_id") or "") not in visible_files:
            continue
        job_id = str(job["job_id"])
        public = source_api._public_parse_job(job)  # noqa: SLF001
        entries.append(
            {
                "uri": _job_uri(workspace_id, job_id),
                "name": job_id,
                "resource_type": "ParseJob",
                "mime_type": "application/json",
                "content_hash": sha256_json(public),
            }
        )
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _blocked(str(exc), "Resource 分页游标无效或列表已变化")
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[entry["uri"] for entry in page["resources"]],
        resources=page["resources"],
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
        snapshot_hash=page["snapshot_hash"],
    )


def read_resource(
    workspace_id: str,
    uri: str,
) -> dict[str, Any]:
    prefix = f"lvke://{DOMAIN}/workspaces/{workspace_id}/"
    if not str(uri).startswith(prefix):
        return _blocked("resource_not_found", "资源不存在或不属于当前工作区")
    parts = str(uri)[len(prefix) :].split("/")
    if len(parts) != 2:
        return _blocked("resource_not_found", "Resource URI 无效")
    segment, object_id = parts
    try:
        require_safe_id(object_id, "object_id")
        state = source_api._load_state(workspace_id)  # noqa: SLF001
        if segment in {"files", "analyses"}:
            record = source_api._require_source_record_from_state(  # noqa: SLF001
                state, workspace_id, object_id, "mcp"
            )
            content_value = (
                _public_record(record)
                if segment == "files"
                else source_api._load_analysis(workspace_id, object_id)  # noqa: SLF001
            )
            if segment == "analyses" and not content_value:
                return _blocked("resource_not_found", "解析 Resource 尚不存在")
        elif segment == "parse-jobs":
            job, _record = source_api._require_parse_job_from_state(  # noqa: SLF001
                state, workspace_id, object_id, "mcp"
            )
            content_value = source_api._public_parse_job(job)  # noqa: SLF001
        else:
            return _blocked("resource_not_found", "未知 Resource 类型")
    except (source_api.SourceFileError, ValueError) as exc:
        if isinstance(exc, source_api.SourceFileError):
            return _from_source_exception(exc)
        return _blocked("resource_not_found", "Resource URI 无效")
    content = json.dumps(content_value, ensure_ascii=False, indent=2)
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[uri],
        uri=uri,
        mime_type="application/json",
        content=content,
        content_hash=sha256_json(content_value),
    )
