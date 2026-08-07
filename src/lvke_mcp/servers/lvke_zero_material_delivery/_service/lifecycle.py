"""Delivery lifecycle routes, assumption confirmation and resource surface."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any


from lvke_mcp.runtime.storage import (
    paginate_resource_entries,
    require_safe_id,
)

from .assumptions import _build_assumption_package
from .base import (
    ASSUMPTION_PROFILE_VERSION,
    ASSUMPTION_REGISTER_STORE,
    ASSUMPTION_STORE,
    EVIDENCE_MANIFEST_STORE,
    GAP_REGISTER_STORE,
    INTENT_STORE,
    MANIFEST_STORE,
    REPORT_STORE,
    RUN_STORE,
    SERVICE_NAME,
    SERVICE_VERSION,
    _RESOURCE_STORES,
    _blocked,
    _envelope,
    _idempotent_mutation,
    _view,
)
from .orchestration import execute
from .routing import _new_run, _planned_run_id


def start(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    idempotency_key = str(args.get("idempotency_key") or "")
    request_payload = {"delivery_run_id": run_id}

    def mutation() -> dict[str, Any]:
        run_record = RUN_STORE.get(workspace_id, run_id)
        if run_record is None:
            return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
        run = _view(run_record, "delivery_run_id")
        if run["stage"] == "cancelled":
            return _blocked(
                "delivery_run_cancelled",
                "该运行已取消，须调用 delivery_transition(operation=\"resume\") 创建恢复快照",
            )
        intent_record = INTENT_STORE.get(
            workspace_id,
            str(run["intent_id"]),
        )
        if intent_record is None:
            return _blocked("delivery_intent_not_found", "运行引用的 DeliveryIntent 不存在")
        intent = _view(intent_record, "delivery_intent_id")
        if not dict(intent.get("industry") or {}).get("resolved"):
            return _blocked(
                "missing_route",
                "行业路线未解析，不能生成行业假设",
                status="missing_inputs",
            )
        existing_package_id = str(run.get("assumption_package_id") or "")
        assumption = (
            ASSUMPTION_STORE.get(
                workspace_id,
                existing_package_id,
            )
            if existing_package_id
            else None
        )
        if assumption is None:
            assumption_payload = _build_assumption_package(intent)
            assumption = ASSUMPTION_STORE.put(
                workspace_id,
                assumption_payload,
                producer=f"{SERVICE_NAME}.delivery_start",
                status="ok",
                source_ids=[intent["delivery_intent_id"]],
                basis={
                    "intent_id": intent["delivery_intent_id"],
                    "profile_version": ASSUMPTION_PROFILE_VERSION,
                },
            )
        assumption_view = _view(assumption, "assumption_package_id")
        domain = execute(
            workspace_id,
            intent,
            assumption_view,
            operation_key=idempotency_key,
        )
        from lvke_mcp.servers.lvke_zero_material_delivery.artifact_delivery import (
            build_delivery_artifacts,
        )

        planned_delivery_run_id = _planned_run_id(
            workspace_id,
            run_id,
            idempotency_key,
        )
        delivery_artifacts = build_delivery_artifacts(
            workspace_id,
            intent,
            assumption_view,
            run,
            domain,
            stores={
                "report": REPORT_STORE,
                "assumption_register": ASSUMPTION_REGISTER_STORE,
                "gap_register": GAP_REGISTER_STORE,
                "evidence_manifest": EVIDENCE_MANIFEST_STORE,
                "manifest": MANIFEST_STORE,
            },
            service_version=SERVICE_VERSION,
            delivery_run_id=planned_delivery_run_id,
        )
        object_refs = {
            "assumption_package_id": assumption["object_id"],
            **{
                str(key): str(value)
                for key, value in dict(domain.get("object_refs") or {}).items()
                if value
            },
            **dict(delivery_artifacts.get("object_refs") or {}),
        }
        artifact_uris = sorted(
            {
                *list(domain.get("resource_uris") or []),
                *list(delivery_artifacts.get("resource_uris") or []),
            }
        )
        blockers = [
            *list(domain.get("blockers") or []),
            *list(delivery_artifacts.get("blockers") or []),
        ]
        technical_preview_ready = (
            str(domain.get("stage") or "") == "tables_ready"
            and not delivery_artifacts.get("blockers")
        )
        next_run = _new_run(
            workspace_id,
            intent_id=intent["delivery_intent_id"],
            assumption_package_id=assumption["object_id"],
            previous_run_id=run_id,
            stage="preview_ready" if technical_preview_ready else str(domain.get("stage") or "assumptions_ready"),
            blockers=blockers,
            status_reason=str(domain.get("status") or "upstream_partial"),
            object_refs=object_refs,
            artifact_uris=artifact_uris,
            manifest_uri=str(delivery_artifacts.get("manifest_uri") or ""),
            domain_results={
                "research_status": str((domain.get("research") or {}).get("status") or ""),
                "finance_status": str((domain.get("finance_run") or {}).get("status") or ""),
                "tables_status": str((domain.get("tables") or {}).get("status") or ""),
                "csv_status": str((domain.get("csv_export") or {}).get("status") or ""),
                "xlsx_status": str((domain.get("xlsx_export") or {}).get("status") or ""),
                "report_preparation_status": str((domain.get("report_preparation") or {}).get("status") or ""),
                "technical_preview_ready": technical_preview_ready,
            },
            object_id=planned_delivery_run_id,
        )
        return _envelope(
            technical_preview_ready,
            "partial" if blockers else "ok",
            warnings=[
                "行业场景仅作为受控假设种子，不是项目证据",
                *list(domain.get("warnings") or []),
            ],
            blockers=blockers,
            next_actions=(
                ["提交公开研究结果后继续规划与正式报告准备；或先确认关键假设并重算"]
                if blockers
                else ["读取交付 Resources；正式发布资格仍保持阻断"]
            ),
            resource_uris=sorted(
                {
                    assumption["resource_uri"],
                    next_run["resource_uri"],
                    *artifact_uris,
                }
            ),
            assumption_package=assumption_view,
            delivery_run=_view(next_run, "delivery_run_id"),
            domain_status=str(domain.get("status") or ""),
            validation_complete=False,
            input_evidence_complete=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="delivery_start",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )


def get_delivery(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    object_id = require_safe_id(args.get("object_id"), "object_id")
    for store, object_type, id_field in _RESOURCE_STORES:
        record = store.get(workspace_id, object_id)
        if record is not None:
            view = _view(record, id_field)
            return _envelope(
                True,
                "ok",
                resource_uris=[record["resource_uri"]],
                object_type=object_type,
                object=view,
                validation_complete=False,
                input_evidence_complete=False,
            )
    return _blocked("delivery_object_not_found", "未找到指定交付对象")


def status(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    record = RUN_STORE.get(workspace_id, run_id)
    if record is None:
        return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
    run = _view(record, "delivery_run_id")
    return _envelope(
        True,
        "ok",
        resource_uris=[record["resource_uri"]],
        delivery_run=run,
        stage=run["stage"],
        progress=_stage_progress(str(run["stage"])),
        resume_token=record["content_hash"],
        validation_complete=False,
        input_evidence_complete=False,
    )


def _stage_progress(stage: str) -> int:
    stages = [
        "received", "intent_resolved", "researching", "assumptions_ready",
        "planning_ready", "finance_ready", "tables_ready", "report_ready",
        "preview_ready", "awaiting_confirmation", "confirmed_estimate_ready",
    ]
    if stage == "cancelled":
        return 0
    try:
        return round(stages.index(stage) * 100 / (len(stages) - 1))
    except ValueError:
        return 0


def list_assumptions(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    package_id = require_safe_id(args.get("assumption_package_id"), "assumption_package_id")
    record = ASSUMPTION_STORE.get(workspace_id, package_id)
    if record is None:
        return _blocked("assumption_package_not_found", "未找到指定 AssumptionPackage")
    package = _view(record, "assumption_package_id")
    fields = sorted(
        [dict(item) for item in package.get("fields") or []],
        key=lambda item: (
            -int(item.get("confirmation_priority_score") or 0),
            str(item.get("name")),
        ),
    )
    limit = max(5, min(int(args.get("limit") or 10), 10))
    return _envelope(
        True,
        "ok",
        resource_uris=[record["resource_uri"]],
        assumption_package_id=package_id,
        assumptions=fields,
        confirmation_items=[item for item in fields if not item.get("confirmed")][:limit],
        validation_complete=False,
        input_evidence_complete=False,
    )


def confirm_assumptions(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    package_id = require_safe_id(args.get("assumption_package_id"), "assumption_package_id")
    idempotency_key = str(args.get("idempotency_key") or "")
    confirmations = [dict(item) for item in args.get("confirmations") or []]
    request_payload = {
        "assumption_package_id": package_id,
        "confirmations": confirmations,
    }

    def mutation() -> dict[str, Any]:
        prior = ASSUMPTION_STORE.get(workspace_id, package_id)
        if prior is None:
            return _blocked("assumption_package_not_found", "未找到指定 AssumptionPackage")
        prior_payload = dict(prior.get("payload") or {})
        known = {str(item.get("name")): dict(item) for item in prior_payload.get("fields") or []}
        unknown = sorted({str(item.get("name") or "") for item in confirmations} - set(known))
        if unknown:
            return _blocked(
                "unknown_assumption_field",
                "确认请求包含未知假设字段",
                unknown_fields=unknown,
            )
        for confirmation in confirmations:
            name = str(confirmation["name"])
            current = known[name]
            current.update(
                {
                    "value": confirmation["value"],
                    "source_type": "user_confirmed",
                    "source_ref": str(confirmation.get("source_ref") or "user_confirmation"),
                    "method": "user_override",
                    "confidence": 1.0,
                    "confirmed": True,
                    "confirmation_note": str(confirmation.get("note") or ""),
                    "validation_condition": "已确认参数仍需与后续原始材料进行 hash 和数值一致性校验",
                }
            )
        payload = {
            **prior_payload,
            "revision": int(prior_payload.get("revision") or 1) + 1,
            "previous_assumption_package_id": package_id,
            "fields": [known[str(item.get("name"))] for item in prior_payload.get("fields") or []],
            "confirmation_status": "partially_confirmed" if any(
                not item.get("confirmed") for item in known.values()
            ) else "confirmed",
            "validation_complete": False,
            "input_evidence_complete": False,
        }
        revised = ASSUMPTION_STORE.put(
            workspace_id,
            payload,
            producer=f"{SERVICE_NAME}.delivery_confirm_assumptions",
            status="ok",
            source_ids=[package_id],
            basis=request_payload,
        )
        source_run = next(
            (
                record for record in reversed(RUN_STORE.list(workspace_id))
                if str((record.get("payload") or {}).get("assumption_package_id") or "") == package_id
            ),
            None,
        )
        intent_id = str((source_run.get("payload") or {}).get("intent_id") or "") if source_run else ""
        if not intent_id:
            return _blocked("delivery_run_lineage_missing", "假设包缺少 DeliveryRun lineage")
        next_run = _new_run(
            workspace_id,
            intent_id=intent_id,
            assumption_package_id=revised["object_id"],
            previous_run_id=str(source_run["object_id"]),
            stage="assumptions_ready",
            blockers=["recalculation_required"],
            status_reason="confirmed_inputs_require_new_domain_objects",
            object_refs={"assumption_package_id": revised["object_id"]},
        )
        return _envelope(
            True,
            "accepted",
            warnings=["用户确认值仍不是合同、测绘、报价或权属证据"],
            blockers=["recalculation_required"],
            next_actions=["使用新的 delivery_run_id 重算财务、十三表和报告"],
            resource_uris=[revised["resource_uri"], next_run["resource_uri"]],
            assumption_package=_view(revised, "assumption_package_id"),
            delivery_run=_view(next_run, "delivery_run_id"),
            validation_complete=False,
            input_evidence_complete=False,
        )

    confirmation = _idempotent_mutation(
        workspace_id,
        operation="delivery_confirm_assumptions",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )
    if not confirmation.get("success"):
        return confirmation
    recalculation_run = dict(confirmation.get("delivery_run") or {})
    recalculation_run_id = str(recalculation_run.get("delivery_run_id") or "")
    if not recalculation_run_id:
        return _blocked(
            "automatic_recalculation_lineage_missing",
            "确认已保存，但未形成可自动重算的 DeliveryRun",
            assumption_package=confirmation.get("assumption_package"),
        )
    recalculation_key = "zmd-auto-recalc-" + hashlib.sha256(
        f"{idempotency_key}:{recalculation_run_id}".encode("utf-8")
    ).hexdigest()[:32]
    recalculated = start(
        {
            "workspace_id": workspace_id,
            "delivery_run_id": recalculation_run_id,
            "idempotency_key": recalculation_key,
        }
    )
    return {
        **recalculated,
        "assumption_package": confirmation.get("assumption_package"),
        "confirmation_run": recalculation_run,
        "automatic_recalculation": True,
        "confirmation_idempotent_replay": bool(confirmation.get("idempotent_replay")),
    }


def cancel(args: dict[str, Any]) -> dict[str, Any]:
    return _transition_control(args, operation="cancel")


def resume(args: dict[str, Any]) -> dict[str, Any]:
    return _transition_control(args, operation="resume")


def _transition_control(args: dict[str, Any], *, operation: str) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    idempotency_key = str(args.get("idempotency_key") or "")
    reason = str(args.get("reason") or "").strip()
    request_payload = {"delivery_run_id": run_id, "reason": reason}

    def mutation() -> dict[str, Any]:
        prior = RUN_STORE.get(workspace_id, run_id)
        if prior is None:
            return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
        payload = dict(prior.get("payload") or {})
        stage = str(payload.get("stage") or "")
        if operation == "cancel" and stage == "cancelled":
            return _blocked("delivery_run_already_cancelled", "该运行已经取消")
        if operation == "resume" and stage != "cancelled":
            return _blocked("delivery_run_not_cancelled", "只有 cancelled 运行可以恢复")
        next_stage = "cancelled" if operation == "cancel" else str(payload.get("resume_stage") or "received")
        next_run = _new_run(
            workspace_id,
            intent_id=str(payload.get("intent_id") or ""),
            assumption_package_id=str(payload.get("assumption_package_id") or ""),
            previous_run_id=run_id,
            stage=next_stage,
            blockers=[] if operation == "resume" else ["cancelled"],
            resume_stage=stage if operation == "cancel" else "",
            status_reason=reason or operation,
            object_refs=dict(payload.get("object_refs") or {}),
        )
        return _envelope(
            True,
            "accepted",
            blockers=[] if operation == "resume" else ["cancelled"],
            next_actions=["调用 delivery_start 继续运行"] if operation == "resume" else [],
            resource_uris=[next_run["resource_uri"]],
            delivery_run=_view(next_run, "delivery_run_id"),
            validation_complete=False,
            input_evidence_complete=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation=f"delivery_{operation}",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )


def get_artifacts(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    record = RUN_STORE.get(workspace_id, run_id)
    if record is None:
        return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
    run = _view(record, "delivery_run_id")
    refs = dict(run.get("object_refs") or {})
    uris = [record["resource_uri"]]
    for ref in refs.values():
        for store, _object_type, _id_field in _RESOURCE_STORES:
            linked = store.get(workspace_id, str(ref))
            if linked is not None:
                uris.append(str(linked["resource_uri"]))
                break
    artifact_uris = [str(item) for item in run.get("artifact_uris") or []]
    return _envelope(
        True,
        "ok" if artifact_uris else "empty",
        warnings=[] if artifact_uris else ["当前运行尚未生成财务、十三表或报告工件"],
        resource_uris=sorted(set([*uris, *artifact_uris])),
        artifacts=artifact_uris,
        manifest_uri=str(run.get("manifest_uri") or ""),
        validation_complete=False,
        input_evidence_complete=False,
    )


def list_resources(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    selected = str(args.get("resource_type") or "")
    entries: list[dict[str, Any]] = []
    for store, object_type, _id_field in _RESOURCE_STORES:
        if selected and selected != object_type:
            continue
        for record in store.list(workspace_id):
            entries.append(
                {
                    "uri": record["resource_uri"],
                    "name": f"{object_type} {record['object_id']}",
                    "mime_type": "application/json",
                    "object_type": object_type,
                    "content_hash": record["content_hash"],
                }
            )
    try:
        page = paginate_resource_entries(
            entries,
            cursor=str(args.get("cursor") or ""),
            limit=int(args.get("limit") or 50),
        )
    except ValueError as exc:
        return _blocked(str(exc), "Resource cursor 无效或资源集合已变化")
    return _envelope(
        True,
        "ok",
        resource_uris=[str(item["uri"]) for item in page.get("resources") or []],
        **page,
    )


def read_resource(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    uri = str(args.get("uri") or "")
    resolved = resolve_resource(uri)
    if resolved is None:
        return _blocked("resource_not_found", "Resource 不存在")
    content, mime_type = resolved
    if isinstance(content, bytes):
        if f"/workspaces/{workspace_id}/" not in uri:
            return _blocked("resource_scope_mismatch", "Resource 不属于指定 workspace")
        return _envelope(
            True,
            "ok",
            resource_uris=[uri],
            uri=uri,
            mime_type=mime_type,
            content_hash="sha256:" + hashlib.sha256(content).hexdigest(),
            encoding="base64",
            content_base64=base64.b64encode(content).decode("ascii"),
        )
    loaded = json.loads(content)
    if str(loaded.get("workspace_id") or "") != workspace_id:
        return _blocked("resource_scope_mismatch", "Resource 不属于指定 workspace")
    return _envelope(
        True,
        "ok",
        resource_uris=[uri],
        uri=uri,
        mime_type=mime_type,
        content_hash=str(loaded.get("content_hash") or ""),
        resource=loaded,
    )


def resolve_resource(
    uri: str,
) -> tuple[str | bytes, str] | None:
    from lvke_mcp.servers.lvke_zero_material_delivery.artifact_delivery import (
        resolve_report_file,
    )

    report_file = resolve_report_file(
        uri,
        report_store=REPORT_STORE,
    )
    if report_file is not None:
        return report_file
    if uri.startswith("lvke://finance-tables/workspaces/"):
        remainder = uri.removeprefix("lvke://finance-tables/workspaces/")
        workspace_id = remainder.split("/", 1)[0]
        try:
            require_safe_id(workspace_id, "workspace_id")
        except ValueError:
            return None
        from lvke_mcp.domains.finance import tables_service as finance_tables

        return finance_tables.resolve_resource(
            uri,
            workspace_id,
        )
    for store, _object_type, _id_field in _RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None:
            return json.dumps(record, ensure_ascii=False, indent=2), "application/json"
    return None
