"""Immutable knowledge candidates, human review, and governed release."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from filelock import FileLock
from lvke_mcp.runtime.workspace import workspace_root

from lvke_mcp.runtime.storage import (
    JSONArtifactStore,
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
)

CANDIDATE_STORE = JSONArtifactStore(
    "knowledge-governance", "candidates", "knc", "candidates"
)
REVIEW_STORE = JSONArtifactStore(
    "knowledge-governance", "reviews", "knr", "reviews"
)
RELEASE_STORE = JSONArtifactStore(
    "knowledge-governance", "releases", "knrel", "releases"
)
IDEMPOTENCY_STORE = JSONArtifactStore(
    "knowledge-governance", "idempotency", "idem", "idempotency"
)

_RESOURCE_STORES = (
    (CANDIDATE_STORE, "KnowledgeCandidate"),
    (REVIEW_STORE, "KnowledgeReview"),
    (RELEASE_STORE, "KnowledgeRelease"),
)


def _envelope(
    success: bool,
    status: str,
    *,
    code: str = "",
    message: str = "",
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    next_actions: list[str] | None = None,
    resource_uris: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "success": success,
        "business_success": success,
        "system_success": True,
        "transport_success": True,
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


def _blocked(code: str, message: str, *, next_actions: list[str] | None = None) -> dict[str, Any]:
    return _envelope(
        False,
        "blocked",
        code=code,
        message=message,
        blockers=[code],
        next_actions=next_actions,
    )


def _idempotency_lock(workspace_id: str) -> FileLock:
    directory = (
        workspace_root(require_safe_id(workspace_id, "workspace_id"))
        / "mcp_objects"
        / "knowledge-governance"
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
                if str(payload.get("request_hash") or "") != request_hash:
                    return _blocked(
                        "idempotency_conflict",
                        "同一 idempotency_key 已用于不同请求",
                    )
                replay = dict(payload.get("response") or {})
                replay["idempotent_replay"] = True
                return replay
        response = mutation()
        IDEMPOTENCY_STORE.put(
            workspace_id,
            {
                "operation": operation,
                "idempotency_key_hash": key_hash,
                "request_hash": request_hash,
                "response": response,
            },
            producer=f"lvke-knowledge-governance.{operation}",
        )
        return response


def _candidate_view(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record.get("payload") or {})
    return {
        **payload,
        "candidate_id": record["object_id"],
        "workspace_id": record["workspace_id"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "created_at": record["created_at"],
        "resource_uri": record["resource_uri"],
    }


def _object_view(record: dict[str, Any], id_field: str) -> dict[str, Any]:
    return {
        **dict(record.get("payload") or {}),
        id_field: record["object_id"],
        "workspace_id": record["workspace_id"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "created_at": record["created_at"],
        "resource_uri": record["resource_uri"],
    }


def _reviews_for_candidate(
    workspace_id: str,
    candidate_id: str,
) -> list[dict[str, Any]]:
    rows = [
        item for item in REVIEW_STORE.list(workspace_id)
        if str((item.get("payload") or {}).get("candidate_id") or "") == candidate_id
    ]
    return sorted(rows, key=lambda item: str(item.get("created_at") or ""))


def _releases_for_candidate(
    workspace_id: str,
    candidate_id: str,
) -> list[dict[str, Any]]:
    return [
        item for item in RELEASE_STORE.list(workspace_id)
        if str((item.get("payload") or {}).get("candidate_id") or "") == candidate_id
    ]


def _validate_evidence(evidence: list[dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for index, item in enumerate(evidence):
        if str(item.get("evidence_track") or "") == "controlled_assumption":
            blockers.append(f"controlled_assumption_not_knowledge_evidence:{index}")
        if str(item.get("source_type") or "") == "search_summary":
            blockers.append(f"search_summary_not_knowledge_evidence:{index}")
        if not str(item.get("resource_uri") or "").startswith("lvke://"):
            blockers.append(f"immutable_resource_required:{index}")
        if not str(item.get("locator") or "").strip():
            blockers.append(f"evidence_locator_required:{index}")
    return blockers


def submit_candidate(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    candidate = dict(args["candidate"])
    evidence = [dict(item) for item in candidate.get("evidence_bindings") or []]
    blockers = _validate_evidence(evidence)
    from lvke_mcp.servers.lvke_deliverable_review.rubrics import ASSESSMENT_STORE

    assessment_id = str(candidate.get("rubric_assessment_id") or "")
    assessment = (
        ASSESSMENT_STORE.get(workspace_id, assessment_id)
        if assessment_id else None
    )
    if not assessment_id or assessment is None:
        blockers.append("rubric_assessment_not_found")
    elif not bool((assessment.get("payload") or {}).get("passing")):
        blockers.append("rubric_assessment_not_passing")
    source_revision_id = str(candidate.get("source_revision_id") or "")
    assessed_revision_id = str(
        ((assessment or {}).get("payload") or {}).get("report_revision_id") or ""
    )
    if source_revision_id and assessed_revision_id != source_revision_id:
        blockers.append("rubric_revision_mismatch")
    if blockers:
        return _envelope(
            False,
            "blocked",
            code="knowledge_candidate_evidence_ineligible",
            message="知识候选必须绑定可定位的不可变证据",
            blockers=blockers,
            next_actions=["补充不可变 Resource、content hash 和 locator 后重新提交"],
        )
    payload = {
        **candidate,
        "candidate_status": "pending_review",
        "evidence_bindings": evidence,
    }
    request_payload = {"candidate": candidate}

    def create() -> dict[str, Any]:
        record = CANDIDATE_STORE.put(
            workspace_id,
            payload,
            producer="lvke-knowledge-governance.knowledge_submit_candidate",
            source_ids=[
                str(candidate.get("source_revision_id") or ""),
                str(candidate.get("rubric_assessment_id") or ""),
            ],
            basis={
                "candidate": candidate,
            },
        )
        view = _candidate_view(record)
        return _envelope(
            True,
            "ok",
            candidate=view,
            candidate_id=record["object_id"],
            candidate_status="pending_review",
            resource_uris=[record["resource_uri"]],
            next_actions=["调用 knowledge_review_candidate 记录内容质量审查结果"],
        )

    return _idempotent_mutation(
        workspace_id,
        operation="knowledge_submit_candidate",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request_payload,
        mutation=create,
    )


def list_candidates(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    status_filter = str(args.get("candidate_status") or "")
    industry = str(args.get("industry") or "")
    section_id = str(args.get("section_id") or "")
    candidate_type = str(args.get("candidate_type") or "")
    rows = []
    for record in CANDIDATE_STORE.list(workspace_id):
        view = _candidate_view(record)
        reviews = _reviews_for_candidate(workspace_id, record["object_id"])
        releases = _releases_for_candidate(workspace_id, record["object_id"])
        effective_status = (
            "published" if releases else
            str((reviews[-1].get("payload") or {}).get("decision") or "pending_review")
            if reviews else "pending_review"
        )
        view["candidate_status"] = effective_status
        if status_filter and effective_status != status_filter:
            continue
        if industry and str(view.get("industry") or "") != industry:
            continue
        if section_id and str(view.get("section_id") or "") != section_id:
            continue
        if candidate_type and str(view.get("candidate_type") or "") != candidate_type:
            continue
        rows.append(view)
    rows.sort(key=lambda item: (str(item.get("created_at") or ""), item["candidate_id"]))
    offset = int(args.get("offset") or 0)
    limit = int(args.get("limit") or 50)
    page = rows[offset:offset + limit]
    return _envelope(
        True,
        "ok",
        candidates=page,
        total=len(rows),
        next_offset=(offset + len(page)) if offset + len(page) < len(rows) else None,
    )


def get_candidate(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    candidate_id = str(args["candidate_id"])
    record = CANDIDATE_STORE.get(workspace_id, candidate_id)
    if record is None:
        return _blocked("knowledge_candidate_not_found", "知识候选不存在或不属于当前工作区")
    reviews = [
        _object_view(item, "knowledge_review_id")
        for item in _reviews_for_candidate(workspace_id, candidate_id)
    ]
    releases = [
        _object_view(item, "knowledge_release_id")
        for item in _releases_for_candidate(workspace_id, candidate_id)
    ]
    return _envelope(
        True,
        "ok",
        candidate=_candidate_view(record),
        reviews=reviews,
        releases=releases,
        resource_uris=[
            record["resource_uri"],
            *[item["resource_uri"] for item in reviews],
            *[item["resource_uri"] for item in releases],
        ],
    )


def review_candidate(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    candidate_id = str(args["candidate_id"])
    decision = str(args["decision"])
    candidate = CANDIDATE_STORE.get(workspace_id, candidate_id)
    if candidate is None:
        return _blocked("knowledge_candidate_not_found", "知识候选不存在或不属于当前工作区")
    request_payload = {
        "candidate_id": candidate_id,
        "decision": decision,
        "review_note": str(args["review_note"]),
        "required_changes": list(args.get("required_changes") or []),
    }

    def create() -> dict[str, Any]:
        existing = _reviews_for_candidate(workspace_id, candidate_id)
        if existing:
            return _blocked("knowledge_candidate_already_reviewed", "知识候选已有不可变审定结果")
        record = REVIEW_STORE.put(
            workspace_id,
            {
                **request_payload,
                "candidate_basis_hash": candidate["basis_hash"],
            },
            producer="lvke-knowledge-governance.knowledge_review_candidate",
            status=decision,
            source_ids=[candidate_id],
            basis={
                "candidate_id": candidate_id,
                "candidate_basis_hash": candidate["basis_hash"],
                "decision": decision,
            },
        )
        return _envelope(
            True,
            "ok",
            knowledge_review=_object_view(record, "knowledge_review_id"),
            knowledge_review_id=record["object_id"],
            candidate_status=decision,
            resource_uris=[record["resource_uri"]],
            next_actions=(
                ["调用 knowledge_publish_release 固化 reviewed knowledge"]
                if decision == "accepted"
                else (["按 required_changes 创建新的知识候选"] if decision == "request_changes" else [])
            ),
        )

    return _idempotent_mutation(
        workspace_id,
        operation="knowledge_review_candidate",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request_payload,
        mutation=create,
    )


def publish_release(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    candidate_id = str(args["candidate_id"])
    candidate = CANDIDATE_STORE.get(workspace_id, candidate_id)
    if candidate is None:
        return _blocked("knowledge_candidate_not_found", "知识候选不存在或不属于当前工作区")
    reviews = _reviews_for_candidate(workspace_id, candidate_id)
    if not reviews or str((reviews[-1].get("payload") or {}).get("decision") or "") != "accepted":
        return _blocked("knowledge_candidate_not_accepted", "知识候选尚未通过内容质量审查")
    review = reviews[-1]
    releases = _releases_for_candidate(workspace_id, candidate_id)
    if releases:
        release = releases[0]
        return _envelope(
            True,
            "ok",
            knowledge_release=_object_view(release, "knowledge_release_id"),
            knowledge_release_id=release["object_id"],
            idempotent_replay=True,
            resource_uris=[release["resource_uri"]],
        )
    payload = dict(candidate.get("payload") or {})
    request_payload = {"candidate_id": candidate_id, "review_id": review["object_id"]}

    def create() -> dict[str, Any]:
        evidence = [
            {
                "source_path": str(item.get("resource_uri") or ""),
                "locator": str(item.get("locator") or ""),
                "evidence_grade": "A" if item.get("evidence_track") == "real" else "T",
                "source_sha256": str(item.get("content_hash") or "").removeprefix("sha256:"),
                "revision_workspace_id": workspace_id,
            }
            for item in payload.get("evidence_bindings") or []
        ]
        memory_id = "mem_" + sha256_json({"candidate_id": candidate_id, "content": payload.get("content")})[7:31]
        memory = {
            "id": memory_id,
            "version": 1,
            "status": "published",
            "content": str(payload.get("content") or ""),
            "source_fingerprint": sha256_json(evidence),
        }
        release = RELEASE_STORE.put(
            workspace_id,
            {
                "candidate_id": candidate_id,
                "knowledge_review_id": review["object_id"],
                "memory_id": str(memory.get("id") or ""),
                "memory_version": memory.get("version"),
                "memory_status": str(memory.get("status") or ""),
                "memory_source_fingerprint": str(memory.get("source_fingerprint") or ""),
                "legacy_mirror": {},
            },
            producer="lvke-knowledge-governance.knowledge_publish_release",
            status="published",
            source_ids=[candidate_id, review["object_id"], str(memory.get("id") or "")],
            basis={
                "candidate_basis_hash": candidate["basis_hash"],
                "review_basis_hash": review["basis_hash"],
                "memory_id": str(memory.get("id") or ""),
            },
        )
        return _envelope(
            True,
            "ok",
            knowledge_release=_object_view(release, "knowledge_release_id"),
            knowledge_release_id=release["object_id"],
            memory_id=str(memory.get("id") or ""),
            resource_uris=[release["resource_uri"]],
            warnings=[],
        )

    return _idempotent_mutation(
        workspace_id,
        operation="knowledge_publish_release",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request_payload,
        mutation=create,
    )


def list_resources(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    requested_type = str(args.get("resource_type") or "")
    entries: list[dict[str, Any]] = []
    for store, object_type in _RESOURCE_STORES:
        if requested_type and requested_type != object_type:
            continue
        for record in store.list(workspace_id):
            entries.append({
                "uri": record["resource_uri"],
                "name": f"{object_type} {record['object_id']}",
                "mime_type": "application/json",
                "object_type": object_type,
                "content_hash": record["content_hash"],
                "basis_hash": record["basis_hash"],
            })
    page = paginate_resource_entries(
        entries,
        cursor=str(args.get("cursor") or ""),
        limit=int(args.get("limit") or 50),
    )
    return _envelope(True, "ok", **page)


def resolve_resource(
    uri: str,
) -> tuple[str, str] | None:
    """Resolve an immutable knowledge Resource for the MCP resources/read path."""

    for store, _object_type in _RESOURCE_STORES:
        record = store.resolve_uri(str(uri))
        if record is not None:
            return json.dumps(record, ensure_ascii=False), "application/json"
    return None


def read_resource(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    uri = str(args["uri"])
    for store, object_type in _RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is None or record.get("workspace_id") != workspace_id:
            continue
        return _envelope(
            True,
            "ok",
            object_type=object_type,
            resource=record,
            content_hash=record["content_hash"],
            resource_uris=[record["resource_uri"]],
        )
    return _blocked("knowledge_resource_not_found", "知识治理 Resource 不存在或不属于当前工作区")


__all__ = [
    "submit_candidate",
    "list_candidates",
    "get_candidate",
    "review_candidate",
    "publish_release",
    "list_resources",
    "resolve_resource",
    "read_resource",
]
