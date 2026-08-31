"""Immutable knowledge candidates and content-addressed snapshots."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from filelock import FileLock
from lvke_mcp.runtime.evidence_qualification import (
    combine_evidence_policies,
    declared_evidence_policy,
    project_fact_may_be_certified,
)
from lvke_mcp.runtime.workspace import workspace_root

from lvke_mcp.runtime.storage import (
    JSONArtifactStore,
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
    utc_now,
)

CANDIDATE_STORE = JSONArtifactStore(
    "knowledge-governance", "candidates", "knc", "candidates"
)
SNAPSHOT_STORE = JSONArtifactStore(
    "knowledge-governance", "snapshots", "kns", "snapshots"
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
RUBRIC_ASSESSMENT_STORE = JSONArtifactStore(
    "deliverable-review", "rubric_assessments", "rva", "rubric-assessments"
)

_RESOURCE_STORES = (
    (CANDIDATE_STORE, "KnowledgeCandidate"),
    (SNAPSHOT_STORE, "KnowledgeSnapshot"),
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


def _validate_evidence(evidence: list[dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for index, item in enumerate(evidence):
        track = str(item.get("evidence_track") or "")
        if track == "controlled_assumption":
            blockers.append(f"controlled_assumption_not_knowledge_evidence:{index}")
        if track not in {"real", "source_reconstructed", "technical_fixture", "controlled_assumption"}:
            blockers.append(f"evidence_track_invalid:{index}")
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

    assessment_id = str(candidate.get("rubric_assessment_id") or "")
    assessment = (
        RUBRIC_ASSESSMENT_STORE.get(workspace_id, assessment_id)
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
        # 三类原因此前共用同一个 code / message / next_actions（"必须绑定可定位
        # 的不可变证据"+"补充 Resource、hash 和 locator"），只有 blockers 里的
        # 码不同。但"评估不存在"要去建评估、"评估未达标"要先改章节到达标、
        # "证据 locator 缺失"才是补 Resource —— 处置完全不同，笼统一句会把人
        # 引向错误方向。这里按具体 blocker 分派。
        code = "knowledge_candidate_evidence_ineligible"
        message = "知识候选必须绑定可定位的不可变证据"
        next_actions = ["补充不可变 Resource、content hash 和 locator 后重新提交"]
        if "rubric_assessment_not_found" in blockers:
            code = "knowledge_candidate_rubric_missing"
            message = (
                "知识候选必须绑定同一工作区内真实存在的 RubricAssessment；"
                f"未找到 rubric_assessment_id={assessment_id or '(未提供)'}"
            )
            next_actions = [
                "先调用 review_score_section 对来源章节评分，取返回的 rubric_assessment_id",
                "确认该评估与本候选在同一 workspace_id 下",
            ]
        elif "rubric_assessment_not_passing" in blockers:
            payload = (assessment or {}).get("payload") or {}
            code = "knowledge_candidate_rubric_not_passing"
            message = (
                "来源章节的 rubric 评分未达标，不能沉淀为知识："
                f"weighted_score={payload.get('weighted_score')}，"
                f"passing={payload.get('passing')}"
            )
            next_actions = [
                "先按 rubric 各维度 signals 修订来源章节，重新 review_score_section 至 passing=true",
                "或改用已达标章节作为知识来源",
            ]
        elif "rubric_revision_mismatch" in blockers:
            code = "knowledge_candidate_rubric_revision_mismatch"
            message = (
                "rubric 评估针对的 report_revision 与 source_revision_id 不一致："
                f"评估自 {assessed_revision_id or '(空)'}，候选声明 {source_revision_id}"
            )
            next_actions = ["对 source_revision_id 指向的同一 revision 重新评分后再提交"]
        return _envelope(
            False,
            "blocked",
            code=code,
            message=message,
            blockers=blockers,
            next_actions=next_actions,
        )
    evidence_policy = combine_evidence_policies(evidence)
    # Evidence bindings are caller descriptors, not resolved immutable parent
    # records.  Until this service verifies each referenced object and hash it
    # must not certify project facts, even when a binding is labelled ``real``.
    project_fact_certified = project_fact_may_be_certified(
        evidence_policy,
        own_qualification_passed=False,
        parents=evidence,
    )
    payload = {
        **candidate,
        "candidate_status": "validated",
        "evidence_bindings": evidence,
        "evidence_policy": evidence_policy,
        "project_fact_certified": project_fact_certified,
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
            candidate_status="validated",
            resource_uris=[record["resource_uri"]],
            next_actions=["调用 knowledge_create_snapshot 生成内容寻址快照"],
        )

    return _idempotent_mutation(
        workspace_id,
        operation="knowledge_submit_candidate",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request_payload,
        mutation=create,
    )


def _snapshots_for_candidate(
    workspace_id: str,
    candidate_id: str,
) -> list[dict[str, Any]]:
    return [
        item for item in SNAPSHOT_STORE.list(workspace_id)
        if str((item.get("payload") or {}).get("candidate_id") or "") == candidate_id
    ]


def _reviews_for_candidate(workspace_id: str, candidate_id: str) -> list[dict[str, Any]]:
    rows = [
        item for item in REVIEW_STORE.list(workspace_id)
        if str((item.get("payload") or {}).get("candidate_id") or "") == candidate_id
    ]
    return sorted(rows, key=lambda item: str(item.get("created_at") or ""))


def _releases_for_candidate(workspace_id: str, candidate_id: str) -> list[dict[str, Any]]:
    rows = [
        item for item in RELEASE_STORE.list(workspace_id)
        if str((item.get("payload") or {}).get("candidate_id") or "") == candidate_id
    ]
    return sorted(rows, key=lambda item: str(item.get("created_at") or ""))


def _candidate_status(workspace_id: str, candidate_id: str) -> str:
    releases = _releases_for_candidate(workspace_id, candidate_id)
    if releases:
        return "published"
    reviews = _reviews_for_candidate(workspace_id, candidate_id)
    if reviews:
        return str((reviews[-1].get("payload") or {}).get("decision") or "needs_revision")
    if _snapshots_for_candidate(workspace_id, candidate_id):
        return "snapshotted"
    return "validated"


def list_candidates(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    status_filter = str(args.get("candidate_status") or "")
    industry = str(args.get("industry") or "")
    section_id = str(args.get("section_id") or "")
    candidate_type = str(args.get("candidate_type") or "")
    rows = []
    for record in CANDIDATE_STORE.list(workspace_id):
        view = _candidate_view(record)
        effective_status = _candidate_status(workspace_id, record["object_id"])
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
    snapshots = [
        _object_view(item, "knowledge_snapshot_id")
        for item in _snapshots_for_candidate(workspace_id, candidate_id)
    ]
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
        candidate_status=_candidate_status(workspace_id, candidate_id),
        snapshots=snapshots,
        reviews=reviews,
        releases=releases,
        resource_uris=[
            record["resource_uri"],
            *[item["resource_uri"] for item in snapshots],
            *[item["resource_uri"] for item in reviews],
            *[item["resource_uri"] for item in releases],
        ],
    )


def review_candidate(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    candidate_id = require_safe_id(args.get("candidate_id"), "candidate_id")
    decision = str(args.get("decision") or "")
    if decision not in {"accepted", "rejected", "needs_revision"}:
        return _blocked("knowledge_review_decision_invalid", "审核结论必须是 accepted、rejected 或 needs_revision")
    candidate = CANDIDATE_STORE.get(workspace_id, candidate_id)
    if candidate is None:
        return _blocked("knowledge_candidate_not_found", "知识候选不存在或不属于当前工作区")
    reason = str(args.get("reason") or args.get("review_note") or "").strip()
    if not reason:
        return _blocked("knowledge_review_reason_required", "审核结论必须提供 reason")
    rubric_assessment_id = str(args.get("rubric_assessment_id") or (candidate.get("payload") or {}).get("rubric_assessment_id") or "")
    request = {
        "candidate_id": candidate_id,
        "decision": decision,
        "reason": reason,
        "rubric_assessment_id": rubric_assessment_id,
        "required_changes": list(args.get("required_changes") or []),
    }

    def create() -> dict[str, Any]:
        candidate_payload = candidate.get("payload") or {}
        evidence_policy = declared_evidence_policy(candidate_payload, default="candidate")
        payload = {
            "candidate_id": candidate_id,
            "decision": decision,
            "reason": reason,
            "required_changes": request["required_changes"],
            "rubric_assessment_id": rubric_assessment_id,
            "candidate_basis_hash": candidate["basis_hash"],
            "candidate_content_hash": candidate["content_hash"],
            "evidence_hash": sha256_json((candidate.get("payload") or {}).get("evidence_bindings") or []),
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_may_be_certified(
                evidence_policy,
                own_qualification_passed=True,
                # 走 parents= 而非把父对象的 certified 当作自身合格信号：前者还会
                # 复核父对象的 evidence_policy，上游 combine 出错时仍能兜住。
                parents=[candidate_payload],
            ),
            "reviewed_at": utc_now(),
        }
        record = REVIEW_STORE.put(
            workspace_id,
            payload,
            producer="lvke-knowledge-governance.knowledge_review_candidate",
            source_ids=[candidate_id, rubric_assessment_id],
            basis=request,
            schema_version="knowledge_review.v1",
        )
        return _envelope(
            True,
            "ok",
            knowledge_review=_object_view(record, "knowledge_review_id"),
            knowledge_review_id=record["object_id"],
            candidate_id=candidate_id,
            decision=decision,
            evidence_hash=payload["evidence_hash"],
            resource_uris=[record["resource_uri"]],
            next_actions=["调用 knowledge_publish_release" if decision == "accepted" else "根据 required_changes 修改候选后重新提交"],
        )

    return _idempotent_mutation(
        workspace_id,
        operation="knowledge_review_candidate",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request,
        mutation=create,
    )


def publish_release(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    candidate_id = require_safe_id(args.get("candidate_id"), "candidate_id")
    review_id = require_safe_id(args.get("review_id") or args.get("knowledge_review_id"), "review_id")
    candidate = CANDIDATE_STORE.get(workspace_id, candidate_id)
    review = REVIEW_STORE.get(workspace_id, review_id)
    if candidate is None:
        return _blocked("knowledge_candidate_not_found", "知识候选不存在或不属于当前工作区")
    if review is None or str((review.get("payload") or {}).get("candidate_id") or "") != candidate_id:
        return _blocked("knowledge_review_not_found", "审核记录不存在或不匹配")
    if str((review.get("payload") or {}).get("decision") or "") != "accepted":
        return _blocked("knowledge_review_not_accepted", "只有 accepted 候选才能发布")
    request = {"candidate_id": candidate_id, "review_id": review_id, "release_note": str(args.get("release_note") or "")}

    def create() -> dict[str, Any]:
        payload = dict(candidate.get("payload") or {})
        evidence_policy = declared_evidence_policy(payload, default="candidate")
        release_payload = {
            "candidate_id": candidate_id,
            "review_id": review_id,
            "title": payload.get("title", ""),
            "content": payload.get("content", ""),
            "candidate_basis_hash": candidate["basis_hash"],
            "candidate_content_hash": candidate["content_hash"],
            "review_basis_hash": review["basis_hash"],
            "evidence_bindings": payload.get("evidence_bindings", []),
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_may_be_certified(
                evidence_policy,
                own_qualification_passed=True,
                parents=[payload],
            ),
            "release_note": request["release_note"],
            "released_at": utc_now(),
        }
        record = RELEASE_STORE.put(
            workspace_id,
            release_payload,
            producer="lvke-knowledge-governance.knowledge_publish_release",
            source_ids=[candidate_id, review_id],
            basis=request,
            schema_version="knowledge_release.v1",
        )
        return _envelope(
            True,
            "ok",
            knowledge_release=_object_view(record, "knowledge_release_id"),
            knowledge_release_id=record["object_id"],
            candidate_id=candidate_id,
            resource_uris=[record["resource_uri"]],
        )

    return _idempotent_mutation(
        workspace_id,
        operation="knowledge_publish_release",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request,
        mutation=create,
    )


def create_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    candidate_id = str(args["candidate_id"])
    candidate = CANDIDATE_STORE.get(workspace_id, candidate_id)
    if candidate is None:
        return _blocked("knowledge_candidate_not_found", "知识候选不存在或不属于当前工作区")
    payload = dict(candidate.get("payload") or {})
    evidence = [
        {
            "resource_uri": str(item.get("resource_uri") or ""),
            "locator": str(item.get("locator") or ""),
            "content_hash": str(item.get("content_hash") or ""),
            "evidence_track": str(item.get("evidence_track") or ""),
        }
        for item in payload.get("evidence_bindings") or []
    ]
    request_payload = {"candidate_id": candidate_id}

    def create() -> dict[str, Any]:
        evidence_policy = declared_evidence_policy(payload, default="candidate")
        snapshot = SNAPSHOT_STORE.put(
            workspace_id,
            {
                "candidate_id": candidate_id,
                "candidate_basis_hash": candidate["basis_hash"],
                "candidate_content_hash": candidate["content_hash"],
                "content": str(payload.get("content") or ""),
                "evidence_fingerprint": sha256_json(evidence),
                "evidence_policy": evidence_policy,
                "project_fact_certified": project_fact_may_be_certified(
                    evidence_policy,
                    own_qualification_passed=True,
                    parents=[payload],
                ),
            },
            producer="lvke-knowledge-governance.knowledge_create_snapshot",
            source_ids=[candidate_id],
            basis={
                "candidate_id": candidate_id,
                "candidate_basis_hash": candidate["basis_hash"],
                "candidate_content_hash": candidate["content_hash"],
                "evidence_fingerprint": sha256_json(evidence),
            },
            schema_version="knowledge_snapshot.v1",
        )
        return _envelope(
            True,
            "ok",
            knowledge_snapshot=_object_view(snapshot, "knowledge_snapshot_id"),
            knowledge_snapshot_id=snapshot["object_id"],
            candidate_status="snapshotted",
            resource_uris=[snapshot["resource_uri"]],
        )

    return _idempotent_mutation(
        workspace_id,
        operation="knowledge_create_snapshot",
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
    "create_snapshot",
    "review_candidate",
    "publish_release",
    "list_resources",
    "resolve_resource",
    "read_resource",
]
