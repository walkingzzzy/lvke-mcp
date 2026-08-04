"""Immutable ProjectContext and InputApplicability lifecycle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable

from filelock import FileLock
from lvke_mcp.runtime.workspace import workspace_root

from lvke_mcp.runtime.storage import (
    JSONArtifactStore,
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
)

PROJECT_CONTEXT_STORE = JSONArtifactStore(
    "project-planning", "project_contexts", "pctx", "project-contexts"
)
INPUT_APPLICABILITY_STORE = JSONArtifactStore(
    "project-planning", "input_applicability", "iapp", "input-applicability"
)
MARKET_CASE_STORE = JSONArtifactStore(
    "project-planning", "market_cases", "mkt", "market-cases"
)
REVENUE_DRIVER_STORE = JSONArtifactStore(
    "project-planning", "revenue_drivers", "revdrv", "revenue-drivers"
)
BUILD_SCALE_STORE = JSONArtifactStore(
    "project-planning", "build_scale_cases", "scale", "build-scale-cases"
)
COST_DRIVER_STORE = JSONArtifactStore(
    "project-planning", "cost_drivers", "costdrv", "cost-drivers"
)
LABOR_PLAN_STORE = JSONArtifactStore(
    "project-planning", "labor_plans", "labor", "labor-plans"
)
OPTION_COMPARISON_STORE = JSONArtifactStore(
    "project-planning", "option_comparisons", "optcmp", "option-comparisons"
)
POLICY_BASIS_STORE = JSONArtifactStore(
    "project-planning", "policy_bases", "policy", "policy-bases"
)
IDEMPOTENCY_STORE = JSONArtifactStore(
    "project-planning", "idempotency", "idem", "idempotency"
)

_RESOURCE_STORES = (
    (PROJECT_CONTEXT_STORE, "ProjectContext"),
    (INPUT_APPLICABILITY_STORE, "InputApplicability"),
    (MARKET_CASE_STORE, "MarketSizingCase"),
    (REVENUE_DRIVER_STORE, "RevenueDriverSet"),
    (BUILD_SCALE_STORE, "BuildScaleCase"),
    (COST_DRIVER_STORE, "CostDriverSet"),
    (LABOR_PLAN_STORE, "LaborPlan"),
    (OPTION_COMPARISON_STORE, "OptionComparison"),
    (POLICY_BASIS_STORE, "PolicyBasis"),
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
_INDUSTRY_SKILL_ROUTES = Path(__file__).resolve().parents[2] / "config" / "industry_skill_routes.json"


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


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _market_candidate(raw: dict[str, Any], index: int) -> dict[str, Any]:
    candidate = dict(raw)
    material = {
        key: candidate.get(key)
        for key in (
            "method",
            "market_size",
            "unit",
            "period",
            "region",
            "target_share",
            "target_volume",
            "formula_inputs",
            "evidence_bindings",
        )
    }
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    if not candidate_id:
        candidate_id = f"mcand_{sha256_json(material).removeprefix('sha256:')[:20]}"
    market_size = _decimal(candidate.get("market_size"))
    share = _decimal(candidate.get("target_share"))
    computed = None
    if market_size is not None and share is not None:
        computed = (market_size * share).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return {
        **candidate,
        "candidate_id": candidate_id,
        "candidate_order": index + 1,
        "computed_target_volume": float(computed) if computed is not None else None,
        "calculation_trace": (
            "market_size * target_share"
            if computed is not None
            else "insufficient_inputs"
        ),
    }


def _validate_market_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    candidates = [
        item for item in payload.get("candidates") or [] if isinstance(item, dict)
    ]
    if len(candidates) < 2:
        errors.append(
            {
                "path": "/candidates",
                "code": "market_paths_insufficient",
                "message": "市场规模至少需要两种独立测算路径",
            }
        )
    methods = {str(item.get("method") or "") for item in candidates}
    if len(methods - {""}) < 2:
        errors.append(
            {
                "path": "/candidates/*/method",
                "code": "market_methods_not_independent",
                "message": "市场案例必须包含至少两种不同方法",
            }
        )
    for index, item in enumerate(candidates):
        prefix = f"/candidates/{index}"
        market_size = _decimal(item.get("market_size"))
        share = _decimal(item.get("target_share"))
        target = _decimal(item.get("target_volume"))
        for field in ("unit", "period", "region"):
            if not str(item.get(field) or "").strip():
                errors.append(
                    {
                        "path": f"{prefix}/{field}",
                        "code": "market_dimension_required",
                        "message": f"{field} 必填",
                    }
                )
        if market_size is None or market_size <= 0:
            errors.append(
                {
                    "path": f"{prefix}/market_size",
                    "code": "market_size_invalid",
                    "message": "market_size 必须大于 0",
                }
            )
        if share is None or share < 0 or share > 1:
            errors.append(
                {
                    "path": f"{prefix}/target_share",
                    "code": "market_share_invalid",
                    "message": "target_share 必须在 0 到 1 之间",
                }
            )
        if market_size is not None and share is not None:
            computed = (market_size * share).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )
            if target is not None and abs(target - computed) > Decimal("0.000001"):
                errors.append(
                    {
                        "path": f"{prefix}/target_volume",
                        "code": "market_target_volume_inconsistent",
                        "message": "target_volume 与 market_size × target_share 不一致",
                    }
                )
        bindings = [
            binding
            for binding in item.get("evidence_bindings") or []
            if isinstance(binding, dict)
        ]
        if not bindings:
            errors.append(
                {
                    "path": f"{prefix}/evidence_bindings",
                    "code": "market_evidence_required",
                    "message": "每条市场路径必须绑定 evidence pack 中的 locator",
                }
            )
        for binding_index, binding in enumerate(bindings):
            if str(binding.get("source_type") or "") == "search_summary":
                errors.append(
                    {
                        "path": f"{prefix}/evidence_bindings/{binding_index}/source_type",
                        "code": "search_summary_not_evidence",
                        "message": "搜索摘要不能作为市场规模证据",
                    }
                )
            for field in ("source_id", "content_hash", "locator"):
                if not binding.get(field):
                    errors.append(
                        {
                            "path": f"{prefix}/evidence_bindings/{binding_index}/{field}",
                            "code": "market_evidence_binding_incomplete",
                            "message": f"证据绑定缺少 {field}",
                        }
                    )
    return errors


def _resolve_market_evidence_track(
    evidence_payload: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Resolve bindings against one immutable EvidencePack and derive its track."""

    pack_track = str(evidence_payload.get("evidence_track") or "real")
    sources = {
        str(item.get("source_id") or ""): item
        for item in evidence_payload.get("sources") or []
        if isinstance(item, dict) and item.get("source_id")
    }
    candidate_locators: dict[str, set[str]] = {}
    for item in evidence_payload.get("fact_candidates") or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        locator = item.get("locator")
        if source_id and locator:
            candidate_locators.setdefault(source_id, set()).add(
                json.dumps(locator, ensure_ascii=False, sort_keys=True)
                if isinstance(locator, (dict, list))
                else str(locator)
            )

    errors: list[dict[str, Any]] = []
    resolved_tracks: list[str] = []
    for candidate_index, candidate in enumerate(candidates):
        for binding_index, binding in enumerate(candidate.get("evidence_bindings") or []):
            if not isinstance(binding, dict):
                continue
            # The ordinary payload validator owns this explicit rejection and
            # keeps its stable search_summary_not_evidence diagnostic.
            if str(binding.get("source_type") or "") == "search_summary":
                continue
            prefix = f"/candidates/{candidate_index}/evidence_bindings/{binding_index}"
            source_id = str(binding.get("source_id") or "")
            source = sources.get(source_id)
            if source is None:
                errors.append({
                    "path": f"{prefix}/source_id",
                    "code": "evidence_binding_not_in_pack",
                    "message": "证据绑定来源不属于指定 EvidencePack",
                })
                continue
            supplied_hash = str(binding.get("content_hash") or "").removeprefix("sha256:")
            source_hash = str(source.get("content_hash") or "").removeprefix("sha256:")
            if not supplied_hash or supplied_hash.lower() != source_hash.lower():
                errors.append({
                    "path": f"{prefix}/content_hash",
                    "code": "evidence_binding_hash_mismatch",
                    "message": "证据绑定 hash 与 EvidencePack 固化来源不一致",
                })
            locator = str(binding.get("locator") or "")
            source_locators = {
                json.dumps(item, ensure_ascii=False, sort_keys=True)
                if isinstance(item, (dict, list))
                else str(item)
                for item in (source.get("locators") or [])
            }
            known_locators = source_locators | candidate_locators.get(source_id, set())
            if not locator or locator not in known_locators:
                errors.append({
                    "path": f"{prefix}/locator",
                    "code": "evidence_binding_locator_mismatch",
                    "message": "证据 locator 未在 EvidencePack 固化来源中解析到",
                })
            binding_track = str(binding.get("evidence_track") or "")
            if not binding_track:
                errors.append({
                    "path": f"{prefix}/evidence_track",
                    "code": "evidence_track_required",
                    "message": "每条证据绑定必须声明 evidence_track",
                })
                continue
            if binding_track != pack_track:
                errors.append({
                    "path": f"{prefix}/evidence_track",
                    "code": "evidence_track_mismatch",
                    "message": "证据绑定轨道与 EvidencePack 资格不一致",
                })
            resolved_tracks.append(binding_track)

    if "controlled_assumption" in resolved_tracks:
        resolved = "controlled_assumption"
    elif "technical_fixture" in resolved_tracks:
        resolved = "technical_fixture"
    else:
        resolved = pack_track
    return resolved, errors


