"""lvke-project-planning application 拆分：公共基座。

信封、视图、幂等、十进制解析与下游失效扫描的公共底座，供 context /
market / factories / options / query 共享；子模块之间不得复制这些工具。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable

from filelock import FileLock
from lvke_mcp.runtime.evidence_qualification import (
    combine_evidence_policies,
    project_fact_may_be_certified,
)
from lvke_mcp.runtime.workspace import workspace_root
from lvke_mcp.runtime.storage import require_safe_id, sha256_json
from lvke_mcp.runtime.formal_promotion import (
    validate_same_formal_lineage,
)
from lvke_mcp.adapters.project_planning_repository import IDEMPOTENCY_STORE


def _envelope(
    *,
    success: bool,
    status: str,
    code: str = "",
    message: str = "",
    resource_uris: list[str] | None = None,
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    next_actions: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "success": success,
        "status": status,
        "resource_uris": resource_uris or [],
        "warnings": warnings or [],
        "blockers": blockers or [],
        "next_actions": next_actions or [],
        **extra,
    }
    if code:
        result["code"] = code
    if message:
        result["message"] = message
    return result


def _blocked(
    code: str,
    message: str,
    *,
    next_actions: list[str] | None = None,
) -> dict[str, Any]:
    return _envelope(
        success=False,
        status="blocked",
        code=code,
        message=message,
        blockers=[code],
        next_actions=next_actions,
    )


def _context_view(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record.get("payload") or {})
    return {
        **payload,
        "project_context_id": record["object_id"],
        "workspace_id": record["workspace_id"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "created_at": record["created_at"],
        "resource_uri": record["resource_uri"],
        "schema_version": record["schema_version"],
    }


def _applicability_view(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record.get("payload") or {})
    return {
        **payload,
        "input_applicability_id": record["object_id"],
        "workspace_id": record["workspace_id"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "created_at": record["created_at"],
        "resource_uri": record["resource_uri"],
        "schema_version": record["schema_version"],
    }


def _market_view(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record.get("payload") or {})
    return {
        **payload,
        "market_case_id": record["object_id"],
        "workspace_id": record["workspace_id"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "created_at": record["created_at"],
        "resource_uri": record["resource_uri"],
        "schema_version": record["schema_version"],
    }


def _planning_view(record: dict[str, Any], id_field: str) -> dict[str, Any]:
    payload = dict(record.get("payload") or {})
    return {
        **payload,
        id_field: record["object_id"],
        "workspace_id": record["workspace_id"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "created_at": record["created_at"],
        "resource_uri": record["resource_uri"],
        "schema_version": record["schema_version"],
    }


def _idempotency_lock(workspace_id: str) -> FileLock:
    directory = (
        workspace_root(require_safe_id(workspace_id, "workspace_id"))
        / "mcp_objects"
        / "project-planning"
    )
    directory.mkdir(parents=True, exist_ok=True)
    return FileLock(str(directory / ".idempotency.lock"), timeout=30)


def _idempotent_mutation(
    workspace_id: str,
    *,
    operation: str,
    idempotency_key: str,
    request_payload: dict[str, Any],
    mutation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    key_hash = "sha256:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    request_hash = sha256_json(request_payload)
    with _idempotency_lock(workspace_id):
        for record in IDEMPOTENCY_STORE.list(workspace_id):
            payload = record.get("payload") or {}
            if (
                payload.get("operation") == operation
                and payload.get("idempotency_key_hash") == key_hash
            ):
                if payload.get("request_hash") != request_hash:
                    return _blocked(
                        "idempotency_conflict",
                        "同一 idempotency_key 已用于不同请求",
                    )
                replay = dict(payload.get("response") or {})
                replay["idempotent_replay"] = True
                return replay
        response = mutation()
        if response.get("status") in {
            "ok",
            "partial",
            "missing_inputs",
            "blocked",
        }:
            IDEMPOTENCY_STORE.put(
                workspace_id,
                {
                    "operation": operation,
                    "idempotency_key_hash": key_hash,
                    "request_hash": request_hash,
                    "response": response,
                },
                producer=f"lvke-project-planning.{operation}",
                basis={
                    "operation": operation,
                    "idempotency_key_hash": key_hash,
                    "request_hash": request_hash,
                },
            )
        return response


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _planning_evidence_qualification(
    *parents: dict[str, Any],
) -> tuple[str, str, bool]:
    """Derive one fail-closed evidence qualification from immediate parents."""

    payloads = [
        parent.get("payload")
        if isinstance(parent.get("payload"), dict)
        else parent
        for parent in parents
        if isinstance(parent, dict)
    ]
    fallback_track = next(
        (
            str(payload.get("evidence_track"))
            for payload in reversed(payloads)
            if payload.get("evidence_track")
        ),
        "real",
    )
    evidence_policy = combine_evidence_policies(
        payloads,
        empty_policy=fallback_track,
    )
    evidence_track = (
        evidence_policy
        if evidence_policy
        in {"real", "source_reconstructed", "technical_fixture", "controlled_assumption", "sim_a_formal"}
        else fallback_track
    )
    project_fact_certified = project_fact_may_be_certified(
        evidence_policy,
        own_qualification_passed=True,
        parents=payloads,
    )
    return evidence_track, evidence_policy, project_fact_certified


def _planning_formal_lineage(
    workspace_id: str,
    *parents: dict[str, Any],
) -> dict[str, Any]:
    """Derive formal ancestry from verified immutable planning parents."""

    payloads = [
        parent.get("payload") if isinstance(parent.get("payload"), dict) else parent
        for parent in parents
        if isinstance(parent, dict)
    ]
    if not any(
        str(payload.get("evidence_policy") or payload.get("evidence_track") or "")
        == "sim_a_formal"
        for payload in payloads
    ):
        return {}
    return validate_same_formal_lineage(workspace_id, parents)


def _contains_object_id(value: Any, object_id: str) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_object_id(item, object_id) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_object_id(item, object_id) for item in value)
    return value == object_id


def _downstream_stale(
    workspace_id: str,
    upstream_object_id: str,
    *,
    reason: str = "project_context_superseded",
) -> list[dict[str, Any]]:
    root = workspace_root(workspace_id) / "mcp_objects"
    if not root.is_dir():
        return []
    stale: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        if "idempotency" in path.parts:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            not isinstance(record, dict)
            or record.get("object_id") == upstream_object_id
        ):
            continue
        payload = record.get("payload")
        if not _contains_object_id(payload, upstream_object_id):
            continue
        stale.append(
            {
                "object_id": record.get("object_id"),
                "object_type": (payload or {}).get(
                    "object_type", record.get("producer", "unknown")
                ),
                "basis_hash": record.get("basis_hash"),
                "reason": reason,
            }
        )
    return stale

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "Callable",
    "Decimal",
    "FileLock",
    "IDEMPOTENCY_STORE",
    "InvalidOperation",
    "Path",
    "ROUND_HALF_UP",
    "_applicability_view",
    "_blocked",
    "_contains_object_id",
    "_context_view",
    "_decimal",
    "_downstream_stale",
    "_envelope",
    "_idempotency_lock",
    "_idempotent_mutation",
    "_market_view",
    "_planning_evidence_qualification",
    "_planning_formal_lineage",
    "_planning_view",
    "combine_evidence_policies",
    "hashlib",
    "json",
    "project_fact_may_be_certified",
    "require_safe_id",
    "sha256_json",
    "validate_same_formal_lineage",
    "workspace_root",
]
