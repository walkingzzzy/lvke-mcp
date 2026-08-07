"""findings 分页查询与单条读取。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lvke_mcp.runtime.storage import canonical_json, paginate_resource_entries, require_safe_id
from lvke_mcp.servers.lvke_deliverable_review.contracts import FINDING_STATUSES, SEVERITIES

from .base import (
    _blocked,
    _finding_uri,
    _message,
    _ok,
)

from .events import (
    _project,
)


def list_findings(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args.get("workspace_id") or "")
    review_id = str(args.get("review_id") or "")
    try:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        state = _project(workspace_id, review_id)
        severity = str(args.get("severity") or "")
        status = str(args.get("status") or "")
        category = str(args.get("category") or "")
        role = str(args.get("review_area") or "")
        location_query = str(args.get("location") or "").lower()
        if severity and severity not in SEVERITIES:
            raise ValueError("severity_invalid")
        if status and status not in FINDING_STATUSES:
            raise ValueError("finding_status_invalid")
        rows = []
        for finding in state.get("findings") or []:
            if severity and finding.get("severity") != severity:
                continue
            if status and finding.get("status") != status:
                continue
            if category and finding.get("category") != category:
                continue
            if role and finding.get("review_area") != role:
                continue
            if location_query and location_query not in canonical_json(finding.get("target_location") or {}).lower():
                continue
            rows.append({**finding, "uri": _finding_uri(workspace_id, review_id, str(finding["finding_id"]))})
        page = paginate_resource_entries(
            rows, cursor=str(args.get("cursor") or ""), limit=int(args.get("limit") or 50),
        )
    except ValueError as exc:
        code = str(exc)
        if code == "invalid review_id":
            code = "review_not_found"
        return _blocked(code, _message(code))
    findings = [{key: value for key, value in row.items() if key != "uri"} for row in page["resources"]]
    return _ok(
        review_id=review_id, findings=findings, total_matching=len(rows),
        next_cursor=page["next_cursor"], has_more=page["has_more"], snapshot_hash=page["snapshot_hash"],
        resource_uris=[row["uri"] for row in page["resources"]], blockers=[], next_actions=[],
    )


def get_finding(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args.get("workspace_id") or "")
    review_id = str(args.get("review_id") or "")
    finding_id = str(args.get("finding_id") or "")
    try:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        state = _project(workspace_id, review_id)
    except ValueError:
        return _blocked("review_not_found", _message("review_not_found"))
    row = next((item for item in state.get("findings") or [] if item.get("finding_id") == finding_id), None)
    if row is None:
        return _blocked("finding_not_found", _message("finding_not_found"))
    return _ok(
        review_id=review_id, finding=deepcopy(row),
        resource_uris=[_finding_uri(workspace_id, review_id, finding_id)], blockers=[], next_actions=[],
    )
