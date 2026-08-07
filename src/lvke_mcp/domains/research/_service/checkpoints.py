"""断点续研：resume token 签发校验、断点创建与恢复。"""

from __future__ import annotations

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from filelock import FileLock
from lvke_mcp.runtime.workspace import workspace_root

from lvke_mcp.adapters.research_repository import AGENT_SESSION_STORE, CHECKPOINT_STORE, PLAN_STORE
from lvke_mcp.runtime.storage import canonical_json

from .base import _append_event, _failure

from .agent_lifecycle import (
    agent_status,
    start_agent,
)

from .planning import (
    _latest_plan,
)


def _resume_signing_key(workspace_id: str) -> bytes:
    configured = os.getenv("LVKE_DR_RESUME_SIGNING_KEY", "").strip()
    if configured:
        return configured.encode("utf-8")
    directory = workspace_root(workspace_id) / "mcp_objects" / "deep-research"
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / ".resume-signing-key"
    with FileLock(str(key_path) + ".lock", timeout=30):
        if key_path.is_file():
            value = key_path.read_bytes()
            if len(value) >= 32:
                return value
        value = os.urandom(32)
        temporary = key_path.with_name(f".{key_path.name}.tmp")
        temporary.write_bytes(value)
        os.chmod(temporary, 0o600)
        os.replace(temporary, key_path)
        return value

