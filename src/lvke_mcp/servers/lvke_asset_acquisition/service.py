"""MCP contract adapter for the existing acquisition domain service."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from lvke_mcp.domains.asset_acquisition import backend as acquisition_service
from lvke_mcp.domains.asset_acquisition.model import AcquisitionModelError
from lvke_mcp.domains.finance.spec import validate, validate_for_formal
from lvke_mcp.runtime.storage import JSONArtifactStore, require_safe_id, sha256_json
from lvke_mcp.runtime.source_reconstruction import SOURCE_RECONSTRUCTED, validate_reconstruction_records
from lvke_mcp.domains.asset_acquisition import tables

IDEMPOTENCY_STORE = JSONArtifactStore(
    "asset-acquisition", "mcp_idempotency", "acqidp", "idempotency"
)


def _uri(workspace_id: str, segment: str, object_id: str) -> str:
    return (
        f"lvke://asset-acquisition/workspaces/{require_safe_id(workspace_id, 'workspace_id')}/"
        f"{require_safe_id(segment, 'segment')}/{require_safe_id(object_id, 'object_id')}"
    )


def _error_code(result: dict[str, Any], fallback: str) -> str:
    error = result.get("error")
    if isinstance(error, dict):
        error = error.get("code")
    return str(error or fallback).upper()


def _failed(result: dict[str, Any], fallback: str) -> dict[str, Any]:
    code = _error_code(result, fallback)
    system_failures = {
        "SPEC_SAVE_FAILED", "SPEC_CONFIRM_FAILED", "RUN_FAILED",
        "MAX_PRICE_FAILED", "ARTIFACT_GENERATION_FAILED",
    }
    status = "failed" if code in system_failures else "blocked"
    return {
        "success": False, "transport_success": status != "failed",
        "business_success": False, "completed": False, "outcome": status,
        "status": status, "code": code,
        "message": str(result.get("message") or result.get("reason") or code),
        "details": copy.deepcopy(result.get("details") or {}),
        "resource_uris": [], "warnings": [], "blockers": [code], "next_actions": [],
    }


def _ok(
    data: dict[str, Any], *, object_id: str = "", uris: list[str] | None = None,
    warnings: list[str] | None = None, next_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "success": True, "status": "partial" if warnings else "ok",
        **({"object_id": object_id} if object_id else {}), **copy.deepcopy(data),
        "resource_uris": list(uris or []), "warnings": list(warnings or []),
        "blockers": [], "next_actions": list(next_actions or []),
    }


def _mutation(
    workspace_id: str, operation: str, idempotency_key: str,
    payload: dict[str, Any], action: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Provide stable MCP-level idempotency for domain writes without a key parameter."""

    store = IDEMPOTENCY_STORE
    key_hash = "sha256:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    payload_hash = sha256_json(payload)
    for record in store.list(workspace_id):
        saved = record.get("payload") or {}
        if saved.get("operation") != operation or saved.get("key_hash") != key_hash:
            continue
        if saved.get("payload_hash") != payload_hash:
            return _failed({"error": "IDEMPOTENCY_CONFLICT"}, "IDEMPOTENCY_CONFLICT")
        replay = copy.deepcopy(saved.get("result") or {})
        replay["idempotent_replay"] = True
        return replay
    result = action()
    if result.get("success") is True and result.get("status") in {"ok", "partial"}:
        store.put(
            workspace_id,
            {
                "operation": operation,
                "key_hash": key_hash,
                "payload_hash": payload_hash,
                "result": result,
            },
            producer=f"lvke-asset-acquisition.{operation}",
            source_ids=[str(result.get("object_id") or result.get("run_id") or "")],
            basis={"operation": operation, "key_hash": key_hash, "payload_hash": payload_hash},
        )
    return result


def validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    valid, errors = validate(spec)
    formal_valid, formal_errors = validate_for_formal(spec)
    transaction = spec.get("transaction") if isinstance(spec, dict) else {}
    asset_type = str(spec.get("asset_type") or "hotel_lease")
    hotel_contract = (
        asset_type == "hotel_lease"
        and isinstance(transaction, dict)
        and str(transaction.get("calculation_granularity") or "").lower() == "monthly"
        and transaction.get("operating_mode") in {"owner_lessor", "mixed_owner_operator"}
    )
    solar_contract = (
        asset_type == "solar_power"
        and isinstance(transaction, dict)
        and str(transaction.get("calculation_granularity") or "annual").lower() == "annual"
    )
    acquisition_contract = hotel_contract or solar_contract
    blockers = [*errors]
    reconstruction_errors = []
    if isinstance(spec, dict) and str(spec.get("evidence_policy") or "") == SOURCE_RECONSTRUCTED:
        reconstruction_errors = validate_reconstruction_records(spec.get("reconstruction_records"))
        blockers.extend(f"source_reconstruction:{item.get('code')}" for item in reconstruction_errors)
    field_errors: list[dict[str, Any]] = []
    def error_path(message: str) -> str:
        if message.startswith("未知收入模型"):
            return "/revenue/model"
        phrase_paths = {
            "历史报表类型": "/historical_statements",
            "正式运行缺主体角色": "/project_parties",
            "正式运行缺 project_parties": "/project_parties",
        }
        for phrase, path in phrase_paths.items():
            if phrase in message:
                return path
        candidates = re.findall(
            r"(?:project_parties|historical_statements|transaction|solar_operation|hotel_operation|lease_portfolio|decision_thresholds|evidence_links|version)"
            r"(?:(?:\.[A-Za-z_][A-Za-z0-9_]*)|(?:\[\d+\]))*",
            message,
        )
        if not candidates:
            return ""
        selected = max(candidates, key=len)
        return "/" + re.sub(r"\[(\d+)\]", r"/\1", selected).replace(".", "/")

    for error in errors:
        message = str(error)
        path = error_path(message)
        if not path:
            continue
        field_errors.append({
            "path": path,
            "code": "invalid_or_missing_value",
            "message": message,
            "stage": "candidate",
        })
    for error in formal_errors:
        message = str(error)
        path = error_path(message)
        if not path:
            continue
        field_errors.append({
            "path": path,
            "code": "formal_requirement_missing",
            "message": message,
            "stage": "formal",
        })
    if spec.get("version") != "finance_spec.v3":
        blockers.append("finance_spec.v3 required")
        field_errors.append({
            "path": "/version", "expected": "finance_spec.v3",
            "actual": spec.get("version"),
        })
    expected_granularity = "annual" if asset_type == "solar_power" else "monthly"
    actual_granularity = transaction.get("calculation_granularity") if isinstance(transaction, dict) else None
    if actual_granularity != expected_granularity:
        field_errors.append({
            "path": "/transaction/calculation_granularity",
            "expected": expected_granularity,
            "actual": actual_granularity,
        })
    if asset_type == "hotel_lease" and (
        not isinstance(transaction, dict) or transaction.get("operating_mode") not in {
        "owner_lessor", "mixed_owner_operator",
        }
    ):
        field_errors.append({
            "path": "/transaction/operating_mode",
            "expected_one_of": ["owner_lessor", "mixed_owner_operator"],
            "actual": transaction.get("operating_mode") if isinstance(transaction, dict) else None,
        })
    opening_date_missing = bool(
        asset_type == "hotel_lease"
        and isinstance(transaction, dict)
        and transaction.get("operating_mode") == "mixed_owner_operator"
        and not str(
            transaction.get("opening_date")
            or transaction.get("hotel_opening_date")
            or ""
        ).strip()
    )
    if opening_date_missing:
        blockers.append("mixed_owner_operator requires transaction.opening_date")
        field_errors.append({
            "path": "/transaction/opening_date",
            "code": "required_for_operating_mode",
            "message": "mixed_owner_operator requires transaction.opening_date",
            "stage": "candidate",
        })
    deduplicated_field_errors: list[dict[str, Any]] = []
    seen_field_errors: set[tuple[str, str]] = set()
    for item in field_errors:
        key = (str(item.get("path") or ""), str(item.get("message") or item.get("code") or ""))
        if key in seen_field_errors:
            continue
        seen_field_errors.add(key)
        deduplicated_field_errors.append(item)
    field_errors = deduplicated_field_errors
    if field_errors and not acquisition_contract:
        blockers.append("acquisition_mode_invalid")
    accepted = bool(valid and acquisition_contract and not opening_date_missing and not reconstruction_errors)
    response_code = None
    if not accepted:
        response_code = "SPEC_VALIDATION_FAILED" if opening_date_missing else "acquisition_mode_invalid"
    return {
        "success": accepted, "transport_success": True,
        "business_success": accepted, "completed": accepted,
        "outcome": "ok" if accepted else "blocked",
        "status": "ok" if accepted else "blocked",
        "code": response_code,
        "valid": accepted,
        "formal_valid": bool(formal_valid and acquisition_contract and not opening_date_missing),
        "validation_errors": errors, "formal_validation_errors": formal_errors,
        "field_errors": field_errors,
        "evidence_policy": str(spec.get("evidence_policy") or "formal_evidence"),
        "project_fact_certified": False if str(spec.get("evidence_policy") or "") == SOURCE_RECONSTRUCTED else True,
        "reconstruction_errors": reconstruction_errors,
        "spec_hash": acquisition_service._hash(spec),  # noqa: SLF001
        "resource_uris": [], "warnings": [], "blockers": blockers,
        "next_actions": (
            ["补齐 field_errors 中的正式主体、资产边界、历史报表和证据后再确认"]
            if accepted and not formal_valid
            else ([] if accepted else ["按资产类型修正 FinanceSpec v3 收购计算粒度和运营字段后重新校验"])
        ),
    }


