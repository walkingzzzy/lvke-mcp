"""研究计划读写：修订提案、应用修订与 source 增删。"""

from __future__ import annotations

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


from lvke_mcp.adapters.research_repository import AGENT_SESSION_STORE, PLAN_PROPOSAL_STORE, PLAN_STORE
from lvke_mcp.runtime.storage import sha256_json

from .base import (
    _agent_transition_guard,
    _append_event,
    _failure,
)


def _plan_records(
    workspace_id: str,
    task_id: str,
) -> list[dict[str, Any]]:
    records = [
        record
        for record in PLAN_STORE.list(workspace_id)
        if str((record.get("payload") or {}).get("task_id") or "") == task_id
    ]
    return sorted(
        records,
        key=lambda record: (
            int((record.get("payload") or {}).get("revision_no") or 0),
            str(record.get("created_at") or ""),
            str(record.get("object_id") or ""),
        ),
    )

def _latest_plan(
    workspace_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    records = _plan_records(workspace_id, task_id)
    return records[-1] if records else None

def _create_plan_revision(
    workspace_id: str,
    task_id: str,
    content: dict[str, Any],
    *,
    parent_plan_revision_id: str,
    source_ids: list[str],
    producer: str,
) -> dict[str, Any]:
    previous = _plan_records(workspace_id, task_id)
    revision_no = (int((previous[-1].get("payload") or {}).get("revision_no") or 0) + 1) if previous else 1
    normalized = {
        "research_brief": content.get("research_brief") if isinstance(content.get("research_brief"), dict) else {},
        "plan_items": list(content.get("plan_items") or []),
        "budget": content.get("budget") if isinstance(content.get("budget"), dict) else {},
        "sources": list(content.get("sources") or []),
        "excluded_sources": list(content.get("excluded_sources") or []),
        "quality_state": content.get("quality_state") if isinstance(content.get("quality_state"), dict) else {},
        "pending_work": list(content.get("pending_work") or []),
    }
    payload = {
        "task_id": task_id,
        "revision_no": revision_no,
        "parent_plan_revision_id": parent_plan_revision_id,
        **normalized,
    }
    return PLAN_STORE.put(
        workspace_id,
        payload,
        producer=producer,
        status="active",
        source_ids=[task_id, parent_plan_revision_id, *source_ids],
        basis={"task_id": task_id, "parent_plan_revision_id": parent_plan_revision_id, **normalized},
    )

def get_plan(
    workspace_id: str,
    task_id: str,
    *,
    plan_revision_id: str = "",
) -> dict[str, Any]:
    session = AGENT_SESSION_STORE.get(workspace_id, task_id)
    if session is None:
        return _failure("task_not_found", "未找到 Agent DR 会话")
    record = (
        PLAN_STORE.get(workspace_id, plan_revision_id)
        if plan_revision_id
        else _latest_plan(workspace_id, task_id)
    )
    if record is None or str((record.get("payload") or {}).get("task_id") or "") != task_id:
        return _failure("plan_not_found", "未找到当前任务的研究计划版本")
    return {
        "success": True,
        "status": "ok",
        "task_id": task_id,
        "plan_revision_id": record["object_id"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "plan": record["payload"],
        "resource_uris": [record["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }

def propose_plan_revision(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    current = _latest_plan(workspace_id, task_id)
    if current is None:
        return _failure("plan_not_found", "未找到可修订的研究计划")
    expected = str(args.get("expected_basis_hash") or "")
    if expected != str(current.get("basis_hash") or ""):
        return _failure("basis_hash_conflict", "研究计划已变更，请重新读取后提案")
    changes = args.get("changes") if isinstance(args.get("changes"), dict) else {}
    base = current.get("payload") or {}
    proposed = {
        key: changes.get(key, base.get(key))
        for key in (
            "research_brief",
            "plan_items",
            "budget",
            "sources",
            "excluded_sources",
            "quality_state",
            "pending_work",
        )
    }
    if proposed == {key: base.get(key) for key in proposed}:
        return _failure("empty_plan_revision", "提案未改变研究计划")
    payload = {
        "task_id": task_id,
        "base_plan_revision_id": current["object_id"],
        "base_basis_hash": current["basis_hash"],
        "reason": str(args.get("reason") or "")[:2000],
        "proposed_plan": proposed,
    }
    proposal = PLAN_PROPOSAL_STORE.put(
        workspace_id,
        payload,
        producer="lvke-deep-research.dr_propose_plan_revision",
        status="proposed",
        source_ids=[task_id, current["object_id"]],
        basis=payload,
    )
    _append_event(
        workspace_id,
        task_id,
        "plan_revision_proposed",
        {
            "proposal_id": proposal["object_id"],
            "base_plan_revision_id": current["object_id"],
        },
    )
    return {
        "success": True,
        "status": "ok",
        "task_id": task_id,
        "proposal_id": proposal["object_id"],
        "base_basis_hash": current["basis_hash"],
        "proposed_basis_hash": sha256_json(proposed),
        "resource_uris": [proposal["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": ["审阅提案后调用 dr_apply_plan_revision"],
    }

def apply_plan_revision(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    proposal_id = str(args["proposal_id"])
    with _agent_transition_guard(workspace_id, task_id):
        proposal = PLAN_PROPOSAL_STORE.get(workspace_id, proposal_id)
        if proposal is None or str((proposal.get("payload") or {}).get("task_id") or "") != task_id:
            return _failure("proposal_not_found", "未找到当前任务的研究计划提案")
        applied = [
            record
            for record in _plan_records(workspace_id, task_id)
            if proposal_id in (record.get("source_ids") or [])
        ]
        if applied:
            record = applied[-1]
            return {
                "success": True,
                "status": "ok",
                "task_id": task_id,
                "plan_revision_id": record["object_id"],
                "basis_hash": record["basis_hash"],
                "replayed": True,
                "resource_uris": [record["resource_uri"]],
                "warnings": [],
                "blockers": [],
                "next_actions": [],
            }
        current = _latest_plan(workspace_id, task_id)
        expected = str(args.get("expected_basis_hash") or "")
        proposal_payload = proposal.get("payload") or {}
        if (
            current is None
            or expected != str(current.get("basis_hash") or "")
            or expected != str(proposal_payload.get("base_basis_hash") or "")
        ):
            return _failure("basis_hash_conflict", "提案 basis 已过期，不能应用")
        record = _create_plan_revision(
            workspace_id,
            task_id,
            dict(proposal_payload.get("proposed_plan") or {}),
            parent_plan_revision_id=current["object_id"],
            source_ids=[proposal_id],
            producer="lvke-deep-research.dr_apply_plan_revision",
        )
        _append_event(
            workspace_id,
            task_id,
            "plan_revision_applied",
            {
                "proposal_id": proposal_id,
                "plan_revision_id": record["object_id"],
            },
        )
        return {
            "success": True,
            "status": "ok",
            "task_id": task_id,
            "plan_revision_id": record["object_id"],
            "basis_hash": record["basis_hash"],
            "replayed": False,
            "resource_uris": [record["resource_uri"]],
            "warnings": [],
            "blockers": [],
            "next_actions": [],
        }

def _source_identity(source: dict[str, Any]) -> str:
    return str(source.get("object_id") or "")

def add_sources(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    with _agent_transition_guard(workspace_id, task_id):
        current = _latest_plan(workspace_id, task_id)
        if current is None:
            return _failure("plan_not_found", "未找到可绑定来源的研究计划")
        if str(args.get("expected_basis_hash") or "") != str(current.get("basis_hash") or ""):
            return _failure("basis_hash_conflict", "研究计划已变更，请重新读取后添加来源")
        current_payload = dict(current.get("payload") or {})
        existing = {str(item.get("object_id") or ""): item for item in current_payload.get("sources") or []}
        for source in list(args.get("sources") or []):
            source_id = _source_identity(source)
            if source_id in existing and existing[source_id] != source:
                return _failure("source_binding_conflict", "同一来源对象已绑定不同 hash 或 locator")
            existing[source_id] = source
        current_payload["sources"] = sorted(existing.values(), key=_source_identity)
        record = _create_plan_revision(
            workspace_id,
            task_id,
            current_payload,
            parent_plan_revision_id=current["object_id"],
            source_ids=[_source_identity(source) for source in args.get("sources") or []],
            producer="lvke-deep-research.dr_add_sources",
        )
        _append_event(
            workspace_id,
            task_id,
            "sources_added",
            {
                "plan_revision_id": record["object_id"],
                "source_ids": [_source_identity(source) for source in args.get("sources") or []],
            },
        )
        return _plan_write_result(task_id, record)

def remove_sources(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    remove_ids = sorted({str(item) for item in args.get("source_object_ids") or []})
    with _agent_transition_guard(workspace_id, task_id):
        current = _latest_plan(workspace_id, task_id)
        if current is None:
            return _failure("plan_not_found", "未找到可修订的研究计划")
        if str(args.get("expected_basis_hash") or "") != str(current.get("basis_hash") or ""):
            return _failure("basis_hash_conflict", "研究计划已变更，请重新读取后移除来源")
        current_payload = dict(current.get("payload") or {})
        sources = list(current_payload.get("sources") or [])
        known = {_source_identity(source) for source in sources}
        missing = sorted(set(remove_ids) - known)
        if missing:
            return _failure("source_binding_not_found", "待移除来源不在当前计划中")
        current_payload["sources"] = [source for source in sources if _source_identity(source) not in remove_ids]
        exclusion = {
            "source_object_ids": remove_ids,
            "reason": str(args.get("reason") or "")[:2000],
            "excluded_at": datetime.now(timezone.utc).isoformat(),
        }
        current_payload["excluded_sources"] = [*(current_payload.get("excluded_sources") or []), exclusion]
        record = _create_plan_revision(
            workspace_id,
            task_id,
            current_payload,
            parent_plan_revision_id=current["object_id"],
            source_ids=remove_ids,
            producer="lvke-deep-research.dr_remove_sources",
        )
        _append_event(
            workspace_id,
            task_id,
            "sources_excluded",
            {
                "plan_revision_id": record["object_id"],
                **exclusion,
            },
        )
        result = _plan_write_result(task_id, record)
        result["excluded_source_object_ids"] = remove_ids
        return result

def _plan_write_result(task_id: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "status": "ok",
        "task_id": task_id,
        "plan_revision_id": record["object_id"],
        "basis_hash": record["basis_hash"],
        "resource_uris": [record["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }
