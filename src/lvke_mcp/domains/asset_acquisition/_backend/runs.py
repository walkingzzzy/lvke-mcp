"""run 生命周期：创建、入队、执行与读取列举。"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from typing import Any


from lvke_mcp.runtime.evidence_qualification import project_fact_may_be_certified
from lvke_mcp.domains.asset_acquisition.model import (
    AcquisitionModelError, projection_consistency_ok, run_acquisition_model,
)
from lvke_mcp.domains.finance.spec import LATEST_SPEC_VERSION, validate, validate_for_formal

from .base import (
    _LOG,
    _RUN_EXECUTION_FAILURE_MESSAGE,
    _RUN_VALIDATION_FAILURE_MESSAGE,
    _SOURCE_EVIDENCE_FAILURE_MESSAGE,
    _hash,
    _now,
)

from .evidence import (
    _bind_spec_evidence,
    _evidence_blocking_issues,
    _evidence_error_strings,
    _formal_assessment,
    _is_estimate_preview_spec,
    _is_process_acceptance_spec,
)

from .specs import (
    save_spec,
)

from .store import (
    _active_idempotency_record,
    _history_event,
    _idempotency_record,
    _load,
    _migration_binding,
    _save,
    _state_guard,
)


def _is_selected_scenario(spec: Mapping[str, Any], scenario_id: str) -> bool:
    """Report whether the requested scenario matches the recorded selection."""

    selected = str(spec.get("selected_scenario_id") or "base").strip()
    requested = str(scenario_id or "base").strip()
    return bool(selected and requested and requested == selected)


def _with_execution_defaults(
    spec: dict[str, Any], scenario_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Add only engine-startup defaults and record every injected assumption."""

    normalized = copy.deepcopy(spec)
    assumptions: list[dict[str, Any]] = []

    def default(container: dict[str, Any], key: str, value: Any, path: str) -> None:
        if container.get(key) not in (None, ""):
            return
        container[key] = copy.deepcopy(value)
        assumptions.append({
            "path": path,
            "value": copy.deepcopy(value),
            "source_type": "deterministic_execution_default",
            "confidence": "low",
            "reason": "缺少该字段时收购模型无法启动；未覆盖用户输入。",
        })

    default(normalized, "version", LATEST_SPEC_VERSION, "/version")
    default(normalized, "finance_kind", "asset_acquisition", "/finance_kind")
    default(normalized, "invest_type", "asset_acquisition", "/invest_type")
    default(normalized, "asset_type", "hotel_lease", "/asset_type")
    default(normalized, "confirmation_status", "candidate", "/confirmation_status")
    default(normalized, "selected_scenario_id", str(scenario_id or "base"), "/selected_scenario_id")

    transaction = normalized.get("transaction")
    if transaction is None:
        transaction = {}
        normalized["transaction"] = transaction
    if isinstance(transaction, dict):
        default(transaction, "purchase_price", 1.0, "/transaction/purchase_price")
        default(transaction, "acquisition_type", "asset", "/transaction/acquisition_type")
        default(transaction, "model_start_date", "2026-01-01", "/transaction/model_start_date")
        default(transaction, "exit_year", 10, "/transaction/exit_year")
        default(transaction, "tenor", 10, "/transaction/tenor")
        default(transaction, "financing_ratio", 0.0, "/transaction/financing_ratio")
        default(transaction, "interest_rate", 0.0, "/transaction/interest_rate")
        default(transaction, "repayment", "equal_principal", "/transaction/repayment")

    asset_type = str(normalized.get("asset_type") or "hotel_lease")
    if asset_type == "solar_power":
        if isinstance(transaction, dict):
            default(transaction, "calculation_granularity", "annual", "/transaction/calculation_granularity")
        solar = normalized.get("solar_operation")
        if solar is None:
            solar = {}
            normalized["solar_operation"] = solar
        if isinstance(solar, dict):
            default(solar, "installed_capacity_mw", 0.001, "/solar_operation/installed_capacity_mw")
            default(solar, "tariff_yuan_per_kwh", 0.01, "/solar_operation/tariff_yuan_per_kwh")
            # 不再给 annual_utilization_hours / projection_years 注默认值：
            # 引擎读的是 `utilization_hours`（solar_engine.py:49）与
            # `remaining_operating_years`（:56），**键名不同**，全仓无任何消费方
            # （已 grep 确认）。填这两个键等于给永不被读的字段注值，却因此在
            # warnings 里产出 low-confidence 的 EXECUTION_DEFAULT_APPLIED 记录：
            # 实测报「annual_utilization_hours 默认为 1.0」而发电量仍按输入的
            # 1250h 正确计算、报「projection_years 默认为 10」而实际用了 20 年。
            # 这类假阳性把真实缺口淹没在噪音里，比不报更糟。
            # 真正缺 utilization_hours 时，引擎自己会 fail-closed 抛
            # AcquisitionModelError（:51-54），不需要在这里兜底。
        normalized.setdefault("revenue", {})
    else:
        if isinstance(transaction, dict):
            default(transaction, "calculation_granularity", "monthly", "/transaction/calculation_granularity")
            default(transaction, "operating_mode", "owner_lessor", "/transaction/operating_mode")
        revenue = normalized.get("revenue")
        if revenue is None:
            revenue = {}
            normalized["revenue"] = revenue
        if isinstance(revenue, dict):
            default(revenue, "model", "flat", "/revenue/model")
            # 同理不注 annual_revenue_wan：收购域的收入由 hotel_operation 与
            # lease_portfolio 驱动（monthly_engine 的 room_revenue + lease_revenue），
            # 全仓无消费方读 revenue.annual_revenue_wan。注了只会产出
            # 「/revenue/annual_revenue_wan 默认为 1.0」这条与实际计算无关的假阳性。
        hotel = normalized.get("hotel_operation")
        if hotel is None:
            hotel = {}
            normalized["hotel_operation"] = hotel
        if isinstance(hotel, dict):
            default(hotel, "rooms", 1, "/hotel_operation/rooms")
            default(hotel, "adr", 1.0, "/hotel_operation/adr")
            default(hotel, "occupancy", 0.01, "/hotel_operation/occupancy")
        portfolio = normalized.get("lease_portfolio")
        if portfolio is None:
            portfolio = {}
            normalized["lease_portfolio"] = portfolio
        if isinstance(portfolio, dict):
            default(portfolio, "projection_years", 10, "/lease_portfolio/projection_years")

    ledger = normalized.get("assumption_ledger")
    if not isinstance(ledger, list):
        ledger = []
    normalized["assumption_ledger"] = [*ledger, *assumptions]
    return normalized, assumptions