def prepare_market_case(
    workspace_id: str,
    project_context_id: str,
    evidence_pack_id: str,
    candidates: list[dict[str, Any]],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    normalized_candidates = [
        _market_candidate(item, index)
        for index, item in enumerate(candidates)
        if isinstance(item, dict)
    ]

    def mutate() -> dict[str, Any]:
        context = PROJECT_CONTEXT_STORE.get(
            workspace_id, project_context_id
        )
        if context is None:
            return _blocked(
                "project_context_not_found",
                "ProjectContext 不存在或不属于当前作用域",
            )
        from lvke_mcp.servers.lvke_data_analysis.service import EVIDENCE_STORE

        evidence = EVIDENCE_STORE.get(
            workspace_id, evidence_pack_id
        )
        if evidence is None:
            return _blocked(
                "evidence_pack_not_found",
                "EvidencePack 不存在或不属于当前作用域",
            )
        evidence_payload = evidence.get("payload") or {}
        resolved_track, binding_errors = _resolve_market_evidence_track(
            evidence_payload,
            normalized_candidates,
        )
        if binding_errors:
            return _envelope(
                success=False,
                status="blocked",
                code="evidence_track_mismatch",
                message="市场测算证据绑定与 EvidencePack 不一致",
                blockers=sorted({str(item["code"]) for item in binding_errors}),
                field_errors={item["path"]: item for item in binding_errors},
                next_actions=["使用 EvidencePack 中相同 source、hash、locator 和 track 重建候选"],
            )
        payload = {
            "object_type": "MarketSizingCase",
            "project_context_id": project_context_id,
            "project_context_basis_hash": context["basis_hash"],
            "evidence_pack_id": evidence_pack_id,
            "evidence_pack_basis_hash": evidence["basis_hash"],
            "evidence_track": resolved_track,
            "candidates": normalized_candidates,
            "status": "candidate",
            "revision_number": 1,
            "parent_object_ids": [project_context_id, evidence_pack_id],
            "selection": None,
            "warnings": [],
            "blockers": [],
            "next_actions": [
                "调用 planning_compare_market_cases 比较路径偏差",
                "调用 planning_validate_market_case 检查证据与口径",
                "由 Codex 明确选择后调用 planning_confirm_market_case",
            ],
        }
        record = MARKET_CASE_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_prepare_market_case",
            status="candidate",
            source_ids=[project_context_id, evidence_pack_id],
            basis={
                "project_context_basis_hash": context["basis_hash"],
                "evidence_pack_basis_hash": evidence["basis_hash"],
                "candidates": normalized_candidates,
            },
        )
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[record["resource_uri"]],
            next_actions=payload["next_actions"],
            market_case_id=record["object_id"],
            object_id=record["object_id"],
            market_case=_market_view(record),
            object_type="MarketSizingCase",
            evidence_track=payload["evidence_track"],
            lineage={
                "project_context_id": project_context_id,
                "evidence_pack_id": evidence_pack_id,
            },
            basis_hash=record["basis_hash"],
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_prepare_market_case",
        idempotency_key=idempotency_key,
        request_payload={
            "project_context_id": project_context_id,
            "evidence_pack_id": evidence_pack_id,
            "candidates": normalized_candidates,
        },
        mutation=mutate,
    )


def compare_market_cases(
    workspace_id: str,
    market_case_id: str,
) -> dict[str, Any]:
    record = MARKET_CASE_STORE.get(
        workspace_id, market_case_id
    )
    if record is None:
        return _blocked("market_case_not_found", "MarketSizingCase 不存在")
    candidates = list((record.get("payload") or {}).get("candidates") or [])
    comparisons: list[dict[str, Any]] = []
    for left_index, left in enumerate(candidates):
        for right in candidates[left_index + 1 :]:
            comparable = all(
                str(left.get(field) or "") == str(right.get(field) or "")
                for field in ("unit", "period", "region")
            )
            left_value = _decimal(left.get("computed_target_volume"))
            right_value = _decimal(right.get("computed_target_volume"))
            deviation = None
            if comparable and left_value is not None and right_value is not None:
                denominator = max(abs(left_value), abs(right_value))
                deviation = (
                    float((abs(left_value - right_value) / denominator * 100).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    ))
                    if denominator
                    else 0.0
                )
            comparisons.append(
                {
                    "left_candidate_id": left.get("candidate_id"),
                    "right_candidate_id": right.get("candidate_id"),
                    "comparable": comparable,
                    "deviation_pct": deviation,
                    "period_or_scope_mismatch": not comparable,
                }
            )
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[record["resource_uri"]],
        market_case_id=market_case_id,
        comparisons=comparisons,
        aggregation="none",
        selection_required=True,
        warnings=["多路径结果并列展示，服务不会取平均值"],
    )