def save_spec(
    workspace_id: str,
    spec: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    checked = validate_spec(spec)
    if not checked["valid"]:
        return checked
    row = acquisition_service.save_spec(
        workspace_id,
        spec,
        idempotency_key=idempotency_key,
    )
    if not row.get("ok"):
        return _failed(row, "SPEC_SAVE_FAILED")
    spec_id = str(row["spec_id"])
    return _ok(
        {"spec_id": spec_id, "spec_hash": row.get("spec_hash"),
         "snapshot_hash": row.get("snapshot_hash"),
         "evidence_binding_hash": row.get("evidence_binding_hash"),
         "confirmation_status": row.get("confirmation_status"),
         "idempotent_replay": bool(row.get("idempotent_replay"))},
        object_id=spec_id, uris=[_uri(workspace_id, "specs", spec_id)],
        next_actions=["调用 acquisition_confirm_spec 创建不可变确认修订"],
    )


def confirm_spec(
    workspace_id: str, spec_id: str, note: str, idempotency_key: str,
) -> dict[str, Any]:
    row = acquisition_service.confirm_saved_spec(
        workspace_id,
        spec_id,
        note=note,
        idempotency_key=idempotency_key,
    )
    if not row.get("ok"):
        return _failed(row, "SPEC_CONFIRM_FAILED")
    confirmed_id = str(row["spec_id"])
    estimate_preview = row.get("confirmation_scope") == "estimate_preview"
    return _ok(
        {"spec_id": confirmed_id, "parent_spec_id": row.get("parent_spec_id"),
         "spec_hash": row.get("spec_hash"), "snapshot_hash": row.get("snapshot_hash"),
         "evidence_binding_hash": row.get("evidence_binding_hash"),
         "confirmation_status": "confirmed",
         "confirmation_scope": row.get("confirmation_scope") or "formal_input",
         "idempotent_replay": bool(row.get("idempotent_replay"))},
        object_id=confirmed_id, uris=[_uri(workspace_id, "specs", confirmed_id)],
        warnings=(
            ["该 Spec 使用 estimate_preview 受控假设，输出会保留完整度限制"]
            if estimate_preview else []
        ),
        next_actions=["调用 acquisition_run_model"],
    )


def run_model(
    workspace_id: str, spec_id: str, discount_rate: float,
    scenario_id: str, idempotency_key: str,
) -> dict[str, Any]:
    saved = acquisition_service.get_spec(
        workspace_id,
        spec_id,
    )
    if not saved:
        return _failed({"error": "SPEC_NOT_FOUND"}, "SPEC_NOT_FOUND")
    if saved.get("confirmation_status") != "confirmed":
        return _failed({"error": "SPEC_NOT_CONFIRMED"}, "SPEC_NOT_CONFIRMED")
    spec = copy.deepcopy(saved.get("spec") or {})
    checked = validate_spec(spec)
    if not checked["valid"]:
        return checked
    try:
        run = acquisition_service.create_run(
            workspace_id, spec, discount_rate=discount_rate, scenario_id=scenario_id,
            idempotency_key=idempotency_key,
        )
    except AcquisitionModelError as exc:
        return _failed(
            {"error": "SPEC_VALIDATION_FAILED", "details": [str(exc)]},
            "SPEC_VALIDATION_FAILED",
        )
    if not run.get("ok"):
        return _failed(run, "RUN_FAILED")
    if run.get("model_version") not in {"acquisition_model.v3", "acquisition_model.solar.v1"}:
        return _failed({"error": "ACQUISITION_MODEL_UNSUPPORTED"}, "ACQUISITION_MODEL_UNSUPPORTED")
    run_id = str(run["run_id"])
    estimate_preview = run.get("delivery_mode") == "estimate_preview"
    return _ok(
        {"run_id": run_id, "spec_id": run.get("spec_id"), "spec_hash": run.get("spec_hash"),
         "input_hash": run.get("input_hash"), "model_version": run.get("model_version"),
         "delivery_mode": run.get("delivery_mode"),
         "formal_spec_valid": bool(run.get("formal_spec_valid")),
         "evidence_binding_hash": run.get("evidence_binding_hash"),
         "validation_status": {
             "consistency_ok": bool(run.get("consistency_ok")),
             "formal_spec_valid": bool(run.get("formal_spec_valid")),
             "evidence_formal_ok": bool(run.get("evidence_formal_ok")),
        }, "idempotent_replay": bool(run.get("idempotent_replay"))},
        object_id=run_id, uris=[_uri(workspace_id, "runs", run_id)],
        warnings=(
            ["该运行使用 estimate_preview 输入，结果会保留完整度限制"]
            if estimate_preview else []
        ),
        next_actions=["可创建情景矩阵、求解最高价格或生成工件"],
    )


def get_run(
    workspace_id: str,
    run_id: str,
    view: str,
) -> dict[str, Any]:
    run = acquisition_service.get_run(
        workspace_id,
        run_id,
    )
    if not run:
        return _failed({"error": "RUN_NOT_FOUND"}, "RUN_NOT_FOUND")
    summary_keys = {
        "run_id", "workspace_id", "status", "available", "lifecycle_status", "model_version",
        "spec_version", "spec_id", "spec_hash", "input_hash", "evidence_binding_hash",
        "scenario_id", "created_at",
    }
    validation_keys = {
        "run_id", "issues", "consistency_ok", "formal_spec_valid",
        "formal_spec_errors", "evidence_formal_ok", "evidence_status",
        "max_acquisition_price_analysis",
    }
    if view == "summary":
        payload = {key: copy.deepcopy(run.get(key)) for key in summary_keys}
        payload["indicators"] = copy.deepcopy((run.get("result") or {}).get("indicators") or {})
    elif view == "result":
        payload = {key: copy.deepcopy(run.get(key)) for key in summary_keys}
        payload["result"] = copy.deepcopy(run.get("result") or {})
    elif view == "governance":
        payload = {key: copy.deepcopy(run.get(key)) for key in validation_keys}
    else:
        payload = copy.deepcopy(run)
    return _ok({"run_id": run_id, "view": view, "run": payload}, object_id=run_id,
               uris=[_uri(workspace_id, "runs", run_id)])


def create_scenario_matrix(
    workspace_id: str, run_id: str, dimensions: dict[str, Any], idempotency_key: str,
) -> dict[str, Any]:
    row = acquisition_service.create_scenario_matrix(
        workspace_id, run_id, dimensions, idempotency_key=idempotency_key,
    )
    if not row.get("ok"):
        return _failed(row, "SCENARIO_MATRIX_FAILED")
    matrix_id = str(row.get("matrix_id") or "")
    return _ok(
        {"run_id": run_id, "scenario_matrix_id": matrix_id,
         "combination_count": row.get("combination_count"), "matrix_hash": row.get("matrix_hash"),
         "dimensions": row.get("dimensions"), "idempotent_replay": bool(row.get("idempotent_replay"))},
        object_id=matrix_id, uris=[_uri(workspace_id, "scenario-matrices", matrix_id)],
    )


def solve_max_price(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    payload = {key: copy.deepcopy(args.get(key)) for key in (
        "run_id", "target_irr", "min_dscr", "lower", "upper"
    )}

    def action() -> dict[str, Any]:
        row = acquisition_service.max_price(
            workspace_id, str(args["run_id"]), target_irr=args.get("target_irr"),
            min_dscr=args.get("min_dscr"), lower=float(args.get("lower", 0)),
            upper=args.get("upper"),
        )
        if not row.get("ok"):
            return _failed(row, "MAX_PRICE_FAILED")
        run_id = str(args["run_id"])
        return _ok(row, object_id=str(row.get("analysis_hash") or run_id),
                   uris=[_uri(workspace_id, "runs", run_id)])

    return _mutation(
        workspace_id,
        "acquisition_solve_max_price",
        args["idempotency_key"],
        payload,
        action,
    )


def generate_artifact(
    workspace_id: str,
    run_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    row = acquisition_service.generate_artifacts(
        workspace_id,
        run_id,
        idempotency_key=idempotency_key,
    )
    if not row.get("ok"):
        return _failed(row, "ARTIFACT_GENERATION_FAILED")
    artifact_id = str(row["artifact_id"])
    return _ok(
        {"artifact_id": artifact_id, "run_id": row.get("run_id"),
         "artifact_status": row.get("status"),
         "spec_hash": row.get("spec_hash"), "report_data_hash": row.get("report_data_hash"),
         "integrity_status": row.get("integrity_status"),
         "numeric_consistency": row.get("numeric_consistency"), "files": row.get("files") or [],
         "idempotent_replay": bool(row.get("idempotent_replay"))},
        object_id=artifact_id, uris=[_uri(workspace_id, "artifacts", artifact_id)],
    )


def get_artifact(
    workspace_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    row = acquisition_service.get_artifact(
        workspace_id,
        artifact_id,
    )
    if not row:
        return _failed({"error": "ARTIFACT_NOT_FOUND"}, "ARTIFACT_NOT_FOUND")
    return _ok({"artifact_id": artifact_id, "artifact": row}, object_id=artifact_id,
               uris=[_uri(workspace_id, "artifacts", artifact_id)])


def render_tables(
    workspace_id: str,
    run_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return _mutation(workspace_id, "acquisition_render_tables", idempotency_key,
                     {"run_id": run_id}, lambda: tables.render(
                         workspace_id, run_id,
                     ))


def export_tables(
    workspace_id: str,
    package_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return _mutation(workspace_id, "acquisition_export_tables_xlsx", idempotency_key,
                     {"package_id": package_id}, lambda: tables.export_xlsx(
                         workspace_id, package_id,
                     ))


def export_tables_csv(
    workspace_id: str,
    package_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return _mutation(workspace_id, "acquisition_export_tables_csv", idempotency_key,
                     {"package_id": package_id}, lambda: tables.export_csv(
                         workspace_id, package_id,
                     ))


def resolve_resource(
    uri: str,
) -> tuple[str | bytes, str] | None:
    table_resource = tables.resolve_resource(uri)
    if table_resource is not None:
        return table_resource
    prefix = "lvke://asset-acquisition/workspaces/"
    if not uri.startswith(prefix):
        return None
    parts = uri[len(prefix):].split("/")
    if len(parts) != 3:
        return None
    workspace_id, segment, object_id = parts
    try:
        require_safe_id(workspace_id, "workspace_id")
        require_safe_id(object_id, "object_id")
    except ValueError:
        return None
    if segment == "specs":
        record = acquisition_service.get_spec(
            workspace_id, object_id,
        )
    elif segment == "runs":
        record = acquisition_service.get_run(
            workspace_id, object_id,
        )
    elif segment == "artifacts":
        record = acquisition_service.get_artifact(
            workspace_id, object_id,
        )
    elif segment == "scenario-matrices":
        record = next((
            acquisition_service.get_scenario_matrix(
                workspace_id,
                str(row.get("run_id") or ""),
                object_id,
            )
            for row in acquisition_service.list_runs(
                workspace_id, limit=100,
            )
            if any(item.get("matrix_id") == object_id for item in acquisition_service.list_scenario_matrices(
                workspace_id,
                str(row.get("run_id") or ""),
            ))
        ), {})
    else:
        return None
    if not record:
        return None
    return json.dumps(record, ensure_ascii=False, indent=2, default=str), "application/json"
