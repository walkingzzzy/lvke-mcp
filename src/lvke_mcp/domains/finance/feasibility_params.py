"""Load CN feasibility default parameter candidates (evidence grade C)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = ROOT / "config" / "feasibility_params_cn_default.v1.yaml"


@lru_cache(maxsize=4)
def load_feasibility_params(path: str | None = None) -> dict[str, Any]:
    target = Path(path) if path else DEFAULT_PATH
    if not target.is_file():
        return {"version": "missing", "evidence_grade": "C", "status": "missing"}
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {"version": "invalid", "evidence_grade": "C", "status": "invalid"}
    data.setdefault("evidence_grade", "C")
    return data


def params_basis_block(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact block safe to attach on finance run raw / package evidence."""
    p = params or load_feasibility_params()
    return {
        "feasibility_params_version": p.get("version"),
        "evidence_grade": p.get("evidence_grade"),
        "status": p.get("status"),
        "income_tax_default_rate": (p.get("income_tax") or {}).get("default_rate"),
        "basic_reserve_rate_default": (p.get("contingency") or {}).get(
            "basic_reserve_rate_default"
        ),
        "initial_wc_ratio": (p.get("working_capital") or {}).get(
            "initial_working_capital_ratio_of_total_wc"
        ),
        "non_operating_require_funding_balance": (p.get("non_operating") or {}).get(
            "require_funding_balance"
        ),
        "note": "C-grade candidates until internal professional ratification",
    }
