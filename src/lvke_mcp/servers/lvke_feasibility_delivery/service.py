"""Stateful, immutable orchestration for feasibility-study delivery runs."""

from __future__ import annotations

import functools

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock

from lvke_mcp.runtime import resource_registry
from lvke_mcp.runtime.evidence_qualification import (
    declared_evidence_policy,
    project_fact_may_be_certified,
)
from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    validate_finance_run,
    validate_finance_tables_package,
    validate_formal_record,
    validate_object_formal_lineage,
    validate_research_package,
)
from lvke_mcp.runtime.quality_severity import (
    aggregate_quality_status,
    is_finance_data_quality_issue,
    split_quality_codes,
)
from lvke_mcp.runtime.storage import (
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
    utc_now,
)
from lvke_mcp.runtime.workspace import workspace_root
from lvke_mcp.adapters.project_planning_repository import RESOURCE_STORES as PLANNING_STORES
from lvke_mcp.adapters.data_analysis_repository import RESOURCE_STORES as ANALYSIS_STORES
from lvke_mcp.adapters.data_acquisition_repository import RESOURCE_STORES as ACQUISITION_DATA_STORES
from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_PACKAGE_STORE
from lvke_mcp.adapters.finance_model_repository import SPEC_STORE, BASIS_OF_ESTIMATE_STORE
from lvke_mcp.adapters.finance_tables_repository import PACKAGE_STORE as TABLE_PACKAGE_STORE
from lvke_mcp.adapters.quality_diagnostic_repository import (
    build_uncertainty,
)
from lvke_mcp.adapters.report_repository import PREPARATION_STORE as REPORT_PREPARATION_STORE, REVISION_STORE as REPORT_REVISION_STORE
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
    explicit_quality = extra.get("quality_issues") if isinstance(extra, dict) else None
    quality_codes = [
        *(str(item) for item in blockers or [] if str(item)),
        *(str(item) for item in explicit_quality or [] if str(item)),
    ]
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
        "operation_status": "completed",
        "diagnostic_available": True,
        "quality_status": aggregate_quality_status(quality_codes) if quality_codes else "pass",
        "uncertainties": [],
        "quality_issues": [],
        "diagnostic_only": False,
        "human_confirmation_required": False,
        "formal_report_allowed": True,
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
    blockers: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    # blockers 默认就是拒绝码本身；调用方可以传更完整的一组（例如把全部
    # 口径阻断项都列出来），此时拒绝码仍保证在列表里。
    resolved = list(blockers) if blockers else [code]
    if code not in resolved:
        resolved = [code, *resolved]
    return _envelope(
        False,
        "blocked",
        code=code,
        message=message,
        blockers=resolved,
        next_actions=next_actions,
        **extra,
    )


_INVALID_ID_CODES = {
    "workspace_id": "invalid_workspace_id",
    "object_id": "invalid_delivery_run_id",
    "delivery_run_id": "invalid_delivery_run_id",
    "checkpoint_id": "invalid_checkpoint_id",
    "segment": "invalid_resource_segment",
}


