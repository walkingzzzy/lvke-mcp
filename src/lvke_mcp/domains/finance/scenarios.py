"""Sensitivity / scenario full-model rerun helpers (P1-5)."""

from __future__ import annotations

from typing import Any, Callable, Optional


def build_sensitivity(
    result: dict[str, Any],
    *,
    rerun: Callable[..., Optional[dict[str, Any]]],
    deltas: Optional[list[float]] = None,
) -> dict[str, Any]:
    deltas = deltas or [-0.20, -0.10, 0.0, 0.10, 0.20]
    op = result.get("operating") or {}
    if not op.get("cashflows") or not result.get("indicators"):
        return {}
    base_irr = (result.get("indicators") or {}).get("project_irr_pct")

    def _extract(rr: Optional[dict[str, Any]]) -> dict[str, Any]:
        ind = (rr or {}).get("indicators") or {}
        return {"irr_pct": ind.get("project_irr_pct"), "npv_wan": ind.get("npv_wan")}

    def _scan(kind: str) -> list[dict]:
        out = []
        for d in deltas:
            if d == 0.0:
                cell = _extract(result)
            elif kind == "revenue":
                cell = _extract(rerun(rev=1 + d))
            elif kind == "op_cost":
                cell = _extract(rerun(cost=1 + d))
            else:
                cell = _extract(rerun(constr=1 + d))
            cell["delta"] = d
            if base_irr and cell.get("irr_pct") is not None and d != 0.0 and base_irr != 0:
                cell["sensitivity_coef"] = round((cell["irr_pct"] - base_irr) / base_irr / d, 3)
            out.append(cell)
        return out

    return {
        "revenue": _scan("revenue"),
        "op_cost": _scan("op_cost"),
        "construction": _scan("construction"),
        "deltas": deltas,
        "method": "full_model_rerun",
    }


def build_scenarios(
    result: dict[str, Any],
    *,
    rerun: Callable[..., Optional[dict[str, Any]]],
) -> dict[str, Any]:
    op = result.get("operating") or {}
    if not op.get("cashflows") or not result.get("indicators"):
        return {}

    def _make(rev_d: float, cost_d: float, base: bool = False) -> dict:
        rr = result if base else rerun(rev=1 + rev_d, cost=1 + cost_d)
        ind = (rr or {}).get("indicators") or {}
        return {"irr_pct": ind.get("project_irr_pct"), "npv_wan": ind.get("npv_wan")}

    return {
        "base": _make(0.0, 0.0, base=True),
        "bull": _make(0.10, -0.05),
        "bear": _make(-0.10, 0.05),
        "method": "full_model_rerun",
    }
