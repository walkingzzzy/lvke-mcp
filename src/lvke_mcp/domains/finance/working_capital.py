"""Working capital by turnover days (P1-1)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional


_MONEY_QUANTUM = Decimal("0.01")


def money(value: Any) -> float:
    """Quantize amounts expressed in 万元 to the public two-decimal precision."""

    try:
        decimal_value = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        decimal_value = Decimal("0")
    return float(decimal_value.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def estimate_from_turnover(
    *,
    revenue: float,
    cash_cost: float,
    turnover: dict[str, Any] | None = None,
    default_days: Optional[float] = None,
) -> dict[str, Any]:
    """Balance = base × days / 360; WC = current assets − current liabilities.

    ``turnover`` keys (days): receivable, inventory, cash, payable.
    If only a single ``default_days`` / overall days is given, spread across
    standard structure with that horizon as the cash cycle driver.
    """
    revenue = float(revenue or 0.0)
    cash_cost = float(cash_cost or 0.0)
    t = dict(turnover or {})
    overall = _f(t.get("overall"))
    if overall is None:
        overall = _f(default_days)

    # Defaults when partial: keep conventional structure but driven by days.
    def _turnover_component(name: str, default_base: float) -> tuple[Optional[float], float]:
        value = t.get(name)
        if isinstance(value, dict):
            days = _f(value.get("days"))
            base = _f(value.get("annual_base_wan"))
            return days, default_base if base is None else base
        days = _f(t.get(f"{name}_days"))
        if days is None:
            days = _f(value)
        return days, default_base

    recv_d, recv_base = _turnover_component("receivable", revenue)
    inv_d, inv_base = _turnover_component("inventory", cash_cost)
    cash_d, cash_base = _turnover_component("cash", cash_cost)
    pay_d, payable_base = _turnover_component("payable", cash_cost)

    if overall is not None and all(x is None for x in (recv_d, inv_d, cash_d, pay_d)):
        # Map overall cycle into conventional shares of asset side days.
        recv_d = round(overall * 0.35, 2)
        inv_d = round(overall * 0.45, 2)
        cash_d = round(overall * 0.20, 2)
        pay_d = round(overall * 0.30, 2)

    recv_d = recv_d if recv_d is not None else 0.0
    inv_d = inv_d if inv_d is not None else 0.0
    cash_d = cash_d if cash_d is not None else 0.0
    pay_d = pay_d if pay_d is not None else 0.0

    # Formal v1 inventory is itemised.  Every component must carry its own
    # base_wan and days; never infer a base from a total or from a name.
    inv_detail = t.get("inventory_detail") if isinstance(t.get("inventory_detail"), dict) else {}
    component_rows: dict[str, dict[str, Any]] = {}
    aliases = {
        "raw": "raw", "raw_material": "raw", "materials": "raw", "原材料": "raw",
        "fuel": "fuel", "energy": "fuel", "燃料": "fuel", "动力": "fuel", "燃料及动力": "fuel",
        "wip": "wip", "work_in_progress": "wip", "在产品": "wip",
        "finished": "finished", "finished_goods": "finished", "fg": "finished", "产成品": "finished",
    }
    for alias, canonical in aliases.items():
        raw_component = inv_detail.get(alias)
        if isinstance(raw_component, dict):
            base = _f(
                raw_component.get("annual_base_wan")
                if raw_component.get("annual_base_wan") is not None
                else raw_component.get("base_wan")
            )
            days_v = _f(raw_component.get("days"))
            component_rows[canonical] = {
                "base_wan": base,
                "days": days_v,
                "base_source": raw_component.get("base_source"),
                "complete": base is not None and base >= 0 and days_v is not None and days_v >= 0,
            }
        elif raw_component not in (None, ""):
            # Legacy numeric day-only input remains estimate/reference data.
            days_v = _f(raw_component)
            component_rows[canonical] = {
                "base_wan": None,
                "days": days_v,
                "base_source": None,
                "complete": False,
            }

    recv = money(Decimal(str(recv_base)) * Decimal(str(recv_d)) / Decimal("360"))
    # Aggregate inventory days remain the balance total when provided. Component
    # days are only structural detail for 附表3; table_render recomputes their
    # amounts with item cost bases and must not re-inflate the inventory total.
    complete_components = all(
        key in component_rows and bool(component_rows[key].get("complete"))
        for key in ("raw", "fuel", "wip", "finished")
    )
    component_amounts: dict[str, float] = {}
    if complete_components:
        component_amounts = {
            key: money(Decimal(str(row["base_wan"])) * Decimal(str(row["days"])) / Decimal("360"))
            for key, row in component_rows.items()
        }
        invy = money(sum(component_amounts.values()))
        # The aggregate inventory day is descriptive only, never a second input.
        inv_d = round(sum(float(component_rows[key]["days"]) for key in component_amounts), 2)
    else:
        invy = money(Decimal(str(inv_base)) * Decimal(str(inv_d)) / Decimal("360"))
    cash = money(Decimal(str(cash_base)) * Decimal(str(cash_d)) / Decimal("360"))
    payable = money(Decimal(str(payable_base)) * Decimal(str(pay_d)) / Decimal("360"))
    cur_assets = money(Decimal(str(recv)) + Decimal(str(invy)) + Decimal(str(cash)))
    cur_liab = money(payable)
    net = money(Decimal(str(cur_assets)) - Decimal(str(cur_liab)))
    out = {
        "method": "turnover_days",
        "total": net,
        "current_assets": cur_assets,
        "receivable": recv,
        "inventory": invy,
        "cash": cash,
        "current_liabilities": cur_liab,
        "payable": payable,
        "net_working_capital": net,
        "days": {
            "receivable": recv_d,
            "inventory": inv_d,
            "cash": cash_d,
            "payable": pay_d,
            "overall": overall,
        },
        "bases": {
            "receivable": recv_base,
            "inventory": inv_base,
            "cash": cash_base,
            "payable": payable_base,
            "revenue": revenue,
            "cash_cost": cash_cost,
        },
    }
    if component_rows:
        out["inventory_detail"] = component_rows
        out["inventory_components_complete"] = complete_components
        out["inventory_component_amounts"] = component_amounts
        out["inventory_from_components"] = complete_components
        if not complete_components:
            out["inventory_detail_note"] = "缺少四类存货逐项 base_wan/days，存货分项仅为估算，不得 formal"
    return out


def estimate_from_total_ratio(wc_total: float) -> dict[str, Any]:
    """Legacy ratio back-solve (汇总级，不得标为分项法完成)."""
    wc_total = round(float(wc_total or 0.0), 2)
    cur_assets = round(wc_total / 0.70, 2) if wc_total > 0 else 0.0
    recv = round(cur_assets * 0.35, 2)
    invy = round(cur_assets * 0.45, 2)
    cash = round(cur_assets - recv - invy, 2)
    payable = round(cur_assets - wc_total, 2)
    return {
        "method": "ratio_backsolve",
        "total": wc_total,
        "current_assets": cur_assets,
        "receivable": recv,
        "inventory": invy,
        "cash": cash,
        "current_liabilities": payable,
        "payable": payable,
        "net_working_capital": round(cur_assets - payable, 2),
        "note": "汇总级反解分项，非周转天数业务明细",
    }


def build_working_capital(
    *,
    wc_total: Optional[float],
    revenue: float = 0.0,
    cash_cost: float = 0.0,
    wc_turnover_days: Optional[float] = None,
    turnover: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Prefer turnover-days method when days/params present; else ratio backsolve."""
    has_days = wc_turnover_days is not None or bool(turnover)
    if has_days and (revenue > 0 or cash_cost > 0):
        row = estimate_from_turnover(
            revenue=revenue,
            cash_cost=cash_cost,
            turnover=turnover,
            default_days=wc_turnover_days,
        )
        # If user also gave a total, keep the honest difference. Never force-scale
        # turnover components to invent 基数×天数÷360 consistency.
        if wc_total is not None:
            stated = money(wc_total)
            row["stated_total"] = stated
            computed = float(row.get("total") or 0.0)
            row["delta_vs_stated"] = money(Decimal(str(computed)) - Decimal(str(stated)))
            if abs(Decimal(str(computed)) - Decimal(str(stated))) > _MONEY_QUANTUM:
                row["scaled_to_stated_total"] = False
                row["reconcile_conflict"] = True
                row["note"] = (
                    f"周转法结果 {computed} 与申报总额 {stated} 不一致；"
                    "保留周转分项，不强制缩放抹平差额"
                )
        return row
    return estimate_from_total_ratio(float(wc_total or 0.0))