def validate_market_case(
    workspace_id: str,
    market_case_id: str,
) -> dict[str, Any]:
    record = MARKET_CASE_STORE.get(
        workspace_id, market_case_id
    )
    if record is None:
        return _blocked("market_case_not_found", "MarketSizingCase 不存在")
    payload = record.get("payload") or {}
    errors = _validate_market_payload(payload)
    if errors:
        return _envelope(
            success=False,
            status="missing_inputs",
            code="market_case_invalid",
            message="MarketSizingCase 缺少可审计输入或口径不一致",
            resource_uris=[record["resource_uri"]],
            blockers=sorted({str(item["code"]) for item in errors}),
            next_actions=["按 field_errors 补充市场路径、证据 locator 或修正计算口径"],
            field_errors={item["path"]: item for item in errors},
            market_case_id=market_case_id,
            valid=False,
        )
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[record["resource_uri"]],
        market_case_id=market_case_id,
        valid=True,
        next_actions=["由 Codex 选择候选并提交选择理由，不得自动平均"],
    )


def confirm_market_case(
    workspace_id: str,
    market_case_id: str,
    selected_candidate_id: str,
    selection_reason: str,
    rejected_candidate_ids: list[str],
    *,
    idempotency_key: str,
    supersedes_market_case_id: str = "",
    expected_basis_hash: str = "",
) -> dict[str, Any]:
    request_payload = {
        "market_case_id": market_case_id,
        "selected_candidate_id": selected_candidate_id,
        "selection_reason": selection_reason,
        "rejected_candidate_ids": sorted(set(rejected_candidate_ids)),
        "supersedes_market_case_id": supersedes_market_case_id,
        "expected_basis_hash": expected_basis_hash,
    }

    def mutate() -> dict[str, Any]:
        candidate_record = MARKET_CASE_STORE.get(
            workspace_id, market_case_id
        )
        if candidate_record is None:
            return _blocked("market_case_not_found", "候选 MarketSizingCase 不存在")
        payload = candidate_record.get("payload") or {}
        if payload.get("status") != "candidate":
            return _blocked("market_case_not_candidate", "只能确认 candidate 状态市场案例")
        errors = _validate_market_payload(payload)
        if errors:
            return _blocked(
                "market_case_invalid",
                "市场案例校验未通过，不能确认",
                next_actions=["调用 planning_validate_market_case 查看 field_errors"],
            )
        by_id = {
            str(item.get("candidate_id") or ""): item
            for item in payload.get("candidates") or []
            if isinstance(item, dict)
        }
        if selected_candidate_id not in by_id:
            return _blocked("market_candidate_not_found", "选定市场候选不存在")
        expected_rejected = set(by_id) - {selected_candidate_id}
        if set(rejected_candidate_ids) != expected_rejected:
            return _blocked(
                "market_rejected_candidates_incomplete",
                "必须显式列出全部未采用候选，避免隐式合并",
            )
        parent_ids = [market_case_id]
        stale: list[dict[str, Any]] = []
        revision_number = 2
        if supersedes_market_case_id:
            superseded = MARKET_CASE_STORE.get(
                workspace_id, supersedes_market_case_id
            )
            if superseded is None or (superseded.get("payload") or {}).get("status") != "confirmed":
                return _blocked("superseded_market_case_not_found", "被替代的已确认市场案例不存在")
            if superseded.get("basis_hash") != expected_basis_hash:
                return _blocked("basis_hash_conflict", "市场案例 basis 已变化")
            parent_ids.append(supersedes_market_case_id)
            revision_number = int((superseded.get("payload") or {}).get("revision_number") or 1) + 1
            stale = _downstream_stale(
                workspace_id,
                supersedes_market_case_id,
                reason="market_case_superseded",
            )
        confirmed_payload = {
            **payload,
            "status": "confirmed",
            "revision_number": revision_number,
            "parent_object_ids": parent_ids,
            "selection": {
                "selected_candidate_id": selected_candidate_id,
                "selected_candidate": by_id[selected_candidate_id],
                "selection_reason": selection_reason,
                "rejected_candidate_ids": sorted(rejected_candidate_ids),
                "aggregation": "none",
            },
            "supersedes_market_case_id": supersedes_market_case_id or None,
            "next_actions": ["使用已确认 MarketSizingCase 创建建设规模和收入驱动对象"],
        }
        record = MARKET_CASE_STORE.put(
            workspace_id,
            confirmed_payload,
            producer="lvke-project-planning.planning_confirm_market_case",
            status="confirmed",
            source_ids=parent_ids,
            basis={
                "candidate_basis_hash": candidate_record["basis_hash"],
                "selection": confirmed_payload["selection"],
                "superseded_basis_hash": expected_basis_hash or None,
            },
        )
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[record["resource_uri"]],
            next_actions=confirmed_payload["next_actions"],
            market_case_id=record["object_id"],
            object_id=record["object_id"],
            market_case=_market_view(record),
            selected_candidate=by_id[selected_candidate_id],
            downstream_stale=stale,
            basis_hash=record["basis_hash"],
            lineage={
                "candidate_market_case_id": market_case_id,
                "project_context_id": payload.get("project_context_id"),
                "evidence_pack_id": payload.get("evidence_pack_id"),
            },
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_confirm_market_case",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )


def get_market_case(
    workspace_id: str,
    market_case_id: str,
) -> dict[str, Any]:
    record = MARKET_CASE_STORE.get(
        workspace_id, market_case_id
    )
    if record is None:
        return _blocked("market_case_not_found", "MarketSizingCase 不存在")
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[record["resource_uri"]],
        market_case_id=market_case_id,
        object_id=market_case_id,
        market_case=_market_view(record),
        lineage={
            "project_context_id": (record.get("payload") or {}).get("project_context_id"),
            "evidence_pack_id": (record.get("payload") or {}).get("evidence_pack_id"),
        },
    )


