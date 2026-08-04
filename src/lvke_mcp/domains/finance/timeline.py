"""Unified project timeline (P0-2)."""

from __future__ import annotations

import math
from typing import Any, Optional


def build_timeline(
    *,
    calc_years: int,
    build_period_months: Optional[int] = None,
    build_years: Optional[int] = None,
    loan_years: int = 0,
    loan_grace_years: int = 0,
    depreciation_years: int = 0,
    amortization_years: int = 0,
) -> dict[str, Any]:
    """Map investment / operations / debt / depreciation onto one period index.

    Periods are annual indices 0..n-1. Construction occupies the first
    ``build_years`` periods; operations start at ``op_start``.
    """
    if build_years is None:
        months = int(build_period_months or 12)
        build_years = max(1, math.ceil(months / 12.0))
    build_years = max(1, int(build_years))
    calc_years = max(build_years + 1, int(calc_years or (build_years + 1)))
    op_years = max(calc_years - build_years, 1)
    op_start = build_years
    periods = []
    for t in range(calc_years):
        phase = "建设期" if t < build_years else "运营期"
        op_year = (t - op_start + 1) if t >= op_start else None
        in_grace = bool(op_year is not None and op_year <= max(int(loan_grace_years or 0), 0))
        in_repay = bool(
            op_year is not None
            and not in_grace
            and op_year <= max(int(loan_grace_years or 0), 0) + max(int(loan_years or 0), 0)
        )
        dep_active = bool(op_year is not None and op_year <= max(int(depreciation_years or 0), 0))
        amort_active = bool(op_year is not None and op_year <= max(int(amortization_years or 0), 0))
        periods.append(
            {
                "t": t,
                "year_index": t + 1,
                "phase": phase,
                "op_year": op_year,
                "in_grace": in_grace,
                "in_repay": in_repay,
                "dep_active": dep_active,
                "amort_active": amort_active,
            }
        )
    return {
        "mode": "annual",
        "calc_years": calc_years,
        "build_years": build_years,
        "op_years": op_years,
        "op_start": op_start,
        "loan_years": int(loan_years or 0),
        "loan_grace_years": int(loan_grace_years or 0),
        "depreciation_years": int(depreciation_years or 0),
        "amortization_years": int(amortization_years or 0),
        "periods": periods,
    }
