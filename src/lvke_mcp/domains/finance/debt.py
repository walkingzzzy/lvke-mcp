"""Debt service schedules (P1-4): multiple repayment methods + ICR/DSCR helpers."""

from __future__ import annotations

from typing import Any, Optional


REPAY_EQUAL_PRINCIPAL = "equal_principal"
REPAY_EQUAL_INSTALLMENT = "equal_installment"  # 等额本息
REPAY_interest_only = "interest_only"  # 到期还本 / 期内只付息
REPAY_BALLOON = "balloon"  # 气球：期内部分还本，期末清剩余


def _f(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_debt_schedule(
    loan: float,
    years: int,
    rate: float,
    op_years: int,
    *,
    method: str = REPAY_EQUAL_PRINCIPAL,
    grace_years: int = 0,
    balloon_pct: float = 0.3,
    principal_schedule: list[float] | None = None,
    interest_schedule: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Operating-period debt schedule with optional grace (interest only).

    Each row: year, begin, principal, interest, end, method, in_grace.
    """
    loan = _f(loan)
    years = max(int(years or 0), 0)
    rate = _f(rate)
    op_years = max(int(op_years or 0), 0)
    grace_years = max(int(grace_years or 0), 0)
    method = (method or REPAY_EQUAL_PRINCIPAL).strip().lower()
    if method in ("等额本息", "annuity"):
        method = REPAY_EQUAL_INSTALLMENT
    if method in ("到期还本", "interest-only", "bullet"):
        method = "interest_only"
    if method in ("气球", "balloon_payment"):
        method = REPAY_BALLOON

    # S2 T-debt: explicit principal schedule from vendor 还本 row (input, not output copy)
    if principal_schedule and any(_f(x) > 0 for x in principal_schedule):
        rows = []
        begin = loan if loan > 0 else 0.0
        sched = [round(_f(x), 2) for x in principal_schedule]
        isched = [round(_f(x), 2) for x in (interest_schedule or [])]
        for y in range(1, op_years + 1):
            interest = (
                isched[y - 1]
                if y - 1 < len(isched)
                else (round(begin * rate, 2) if begin > 0 else 0.0)
            )
            principal = sched[y - 1] if y - 1 < len(sched) else 0.0
            principal = round(min(max(principal, 0.0), begin), 2) if begin > 0 else 0.0
            end = round(max(begin - principal, 0.0), 2)
            rows.append({
                "year": y,
                "begin": round(begin, 2),
                "principal": principal,
                "interest": interest,
                "end": end,
                "method": "principal_schedule",
                "in_grace": False,
            })
            begin = end
        return rows

    rows: list[dict[str, Any]] = []
    begin = loan if loan > 0 else 0.0
    repay_years = max(years, 0)
    # Principal repayment window after grace
    principal_window = max(repay_years - grace_years, 0)

    # Precompute equal installment payment if needed
    installment = 0.0
    if method == REPAY_EQUAL_INSTALLMENT and principal_window > 0 and begin > 0:
        r = rate
        n = principal_window
        if r > 0:
            installment = begin * r * (1 + r) ** n / ((1 + r) ** n - 1)
        else:
            installment = begin / n
        installment = round(installment, 2)

    # Equal principal per year in principal window
    principal_per = 0.0
    if method == REPAY_EQUAL_PRINCIPAL and principal_window > 0 and begin > 0:
        principal_per = round(begin / principal_window, 2)

    # Balloon: amortize (1-balloon_pct) over principal_window-1, rest at end
    balloon_pct = min(max(_f(balloon_pct), 0.0), 0.95)
    amort_target = round(begin * (1.0 - balloon_pct), 2) if method == REPAY_BALLOON else 0.0
    balloon_principal_per = 0.0
    if method == REPAY_BALLOON and principal_window > 1 and amort_target > 0:
        balloon_principal_per = round(amort_target / (principal_window - 1), 2)

    for y in range(1, op_years + 1):
        in_grace = y <= grace_years
        in_repay_window = (not in_grace) and (y <= repay_years) and begin > 1e-9
        interest = round(begin * rate, 2) if begin > 0 and y <= max(repay_years, grace_years) else 0.0
        principal = 0.0

        if in_grace:
            principal = 0.0
        elif in_repay_window:
            if method == REPAY_EQUAL_PRINCIPAL:
                principal = round(min(principal_per, begin), 2)
            elif method == REPAY_EQUAL_INSTALLMENT:
                if rate > 0:
                    principal = round(min(max(installment - interest, 0.0), begin), 2)
                else:
                    principal = round(min(installment, begin), 2)
            elif method == "interest_only":
                # principal only on last repay year
                if y == repay_years:
                    principal = round(begin, 2)
                else:
                    principal = 0.0
            elif method == REPAY_BALLOON:
                if y < repay_years:
                    principal = round(min(balloon_principal_per, begin), 2)
                else:
                    principal = round(begin, 2)
            else:
                # fallback equal principal
                principal = round(min(principal_per or (begin / max(principal_window, 1)), begin), 2)
            # The contractual final repayment clears the exact outstanding
            # principal.  Reusing a rounded annuity/principal amount can leave
            # several cents of debt stranded after the repayment window.
            if y == repay_years:
                principal = round(begin, 2)
        else:
            interest = 0.0
            principal = 0.0

        end = round(max(begin - principal, 0.0), 2)
        # Last repay year: clear residual dust
        if in_repay_window and y == repay_years and end <= 0.05:
            principal = round(principal + end, 2)
            end = 0.0

        rows.append(
            {
                "year": y,
                "begin": round(begin, 2),
                "principal": round(principal, 2),
                "interest": round(interest, 2),
                "end": end,
                "method": method,
                "in_grace": in_grace,
            }
        )
        begin = end
    return rows


def attach_coverage(
    debt_rows: list[dict[str, Any]],
    *,
    ebit_by_year: Optional[list[float]] = None,
    cfads_by_year: Optional[list[float]] = None,
) -> list[dict[str, Any]]:
    """Attach ICR = EBIT/interest and DSCR = CFADS/(principal+interest)."""
    out = []
    for i, row in enumerate(debt_rows):
        r = dict(row)
        interest = _f(r.get("interest"))
        due = _f(r.get("principal")) + interest
        ebit = _f(ebit_by_year[i]) if ebit_by_year and i < len(ebit_by_year) else None
        cfads = _f(cfads_by_year[i]) if cfads_by_year and i < len(cfads_by_year) else None
        r["icr"] = round(ebit / interest, 2) if (ebit is not None and interest > 0) else None
        r["dscr"] = round(cfads / due, 2) if (cfads is not None and due > 0) else None
        out.append(r)
    return out


# Back-compat alias used by finance_model
def equal_principal_debt(loan: float, years: int, rate: float, op_years: int) -> list[dict]:
    return build_debt_schedule(loan, years, rate, op_years, method=REPAY_EQUAL_PRINCIPAL)
