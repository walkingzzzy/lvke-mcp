"""lvke-project-planning application 拆分：ProjectContext / InputApplicability 域。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lvke_mcp.runtime.skill_inventory import (
    AUTHORITATIVE_SOURCES,
    resolve_skill_inventory,
)
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
    "evidence_policy",
    "project_fact_certified",
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
_REPO_ROOT = Path(__file__).resolve().parents[5]
# 离线校验时用于解析 reference_path 的 Skill 根。运行时资格不看这些路径。
_OFFLINE_SKILL_ROOTS = (
    _REPO_ROOT / "skills",
    _REPO_ROOT / "plugins" / "lvke-mcp" / "skills",
)


def _reference_exists(carrier_skill: str, reference_path: str) -> bool:
    """Resolve a carrier-relative reference path under the offline Skill roots."""

    if not carrier_skill or not reference_path:
        return False
    candidate = Path(reference_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return False
    for root in _OFFLINE_SKILL_ROOTS:
        if (root / carrier_skill / candidate).is_file():
            return True
    return False


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
    # 未命中与歧义两种阻断也必须带 manifest lineage：否则同样无法审计是哪一版
    # manifest 判定了未命中。
    manifest_lineage = {
        "project_context_id": record["object_id"],
        "project_context_basis_hash": record["basis_hash"],
        "route_manifest_version": str(manifest.get("schema_version") or ""),
        "route_manifest_hash": sha256_json(manifest),
    }
    if not matches:
        return _envelope(
            success=False,
            status="blocked",
            code="industry_skill_route_not_found",
            message="没有与 ProjectContext 匹配的行业 Skill；禁止静默使用通用行业默认值",
            blockers=["industry_skill_route_not_found"],
            next_actions=["补充或修订 industry_code/asset_type 后重新解析"],
            project_context_id=record["object_id"],
            resolved_context={
                "industry_code": industry,
                "project_type": project_type,
                "transaction_structure": transaction,
                "asset_type": asset_type,
            },
            route_manifest_version=str(manifest.get("schema_version") or ""),
            route_manifest_hash=sha256_json(manifest),
            lineage=manifest_lineage,
        )
    top_priority = max(int(item.get("priority") or 0) for item in matches)
    selected = [item for item in matches if int(item.get("priority") or 0) == top_priority]
    if len(selected) != 1:
        return _envelope(
            success=False,
            status="blocked",
            code="industry_skill_route_ambiguous",
            message="ProjectContext 同时命中多个同优先级行业 Skill",
            blockers=["industry_skill_route_ambiguous"],
            next_actions=["修订路由 manifest 或 ProjectContext，确保唯一主 Skill"],
            project_context_id=record["object_id"],
            ambiguous_route_ids=sorted(
                str(item.get("route_id") or "") for item in selected
            ),
            route_manifest_version=str(manifest.get("schema_version") or ""),
            route_manifest_hash=sha256_json(manifest),
            lineage=manifest_lineage,
        )
    route = selected[0]
    manifest_hash = sha256_json(manifest)
    primary_skill = str(route.get("primary_skill") or "")
    auxiliary_skills = [str(item) for item in route.get("auxiliary_skills") or []]
    # 行业口径的真实承载：通用编排 Skill 只协调阶段，不含行业内容，所以不能用它
    # 顶替行业参考。每条 industry_reference 给出 carrier_skill（可加载的顶层 Skill）
    # 与 reference_path（该 Skill 内真实存在的参考文件），调用方可机器解析后直接读。
    industry_references: list[dict[str, Any]] = []
    unresolved_references: list[str] = []
    for item in route.get("industry_references") or []:
        if not isinstance(item, dict):
            continue
        carrier = str(item.get("carrier_skill") or "")
        reference_path = str(item.get("reference_path") or "")
        capability = str(item.get("capability") or "")
        resolved = _reference_exists(carrier, reference_path)
        industry_references.append(
            {
                "capability": capability,
                "carrier_skill": carrier,
                "reference_path": reference_path,
                "reference_resolved": resolved,
            }
        )
        if not resolved:
            unresolved_references.append(f"{carrier}/{reference_path}")

    # manifest 里的名字不等于宿主真的加载了那个 Skill。运行时资格只承认宿主声明的
    # inventory；宿主没声明时如实报 source=unavailable，不用磁盘冒充运行时状态。
    inventory = resolve_skill_inventory()
    inventory_source = str(inventory["source"])
    installed: set[str] = set(inventory["names"])
    # 路由 lineage 在所有分支都必须返回：manifest 变更后没有版本与 hash 就无法审计
    # 是哪一版产生了当前结论。
    lineage = {
        "project_context_id": record["object_id"],
        "project_context_basis_hash": record["basis_hash"],
        "route_id": str(route.get("route_id") or ""),
        "route_manifest_version": str(manifest.get("schema_version") or ""),
        "route_manifest_hash": manifest_hash,
        "skill_inventory_source": inventory_source,
    }
    resolved_context = {
        "industry_code": industry,
        "project_type": project_type,
        "transaction_structure": transaction,
        "asset_type": asset_type,
    }

    missing_primary = bool(primary_skill) and primary_skill not in installed
    missing_auxiliary = sorted(
        name for name in auxiliary_skills if name not in installed
    )
    missing_carriers = sorted(
        {
            str(item["carrier_skill"])
            for item in industry_references
            if item["carrier_skill"] and item["carrier_skill"] not in installed
        }
    )
    if missing_primary:
        # 用 _envelope 而非 _blocked：后者只收 code/message/next_actions，
        # 而调用方需要知道是哪条 manifest 版本的哪条路由、缺哪些 Skill 才能修。
        return _envelope(
            success=False,
            status="blocked",
            code="industry_skill_not_installed",
            message=f"路由命中的主 Skill 未安装：{primary_skill}",
            blockers=["industry_skill_not_installed"],
            next_actions=[
                "安装缺失的行业 Skill，或修订 industry_skill_routes.json 指向已安装 Skill",
            ],
            project_context_id=record["object_id"],
            project_context_basis_hash=record["basis_hash"],
            resolved_context=resolved_context,
            route_id=str(route.get("route_id") or ""),
            primary_skill=primary_skill,
            auxiliary_skills=auxiliary_skills,
            industry_references=industry_references,
            unresolved_industry_references=unresolved_references,
            missing_skills=[primary_skill, *missing_auxiliary],
            skill_inventory_source=inventory_source,
            route_manifest_version=str(manifest.get("schema_version") or ""),
            route_manifest_hash=manifest_hash,
            lineage=lineage,
        )
    # 资格判定是不对称的：磁盘上没有 → 一定加载不了，可据此阻断；磁盘上有 →
    # 只说明仓库里写了它，不证明这份部署带上了它。因此 disk_offline 下即使全部
    # 命中也不给 ok，如实降级为 partial 并说明如何取得运行时资格。
    unverified = inventory_source not in AUTHORITATIVE_SOURCES
    degraded = bool(
        missing_auxiliary or missing_carriers or unresolved_references or unverified
    )
    warnings = [
        *(f"auxiliary_skill_not_installed:{name}" for name in missing_auxiliary),
        *(f"industry_reference_carrier_not_installed:{name}" for name in missing_carriers),
        *(f"industry_reference_unresolved:{item}" for item in unresolved_references),
        *(["skill_loadability_unverified"] if unverified else []),
    ]
    next_actions: list[str] = []
    if missing_auxiliary or missing_carriers:
        next_actions.append(
            "缺失的 Skill 无法加载，不得据此认为已取得对应行业口径；"
            "补装 Skill 或改用已安装 Skill 的口径"
        )
    if unresolved_references:
        next_actions.append(
            "行业参考文件未解析到，修订 industry_skill_routes.json 的 reference_path "
            "或补齐该参考文件；不得用通用编排 Skill 顶替行业口径"
        )
    if unverified:
        next_actions.append(
            "未找到已发布 Skill 清单，本响应只证明仓库内存在这些 Skill，"
            "不证明这份部署已带上它们；运行 scripts/build_codex_plugin.py 生成 "
            "skill_inventory.json，或由宿主设置 LVKE_MCP_SKILL_INVENTORY "
            "后重新解析才能取得运行时资格"
        )
    return _envelope(
        success=not degraded,
        status="partial" if degraded else "ok",
        code="skill_loadability_unverified" if unverified else "",
        message=(
            "路由已解析，但宿主未声明 Skill 清单，Skill 可加载性未经运行时校验"
            if unverified
            else ""
        ),
        project_context_id=record["object_id"],
        project_context_basis_hash=record["basis_hash"],
        resolved_context=resolved_context,
        route_id=str(route.get("route_id") or ""),
        primary_skill=primary_skill,
        auxiliary_skills=auxiliary_skills,
        industry_references=industry_references,
        unresolved_industry_references=unresolved_references,
        installed_auxiliary_skills=[
            name for name in auxiliary_skills if name in installed
        ],
        missing_skills=missing_auxiliary,
        skill_inventory_source=inventory_source,
        route_manifest_version=str(manifest.get("schema_version") or ""),
        route_manifest_hash=manifest_hash,
        lineage=lineage,
        warnings=warnings,
        next_actions=next_actions,
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
        if str(normalized.get("evidence_track") or "") == "sim_a_formal":
            normalized.setdefault("evidence_policy", "sim_a_formal")
            normalized.setdefault("project_fact_certified", True)
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