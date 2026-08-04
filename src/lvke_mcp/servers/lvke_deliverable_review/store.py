"""Append-only event store for deliverable reviews."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from filelock import FileLock

from lvke_mcp.runtime.storage import require_safe_id, sha256_json, utc_now
from lvke_mcp.runtime.workspace import workspace_root


class ReviewEventStore:
    def _root(self, workspace_id: str) -> Path:
        return workspace_root(require_safe_id(workspace_id, "workspace_id")) / "mcp_objects" / "deliverable-review"

    def _events(self, workspace_id: str) -> Path:
        return self._root(workspace_id) / "events"

    def _idempotency(self, workspace_id: str) -> Path:
        return self._root(workspace_id) / "idempotency"

    @contextmanager
    def mutation_guard(self, workspace_id: str, operation: str, key: str) -> Iterator[None]:
        """Serialize the complete check-mutate-remember idempotency window."""

        digest = sha256_json({"operation": operation, "key": key}).removeprefix("sha256:")
        path = self._idempotency(workspace_id) / f"{digest}.operation.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(path), timeout=30):
            yield

    @staticmethod
    def _write_once(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def idempotent(self, workspace_id: str, operation: str, key: str, request: Any) -> dict[str, Any] | None:
        digest = sha256_json({"operation": operation, "key": key}).removeprefix("sha256:")
        path = self._idempotency(workspace_id) / f"{digest}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("request_hash") != sha256_json(request):
            raise ValueError("idempotency_key_conflict")
        return data.get("response") if isinstance(data.get("response"), dict) else None

    def remember(self, workspace_id: str, operation: str, key: str, request: Any, response: dict[str, Any]) -> None:
        digest = sha256_json({"operation": operation, "key": key}).removeprefix("sha256:")
        path = self._idempotency(workspace_id) / f"{digest}.json"
        with FileLock(str(path) + ".lock", timeout=30):
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing.get("request_hash") != sha256_json(request):
                    raise ValueError("idempotency_key_conflict")
                return
            self._write_once(path, {
                "operation": operation, "key_hash": "sha256:" + digest,
                "request_hash": sha256_json(request), "response": response, "created_at": utc_now(),
            })

    def append(self, workspace_id: str, review_id: str, event_type: str, payload: dict[str, Any], event_source: str = "") -> dict[str, Any]:
        review_id = require_safe_id(review_id, "review_id")
        directory = self._events(workspace_id) / review_id
        directory.mkdir(parents=True, exist_ok=True)
        with FileLock(str(directory / "append.lock"), timeout=30):
            existing_paths = sorted(directory.glob("[0-9]*.json"))
            sequence = len(existing_paths) + 1
            previous_event_hash = ""
            if existing_paths:
                try:
                    previous = json.loads(existing_paths[-1].read_text(encoding="utf-8"))
                    previous_event_hash = str(previous.get("event_hash") or "")
                except (OSError, json.JSONDecodeError):
                    # A damaged predecessor must not be silently skipped.  The
                    # projection will fail closed when it verifies the chain.
                    previous_event_hash = "invalid"
            body = {
                "schema_version": "deliverable_review_event.v1", "review_id": review_id,
                "sequence": sequence, "event_type": event_type,
                "created_at": utc_now(), "previous_event_hash": previous_event_hash,
                "payload": payload,
            }
            body["event_hash"] = sha256_json(body)
            self._write_once(directory / f"{sequence:08d}.json", body)
            return body

    def events(self, workspace_id: str, review_id: str) -> list[dict[str, Any]]:
        directory = self._events(workspace_id) / require_safe_id(review_id, "review_id")
        rows: list[dict[str, Any]] = []
        if directory.is_dir():
            for path in sorted(directory.glob("[0-9]*.json")):
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows

    def review_ids(self, workspace_id: str) -> list[str]:
        root = self._events(workspace_id)
        return sorted(path.name for path in root.iterdir() if path.is_dir()) if root.is_dir() else []

    def event_chain_hash(self, workspace_id: str, review_id: str) -> str:
        return sha256_json([row.get("event_hash") for row in self.events(workspace_id, review_id)])

    def verify_event_chain(self, workspace_id: str, review_id: str) -> tuple[bool, list[str]]:
        """Verify sequence, content hashes and predecessor links.

        Events written by the first development spike did not yet carry a
        predecessor link.  They remain readable, but every event that does
        declare one is verified strictly.  This keeps historical compatibility
        without allowing new append-only records to be rewritten unnoticed.
        """

        reasons: list[str] = []
        directory = self._events(workspace_id) / require_safe_id(review_id, "review_id")
        raw_paths = sorted(directory.glob("[0-9]*.json")) if directory.is_dir() else []
        for path in raw_paths:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                reasons.append(f"event_unreadable:{path.stem}")
                continue
            if not isinstance(loaded, dict):
                reasons.append(f"event_invalid:{path.stem}")
        previous_hash = ""
        for expected_sequence, row in enumerate(self.events(workspace_id, review_id), start=1):
            if int(row.get("sequence") or 0) != expected_sequence:
                reasons.append(f"event_sequence_invalid:{expected_sequence}")
            declared_previous = row.get("previous_event_hash")
            if declared_previous is not None and str(declared_previous) != previous_hash:
                reasons.append(f"event_predecessor_invalid:{expected_sequence}")
            declared_hash = str(row.get("event_hash") or "")
            body = {key: value for key, value in row.items() if key != "event_hash"}
            if declared_hash != sha256_json(body):
                reasons.append(f"event_hash_invalid:{expected_sequence}")
            previous_hash = declared_hash
        return not reasons, reasons


STORE = ReviewEventStore()