def _confirmed_market_basis(
    workspace_id: str,
    project_context_id: str,
    market_case_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    context = PROJECT_CONTEXT_STORE.get(
        workspace_id, project_context_id
    )
    if context is None:
        return None, None, _blocked("project_context_not_found", "ProjectContext 不存在")
    market = MARKET_CASE_STORE.get(workspace_id, market_case_id)
    market_payload = (market or {}).get("payload") or {}
    if market is None or market_payload.get("status") != "confirmed":
        return context, None, _blocked(
            "confirmed_market_case_required", "必须绑定当前作用域内已确认的 MarketSizingCase"
        )
    if market_payload.get("project_context_id") != project_context_id:
        return context, market, _blocked(
            "planning_basis_mismatch", "MarketSizingCase 与 ProjectContext 不属于同一 basis"
        )
    return context, market, None


def create_revenue_driver_set(
    workspace_id: str,
    project_context_id: str,
    market_case_id: str,
    revenue_spec: dict[str, Any],
    op_years: int,
    *,
    mode: str = "estimate_preview",
    flat_evidence_binding: dict[str, Any] | None = None,
    parent_candidate_id: str = "",
    selection: dict[str, Any] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    normalized_spec = dict(revenue_spec or {})
    if str(normalized_spec.get("model") or "") == "tourism":
        from lvke_mcp.domains.finance.revenue_models import normalize_tourism_revenue

        normalized_spec, normalization_errors = normalize_tourism_revenue(normalized_spec)
    else:
        normalization_errors = []
    request_payload = {
        "project_context_id": project_context_id,
        "market_case_id": market_case_id,
        "revenue_spec": normalized_spec,
        "op_years": op_years,
        "mode": mode,
        "flat_evidence_binding": flat_evidence_binding,
        "parent_candidate_id": parent_candidate_id,
        "selection": selection,
    }

    def mutate() -> dict[str, Any]:
        context, market, error = _confirmed_market_basis(
            workspace_id,
            project_context_id,
            market_case_id,
        )
        if error:
            return error
        assert context is not None and market is not None
        if normalization_errors:
            return _envelope(
                success=False,
                status="blocked",
                code="revenue_component_conflict",
                message="文旅收入产品树与兼容字段不一致",
                blockers=["revenue_component_conflict"],
                field_errors={item["path"]: item for item in normalization_errors},
            )
        evidence_track = str((context.get("payload") or {}).get("evidence_track") or "real")
        if mode not in {"estimate_preview", "review_candidate"}:
            return _blocked("revenue_mode_invalid", "mode 必须为 estimate_preview 或 review_candidate")
        if not isinstance(op_years, int) or isinstance(op_years, bool) or not 1 <= op_years <= 100:
            return _blocked("revenue_op_years_invalid", "op_years 必须为 1 到 100 的整数")
        model = str(normalized_spec.get("model") or "")
        required_by_model = {
            "product_sales": ("products",),
            "property_sales": ("saleable_area", "price_per_sqm"),
            "tourism": ("annual_visitors", "visitor_unit"),
            "gov_payment": ("annual_gov_payment_wan",),
            "flat": ("annual_revenue_wan",),
        }
        if model not in required_by_model:
            return _blocked("revenue_model_invalid", "收入模型必须为已注册的五种模型之一")
        missing = [
            field
            for field in required_by_model[model]
            if normalized_spec.get(field) in (None, "", [])
        ]
        if missing:
            return _envelope(
                success=False,
                status="missing_inputs",
                code="revenue_driver_missing_inputs",
                message="收入驱动缺少模型必填字段",
                blockers=missing,
                field_errors={f"/revenue_spec/{field}": {"code": "required"} for field in missing},
            )
        if model == "flat" and mode == "review_candidate":
            binding = flat_evidence_binding or {}
            if not all(binding.get(field) for field in ("source_id", "content_hash", "locator")):
                return _blocked(
                    "flat_revenue_formal_evidence_required",
                    "flat 在 review_candidate 模式必须绑定正式原始资料 locator 与 hash",
                )
        from lvke_mcp.domains.finance.revenue_models import expand

        expanded = expand({"revenue": normalized_spec}, op_years)
        revenue_series = list(expanded.get("revenue_by_year") or [])
        if len(revenue_series) != op_years or any(
            _decimal(value) is None or _decimal(value) < 0 for value in revenue_series
        ):
            return _blocked("revenue_expansion_invalid", "收入模型未生成有效的非负逐年序列")
        payload = {
            "object_type": "RevenueDriverSet",
            "project_context_id": project_context_id,
            "market_case_id": market_case_id,
            "mode": mode,
            "evidence_track": evidence_track,
            "revenue_spec": normalized_spec,
            "op_years": op_years,
            "expanded": expanded,
            "flat_evidence_binding": flat_evidence_binding,
            "finance_spec_ledger": [
                {
                    "target_object_type": "FinanceSpec",
                    "target_pointer": "/revenue",
                    "value": normalized_spec,
                    "source_object_id": market_case_id,
                    "transformation": "lvke_mcp.domains.finance.revenue_models.expand",
                }
            ],
            "status": "confirmed",
            "parent_candidate_id": parent_candidate_id or None,
            "selection": selection,
            "parent_object_ids": [
                project_context_id,
                market_case_id,
                *([parent_candidate_id] if parent_candidate_id else []),
            ],
            "next_actions": ["将 finance_spec_ledger 交给 finance_prepare_spec，不在 planning 层重算收入"],
        }
        record = REVENUE_DRIVER_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_create_revenue_drivers",
            status="confirmed",
            source_ids=payload["parent_object_ids"],
            basis={
                "context_basis_hash": context["basis_hash"],
                "market_basis_hash": market["basis_hash"],
                "revenue_spec": normalized_spec,
                "op_years": op_years,
                "mode": mode,
            },
        )
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[record["resource_uri"]],
            next_actions=payload["next_actions"],
            revenue_driver_set_id=record["object_id"],
            object_id=record["object_id"],
            revenue_driver_set=_planning_view(record, "revenue_driver_set_id"),
            finance_spec_ledger=payload["finance_spec_ledger"],
            lineage={"project_context_id": project_context_id, "market_case_id": market_case_id},
            evidence_track=evidence_track,
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_create_revenue_drivers",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )


def create_build_scale_case(
    workspace_id: str,
    project_context_id: str,
    market_case_id: str,
    target_capacity: dict[str, Any],
    land_area_m2: float,
    capacity_intensity_per_m2: float,
    constraints: dict[str, Any],
    facilities: list[dict[str, Any]],
    *,
    parent_candidate_id: str = "",
    selection: dict[str, Any] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    request_payload = {
        "project_context_id": project_context_id,
        "market_case_id": market_case_id,
        "target_capacity": target_capacity,
        "land_area_m2": land_area_m2,
        "capacity_intensity_per_m2": capacity_intensity_per_m2,
        "constraints": constraints,
        "facilities": facilities,
        "parent_candidate_id": parent_candidate_id,
        "selection": selection,
    }

    def mutate() -> dict[str, Any]:
        context, market, error = _confirmed_market_basis(
            workspace_id, project_context_id, market_case_id
        )
        if error:
            return error
        assert context is not None and market is not None
        evidence_track = str((context.get("payload") or {}).get("evidence_track") or "real")
        target = _decimal(target_capacity.get("value"))
        land = _decimal(land_area_m2)
        intensity = _decimal(capacity_intensity_per_m2)
        if target is None or target <= 0 or land is None or land <= 0 or intensity is None or intensity <= 0:
            return _blocked("build_scale_inputs_invalid", "目标产能、用地和单位面积产能必须大于 0")
        market_selected = (((market.get("payload") or {}).get("selection") or {}).get("selected_candidate") or {})
        market_volume = _decimal(market_selected.get("computed_target_volume"))
        if (
            market_volume is not None
            and str(target_capacity.get("unit") or "") == str(market_selected.get("unit") or "")
            and target > market_volume
        ):
            return _blocked("build_capacity_exceeds_selected_market", "目标产能超过已选择市场需求量")
        required_floor = (target / intensity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        floor_total = sum((_decimal(item.get("floor_area_m2")) or Decimal("0")) for item in facilities)
        footprint_total = sum((_decimal(item.get("footprint_m2")) or Decimal("0")) for item in facilities)
        plot_ratio = floor_total / land
        coverage = footprint_total / land
        plot_min = _decimal(constraints.get("plot_ratio_min")) or Decimal("0")
        plot_max = _decimal(constraints.get("plot_ratio_max"))
        coverage_max = _decimal(constraints.get("building_coverage_max"))
        green_min = _decimal(constraints.get("green_ratio_min"))
        green_area = _decimal(constraints.get("green_area_m2"))
        failures: list[str] = []
        if floor_total < required_floor:
            failures.append("capacity_floor_area_insufficient")
        if plot_max is None or plot_ratio > plot_max or plot_ratio < plot_min:
            failures.append("plot_ratio_constraint_failed")
        if coverage_max is None or coverage > coverage_max:
            failures.append("building_coverage_constraint_failed")
        if green_min is None or green_area is None or green_area / land < green_min:
            failures.append("green_ratio_constraint_failed")
        if failures:
            return _envelope(
                success=False,
                status="blocked",
                code="build_scale_constraints_failed",
                message="建设规模未同时满足产能、用地与规划约束",
                blockers=failures,
                calculations={
                    "required_floor_area_m2": float(required_floor),
                    "facility_floor_area_m2": float(floor_total),
                    "plot_ratio": float(plot_ratio),
                    "building_coverage": float(coverage),
                },
            )
        calculations = {
            "required_floor_area_m2": float(required_floor),
            "facility_floor_area_m2": float(floor_total),
            "facility_footprint_m2": float(footprint_total),
            "plot_ratio": float(plot_ratio.quantize(Decimal("0.000001"))),
            "building_coverage": float(coverage.quantize(Decimal("0.000001"))),
            "green_ratio": float((green_area / land).quantize(Decimal("0.000001"))),
            "capacity_margin": float((floor_total * intensity - target).quantize(Decimal("0.01"))),
        }
        payload = {
            "object_type": "BuildScaleCase",
            "project_context_id": project_context_id,
            "market_case_id": market_case_id,
            "target_capacity": target_capacity,
            "land_area_m2": float(land),
            "capacity_intensity_per_m2": float(intensity),
            "constraints": constraints,
            "facilities": facilities,
            "calculations": calculations,
            "evidence_track": evidence_track,
            "status": "confirmed",
            "parent_candidate_id": parent_candidate_id or None,
            "selection": selection,
            "parent_object_ids": [
                project_context_id,
                market_case_id,
                *([parent_candidate_id] if parent_candidate_id else []),
            ],
            "planning_conversion_ledger": [
                {"target_pointer": "/build_scale/target_capacity", "value": target_capacity},
                {"target_pointer": "/build_scale/land_area_m2", "value": float(land)},
            ],
        }
        record = BUILD_SCALE_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_create_build_scale",
            status="confirmed",
            source_ids=payload["parent_object_ids"],
            basis={
                "context_basis_hash": context["basis_hash"],
                "market_basis_hash": market["basis_hash"],
                **request_payload,
            },
        )
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[record["resource_uri"]],
            next_actions=["基于 BuildScaleCase 编制投资和定员驱动，不把估算规模冒充设计成果"],
            build_scale_case_id=record["object_id"],
            object_id=record["object_id"],
            build_scale_case=_planning_view(record, "build_scale_case_id"),
            calculations=calculations,
            planning_conversion_ledger=payload["planning_conversion_ledger"],
            evidence_track=evidence_track,
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_create_build_scale",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )


def create_cost_driver_set(
    workspace_id: str,
    project_context_id: str,
    build_scale_case_id: str,
    invest_breakdown: dict[str, Any],
    operating_cost_items: list[dict[str, Any]],
    *,
    parent_candidate_id: str = "",
    selection: dict[str, Any] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    request_payload = {
        "project_context_id": project_context_id,
        "build_scale_case_id": build_scale_case_id,
        "invest_breakdown": invest_breakdown,
        "operating_cost_items": operating_cost_items,
        "parent_candidate_id": parent_candidate_id,
        "selection": selection,
    }

    def mutate() -> dict[str, Any]:
        context = PROJECT_CONTEXT_STORE.get(workspace_id, project_context_id)
        scale = BUILD_SCALE_STORE.get(workspace_id, build_scale_case_id)
        if context is None or scale is None:
            return _blocked("cost_driver_basis_not_found", "ProjectContext 或 BuildScaleCase 不存在")
        if (scale.get("payload") or {}).get("project_context_id") != project_context_id:
            return _blocked("planning_basis_mismatch", "BuildScaleCase 与 ProjectContext 不属于同一 basis")
        evidence_track = str((context.get("payload") or {}).get("evidence_track") or "real")
        amount_fields = (
            "construction_wan", "civil_wan", "equipment_wan", "installation_wan",
            "other_wan", "reserve_wan", "interest_wan", "working_capital_wan",
        )
        amounts = {field: _decimal(invest_breakdown.get(field)) for field in amount_fields}
        if any(value is None or value < 0 for value in amounts.values()):
            return _blocked("investment_breakdown_invalid", "投资明细字段必须完整且非负")
        construction_components = sum(
            amounts[field] for field in ("civil_wan", "equipment_wan", "installation_wan", "other_wan", "reserve_wan")
        )
        assert amounts["construction_wan"] is not None
        if abs(amounts["construction_wan"] - construction_components) > Decimal("0.01"):
            return _blocked("investment_breakdown_inconsistent", "建设投资与工程、其他费、预备费明细不闭合")
        cost_items: dict[str, float] = {}
        for index, item in enumerate(operating_cost_items):
            name = str(item.get("name") or "").strip()
            amount = _decimal(item.get("annual_amount_wan"))
            if not name or amount is None or amount < 0:
                return _blocked("operating_cost_item_invalid", f"第 {index + 1} 条经营成本无效")
            if name in cost_items:
                return _blocked("operating_cost_item_duplicate", "经营成本科目名称不得重复")
            cost_items[name] = float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        if len(cost_items) < 3:
            return _blocked("operating_cost_detail_insufficient", "经营成本至少需要三个可审计科目")
        project_total = sum(
            amounts[field]
            for field in ("construction_wan", "interest_wan", "working_capital_wan")
        )
        ledger = [
            {
                "target_object_type": "FinanceInputRevision",
                "target_pointer": "/invest_breakdown",
                "value": invest_breakdown,
            },
            {
                "target_object_type": "FinanceInputRevision",
                "target_pointer": "/cost_items",
                "value": cost_items,
            },
        ]
        payload = {
            "object_type": "CostDriverSet",
            "project_context_id": project_context_id,
            "build_scale_case_id": build_scale_case_id,
            "invest_breakdown": invest_breakdown,
            "operating_cost_items": operating_cost_items,
            "annual_operating_cost_wan": round(sum(cost_items.values()), 2),
            "project_total_investment_wan": float(project_total.quantize(Decimal("0.01"))),
            "evidence_track": evidence_track,
            "finance_spec_ledger": ledger,
            "status": "confirmed",
            "parent_candidate_id": parent_candidate_id or None,
            "selection": selection,
            "parent_object_ids": [
                project_context_id,
                build_scale_case_id,
                *([parent_candidate_id] if parent_candidate_id else []),
            ],
        }
        record = COST_DRIVER_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_create_cost_drivers",
            status="confirmed",
            source_ids=payload["parent_object_ids"],
            basis={"context_basis_hash": context["basis_hash"], **request_payload},
        )
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[record["resource_uri"]],
            next_actions=["将投资与成本 ledger 合并进 FinanceSpec，并由财务服务重新校验"],
            cost_driver_set_id=record["object_id"],
            object_id=record["object_id"],
            cost_driver_set=_planning_view(record, "cost_driver_set_id"),
            finance_spec_ledger=ledger,
            evidence_track=evidence_track,
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_create_cost_drivers",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )


def create_labor_plan(
    workspace_id: str,
    project_context_id: str,
    build_scale_case_id: str,
    positions: list[dict[str, Any]],
    *,
    parent_candidate_id: str = "",
    selection: dict[str, Any] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    request_payload = {
        "project_context_id": project_context_id,
        "build_scale_case_id": build_scale_case_id,
        "positions": positions,
        "parent_candidate_id": parent_candidate_id,
        "selection": selection,
    }

    def mutate() -> dict[str, Any]:
        context = PROJECT_CONTEXT_STORE.get(workspace_id, project_context_id)
        scale = BUILD_SCALE_STORE.get(workspace_id, build_scale_case_id)
        if context is None or scale is None:
            return _blocked("labor_plan_basis_not_found", "ProjectContext 或 BuildScaleCase 不存在")
        if (scale.get("payload") or {}).get("project_context_id") != project_context_id:
            return _blocked("planning_basis_mismatch", "BuildScaleCase 与 ProjectContext 不属于同一 basis")
        evidence_track = str((context.get("payload") or {}).get("evidence_track") or "real")
        finance_rows: list[dict[str, Any]] = []
        wage_total = Decimal("0")
        welfare_total = Decimal("0")
        headcount_total = 0
        names: set[str] = set()
        for index, item in enumerate(positions):
            name = str(item.get("name") or "").strip()
            category = str(item.get("category") or "").strip()
            headcount = item.get("headcount")
            wage = _decimal(item.get("avg_wage_yuan"))
            welfare_rate = _decimal(item.get("welfare_rate"))
            if (
                not name or not category or name in names
                or not isinstance(headcount, int) or isinstance(headcount, bool) or headcount <= 0
                or wage is None or wage < 0
                or welfare_rate is None or welfare_rate < 0 or welfare_rate > 1
            ):
                return _blocked("labor_position_invalid", f"第 {index + 1} 条岗位定员无效或重复")
            names.add(name)
            base = Decimal(headcount) * wage / Decimal("10000")
            wage_total += base
            welfare_total += base * welfare_rate
            headcount_total += headcount
            finance_rows.append(
                {
                    "category": category,
                    "name": name,
                    "headcount": headcount,
                    "avg_wage_yuan": float(wage),
                }
            )
        if not finance_rows:
            return _blocked("labor_positions_required", "至少需要一个岗位类别")
        wage_total = wage_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        welfare_total = welfare_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ledger = [
            {
                "target_object_type": "FinanceInputRevision",
                "target_pointer": "/labor_plan",
                "value": finance_rows,
            },
            {
                "target_object_type": "FinanceInputRevision",
                "target_pointer": "/cost_items/工资",
                "value": float(wage_total),
            },
            {
                "target_object_type": "FinanceInputRevision",
                "target_pointer": "/cost_items/福利",
                "value": float(welfare_total),
            },
        ]
        payload = {
            "object_type": "LaborPlan",
            "project_context_id": project_context_id,
            "build_scale_case_id": build_scale_case_id,
            "positions": positions,
            "headcount_total": headcount_total,
            "annual_wage_wan": float(wage_total),
            "annual_welfare_wan": float(welfare_total),
            "evidence_track": evidence_track,
            "finance_spec_ledger": ledger,
            "status": "confirmed",
            "parent_candidate_id": parent_candidate_id or None,
            "selection": selection,
            "parent_object_ids": [
                project_context_id,
                build_scale_case_id,
                *([parent_candidate_id] if parent_candidate_id else []),
            ],
        }
        record = LABOR_PLAN_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_create_labor_plan",
            status="confirmed",
            source_ids=payload["parent_object_ids"],
            basis={"context_basis_hash": context["basis_hash"], **request_payload},
        )
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[record["resource_uri"]],
            next_actions=["将 labor_plan 与工资福利 ledger 合并到 CostDriverSet/FinanceSpec，冲突时 fail closed"],
            labor_plan_id=record["object_id"],
            object_id=record["object_id"],
            labor_plan=_planning_view(record, "labor_plan_id"),
            finance_spec_ledger=ledger,
            evidence_track=evidence_track,
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_create_labor_plan",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )


def _option_basis_records(
    workspace_id: str,
    project_context_id: str,
    basis_object_ids: list[str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    context = PROJECT_CONTEXT_STORE.get(
        workspace_id, project_context_id
    )
    if context is None:
        return None, [], _blocked("project_context_not_found", "ProjectContext 不存在")
    records: list[dict[str, Any]] = []
    for object_id in basis_object_ids:
        record = None
        for store, _kind in _RESOURCE_STORES:
            if store is OPTION_COMPARISON_STORE:
                continue
            record = store.get(workspace_id, object_id)
            if record is not None:
                break
        if record is None:
            return context, records, _blocked(
                "option_basis_not_found",
                f"方案比选 basis 对象不存在：{object_id}",
            )
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        bound_context_id = payload.get("project_context_id")
        if bound_context_id and bound_context_id != project_context_id:
            return context, records, _blocked(
                "option_basis_context_mismatch",
                "方案比选 basis 对象与 ProjectContext 不一致",
            )
        records.append(record)
    return context, records, None


def _validate_option_evidence(
    options: list[dict[str, Any]], criteria_ids: set[str]
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for option_index, option in enumerate(options):
        evidence = option.get("evidence_bindings")
        evidence = evidence if isinstance(evidence, dict) else {}
        for criterion_id in criteria_ids:
            bindings = evidence.get(criterion_id)
            bindings = bindings if isinstance(bindings, list) else []
            if not bindings:
                errors.append({
                    "path": f"/options/{option_index}/evidence_bindings/{criterion_id}",
                    "code": "option_criterion_evidence_required",
                })
                continue
            for binding_index, binding in enumerate(bindings):
                binding = binding if isinstance(binding, dict) else {}
                if binding.get("source_type") == "search_summary":
                    errors.append({
                        "path": (
                            f"/options/{option_index}/evidence_bindings/"
                            f"{criterion_id}/{binding_index}/source_type"
                        ),
                        "code": "search_summary_not_evidence",
                    })
                for field in ("source_id", "content_hash", "locator"):
                    if not binding.get(field):
                        errors.append({
                            "path": (
                                f"/options/{option_index}/evidence_bindings/"
                                f"{criterion_id}/{binding_index}/{field}"
                            ),
                            "code": "option_evidence_binding_incomplete",
                        })
    return errors


def prepare_option_comparison(
    workspace_id: str,
    project_context_id: str,
    category: str,
    criteria: list[dict[str, Any]],
    options: list[dict[str, Any]],
    mandatory_constraints: list[dict[str, Any]],
    basis_object_ids: list[str],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    request_payload = {
        "project_context_id": project_context_id,
        "category": category,
        "criteria": criteria,
        "options": options,
        "mandatory_constraints": mandatory_constraints,
        "basis_object_ids": sorted(set(basis_object_ids)),
    }

    def mutate() -> dict[str, Any]:
        context, basis_records, error = _option_basis_records(
            workspace_id,
            project_context_id,
            request_payload["basis_object_ids"],
        )
        if error:
            return error
        assert context is not None
        allowed_categories = {"equipment", "building", "process", "site", "operating_model"}
        if category not in allowed_categories:
            return _blocked("option_category_invalid", "方案类型未注册")
        if not 1 <= len(criteria) <= 20 or not 2 <= len(options) <= 20:
            return _blocked(
                "option_comparison_size_invalid",
                "方案比选需要 1..20 个指标和 2..20 个方案",
            )
        criterion_ids = [str(item.get("criterion_id") or "") for item in criteria]
        option_ids = [str(item.get("option_id") or "") for item in options]
        constraint_ids = [
            str(item.get("constraint_id") or "") for item in mandatory_constraints
        ]
        if (
            "" in criterion_ids
            or len(set(criterion_ids)) != len(criterion_ids)
            or "" in option_ids
            or len(set(option_ids)) != len(option_ids)
            or "" in constraint_ids
            or len(set(constraint_ids)) != len(constraint_ids)
        ):
            return _blocked(
                "option_identifier_invalid",
                "指标、方案和约束 ID 必须非空且各自唯一",
            )
        weights = [_decimal(item.get("weight")) for item in criteria]
        if any(weight is None or weight <= 0 for weight in weights):
            return _blocked("option_weight_invalid", "每个评价指标权重必须大于 0")
        weight_total = sum((weight for weight in weights if weight is not None), Decimal("0"))
        if abs(weight_total - Decimal("1")) > Decimal("0.000001"):
            return _blocked("option_weight_sum_invalid", "评价指标权重合计必须等于 1")
        criteria_by_id = {str(item["criterion_id"]): item for item in criteria}
        constraint_set = set(constraint_ids)
        field_errors: list[dict[str, Any]] = []
        for option_index, option in enumerate(options):
            values = option.get("values") if isinstance(option.get("values"), dict) else {}
            if set(values) != set(criterion_ids):
                field_errors.append({
                    "path": f"/options/{option_index}/values",
                    "code": "option_values_incomplete",
                })
            for criterion_id, value in values.items():
                if criterion_id in criteria_by_id and _decimal(value) is None:
                    field_errors.append({
                        "path": f"/options/{option_index}/values/{criterion_id}",
                        "code": "option_value_invalid",
                    })
            results = option.get("constraint_results")
            results = results if isinstance(results, dict) else {}
            if set(results) != constraint_set or any(not isinstance(value, bool) for value in results.values()):
                field_errors.append({
                    "path": f"/options/{option_index}/constraint_results",
                    "code": "option_constraint_results_incomplete",
                })
        field_errors.extend(_validate_option_evidence(options, set(criterion_ids)))
        if field_errors:
            return _envelope(
                success=False,
                status="missing_inputs",
                code="option_comparison_invalid",
                message="方案比选缺少可复算数值、约束结果或证据定位",
                blockers=sorted({str(item["code"]) for item in field_errors}),
                field_errors={item["path"]: item for item in field_errors},
            )

        ranges: dict[str, tuple[Decimal, Decimal]] = {}
        for criterion_id in criterion_ids:
            values = [_decimal(option["values"][criterion_id]) for option in options]
            numeric = [value for value in values if value is not None]
            ranges[criterion_id] = (min(numeric), max(numeric))
        scored_options: list[dict[str, Any]] = []
        for option in options:
            score_parts: list[dict[str, Any]] = []
            weighted_score = Decimal("0")
            for criterion_id in criterion_ids:
                criterion = criteria_by_id[criterion_id]
                value = _decimal(option["values"][criterion_id])
                assert value is not None
                low, high = ranges[criterion_id]
                if high == low:
                    normalized = Decimal("1")
                elif criterion.get("direction") == "lower_is_better":
                    normalized = (high - value) / (high - low)
                else:
                    normalized = (value - low) / (high - low)
                weight = _decimal(criterion["weight"])
                assert weight is not None
                contribution = normalized * weight
                weighted_score += contribution
                score_parts.append({
                    "criterion_id": criterion_id,
                    "raw_value": float(value),
                    "normalized_score": float(normalized.quantize(Decimal("0.000001"))),
                    "weighted_contribution": float(
                        (contribution * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                    ),
                })
            constraint_results = dict(option.get("constraint_results") or {})
            eligible = all(constraint_results.values()) if constraint_results else True
            scored_options.append({
                **option,
                "eligible": eligible,
                "failed_constraint_ids": sorted(
                    key for key, passed in constraint_results.items() if not passed
                ),
                "weighted_score": float(
                    (weighted_score * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                ),
                "score_parts": score_parts,
            })
        ranked = sorted(
            (option for option in scored_options if option["eligible"]),
            key=lambda option: (-option["weighted_score"], option["option_id"]),
        )
        rank_by_id = {option["option_id"]: index + 1 for index, option in enumerate(ranked)}
        scored_options = [
            {**option, "score_rank": rank_by_id.get(option["option_id"])}
            for option in scored_options
        ]
        leader = ranked[0]["option_id"] if ranked else None
        payload = {
            "object_type": "OptionComparison",
            "project_context_id": project_context_id,
            "category": category,
            "criteria": criteria,
            "mandatory_constraints": mandatory_constraints,
            "options": scored_options,
            "score_method": "min_max_weighted_sum.v1",
            "score_leader_option_id": leader,
            "selection_required": True,
            "selection": None,
            "status": "candidate",
            "evidence_track": (context.get("payload") or {}).get("evidence_track", "real"),
            "basis_object_ids": request_payload["basis_object_ids"],
            "parent_object_ids": [project_context_id, *request_payload["basis_object_ids"]],
            "next_actions": [
                "由 Codex/人员审阅得分、约束和证据后提交选择理由"
            ],
        }
        record = OPTION_COMPARISON_STORE.put(
            workspace_id,
            payload,
            producer="lvke-project-planning.planning_prepare_option_comparison",
            status="candidate",
            source_ids=payload["parent_object_ids"],
            basis={
                "context_basis_hash": context["basis_hash"],
                "basis_hashes": [record["basis_hash"] for record in basis_records],
                **request_payload,
            },
        )
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[record["resource_uri"]],
            next_actions=payload["next_actions"],
            option_comparison_id=record["object_id"],
            object_id=record["object_id"],
            option_comparison=_planning_view(record, "option_comparison_id"),
            score_leader_option_id=leader,
            selection_required=True,
            lineage={
                "project_context_id": project_context_id,
                "basis_object_ids": request_payload["basis_object_ids"],
            },
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_prepare_option_comparison",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )


def confirm_option_selection(
    workspace_id: str,
    option_comparison_id: str,
    selected_option_id: str,
    selection_reason: str,
    rejected_option_ids: list[str],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    request_payload = {
        "option_comparison_id": option_comparison_id,
        "selected_option_id": selected_option_id,
        "selection_reason": selection_reason,
        "rejected_option_ids": sorted(set(rejected_option_ids)),
    }

    def mutate() -> dict[str, Any]:
        candidate = OPTION_COMPARISON_STORE.get(
            workspace_id, option_comparison_id
        )
        if candidate is None:
            return _blocked("option_comparison_not_found", "候选方案比选对象不存在")
        payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
        if payload.get("status") != "candidate":
            return _blocked("option_comparison_not_candidate", "只能确认 candidate 状态方案比选")
        by_id = {
            str(option.get("option_id") or ""): option
            for option in payload.get("options") or []
            if isinstance(option, dict)
        }
        selected = by_id.get(selected_option_id)
        if selected is None:
            return _blocked("option_not_found", "选定方案不存在")
        if not selected.get("eligible"):
            return _blocked("option_ineligible", "选定方案未通过强制约束")
        expected_rejected = set(by_id) - {selected_option_id}
        if set(rejected_option_ids) != expected_rejected:
            return _blocked(
                "option_rejected_list_incomplete",
                "必须显式列出全部未选方案，禁止隐式合并",
            )
        reason = str(selection_reason or "").strip()
        if len(reason) < 10:
            return _blocked("option_selection_reason_insufficient", "方案选择理由至少 10 个字符")
        leader = payload.get("score_leader_option_id")
        selection = {
            "selected_option_id": selected_option_id,
            "selected_option": selected,
            "selection_reason": reason,
            "rejected_option_ids": sorted(rejected_option_ids),
            "score_leader_option_id": leader,
            "selection_deviates_from_score_leader": selected_option_id != leader,
        }
        confirmed_payload = {
            **payload,
            "status": "confirmed",
            "selection_required": False,
            "selection": selection,
            "parent_object_ids": [option_comparison_id, *list(payload.get("parent_object_ids") or [])],
            "next_actions": ["将已确认方案的数值边界传递到规模、成本或报告对象"],
        }
        record = OPTION_COMPARISON_STORE.put(
            workspace_id,
            confirmed_payload,
            producer="lvke-project-planning.planning_confirm_option_selection",
            status="confirmed",
            source_ids=confirmed_payload["parent_object_ids"],
            basis={
                "candidate_basis_hash": candidate["basis_hash"],
                "selection": selection,
            },
        )
        return _envelope(
            success=True,
            status="ok",
            resource_uris=[record["resource_uri"]],
            next_actions=confirmed_payload["next_actions"],
            option_comparison_id=record["object_id"],
            object_id=record["object_id"],
            option_comparison=_planning_view(record, "option_comparison_id"),
            selection=selection,
            lineage={
                "candidate_option_comparison_id": option_comparison_id,
                "project_context_id": payload.get("project_context_id"),
            },
            idempotent_replay=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="planning_confirm_option_selection",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutate,
    )


def get_planning_object(
    workspace_id: str,
    object_type: str,
    object_id: str,
) -> dict[str, Any]:
    mapping = {
        "RevenueDriverSet": (REVENUE_DRIVER_STORE, "revenue_driver_set_id"),
        "BuildScaleCase": (BUILD_SCALE_STORE, "build_scale_case_id"),
        "CostDriverSet": (COST_DRIVER_STORE, "cost_driver_set_id"),
        "LaborPlan": (LABOR_PLAN_STORE, "labor_plan_id"),
        "OptionComparison": (OPTION_COMPARISON_STORE, "option_comparison_id"),
        "PolicyBasis": (POLICY_BASIS_STORE, "policy_basis_id"),
    }
    selected = mapping.get(object_type)
    if selected is None:
        return _blocked("planning_object_type_invalid", "未知 planning 对象类型")
    store, id_field = selected
    record = store.get(workspace_id, object_id)
    if record is None:
        return _blocked("planning_object_not_found", "planning 对象不存在或不属于当前作用域")
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[record["resource_uri"]],
        object_id=object_id,
        object_type=object_type,
        planning_object=_planning_view(record, id_field),
        basis_hash=record["basis_hash"],
        content_hash=record["content_hash"],
    )


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


def list_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    allowed = {kind for _store, kind in _RESOURCE_STORES}
    if resource_type and resource_type not in allowed:
        return _blocked(
            "resource_type_invalid",
            "未知 Resource 类型过滤条件",
        )
    entries: list[dict[str, Any]] = []
    for store, kind in _RESOURCE_STORES:
        if resource_type and kind != resource_type:
            continue
        for record in store.list(workspace_id):
            entries.append(
                {
                    "uri": record["resource_uri"],
                    "name": record["object_id"],
                    "resource_type": kind,
                    "mime_type": "application/json",
                    "created_at": record["created_at"],
                    "content_hash": record["content_hash"],
                }
            )
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _blocked(
            str(exc),
            "Resource 分页游标无效或列表已变化",
        )
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[entry["uri"] for entry in page["resources"]],
        resources=page["resources"],
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
        snapshot_hash=page["snapshot_hash"],
    )


def resolve_resource(
    uri: str,
    workspace_id: str,
) -> dict[str, Any] | None:
    expected = f"lvke://project-planning/workspaces/{workspace_id}/"
    if not str(uri).startswith(expected):
        return None
    for store, _kind in _RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None:
            return record
    return None


def read_resource(
    workspace_id: str,
    uri: str,
) -> dict[str, Any]:
    record = resolve_resource(uri, workspace_id)
    if record is None:
        return _blocked(
            "resource_not_found",
            "资源不存在或不属于当前工作区",
        )
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[uri],
        uri=uri,
        mime_type="application/json",
        content=json.dumps(record, ensure_ascii=False, indent=2),
        content_hash=record["content_hash"],
    )
