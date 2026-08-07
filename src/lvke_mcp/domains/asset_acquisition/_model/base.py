"""独立情景字段、错误类型与数值/序列/IRR 原语。"""

from __future__ import annotations

import math
from typing import Any

from lvke_mcp.domains.finance.calculations import irr



INDEPENDENT_SCENARIO_FIELDS = {
    "transaction.purchase_price",
    "lease_portfolio.market_rent",
    "hotel_operation.adr",
    "hotel_operation.occupancy",
    "transaction.financing_ratio",
    "transaction.interest_rate",
    "transaction.tenor",
    "transaction.transaction_taxes",
    "hotel_operation.maintenance_capex",
    "transaction.exit_value",
    "solar_operation.tariff_yuan_per_kwh",
    "solar_operation.annual_generation_mwh",
    "solar_operation.utilization_hours",
    "solar_operation.annual_opex_wan",
    "solar_operation.maintenance_capex_wan",
    "solar_operation.curtailment_rate",
}


class AcquisitionModelError(ValueError):
    """Raised when a v3 acquisition spec cannot be deterministically solved."""


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _series(value: Any, years: int, *, default: float = 0.0) -> list[float]:
    if isinstance(value, (list, tuple)):
        values = [_number(item, default) for item in value]
        if not values:
            values = [default]
    else:
        values = [_number(value, default)]
    if len(values) < years:
        values.extend([values[-1]] * (years - len(values)))
    return values[:years]


def _safe_irr(cashflows: list[float]) -> float | None:
    try:
        return float(irr(cashflows))
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
