"""Paginated source-file listing and single-record reads."""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters import source_files_repository as source_api
from lvke_mcp.runtime.storage import paginate_resource_entries

from .envelope import _blocked, _envelope, _from_source_exception
from .imports import _public_record
from .paths import _analysis_uri, _file_uri


def list_source_files(
    workspace_id: str,
    *,
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    state = source_api._load_state(workspace_id)  # noqa: SLF001
    entries = [
        {
            **_public_record(record),
            "uri": _file_uri(workspace_id, str(record["file_id"])),
        }
        for record in state["files"].values()
    ]
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _blocked(str(exc), "原始资料分页游标无效或列表已变化")
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[entry["uri"] for entry in page["resources"]],
        source_files=page["resources"],
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
        snapshot_hash=page["snapshot_hash"],
    )


def get_source_file(
    workspace_id: str,
    file_id: str,
) -> dict[str, Any]:
    try:
        _state, record = source_api._require_source_record(  # noqa: SLF001
            workspace_id, file_id, "mcp"
        )
    except source_api.SourceFileError as exc:
        return _from_source_exception(exc)
    analysis = source_api._load_analysis(workspace_id, file_id)  # noqa: SLF001
    uris = [_file_uri(workspace_id, file_id)]
    if analysis:
        uris.append(_analysis_uri(workspace_id, file_id))
    return _envelope(
        success=True,
        status="ok",
        resource_uris=uris,
        file_id=file_id,
        source_file=_public_record(record),
        analysis=analysis or None,
    )
