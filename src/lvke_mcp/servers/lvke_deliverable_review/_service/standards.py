"""标准适用性解析、需求列举、证据绑定与校验。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from lvke_mcp.runtime.storage import require_safe_id, sha256_json
from lvke_mcp.runtime.evidence_qualification import (
    FORMAL_EVIDENCE,
    project_fact_may_be_certified,
)
from lvke_mcp.servers.lvke_deliverable_review.contracts import normalize_project_context

from .base import (
    PACKAGE_CONFIG_DIR,
    STANDARD_APPLICABILITY_STORE,
    STANDARD_EVIDENCE_STORE,
    _blocked,
    _message,
    _ok,
    _write,
)

from .resources import (
    resolve_resource,
)


def _standard_catalog() -> dict[str, Any]:
    path = PACKAGE_CONFIG_DIR / "review_standard_requirements.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("standard_catalog_invalid") from None
    requirements = document.get("requirements") if isinstance(document, dict) else None
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("standard_catalog_invalid")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in requirements:
        if not isinstance(raw, dict):
            raise ValueError("standard_catalog_invalid")
        requirement_id = str(raw.get("requirement_id") or "").strip()
        if not requirement_id or requirement_id in seen:
            raise ValueError("standard_catalog_invalid")
        seen.add(requirement_id)
        normalized.append(deepcopy(raw))
    body = {
        "schema_version": str(document.get("schema_version") or ""),
        "catalog_version": str(document.get("catalog_version") or ""),
        "requirements": normalized,
    }
    return {**body, "content_hash": sha256_json(body)}


def _standard_requirement_applicability(
    requirement: dict[str, Any],
    project_context: dict[str, Any],
    facilities: list[dict[str, Any]],
) -> tuple[bool, str, list[str]]:
    project_types = {str(item) for item in requirement.get("applicable_project_types") or []}
    if project_types and str(project_context.get("project_type") or "") not in project_types:
        return False, "project_type_not_applicable", []
    asset_types = {str(item) for item in requirement.get("applicable_asset_types") or []}
    if asset_types and str(project_context.get("asset_type") or "") not in asset_types:
        return False, "asset_type_not_applicable", []
    facility_types = {str(item) for item in requirement.get("applicable_facility_types") or []}
    if not facility_types:
        return True, "project_context_match", []
    matched = [
        str(item.get("facility_id") or item.get("name") or item.get("facility_type") or "")
        for item in facilities
        if str(item.get("facility_type") or "") in facility_types
    ]
    if matched:
        return True, "facility_inventory_match", matched
    if not facilities:
        # Missing equipment inventory must widen the pending scope rather than
        # silently exclude a potentially mandatory large-facility standard.
        return True, "facility_inventory_pending", []
    return False, "facility_type_not_present", []


def resolve_standards(args: dict[str, Any]) -> dict[str, Any]:
    def execute(workspace_id: str) -> dict[str, Any]:
        raw_context = args.get("project_context") if isinstance(args.get("project_context"), dict) else {}
        target_type = str(raw_context.get("target_type") or "report_revision")
        project_context = normalize_project_context(raw_context, target_type=target_type)
        facilities = [
            {
                "facility_id": str(item.get("facility_id") or "").strip(),
                "name": str(item.get("name") or "").strip(),
                "facility_type": str(item.get("facility_type") or "").strip(),
                "model": str(item.get("model") or "").strip(),
                "quantity": int(item.get("quantity") or 1),
            }
            for item in (args.get("facilities") or [])
            if isinstance(item, dict)
        ]
        catalog = _standard_catalog()
        applicable: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for requirement in catalog["requirements"]:
            selected, reason, matched_facility_ids = _standard_requirement_applicability(
                requirement, project_context, facilities,
            )
            row = {
                **deepcopy(requirement),
                "applicability_reason": reason,
                "matched_facility_ids": matched_facility_ids,
                "evidence_status": "pending_evidence" if selected else "not_applicable",
            }
            (applicable if selected else excluded).append(row)
        payload = {
            "project_context": project_context,
            "facilities": facilities,
            "applicable_requirements": applicable,
            "excluded_requirements": excluded,
            "catalog_version": catalog["catalog_version"],
            "catalog_content_hash": catalog["content_hash"],

        }
        record = STANDARD_APPLICABILITY_STORE.put(
            workspace_id,
            payload,
            producer="lvke-deliverable-review.review_resolve_standards",
            status="ok",
            source_ids=[row["requirement_id"] for row in applicable],
            basis={
                "project_context": project_context,
                "facilities": facilities,
                "catalog_content_hash": catalog["content_hash"],
            },
            schema_version="standard_applicability.v1",
        )
        pending_inventory = any(
            row.get("applicability_reason") == "facility_inventory_pending"
            for row in applicable
        )
        return _ok(
            standard_applicability_id=record["object_id"],
            project_context=project_context,
            applicable_requirements=applicable,
            excluded_requirements=excluded,
            applicable_requirement_count=len(applicable),
            excluded_requirement_count=len(excluded),
            standards_content_hash=record["content_hash"],
            catalog_content_hash=catalog["content_hash"],
            compliance_conclusion="not_determined",
            resource_uris=[record["resource_uri"]],
            warnings=["设备设施清单缺失，涉及设备类型的标准仅能判为待确认"] if pending_inventory else [],
            blockers=[],
            next_actions=["调用 review_list_requirements 查看证明材料需求并绑定不可变证据"],
        )
    scoped_args = dict(args)
    if not str(scoped_args.get("idempotency_key") or "").strip():
        basis = {
            "workspace_id": scoped_args.get("workspace_id"),
            "project_context": scoped_args.get("project_context"),
            "facilities": scoped_args.get("facilities"),
        }
        scoped_args["idempotency_key"] = (
            "standards-" + sha256_json(basis).removeprefix("sha256:")[:40]
        )
    return _write("review_resolve_standards", scoped_args, execute)


def _standard_applicability_record(
    workspace_id: str,
    applicability_id: str,
) -> dict[str, Any] | None:
    try:
        record = STANDARD_APPLICABILITY_STORE.get(
            workspace_id, applicability_id, 
        )
    except ValueError:
        return None
    if not record or record.get("content_hash") != sha256_json(record.get("payload") or {}):
        return None
    return record


def _standard_evidence_rows(
    workspace_id: str,
    applicability_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in STANDARD_EVIDENCE_STORE.list(workspace_id):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if str(payload.get("standard_applicability_id") or "") == applicability_id:
            rows.append({**deepcopy(payload), "standard_evidence_id": record.get("object_id")})
    return rows


def _requirement_evidence_status(evidence_track: str, attached: bool) -> str:
    if not attached:
        return "pending_evidence"
    if evidence_track == "technical_fixture":
        return "satisfied_technical_fixture"
    if evidence_track == "source_reconstructed":
        return "satisfied_source_reconstructed_process_acceptance"
    if evidence_track == "real":
        return "evidence_attached_pending_review"
    if evidence_track == "sim_a_formal":
        return "satisfied_sim_a_formal"
    return "unable_to_determine"


def list_standard_requirements(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args.get("workspace_id") or "")
    applicability_id = str(args.get("standard_applicability_id") or "")
    try:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        applicability_id = require_safe_id(applicability_id, "standard_applicability_id")
    except ValueError as exc:
        return _blocked(str(exc), _message(str(exc)))
    record = _standard_applicability_record(workspace_id, applicability_id)
    if record is None:
        return _blocked("standard_applicability_not_found", _message("standard_applicability_not_found"))
    payload = record.get("payload") or {}
    evidence = _standard_evidence_rows(workspace_id, applicability_id)
    by_requirement: dict[str, list[dict[str, Any]]] = {}
    for row in evidence:
        by_requirement.setdefault(str(row.get("requirement_id") or ""), []).append(row)
    evidence_track = str((payload.get("project_context") or {}).get("evidence_track") or "real")
    requirements = []
    pending = 0
    for row in payload.get("applicable_requirements") or []:
        requirement_id = str(row.get("requirement_id") or "")
        attachments = by_requirement.get(requirement_id, [])
        evidence_status = _requirement_evidence_status(evidence_track, bool(attachments))
        if evidence_status == "pending_evidence":
            pending += 1
        requirements.append({
            **deepcopy(row),
            "evidence_attachments": attachments,
            "evidence_status": evidence_status,
        })
    return _ok(
        standard_applicability_id=applicability_id,
        project_context=payload.get("project_context") or {},
        requirements=requirements,
        excluded_requirements=deepcopy(payload.get("excluded_requirements") or []),
        requirement_count=len(requirements),
        resource_uris=[record["resource_uri"], *[str(row.get("resource_uri") or "") for row in evidence if row.get("resource_uri")]],
        warnings=[], blockers=[],
        next_actions=(
            ["为待补证要求调用 review_attach_requirement_evidence"]
            if pending
            else ["调用 review_validate_standards 汇总证据状态"]
        ),
    )


def _resolve_standard_evidence_resource(
    uri: str,
    workspace_id: str,
) -> tuple[dict[str, Any], str] | None:
    if uri.startswith(f"lvke://data-acquisition/workspaces/{workspace_id}/"):
        from lvke_mcp.adapters.data_acquisition_repository import resolve_resource

        record = resolve_resource(uri, workspace_id)
        if not isinstance(record, dict):
            return None
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        # A browser or unsigned external snapshot remains a candidate source;
        # its URI and hash do not turn it into formal standard evidence.
        return record, "real" if payload.get("formal_use_allowed") is True else "candidate"
    if uri.startswith(f"lvke://data-analysis/workspaces/{workspace_id}/"):
        from lvke_mcp.adapters.data_analysis_repository import resolve_resource

        record = resolve_resource(uri, workspace_id)
        if not isinstance(record, dict):
            return None
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        evidence_track = str(payload.get("evidence_track") or "real")
        return record, evidence_track
    if uri.startswith(f"lvke://source-files/workspaces/{workspace_id}/"):
        from lvke_mcp.adapters import source_files_repository as source_files

        file_id = uri.rstrip("/").rsplit("/", 1)[-1]
        state = source_files._load_state(workspace_id)  # noqa: SLF001
        stored = (state.get("files") or {}).get(file_id)
        if not isinstance(stored, dict):
            return None
        digest = str(stored.get("sha256") or "").lower()
        if digest and not digest.startswith("sha256:"):
            digest = f"sha256:{digest}"
        policy = str(stored.get("evidence_policy") or "candidate")
        track = policy if policy in {
            "sim_a_formal",
            "source_reconstructed",
            "technical_fixture",
            "controlled_assumption",
            "real",
        } else "candidate"
        return (
            {
                "object_id": file_id,
                "content_hash": digest,
                "payload": {
                    **stored,
                    "content_hash": digest,
                    "evidence_track": track,
                    "evidence_policy": policy,
                    "formal_use_allowed": bool(stored.get("project_fact_certified")),
                    "project_fact_certified": bool(stored.get("project_fact_certified")),
                },
            },
            track,
        )
    return None


def attach_requirement_evidence(args: dict[str, Any]) -> dict[str, Any]:
    def execute(workspace_id: str) -> dict[str, Any]:
        applicability_id = require_safe_id(
            str(args.get("standard_applicability_id") or ""),
            "standard_applicability_id",
        )
        requirement_id = str(args.get("requirement_id") or "").strip()
        record = _standard_applicability_record(workspace_id, applicability_id)
        if record is None:
            return _blocked("standard_applicability_not_found", _message("standard_applicability_not_found"))
        payload = record.get("payload") or {}
        requirement = next((
            row for row in payload.get("applicable_requirements") or []
            if str(row.get("requirement_id") or "") == requirement_id
        ), None)
        if requirement is None:
            return _blocked("standard_requirement_not_found", _message("standard_requirement_not_found"))
        resource_uri = str(args.get("resource_uri") or "").strip()
        resolved = _resolve_standard_evidence_resource(resource_uri, workspace_id)
        if resolved is None:
            return _blocked("standard_evidence_resource_invalid", _message("standard_evidence_resource_invalid"))
        source_record, source_track = resolved
        supplied_hash = str(args.get("content_hash") or "").lower()
        if supplied_hash and not supplied_hash.startswith("sha256:"):
            supplied_hash = f"sha256:{supplied_hash}"
        actual_hash = str(source_record.get("content_hash") or "").lower()
        if actual_hash != supplied_hash:
            return _blocked("standard_evidence_hash_mismatch", _message("standard_evidence_hash_mismatch"))
        requested_track = str(args.get("evidence_track") or "")
        applicability_track = str((payload.get("project_context") or {}).get("evidence_track") or "real")
        if requested_track != applicability_track or source_track != requested_track:
            return _blocked("standard_evidence_track_mismatch", _message("standard_evidence_track_mismatch"))
        evidence_payload = {
            "standard_applicability_id": applicability_id,
            "requirement_id": requirement_id,
            "resource_uri": resource_uri,
            "locator": str(args.get("locator") or "").strip(),
            "content_hash": actual_hash,
            "evidence_track": requested_track,

        }
        evidence_record = STANDARD_EVIDENCE_STORE.put(
            workspace_id,
            evidence_payload,
            producer="lvke-deliverable-review.review_attach_requirement_evidence",
            source_ids=[applicability_id, requirement_id, str(source_record.get("object_id") or "")],
            basis=evidence_payload,
            schema_version="standard_requirement_evidence.v1",
        )
        return _ok(
            standard_applicability_id=applicability_id,
            standard_evidence_id=evidence_record["object_id"],
            requirement_id=requirement_id,
            evidence_track=requested_track,
            evidence_status={
                "technical_fixture": "satisfied_technical_fixture",
                "source_reconstructed": "satisfied_source_reconstructed_process_acceptance",
                "real": "evidence_attached_pending_review",
                "controlled_assumption": "unable_to_determine",
                "sim_a_formal": "satisfied_sim_a_formal",
            }.get(requested_track, "unable_to_determine"),
            formal_evidence_candidate=requested_track in {"real", "source_reconstructed", "sim_a_formal"},
            project_fact_certified=project_fact_may_be_certified(
                FORMAL_EVIDENCE if requested_track == "real" else requested_track,
                own_qualification_passed=(
                    requested_track in {"real", "sim_a_formal"}
                    and (
                        (source_record.get("payload") or {}).get("formal_use_allowed") is True
                        or (source_record.get("payload") or {}).get("project_fact_certified") is True
                    )
                ),
                parents=[source_record.get("payload") or {}],
            ),
            compliance_conclusion="not_determined",
            resource_uris=[evidence_record["resource_uri"], resource_uri],
            warnings=[], blockers=[], next_actions=["调用 review_validate_standards 汇总证据状态"],
        )
    return _write("review_attach_requirement_evidence", args, execute)


def validate_standards(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args.get("workspace_id") or "")
    applicability_id = str(args.get("standard_applicability_id") or "")
    try:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        applicability_id = require_safe_id(applicability_id, "standard_applicability_id")
    except ValueError as exc:
        return _blocked(str(exc), _message(str(exc)))
    record = _standard_applicability_record(workspace_id, applicability_id)
    if record is None:
        return _blocked("standard_applicability_not_found", _message("standard_applicability_not_found"))
    payload = record.get("payload") or {}
    evidence_track = str((payload.get("project_context") or {}).get("evidence_track") or "real")
    attachments = _standard_evidence_rows(workspace_id, applicability_id)
    attached_ids = {str(row.get("requirement_id") or "") for row in attachments}
    requirements: list[dict[str, Any]] = []
    for row in payload.get("applicable_requirements") or []:
        requirement_id = str(row.get("requirement_id") or "")
        if requirement_id not in attached_ids:
            evidence_status = "pending_evidence"
        else:
            evidence_status = _requirement_evidence_status(evidence_track, True)
        requirements.append({**deepcopy(row), "evidence_status": evidence_status})
    status_counts = {
        status: sum(1 for row in requirements if row.get("evidence_status") == status)
        for status in (
            "satisfied_technical_fixture",
            "satisfied_source_reconstructed_process_acceptance",
            "satisfied_sim_a_formal",
            "evidence_attached_pending_review",
            "pending_evidence",
            "unable_to_determine",
        )
    }
    unresolved = status_counts["pending_evidence"] + status_counts["unable_to_determine"]
    technical_complete = bool(
        evidence_track == "technical_fixture" and requirements and not unresolved
    )
    result_status = "ok" if technical_complete else "partial"
    return _ok(
        status=result_status,
        standard_applicability_id=applicability_id,
        evidence_track=evidence_track,
        requirements=requirements,
        excluded_requirements=deepcopy(payload.get("excluded_requirements") or []),
        status_counts=status_counts,
        technical_validation_complete=technical_complete,
        formal_compliance_determined=False,
        compliance_conclusion="not_determined",
        formal_evidence_claim_count=(
            status_counts["evidence_attached_pending_review"]
            if evidence_track == "real"
            else status_counts.get("satisfied_sim_a_formal", 0)
            if evidence_track == "sim_a_formal"
            else 0
        ),
        source_reconstructed_claim_count=(
            status_counts.get("satisfied_source_reconstructed_process_acceptance", 0)
            if evidence_track == "source_reconstructed" else 0
        ),
        technical_fixture_claim_count=(
            status_counts["satisfied_technical_fixture"]
            if evidence_track == "technical_fixture" else 0
        ),
        external_data_gap_count=(
            status_counts["pending_evidence"] + status_counts["unable_to_determine"]
        ),
        local_implementation_issue_count=0,
        resource_uris=[record["resource_uri"], *[
            str(row.get("resource_uri") or "") for row in attachments if row.get("resource_uri")
        ]],
        warnings=["标准证据状态仅表示当前技术验证结果"],
        blockers=[] if technical_complete else ["standard_evidence_validation_incomplete"],
        next_actions=(
            ["技术夹具链已完成；结果保留当前 evidence track 标记"]
            if technical_complete else ["补充真实不可变证据并完成质量核验"]
        ),
    )

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "FORMAL_EVIDENCE",
    "PACKAGE_CONFIG_DIR",
    "STANDARD_APPLICABILITY_STORE",
    "STANDARD_EVIDENCE_STORE",
    "_blocked",
    "_message",
    "_ok",
    "_requirement_evidence_status",
    "_resolve_standard_evidence_resource",
    "_standard_applicability_record",
    "_standard_catalog",
    "_standard_evidence_rows",
    "_standard_requirement_applicability",
    "_write",
    "attach_requirement_evidence",
    "deepcopy",
    "json",
    "list_standard_requirements",
    "normalize_project_context",
    "project_fact_may_be_certified",
    "require_safe_id",
    "resolve_resource",
    "resolve_standards",
    "sha256_json",
    "validate_standards",
]
