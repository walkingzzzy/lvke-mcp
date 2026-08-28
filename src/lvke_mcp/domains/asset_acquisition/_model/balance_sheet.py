"""Roll year-end balance-sheet projections from acquisition cash and debt."""

from __future__ import annotations

from typing import Any


def roll_annual_balance_sheet(
    *,
    years: int,
    total_cost: float,
    opening_equity: float,
    equity_cf: list[float],
    net_profit: list[float],
    depreciation: list[float],
    closing_debt: list[float],
    year_meta: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Accumulate cash, book equity and net fixed assets year by year.

    Identities kept here are ``cash + FA = total assets`` and
    ``debt + equity = total liabilities and equity``. A residual between the
    two totals is left visible; it is not plugged.
    """

    cash = 0.0
    retained = 0.0
    cumulative_depreciation = 0.0
    rows: list[dict[str, Any]] = []
    for index in range(max(int(years), 0)):
        cash = round(cash + float(equity_cf[index] if index < len(equity_cf) else 0.0), 2)
        retained = round(retained + float(net_profit[index] if index < len(net_profit) else 0.0), 2)
        cumulative_depreciation = round(
            cumulative_depreciation + float(depreciation[index] if index < len(depreciation) else 0.0),
            2,
        )
        fixed_asset_net = round(max(float(total_cost) - cumulative_depreciation, 0.0), 2)
        debt_wan = round(float(closing_debt[index] if index < len(closing_debt) else 0.0), 2)
        equity_wan = round(float(opening_equity) + retained, 2)
        total_assets = round(cash + fixed_asset_net, 2)
        total_le = round(debt_wan + equity_wan, 2)
        meta = dict(year_meta[index]) if year_meta and index < len(year_meta) else {}
        meta.setdefault("year", index + 1)
        meta.setdefault("year_index", index + 1)
        meta.update({
            "cash_wan": cash,
            "fixed_asset_net_wan": fixed_asset_net,
            "total_assets_wan": total_assets,
            "debt_wan": debt_wan,
            "equity_wan": equity_wan,
            "total_liabilities_equity_wan": total_le,
        })
        rows.append(meta)
    return rows


def projection_consistency_ok(result: dict[str, Any]) -> bool:
    """Numerical projection completeness, independent of spec/evidence issues."""

    annual = result.get("annual_summary") if isinstance(result.get("annual_summary"), list) else []
    if not annual:
        return False
    last = annual[-1] if isinstance(annual[-1], dict) else {}
    required = (
        "cash_wan", "fixed_asset_net_wan", "total_assets_wan",
        "debt_wan", "equity_wan", "total_liabilities_equity_wan",
    )
    if any(last.get(key) is None or last.get(key) == "" for key in required):
        return False
    cash = float(last.get("cash_wan") or 0.0)
    fixed_asset = float(last.get("fixed_asset_net_wan") or 0.0)
    total_assets = float(last.get("total_assets_wan") or 0.0)
    debt = float(last.get("debt_wan") or 0.0)
    equity = float(last.get("equity_wan") or 0.0)
    total_le = float(last.get("total_liabilities_equity_wan") or 0.0)
    if abs(cash + fixed_asset - total_assets) > 0.05:
        return False
    if abs(debt + equity - total_le) > 0.05:
        return False
    return True
