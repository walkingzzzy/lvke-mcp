"""lvke-project-planning application 拆分：MarketSizingCase 域。"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from lvke_mcp.runtime.storage import (
    canonical_json,
    sha256_json,
)
from lvke_mcp.runtime.evidence_qualification import (
    declared_evidence_policy,
    project_fact_may_be_certified,
)
from lvke_mcp.adapters.project_planning_repository import (
    MARKET_CASE_STORE,
    PROJECT_CONTEXT_STORE,
)

from .base import (
    _blocked,
    _decimal,
    _downstream_stale,
    _envelope,
    _idempotent_mutation,
    _market_view,
)


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

    def normalized_locator(value: Any) -> str:
        """Reduce a locator to its canonical form so both sides compare equal.

        Applied to the binding and to every pack locator before the membership
        test below, which is what lets a caller pass a structured locator in any
        JSON spelling — ``json.dumps`` defaults (spaces), ``indent``, a different
        key order, or ``ensure_ascii=True`` — and still match.  Plain strings are
        compared verbatim apart from surrounding whitespace, and numbers keep
        their type (``1`` and ``1.0`` are distinct).
        """

        if isinstance(value, (dict, list)):
            return canonical_json(value)
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        return canonical_json(parsed) if isinstance(parsed, (dict, list)) else text

    candidate_locators: dict[str, set[str]] = {}
    for item in evidence_payload.get("fact_candidates") or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        locator = item.get("locator")
        if source_id and locator:
            candidate_locators.setdefault(source_id, set()).add(normalized_locator(locator))

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
            locator = normalized_locator(binding.get("locator"))
            source_locators = {
                normalized_locator(item)
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
        from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE

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
        context_payload = context.get("payload") or {}
        evidence_policy = declared_evidence_policy(
            evidence_payload,
            default=resolved_track,
        )
        project_fact_certified = project_fact_may_be_certified(
            evidence_policy,
            own_qualification_passed=True,
            parents=[evidence_payload, context_payload],
        )
        next_actions = [
            "调用 planning_compare(object_kind=\"market_case\") 比较路径偏差",
            "调用 planning_validate(object_kind=\"market_case\") 检查证据与口径",
            "由 Codex 明确选择后调用 planning_confirm(object_kind=\"market_case\")",
        ]
        payload = {
            "object_type": "MarketSizingCase",
            "project_context_id": project_context_id,
            "project_context_basis_hash": context["basis_hash"],
            "evidence_pack_id": evidence_pack_id,
            "evidence_pack_basis_hash": evidence["basis_hash"],
            "evidence_track": resolved_track,
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_certified,
            "candidates": normalized_candidates,
            "status": "candidate",
            "revision_number": 1,
            "parent_object_ids": [project_context_id, evidence_pack_id],
            "selection": None,
            "warnings": [],
            "blockers": [],
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
            next_actions=next_actions,
            market_case_id=record["object_id"],
            object_id=record["object_id"],
            market_case=_market_view(record),
            object_type="MarketSizingCase",
            evidence_track=payload["evidence_track"],
            evidence_policy=evidence_policy,
            project_fact_certified=project_fact_certified,
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
                next_actions=["调用 planning_validate(object_kind=\"market_case\") 查看 field_errors"],
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
