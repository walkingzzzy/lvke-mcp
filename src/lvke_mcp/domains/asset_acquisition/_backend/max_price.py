"""最高可接受价求解与差异阻断判定。"""

from __future__ import annotations

import copy
from typing import Any


from lvke_mcp.domains.asset_acquisition.model import solve_max_acquisition_price

from .base import (
    _hash,
    _now,
    _same_optional_number,
)

from .runs import (
    get_run,
)

from .specs import (
    _decision_thresholds,
)

from .store import (
    _close_issue,
    _history_event,
    _load,
    _open_issue,
    _save,
    _state_guard,
)


def _diff_is_blocking(row: dict[str, Any], tolerance: float) -> bool:
    if bool(row.get("blocking")) or row.get("within_tolerance") is False:
        return True
    relative = row.get("relative_difference")
    if relative is None:
        try:
            reference = float(row["reference_value"])
            calculated = float(row["calculated_value"])
            relative = abs(calculated - reference) / max(abs(reference), 1e-12)
        except (KeyError, TypeError, ValueError):
            return False
    try:
        exceeds = abs(float(relative)) > max(float(tolerance), 0.0)
    except (TypeError, ValueError):
        return True
    if not exceeds:
        return False
    validation = str(row.get("validation_status") or "").lower()
    has_reason = bool(str(row.get("validation_reason") or row.get("reason") or "").strip())
    has_evidence = bool(row.get("evidence_ids") or row.get("source_locators"))
    return validation not in {"passed", "resolved", "within_tolerance"} or not has_reason or not has_evidence


def max_price(
    workspace_id: str, run_id: str, *, target_irr: float | None = None,
    min_dscr: float | None = None,
    lower: float = 0.0, upper: float | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    run = get_run(workspace_id, run_id)
    if not run:
        return {"ok": False, "error": "run_not_found"}
    spec = (
        _load(workspace_id)["specs"].get(
            str(run.get("spec_id") or "")
        )
        or {}
    ).get("spec")
    if not isinstance(spec, dict):
        return {"ok": False, "error": "spec_snapshot_missing"}
    thresholds, threshold_error = _decision_thresholds(spec)
    if threshold_error:
        return {
            "ok": False, "error": threshold_error,
            "decision_thresholds": spec.get("decision_thresholds"),
        }
    expected_target = float(thresholds["target_project_irr"] or 0.0)
    expected_dscr = thresholds["minimum_dscr"]
    try:
        effective_target = expected_target if target_irr is None else float(target_irr)
        effective_dscr = expected_dscr if min_dscr is None else float(min_dscr)
        effective_lower = float(lower)
        effective_upper = float(upper) if upper is not None else None
    except (TypeError, ValueError):
        return {
            "ok": False, "error": "MAX_PRICE_PARAMETERS_INVALID",
            "parameters": {
                "target_irr": target_irr, "min_dscr": min_dscr,
                "lower": lower, "upper": upper,
            },
        }
    parameters = {
        "target_irr": effective_target, "min_dscr": effective_dscr,
        "lower": effective_lower, "upper": effective_upper,
    }
    if (
        not _same_optional_number(effective_target, expected_target)
        or not _same_optional_number(effective_dscr, expected_dscr)
    ):
        return {
            "ok": False, "error": "MAX_PRICE_THRESHOLD_MISMATCH",
            "parameters": parameters, "decision_thresholds": thresholds,
        }
    if (
        not 0 < effective_target <= 5
        or (effective_dscr is not None and effective_dscr <= 0)
        or effective_lower < 0
        or (effective_upper is not None and effective_upper <= effective_lower)
    ):
        return {"ok": False, "error": "MAX_PRICE_PARAMETERS_INVALID", "parameters": parameters}
    existing = run.get("max_acquisition_price_analysis") or {}
    if existing.get("parameters") == parameters and isinstance(existing.get("result"), dict):
        expected_hash = _hash({
            key: value for key, value in existing.items() if key != "analysis_hash"
        })
        if existing.get("analysis_hash") != expected_hash:
            return {"ok": False, "error": "MAX_PRICE_HASH_MISMATCH"}
        return {
            "ok": True,
            "run_id": run_id,
            "analysis_hash": existing.get("analysis_hash"),
            "validation_status": existing.get("validation_status") or "passed",
            "idempotent_replay": True,
            **copy.deepcopy(existing.get("result") or {}),
        }
    baseline_analysis_hash = str(existing.get("analysis_hash") or "")
    solved = solve_max_acquisition_price(
        spec, target_irr=effective_target, min_dscr=effective_dscr,
        lower=effective_lower, upper=effective_upper,
    )
    analysis = {
        "status": "calculated",
        "validation_status": (
            "passed" if solved.get("feasible") and solved.get("converged") else "failed"
        ),
        "parameters": parameters,
        "result": copy.deepcopy(solved),
        "calculated_at": _now(),
        "engine_version": run.get("model_version"),
        "request_id": request_id,
    }
    analysis["analysis_hash"] = _hash(analysis)
    with _state_guard(workspace_id):
        state = _load(workspace_id)
        current = state["runs"].get(run_id)
        if not current:
            return {"ok": False, "error": "run_not_found"}
        if current.get("spec_hash") != run.get("spec_hash") or current.get("input_hash") != run.get("input_hash"):
            return {"ok": False, "error": "RUN_SPEC_MISMATCH"}
        current_analysis = current.get("max_acquisition_price_analysis") or {}
        if (
            current_analysis.get("parameters") == parameters
            and isinstance(current_analysis.get("result"), dict)
        ):
            return {
                "ok": True,
                "run_id": run_id,
                "analysis_hash": current_analysis.get("analysis_hash"),
                "validation_status": current_analysis.get("validation_status") or "passed",
                "idempotent_replay": True,
                **copy.deepcopy(current_analysis.get("result") or {}),
            }
        if str(current_analysis.get("analysis_hash") or "") != baseline_analysis_hash:
            return {
                "ok": False,
                "error": "WORKSPACE_VERSION_CONFLICT",
                "resource_id": run_id,
            }
        current["max_acquisition_price_analysis"] = analysis
        if solved.get("feasible") and solved.get("converged"):
            _close_issue(current, "MAX_PRICE_NOT_FEASIBLE", reason="最高可接受价求解可行且收敛")
        else:
            _open_issue(current, "MAX_PRICE_NOT_FEASIBLE", "最高可接受价求解不可行或未收敛")
        current.setdefault("max_acquisition_price_history", []).append(copy.deepcopy(analysis))
        current.setdefault("state_history", []).append(_history_event(
            "max_acquisition_price_calculated",
            request_id=request_id,
            analysis_hash=analysis["analysis_hash"],
            validation_status=analysis["validation_status"],
        ))
        _save(workspace_id, state)
    return {
        "ok": True,
        "run_id": run_id,
        "analysis_hash": analysis["analysis_hash"],
        "validation_status": analysis["validation_status"],
        **solved,
    }
