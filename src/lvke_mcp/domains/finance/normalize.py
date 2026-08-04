"""Input normalization and legacy field migration (P0-1 / 方案 §5.4)."""

from __future__ import annotations

from typing import Any, Optional

from lvke_mcp.domains.finance.contracts import (
    LEGACY_INVESTMENT_SCOPE_AMBIGUOUS,
    SCOPE_AMBIGUOUS,
    SCOPE_CLEAR,
    SCOPE_DEGRADED,
    build_total_investment,
    envelope,
    make_element,
)

_SCOPE_TOL = 1.0


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classify_investment_scope(
    total: Optional[float],
    construction: Optional[float],
    other: Optional[float],
    reserve: Optional[float],
    interest: Optional[float],
    working: Optional[float],
) -> dict[str, Any]:
    """判定项目总投资口径是否自洽（方案 §5.1/§5.4）。

    construction 视为已含 other/reserve 的建设投资，判定时不再把 other/reserve 外加。
    """
    t = _f(total)
    c = _f(construction)
    i = _f(interest)
    w = _f(working)

    if t is None or t <= 0:
        return {"status": SCOPE_DEGRADED, "code": "", "reason": "缺项目总投资，无法判定口径"}

    if c is None and i is None and w is None:
        return {
            "status": SCOPE_DEGRADED,
            "code": "",
            "reason": "仅提供项目总投资、无投资分项，按匡算预览处理（非口径歧义）",
        }

    if c is not None and i is not None and w is not None:
        composed = round(c + i + w, 2)
        if abs(composed - t) < _SCOPE_TOL:
            return {
                "status": SCOPE_CLEAR,
                "code": "",
                "reason": "总投资 = 建设投资 + 建设期利息 + 流动资金，口径自洽",
            }
        if abs(round(c + w, 2) - t) < _SCOPE_TOL and i > 0:
            return {
                "status": SCOPE_AMBIGUOUS,
                "code": LEGACY_INVESTMENT_SCOPE_AMBIGUOUS,
                "reason": (
                    "总投资=建设投资+流动资金已成立，却另给了建设期利息 "
                    f"{i:.2f} 万元——无法判定利息是否已含在建设投资内（方案 §5.4）。"
                    "仅可匡算预览，终稿须人工确认口径后发布。"
                ),
                "composed": composed,
                "total": t,
            }
        return {
            "status": SCOPE_AMBIGUOUS,
            "code": LEGACY_INVESTMENT_SCOPE_AMBIGUOUS,
            "reason": (
                f"投资分项合计 {composed:.2f} 万元与项目总投资 {t:.2f} 万元不一致"
                f"（差 {composed - t:.2f} 万元），口径存在重复计入或漏计可能。"
            ),
            "composed": composed,
            "total": t,
        }

    known = round((c or 0.0) + (i or 0.0) + (w or 0.0), 2)
    if known - t > _SCOPE_TOL:
        return {
            "status": SCOPE_AMBIGUOUS,
            "code": LEGACY_INVESTMENT_SCOPE_AMBIGUOUS,
            "reason": (
                f"已知投资分项合计 {known:.2f} 万元已超过项目总投资 {t:.2f} 万元，"
                "疑似其他费/预备费在建设投资之外被重复计入（方案 §5.1）。"
            ),
            "composed": known,
            "total": t,
        }
    return {
        "status": SCOPE_CLEAR,
        "code": "",
        "reason": "投资分项未超总投资，缺项按倒算推定，口径可接受",
    }


def normalize_finance_inputs(finance: dict[str, Any]) -> dict[str, Any]:
    """Preserve legacy raw snapshot and attach schema envelope.

    Does not rewrite arithmetic values used by the engine; only classifies and
    records normalized view for audit / release gates.
    """
    fin = dict(finance or {})
    bd = dict(fin.get("invest_breakdown") or {})
    total = _f(fin.get("total_investment_wan"))
    construction = _f(bd.get("construction_wan"))
    other = _f(bd.get("other_wan"))
    reserve = _f(bd.get("reserve_wan"))
    interest = _f(bd.get("interest_wan"))
    working = _f(bd.get("working_capital_wan"))

    # other/reserve are components of construction, not additive outside it.
    construction_note = ""
    if construction is not None and (other or reserve):
        # If construction looks like pure engineering (smaller than other+reserve add),
        # flag but do not auto-add (avoid double count); detail path handles rebuild.
        construction_note = "other_wan/reserve_wan treated as components of construction_wan"

    scope = classify_investment_scope(total, construction, other, reserve, interest, working)

    composed_total = build_total_investment(construction, interest, working)
    normalized = {
        "total_investment_wan": total,
        "construction_wan": construction,
        "other_wan": other,
        "reserve_wan": reserve,
        "interest_wan": interest,
        "working_capital_wan": working,
        "composed_total_wan": composed_total,
        "other_reserve_policy": construction_note or "not_applicable",
        "loan_repay_method": fin.get("loan_repay_method") or "equal_principal",
        "loan_grace_years": int(_f(fin.get("loan_grace_years")) or 0),
        "wc_turnover_days": _f(fin.get("wc_turnover_days")),
        "depreciation_years": _f(fin.get("depreciation_years")),
        "amortization_years": _f(fin.get("amortization_years")),
    }

    elements = []
    mapping = [
        ("investment.total", total, "万元"),
        ("investment.construction", construction, "万元"),
        ("investment.other", other, "万元"),
        ("investment.reserve", reserve, "万元"),
        ("investment.interest", interest, "万元"),
        ("investment.working_capital", working, "万元"),
        ("revenue.annual", _f(fin.get("annual_revenue_wan")), "万元"),
        ("debt.loan", _f(fin.get("loan_wan")), "万元"),
        ("debt.rate", _f(fin.get("loan_rate")), "小数"),
        ("debt.years", _f(fin.get("loan_years")), "年"),
        ("assets.depreciation_years", _f(fin.get("depreciation_years")), "年"),
        ("wc.turnover_days", _f(fin.get("wc_turnover_days")), "天"),
    ]
    for eid, val, unit in mapping:
        if val is not None:
            elements.append(
                make_element(
                    eid,
                    val,
                    unit=unit,
                    method="user_or_requirement",
                    evidence_grade="C",
                    review_status="draft",
                )
            )

    return envelope(
        legacy_raw_inputs={"finance": fin, "invest_breakdown": bd},
        normalized_inputs=normalized,
        elements=elements,
        scope_status=scope,
    )
