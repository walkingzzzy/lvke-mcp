"""lvke-project-planning application 拆分：ProjectContext / InputApplicability 域。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lvke_mcp.runtime.storage import (
    paginate_resource_entries,
    sha256_json,
)
from lvke_mcp.adapters.project_planning_repository import (
    INPUT_APPLICABILITY_STORE,
    PROJECT_CONTEXT_STORE,
)

from .base import (
    _applicability_view,
    _blocked,
    _context_view,
    _downstream_stale,
    _envelope,
    _idempotent_mutation,
)

_CONTEXT_FIELDS = {
    "project_name",
    "industry_code",
    "project_type",
    "region",
    "objective",
    "report_type",
    "transaction_structure",
    "target_type",
    "asset_type",
    "evidence_track",
    "description",
    "tags",
}
_REQUIRED_CONTEXT_FIELDS = (
    "project_name",
    "industry_code",
    "project_type",
    "region",
    "objective",
    "report_type",
    "evidence_track",
)
_INDUSTRY_SKILL_ROUTES = Path(__file__).resolve().parents[3] / "config" / "industry_skill_routes.json"


def resolve_industry_skill(
    workspace_id: str,
    project_context_id: str,
) -> dict[str, Any]:
    """Resolve exactly one primary industry Skill from immutable context."""

    record = PROJECT_CONTEXT_STORE.get(
        workspace_id,
        project_context_id,
    )
    if record is None:
        return _blocked("project_context_not_found", "ProjectContext 不存在或不属于当前工作区")
    try:
        manifest = json.loads(_INDUSTRY_SKILL_ROUTES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blocked("industry_skill_manifest_unavailable", "行业 Skill 路由清单不可用")
    context = dict(record.get("payload") or {})
    industry = str(context.get("industry_code") or "").strip().lower()
    asset_type = str(context.get("asset_type") or "").strip().lower()
    project_type = str(context.get("project_type") or "").strip().lower()
    transaction = str(context.get("transaction_structure") or "").strip().lower()
    matches: list[dict[str, Any]] = []
    for route in manifest.get("routes") or []:
        if not isinstance(route, dict):
            continue
        prefixes = [str(item).lower() for item in route.get("industry_prefixes") or []]
        assets = [str(item).lower() for item in route.get("asset_types") or []]
        projects = [str(item).lower() for item in route.get("project_types") or []]
        transactions = [str(item).lower() for item in route.get("transaction_structures") or []]
        domain_match = any(
            industry == prefix or industry.startswith(prefix + ".") or industry.startswith(prefix + "-")
            for prefix in prefixes
        ) or bool(asset_type and asset_type in assets)
        if not domain_match:
            continue
        if projects and project_type not in projects:
            continue
        if transactions and transaction not in transactions:
            continue
        matches.append(route)
    if not matches:
        return _blocked(
            "industry_skill_route_not_found",
            "没有与 ProjectContext 匹配的行业 Skill；禁止静默使用通用行业默认值",
            next_actions=["补充或修订 industry_code/asset_type 后重新解析"],
        )
    top_priority = max(int(item.get("priority") or 0) for item in matches)
    selected = [item for item in matches if int(item.get("priority") or 0) == top_priority]
    if len(selected) != 1:
        return _blocked(
            "industry_skill_route_ambiguous",
            "ProjectContext 同时命中多个同优先级行业 Skill",
            next_actions=["修订路由 manifest 或 ProjectContext，确保唯一主 Skill"],
        )
    route = selected[0]
    manifest_hash = sha256_json(manifest)
    return _envelope(
        success=True,
        status="ok",
        project_context_id=record["object_id"],
        project_context_basis_hash=record["basis_hash"],
        resolved_context={
            "industry_code": industry,
            "project_type": project_type,
            "transaction_structure": transaction,
            "asset_type": asset_type,
        },
        route_id=str(route.get("route_id") or ""),
        primary_skill=str(route.get("primary_skill") or ""),
        auxiliary_skills=[str(item) for item in route.get("auxiliary_skills") or []],
        route_manifest_version=str(manifest.get("schema_version") or ""),
        route_manifest_hash=manifest_hash,
    )


def _normalized_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        field: context[field]
        for field in sorted(_CONTEXT_FIELDS)
        if field in context
    }


def create_project_context(
    workspace_id: str,
    context: dict[str, Any],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    normalized = _normalized_context(context)

    def mutate() -> dict[str, Any]:
        payload = {
            "object_type": "ProjectContext",
            **normalized,
            "revision_number": 1,
            "parent_object_ids": [],
            "status": "draft",
            "lineage": {},
            "blockers": [],
            "warnings": [],
            "next_actions": ["调用 project_context_validate 生成输入适用性清单"],
        }
        record = PROJECT_CONTEXT_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.project_context_create",
            basis=normalized,
        )
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[record["resource_uri"]],
            next_actions=payload["next_actions"],
            project_context_id=record["object_id"],
            object_id=record["object_id"],
            project_context=_context_view(record),
            object_type="ProjectContext",
            expected_output_types=["ProjectContext"],
            evidence_track=normalized.get("evidence_track", "real"),
            lineage={"parent_object_ids": []},
            basis_hash=record["basis_hash"],
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="project_context_create",
        idempotency_key=idempotency_key,
        request_payload=normalized,
        mutation=mutate,
    )


def _derive_applicability(context: dict[str, Any]) -> dict[str, Any]:
    missing = [
        field for field in _REQUIRED_CONTEXT_FIELDS if not context.get(field)
    ]
    transaction = str(context.get("transaction_structure") or "none")
    project_type = str(context.get("project_type") or "")
    acquisition = project_type == "acquisition" or transaction in {
        "asset_transfer",
        "equity_transfer",
    }
    conditional_required = (
        ["transaction_structure", "target_type", "asset_type"]
        if acquisition
        else []
    )
    missing.extend(
        field for field in conditional_required if not context.get(field)
    )
    required = set((*_REQUIRED_CONTEXT_FIELDS, *conditional_required))
    field_states = {
        field: (
            "required_missing"
            if field in missing
            else "required_present"
            if field in required
            else "optional_present"
            if context.get(field) not in (None, "", [])
            else "optional"
        )
        for field in sorted(_CONTEXT_FIELDS)
    }
    if not acquisition:
        for field in ("target_type", "asset_type"):
            if not context.get(field):
                field_states[field] = "not_applicable"
    return {
        "missing_fields": sorted(set(missing)),
        "field_states": field_states,
        "required_fields": sorted(required),
        "optional_fields": sorted(
            field
            for field, state in field_states.items()
            if state.startswith("optional")
        ),
        "not_applicable_fields": sorted(
            field
            for field, state in field_states.items()
            if state == "not_applicable"
        ),
    }


def validate_project_context(
    workspace_id: str,
    project_context_id: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    def mutate() -> dict[str, Any]:
        record = PROJECT_CONTEXT_STORE.get(
            workspace_id,
            project_context_id,
        )
        if record is None:
            return _blocked(
                "project_context_not_found",
                "ProjectContext 不存在或不属于当前作用域",
            )
        context = _context_view(record)
        applicability = _derive_applicability(context)
        missing = applicability["missing_fields"]
        status = "missing_inputs" if missing else "ok"
        actions = (
            [f"补充 ProjectContext 字段: {field}" for field in missing]
            if missing
            else ["使用该 ProjectContext 创建市场、收入、规模、成本和定员对象"]
        )
        app_payload = {
            "object_type": "InputApplicability",
            "project_context_id": project_context_id,
            "project_context_basis_hash": record["basis_hash"],
            "parent_object_ids": [project_context_id],
            "status": status,
            **applicability,
            "lineage": {"project_context_id": project_context_id},
            "blockers": missing,
            "warnings": [],
            "next_actions": actions,
        }
        app_record = INPUT_APPLICABILITY_STORE.put(
            workspace_id,
            app_payload,
            producer="lvke-project-planning.project_context_validate",
            status=status,
            source_ids=[project_context_id],
            basis={
                "project_context_id": project_context_id,
                "project_context_basis_hash": record["basis_hash"],
                **applicability,
            },
        )
        return _envelope(
            success=not missing,
            status=status,
            code="project_context_missing_inputs" if missing else "",
            message="ProjectContext 缺少必填字段" if missing else "",
            resource_uris=[record["resource_uri"], app_record["resource_uri"]],
            blockers=missing,
            next_actions=actions,
            project_context_id=project_context_id,
            input_applicability_id=app_record["object_id"],
            object_id=app_record["object_id"],
            input_applicability=_applicability_view(app_record),
            object_type="InputApplicability",
            expected_output_types=["InputApplicability"],
            input_object_ids=[project_context_id],
            lineage={"project_context_id": project_context_id},
            evidence_track=context.get("evidence_track", "real"),
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="project_context_validate",
        idempotency_key=idempotency_key,
        request_payload={"project_context_id": project_context_id},
        mutation=mutate,
    )


def revise_project_context(
    workspace_id: str,
    project_context_id: str,
    expected_basis_hash: str,
    patch: dict[str, Any],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    normalized_patch = _normalized_context(patch)

    def mutate() -> dict[str, Any]:
        parent = PROJECT_CONTEXT_STORE.get(
            workspace_id,
            project_context_id,
        )
        if parent is None:
            return _blocked(
                "project_context_not_found",
                "ProjectContext 不存在或不属于当前作用域",
            )
        if parent.get("basis_hash") != expected_basis_hash:
            return _blocked(
                "basis_hash_conflict",
                "ProjectContext 已变化，必须基于最新 basis_hash 重新修订",
                next_actions=["调用 project_context_get 读取最新不可变对象"],
            )
        parent_view = _context_view(parent)
        merged = {
            field: parent_view[field]
            for field in _CONTEXT_FIELDS
            if field in parent_view
        }
        merged.update(normalized_patch)
        payload = {
            "object_type": "ProjectContext",
            **merged,
            "revision_number": int(parent_view.get("revision_number", 1)) + 1,
            "parent_object_ids": [project_context_id],
            "supersedes_project_context_id": project_context_id,
            "status": "draft",
            "lineage": {"project_context_id": project_context_id},
            "blockers": [],
            "warnings": [],
            "next_actions": [
                "重新调用 project_context_validate 并重建 stale 下游对象"
            ],
        }
        stale = _downstream_stale(
            workspace_id,
            project_context_id,
        )
        record = PROJECT_CONTEXT_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.project_context_revise",
            source_ids=[project_context_id],
            basis=merged,
        )
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[parent["resource_uri"], record["resource_uri"]],
            warnings=["旧 ProjectContext 及其下游对象保持只读"] if stale else [],
            next_actions=payload["next_actions"],
            project_context_id=record["object_id"],
            object_id=record["object_id"],
            supersedes_project_context_id=project_context_id,
            project_context=_context_view(record),
            downstream_stale=stale,
            object_type="ProjectContext",
            expected_output_types=["ProjectContext"],
            input_object_ids=[project_context_id],
            lineage={"parent_project_context_id": project_context_id},
            evidence_track=merged.get("evidence_track", "real"),
            basis_hash=record["basis_hash"],
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="project_context_revise",
        idempotency_key=idempotency_key,
        request_payload={
            "project_context_id": project_context_id,
            "expected_basis_hash": expected_basis_hash,
            "patch": normalized_patch,
        },
        mutation=mutate,
    )


def get_project_context(
    workspace_id: str,
    project_context_id: str,
) -> dict[str, Any]:
    record = PROJECT_CONTEXT_STORE.get(
        workspace_id,
        project_context_id,
    )
    if record is None:
        return _blocked(
            "project_context_not_found",
            "ProjectContext 不存在或不属于当前作用域",
        )
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[record["resource_uri"]],
        project_context_id=project_context_id,
        object_id=project_context_id,
        project_context=_context_view(record),
        object_type="ProjectContext",
        expected_output_types=["ProjectContext"],
        evidence_track=(record.get("payload") or {}).get(
            "evidence_track", "real"
        ),
        lineage=(record.get("payload") or {}).get("lineage", {}),
    )


def list_project_contexts(
    workspace_id: str,
    *,
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    entries = [
        {
            "uri": record["resource_uri"],
            "project_context_id": record["object_id"],
            "project_name": (record.get("payload") or {}).get(
                "project_name", ""
            ),
            "revision_number": (record.get("payload") or {}).get(
                "revision_number", 1
            ),
            "basis_hash": record["basis_hash"],
            "created_at": record["created_at"],
        }
        for record in PROJECT_CONTEXT_STORE.list(
            workspace_id,
        )
    ]
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _blocked(
            str(exc),
            "ProjectContext 分页游标无效或列表已变化",
        )
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[entry["uri"] for entry in page["resources"]],
        project_contexts=page["resources"],
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
        snapshot_hash=page["snapshot_hash"],
    )