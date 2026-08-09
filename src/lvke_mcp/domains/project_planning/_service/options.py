"""lvke-project-planning application 拆分：OptionComparison 域。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from lvke_mcp.adapters.project_planning_repository import (
    OPTION_COMPARISON_STORE,
    PROJECT_CONTEXT_STORE,
    RESOURCE_STORES as _RESOURCE_STORES,
)

from .base import (
    _blocked,
    _decimal,
    _envelope,
    _idempotent_mutation,
    _planning_view,
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
                    "missing_criterion_ids": sorted(set(criterion_ids) - set(values)),
                    "unexpected_criterion_ids": sorted(set(values) - set(criterion_ids)),
                    "expected_criterion_ids": list(criterion_ids),
                    "resolution": "为每个评价指标提供且仅提供一个数值",
                })
            for criterion_id, value in values.items():
                if criterion_id in criteria_by_id and _decimal(value) is None:
                    field_errors.append({
                        "path": f"/options/{option_index}/values/{criterion_id}",
                        "code": "option_value_invalid",
                    })
            results = option.get("constraint_results")
            results = results if isinstance(results, dict) else {}
            non_boolean = sorted(
                key for key, value in results.items() if not isinstance(value, bool)
            )
            if set(results) != constraint_set or non_boolean:
                # 缺哪些键、多哪些键、哪些键不是布尔，三者都已算出；只回一个 code
                # 会让调用方在 20 个约束里盲猜。
                field_errors.append({
                    "path": f"/options/{option_index}/constraint_results",
                    "code": "option_constraint_results_incomplete",
                    "missing_constraint_ids": sorted(constraint_set - set(results)),
                    "unexpected_constraint_ids": sorted(set(results) - constraint_set),
                    "non_boolean_constraint_ids": non_boolean,
                    "expected_constraint_ids": sorted(constraint_set),
                    "resolution": (
                        "为每个 mandatory_constraint 提供且仅提供一个布尔结果"
                    ),
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