"""Stateful, immutable orchestration for feasibility-study delivery runs."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from filelock import FileLock

from lvke_mcp.runtime.storage import (
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
    utc_now,
)
from lvke_mcp.runtime.workspace import workspace_root
from lvke_mcp.servers.lvke_feasibility_delivery.contracts import (
    DELIVERY_MODES,
    EVIDENCE_POLICIES,
    RELEASE_SCOPES,
    RUN_STATUSES,
    STAGES,
    STAGE_STATUSES,
    empty_stages,
)
from lvke_mcp.servers.lvke_feasibility_delivery.store import (
    CHECKPOINT_STORE,
    IDEMPOTENCY_STORE,
    RELEASE_STORE,
    RESOURCE_STORES,
    RUN_STORE,
)


def _envelope(
    success: bool,
    status: str,
    *,
    code: str = "",
    message: str = "",
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    next_actions: list[str] | None = None,
    resource_uris: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
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


def _blocked(code: str, message: str, *, next_actions: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return _envelope(
        False,
        "blocked",
        code=code,
        message=message,
        blockers=[code],
        next_actions=next_actions,
        **extra,
    )


def _lock(workspace_id: str) -> FileLock:
    directory = workspace_root(require_safe_id(workspace_id, "workspace_id")) / "mcp_objects" / "feasibility-delivery"
    directory.mkdir(parents=True, exist_ok=True)
    return FileLock(str(directory / ".idempotency.lock"), timeout=30)


def _idempotent(
    workspace_id: str,
    *,
    operation: str,
    idempotency_key: str,
    request_payload: dict[str, Any],
    mutation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    key_hash = "sha256:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    request_hash = sha256_json(request_payload)
    with _lock(workspace_id):
        for record in IDEMPOTENCY_STORE.list(workspace_id):
            payload = record.get("payload") or {}
            if payload.get("operation") != operation or payload.get("key_hash") != key_hash:
                continue
            if payload.get("request_hash") != request_hash:
                return _blocked("idempotency_conflict", "同一幂等键已用于不同请求")
            replay = dict(payload.get("response") or {})
            replay["idempotent_replay"] = True
            return replay
        response = mutation()
        IDEMPOTENCY_STORE.put(
            workspace_id,
            {
                "operation": operation,
                "key_hash": key_hash,
                "request_hash": request_hash,
                "response": response,
            },
            producer=f"lvke-feasibility-delivery.{operation}",
        )
        return response


def _view(record: dict[str, Any], id_field: str = "delivery_run_id") -> dict[str, Any]:
    return {
        **dict(record.get("payload") or {}),
        id_field: record["object_id"],
        "workspace_id": record["workspace_id"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "created_at": record["created_at"],
        "resource_uri": record["resource_uri"],
    }


def _record(workspace_id: str, delivery_run_id: str) -> dict[str, Any] | None:
    return RUN_STORE.get(require_safe_id(workspace_id, "workspace_id"), delivery_run_id)


def _stage_index(stage: str) -> int:
    return STAGES.index(stage)


def _run_response(record: dict[str, Any], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    run = _view(record)
    payload = {
        "delivery_run": run,
        "delivery_run_id": record["object_id"],
        "current_stage": run.get("current_stage"),
        "preview_only": str(run.get("delivery_mode") or "") == "estimate_preview",
        "resource_uris": [record["resource_uri"]],
    }
    if extra:
        payload.update(extra)
    return _envelope(True, str(run.get("status") or "in_progress"), **payload)


def start(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    mode = str(args.get("delivery_mode") or "")
    if mode not in DELIVERY_MODES:
        return _blocked("delivery_mode_invalid", "delivery_mode 必须是 estimate_preview、review_candidate 或 formal_release")
    project_context_id = str(args.get("project_context_id") or "")
    evidence_policy = str(args.get("evidence_policy") or "formal_evidence")
    release_scope = str(args.get("release_scope") or "project_delivery")
    if evidence_policy not in EVIDENCE_POLICIES:
        return _blocked("evidence_policy_invalid", "evidence_policy 不受支持")
    if release_scope not in RELEASE_SCOPES:
        return _blocked("release_scope_invalid", "release_scope 必须是 process_acceptance 或 project_delivery")
    if mode == "estimate_preview" and release_scope == "project_delivery":
        release_scope = "process_acceptance"
    request = {
        "workspace_id": workspace_id,
        "delivery_mode": mode,
        "project_context_id": project_context_id,
        "evidence_policy": evidence_policy,
        "release_scope": release_scope,
        "project_fact_certified": bool(args.get("project_fact_certified", evidence_policy == "formal_evidence")),
        "reconstructed_source_ids": list(args.get("reconstructed_source_ids") or []),
        "unresolved_inputs": list(args.get("unresolved_inputs") or []),
        "release_limitations": list(args.get("release_limitations") or []),
    }

    def create() -> dict[str, Any]:
        stages = empty_stages()
        current_stage = "project"
        if project_context_id:
            stages["project"].update({
                "status": "completed",
                "output_refs": [project_context_id],
                "basis_hash": sha256_json({"project_context_id": project_context_id}),
                "next_actions": [],
            })
            stages["research"]["status"] = "in_progress"
            current_stage = "research"
        root_run_id = "fdr_" + sha256_json(request).removeprefix("sha256:")[:24]
        payload = {
            "root_run_id": root_run_id,
            "parent_run_id": "",
            "delivery_mode": mode,
            "status": "in_progress",
            "current_stage": current_stage,
            "project_context_id": project_context_id,
            "evidence_policy": evidence_policy,
            "release_scope": release_scope,
            "project_fact_certified": request["project_fact_certified"],
            "reconstructed_source_ids": request["reconstructed_source_ids"],
            "unresolved_inputs": request["unresolved_inputs"],
            "release_limitations": request["release_limitations"],
            "stages": stages,
            "lineage": {},
            "next_actions": [],
            "created_at": utc_now(),
        }
        record = RUN_STORE.put(
            workspace_id,
            payload,
            producer="lvke-feasibility-delivery.feasibility_start",
            basis=request,
            object_id=root_run_id,
        )
        return _run_response(record)

    return _idempotent(
        workspace_id,
        operation="feasibility_start",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request,
        mutation=create,
    )


def status(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    record = _record(workspace_id, str(args.get("delivery_run_id") or ""))
    if record is None:
        return _blocked("delivery_run_not_found", "可研交付运行不存在")
    return _run_response(record)


def stage(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    delivery_run_id = str(args.get("delivery_run_id") or "")
    record = _record(workspace_id, delivery_run_id)
    if record is None:
        return _blocked("delivery_run_not_found", "可研交付运行不存在")
    stage_name = str(args.get("stage") or "")
    stage_status = str(args.get("status") or "")
    if stage_name not in STAGES or stage_name == "released":
        return _blocked("stage_invalid", "stage 不是可更新的业务阶段")
    if stage_status not in STAGE_STATUSES:
        return _blocked("stage_status_invalid", "stage status 不受支持")
    expected_basis_hash = str(args.get("expected_basis_hash") or "")
    if expected_basis_hash and expected_basis_hash != str(record.get("basis_hash") or ""):
        return _blocked("basis_conflict", "当前交付快照已变化，请重新读取 feasibility_status")
    current_stage = str((record.get("payload") or {}).get("current_stage") or "project")
    current_index = _stage_index(current_stage)
    target_index = _stage_index(stage_name)
    reopen = bool(args.get("reopen", False))
    if target_index > current_index:
        return _blocked("stage_order_violation", f"当前阶段为 {current_stage}，不能跳到 {stage_name}")
    if target_index < current_index and not reopen:
        return _blocked("stage_not_current", "修改已完成阶段必须显式设置 reopen=true")
    if stage_status == "completed" and not args.get("output_refs") and stage_name != "project":
        return _blocked("stage_output_required", "completed 阶段必须提供 output_refs")
    if stage_status == "completed" and not str(
        args.get("basis_hash") or args.get("stage_basis_hash") or ""
    ).strip():
        return _blocked("stage_basis_hash_required", "completed 阶段必须提供 basis_hash")

    request = {
        "delivery_run_id": delivery_run_id,
        "stage": stage_name,
        "status": stage_status,
        "input_refs": list(args.get("input_refs") or []),
        "output_refs": list(args.get("output_refs") or []),
        "basis_hash": str(args.get("basis_hash") or args.get("stage_basis_hash") or ""),
        "warnings": list(args.get("warnings") or []),
        "blockers": list(args.get("blockers") or []),
        "next_actions": list(args.get("next_actions") or []),
        "reopen": reopen,
    }

    def update() -> dict[str, Any]:
        payload = json.loads(json.dumps(record.get("payload") or {}))
        stages = payload.setdefault("stages", empty_stages())
        if reopen or target_index < current_index:
            for downstream in STAGES[target_index + 1 :]:
                if downstream != "released":
                    stages[downstream]["status"] = "stale"
                    stages[downstream]["warnings"] = ["upstream_stage_reopened"]
                    stages[downstream]["blockers"] = ["downstream_invalidated"]
        stage_record = dict(stages[stage_name])
        stage_record.update({
            "status": stage_status,
            "input_refs": request["input_refs"],
            "output_refs": request["output_refs"],
            "basis_hash": request["basis_hash"],
            "warnings": request["warnings"],
            "blockers": request["blockers"],
            "next_actions": request["next_actions"],
            "updated_from_run_id": delivery_run_id,
        })
        stages[stage_name] = stage_record
        payload["stages"] = stages
        payload["current_stage"] = stage_name
        payload["status"] = stage_status if stage_status in {"partial", "blocked", "stale"} else "in_progress"
        payload["next_actions"] = request["next_actions"]
        if stage_status == "completed":
            next_stage = STAGES[target_index + 1] if target_index + 1 < len(STAGES) else "released"
            payload["current_stage"] = next_stage
            if next_stage != "released":
                stages[next_stage]["status"] = "in_progress"
            payload["status"] = "completed" if next_stage == "released" else "in_progress"
        if stage_name == "project" and request["output_refs"]:
            payload["project_context_id"] = request["output_refs"][0]
        lineage = dict(payload.get("lineage") or {})
        for ref in request["output_refs"]:
            if ":" in str(ref):
                key, value = str(ref).split(":", 1)
                lineage[key] = value
        payload["lineage"] = lineage
        child = RUN_STORE.put(
            workspace_id,
            payload,
            producer="lvke-feasibility-delivery.feasibility_stage",
            basis={"parent_run_id": delivery_run_id, "request": request},
        )
        stale_stages = [name for name, item in stages.items() if item.get("status") == "stale"]
        return _run_response(child, extra={"stale_stages": stale_stages, "parent_run_id": delivery_run_id})

    return _idempotent(
        workspace_id,
        operation="feasibility_stage",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request,
        mutation=update,
    )


_NEXT_TOOLS: dict[str, list[str]] = {
    "project": ["project_context_create", "project_context_validate"],
    "research": ["dr_prepare", "dr_start", "data_discover", "data_fetch", "analysis_build_evidence_pack"],
    "market": ["planning_prepare_market_case", "planning_validate_market_case", "planning_confirm_market_case"],
    "option": ["planning_prepare_option_comparison", "planning_score_option_comparison", "planning_confirm_option_comparison"],
    "scale": ["planning_solve_build_scale", "planning_validate_build_scale", "planning_confirm_build_scale"],
    "drivers": ["planning_create_cost_drivers", "planning_create_labor_plan", "planning_create_revenue_drivers"],
    "finance_spec": ["finance_prepare_spec", "finance_validate_spec", "finance_build_basis_of_estimate", "finance_confirm_spec"],
    "finance_run": ["finance_run_model", "finance_get_run"],
    "finance_tables": ["tables_render", "tables_validate", "tables_export_xlsx"],
    "report": ["report_prepare", "report_propose_section", "report_apply", "report_validate"],
    "review": ["review_start", "review_list_findings", "review_retest"],
    "released": ["feasibility_validate", "feasibility_release"],
}


def next_actions(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    record = _record(workspace_id, str(args.get("delivery_run_id") or ""))
    if record is None:
        return _blocked("delivery_run_not_found", "可研交付运行不存在")
    run = _view(record)
    stage_name = str(run.get("current_stage") or "project")
    stage_record = dict((run.get("stages") or {}).get(stage_name) or {})
    stored = list(stage_record.get("next_actions") or [])
    actions = stored or _NEXT_TOOLS.get(stage_name, [])
    return _envelope(
        True,
        str(run.get("status") or "in_progress"),
        delivery_run_id=record["object_id"],
        current_stage=stage_name,
        actions=[{"tool": item, "stage": stage_name, "reason": "完成当前阶段或处理当前缺口"} for item in actions],
        missing_inputs=list(stage_record.get("blockers") or []),
        resource_uris=[record["resource_uri"]],
    )


def checkpoint(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    delivery_run_id = str(args.get("delivery_run_id") or "")
    record = _record(workspace_id, delivery_run_id)
    if record is None:
        return _blocked("delivery_run_not_found", "可研交付运行不存在")
    request = {"delivery_run_id": delivery_run_id, "reason": str(args.get("reason") or "")}

    def create() -> dict[str, Any]:
        payload = {
            "delivery_run_id": delivery_run_id,
            "delivery_run_basis_hash": record["basis_hash"],
            "current_stage": (record.get("payload") or {}).get("current_stage"),
            "reason": request["reason"],
            "created_at": utc_now(),
        }
        saved = CHECKPOINT_STORE.put(
            workspace_id,
            payload,
            producer="lvke-feasibility-delivery.feasibility_checkpoint",
            source_ids=[delivery_run_id],
        )
        return _envelope(
            True,
            "ok",
            checkpoint=_view(saved, "checkpoint_id"),
            checkpoint_id=saved["object_id"],
            delivery_run_id=delivery_run_id,
            resource_uris=[saved["resource_uri"]],
        )

    return _idempotent(
        workspace_id,
        operation="feasibility_checkpoint",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request,
        mutation=create,
    )


def resume(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    checkpoint_id = str(args.get("checkpoint_id") or "")
    saved = CHECKPOINT_STORE.get(workspace_id, checkpoint_id)
    if saved is None:
        return _blocked("checkpoint_not_found", "交付 checkpoint 不存在")
    old_id = str((saved.get("payload") or {}).get("delivery_run_id") or "")
    old = _record(workspace_id, old_id)
    if old is None:
        return _blocked("delivery_run_not_found", "checkpoint 指向的交付运行不存在")
    request = {"checkpoint_id": checkpoint_id, "supplemental_inputs": args.get("supplemental_inputs") or {}}

    def create() -> dict[str, Any]:
        payload = json.loads(json.dumps(old.get("payload") or {}))
        payload["parent_run_id"] = old["object_id"]
        payload["status"] = "in_progress"
        current = str(payload.get("current_stage") or "project")
        payload.setdefault("stages", empty_stages())[current]["status"] = "in_progress"
        payload["next_actions"] = []
        payload["resume_from_checkpoint_id"] = checkpoint_id
        child = RUN_STORE.put(
            workspace_id,
            payload,
            producer="lvke-feasibility-delivery.feasibility_resume",
            source_ids=[old["object_id"], checkpoint_id],
            basis=request,
        )
        return _run_response(child, extra={"resumed_from_checkpoint_id": checkpoint_id})

    return _idempotent(
        workspace_id,
        operation="feasibility_resume",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request,
        mutation=create,
    )


def _validation(run: dict[str, Any], scope: str) -> tuple[bool, list[str], list[str]]:
    stages = run.get("stages") or {}
    blockers: list[str] = []
    warnings: list[str] = []
    for index, name in enumerate(STAGES[:-1]):
        item = stages.get(name) or {}
        item_status = str(item.get("status") or "pending")
        if item_status == "completed" and not item.get("output_refs"):
            blockers.append(f"{name}_output_refs_missing")
        if item_status == "completed" and not str(item.get("basis_hash") or "").strip():
            blockers.append(f"{name}_basis_hash_missing")
        if item_status == "completed" and name != "project" and not item.get("input_refs"):
            blockers.append(f"{name}_input_refs_missing")
        if scope == "formal":
            if item_status != "completed":
                blockers.append(f"{name}_{item_status or 'pending'}")
        elif item_status in {"partial", "blocked", "stale"}:
            warnings.append(f"{name}_{item_status}")
        if index > 0:
            previous = stages.get(STAGES[index - 1]) or {}
            if item_status == "completed" and previous.get("status") != "completed":
                blockers.append(f"stage_order_invalid:{name}")
    if scope == "formal" and str(run.get("delivery_mode") or "") == "estimate_preview":
        blockers.append("preview_cannot_formal_release")
    evidence_policy = str(run.get("evidence_policy") or "formal_evidence")
    release_scope = str(run.get("release_scope") or "project_delivery")
    if scope == "formal" and evidence_policy == "controlled_assumption":
        blockers.append("controlled_assumption_formal_forbidden")
    if scope == "formal" and release_scope == "project_delivery" and evidence_policy == "source_reconstructed":
        blockers.append("project_fact_evidence_missing")
    if scope == "formal" and evidence_policy == "source_reconstructed":
        if not run.get("reconstructed_source_ids"):
            blockers.append("reconstructed_source_ids_missing")
        if run.get("project_fact_certified") is True:
            blockers.append("source_reconstructed_cannot_certify_project_fact")
    return not blockers, blockers, warnings


def validate(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    record = _record(workspace_id, str(args.get("delivery_run_id") or ""))
    if record is None:
        return _blocked("delivery_run_not_found", "可研交付运行不存在")
    scope = str(args.get("scope") or "technical")
    if scope not in {"technical", "formal"}:
        return _blocked("validation_scope_invalid", "scope 必须是 technical 或 formal")
    run = _view(record)
    passed, blockers, warnings = _validation(run, scope)
    return _envelope(
        passed,
        "ok" if passed else "blocked",
        code="" if passed else "delivery_validation_failed",
        message="交付校验通过" if passed else "交付校验未通过",
        validation={"scope": scope, "passed": passed, "blockers": blockers, "warnings": warnings},
        blockers=blockers,
        warnings=warnings,
        delivery_run_id=record["object_id"],
        release_scope=str(run.get("release_scope") or "project_delivery"),
        evidence_policy=str(run.get("evidence_policy") or "formal_evidence"),
        project_fact_certified=bool(run.get("project_fact_certified")),
        unresolved_inputs=list(run.get("unresolved_inputs") or []),
        release_limitations=list(run.get("release_limitations") or []),
        resource_uris=[record["resource_uri"]],
    )


def release(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    delivery_run_id = str(args.get("delivery_run_id") or "")
    record = _record(workspace_id, delivery_run_id)
    if record is None:
        return _blocked("delivery_run_not_found", "可研交付运行不存在")
    run = _view(record)
    requested_scope = str(args.get("release_scope") or run.get("release_scope") or "project_delivery")
    if requested_scope not in RELEASE_SCOPES:
        return _blocked("release_scope_invalid", "release_scope 必须是 process_acceptance 或 project_delivery")
    if requested_scope != str(run.get("release_scope") or "project_delivery"):
        run = {**run, "release_scope": requested_scope}
    passed, blockers, warnings = _validation(run, "formal")
    if not passed:
        if "project_fact_evidence_missing" in blockers:
            return _blocked("project_fact_evidence_missing", "当前资料只有 source_reconstructed，不能作为 project_delivery 发布", next_actions=["将 release_scope 改为 process_acceptance"], validation_blockers=blockers)
        return _blocked("formal_validation_required", "formal 校验未通过，不能发布", next_actions=["调用 feasibility_validate(scope=formal)"], validation_blockers=blockers)
    request = {"delivery_run_id": delivery_run_id, "release_note": str(args.get("release_note") or "")}

    def create() -> dict[str, Any]:
        release_payload = {
            "delivery_run_id": delivery_run_id,
            "delivery_mode": run.get("delivery_mode"),
            "release_scope": requested_scope,
            "evidence_policy": run.get("evidence_policy") or "formal_evidence",
            "project_fact_certified": bool(run.get("project_fact_certified")),
            "reconstructed_source_ids": list(run.get("reconstructed_source_ids") or []),
            "unresolved_inputs": list(run.get("unresolved_inputs") or []),
            "release_limitations": list(run.get("release_limitations") or []),
            "release_note": request["release_note"],
            "object_refs": run.get("lineage") or {},
            "released_at": utc_now(),
        }
        release_record = RELEASE_STORE.put(
            workspace_id,
            release_payload,
            producer="lvke-feasibility-delivery.feasibility_release",
            source_ids=[delivery_run_id],
        )
        payload = json.loads(json.dumps(record.get("payload") or {}))
        payload["parent_run_id"] = delivery_run_id
        payload["status"] = "released"
        payload["current_stage"] = "released"
        payload.setdefault("stages", empty_stages())["released"] = {
            "status": "completed",
            "input_refs": [delivery_run_id],
            "output_refs": [release_record["object_id"]],
            "basis_hash": release_record["basis_hash"],
            "warnings": warnings,
            "blockers": [],
            "next_actions": [],
            "updated_from_run_id": delivery_run_id,
        }
        payload["release_id"] = release_record["object_id"]
        released_run = RUN_STORE.put(
            workspace_id,
            payload,
            producer="lvke-feasibility-delivery.feasibility_release.run",
            source_ids=[delivery_run_id, release_record["object_id"]],
        )
        return _envelope(
            True,
            "released",
            release=_view(release_record, "release_id"),
            release_id=release_record["object_id"],
            delivery_run=_view(released_run),
            delivery_run_id=released_run["object_id"],
            resource_uris=[release_record["resource_uri"], released_run["resource_uri"]],
        )

    return _idempotent(
        workspace_id,
        operation="feasibility_release",
        idempotency_key=str(args["idempotency_key"]),
        request_payload=request,
        mutation=create,
    )


def list_resources(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    resource_type = str(args.get("resource_type") or "")
    entries: list[dict[str, Any]] = []
    for store, object_type in RESOURCE_STORES:
        if resource_type and resource_type != object_type:
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
    return _envelope(True, "ok", **paginate_resource_entries(entries, cursor=str(args.get("cursor") or ""), limit=int(args.get("limit") or 50)))


def read_resource(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    uri = str(args.get("uri") or "")
    for store, object_type in RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None and record.get("workspace_id") == workspace_id:
            return _envelope(True, "ok", object_type=object_type, resource=record, resource_uris=[uri])
    return _blocked("resource_not_found", "交付 Resource 不存在或不属于当前工作区")


def resolve_resource(uri: str) -> tuple[str, str] | None:
    for store, _object_type in RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None:
            return json.dumps(record, ensure_ascii=False), "application/json"
    return None
