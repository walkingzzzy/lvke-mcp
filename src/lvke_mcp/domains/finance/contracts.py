"""Finance schema contracts (P0-1): investment dictionary + critical input elements."""

from __future__ import annotations

from typing import Any, Optional

FINANCE_SCHEMA_VERSION = "2.0"

# Canonical investment definitions (方案 §5.1)
# 工程费用 = 建筑 + 设备 + 安装 + 其他工程费
# 建设投资 = 工程费用 + 工程建设其他费用 + 预备费
# 建设期融资费用 = 建设期利息 + 其他建设期融资费用
# 项目总投资 = 建设投资 + 建设期融资费用 + 流动资金

SCOPE_CLEAR = "clear"
SCOPE_AMBIGUOUS = "ambiguous"
SCOPE_DEGRADED = "degraded"
LEGACY_INVESTMENT_SCOPE_AMBIGUOUS = "LEGACY_INVESTMENT_SCOPE_AMBIGUOUS"

CRITICAL_ELEMENTS = (
    "investment.construction",
    "investment.other",
    "investment.reserve",
    "investment.interest",
    "investment.working_capital",
    "investment.total",
    "revenue.annual",
    "cost.cash_operating",
    "tax.income_rate",
    "tax.vat_rate",
    "debt.loan",
    "debt.rate",
    "debt.years",
    "debt.repay_method",
    "assets.depreciation_years",
    "assets.salvage_rate",
    "wc.turnover_days",
    "params.calc_years",
    "params.build_years",
)


def make_element(
    element_id: str,
    value: Any,
    *,
    unit: str = "万元",
    source_id: str = "",
    evidence_grade: str = "D",
    method: str = "",
    validation_status: str = "unvalidated",
    note: str = "",
    price_basis: str = "",
    as_of: str = "",
) -> dict[str, Any]:
    """Critical input element structure (方案 §5.2)."""
    return {
        "element_id": element_id,
        "value": value,
        "unit": unit,
        "price_basis": price_basis,
        "source_id": source_id,
        "evidence_grade": evidence_grade,
        "method": method,
        "as_of": as_of,
        "validation_status": validation_status,
        "note": note,
    }


def build_construction_investment(
    engineering: Optional[float],
    other: Optional[float],
    reserve: Optional[float],
) -> Optional[float]:
    """建设投资 = 工程费用 + 工程建设其他费用 + 预备费 (None if all missing)."""
    parts = [engineering, other, reserve]
    if all(p is None for p in parts):
        return None
    return round(sum(float(p or 0.0) for p in parts), 2)


def build_total_investment(
    construction: Optional[float],
    interest: Optional[float],
    working: Optional[float],
) -> Optional[float]:
    """项目总投资 = 建设投资 + 建设期融资费用 + 流动资金."""
    if construction is None and interest is None and working is None:
        return None
    return round(float(construction or 0.0) + float(interest or 0.0) + float(working or 0.0), 2)


def envelope(
    *,
    legacy_raw_inputs: Optional[dict[str, Any]] = None,
    normalized_inputs: Optional[dict[str, Any]] = None,
    elements: Optional[list[dict[str, Any]]] = None,
    scope_status: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "finance_schema_version": FINANCE_SCHEMA_VERSION,
        "legacy_raw_inputs": legacy_raw_inputs or {},
        "normalized_inputs": normalized_inputs or {},
        "elements": elements or [],
        "scope_status": scope_status or {},
    }