def _guard_identifiers(handler: Callable[[dict[str, Any]], dict[str, Any]]):
    """Report malformed identifiers as business blocks, not server faults.

    ``require_safe_id`` raises ``ValueError`` and every entrypoint calls it
    outside any try, so the transport degraded a rejected *input* into a generic
    ``internal_error`` with ``system_success=False`` — losing both the specific
    code and the object identity a caller needs to fix the call.
    """

    @functools.wraps(handler)
    def wrapper(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return handler(args)
        except ValueError as error:
            detail = str(error)
            field = next(
                (name for name in _INVALID_ID_CODES if name in detail),
                "",
            )
            if not field:
                raise
            return _blocked(
                _INVALID_ID_CODES[field],
                f"标识符不合法：{detail}",
                next_actions=["改用服务端签发的合法标识符重新调用"],
                workspace_id=str(args.get("workspace_id") or ""),
                delivery_run_id=str(args.get("delivery_run_id") or ""),
            )

    return wrapper


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


@_guard_identifiers
def start(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    mode = str(args.get("delivery_mode") or "")
    if mode not in DELIVERY_MODES:
        return _blocked("delivery_mode_invalid", "delivery_mode 必须是 estimate_preview、review_candidate 或 formal_release")
    project_context_id = str(args.get("project_context_id") or "")
    requested_evidence_policy = str(args.get("evidence_policy") or "formal_evidence")
    # 技术验收阶段默认只走 process_acceptance（§7/§9-18）：
    # project_delivery / formal_release 不加入当前主流程，调用方必须显式声明。
    release_scope = str(args.get("release_scope") or "process_acceptance")
    if requested_evidence_policy not in EVIDENCE_POLICIES:
        return _blocked("evidence_policy_invalid", "evidence_policy 不受支持")
    if release_scope not in RELEASE_SCOPES:
        return _blocked("release_scope_invalid", "release_scope 必须是 process_acceptance 或 project_delivery")
    if mode == "estimate_preview" and release_scope == "project_delivery":
        release_scope = "process_acceptance"
    project_object: dict[str, Any] | None = None
    canonical_lineage: dict[str, Any] = {}
    if project_context_id:
        project_object = _resolve_object(workspace_id, project_context_id)
        if project_object is None or project_object.get("kind") != "ProjectContext":
            return _blocked("project_context_not_found", "project_context_id 必须解析到当前 workspace 的真实 ProjectContext")
        project_payload = dict(project_object.get("payload") or {})
        if str(project_payload.get("evidence_policy") or project_payload.get("evidence_track") or "") == "sim_a_formal":
            try:
                canonical_lineage = validate_object_formal_lineage(workspace_id, project_payload)
            except FormalLineageError:
                # Formal lineage is retained as metadata when available, not a
                # prerequisite for starting work.
                canonical_lineage = {}
    evidence_policy = str(canonical_lineage.get("evidence_policy") or requested_evidence_policy)
    project_fact_certified = bool(canonical_lineage.get("project_fact_certified")) or project_fact_may_be_certified(
        evidence_policy,
        own_qualification_passed=False,
        parents=[project_object] if project_object is not None else [],
    )
    request = {
        "workspace_id": workspace_id,
        "delivery_mode": mode,
        "project_context_id": project_context_id,
        "evidence_policy": evidence_policy,
        "evidence_origin": canonical_lineage.get("evidence_origin"),
        "release_scope": release_scope,
        "project_fact_certified": project_fact_certified,
        "formal_promotion": canonical_lineage.get("formal_promotion"),
        "reconstructed_source_ids": list(args.get("reconstructed_source_ids") or []),
        "reconstruction_records": list(args.get("reconstruction_records") or []),
        "unresolved_inputs": list(args.get("unresolved_inputs") or []),
        "release_limitations": list(args.get("release_limitations") or []),
    }

    def create() -> dict[str, Any]:
        stages = empty_stages()
        current_stage = "project"
        if project_context_id:
            project_object = _resolve_object(workspace_id, project_context_id)
            stages["project"].update({
                "status": "completed",
                "output_refs": [project_context_id],
                "basis_hash": str((project_object or {}).get("basis_hash") or sha256_json({"project_context_id": project_context_id})),
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
            "evidence_origin": request.get("evidence_origin"),
            "release_scope": release_scope,
            "project_fact_certified": request["project_fact_certified"],
            "formal_promotion": request.get("formal_promotion"),
            "reconstructed_source_ids": request["reconstructed_source_ids"],
            "reconstruction_records": request["reconstruction_records"],
            "unresolved_inputs": request["unresolved_inputs"],
            "release_limitations": request["release_limitations"],
            "stages": stages,
            "lineage": canonical_lineage,
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
        idempotency_key=str(args.get("idempotency_key") or ""),
        request_payload=request,
        mutation=create,
    )


@_guard_identifiers
def status(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    record = _record(workspace_id, str(args.get("delivery_run_id") or ""))
    if record is None:
        return _blocked("delivery_run_not_found", "可研交付运行不存在")
    return _run_response(record)


@_guard_identifiers
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
    stage_quality_issues: list[str] = []
    if target_index > current_index:
        stage_quality_issues.append("stage_order_violation")
    if target_index < current_index and not reopen:
        stage_quality_issues.append("stage_not_current")
    bind_lineage = bool(args.get("bind_workspace_lineage"))
    output_refs = [str(item) for item in (args.get("output_refs") or []) if str(item)]
    input_refs = [str(item) for item in (args.get("input_refs") or []) if str(item)]
    basis_hash = str(args.get("basis_hash") or args.get("stage_basis_hash") or "")
    if bind_lineage:
        if not output_refs:
            output_refs = _discover_stage_output_ids(workspace_id, stage_name)
        if not input_refs and target_index > 0:
            previous = ((record.get("payload") or {}).get("stages") or {}).get(
                STAGES[target_index - 1], {}
            )
            input_refs = [str(item) for item in (previous.get("output_refs") or []) if str(item)]
    if stage_status == "completed" and not output_refs and stage_name != "project":
        stage_quality_issues.append("stage_output_required")
    if stage_status == "completed" and not basis_hash.strip():
        if bind_lineage and output_refs:
            resolved_for_basis = []
            for ref in output_refs:
                resolved, _error = _validate_reference(workspace_id, str(ref), stage_name, "output")
                if resolved is not None:
                    resolved_for_basis.append(resolved)
            basis_hash = sha256_json({
                "input_refs": input_refs,
                "output_refs": output_refs,
                "output_basis_hashes": sorted(str(item.get("basis_hash") or "") for item in resolved_for_basis),
            })
        else:
            stage_quality_issues.append("stage_basis_hash_required")
            basis_hash = sha256_json({
                "stage": stage_name,
                "input_refs": input_refs,
                "output_refs": output_refs,
            })

    request = {
        "delivery_run_id": delivery_run_id,
        "stage": stage_name,
        "status": stage_status,
        "input_refs": input_refs,
        "output_refs": output_refs,
        "basis_hash": basis_hash,
        "warnings": [*list(args.get("warnings") or []), *stage_quality_issues],
        "blockers": [],
        "quality_issues": list(args.get("blockers") or []),
        "next_actions": list(args.get("next_actions") or []),
        "reopen": reopen,
    }
    if stage_status == "completed":
        if args.get("blockers"):
            request["warnings"].append("stage_blockers_present")
        resolved_objects: list[dict[str, Any]] = []
        resolved_outputs: list[dict[str, Any]] = []
        reference_errors: list[str] = []
        for role in ("input", "output"):
            for ref in request[f"{role}_refs"]:
                resolved, error = _validate_reference(workspace_id, str(ref), stage_name, role)
                if error:
                    reference_errors.append(error)
                elif resolved is not None:
                    resolved_objects.append(resolved)
                    if role == "output":
                        resolved_outputs.append(resolved)
        if reference_errors:
            request["warnings"].extend(reference_errors)
            request["quality_issues"].append("stage_reference_invalid")
        try:
            _require_same_stage_lineage(
                workspace_id,
                _view(record),
                resolved_objects,
            )
        except FormalLineageError as exc:
            request["warnings"].append(exc.code)
            request["quality_issues"].append(exc.code)
        required_kinds = {
            "project": {"ProjectContext"},
            "research": {"ResearchPackage"},
            "market": {"MarketSizingCase"},
            "option": {"OptionComparison"},
            "scale": {"BuildScaleCase"},
            "drivers": {"CostDriverSet", "LaborPlan", "RevenueDriverSet"},
            "finance_spec": {"FinanceSpec", "BasisOfEstimate"},
            "finance_run": {"FinanceRun"},
            "finance_tables": {"FinanceTablesPackage"},
            "report": {"ReportRevision"},
            "review": {"ReviewRun"},
        }[stage_name]
        actual_kinds = {str(item.get("kind") or "") for item in resolved_outputs}
        if stage_name == "finance_spec" and "AcquisitionFinanceSpec" in actual_kinds:
            required_kinds = {"AcquisitionFinanceSpec"}
        elif stage_name == "finance_run" and "AcquisitionRun" in actual_kinds:
            required_kinds = {"AcquisitionRun"}
        elif stage_name == "finance_tables" and "AcquisitionTablesPackage" in actual_kinds:
            required_kinds = {"AcquisitionTablesPackage"}
        if not required_kinds.issubset(actual_kinds):
            request["warnings"].extend(
                f"stage_output_type_missing:{item}"
                for item in sorted(required_kinds - actual_kinds)
            )
            request["quality_issues"].append("stage_output_type_invalid")
        previous_outputs = set(
            str(item)
            for item in ((record.get("payload") or {}).get("stages") or {}).get(
                STAGES[target_index - 1] if target_index > 0 else "", {}
            ).get("output_refs", [])
        )
        if target_index > 0 and previous_outputs and not previous_outputs.intersection(
            str(item) for item in request["input_refs"]
        ):
            request["warnings"].append("stage_parent_binding_missing")
            request["quality_issues"].append("stage_parent_binding_missing")
        allowed_basis = {str(item.get("basis_hash") or "") for item in resolved_outputs}
        allowed_basis.add(sha256_json({
            "input_refs": request["input_refs"],
            "output_refs": request["output_refs"],
            "output_basis_hashes": sorted(str(item.get("basis_hash") or "") for item in resolved_outputs),
        }))
        if request["basis_hash"] not in allowed_basis:
            request["warnings"].append("stage_basis_hash_mismatch")
            request["quality_issues"].append("stage_basis_hash_mismatch")

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
            "warnings": sorted(set(request["warnings"])),
            "blockers": [],
            "quality_issues": sorted(set(request["quality_issues"])),
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
        # ``lineage`` is reserved for canonical promotion metadata. Stage
        # object references live in ``stage_bindings`` and must never mutate
        # the signed root merely because a caller used a Resource URI.
        payload["lineage"] = dict(payload.get("lineage") or {})
        stage_bindings = dict(payload.get("stage_bindings") or {})
        stage_bindings[stage_name] = {
            "input_refs": list(request["input_refs"]),
            "output_refs": list(request["output_refs"]),
            "basis_hash": request["basis_hash"],
        }
        payload["stage_bindings"] = stage_bindings
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
        idempotency_key=str(args.get("idempotency_key") or ""),
        request_payload=request,
        mutation=update,
    )


_NEXT_TOOLS: dict[str, list[Any]] = {
    "project": ["project_context_create", "project_context_validate"],
    "research": ["dr_prepare", "dr_start", "data_discover", "data_fetch", "analysis_build_evidence_pack"],
    "market": [
        {"tool": "planning_prepare", "arguments": {"object_kind": "market_case"}},
        {"tool": "planning_validate", "arguments": {"object_kind": "market_case"}},
        {"tool": "planning_confirm", "arguments": {"object_kind": "market_case"}},
    ],
    "option": [
        {"tool": "planning_prepare", "arguments": {"object_kind": "option_comparison"}},
        "planning_score_option_comparison",
        {"tool": "planning_validate", "arguments": {"object_kind": "option_comparison"}},
        {"tool": "planning_confirm", "arguments": {"object_kind": "option_comparison"}},
    ],
    "scale": [
        "planning_solve_build_scale",
        {"tool": "planning_validate", "arguments": {"object_kind": "build_scale"}},
        {"tool": "planning_confirm", "arguments": {"object_kind": "build_scale"}},
    ],
    "drivers": [
        {"tool": "planning_create", "arguments": {"object_kind": "cost_drivers"}},
        {"tool": "planning_create", "arguments": {"object_kind": "labor_plan"}},
        {"tool": "planning_create", "arguments": {"object_kind": "revenue_drivers"}},
    ],
    "finance_spec": ["finance_prepare_spec", "finance_validate_spec", "finance_build_basis_of_estimate", "finance_confirm_spec"],
    "finance_run": ["finance_run_model", "finance_get_run"],
    "finance_tables": ["tables_render", "tables_validate", "tables_export_xlsx"],
    "report": ["report_prepare", "report_propose_section", "report_apply", "report_validate"],
    "review": ["review_start", "review_list_findings", "review_retest"],
    "released": ["feasibility_validate", "feasibility_release"],
}

def _latest_record_id(records: list[dict[str, Any]], id_field: str = "object_id") -> str:
    if not records:
        return ""
    ordered = sorted(records, key=lambda item: str(item.get("created_at") or item.get(id_field) or ""))
    return str(ordered[-1].get(id_field) or "")


def _discover_stage_output_ids(workspace_id: str, stage_name: str) -> list[str]:
    """Return the newest workspace objects that can bind this FDR stage."""

    from lvke_mcp.adapters.project_planning_repository import (
        BUILD_SCALE_STORE,
        COST_DRIVER_STORE,
        LABOR_PLAN_STORE,
        MARKET_CASE_STORE,
        OPTION_COMPARISON_STORE,
        PROJECT_CONTEXT_STORE,
        REVENUE_DRIVER_STORE,
    )
    from lvke_mcp.adapters.finance_model_repository import SPEC_STORE, BASIS_OF_ESTIMATE_STORE
    from lvke_mcp.adapters.finance_tables_repository import PACKAGE_STORE as TABLE_PACKAGE_STORE
    from lvke_mcp.adapters.report_repository import REVISION_STORE as REPORT_REVISION_STORE
    from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_PACKAGE_STORE

    mapping: dict[str, list[Any]] = {
        "project": [(PROJECT_CONTEXT_STORE, "object_id")],
        "research": [(RESEARCH_PACKAGE_STORE, "object_id")],
        "market": [(MARKET_CASE_STORE, "object_id")],
        "option": [(OPTION_COMPARISON_STORE, "object_id")],
        "scale": [(BUILD_SCALE_STORE, "object_id")],
        "drivers": [
            (COST_DRIVER_STORE, "object_id"),
            (LABOR_PLAN_STORE, "object_id"),
            (REVENUE_DRIVER_STORE, "object_id"),
        ],
        "finance_spec": [(SPEC_STORE, "object_id"), (BASIS_OF_ESTIMATE_STORE, "object_id")],
        "finance_tables": [(TABLE_PACKAGE_STORE, "object_id")],
        "report": [(REPORT_REVISION_STORE, "object_id")],
    }
    found: list[str] = []
    if stage_name == "finance_run":
        try:
            from lvke_mcp.domains.finance import run_store

            latest = run_store.latest_run(workspace_id)
            if latest.get("run_id"):
                found.append(str(latest["run_id"]))
        except Exception:  # noqa: BLE001
            pass
        return found
    if stage_name == "review":
        root = workspace_root(workspace_id) / "mcp_objects" / "deliverable-review" / "events"
        if root.is_dir():
            names = sorted(path.name for path in root.iterdir() if path.is_dir())
            if names:
                found.append(names[-1])
        return found
    for store, field in mapping.get(stage_name, []):
        try:
            object_id = _latest_record_id(store.list(workspace_id), field)
        except Exception:  # noqa: BLE001
            object_id = ""
        if object_id:
            found.append(object_id)
    return found


@_guard_identifiers
def next_actions(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    record = _record(workspace_id, str(args.get("delivery_run_id") or ""))
    if record is None:
        return _blocked("delivery_run_not_found", "可研交付运行不存在")
    run = _view(record)
    stage_name = str(run.get("current_stage") or "project")
    stage_record = dict((run.get("stages") or {}).get(stage_name) or {})
    stored = list(stage_record.get("next_actions") or [])
    tools = stored or _NEXT_TOOLS.get(stage_name, [])
    output_refs = [str(item) for item in stage_record.get("output_refs") or [] if str(item)]
    input_refs = [str(item) for item in stage_record.get("input_refs") or [] if str(item)]
    missing_inputs = list(stage_record.get("blockers") or [])
    if not input_refs and stage_name != "project":
        missing_inputs.append(f"{stage_name}_input_refs")
    if not output_refs and stage_name != "released":
        missing_inputs.append(f"{stage_name}_output_refs")
    next_items: list[dict[str, Any]] = []
    for item in tools:
        if isinstance(item, dict):
            tool = str(item.get("tool") or "")
            reason = str(item.get("reason") or "完成当前阶段或处理当前缺口")
            arguments = dict(item.get("arguments") or {})
        else:
            tool = str(item)
            reason = "完成当前阶段或处理当前缺口"
            arguments = {"workspace_id": workspace_id}
        arguments.setdefault("workspace_id", workspace_id)
        if output_refs:
            if tool in {"planning_validate", "planning_compare", "planning_confirm"}:
                arguments["target_id"] = output_refs[0]
            elif stage_name == "finance_spec" and tool == "finance_validate_spec":
                arguments["spec_id"] = output_refs[0]
            elif stage_name == "finance_run" and tool == "finance_get_run":
                arguments["run_id"] = output_refs[0]
            elif stage_name == "finance_tables" and tool == "tables_validate":
                arguments["finance_tables_package_id"] = output_refs[0]
            elif stage_name == "report" and tool == "report_validate":
                arguments["report_revision_id"] = output_refs[0]
        next_items.append({"tool": tool, "arguments": arguments, "reason": reason})
    discovered = _discover_stage_output_ids(workspace_id, stage_name)
    if discovered and not output_refs and stage_name != "released":
        previous_outputs = []
        if stage_name != "project":
            previous = STAGES[max(_stage_index(stage_name) - 1, 0)]
            previous_outputs = [
                str(item)
                for item in ((run.get("stages") or {}).get(previous) or {}).get("output_refs") or []
                if str(item)
            ]
        next_items.insert(0, {
            "tool": "feasibility_stage",
            "arguments": {
                "workspace_id": workspace_id,
                "delivery_run_id": record["object_id"],
                "stage": stage_name,
                "status": "completed",
                "output_refs": discovered,
                "input_refs": previous_outputs,
                "bind_workspace_lineage": True,
                "idempotency_key": f"bind-{stage_name}-{record['object_id'][-8:]}",
            },
            "reason": "工作区已有对象可绑回当前 FDR 阶段",
        })
    # Envelope ``next_actions`` stays a string array so tools/list lightweight
    # schema and MCP output validation both accept the payload. Executable
    # descriptors remain on ``actions``.
    return _envelope(
        True,
        str(run.get("status") or "in_progress"),
        delivery_run_id=record["object_id"],
        current_stage=stage_name,
        next_actions=[
            f"{item['tool']}: {item['reason']}" if item.get("reason") else str(item["tool"])
            for item in next_items
        ],
        actions=[
            {
                "tool": item["tool"],
                "stage": stage_name,
                "reason": item["reason"],
                "arguments": dict(item.get("arguments") or {}),
            }
            for item in next_items
        ],
        missing_inputs=sorted(set(missing_inputs)),
        resource_uris=[record["resource_uri"]],
    )


@_guard_identifiers
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
        idempotency_key=str(args.get("idempotency_key") or ""),
        request_payload=request,
        mutation=create,
    )


@_guard_identifiers
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
        idempotency_key=str(args.get("idempotency_key") or ""),
        request_payload=request,
        mutation=create,
    )


def _record_view(record: dict[str, Any], kind: str) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return {
        "kind": kind,
        "object_id": str(record.get("object_id") or ""),
        "workspace_id": str(record.get("workspace_id") or ""),
        "resource_uri": str(record.get("resource_uri") or ""),
        "content_hash": str(record.get("content_hash") or ""),
        "basis_hash": str(record.get("basis_hash") or ""),
        "status": str(record.get("status") or ""),
        "payload": payload,
        "record": record,
        "content_integrity_ok": str(record.get("content_hash") or "") == sha256_json(payload),
        "source_ids": [str(item) for item in (record.get("source_ids") or []) if str(item)],
    }


def _resolve_object(workspace_id: str, reference: str) -> dict[str, Any] | None:
    """Resolve a stage reference against existing MCP object stores.

    Delivery orchestration stores references only; it never creates a shadow
    copy of a domain object.  A reference is therefore valid only when its
    object or canonical Resource already exists in this workspace.
    """
    ref = str(reference or "").strip()
    if not ref or ":" in ref and not ref.startswith("lvke://"):
        return None
    source_prefix = f"lvke://source-files/workspaces/{workspace_id}/files/"
    source_ref = ref.removeprefix(source_prefix).split("/", 1)[0] if ref.startswith(source_prefix) else ref
    try:
        from lvke_mcp.adapters import source_files_repository

        state, source = source_files_repository._require_source_record(  # noqa: SLF001
            workspace_id, source_ref, "feasibility-delivery",
        )
        del state
        path = Path(str(source.get("path") or ""))
        raw = path.read_bytes() if path.is_file() else b""
        recorded_hash = "sha256:" + str(source.get("sha256") or "").removeprefix("sha256:")
        actual_hash = "sha256:" + hashlib.sha256(raw).hexdigest() if raw else ""
        source_uri = f"lvke://source-files/workspaces/{workspace_id}/files/{source_ref}"
        if not ref.startswith("lvke://") or ref == source_uri:
            return {
                "kind": "SourceFileSnapshot",
                "object_id": source_ref,
                "workspace_id": workspace_id,
                "resource_uri": source_uri,
                "content_hash": recorded_hash,
                "basis_hash": sha256_json({
                    "source_id": source_ref,
                    "source_version": source.get("version"),
                    "content_hash": recorded_hash,
                }),
                "status": str(source.get("extract_status") or source.get("status") or ""),
                "payload": dict(source),
                "record": dict(source),
                "content_integrity_ok": bool(
                    raw
                    and actual_hash == recorded_hash
                    and len(raw) == int(source.get("size_bytes") or -1)
                ),
            }
    except Exception:  # noqa: BLE001 - continue with the remaining object stores
        pass
    stores: list[tuple[Any, str]] = [
        *PLANNING_STORES,
        *ANALYSIS_STORES,
        *ACQUISITION_DATA_STORES,
        (RESEARCH_PACKAGE_STORE, "ResearchPackage"),
        (SPEC_STORE, "FinanceSpec"),
        (BASIS_OF_ESTIMATE_STORE, "BasisOfEstimate"),
        (TABLE_PACKAGE_STORE, "FinanceTablesPackage"),
        (REPORT_PREPARATION_STORE, "ReportPreparation"),
        (REPORT_REVISION_STORE, "ReportRevision"),
        (RUN_STORE, "FeasibilityDeliveryRun"),
        (CHECKPOINT_STORE, "FeasibilityCheckpoint"),
        (RELEASE_STORE, "FeasibilityRelease"),
    ]
    for store, kind in stores:
        try:
            record = store.resolve_uri(ref) if ref.startswith("lvke://") else store.get(workspace_id, ref)
        except (OSError, ValueError):
            record = None
        if record is None:
            continue
        if ref.startswith("lvke://") and str(record.get("resource_uri") or "") != ref:
            continue
        if str(record.get("workspace_id") or "") != workspace_id:
            continue
        normalized_kind = "SourceSnapshot" if kind == "source_snapshot" else kind
        return _record_view(record, normalized_kind)

    acquisition_ref = ref
    for prefix in (
        f"lvke://asset-acquisition/workspaces/{workspace_id}/runs/",
        f"lvke://asset-acquisition/workspaces/{workspace_id}/specs/",
        f"lvke://asset-acquisition/workspaces/{workspace_id}/table-packages/",
    ):
        if ref.startswith(prefix):
            acquisition_ref = ref.removeprefix(prefix).split("/", 1)[0]
            break
    try:
        from lvke_mcp.domains.asset_acquisition.backend import get_run as get_acquisition_run, get_spec as get_acquisition_spec
        from lvke_mcp.domains.asset_acquisition.tables import get_package_record as get_acquisition_package

        acquisition_package = get_acquisition_package(workspace_id, acquisition_ref)
        if acquisition_package is not None:
            return _record_view(acquisition_package, "AcquisitionTablesPackage")
        acquisition_run = get_acquisition_run(workspace_id, acquisition_ref)
        if acquisition_run and str(acquisition_run.get("run_id") or "") == acquisition_ref:
            return {
                "kind": "AcquisitionRun", "object_id": acquisition_ref,
                "workspace_id": workspace_id, "resource_uri": "",
                "content_hash": sha256_json(acquisition_run),
                "basis_hash": str(acquisition_run.get("spec_hash") or acquisition_run.get("input_hash") or ""),
                "status": str(acquisition_run.get("status") or ""),
                "payload": acquisition_run, "record": acquisition_run,
            }
        acquisition_spec = get_acquisition_spec(workspace_id, acquisition_ref)
        if acquisition_spec and str(acquisition_spec.get("spec_id") or "") == acquisition_ref:
            acquisition_spec_payload = dict(acquisition_spec)
            saved_spec = acquisition_spec.get("spec") if isinstance(acquisition_spec.get("spec"), dict) else {}
            for field in (
                "evidence_policy", "project_fact_certified", "reconstruction_records",
                "reconstructed_source_ids", "unresolved_inputs", "release_limitations",
                "business_decision_status",
            ):
                if field in saved_spec:
                    acquisition_spec_payload[field] = saved_spec[field]
            return {
                "kind": "AcquisitionFinanceSpec", "object_id": acquisition_ref,
                "workspace_id": workspace_id, "resource_uri": "",
                "content_hash": sha256_json(acquisition_spec),
                "basis_hash": str(acquisition_spec.get("spec_hash") or ""),
                "status": str(acquisition_spec.get("confirmation_status") or ""),
                "payload": acquisition_spec_payload, "record": acquisition_spec,
            }
    except Exception:  # noqa: BLE001
        pass

    finance_ref = ref
    finance_prefix = f"lvke://finance-model/workspaces/{workspace_id}/runs/"
    if ref.startswith(finance_prefix):
        finance_ref = ref.removeprefix(finance_prefix).split("/", 1)[0]
    # Finance runs are persisted by the finance engine rather than the JSON
    # artifact adapter.  Expose the same immutable metadata to the gate.
    try:
        from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

        finance_run = get_workspace_finance_run(workspace_id, run_id=finance_ref, view="full")
        if finance_run.get("available") and str(finance_run.get("run_id") or "") == finance_ref:
            return {
                "kind": "FinanceRun",
                "object_id": finance_ref,
                "workspace_id": workspace_id,
                "resource_uri": "",
                "content_hash": sha256_json(finance_run),
                "basis_hash": str(finance_run.get("spec_hash") or finance_run.get("input_hash") or ""),
                "status": "succeeded" if finance_run.get("consistency_ok") else "failed",
                "payload": finance_run,
                "record": finance_run,
            }
    except Exception:  # noqa: BLE001 - an unavailable engine is a missing object
        pass

    # Review runs use an append-only event store and consequently do not have a
    # JSONArtifactStore record.  Resolve them through the review projection.
    review_ref = ref
    review_prefix = f"lvke://deliverable-review/workspaces/{workspace_id}/reviews/"
    if ref.startswith(review_prefix):
        review_ref = ref.removeprefix(review_prefix).split("/", 1)[0]
    try:
        review = resource_registry.get_review(workspace_id, review_ref)
        if isinstance(review.get("review"), dict):
            projected = review["review"]
            return {
                "kind": "ReviewRun",
                "object_id": review_ref,
                "workspace_id": workspace_id,
                "resource_uri": f"lvke://deliverable-review/workspaces/{workspace_id}/reviews/{review_ref}",
                "content_hash": sha256_json(projected),
                "basis_hash": sha256_json({"review_id": review_ref, "target": projected.get("target")}),
                "status": str(projected.get("review_status") or ""),
                "payload": projected,
                "record": projected,
            }
    except Exception:  # noqa: BLE001
        pass
    return None


def _uri_workspace(reference: str) -> str | None:
    """Return the workspace embedded in an ``lvke://`` URI, if it has one.

    Every canonical Resource URI carries its owning workspace as
    ``lvke://<domain>/workspaces/<workspace_id>/<segment>/<object_id>``.  Reading
    it back is what lets the gate tell "no such object" apart from "that object
    belongs to another workspace" without probing every store.
    """

    ref = str(reference or "").strip()
    if not ref.startswith("lvke://"):
        return None
    parts = ref.removeprefix("lvke://").split("/")
    if len(parts) < 4 or parts[1] != "workspaces":
        return None
    return parts[2] or None


def _validate_reference(workspace_id: str, reference: str, stage: str, role: str) -> tuple[dict[str, Any] | None, str | None]:
    resolved = _resolve_object(workspace_id, reference)
    if resolved is None:
        # P1-017 修复：URI 合法但属于另一个 workspace 时，原先一律报 ref_not_found，
        # 把"跨 workspace 引用"误述为"对象不存在"，指向了错误的原因。
        embedded = _uri_workspace(reference)
        if embedded is not None and embedded != workspace_id:
            return None, f"{stage}_{role}_ref_wrong_workspace:{reference}"
        return None, f"{stage}_{role}_ref_not_found:{reference}"
    if not str(resolved.get("content_hash") or "").startswith("sha256:"):
        return None, f"{stage}_{role}_content_hash_missing:{reference}"
    if resolved.get("content_integrity_ok") is False:
        return None, f"{stage}_{role}_content_hash_mismatch:{reference}"
    if not str(resolved.get("basis_hash") or "").startswith("sha256:"):
        return None, f"{stage}_{role}_basis_hash_missing:{reference}"
    return resolved, None


def _canonical_delivery_run_lineage(
    workspace_id: str,
    run: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild the formal root from ProjectContext and reject copied labels."""

    if str(run.get("evidence_policy") or "") != "sim_a_formal":
        return {}
    project_context_id = str(run.get("project_context_id") or "")
    project = _resolve_object(workspace_id, project_context_id)
    if project is None or project.get("kind") != "ProjectContext":
        raise FormalLineageError(
            "formal_project_context_not_found",
            "交付运行绑定的正式 ProjectContext 不存在或不属于当前工作区",
        )
    canonical = validate_formal_record(workspace_id, project.get("record") or {})
    stored = {
        field: run.get(field)
        for field in (
            "evidence_policy",
            "evidence_origin",
            "project_fact_certified",
            "formal_promotion",
        )
    }
    if stored != canonical or run.get("lineage") != canonical:
        raise FormalLineageError(
            "formal_delivery_run_lineage_mismatch",
            "交付运行持久化的正式谱系与 ProjectContext 不一致",
        )
    return canonical


def _validate_resolved_formal_object(
    workspace_id: str,
    resolved: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate a formal stage object using its domain's immutable parent rules."""

    kind = str(resolved.get("kind") or "")
    payload = resolved.get("payload") if isinstance(resolved.get("payload"), dict) else {}
    if kind == "FinanceRun":
        return validate_finance_run(workspace_id, str(resolved.get("object_id") or ""))
    if kind == "FinanceTablesPackage":
        return validate_finance_tables_package(workspace_id, resolved.get("record") or {})
    if kind == "ReportRevision":
        from lvke_mcp.domains.reports.formal_lineage import validate_report_revision_lineage

        return validate_report_revision_lineage(workspace_id, resolved.get("record") or {})
    if kind == "ReviewRun":
        metadata = payload.get("evidence_metadata")
        if not isinstance(metadata, dict):
            raise FormalLineageError(
                "formal_lineage_unsigned_history",
                "正式 ReviewRun 缺少 evidence_metadata",
            )
        canonical = validate_object_formal_lineage(workspace_id, metadata)
        stored = {
            field: metadata.get(field)
            for field in (
                "evidence_policy",
                "evidence_origin",
                "project_fact_certified",
                "formal_promotion",
            )
        }
        if stored != canonical:
            raise FormalLineageError(
                "formal_review_lineage_mismatch",
                "ReviewRun 持久化的正式谱系不是规范值",
            )
        return canonical

    if kind == "ResearchPackage":
        return validate_research_package(workspace_id, resolved.get("record") or {})

    policy = str(payload.get("evidence_policy") or payload.get("evidence_track") or "")
    always_formal = {
        "ProjectContext",
        "evidence_pack",
        "FinanceSpec",
        "BasisOfEstimate",
    }
    if kind in always_formal or policy == "sim_a_formal":
        return validate_formal_record(workspace_id, resolved.get("record") or {})
    return None


def _require_same_stage_lineage(
    workspace_id: str,
    run: dict[str, Any],
    objects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Revalidate all formal objects at one boundary against the run root."""

    canonical = _canonical_delivery_run_lineage(workspace_id, run)
    if not canonical:
        return {}
    for resolved in objects:
        object_lineage = _validate_resolved_formal_object(workspace_id, resolved)
        if object_lineage is not None and object_lineage != canonical:
            raise FormalLineageError(
                "formal_lineage_mixed_promotions",
                f"阶段对象来自不同 promotion: {resolved.get('object_id')}",
            )
    return canonical


def _stage_objects(run: dict[str, Any], workspace_id: str, stage: str) -> tuple[list[dict[str, Any]], list[str]]:
    item = (run.get("stages") or {}).get(stage) or {}
    resolved: list[dict[str, Any]] = []
    errors: list[str] = []
    for role in ("input", "output"):
        for ref in item.get(f"{role}_refs") or []:
            obj, error = _validate_reference(workspace_id, str(ref), stage, role)
            if error:
                errors.append(error)
            elif obj is not None:
                resolved.append({**obj, "reference_role": role})
    return resolved, errors


def _delivery_lineage_ids(run: dict[str, Any]) -> set[str]:
    """All object ids already bound into this delivery run."""

    ids = {str(run.get("project_context_id") or "")}
    for stage in (run.get("stages") or {}).values():
        if not isinstance(stage, dict):
            continue
        for role in ("input_refs", "output_refs"):
            ids.update(str(item) for item in (stage.get(role) or []) if str(item))
    ids.discard("")
    return ids


def _declared_parent_ids(payload: dict[str, Any]) -> set[str]:
    parents = {str(item) for item in (payload.get("parent_object_ids") or []) if str(item)}
    upstream = payload.get("upstream") if isinstance(payload.get("upstream"), dict) else {}
    for source in (payload, upstream):
        for key, value in source.items():
            if key in {"parent_spec_id", "parent_revision_id", "parent_run_id"}:
                # Immutable revision ancestry is not a business-stage input.
                # The stage gate validates the selected revision itself.
                continue
            if key.startswith("parent_") and key.endswith("_id") and isinstance(value, str) and value:
                parents.add(value)
            elif key in {
                "project_context_id", "evidence_pack_id", "evidence_pack_ids",
                "research_package_ids", "run_id", "finance_run_id",
                "finance_tables_package_id", "spec_id", "basis_of_estimate_id",
                "market_case_id", "option_comparison_id", "build_scale_case_id",
                "revenue_driver_set_id", "cost_driver_set_id", "labor_plan_id",
            }:
                values = value if isinstance(value, list) else [value]
                parents.update(str(item) for item in values if str(item))
    return parents


def _object_chain_validation(
    run: dict[str, Any],
    workspace_id: str,
    *,
    validation_scope: str,
) -> list[str]:
    """Validate the immutable delivery chain for both acceptance scopes.

    ``technical`` relaxes only formal evidence/release eligibility.  Object
    existence, hashes, stage bindings and the substantive output contract are
    common to both scopes; otherwise an empty or synthetic chain could pass a
    process-acceptance check.
    """

    blockers: list[str] = []
    stages = run.get("stages") or {}
    objects_by_stage: dict[str, list[dict[str, Any]]] = {}
    for name in STAGES[:-1]:
        objects, errors = _stage_objects(run, workspace_id, name)
        objects_by_stage[name] = objects
        blockers.extend(errors)

    if validation_scope == "formal" and str(run.get("evidence_policy") or "") == "sim_a_formal":
        try:
            _require_same_stage_lineage(
                workspace_id,
                run,
                [obj for objects in objects_by_stage.values() for obj in objects],
            )
        except FormalLineageError as exc:
            blockers.append(exc.code)

    def payloads(name: str) -> list[dict[str, Any]]:
        return [
            obj.get("payload") or {}
            for obj in objects_by_stage.get(name, [])
            if obj.get("reference_role") == "output"
        ]

    # Every completed delivery stage must carry a real immutable object.  This
    # is a technical invariant, not a formal-evidence qualification rule.
    for name in STAGES[:-1]:
        item = stages.get(name) or {}
        if item.get("blockers"):
            blockers.append(f"{name}_blockers_present")
        if item.get("warnings") and any(str(w).lower() in {"stale", "partial"} for w in item.get("warnings") or []):
            blockers.append(f"{name}_stale_or_partial_warning")

    required_output_kinds: dict[str, set[str]] = {
        "project": {"ProjectContext"},
        "research": {"ResearchPackage"},
        "market": {"MarketSizingCase"},
        "option": {"OptionComparison"},
        "scale": {"BuildScaleCase"},
        "drivers": {"CostDriverSet", "LaborPlan", "RevenueDriverSet"},
        "finance_spec": {"FinanceSpec", "BasisOfEstimate"},
        "finance_run": {"FinanceRun"},
        "finance_tables": {"FinanceTablesPackage"},
        "report": {"ReportRevision"},
        "review": {"ReviewRun"},
    }
    for name, required in required_output_kinds.items():
        actual = {
            str(obj.get("kind") or "")
            for obj in objects_by_stage.get(name, [])
            if obj.get("reference_role") == "output"
        }
        if name == "finance_spec" and "AcquisitionFinanceSpec" in actual:
            required = {"AcquisitionFinanceSpec"}
        elif name == "finance_run" and "AcquisitionRun" in actual:
            required = {"AcquisitionRun"}
        elif name == "finance_tables" and "AcquisitionTablesPackage" in actual:
            required = {"AcquisitionTablesPackage"}
        for kind in sorted(required - actual):
            blockers.append(f"{name}_output_kind_missing:{kind}")
        outputs = [
            obj for obj in objects_by_stage.get(name, [])
            if obj.get("reference_role") == "output"
        ]
        if outputs:
            stage_basis = str(((stages.get(name) or {}).get("basis_hash") or ""))
            allowed_basis = {str(obj.get("basis_hash") or "") for obj in outputs}
            allowed_basis.add(sha256_json({
                "input_refs": list((stages.get(name) or {}).get("input_refs") or []),
                "output_refs": list((stages.get(name) or {}).get("output_refs") or []),
                "output_basis_hashes": sorted(str(obj.get("basis_hash") or "") for obj in outputs),
            }))
            if stage_basis not in allowed_basis:
                blockers.append(f"{name}_stage_basis_mismatch")

    for index, name in enumerate(STAGES[1:-1], start=1):
        previous = STAGES[index - 1]
        previous_outputs = {str(item) for item in ((stages.get(previous) or {}).get("output_refs") or [])}
        current_inputs = {str(item) for item in ((stages.get(name) or {}).get("input_refs") or [])}
        if previous_outputs and not previous_outputs.intersection(current_inputs):
            blockers.append(f"{name}_parent_stage_binding_missing:{previous}")
        for obj in objects_by_stage.get(name, []):
            if obj.get("reference_role") != "output":
                continue
            declared_parents = _declared_parent_ids(obj.get("payload") or {})
            declared_parents.discard(str(obj.get("object_id") or ""))
            # Immediate previous-stage outputs are not the only valid parents.
            # Scale/revenue objects parent to ProjectContext + MarketCase, which
            # sit earlier in the FDR chain. Accept any delivery-lineage id.
            lineage_ids = _delivery_lineage_ids(run)
            if current_inputs and declared_parents and not (
                declared_parents.intersection(current_inputs)
                or declared_parents.intersection(lineage_ids)
            ):
                blockers.append(f"{name}_object_parent_binding_mismatch:{obj.get('object_id')}")

    if validation_scope == "formal":
        expected_policy = str(run.get("evidence_policy") or "formal_evidence")
        evidence_kinds = {
            "evidence_pack", "ResearchPackage", "FinanceSpec", "BasisOfEstimate",
            "FinanceRun", "FinanceTablesPackage", "AcquisitionFinanceSpec",
            "AcquisitionRun", "AcquisitionTablesPackage", "ReportRevision", "ReviewRun",
        }
        for objects in objects_by_stage.values():
            for obj in objects:
                if str(obj.get("kind") or "") not in evidence_kinds:
                    continue
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                if obj.get("kind") == "ReportRevision":
                    payload = payload.get("upstream") if isinstance(payload.get("upstream"), dict) else {}
                elif obj.get("kind") == "ReviewRun":
                    payload = payload.get("evidence_metadata") if isinstance(payload.get("evidence_metadata"), dict) else {}
                policy = declared_evidence_policy(payload)
                if policy in {"controlled_assumption", "technical_fixture"}:
                    blockers.append(f"formal_evidence_policy_forbidden:{obj.get('object_id')}:{policy}")
                if policy and policy != expected_policy:
                    blockers.append(f"evidence_policy_mismatch:{obj.get('object_id')}")
                if expected_policy == "source_reconstructed":
                    if payload.get("project_fact_certified") is True:
                        blockers.append(f"source_reconstructed_fact_certification_forbidden:{obj.get('object_id')}")
                    records = payload.get("reconstruction_records") or []
                    if not records:
                        blockers.append(f"reconstruction_records_not_propagated:{obj.get('object_id')}")

    project_ids = [str(item.get("object_id") or "") for item in objects_by_stage.get("project", []) if item.get("kind") == "ProjectContext" and item.get("reference_role") == "output"]
    if not project_ids:
        blockers.append("project_context_object_required")
    run_project = str(run.get("project_context_id") or "")
    if run_project and project_ids and run_project not in project_ids:
        blockers.append("project_context_binding_mismatch")

    research_payloads = payloads("research")
    if not research_payloads:
        blockers.append("research_package_object_required")
    for payload in research_payloads:
        status = str(payload.get("status") or "")
        if status == "partial" or not payload.get("quality_review_id"):
            blockers.append("research_quality_confirmation_required")
        if payload.get("quality_review_status") not in {"accepted", "accepted_with_limitations", "passed"}:
            blockers.append("research_quality_not_accepted")

    spec_payloads = [
        obj.get("payload") or {}
        for obj in objects_by_stage.get("finance_spec", [])
        if obj.get("reference_role") == "output"
        and obj.get("kind") in {"FinanceSpec", "AcquisitionFinanceSpec"}
    ]
    if not spec_payloads:
        blockers.append("finance_spec_object_required")
    for payload in spec_payloads:
        if str(payload.get("confirmation_status") or "") not in {"confirmed", "formal_ready"}:
            blockers.append("finance_spec_not_confirmed")

    finance_payloads = payloads("finance_run")
    finance_run_ids = [str(obj.get("object_id") or "") for obj in objects_by_stage.get("finance_run", []) if obj.get("kind") in {"FinanceRun", "AcquisitionRun"} and obj.get("reference_role") == "output"]
    if not finance_run_ids:
        blockers.append("finance_run_object_required")
    spec_ids = {
        str(obj.get("object_id") or "")
        for obj in objects_by_stage.get("finance_spec", [])
        if obj.get("reference_role") == "output" and obj.get("kind") in {"FinanceSpec", "AcquisitionFinanceSpec"}
    }
    spec_hashes = {str(payload.get("spec_hash") or "") for payload in spec_payloads}
    for payload in finance_payloads:
        if str(payload.get("status") or payload.get("calculation_status") or "") in {"failed", "none"}:
            blockers.append("finance_run_not_successful")
        run_spec_id = str(payload.get("spec_id") or payload.get("finance_spec_id") or "")
        if spec_ids and run_spec_id:
            if run_spec_id not in spec_ids:
                blockers.append("finance_run_spec_binding_mismatch")
        elif spec_hashes and str(payload.get("spec_hash") or "") not in spec_hashes:
            blockers.append("finance_run_spec_binding_mismatch")

    table_payloads = payloads("finance_tables")
    if not table_payloads:
        blockers.append("finance_tables_package_required")
    from lvke_mcp.domains.finance.run_service import DELIVERY_TABLE_KEYS
    acquisition_table_keys: set[str] = set()
    try:
        from lvke_mcp.domains.asset_acquisition.tables import TABLE_DEFINITIONS
        acquisition_table_keys = {key for key, _title in TABLE_DEFINITIONS}
    except Exception:  # noqa: BLE001
        pass
    for payload in table_payloads:
        tables = payload.get("tables") if isinstance(payload.get("tables"), dict) else {}
        required_tables = acquisition_table_keys if str(payload.get("package_schema") or "").startswith("acquisition_") else set(DELIVERY_TABLE_KEYS)
        missing = [key for key in required_tables if key not in tables]
        if missing:
            blockers.append("finance_tables_incomplete")
        if finance_run_ids and str(payload.get("run_id") or payload.get("bound_run_id") or "") != finance_run_ids[0]:
            blockers.append("finance_tables_run_binding_mismatch")
        acquisition_integrity = str((payload.get("integrity") or {}).get("status") or "") == "passed"
        if not acquisition_integrity:
            try:
                from lvke_mcp.domains.finance import tables_service

                table_validation = tables_service.validate(
                    workspace_id,
                    str(payload.get("run_id") or ""),
                    validation_scope=validation_scope,
                )
            except Exception:  # noqa: BLE001
                table_validation = {"success": False}
            if not table_validation.get("success"):
                blockers.append(
                    "finance_tables_formal_validation_required"
                    if validation_scope == "formal"
                    else "finance_tables_technical_validation_required"
                )

    report_payloads = payloads("report")
    if not report_payloads:
        blockers.append("report_revision_required")
    for payload in report_payloads:
        snapshot = payload.get("document_snapshot") if isinstance(payload.get("document_snapshot"), dict) else {}
        content = str(snapshot.get("content") or "")
        chapter_count = sum(1 for number in range(1, 10) if f"第{number}章" in content)
        if chapter_count < 9:
            blockers.append("report_nine_chapters_required")
        section_lineage = payload.get("section_lineage") if isinstance(payload.get("section_lineage"), dict) else {}
        complete_section_bindings = [
            item for item in section_lineage.values()
            if isinstance(item, dict)
            and item.get("upstream_refs")
            and item.get("citation_locators")
            and item.get("upstream_basis_hashes")
        ]
        if len(complete_section_bindings) < 9:
            blockers.append("report_section_lineage_incomplete")
        upstream = payload.get("upstream") if isinstance(payload.get("upstream"), dict) else {}
        if not upstream.get("upstream_refs") and not upstream.get("evidence_pack_ids"):
            blockers.append("report_upstream_refs_required")
        if finance_run_ids and str(upstream.get("run_id") or "") != finance_run_ids[0]:
            blockers.append("report_finance_run_binding_mismatch")
        if table_payloads and str(upstream.get("finance_tables_package_id") or "") not in {str(obj.get("object_id") or "") for obj in objects_by_stage.get("finance_tables", []) if obj.get("reference_role") == "output"}:
            blockers.append("report_finance_tables_binding_mismatch")
        if payload.get("readiness") is False:
            blockers.append("report_readiness_failed")
    # report_validate includes the strict finance publish binding.  The common
    # technical checks above still require nine chapters, section lineage and
    # readiness; only formal release invokes that additional publish gate.
    if validation_scope == "formal":
        for obj in objects_by_stage.get("report", []):
            if obj.get("reference_role") != "output" or obj.get("kind") != "ReportRevision":
                continue
            try:
                from lvke_mcp.domains.reports.validation import validate_report
                report_validation = validate_report(workspace_id, str(obj.get("object_id") or ""))
                if not report_validation.get("valid"):
                    blockers.append("report_readiness_failed")
            except Exception:  # noqa: BLE001
                blockers.append("report_readiness_unverifiable")

    review_payloads = payloads("review")
    if not review_payloads:
        blockers.append("review_run_required")
    for payload in review_payloads:
        if payload.get("active_blocking_finding_ids"):
            blockers.append("review_open_blocker")
        findings = payload.get("findings") or []
        open_findings = [row for row in findings if isinstance(row, dict) and str(row.get("status") or "").lower() in {"open", "pending", "needs_revision", "in_progress"}]
        if open_findings:
            blockers.append("review_open_finding")
        if payload.get("validation_complete") is False:
            blockers.append("review_not_complete")
        review_purpose = str(
            (payload.get("project_context") or {}).get("review_purpose")
            or (payload.get("project_context") or {}).get("release_scope")
            or ""
        )
        release_scope = str(run.get("release_scope") or "project_delivery")
        if review_purpose and review_purpose != release_scope:
            blockers.append("review_release_scope_mismatch")
        if payload.get("technical_verdict") in {"fail", "incomplete"}:
            blockers.append("review_technical_verdict_not_acceptable")
        if validation_scope == "formal" and payload.get("release_verdict") in {"fail", "incomplete"}:
            blockers.append("review_release_verdict_not_acceptable")
            blockers.extend(
                str(item)
                for item in payload.get("blockers") or []
                if str(item).startswith((
                    "standard_methodology_full_text_required:",
                    "controlled_assumption_release_forbidden",
                    "source_reconstructed_release_forbidden",
                    "technical_fixture_release_forbidden",
                ))
            )
    knowledge_ids = list(run.get("knowledge_candidate_ids") or [])
    if validation_scope == "formal" and knowledge_ids:
        try:
            for candidate_id in knowledge_ids:
                result = resource_registry.get_knowledge_candidate(workspace_id, candidate_id)
                reviews = list(result.get("reviews") or [])
                releases = list(result.get("releases") or [])
                accepted = any(str((row.get("decision") or (row.get("payload") or {}).get("decision") or "")) == "accepted" for row in reviews if isinstance(row, dict))
                if not result.get("success") or str(result.get("candidate_status") or "") != "published" or not accepted or not releases:
                    blockers.append(f"knowledge_candidate_not_accepted:{candidate_id}")
        except Exception:
            blockers.append("knowledge_review_required")
    return sorted(set(blockers))


def _formal_object_validation(run: dict[str, Any], workspace_id: str) -> list[str]:
    """Compatibility wrapper for callers of the former private helper."""

    return _object_chain_validation(
        run,
        workspace_id,
        validation_scope="formal",
    )


def _validation(run: dict[str, Any], scope: str, workspace_id: str = "") -> tuple[bool, list[str], list[str]]:
    stages = run.get("stages") or {}
    blockers: list[str] = []
    warnings: list[str] = []
    for index, name in enumerate(STAGES[:-1]):
        item = stages.get(name) or {}
        item_status = str(item.get("status") or "pending")
        if item_status != "completed":
            blockers.append(f"{name}_{item_status or 'pending'}")
        if not item.get("output_refs"):
            blockers.append(f"{name}_output_refs_missing")
        if not str(item.get("basis_hash") or "").strip():
            blockers.append(f"{name}_basis_hash_missing")
        if name != "project" and not item.get("input_refs"):
            blockers.append(f"{name}_input_refs_missing")
        if item.get("blockers"):
            blockers.append(f"{name}_blockers_present")
        if index > 0:
            previous = stages.get(STAGES[index - 1]) or {}
            if item_status == "completed" and previous.get("status") != "completed":
                blockers.append(f"stage_order_invalid:{name}")
        if workspace_id and item_status == "completed":
            _objects, reference_errors = _stage_objects(run, workspace_id, name)
            blockers.extend(reference_errors)
            if index > 0:
                previous_outputs = {
                    str(ref) for ref in (stages.get(STAGES[index - 1]) or {}).get("output_refs") or []
                }
                current_inputs = {str(ref) for ref in item.get("input_refs") or []}
                if previous_outputs and not previous_outputs.intersection(current_inputs):
                    blockers.append(f"{name}_parent_stage_binding_missing:{STAGES[index - 1]}")
    if scope == "formal" and str(run.get("delivery_mode") or "") == "estimate_preview":
        blockers.append("preview_cannot_formal_release")
    evidence_policy = str(run.get("evidence_policy") or "formal_evidence")
    release_scope = str(run.get("release_scope") or "project_delivery")
    if scope == "technical":
        if evidence_policy not in {"formal_evidence", "sim_a_formal"}:
            warnings.append(f"formal_evidence_not_established:{evidence_policy}")
        if not run.get("project_fact_certified"):
            warnings.append("project_fact_not_certified")
        warnings.extend(
            f"release_limitation:{item}"
            for item in run.get("release_limitations") or []
            if str(item)
        )
    if scope == "formal" and evidence_policy == "controlled_assumption":
        blockers.append("controlled_assumption_formal_forbidden")
    if scope == "formal" and release_scope == "project_delivery" and evidence_policy == "source_reconstructed":
        blockers.append("project_fact_evidence_missing")
    if scope == "formal" and release_scope == "project_delivery" and not run.get("project_fact_certified"):
        blockers.append("project_fact_certification_required")
    # 重建记录的完整性与自洽性在两个 scope 都要查。technical 放宽的是
    # "证据是否达到正式资格"，不是"声称重建却拿不出重建记录"——后者是
    # 数据自相矛盾：evidence_policy 说值来自重建，却没有任何记录说明
    # 重建自何处、用什么方法。这种链在过程验收阶段同样不可采信。
    if evidence_policy == "source_reconstructed":
        if not run.get("reconstructed_source_ids"):
            blockers.append("reconstructed_source_ids_missing")
        records = list(run.get("reconstruction_records") or [])
        if not records:
            blockers.append("reconstruction_records_missing")
        else:
            from lvke_mcp.runtime.source_reconstruction import validate_reconstruction_records
            blockers.extend(
                f"reconstruction_record_invalid:{item.get('index')}:{item.get('code')}"
                for item in validate_reconstruction_records(records)[:20]
            )
            for item in records:
                uri = str(item.get("source_uri") or "")
                if uri.startswith("lvke://source-reconstructed/"):
                    continue
                resolved = _resolve_object(workspace_id, uri)
                if resolved is None:
                    blockers.append(f"reconstruction_source_not_found:{uri}")
                elif str(resolved.get("content_hash") or "") != str(item.get("content_hash") or ""):
                    blockers.append(f"reconstruction_source_hash_mismatch:{uri}")
        if run.get("project_fact_certified") is True:
            blockers.append("source_reconstructed_cannot_certify_project_fact")
    if workspace_id:
        blockers.extend(
            _object_chain_validation(
                run,
                workspace_id,
                validation_scope=scope,
            )
        )
    else:
        # 没有 workspace 就无法核对任何上游对象是否真实存在。此前这里静默跳过
        # 整段对象链校验，等于让一条合成的、指向不存在对象的链拿到 ok=True。
        blockers.append("object_chain_not_verifiable_without_workspace")
    return not blockers, sorted(set(blockers)), sorted(set(warnings))


@_guard_identifiers
def validate(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    record = _record(workspace_id, str(args.get("delivery_run_id") or ""))
    if record is None:
        return _blocked("delivery_run_not_found", "可研交付运行不存在")
    scope = str(args.get("scope") or "technical")
    if scope not in {"technical", "formal"}:
        return _blocked("validation_scope_invalid", "scope 必须是 technical 或 formal")
    run = _view(record)
    passed, blockers, warnings = _validation(run, scope, workspace_id)
    # 区分"口径非法"与"置信度不足"：前者必须留在 blockers 并让 success=False，
    # 后者才是 quality_issues。此前这里把两类一律降级、blockers 恒为 []，
    # 于是"受控假设走正式发布"这种非法口径也报 success=True。
    blocking_codes, quality_issues = split_quality_codes(blockers)
    # 技术验收阶段诊断信封（§7）：把发现固化为结构化不确定性，并聚合上游
    # FinanceRun / 表包 / 报告修订已固化的 QualityDiagnostic，供人工复核。
    uncertainties = _build_run_uncertainties(blocking_codes, quality_issues, scope)
    diagnostic_ids = _aggregate_upstream_diagnostics(workspace_id, run)
    return _envelope(
        True,
        "partial" if (blocking_codes or quality_issues) else "ok",
        message=(
            "技术验收已完成；发现数据质量问题，结果仅供诊断"
            if blocking_codes or quality_issues
            else "交付校验已完成；质量发现仅作诊断，不阻止后续发布"
        ),
        validation={
            "scope": scope,
            "passed": True,
            "quality_passed": passed,
            "validation_complete": True,
            "blockers": [],
            "quality_issues": quality_issues,
            "uncertainties": uncertainties,
            "diagnostic_ids": diagnostic_ids,
            "warnings": warnings,
        },
        blockers=[],
        quality_issues=quality_issues,
        uncertainties=uncertainties,
        quality_diagnostic_ids=diagnostic_ids,
        warnings=[
            *warnings,
            *(f"质量问题：{item}" for item in blocking_codes),
            *(
                f"质量提示：{item}"
                for item in quality_issues
                if item not in set(blocking_codes)
            ),
        ],
        delivery_run_id=record["object_id"],
        release_scope=str(run.get("release_scope") or "process_acceptance"),
        evidence_policy=str(run.get("evidence_policy") or "formal_evidence"),
        project_fact_certified=bool(run.get("project_fact_certified")),
        unresolved_inputs=list(run.get("unresolved_inputs") or []),
        release_limitations=list(run.get("release_limitations") or []),
        resource_uris=[record["resource_uri"]],
    )


def _build_run_uncertainties(
    blocking_codes: list[str],
    quality_issues: list[str],
    scope: str,
) -> list[dict[str, Any]]:
    """Turn validation findings into structured uncertainties (§3/§7).

    口径/勾稽冲突 → ``conflict``；阶段缺口等置信度不足 → ``unverified``。
    ``material`` 影响项必须在清单里，供报告草稿的人工确认章节引用。
    """

    from lvke_mcp.runtime.quality_severity import classify_quality

    uncertainties: list[dict[str, Any]] = []
    for code in [*blocking_codes, *quality_issues]:
        text = str(code or "").strip()
        if not text:
            continue
        classified = classify_quality(text)
        uncertainty_type = (
            "conflict" if classified.get("material_conflict") is True else "unverified"
        )
        uncertainty = build_uncertainty(
            uncertainty_type,
            field=text.split(":", 1)[0],
            message=text,
            severity="material" if classified.get("material_conflict") is True else "moderate",
            confidence="unknown",
            affected_outputs=["delivery_run"],
            required_action=(
                "修复口径冲突或由人工线下确认采用值"
                if classified.get("material_conflict") is True
                else "补齐证据或阶段对象后由人工线下确认"
            ),
        )
        uncertainties.append(uncertainty)
    return uncertainties


def _aggregate_upstream_diagnostics(
    workspace_id: str,
    run: dict[str, Any],
) -> list[str]:
    """Collect QualityDiagnostic ids already persisted on upstream chain objects."""

    from lvke_mcp.adapters.quality_diagnostic_repository import diagnostics_for_target

    candidates: set[str] = set()
    stages = run.get("stages") or {}
    for stage_name in ("finance_run", "finance_tables", "report", "review"):
        for ref in ((stages.get(stage_name) or {}).get("output_refs") or []):
            target = str(ref or "").strip()
            if not target or "/" in target:
                target = target.rsplit("/", 1)[-1]
            if not target:
                continue
            try:
                for item in diagnostics_for_target(workspace_id, target):
                    candidates.add(str(item["object_id"]))
            except Exception:  # noqa: BLE001
                continue
    return sorted(candidates)


@_guard_identifiers
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
    validation_scope = "technical" if requested_scope == "process_acceptance" else "formal"
    passed, blockers, warnings = _validation(run, validation_scope, workspace_id)
    blocking_codes, quality_issues = split_quality_codes(blockers)
    # Release is never a gate. Financial model conflicts remain explicit
    # diagnostics in the release record, alongside all other limitations.
    warnings = [
        *warnings,
        *(f"发布质量提示：{item}" for item in quality_issues),
    ]
    request = {
        "delivery_run_id": delivery_run_id,
        "release_scope": requested_scope,
        "validation_scope": validation_scope,
        "release_note": str(args.get("release_note") or ""),
    }

    def create() -> dict[str, Any]:
        canonical_lineage: dict[str, Any] = {}
        # Formal lineage is optional provenance.  It is recorded when it can
        # be resolved, but no longer controls whether a release is written.
        if validation_scope == "formal" and str(run.get("evidence_policy") or "") == "sim_a_formal":
            stage_objects = [
                obj
                for name in STAGES[:-1]
                for obj in _stage_objects(run, workspace_id, name)[0]
            ]
            try:
                canonical_lineage = _require_same_stage_lineage(
                    workspace_id,
                    run,
                    stage_objects,
                )
            except FormalLineageError as exc:
                warnings.append(f"质量提示：{exc.code}")
                quality_issues.append(exc.code)
        release_payload = {
            "delivery_run_id": delivery_run_id,
            "delivery_mode": run.get("delivery_mode"),
            "release_scope": requested_scope,
            "validation_scope": validation_scope,
            "evidence_policy": canonical_lineage.get("evidence_policy") or run.get("evidence_policy") or "formal_evidence",
            "evidence_origin": canonical_lineage.get("evidence_origin") or run.get("evidence_origin") or "",
            "project_fact_certified": bool(canonical_lineage.get("project_fact_certified", run.get("project_fact_certified"))),
            "formal_promotion": canonical_lineage.get("formal_promotion") or run.get("formal_promotion"),
            "reconstructed_source_ids": list(run.get("reconstructed_source_ids") or []),
            "unresolved_inputs": list(run.get("unresolved_inputs") or []),
            "release_limitations": list(dict.fromkeys([
                *list(run.get("release_limitations") or []),
                *quality_issues,
            ])),
            "quality_issues": quality_issues,
            "uncertainties": _build_run_uncertainties(blocking_codes, quality_issues, validation_scope),
            "quality_valid": not any(is_finance_data_quality_issue(item) for item in quality_issues),
            "diagnostic_only": False,
            "human_confirmation_required": False,
            "formal_report_allowed": True,
            "artifact_kind": "feasibility_delivery",
            "confirmation_status": "not_required",
            "reconstruction_records": list(run.get("reconstruction_records") or []),
            "stage_bindings": dict(run.get("stage_bindings") or {}),
            "lineage_hash": sha256_json({
                "lineage": canonical_lineage or run.get("lineage") or {},
                "stage_bindings": run.get("stage_bindings") or {},
            }),
            "release_note": request["release_note"],
            "object_refs": canonical_lineage or run.get("lineage") or {},
            "released_at": utc_now(),
        }
        release_record = RELEASE_STORE.put(
            workspace_id,
            release_payload,
            producer="lvke-feasibility-delivery.feasibility_release",
            source_ids=[delivery_run_id],
            basis={
                "delivery_run_id": delivery_run_id,
                "delivery_run_basis_hash": record.get("basis_hash"),
                "release_scope": requested_scope,
                "validation_scope": validation_scope,
                "lineage": canonical_lineage or run.get("lineage") or {},
                "stage_bindings": run.get("stage_bindings") or {},
            },
        )
        payload = json.loads(json.dumps(record.get("payload") or {}))
        payload["parent_run_id"] = delivery_run_id
        # 过程验收与正式交付不得共用 status=released：只读
        # delivery_run.status 的调用方无法区分两条路径。
        payload["status"] = (
            "process_accepted" if requested_scope == "process_acceptance" else "released"
        )
        payload["current_stage"] = "released"
        payload["release_grade"] = requested_scope
        payload["release_scope"] = requested_scope
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
            (
                "released" if requested_scope == "project_delivery"
                else "process_accepted"
            ),
            completed=True,
            release=_view(release_record, "release_id"),
            release_id=release_record["object_id"],
            delivery_run=_view(released_run),
            delivery_run_id=released_run["object_id"],
            release_scope=requested_scope,
            release_grade=requested_scope,
            validation_scope=validation_scope,
            quality_valid=not blocking_codes,
            quality_issues=quality_issues,
            uncertainties=release_payload["uncertainties"],
            diagnostic_only=False,
            human_confirmation_required=False,
            formal_report_allowed=True,
            artifact_kind="feasibility_delivery",
            confirmation_status="not_required",
            blockers=[],
            warnings=warnings,
            resource_uris=[release_record["resource_uri"], released_run["resource_uri"]],
        )

    return _idempotent(
        workspace_id,
        operation="feasibility_release",
        idempotency_key=str(args.get("idempotency_key") or ""),
        request_payload=request,
        mutation=create,
    )


@_guard_identifiers
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


def list_asset_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Expose asset-acquisition resources through the delivery aggregator."""
    from lvke_mcp.domains.asset_acquisition import resources

    return resources.list_resources(
        workspace_id,
        resource_type=resource_type,
        cursor=cursor,
        limit=limit,
    )


@_guard_identifiers
def read_resource(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    uri = str(args.get("uri") or "")
    for store, object_type in RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None and record.get("workspace_id") == workspace_id:
            return _envelope(True, "ok", object_type=object_type, resource=record, resource_uris=[uri])
    return _blocked("resource_not_found", "交付 Resource 不存在或不属于当前工作区")


def read_asset_resource(workspace_id: str, uri: str) -> dict[str, Any]:
    """Read an asset-acquisition resource through the delivery aggregator."""
    from lvke_mcp.domains.asset_acquisition import resources

    return resources.read_resource(workspace_id, uri)


def resolve_resource(uri: str) -> tuple[str, str] | None:
    for store, _object_type in RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None:
            return json.dumps(record, ensure_ascii=False), "application/json"
    return None

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "ACQUISITION_DATA_STORES",
    "ANALYSIS_STORES",
    "Any",
    "BASIS_OF_ESTIMATE_STORE",
    "CHECKPOINT_STORE",
    "Callable",
    "DELIVERY_MODES",
    "EVIDENCE_POLICIES",
    "FileLock",
    "FormalLineageError",
    "IDEMPOTENCY_STORE",
    "PLANNING_STORES",
    "Path",
    "RELEASE_SCOPES",
    "RELEASE_STORE",
    "REPORT_PREPARATION_STORE",
    "REPORT_REVISION_STORE",
    "RESEARCH_PACKAGE_STORE",
    "RESOURCE_STORES",
    "RUN_STATUSES",
    "RUN_STORE",
    "SPEC_STORE",
    "STAGES",
    "STAGE_STATUSES",
    "TABLE_PACKAGE_STORE",
    "_INVALID_ID_CODES",
    "_NEXT_TOOLS",
    "_blocked",
    "_canonical_delivery_run_lineage",
    "_declared_parent_ids",
    "_delivery_lineage_ids",
    "_discover_stage_output_ids",
    "_envelope",
    "_formal_object_validation",
    "_guard_identifiers",
    "_idempotent",
    "_latest_record_id",
    "_lock",
    "_object_chain_validation",
    "_record",
    "_record_view",
    "_require_same_stage_lineage",
    "_resolve_object",
    "_run_response",
    "_stage_index",
    "_stage_objects",
    "_uri_workspace",
    "_validate_reference",
    "_validate_resolved_formal_object",
    "_validation",
    "_view",
    "checkpoint",
    "declared_evidence_policy",
    "empty_stages",
    "functools",
    "hashlib",
    "json",
    "list_asset_resources",
    "list_resources",
    "next_actions",
    "paginate_resource_entries",
    "project_fact_may_be_certified",
    "read_asset_resource",
    "read_resource",
    "release",
    "require_safe_id",
    "resolve_resource",
    "resource_registry",
    "resume",
    "sha256_json",
    "split_quality_codes",
    "stage",
    "start",
    "status",
    "utc_now",
    "validate",
    "validate_finance_run",
    "validate_finance_tables_package",
    "validate_formal_record",
    "validate_object_formal_lineage",
    "validate_research_package",
    "workspace_root",
]
