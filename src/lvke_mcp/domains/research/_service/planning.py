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


_SOURCE_REQUIRED_FIELDS = (
    "source_type",
    "object_id",
    "resource_uri",
    "content_hash",
    "locator",
    "evidence_track",
    "allowed_uses",
)
_SOURCE_URI_DOMAINS = {
    "source_snapshot": "data-acquisition",
    "source_file": "source-files",
    "evidence_pack": "data-analysis",
}
_SOURCE_TRACK_CONSTRAINTS = {
    "technical_fixture": "technical_fixture",
    "source_reconstructed": "source_reconstructed",
}


def _describe_source_rejection(sources: list[Any]) -> dict[str, Any] | None:
    """Name the matched branch and the exact missing/invalid fields.

    传输层 schema 已能拦下大部分错误输入，但错误响应必须自己说清"命中哪个分支、
    具体缺什么"——否则调用方只能看到 oneOf/allOf 的通用失败，无从修正。
    """

    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            return {
                "code": "source_descriptor_invalid",
                "message": f"sources[{index}] 必须是对象",
                "source_index": index,
                "matched_branch": None,
                "missing_fields": list(_SOURCE_REQUIRED_FIELDS),
            }
        source_type = str(source.get("source_type") or "")
        if not source_type:
            return {
                "code": "source_type_required",
                "message": f"sources[{index}] 缺少判别字段 source_type",
                "source_index": index,
                "matched_branch": None,
                "missing_fields": ["source_type"],
                "supported_source_types": sorted(
                    {*_SOURCE_URI_DOMAINS, *_SOURCE_TRACK_CONSTRAINTS}
                ),
            }
        missing = [
            field
            for field in _SOURCE_REQUIRED_FIELDS
            if source.get(field) in (None, "", [], {})
        ]
        if missing:
            return {
                "code": "source_descriptor_incomplete",
                "message": (
                    f"sources[{index}] 命中 source_type={source_type} 分支，"
                    f"缺少必填字段：{'、'.join(missing)}"
                ),
                "source_index": index,
                "matched_branch": source_type,
                "missing_fields": missing,
                "required_fields": list(_SOURCE_REQUIRED_FIELDS),
            }
        domain = _SOURCE_URI_DOMAINS.get(source_type)
        uri = str(source.get("resource_uri") or "")
        if domain and not uri.startswith(f"lvke://{domain}/"):
            return {
                "code": "source_resource_uri_domain_mismatch",
                "message": (
                    f"sources[{index}] 命中 source_type={source_type} 分支，"
                    f"resource_uri 必须以 lvke://{domain}/ 开头，实际为 {uri[:80]}"
                ),
                "source_index": index,
                "matched_branch": source_type,
                "invalid_fields": ["resource_uri"],
                "expected_uri_prefix": f"lvke://{domain}/",
            }
        required_track = _SOURCE_TRACK_CONSTRAINTS.get(source_type)
        track = str(source.get("evidence_track") or "")
        if required_track and track != required_track:
            return {
                "code": "source_evidence_track_mismatch",
                "message": (
                    f"sources[{index}] 命中 source_type={source_type} 分支，"
                    f"evidence_track 必须为 {required_track}，实际为 {track}"
                ),
                "source_index": index,
                "matched_branch": source_type,
                "invalid_fields": ["evidence_track"],
                "expected_evidence_track": required_track,
            }
        if source_type == "technical_fixture":
            uses = [str(item) for item in (source.get("allowed_uses") or [])]
            if set(uses) - {"technical_validation"}:
                return {
                    "code": "source_allowed_uses_not_permitted",
                    "message": (
                        f"sources[{index}] 命中 technical_fixture 分支，"
                        "allowed_uses 只能是 technical_validation"
                    ),
                    "source_index": index,
                    "matched_branch": source_type,
                    "invalid_fields": ["allowed_uses"],
                    "expected_allowed_uses": ["technical_validation"],
                }
    return None


def add_sources(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    rejection = _describe_source_rejection(list(args.get("sources") or []))
    if rejection is not None:
        failure = _failure(str(rejection["code"]), str(rejection["message"]))
        failure.update(
            {key: value for key, value in rejection.items() if key not in {"code", "message"}}
        )
        failure["next_actions"] = [
            "按 matched_branch 对应的 required_fields 补齐字段后重试；"
            "source_type 是显式判别式，不同类型的 resource_uri 域与 evidence_track 约束不同",
        ]
        return failure
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