def _sign_resume_token(workspace_id: str, claims: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(canonical_json(claims).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_resume_signing_key(workspace_id), payload.encode("ascii"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"drresume.v1.{payload}.{encoded_signature}"

def _verify_resume_token(workspace_id: str, token: str) -> dict[str, Any]:
    try:
        prefix, version, payload, signature = token.split(".")
        if (prefix, version) != ("drresume", "v1"):
            raise ValueError
        expected = hmac.new(_resume_signing_key(workspace_id), payload.encode("ascii"), hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode((signature + "=" * (-len(signature) % 4)).encode("ascii"))
        canonical_signature = base64.urlsafe_b64encode(actual).decode("ascii").rstrip("=")
        if not hmac.compare_digest(signature, canonical_signature):
            raise ValueError
        if not hmac.compare_digest(expected, actual):
            raise ValueError
        raw = base64.urlsafe_b64decode((payload + "=" * (-len(payload) % 4)).encode("ascii"))
        claims = json.loads(raw.decode("utf-8"))
        if not isinstance(claims, dict):
            raise ValueError
        return claims
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("resume_token_invalid") from exc

def create_checkpoint(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    session = AGENT_SESSION_STORE.get(workspace_id, task_id)
    if session is None:
        return _failure("task_not_found", "未找到 Agent DR 会话")
    current = _latest_plan(workspace_id, task_id)
    if current is None:
        return _failure("plan_not_found", "未找到可固化的研究计划")
    if str(args.get("expected_basis_hash") or "") != str(current.get("basis_hash") or ""):
        return _failure("basis_hash_conflict", "研究计划已变更，不能创建 checkpoint")
    current_payload = current.get("payload") or {}
    task_state = agent_status(workspace_id, task_id) or {}
    payload = {
        "task_id": task_id,
        "plan_revision_id": current["object_id"],
        "plan_basis_hash": current["basis_hash"],
        "budget": current_payload.get("budget") or (session.get("payload") or {}).get("budget") or {},
        "sources": list(current_payload.get("sources") or []),
        "quality_state": current_payload.get("quality_state") or task_state.get("quality") or {},
        "pending_work": list(current_payload.get("pending_work") or []),
        "task_status": str(task_state.get("status") or "agent_collecting"),
        "reason": str(args.get("reason") or "")[:2000],
    }
    checkpoint = CHECKPOINT_STORE.put(
        workspace_id,
        payload,
        producer="lvke-deep-research.dr_create_checkpoint",
        status="checkpointed",
        source_ids=[task_id, current["object_id"]],
        basis=payload,
    )
    ttl = max(60, min(int(args.get("expires_in_seconds") or 86400), 604800))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    claims = {
        "workspace_id": workspace_id,
        "task_id": task_id,
        "checkpoint_id": checkpoint["object_id"],
        "basis_hash": checkpoint["basis_hash"],
        "expires_at": expires_at.isoformat(),
    }
    token = _sign_resume_token(workspace_id, claims)
    _append_event(
        workspace_id,
        task_id,
        "checkpoint_created",
        {
            "checkpoint_id": checkpoint["object_id"],
            "expires_at": claims["expires_at"],
        },
    )
    return {
        "success": True,
        "status": "ok",
        "task_id": task_id,
        "checkpoint_id": checkpoint["object_id"],
        "basis_hash": checkpoint["basis_hash"],
        "resume_token": token,
        "expires_at": claims["expires_at"],
        "resource_uris": [checkpoint["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": ["需恢复时调用 dr_resume；令牌不可跨 workspace 使用"],
    }

def resume_from_checkpoint(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    token = str(args.get("resume_token") or "")
    try:
        claims = _verify_resume_token(workspace_id, token)
    except ValueError:
        return _failure("resume_token_invalid", "恢复令牌无效或已被篡改")
    if claims.get("workspace_id") != workspace_id:
        return _failure("resume_scope_mismatch", "恢复令牌不属于当前 workspace")
    try:
        expires_at = datetime.fromisoformat(str(claims.get("expires_at") or ""))
    except ValueError:
        return _failure("resume_token_invalid", "恢复令牌缺少有效期")
    if expires_at <= datetime.now(timezone.utc):
        return _failure("resume_token_expired", "恢复令牌已过期")
    checkpoint_id = str(claims.get("checkpoint_id") or "")
    checkpoint = CHECKPOINT_STORE.get(workspace_id, checkpoint_id)
    if checkpoint is None or str(checkpoint.get("basis_hash") or "") != str(claims.get("basis_hash") or ""):
        return _failure("checkpoint_not_found", "恢复令牌对应的 checkpoint 不存在")
    checkpoint_payload = checkpoint.get("payload") or {}
    original_task_id = str(checkpoint_payload.get("task_id") or "")
    session = AGENT_SESSION_STORE.get(workspace_id, original_task_id)
    plan = PLAN_STORE.get(
        workspace_id,
        str(checkpoint_payload.get("plan_revision_id") or ""),
    )
    if session is None or plan is None:
        return _failure("checkpoint_lineage_missing", "checkpoint 的任务或计划 lineage 不完整")
    session_payload = session.get("payload") or {}
    plan_payload = plan.get("payload") or {}
    started = start_agent(
        {
            "workspace_id": workspace_id,
            "topic": session_payload.get("topic") or "恢复研究",
            "industry": session_payload.get("industry") or "",
            "region": session_payload.get("region") or "",
            "research_brief": plan_payload.get("research_brief") or {},
            "plan_items": plan_payload.get("plan_items") or [],
            "subqueries": list(args.get("supplemental_questions") or []),
            "budget": checkpoint_payload.get("budget") or {},
            "source_policy": session_payload.get("source_policy") or {},
            "source_descriptors": checkpoint_payload.get("sources") or [],
            "quality_state": checkpoint_payload.get("quality_state") or {},
            "pending_work": checkpoint_payload.get("pending_work") or [],
            "continued_from_task_id": original_task_id,
            "resumed_from_checkpoint_id": checkpoint_id,
            "idempotency_key": str(args.get("idempotency_key") or f"resume:{checkpoint_id}"),
        }
    )
    if not started.get("success"):
        return started
    if not started.get("replayed"):
        _append_event(
            workspace_id,
            original_task_id,
            "task_resumed",
            {
                "checkpoint_id": checkpoint_id,
                "new_task_id": started["task_id"],
            },
        )
    return {
        "success": True,
        "status": "ok",
        "task_id": started["task_id"],
        "resumed_from_task_id": original_task_id,
        "checkpoint_id": checkpoint_id,
        "plan_revision_id": started.get("plan_revision_id"),
        "plan_basis_hash": started.get("plan_basis_hash"),
        "replayed": bool(started.get("replayed")),
        "resource_uris": [str(started.get("resource_uri") or "")],
        "warnings": ["恢复创建新任务；原任务和 checkpoint 保持不变"],
        "blockers": [],
        "next_actions": ["调用 dr_get_plan 核对恢复后的计划与来源"],
    }

def load_checkpoint(workspace_id: str, task_id: str) -> Any:
    """Read an MCP-owned checkpoint for a task, if one exists."""
    records = [
        record for record in CHECKPOINT_STORE.list(workspace_id)
        if str((record.get("payload") or {}).get("task_id") or "") == str(task_id)
    ]
    if not records:
        return None
    return records[-1].get("payload") or None
