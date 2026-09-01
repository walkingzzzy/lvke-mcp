"""复测入口：操作识别、意图查找、事件追加与整改前后关联。分类原语在 base，因为 events 的事件投影也要用它们。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lvke_mcp.runtime.storage import sha256_json, utc_now
from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    SIM_A_FORMAL,
    validate_object_formal_lineage,
)
from lvke_mcp.servers.lvke_deliverable_review.contracts import normalize_target
from lvke_mcp.servers.lvke_deliverable_review.store import STORE

from .base import (
    _blocked,
    _finding_coverage_rule_id,
    _finding_match_key,
    _message,
    _next_actions,
    _ok,
    _review_uri,
    _write,
)

from .disposition import (
    _evidence_is_precise,
    _retest_target_scope_matches,
)

from .events import (
    _project,
)

from .lifecycle import (
    start,
)

from .preparation import (
    prepare,
)

from .target_resolve import (
    _resolve_target,
)



def _retest_operation_identity(
    args: dict[str, Any],
) -> tuple[str, str, str]:
    key_identity = {
        "operation": "review_retest",
        "idempotency_key": str(args.get("idempotency_key") or ""),
    }
    key_hash = sha256_json(key_identity)
    operation_id = "retestop_" + key_hash.removeprefix("sha256:")[:32]
    return operation_id, key_hash, sha256_json(args)


def _find_retest_intent(
    workspace_id: str,
    operation_id: str,
) -> tuple[str, dict[str, Any]] | None:
    matches: list[tuple[str, dict[str, Any]]] = []
    for review_id in STORE.review_ids(workspace_id):
        for event in STORE.events(workspace_id, review_id):
            payload = event.get("payload") or {}
            if (
                event.get("event_type") == "retest_started"
                and payload.get("operation_id") == operation_id
            ):
                matches.append((review_id, event))
    if len(matches) > 1:
        raise ValueError("retest_operation_conflict")
    return matches[0] if matches else None


def _append_retest_event_once(
    workspace_id: str,
    review_id: str,
    event_type: str,
    payload: dict[str, Any],

    *,
    identity_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    operation_id = str(payload.get("operation_id") or "")
    matches = [
        event
        for event in STORE.events(workspace_id, review_id)
        if event.get("event_type") == event_type
        and str((event.get("payload") or {}).get("operation_id") or "") == operation_id
        and all(
            (event.get("payload") or {}).get(field) == payload.get(field)
            for field in identity_fields
        )
    ]
    if matches:
        if len(matches) != 1 or sha256_json(matches[0].get("payload") or {}) != sha256_json(payload):
            raise ValueError("retest_operation_conflict")
        return matches[0]
    return STORE.append(workspace_id, review_id, event_type, payload)


def _retest_failure(
    workspace_id: str,
    parent_review_id: str,
    intent: dict[str, Any],

    code: str,
    message: str,
) -> dict[str, Any]:
    payload = {
        "operation_id": intent["operation_id"],
        "code": code,
        "message": message,
        "failed_at_operation_started_at": intent["operation_started_at"],
    }
    _append_retest_event_once(
        workspace_id,
        parent_review_id,
        "retest_failed",
        payload,

    )
    return _blocked(code, message, review_id=parent_review_id)


def retest(args: dict[str, Any]) -> dict[str, Any]:
    def execute(workspace_id: str) -> dict[str, Any]:
        requested_parent_review_id = str(args.get("review_id") or "")
        operation_id, key_hash, request_hash = _retest_operation_identity(
            args,
        )
        located_intent = _find_retest_intent(workspace_id, operation_id)
        if located_intent is not None:
            intent_parent_review_id, intent_event = located_intent
            intent = deepcopy(intent_event.get("payload") or {})
            if (
                intent_parent_review_id != requested_parent_review_id
                or intent.get("key_hash") != key_hash
                or intent.get("request_hash") != request_hash
            ):
                # Do not let a conflicting retry become the cached owner of
                # the original operation's idempotency key.
                raise ValueError("idempotency_key_conflict")
            parent_review_id = intent_parent_review_id
        else:
            intent = {}
            parent_review_id = requested_parent_review_id

        try:
            parent = _project(workspace_id, parent_review_id)
        except ValueError:
            return _blocked("review_not_found", _message("review_not_found"))
        parent_evidence = (
            parent.get("evidence_metadata")
            if isinstance(parent.get("evidence_metadata"), dict)
            else {}
        )
        parent_formal_lineage: dict[str, Any] = {}
        if (
            str((parent.get("project_context") or {}).get("evidence_track") or "")
            == SIM_A_FORMAL
            or str(parent_evidence.get("evidence_policy") or "") == SIM_A_FORMAL
        ):
            try:
                parent_formal_lineage = validate_object_formal_lineage(
                    workspace_id,
                    parent_evidence,
                )
            except FormalLineageError as exc:
                return _blocked(
                    exc.code,
                    f"复测前父审查的正式 promotion 谱系无效：{exc.message}",
                    review_id=parent_review_id,
                )
            if any(
                parent_evidence.get(key) != value
                for key, value in parent_formal_lineage.items()
            ):
                return _blocked(
                    "formal_lineage_metadata_mismatch",
                    "复测前父审查的 promotion 元数据不是规范值",
                    review_id=parent_review_id,
                )
        if intent:
            failed = next(
                (
                    event.get("payload") or {}
                    for event in STORE.events(workspace_id, parent_review_id)
                    if event.get("event_type") == "retest_failed"
                    and (event.get("payload") or {}).get("operation_id") == operation_id
                ),
                None,
            )
            if failed is not None:
                return _blocked(
                    str(failed.get("code") or "retest_operation_failed"),
                    str(failed.get("message") or "复测操作未完成"),
                    review_id=parent_review_id,
                )
        else:
            if not parent.get("validation_complete"):
                return _blocked("review_not_ready", "原校验尚未完成，不能复测")
            if parent.get("pending_retest_operation_ids"):
                return _blocked(
                    "retest_already_in_progress",
                    "已有复测操作未完成；必须使用原 idempotency_key 恢复，不能并行创建新复测链",
                    review_id=parent_review_id,
                    pending_retest_operation_ids=parent["pending_retest_operation_ids"],
                )
            evidence = args.get("remediation_evidence") or []
            if not _evidence_is_precise(evidence):
                return _blocked("retest_evidence_required", "复测必须提供带内容哈希和精确定位的整改证据")
            target = args.get("target")
            if not isinstance(target, dict):
                return _blocked("retest_target_required", "复测必须显式提供新目标版本")
            try:
                normalized = normalize_target(target)
                resolved, target_blockers = _resolve_target(
                    workspace_id,
                    normalized,
                )
            except ValueError as exc:
                return _blocked(str(exc), _message(str(exc)))
            if target_blockers or resolved is None:
                return _blocked(target_blockers[0], "复测目标无法完整解析", blockers=target_blockers)
            if not _retest_target_scope_matches(parent, resolved):
                return _blocked(
                    "retest_target_scope_mismatch",
                    "复测目标必须保持原目标类型、逻辑身份及报告业务域范围",
                )
            if resolved.get("target_sha256") == (parent.get("target") or {}).get("target_sha256"):
                return _blocked("retest_target_not_newer", "复测对象内容必须新于原目标，不能复用相同哈希")
            old_components = [
                str(row.get("rule_pack_id") or "")
                for row in ((parent.get("rule_pack") or {}).get("components") or [])
                if str(row.get("rule_pack_id") or "")
            ]
            requested_packs = list(args.get("rule_pack_ids") or old_components)
            mode = str(args.get("mode") or parent.get("mode") or "quick")
            if mode not in {"quick", "standard", "deep"}:
                return _blocked("review_mode_invalid", "mode 必须为 quick、standard 或 deep")
            prepare_args = {
                "workspace_id": workspace_id,

                "idempotency_key": f"{operation_id}:prepare",
                "target": deepcopy(target),
                "rule_pack_ids": requested_packs,
                "industry_overlays": list(args.get("industry_overlays") or []),
                "project_context": deepcopy(parent.get("project_context") or {}),
                "review_profile": str(parent.get("review_profile") or mode),
                "review_mode": str(parent.get("review_mode") or "internal"),
            }
            parent_basis = {
                "target": deepcopy(parent.get("target") or {}),
                "target_spec": deepcopy(parent.get("target_spec") or {}),
                "rule_pack": deepcopy(parent.get("rule_pack") or {}),
                "mode": str(parent.get("mode") or "quick"),
                "deployment_mode": str(parent.get("deployment_mode") or "enforced"),
                "findings": deepcopy(parent.get("findings") or []),
                "event_chain_hash": str(parent.get("event_chain_hash") or ""),
            }
            intent = {
                "operation_id": operation_id,
                "key_hash": key_hash,
                "request_hash": request_hash,
                "parent_review_id": parent_review_id,
                "parent_basis": parent_basis,
                "resolved_target": {
                    key: deepcopy(resolved.get(key))
                    for key in ("target_type", "target_id", "target_sha256", "target_spec")
                },
                "prepare_args": prepare_args,
                "mode": mode,
                "expected_finding_ids": sorted(
                    str(row.get("finding_id") or "")
                    for row in parent_basis["findings"]
                    if str(row.get("finding_id") or "")
                ),
                "remediation_evidence": deepcopy(evidence),
                "remediation_evidence_hash": sha256_json(evidence),
                **deepcopy(parent_formal_lineage),
                "operation_started_at": utc_now(),
            }
            _append_retest_event_once(
                workspace_id,
                parent_review_id,
                "retest_started",
                intent,

            )

        operation_events = [
            event
            for event in STORE.events(workspace_id, parent_review_id)
            if (event.get("payload") or {}).get("operation_id") == operation_id
        ]
        prepared_event = next(
            (event for event in operation_events if event.get("event_type") == "retest_prepared"),
            None,
        )
        if prepared_event is None:
            prepared = prepare(deepcopy(intent["prepare_args"]))
            if prepared.get("status") == "blocked":
                return _retest_failure(
                    workspace_id,
                    parent_review_id,
                    intent,

                    str(prepared.get("code") or "retest_preparation_failed"),
                    str(prepared.get("message") or "复测审查准备失败"),
                )
            intended_target_sha256 = str((intent.get("resolved_target") or {}).get("target_sha256") or "")
            if str((prepared.get("target") or {}).get("target_sha256") or "") != intended_target_sha256:
                return _retest_failure(
                    workspace_id,
                    parent_review_id,
                    intent,

                    "retest_target_changed_during_operation",
                    "复测目标在操作恢复期间发生变化，已按失败关闭",
                )
            prepared_payload = {
                "operation_id": operation_id,
                "review_preparation_id": str(prepared.get("review_preparation_id") or ""),
                "target_sha256": intended_target_sha256,
            }
            prepared_event = _append_retest_event_once(
                workspace_id,
                parent_review_id,
                "retest_prepared",
                prepared_payload,

            )
        prepared_payload = prepared_event.get("payload") or {}
        preparation_id = str(prepared_payload.get("review_preparation_id") or "")

        operation_events = [
            event
            for event in STORE.events(workspace_id, parent_review_id)
            if (event.get("payload") or {}).get("operation_id") == operation_id
        ]
        child_started_event = next(
            (event for event in operation_events if event.get("event_type") == "retest_child_started"),
            None,
        )
        if child_started_event is None:
            parent_basis = intent.get("parent_basis") or {}
            started = start({
                "workspace_id": workspace_id,

                "idempotency_key": f"{operation_id}:start",
                "review_preparation_id": preparation_id,
                "mode": str(intent.get("mode") or "quick"),
                "execution": "sync",
                "deployment_mode": str(
                    (intent.get("parent_basis") or {}).get("deployment_mode") or "enforced"
                ),
            })
            if started.get("status") == "blocked":
                return _retest_failure(
                    workspace_id,
                    parent_review_id,
                    intent,

                    str(started.get("code") or "retest_child_review_failed"),
                    str(started.get("message") or "复测子审查创建失败"),
                )
            child_review_id = str(started.get("review_id") or "")
            try:
                child = _project(workspace_id, child_review_id, check_freshness=False)
            except ValueError:
                return _retest_failure(
                    workspace_id,
                    parent_review_id,
                    intent,

                    "retest_child_review_unavailable",
                    _message("retest_child_review_unavailable"),
                )
            if parent_formal_lineage:
                child_evidence = (
                    child.get("evidence_metadata")
                    if isinstance(child.get("evidence_metadata"), dict)
                    else {}
                )
                try:
                    child_lineage = validate_object_formal_lineage(
                        workspace_id,
                        child_evidence,
                    )
                except FormalLineageError as exc:
                    return _retest_failure(
                        workspace_id,
                        parent_review_id,
                        intent,
                        exc.code,
                        f"复测子审查的正式 promotion 谱系无效：{exc.message}",
                    )
                if child_lineage != parent_formal_lineage:
                    return _retest_failure(
                        workspace_id,
                        parent_review_id,
                        intent,
                        "formal_lineage_mixed_promotions",
                        "复测子审查与父审查来自不同 promotion",
                    )
            intended_target_sha256 = str((intent.get("resolved_target") or {}).get("target_sha256") or "")
            if str((child.get("target") or {}).get("target_sha256") or "") != intended_target_sha256:
                return _retest_failure(
                    workspace_id,
                    parent_review_id,
                    intent,

                    "retest_child_target_mismatch",
                    "复测子审查未绑定操作意图中的目标哈希",
                )
            child_started_event = _append_retest_event_once(
                workspace_id,
                parent_review_id,
                "retest_child_started",
                {
                    "operation_id": operation_id,
                    "review_preparation_id": preparation_id,
                    "child_review_id": child_review_id,
                    "child_target_sha256": intended_target_sha256,
                    **deepcopy(parent_formal_lineage),
                },

            )
        child_review_id = str((child_started_event.get("payload") or {}).get("child_review_id") or "")
        try:
            child = _project(workspace_id, child_review_id, check_freshness=False)
        except ValueError:
            return _retest_failure(
                workspace_id,
                parent_review_id,
                intent,

                "retest_child_review_unavailable",
                _message("retest_child_review_unavailable"),
            )
        if str((parent.get("target") or {}).get("target_type") or "") == "review_package":
            return _ok(
                status="accepted",
                code="retest_assessment_required",
                parent_review_id=parent_review_id,
                retest_review_id=child_review_id,
                review_id=child_review_id,
                review_status=child["review_status"],
                validation_status=child["validation_status"],
                validation_complete=child["validation_complete"],
                overall_verdict="incomplete",
                closed_finding_ids=[],
                remaining_finding_ids=list(intent.get("expected_finding_ids") or []),
                resource_uris=[
                    _review_uri(workspace_id, parent_review_id),
                    _review_uri(workspace_id, child_review_id),
                ],
                blockers=["retest_assessment_required"],
                next_actions=["在复测子审查重新提交受影响领域 Assessment、确认七域并调用 review_finalize"],
            )
        parent_basis = intent.get("parent_basis") or {}
        old_findings = list(parent_basis.get("findings") or [])
        new_by_match = {_finding_match_key(row): row for row in child.get("findings") or []}
        new_by_rule: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for finding in child.get("findings") or []:
            new_by_rule.setdefault(
                (
                    str(finding.get("rule_id") or ""),
                    str(finding.get("category") or ""),
                ),
                [],
            ).append(finding)
        executed_rules = set((child.get("coverage") or {}).get("executed_rules") or [])
        closed: list[str] = []
        remaining: list[str] = []
        for old_finding in old_findings:
            old_id = str(old_finding.get("finding_id") or "")
            new_finding = new_by_match.get(_finding_match_key(old_finding))
            if new_finding is None:
                conservative_matches = new_by_rule.get(
                    (
                        str(old_finding.get("rule_id") or ""),
                        str(old_finding.get("category") or ""),
                    ),
                    [],
                )
                new_finding = conservative_matches[0] if conservative_matches else None
            coverage_rule_id = _finding_coverage_rule_id(old_finding)
            can_conclude_absence = coverage_rule_id in executed_rules and not any(
                str(reason) == f"rule_not_executed:{coverage_rule_id}"
                for reason in child.get("incomplete_reasons") or []
            )
            retest_passed = new_finding is None and can_conclude_absence
            if retest_passed:
                closed.append(old_id)
                after_value = "finding_not_reproduced"
            else:
                remaining.append(old_id)
                after_value = deepcopy((new_finding or {}).get("actual"))
            _append_retest_event_once(
                workspace_id,
                parent_review_id,
                "finding_retested",
                {
                    "operation_id": operation_id,
                    "finding_id": old_id,
                    "new_status": "remediation_in_progress",
                    "retest_passed": retest_passed,
                    "parent_review_id": parent_review_id,
                    "retest_review_id": child_review_id,
                    "before_value": deepcopy(old_finding.get("actual")),
                    "after_value": after_value,
                    "remediation_evidence": deepcopy(intent.get("remediation_evidence") or []),
                    "same_rule_pack": (
                        (child.get("rule_pack") or {}).get("content_hash")
                        == (parent_basis.get("rule_pack") or {}).get("content_hash")
                    ),
                    "retested_at": intent.get("operation_started_at"),
                },

                identity_fields=("finding_id",),
            )
        closed = sorted(set(closed))
        remaining = sorted(set(remaining))
        link = {
            "operation_id": operation_id,
            "parent_review_id": parent_review_id,
            "child_review_id": child_review_id,
            "parent_target_sha256": (parent_basis.get("target") or {}).get("target_sha256"),
            "child_target_sha256": (child.get("target") or {}).get("target_sha256"),
            "parent_rule_pack_hash": (parent_basis.get("rule_pack") or {}).get("content_hash"),
            "child_rule_pack_hash": (child.get("rule_pack") or {}).get("content_hash"),
            "completed": True,
            "closed_finding_ids": closed,
            "remaining_finding_ids": remaining,
            "remediation_evidence": deepcopy(intent.get("remediation_evidence") or []),
            "remediation_evidence_hash": str(intent.get("remediation_evidence_hash") or ""),
            **deepcopy(parent_formal_lineage),
            "retested_at": intent.get("operation_started_at"),
        }
        _append_retest_event_once(
            workspace_id,
            parent_review_id,
            "retest_linked",
            link,

        )
        _append_retest_event_once(
            workspace_id,
            child_review_id,
            "retest_linked",
            link,

        )
        completion = {
            "operation_id": operation_id,
            "parent_review_id": parent_review_id,
            "child_review_id": child_review_id,
            "expected_finding_ids": list(intent.get("expected_finding_ids") or []),
            "link_hash": sha256_json(link),
            **deepcopy(parent_formal_lineage),
            "completed": True,
        }
        _append_retest_event_once(
            workspace_id,
            child_review_id,
            "retest_completed",
            {**completion, "side": "child"},

        )
        _append_retest_event_once(
            workspace_id,
            parent_review_id,
            "retest_completed",
            {**completion, "side": "parent"},

        )
        child = _project(workspace_id, child_review_id, check_freshness=False)
        return _ok(
            parent_review_id=parent_review_id,
            retest_review_id=child_review_id,
            review_id=child_review_id,
            review_status=child["review_status"],
            validation_status=child["validation_status"],
            validation_complete=child["validation_complete"],
            overall_verdict=child["overall_verdict"],
            closed_finding_ids=closed,
            remaining_finding_ids=remaining,
            resource_uris=[
                _review_uri(workspace_id, parent_review_id),
                _review_uri(workspace_id, child_review_id),
            ],
            blockers=child["blockers"],
            next_actions=_next_actions(child),
        )

    return _write("review_retest", args, execute)
