"""情景矩阵构造与读取。"""

from __future__ import annotations

import copy
import math
import uuid
from itertools import product
from typing import Any


from lvke_mcp.domains.asset_acquisition.model import INDEPENDENT_SCENARIO_FIELDS, AcquisitionModelError, apply_scenario, run_acquisition_model

from .base import (
    _hash,
    _now,
)

from .store import (
    _active_idempotency_record,
    _idempotency_record,
    _load,
    _save,
    _state_guard,
)


MAX_SCENARIO_MATRIX_COMBINATIONS = 64


def create_scenario_matrix(
    workspace_id: str, run_id: str, dimensions: dict[str, Any], *,
    idempotency_key: str = "", request_id: str = "",
) -> dict[str, Any]:
    """Evaluate a bounded Cartesian product without mutating the bound run/spec."""

    request_id = request_id or f"req_{uuid.uuid4().hex}"
    if not isinstance(dimensions, dict) or not dimensions:
        return {"ok": False, "error": "SCENARIO_MATRIX_INVALID", "details": {"reason": "dimensions_required"}}
    unknown = sorted(set(dimensions) - INDEPENDENT_SCENARIO_FIELDS)
    if unknown:
        return {"ok": False, "error": "SCENARIO_FIELD_UNSUPPORTED", "details": {"fields": unknown}}
    normalized: dict[str, list[Any]] = {}
    for field in sorted(dimensions):
        values = dimensions[field]
        if not isinstance(values, (list, tuple)) or not values:
            return {
                "ok": False, "error": "SCENARIO_MATRIX_INVALID",
                "details": {"field": field, "reason": "non_empty_array_required"},
            }
        rows = list(values)
        fingerprints = [_hash(value) for value in rows]
        if len(fingerprints) != len(set(fingerprints)):
            return {
                "ok": False, "error": "SCENARIO_MATRIX_INVALID",
                "details": {"field": field, "reason": "duplicate_values"},
            }
        normalized[field] = rows
    combination_count = math.prod(len(values) for values in normalized.values())
    if combination_count > MAX_SCENARIO_MATRIX_COMBINATIONS:
        return {
            "ok": False, "error": "SCENARIO_MATRIX_TOO_LARGE",
            "details": {"combination_count": combination_count, "max_combinations": MAX_SCENARIO_MATRIX_COMBINATIONS},
        }
    body_hash = _hash({"run_id": run_id, "dimensions": normalized})
    with _state_guard(workspace_id):
        state = _load(workspace_id)
        run = state["runs"].get(run_id)
        if not run:
            return {"ok": False, "error": "RUN_NOT_FOUND"}
        if run.get("status") != "succeeded" or not run.get("available"):
            return {"ok": False, "error": "RUN_NOT_READY", "details": {"status": run.get("status")}}
        spec_row = state["specs"].get(str(run.get("spec_id") or "")) or {}
        spec = spec_row.get("spec")
        if not isinstance(spec, dict) or _hash(spec) != run.get("spec_hash"):
            return {"ok": False, "error": "RUN_SPEC_MISMATCH"}
        scope = f"scenario_matrix:{run_id}:{idempotency_key}" if idempotency_key else ""
        prior = _active_idempotency_record(state["idempotency"], scope) if scope else None
        if prior:
            if prior.get("body_hash") != body_hash:
                return {
                    "ok": False, "error": "IDEMPOTENCY_CONFLICT",
                    "resource_id": prior.get("matrix_id", ""),
                }
            existing = state["scenario_matrices"].get(prior.get("matrix_id"))
            if not existing:
                raise RuntimeError("scenario matrix idempotency record points to a missing matrix")
            return {**existing, "idempotent_replay": True}
        bound = {
            "run_id": run_id, "spec_id": run.get("spec_id"), "spec_hash": run.get("spec_hash"),
            "input_hash": run.get("input_hash"), "model_version": run.get("model_version"),
            "scenario_id": run.get("scenario_id"),
        }

    fields = list(normalized)
    rows: list[dict[str, Any]] = []
    matrix_id = f"scenario_matrix_{uuid.uuid4().hex}"
    asset_type = str(spec.get("asset_type") or "hotel_lease")
    base_market_rent = copy.deepcopy((spec.get("lease_portfolio") or {}).get("market_rent"))
    for index, values in enumerate(product(*(normalized[field] for field in fields)), 1):
        scenario_id = f"{matrix_id}:row_{index:03d}"
        changes = {field: copy.deepcopy(value) for field, value in zip(fields, values)}
        try:
            scenario_spec, ledger = apply_scenario(spec, changes)
        except AcquisitionModelError as exc:
            return {
                "ok": False, "error": "SCENARIO_MATRIX_INVALID",
                "details": {"scenario_index": index, "reason": str(exc)},
            }
        expected_market_rent = copy.deepcopy(changes.get("lease_portfolio.market_rent", base_market_rent))
        actual_market_rent = copy.deepcopy((scenario_spec.get("lease_portfolio") or {}).get("market_rent"))
        if asset_type == "hotel_lease" and actual_market_rent != expected_market_rent:
            return {
                "ok": False, "error": "SCENARIO_INDEPENDENCE_VIOLATION",
                "details": {"field": "lease_portfolio.market_rent", "expected": expected_market_rent, "actual": actual_market_rent},
            }
        try:
            result = run_acquisition_model(
                scenario_spec, discount_rate=float(run.get("discount_rate") or 0.08),
                scenario_id=scenario_id,
            )
        except AcquisitionModelError as exc:
            return {
                "ok": False, "error": "SCENARIO_MATRIX_INVALID",
                "details": {"scenario_index": index, "changes": changes, "reason": str(exc)},
            }
        solar = dict(scenario_spec.get("solar_operation") or {})
        row = {
            "scenario_id": scenario_id, "changes": changes,
            "scenario_change_ledger": ledger, "scenario_spec_hash": _hash(scenario_spec),
            "result_hash": _hash(result),
            "purchase_price_wan": result.get("purchase_price_wan"),
            "financing_ratio": (scenario_spec.get("transaction") or {}).get("financing_ratio"),
            "indicators": copy.deepcopy(result.get("indicators") or {}),
        }
        if asset_type == "solar_power":
            row.update({
                "tariff_yuan_per_kwh": solar.get("tariff_yuan_per_kwh"),
                "annual_generation_mwh": solar.get("annual_generation_mwh"),
                "utilization_hours": solar.get("utilization_hours"),
                "annual_opex_wan": solar.get("annual_opex_wan"),
            })
        else:
            row.update({
                "market_rent": actual_market_rent,
                "occupancy": copy.deepcopy(
                    (scenario_spec.get("hotel_operation") or {}).get("occupancy")
                ),
            })
        rows.append(row)
    created_at = _now()
    matrix = {
        "ok": True, "matrix_id": matrix_id, "status": "succeeded", "read_only": True,
        "workspace_id": workspace_id, **bound, "dimensions": normalized,
        "combination_count": combination_count, "max_combinations": MAX_SCENARIO_MATRIX_COMBINATIONS,
        "rows": rows, "request_id": request_id, "created_at": created_at,
    }
    matrix["matrix_hash"] = _hash({
        "bound": bound, "dimensions": normalized,
        "rows": [{"changes": row["changes"], "scenario_spec_hash": row["scenario_spec_hash"], "result_hash": row["result_hash"]} for row in rows],
    })
    with _state_guard(workspace_id):
        state = _load(workspace_id)
        current = state["runs"].get(run_id)
        if not current or current.get("spec_hash") != bound["spec_hash"] or current.get("input_hash") != bound["input_hash"]:
            return {"ok": False, "error": "RUN_SPEC_MISMATCH"}
        scope = f"scenario_matrix:{run_id}:{idempotency_key}" if idempotency_key else ""
        if scope and (prior := _active_idempotency_record(state["idempotency"], scope)):
            if prior.get("body_hash") != body_hash:
                return {"ok": False, "error": "IDEMPOTENCY_CONFLICT", "resource_id": prior.get("matrix_id", "")}
            return {**state["scenario_matrices"][prior["matrix_id"]], "idempotent_replay": True}
        state["scenario_matrices"][matrix_id] = matrix
        if scope:
            state["idempotency"][scope] = _idempotency_record(
                scope, body_hash, matrix_id=matrix_id,
            )
        _save(workspace_id, state)
    return matrix


def get_scenario_matrix(
    workspace_id: str,
    run_id: str,
    matrix_id: str,
) -> dict[str, Any]:
    matrix = dict(
        _load(workspace_id)["scenario_matrices"].get(
            matrix_id
        )
        or {}
    )
    if not matrix or matrix.get("run_id") != run_id:
        return {}
    return matrix


def list_scenario_matrices(
    workspace_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    rows = [
        value
        for value in _load(workspace_id)[
            "scenario_matrices"
        ].values()
        if value.get("run_id") == run_id
    ]
    rows.sort(key=lambda value: str(value.get("created_at") or ""), reverse=True)
    return [
        {key: value.get(key) for key in (
            "matrix_id", "matrix_hash", "run_id", "spec_id", "spec_hash", "input_hash",
            "combination_count", "status", "created_at",
        )}
        for value in rows
    ]
