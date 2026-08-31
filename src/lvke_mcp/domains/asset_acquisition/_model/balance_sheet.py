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
    maintenance_capex: list[float] | None = None,
    disposal_period_index: int | None = None,
    year_meta: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Accumulate cash, book equity and net fixed assets year by year.

    Keeps three identities: ``cash + FA = total assets``,
    ``debt + equity = total liabilities and equity``, and the accounting
    identity ``total assets = total liabilities and equity``.

    维护性资本支出必须资本化进固定资产：它减少现金，若不同时增加 FA 原值，
    资产侧就凭空少一块，恒等式永远不成立（实测差额逐年放大）。此前该残差被
    当作"可见但不补平"的已知现象，实际是把一个真实的记账缺口当成了设计。
    """

    cash = 0.0
    retained = 0.0
    cumulative_depreciation = 0.0
    cumulative_capex = 0.0
    capex = maintenance_capex or []
    disposed = False
    rows: list[dict[str, Any]] = []
    for index in range(max(int(years), 0)):
        cash = round(cash + float(equity_cf[index] if index < len(equity_cf) else 0.0), 2)
        retained = round(retained + float(net_profit[index] if index < len(net_profit) else 0.0), 2)
        cumulative_depreciation = round(
            cumulative_depreciation + float(depreciation[index] if index < len(depreciation) else 0.0),
            2,
        )
        period_capex = float(capex[index] if index < len(capex) else 0.0)
        if not disposed:
            cumulative_capex = round(cumulative_capex + period_capex, 2)
        book_value = round(
            max(float(total_cost) + cumulative_capex - cumulative_depreciation, 0.0), 2
        )
        # 处置当期资产出账：处置价款进现金，账面净值必须同时移出资产侧，
        # 否则资产侧凭空多留一份已卖掉的资产——实测退出年起 total_assets 恒比
        # L+E 多出一个 exit_value 且逐期不收敛。处置损益由各引擎在 net_profit
        # 中体现，这里只负责资产出账。
        if disposal_period_index is not None and index >= int(disposal_period_index):
            disposed = True
        fixed_asset_net = 0.0 if disposed else book_value
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
    # 下面两条是同义反复：total_assets 与 total_le 正是在 build 阶段由这两个
    # 表达式定义的（见 :40-41），所以它们恒真、检不出任何问题。保留是因为它们
    # 能挡住"字段被下游改写成不自洽的值"，但真正的会计恒等式必须单独验。
    if abs(cash + fixed_asset - total_assets) > 0.05:
        return False
    if abs(debt + equity - total_le) > 0.05:
        return False
    # 资产 = 负债 + 权益。此前从未检验：实测酒店 Y1 差 559.60 万元、Y4 差
    # 1891.30 且逐年放大（差额 = 累计利息 + 累计维护性资本支出），光伏 Y1 差
    # 979.46，而 consistency_ok 一路为 true、failed_checks 为空。容差按逐期
    # 四舍五入的累积量放宽到 0.5，仍远小于任何真实失衡。
    if abs(total_assets - total_le) > 0.5:
        return False
    return True
