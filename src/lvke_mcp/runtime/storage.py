"""Small immutable workspace object store shared by the formal MCP adapters.

This is deliberately not a new database or product subsystem.  It persists the
IDs and manifests that MCP tools exchange while leaving acquisition, analysis,
finance, research and report logic in their existing domain services.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from filelock import FileLock
from lvke_mcp.runtime.workspace import workspace_root

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def require_safe_id(value: str, field: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID.fullmatch(text):
        raise ValueError(f"invalid {field}")
    return text


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def paginate_resource_entries(
    entries: Iterable[dict[str, Any]],
    *,
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Page an immutable URI projection with a collection-consistency cursor."""

    ordered = sorted(entries, key=lambda entry: str(entry.get("uri") or ""))
    snapshot_hash = sha256_json([str(entry.get("uri") or "") for entry in ordered])
    last_uri = ""
    if cursor:
        try:
            padding = "=" * (-len(cursor) % 4)
            decoded = json.loads(
                base64.urlsafe_b64decode((cursor + padding).encode("ascii")).decode("utf-8")
            )
            if not isinstance(decoded, dict):
                raise ValueError("resource_cursor_invalid")
            if decoded.get("snapshot_hash") != snapshot_hash:
                raise ValueError("resource_list_changed")
            last_uri = str(decoded.get("last_uri") or "")
            if not last_uri:
                raise ValueError("resource_cursor_invalid")
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            code = str(exc) if str(exc) in {"resource_cursor_invalid", "resource_list_changed"} else "resource_cursor_invalid"
            raise ValueError(code) from None
    if last_uri:
        ordered = [entry for entry in ordered if str(entry.get("uri") or "") > last_uri]
    bounded_limit = max(1, min(int(limit), 200))
    page = ordered[:bounded_limit]
    has_more = len(ordered) > bounded_limit
    next_cursor = None
    if has_more and page:
        payload = canonical_json({
            "last_uri": str(page[-1].get("uri") or ""),
            "snapshot_hash": snapshot_hash,
        }).encode("utf-8")
        next_cursor = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return {
        "resources": page,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "snapshot_hash": snapshot_hash,
    }


class JSONArtifactStore:
    """Persist immutable JSON objects beneath a validated workspace directory."""

    def __init__(self, domain: str, kind: str, id_prefix: str, uri_segment: str) -> None:
        self.domain = require_safe_id(domain, "domain")
        self.kind = require_safe_id(kind, "kind")
        self.id_prefix = require_safe_id(id_prefix, "id_prefix")
        self.uri_segment = require_safe_id(uri_segment, "uri_segment")

    def _directory(self, workspace_id: str) -> Path:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        return workspace_root(workspace_id) / "mcp_objects" / self.domain / self.kind

    def uri(self, workspace_id: str, object_id: str) -> str:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        object_id = require_safe_id(object_id, "object_id")
        return (
            f"lvke://{self.domain}/workspaces/{workspace_id}/"
            f"{self.uri_segment}/{object_id}"
        )

    def preview_identity(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, str]:
        """Derive the identity a matching ``put`` will produce, without writing.

        Object IDs are content-addressed, so this is a pure function of the
        payload.  Handlers that must build and validate a complete response
        before any state changes can resolve identifiers up front and keep the
        writes as their last step.
        """

        object_id = f"{self.id_prefix}_{sha256_json(payload).removeprefix('sha256:')[:24]}"
        return {"object_id": object_id, "resource_uri": self.uri(workspace_id, object_id)}

    def put(
        self,
        workspace_id: str,
        payload: dict[str, Any],
        *,
        producer: str,
        status: str = "ok",
        source_ids: Iterable[str] = (),
        basis: Any | None = None,
        schema_version: str = "1.0",
        object_id: str | None = None,
    ) -> dict[str, Any]:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        content_hash = sha256_json(payload)
        stored_basis = payload if basis is None else basis
        basis_hash = sha256_json(stored_basis)
        object_id = require_safe_id(object_id, "object_id") if object_id else (
            f"{self.id_prefix}_{content_hash.removeprefix('sha256:')[:24]}"
        )
        record = {
            "object_id": object_id,
            "workspace_id": workspace_id,
            "schema_version": schema_version,
            "producer": producer,
            "created_at": utc_now(),
            "content_hash": content_hash,
            "basis_hash": basis_hash,
            # Retain the canonical basis so formal lineage can be independently
            # recomputed. Historical records without it intentionally fail the
            # signed-lineage validator instead of being silently backfilled.
            "basis": stored_basis,
            "status": status,
            "source_ids": sorted({str(item) for item in source_ids if str(item)}),
            "resource_uri": self.uri(workspace_id, object_id),
            "payload": payload,
        }
        directory = self._directory(workspace_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{object_id}.json"
        # Object IDs are content-addressed, so concurrent callers commonly
        # target the same file.  Serialize the existence check and replace to
        # keep immutable records stable across MCP processes.
        with FileLock(str(target) + ".lock", timeout=30):
            if target.exists():
                existing = self.get(workspace_id, object_id)
                if existing is not None:
                    return existing
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return record

    def get(self, workspace_id: str, object_id: str) -> dict[str, Any] | None:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        object_id = require_safe_id(object_id, "object_id")
        path = self._directory(workspace_id) / f"{object_id}.json"
        if not path.is_file():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(loaded, dict):
            return None
        if str(loaded.get("workspace_id") or "") != workspace_id:
            return None
        return loaded

    def list(self, workspace_id: str) -> list[dict[str, Any]]:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        directory = self._directory(workspace_id)
        if not directory.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(directory.glob(f"{self.id_prefix}_*.json")):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(loaded, dict)
                and str(loaded.get("workspace_id") or "") == workspace_id
            ):
                records.append(loaded)
        return records

    def resolve_uri(self, uri: str) -> dict[str, Any] | None:
        prefix = f"lvke://{self.domain}/workspaces/"
        if not uri.startswith(prefix):
            return None
        remainder = uri[len(prefix) :]
        parts = remainder.split("/")
        if len(parts) != 3 or parts[1] != self.uri_segment:
            return None
        try:
            return self.get(parts[0], parts[2])
        except ValueError:
            return None
