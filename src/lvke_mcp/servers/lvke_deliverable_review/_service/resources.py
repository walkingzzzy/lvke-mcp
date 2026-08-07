"""Resource 列举、解析与读取。"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from lvke_mcp.runtime.storage import paginate_resource_entries, require_safe_id
from lvke_mcp.servers.lvke_deliverable_review import rules
from lvke_mcp.servers.lvke_deliverable_review.store import STORE

from .base import (
    EXPORT_STORE,
    PREPARATION_STORE,
    REPO_ROOT,
    STANDARD_APPLICABILITY_STORE,
    STANDARD_EVIDENCE_STORE,
    _blocked,
    _finding_uri,
    _message,
    _metrics_uri,
    _ok,
    _review_uri,
)

from .events import (
    _project,
    _project_events,
)

from .export import (
    _export_resource_uri,
    _export_root,
)

from .metrics import (
    _workspace_metrics_payload,
)

from .preparation import (
    _verified_preparation_record,
)


def _resource_entry(uri: str, resource_type: str, name: str, description: str) -> dict[str, Any]:
    return {
        "uri": uri, "resource_type": resource_type, "name": name,
        "description": description, "mime_type": "application/json",
    }


def list_resources(args: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(args, str):
        args = {"workspace_id": args}
    workspace_id = str(args.get("workspace_id") or "")
    try:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        entries: dict[str, dict[str, Any]] = {}
        for candidate in PREPARATION_STORE.list(workspace_id):
            record, _integrity_reasons = _verified_preparation_record(
                workspace_id,
                str(candidate.get("object_id") or ""),
            )
            if record is None:
                continue
            uri = str(record.get("resource_uri") or "")
            entries[uri] = _resource_entry(uri, "preparation", str(record.get("object_id") or ""), "不可变审查准备对象")
        for review_id in STORE.review_ids(workspace_id):
            try:
                state = _project_events(workspace_id, review_id)
            except ValueError:
                continue
            uri = _review_uri(workspace_id, review_id)
            entries[uri] = _resource_entry(uri, "review", review_id, "append-only 审查运行投影")
            for finding in state.get("findings") or []:
                finding_id = str(finding.get("finding_id") or "")
                finding_uri = _finding_uri(workspace_id, review_id, finding_id)
                entries[finding_uri] = _resource_entry(finding_uri, "finding", finding_id, "审查 finding 及完整历史")
        for record in EXPORT_STORE.list(workspace_id):
            payload = record.get("payload") or {}
            review_id = str(payload.get("review_id") or "")
            export_id = str(payload.get("export_id") or "")
            uri = _export_resource_uri(workspace_id, export_id)
            entries[uri] = _resource_entry(uri, "export", export_id, "不可变审查导出清单")
            for file_row in payload.get("files") or []:
                file_uri = str(file_row.get("uri") or "")
                entries[file_uri] = {
                    **_resource_entry(file_uri, "export_file", str(file_row.get("filename") or ""), "审查报告导出文件"),
                    "mime_type": file_row.get("media_type") or "application/octet-stream",
                }
        for record in STANDARD_APPLICABILITY_STORE.list(workspace_id):
            uri = str(record.get("resource_uri") or "")
            entries[uri] = _resource_entry(
                uri, "standard_applicability", str(record.get("object_id") or ""),
                "标准适用性解析及排除原因",
            )
        for record in STANDARD_EVIDENCE_STORE.list(workspace_id):
            uri = str(record.get("resource_uri") or "")
            entries[uri] = _resource_entry(
                uri, "standard_evidence", str(record.get("object_id") or ""),
                "标准需求绑定的不可变证据索引",
            )
        from lvke_mcp.servers.lvke_deliverable_review import rubrics

        for store, resource_type, description in (
            (rubrics.ASSESSMENT_STORE, "rubric_assessment", "不可变章节 rubric 评分"),
            (rubrics.COMPARISON_STORE, "rubric_comparison", "修订前后 rubric 对比"),
        ):
            for record in store.list(workspace_id):
                uri = str(record.get("resource_uri") or "")
                entries[uri] = _resource_entry(
                    uri,
                    resource_type,
                    str(record.get("object_id") or ""),
                    description,
                )
        for pack in rules.registry():
            pack_id = str(pack.get("rule_pack_id") or "")
            uri = f"lvke://deliverable-review/workspaces/{workspace_id}/rule-packs/{quote(pack_id)}"
            entries[uri] = _resource_entry(uri, "rule_pack", pack_id, "版本化规则包")
        standards_uri = f"lvke://deliverable-review/workspaces/{workspace_id}/standards/current"
        entries[standards_uri] = _resource_entry(standards_uri, "standards", "current", "当前标准来源锁定清单")
        metrics_uri = _metrics_uri(workspace_id)
        entries[metrics_uri] = _resource_entry(
            metrics_uri,
            "metrics",
            "current",
            "工作区统一审查上线指标与影子门禁差异",
        )
        resource_type = str(args.get("resource_type") or "")
        page = paginate_resource_entries(
            (row for row in entries.values() if not resource_type or row["resource_type"] == resource_type),
            cursor=str(args.get("cursor") or ""), limit=int(args.get("limit") or 50),
        )
    except ValueError as exc:
        return _blocked(str(exc), _message(str(exc)))
    return _ok(
        resources=page["resources"], next_cursor=page["next_cursor"], has_more=page["has_more"],
        snapshot_hash=page["snapshot_hash"], resource_uris=[row["uri"] for row in page["resources"]],
        blockers=[], next_actions=[],
    )


def resolve_resource(
    uri: str,
    workspace_id: str | None = None,
) -> tuple[str | bytes, str] | None:
    prefix = "lvke://deliverable-review/workspaces/"
    if not str(uri).startswith(prefix):
        return None
    parts = str(uri)[len(prefix):].split("/")
    if len(parts) < 3:
        return None
    uri_workspace = parts[0]
    if workspace_id is not None and uri_workspace != workspace_id:
        return None
    try:
        require_safe_id(uri_workspace, "workspace_id")
    except ValueError:
        return None
    segment = parts[1]
    if segment == "preparations" and len(parts) == 3:
        record, _integrity_reasons = _verified_preparation_record(
            uri_workspace,
            parts[2],
        )
        if not record:
            return None
        return (json.dumps(record, ensure_ascii=False, indent=2, default=str), "application/json") if record else None
    if segment == "reviews" and len(parts) in {3, 5}:
        try:
            state = _project(uri_workspace, parts[2])
        except ValueError:
            return None
        if len(parts) == 3:
            return json.dumps(state, ensure_ascii=False, indent=2, default=str), "application/json"
        if parts[3] != "findings":
            return None
        finding = next((row for row in state.get("findings") or [] if row.get("finding_id") == parts[4]), None)
        return (json.dumps(finding, ensure_ascii=False, indent=2, default=str), "application/json") if finding else None
    if segment == "exports" and len(parts) == 3:
        requested_export_id = parts[2]
        record = next((
            row for row in EXPORT_STORE.list(uri_workspace)
            if str((row.get("payload") or {}).get("export_id") or "")
            == requested_export_id
        ), None)
        if record is None:
            # Read-only compatibility for historical content-addressed record URIs.
            record = EXPORT_STORE.get(uri_workspace, requested_export_id)
        if not record:
            return None
        if record:
            projected = {
                **record,
                "canonical_resource_uri": _export_resource_uri(
                    uri_workspace,
                    str((record.get("payload") or {}).get("export_id") or requested_export_id),
                ),
            }
            return json.dumps(projected, ensure_ascii=False, indent=2, default=str), "application/json"
        return None
    if segment == "exports" and len(parts) == 5 and parts[3] == "files":
        export_id = parts[2]
        filename = unquote(parts[4])
        if Path(filename).name != filename:
            return None
        record = next((
            row for row in EXPORT_STORE.list(uri_workspace)
            if str((row.get("payload") or {}).get("export_id") or "") == export_id
        ), None)
        if not record:
            return None
        file_row = next((
            row for row in ((record or {}).get("payload") or {}).get("files") or []
            if row.get("filename") == filename
        ), None)
        path = _export_root(uri_workspace, export_id) / filename
        if file_row is None or not path.is_file():
            return None
        content = path.read_bytes()
        if "sha256:" + hashlib.sha256(content).hexdigest() != file_row.get("sha256"):
            return None
        return content, str(file_row.get("media_type") or "application/octet-stream")
    if segment == "standard-applicabilities" and len(parts) == 3:
        record = STANDARD_APPLICABILITY_STORE.resolve_uri(uri)
        if record is None or str(record.get("workspace_id") or "") != uri_workspace:
            return None
        return json.dumps(record, ensure_ascii=False, indent=2, default=str), "application/json"
    if segment == "standard-evidence" and len(parts) == 3:
        record = STANDARD_EVIDENCE_STORE.resolve_uri(uri)
        if record is None or str(record.get("workspace_id") or "") != uri_workspace:
            return None
        return json.dumps(record, ensure_ascii=False, indent=2, default=str), "application/json"
    if segment in {"rubric-assessments", "rubric-comparisons"} and len(parts) == 3:
        from lvke_mcp.servers.lvke_deliverable_review import rubrics

        resolved = rubrics.resolve_rubric_resource(uri)
        if resolved is None:
            return None
        record, _object_type = resolved
        if str(record.get("workspace_id") or "") != uri_workspace:
            return None
        return json.dumps(record, ensure_ascii=False, indent=2, default=str), "application/json"
    if segment == "rule-packs" and len(parts) == 3:
        pack_id = unquote(parts[2])
        record = next((row for row in rules.registry() if row.get("rule_pack_id") == pack_id), None)
        return (json.dumps(record, ensure_ascii=False, indent=2), "application/json") if record else None
    if segment == "standards" and parts[2] == "current" and len(parts) == 3:
        path = REPO_ROOT / "config" / "review_standards.lock.json"
        return (path.read_text(encoding="utf-8"), "application/json") if path.is_file() else None
    if segment == "metrics" and parts[2] == "current" and len(parts) == 3:
        metrics = _workspace_metrics_payload(uri_workspace)
        return json.dumps(metrics, ensure_ascii=False, indent=2, default=str), "application/json"
    return None


def read_resource(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args.get("workspace_id") or "")
    uri = str(args.get("uri") or "")
    try:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
    except ValueError as exc:
        return _blocked(str(exc), _message(str(exc)))
    resolved = resolve_resource(uri, workspace_id)
    if resolved is None:
        return _blocked("resource_not_found", "资源不存在或不属于当前工作区")
    content, mime_type = resolved
    encoded = isinstance(content, bytes)
    return _ok(
        uri=uri, mime_type=mime_type, content_encoding="base64" if encoded else "utf-8",
        content=base64.b64encode(content).decode("ascii") if encoded else content,
        resource_uris=[uri], blockers=[], next_actions=[],
    )