def create_run(
    workspace_id: str, spec: dict[str, Any], *, discount_rate: float = 0.08,
    scenario_id: str = "base", idempotency_key: str = "", request_id: str = "",
    scenario_change_ledger: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scenario_matches = _is_selected_scenario(spec, scenario_id)
    spec, execution_assumptions = _with_execution_defaults(spec, scenario_id)
    estimate_preview = _is_estimate_preview_spec(spec)
    process_acceptance = _is_process_acceptance_spec(spec)
    schema_ok, schema_errors = (
        validate(spec) if estimate_preview or process_acceptance else validate_for_formal(spec)
    )
    evidence_binding = _bind_spec_evidence(
        workspace_id,
        spec,
    )
    request_id = request_id or f"req_{uuid.uuid4().hex}"
    saved_spec = save_spec(
        workspace_id, spec, request_id=request_id, trusted_confirmation=True,
    )
    if not saved_spec.get("ok"):
        return saved_spec
    spec = copy.deepcopy(saved_spec.get("spec") or {})
    evidence_binding = copy.deepcopy(saved_spec.get("evidence_binding") or {})
    body = {"spec": spec, "discount_rate": discount_rate, "scenario_id": scenario_id}
    body_hash = _hash(body)
    with _state_guard(workspace_id):
        state = _load(workspace_id)
        scope = f"run:{idempotency_key}" if idempotency_key else ""
        prior = _active_idempotency_record(state["idempotency"], scope) if scope else None
        if prior:
            if prior["body_hash"] != body_hash:
                return {"ok": False, "error": "IDEMPOTENCY_CONFLICT", "resource_id": prior["run_id"]}
            existing = state["runs"].get(prior["run_id"])
            if not existing:
                raise RuntimeError("run idempotency record points to a missing run")
            return {**existing, "idempotent_replay": True}
        result = run_acquisition_model(spec, discount_rate=discount_rate, scenario_id=scenario_id)
        run_id = f"acqrun_{uuid.uuid4().hex}"
        schema_formal_ok, schema_errors = validate_for_formal(spec)
        formal_ok, formal_errors, evidence_binding = _formal_assessment(
            workspace_id,
            spec,
            evidence_binding=evidence_binding,
        )
        created_at = _now()
        issues = [
            {"code": "SPEC_VALIDATION_FAILED", "blocking": False, "status": "open", "detail": item, "created_at": created_at}
            for item in schema_errors
        ]
        issues.extend(
            {
                "code": "EXECUTION_DEFAULT_APPLIED",
                "blocking": False,
                "status": "open",
                "detail": item,
                "created_at": created_at,
            }
            for item in execution_assumptions
        )
        if not scenario_matches:
            issues.append({
                "code": "SCENARIO_SELECTION_MISMATCH",
                "blocking": False,
                "status": "open",
                "detail": {
                    "selected": spec.get("selected_scenario_id"),
                    "requested": scenario_id,
                },
                "created_at": created_at,
            })
        issues.extend(_evidence_blocking_issues(evidence_binding, created_at=created_at))
        for issue in issues:
            issue["blocking"] = False
        scenario_ledger = list(scenario_change_ledger or [])
        invalid_scenario_sources = [
            index for index, item in enumerate(scenario_ledger)
            if not isinstance(item, Mapping)
            or not str(item.get("source") or "").strip()
        ]
        if invalid_scenario_sources:
            issues.append({
                "code": "SCENARIO_SOURCE_REQUIRED", "blocking": False, "status": "open",
                "detail": "情景调整缺少可追溯来源", "rows": invalid_scenario_sources,
                "created_at": created_at,
            })
        row = {
            "ok": True, "available": True, "run_id": run_id, "workspace_id": workspace_id,
            "status": "succeeded",
            "lifecycle_status": "validated" if not issues else "validation_failed",
            "delivery_mode": (
                "estimate_preview" if estimate_preview else (
                    "process_acceptance" if process_acceptance else "formal_candidate"
                )
            ),
            "model_version": result["model_version"],
            "spec_version": LATEST_SPEC_VERSION, "spec_hash": _hash(spec), "input_hash": body_hash,
            "spec_id": saved_spec.get("spec_id"),
            "spec_snapshot_hash": saved_spec.get("snapshot_hash"),
            "evidence_binding_version": evidence_binding.get("binding_version"),
            "evidence_binding_hash": evidence_binding.get("binding_hash"),
            "evidence_status": evidence_binding.get("status"),
            "evidence_formal_ok": bool(evidence_binding.get("formal_ok")),
            "evidence_binding": evidence_binding,
            "scenario_id": scenario_id, "scenario_change_ledger": scenario_ledger,
            "discount_rate": discount_rate, "result": result,
            "consistency_ok": projection_consistency_ok(result),
            "validation_status": "passed" if not issues else "failed",
            "formal_spec_valid": bool(schema_formal_ok and formal_ok),
            "process_acceptance_valid": bool(process_acceptance and schema_ok),
            "formal_spec_errors": formal_errors, "request_id": request_id,
            "created_at": created_at,
            "evidence_policy": str(spec.get("evidence_policy") or "formal_evidence"),
            # 不采信 spec 自报，也不把 "非重建" 当作已认证。
            "project_fact_certified": project_fact_may_be_certified(
                str(spec.get("evidence_policy") or "formal_evidence"),
                own_qualification_passed=bool(schema_ok and not formal_errors),
            ),
            "reconstruction_records": copy.deepcopy(spec.get("reconstruction_records") or []),
            "reconstructed_source_ids": copy.deepcopy(spec.get("reconstructed_source_ids") or []),
            "unresolved_inputs": copy.deepcopy(spec.get("unresolved_inputs") or []),
            "release_limitations": copy.deepcopy(spec.get("release_limitations") or []),
            "business_decision_status": str(spec.get("business_decision_status") or "not_selected"),
            "assumptions": copy.deepcopy(execution_assumptions),
            **_migration_binding(spec),
            "issues": issues,
            "state_history": [
                _history_event("validated_spec", request_id=request_id),
                _history_event("running", request_id=request_id),
                _history_event("calculated", request_id=request_id),
                _history_event("internally_consistent", request_id=request_id),
                _history_event(
                    "validated" if not issues else "validation_failed",
                    request_id=request_id,
                ),
            ],
        }
        state["runs"][run_id] = row
        if scope:
            state["idempotency"][scope] = _idempotency_record(
                scope, body_hash, run_id=run_id,
            )
        _save(workspace_id, state)
    return row


def enqueue_run(
    workspace_id: str, spec: dict[str, Any], *, discount_rate: float = 0.08,
    scenario_id: str = "base", idempotency_key: str = "", request_id: str = "",
    scenario_change_ledger: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist a pollable queued run before any model calculation starts."""

    scenario_matches = _is_selected_scenario(spec, scenario_id)
    spec, execution_assumptions = _with_execution_defaults(spec, scenario_id)
    schema_ok, schema_errors = validate_for_formal(spec)
    evidence_binding = _bind_spec_evidence(
        workspace_id,
        spec,
    )
    request_id = request_id or f"req_{uuid.uuid4().hex}"
    saved_spec = save_spec(
        workspace_id, spec, request_id=request_id, trusted_confirmation=True,
    )
    if not saved_spec.get("ok"):
        return saved_spec
    spec = copy.deepcopy(saved_spec.get("spec") or {})
    evidence_binding = copy.deepcopy(saved_spec.get("evidence_binding") or {})
    body = {"spec": spec, "discount_rate": discount_rate, "scenario_id": scenario_id}
    body_hash = _hash(body)
    schema_formal_ok, _schema_errors = validate_for_formal(spec)
    formal_ok, formal_errors, evidence_binding = _formal_assessment(
        workspace_id,
        spec,
        evidence_binding=evidence_binding,
    )
    with _state_guard(workspace_id):
        state = _load(workspace_id)
        scope = f"run:{idempotency_key}" if idempotency_key else ""
        prior = _active_idempotency_record(state["idempotency"], scope) if scope else None
        if prior:
            if prior.get("body_hash") != body_hash:
                return {"ok": False, "error": "IDEMPOTENCY_CONFLICT", "resource_id": prior.get("run_id", "")}
            existing = state["runs"].get(prior.get("run_id"))
            if not existing:
                raise RuntimeError("run idempotency record points to a missing run")
            return {**existing, "idempotent_replay": True}
        run_id = f"acqrun_{uuid.uuid4().hex}"
        created_at = _now()
        issues = [
            {
                "code": "SPEC_VALIDATION_FAILED",
                "blocking": False,
                "status": "open",
                "detail": item,
                "created_at": created_at,
            }
            for item in schema_errors
        ]
        issues.extend(
            {
                "code": "EXECUTION_DEFAULT_APPLIED",
                "blocking": False,
                "status": "open",
                "detail": item,
                "created_at": created_at,
            }
            for item in execution_assumptions
        )
        issues.extend(_evidence_blocking_issues(evidence_binding, created_at=created_at))
        for issue in issues:
            issue["blocking"] = False
        if not scenario_matches:
            issues.append({
                "code": "SCENARIO_SELECTION_MISMATCH",
                "blocking": False,
                "status": "open",
                "detail": {"selected": spec.get("selected_scenario_id"), "requested": scenario_id},
                "created_at": created_at,
            })
        row = {
            "ok": True, "available": False, "run_id": run_id, "workspace_id": workspace_id,
            "status": "queued", "progress": 0, "lifecycle_status": "validated_spec",
            "spec_version": LATEST_SPEC_VERSION, "spec_hash": _hash(spec), "input_hash": body_hash,
            "spec_id": saved_spec.get("spec_id"), "scenario_id": scenario_id,
            "spec_snapshot_hash": saved_spec.get("snapshot_hash"),
            "evidence_binding_version": evidence_binding.get("binding_version"),
            "evidence_binding_hash": evidence_binding.get("binding_hash"),
            "evidence_status": evidence_binding.get("status"),
            "evidence_formal_ok": bool(evidence_binding.get("formal_ok")),
            "evidence_binding": evidence_binding,
            "scenario_change_ledger": list(scenario_change_ledger or []), "discount_rate": discount_rate,
            "validation_status": "pending",
            "formal_spec_valid": bool(schema_formal_ok and formal_ok),
            "formal_spec_errors": formal_errors,
            "request_id": request_id, "created_at": created_at, "updated_at": created_at,
            "issues": issues,
            "evidence_policy": str(spec.get("evidence_policy") or "formal_evidence"),
            # 同上：不采信自报，非重建也不等于已认证。
            "project_fact_certified": project_fact_may_be_certified(
                str(spec.get("evidence_policy") or "formal_evidence"),
                own_qualification_passed=bool(schema_formal_ok and formal_ok),
            ),
            "reconstruction_records": copy.deepcopy(spec.get("reconstruction_records") or []),
            "reconstructed_source_ids": copy.deepcopy(spec.get("reconstructed_source_ids") or []),
            "unresolved_inputs": copy.deepcopy(spec.get("unresolved_inputs") or []),
            "release_limitations": copy.deepcopy(spec.get("release_limitations") or []),
            "business_decision_status": str(spec.get("business_decision_status") or "not_selected"),
            "assumptions": copy.deepcopy(execution_assumptions),
            **_migration_binding(spec),
            "state_history": [
                _history_event("validated_spec", request_id=request_id),
                _history_event("queued", request_id=request_id),
            ],
        }
        state["runs"][run_id] = row
        if scope:
            state["idempotency"][scope] = _idempotency_record(
                scope, body_hash, run_id=run_id,
            )
        _save(workspace_id, state)
        return row


def execute_queued_run(
    workspace_id: str,
    run_id: str,
) -> None:
    """Execute one durable queued run and converge it to succeeded/failed."""

    with _state_guard(workspace_id):
        state = _load(workspace_id)
        run = state["runs"].get(run_id)
        if not run or run.get("status") in {"succeeded", "failed", "cancelled"}:
            return
        spec_row = state["specs"].get(str(run.get("spec_id") or "")) or {}
        spec = spec_row.get("spec")
        if not isinstance(spec, dict):
            run.update({
                "status": "failed", "progress": 100, "updated_at": _now(),
                "error": {"code": "SPEC_VALIDATION_FAILED", "message": "spec snapshot missing", "retryable": False},
            })
            _save(workspace_id, state)
            return
        run.update({"status": "running", "progress": 10, "lifecycle_status": "running", "updated_at": _now()})
        run.setdefault("state_history", []).append(_history_event(
            "running", request_id=str(run.get("request_id") or ""),
        ))
        discount_rate = float(run.get("discount_rate") or 0.08)
        scenario_id = str(run.get("scenario_id") or "base")
        _save(workspace_id, state)
    try:
        current_evidence = _bind_spec_evidence(
            workspace_id,
            spec,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "asset acquisition evidence binding failed; error_type=%s",
            type(exc).__name__,
        )
        current_evidence = {
            "formal_ok": False,
            "status": "invalid",
            "binding_version": "",
            "binding_hash": "",
            "bindings": [],
            "missing": [],
            "pending": [],
            "invalid": [{
                "source_path": "$",
                "code": "SOURCE_EVIDENCE_STATE_INVALID",
                "message": _SOURCE_EVIDENCE_FAILURE_MESSAGE,
            }],
        }
    evidence_hash_matches = bool(
        current_evidence.get("binding_hash") == run.get("evidence_binding_hash")
        and current_evidence.get("binding_version") == run.get("evidence_binding_version")
    )
    evidence_snapshot_matches = bool(evidence_hash_matches and current_evidence.get("formal_ok"))
    try:
        result = run_acquisition_model(spec, discount_rate=discount_rate, scenario_id=scenario_id)
    except Exception as exc:  # noqa: BLE001
        code = "SPEC_VALIDATION_FAILED" if isinstance(exc, AcquisitionModelError) else "RUN_FAILED"
        safe_message = (
            _RUN_VALIDATION_FAILURE_MESSAGE
            if code == "SPEC_VALIDATION_FAILED"
            else _RUN_EXECUTION_FAILURE_MESSAGE
        )
        _LOG.warning(
            "asset acquisition run failed; run_id=%s error_type=%s code=%s",
            run_id,
            type(exc).__name__,
            code,
        )
        with _state_guard(workspace_id):
            state = _load(workspace_id)
            run = state["runs"].get(run_id)
            if run and run.get("status") != "cancelled":
                run.update({
                    "status": "failed", "progress": 100, "lifecycle_status": "failed", "updated_at": _now(),
                    "error": {"code": code, "message": safe_message, "retryable": False},
                })
                run.setdefault("state_history", []).append(_history_event(
                    "failed", request_id=str(run.get("request_id") or ""), error_code=code,
                ))
                _save(workspace_id, state)
        return

    with _state_guard(workspace_id):
        state = _load(workspace_id)
        run = state["runs"].get(run_id)
        if not run or run.get("status") == "cancelled":
            return
        created_at = str(run.get("created_at") or _now())
        schema_formal_ok, schema_errors = validate_for_formal(spec)
        formal_errors = [*schema_errors, *_evidence_error_strings(current_evidence)]
        if not evidence_hash_matches:
            formal_errors.append(
                "finance_spec.v3 证据绑定快照已变化或不再满足正式复核条件"
            )
        issues = [
            {"code": "SPEC_VALIDATION_FAILED", "blocking": True, "status": "open", "detail": item, "created_at": created_at}
            for item in schema_errors
        ]
        issues.extend(_evidence_blocking_issues(current_evidence, created_at=created_at))
        if not evidence_hash_matches:
            issues.append({
                "code": "EVIDENCE_BINDING_STALE",
                "blocking": True,
                "status": "open",
                "detail": (
                    "运行排队后证据绑定状态发生变化；必须保存新Spec修订并重新运行。"
                    f" snapshot={run.get('evidence_binding_hash')} current={current_evidence.get('binding_hash')}"
                ),
                "created_at": created_at,
            })
        scenario_ledger = list(run.get("scenario_change_ledger") or [])
        invalid_scenario_sources = [
            index for index, item in enumerate(scenario_ledger)
            if not isinstance(item, Mapping)
            or not str(item.get("source") or "").strip()
        ]
        if invalid_scenario_sources:
            issues.append({
                "code": "SCENARIO_SOURCE_REQUIRED", "blocking": False, "status": "open",
                "detail": "情景调整缺少可追溯来源", "rows": invalid_scenario_sources,
                "created_at": created_at,
            })
        run.update({
            "available": True, "status": "succeeded", "progress": 100,
            "lifecycle_status": "validated" if not issues else "validation_failed",
            "model_version": result["model_version"],
            "result": result, "consistency_ok": projection_consistency_ok(result),
            "issues": issues, "updated_at": _now(),
            "validation_status": "passed" if not issues else "failed",
            "formal_spec_valid": bool(schema_formal_ok and evidence_snapshot_matches),
            "formal_spec_errors": formal_errors,
            "evidence_revalidation": {
                "checked_at": _now(),
                "matches_snapshot": evidence_hash_matches,
                "binding_version": current_evidence.get("binding_version"),
                "binding_hash": current_evidence.get("binding_hash"),
                "status": current_evidence.get("status"),
                "formal_ok": bool(current_evidence.get("formal_ok")),
            },
        })
        run.setdefault("state_history", []).extend([
            _history_event("calculated", request_id=str(run.get("request_id") or "")),
            _history_event("internally_consistent", request_id=str(run.get("request_id") or "")),
            _history_event(
                "validated" if not issues else "validation_failed",
                request_id=str(run.get("request_id") or ""),
            ),
        ])
        _save(workspace_id, state)


def get_run(
    workspace_id: str,
    run_id: str,
) -> dict[str, Any]:
    return dict(
        _load(workspace_id)["runs"].get(run_id) or {}
    )


def list_runs(
    workspace_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return lightweight acquisition run summaries newest-first."""

    rows: list[dict[str, Any]] = []
    summary_keys = (
        "run_id", "workspace_id", "status", "lifecycle_status",
        "model_version", "spec_version", "spec_hash", "input_hash",
        "spec_id", "scenario_id", "discount_rate", "consistency_ok",
        "spec_snapshot_hash", "evidence_binding_version", "evidence_binding_hash",
        "evidence_status", "evidence_formal_ok", "evidence_revalidation",
        "validation_status", "formal_spec_valid", "formal_spec_errors",
        "request_id", "created_at", "issues",
    )
    for run in _load(workspace_id)["runs"].values():
        indicators = (run.get("result") or {}).get("indicators") or {}
        row = {key: copy.deepcopy(run.get(key)) for key in summary_keys}
        row.update({
            "invest_type": "asset_acquisition",
            "industry": "资产收购",
            "available": bool(run.get("available")),
            "started_at": run.get("created_at"),
            "finished_at": run.get("finished_at") or run.get("created_at"),
            "indicators": {
                key: copy.deepcopy(indicators.get(key))
                for key in ("project_irr_pct", "equity_irr_pct", "npv_wan", "minimum_dscr")
            },
        })
        rows.append(row)
    rows.sort(
        key=lambda row: (str(row.get("created_at") or ""), str(row.get("run_id") or "")),
        reverse=True,
    )
    return rows[: max(1, min(int(limit or 50), 10_000))]
