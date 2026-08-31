"""酒店月度收购模型引擎。"""

from __future__ import annotations

import calendar  # compatibility export; calendar arithmetic lives in monthly_drivers
from typing import Any

from lvke_mcp.domains.finance.calculations import payback_period

from lvke_mcp.domains.finance.spec import LATEST_SPEC_VERSION

from .base import (
    AcquisitionModelError,
    _number,
    _safe_irr,
    _series,
)

from .period_dates import (
    _add_months,
    _date_value,
    _month_end,
    _month_overlap_days,
    _month_start,
)

from .monthly_drivers import (
    resolve_monthly_driver,
    resolve_operating_calendar,
)

from .schedules import (
    _depreciation_schedule,
    _monthly_debt_schedule,
    _monthly_lease_income,
)


def _run_monthly_acquisition_model(
    spec: dict[str, Any], *, discount_rate: float, scenario_id: str,
) -> dict[str, Any]:
    """Monthly mixed hotel/lease acquisition model with annual projections.

    This is intentionally an additive v3 path: old annual specifications keep
    using the v2 algorithm below.  The monthly path has no implied lease
    renewal and never converts hotel affordability into owner revenue.
    """

    transaction = dict(spec.get("transaction") or {})
    hotel = dict(spec.get("hotel_operation") or {})
    portfolio = dict(spec.get("lease_portfolio") or {})
    start = _date_value(transaction.get("model_start_date") or transaction.get("closing_date"))
    opening = _date_value(transaction.get("opening_date") or transaction.get("hotel_opening_date"))
    if start is None:
        raise AcquisitionModelError("monthly acquisition requires transaction.model_start_date or closing_date")
    operating_mode = str(transaction.get("operating_mode") or spec.get("operating_mode") or "")
    if operating_mode not in {"owner_lessor", "mixed_owner_operator"}:
        raise AcquisitionModelError("monthly acquisition requires operating_mode owner_lessor or mixed_owner_operator")
    if operating_mode == "mixed_owner_operator" and opening is None:
        raise AcquisitionModelError("mixed_owner_operator requires transaction.opening_date")
    years = max(
        int(_number(transaction.get("exit_year"), 0)),
        int(_number(transaction.get("tenor"), 0)),
        int(_number(portfolio.get("projection_years"), 0)),
        1,
    )
    months = years * 12
    purchase_price = _number(transaction.get("purchase_price"))
    taxes = transaction.get("transaction_taxes") or {}
    transaction_tax = sum(_number(value) for value in taxes.values()) if isinstance(taxes, dict) else _number(taxes)
    closing_cost = _number(transaction.get("closing_costs"))
    fitout_capex = _number(transaction.get("fitout_capex_wan"))
    total_cost = purchase_price + transaction_tax + closing_cost + fitout_capex
    financing_ratio = min(max(_number(transaction.get("financing_ratio")), 0.0), 1.0)
    debt = total_cost * financing_ratio
    equity = total_cost - debt
    repayment = str(transaction.get("repayment") or "equal_principal")
    if repayment not in {"equal_principal", "equal_payment", "annuity", "bullet", "interest_only"}:
        raise AcquisitionModelError(f"unsupported repayment method: {repayment}")
    debt_rows = _monthly_debt_schedule(
        debt, _number(transaction.get("interest_rate")), int(_number(transaction.get("tenor"))), months, repayment,
    )
    depreciation = _depreciation_schedule(
        {**transaction, "depreciation_schedule": spec.get("depreciation_schedule")},
        years,
    )
    annual_depreciation = depreciation.get("annual_depreciation_wan") or [0.0] * years
    cost = spec.get("cost") or {}
    cost_items = cost.get("cost_items") if isinstance(cost, dict) else {}
    owner_opex_raw = (cost or {}).get("annual_owner_operating_cost_wan")
    if owner_opex_raw is None:
        owner_opex_raw = sum(_number(value) for value in (cost_items or {}).values())
    tax_cfg = spec.get("tax") or {}
    income_tax_rate = min(max(_number(tax_cfg.get("income_tax_rate")), 0.0), 1.0)
    vat_rate = min(max(_number(tax_cfg.get("vat_rate")), 0.0), 1.0)
    surtax_rate = min(max(_number(tax_cfg.get("surtax_rate")), 0.0), 1.0)
    exit_year = int(_number(transaction.get("exit_year"), years))
    exit_month = min(max(exit_year * 12 - 1, 0), months - 1)
    exit_value = _number(transaction.get("exit_value"))
    exit_cost = _number(transaction.get("exit_cost_wan")) + exit_value * min(max(_number(transaction.get("exit_cost_rate")), 0.0), 1.0)
    exit_tax = _number(transaction.get("exit_tax_wan")) + exit_value * min(max(_number(transaction.get("exit_tax_rate")), 0.0), 1.0)
    net_exit = max(exit_value - exit_cost - exit_tax, 0.0)
    monthly_rows: list[dict[str, Any]] = []
    project_monthly = [-total_cost]
    equity_monthly = [-equity]
    annual: list[dict[str, Any]] = []
    for year_index in range(years):
        year_start = _add_months(_month_start(start), year_index * 12)
        year_end = _month_end(_add_months(year_start, 11))
        annual.append({
            "year": year_end.year,
            "year_index": year_index + 1,
            "period_start": year_start.isoformat(),
            "period_end": year_end.isoformat(),
            "period_label": (
                str(year_end.year)
                if year_start.month == 1 and year_end.month == 12
                else f"{year_start:%Y-%m}/{year_end:%Y-%m}"
            ),
            "period_basis": "calendar_year" if year_start.month == 1 else "rolling_model_year",
            "hotel_revenue_wan": 0.0,
            "lease_revenue_wan": 0.0,
            "revenue_wan": 0.0,
            "operating_cost_wan": 0.0,
            "tax_wan": 0.0,
            "income_tax_wan": 0.0,
            "maintenance_capex_wan": 0.0,
            "project_cf_wan": 0.0,
            "equity_cf_wan": 0.0,
            "debt_service_wan": 0.0,
            "interest_wan": 0.0,
            "vat_wan": 0.0,
            "surtax_wan": 0.0,
            "loss_carryforward_wan": 0.0,
            "depreciation_wan": 0.0,
            "net_profit_wan": 0.0,
        })
    operating_calendar, calendar_manifest = resolve_operating_calendar(
        start,
        months,
        hotel.get("operating_calendar"),
        legacy_operating_days=hotel.get("operating_days"),
    )
    driver_specs = {
        "adr": (hotel.get("adr"), "level", 0.0, None),
        "occupancy": (hotel.get("occupancy"), "level", 0.0, 1.0),
        "payroll": (hotel.get("payroll"), "annual_total", 0.0, None),
        "utilities": (hotel.get("utilities"), "annual_total", 0.0, None),
        "consumables": (hotel.get("consumables"), "annual_total", 0.0, None),
        "maintenance": (
            hotel.get("maintenance")
            if hotel.get("maintenance") is not None
            else hotel.get("maintenance_capex"),
            "annual_total",
            0.0,
            None,
        ),
        "owner_opex": (owner_opex_raw, "annual_total", 0.0, None),
        "ota_commission": (hotel.get("ota_commission"), "level", 0.0, None),
    }
    if hotel.get("ancillary_revenue") is not None:
        driver_specs["ancillary_revenue"] = (
            hotel.get("ancillary_revenue"), "annual_total", 0.0, None,
        )
    else:
        driver_specs.update({
            "food_beverage_revenue": (hotel.get("food_beverage_revenue"), "annual_total", 0.0, None),
            "meeting_revenue": (hotel.get("meeting_revenue"), "annual_total", 0.0, None),
            "other_revenue": (hotel.get("other_revenue"), "annual_total", 0.0, None),
        })
    drivers: dict[str, list[float]] = {}
    driver_manifest: dict[str, dict[str, Any]] = {}
    for name, (raw, kind, default, maximum) in driver_specs.items():
        values, manifest = resolve_monthly_driver(
            f"hotel_operation.{name}" if name != "owner_opex" else "cost.annual_owner_operating_cost_wan",
            raw,
            months=months,
            periods=operating_calendar,
            kind=kind,
            default=default,
            maximum=maximum,
        )
        drivers[name] = values
        driver_manifest[name] = manifest
    rooms = _number(hotel.get("rooms"))
    cursor = _month_start(start)
    loss_carryforward = 0.0
    cash = 0.0
    retained_earnings = 0.0
    cumulative_depreciation = 0.0
    # 维护性资本支出资本化累计额：它从现金流出，必须同时进固定资产原值，
    # 否则资产 = 负债 + 权益 永不成立（见 balance_sheet.py 同一修复）。
    cumulative_capex = 0.0
    asset_disposed = False
    ota_semantics_seen: set[str] = set()
    for index in range(months):
        period_start = cursor
        period_end = _month_end(cursor)
        model_year = index // 12
        days = (period_end - period_start).days + 1
        owner_days = _month_overlap_days(period_start, period_end, start)
        hotel_days = _month_overlap_days(period_start, period_end, opening) if opening else 0
        calendar_row = operating_calendar[index]
        selected_days = float(calendar_row["selected_days"])
        owner_activity = owner_days / days if days else 0.0
        hotel_activity = hotel_days / days if days else 0.0
        hotel_operating_days = selected_days * hotel_activity
        lease_revenue, lease_adjustment = _monthly_lease_income(portfolio, period_start, period_end)
        hotel_revenue = 0.0
        hotel_cost = 0.0
        maintenance_capex = 0.0
        room_revenue = 0.0
        ancillary = 0.0
        ota_cost = 0.0
        payroll_cost = 0.0
        utilities_cost = 0.0
        consumables_cost = 0.0
        if operating_mode == "mixed_owner_operator" and hotel_days:
            room_revenue = rooms * drivers["adr"][index] * drivers["occupancy"][index] * hotel_operating_days / 10000.0
            ancillary = (
                drivers["ancillary_revenue"][index]
                if "ancillary_revenue" in drivers
                else drivers["food_beverage_revenue"][index]
                + drivers["meeting_revenue"][index]
                + drivers["other_revenue"][index]
            ) * hotel_activity
            hotel_revenue = room_revenue + ancillary
            ota_value = drivers["ota_commission"][index]
            # ota_commission 有两种语义且按取值区间判别：[0,1] 视为抽佣比率，
            # >1 视为金额（万元）。这是既有契约行为，改判会静默重算历史 spec，
            # 因此保留判别规则，但必须把判别结果显式披露——否则同一字段填
            # 0.08 与 135 会得到 IRR +5.52% 与 −33.66% 两种结论而无任何提示，
            # 且 0.5 万元的真实小额佣金会被当成 50% 抽佣。
            ota_is_rate = 0 <= ota_value <= 1
            ota_cost = room_revenue * ota_value if ota_is_rate else ota_value * hotel_activity
            if ota_value:
                ota_semantics_seen.add("rate" if ota_is_rate else "amount_wan")
            payroll_cost = drivers["payroll"][index] * hotel_activity
            utilities_cost = drivers["utilities"][index] * hotel_activity
            consumables_cost = drivers["consumables"][index] * hotel_activity
            hotel_cost = ota_cost + payroll_cost + utilities_cost + consumables_cost
            maintenance_capex = drivers["maintenance"][index] * hotel_activity
        owner_cost = drivers["owner_opex"][index] * owner_activity
        revenue = lease_revenue + (hotel_revenue if operating_mode == "mixed_owner_operator" else 0.0)
        operating_cost = owner_cost + (hotel_cost if operating_mode == "mixed_owner_operator" else 0.0)
        depreciation_month = _number(annual_depreciation[model_year] if model_year < len(annual_depreciation) else 0.0) / 12.0
        taxable_raw = revenue - operating_cost - depreciation_month
        if taxable_raw < 0:
            loss_carryforward += -taxable_raw
            taxable = 0.0
        else:
            used = min(loss_carryforward, taxable_raw)
            loss_carryforward -= used
            taxable = taxable_raw - used
        tax = taxable * income_tax_rate
        vat = revenue * vat_rate
        surtax = vat * surtax_rate
        exit_cash = net_exit if index == exit_month else 0.0
        debt_row = debt_rows[index]
        interest_month = _number(debt_row.get("interest_wan") or 0.0)
        # 利润表净利润必须扣利息与税金及附加。此前是 `taxable_raw - tax`，即
        # 只扣了成本与折旧：实测利息列 404.60 明明在同一行输出，净利润却没扣，
        # 差额恒等于利息本身；光伏侧同一缺陷使净利由 +48.375 翻成 −731.077。
        # 所得税沿用项目（税前融资）口径不变——现金流与 IRR 不受本次修改影响，
        # 改的只是利润表这一行，故与附表 tax 列的口径差异在此显式说明。
        # lease_adjustment 是负值一次性招租支出（leasing_cost + fitout_allowance，
        # 见 schedules.py:160-161），属真实费用而非押金，必须进利润表；只从现金
        # 扣、不进损益，同样会撑开资产=负债+权益的缺口。
        net_profit = taxable_raw - interest_month - surtax - tax + lease_adjustment
        cfads = revenue - operating_cost - tax - surtax - maintenance_capex
        project_cf = cfads + lease_adjustment + exit_cash
        equity_cf = project_cf - debt_row["debt_service_wan"]
        project_monthly.append(project_cf)
        equity_monthly.append(equity_cf)
        cash = round(cash + equity_cf, 2)
        cumulative_depreciation = round(cumulative_depreciation + depreciation_month, 2)
        # 资产已处置后不再资本化维护支出——资产不在账上，再资本化就又撑开缺口
        # （实测每期偏 95，恰为月度维护性资本支出）。此时它是纯费用，已通过
        # cfads 从现金扣除，也须进损益。
        if asset_disposed:
            net_profit -= maintenance_capex
        else:
            cumulative_capex = round(cumulative_capex + maintenance_capex, 2)
        book_value = round(max(total_cost + cumulative_capex - cumulative_depreciation, 0.0), 2)
        if exit_cash:
            # 处置当期资产必须出账，处置损益进损益表。此前 exit_cash 只进现金、
            # 资产原值不核销，资产侧凭空多出一份已卖掉的资产：实测 Y5 起
            # total_assets 比 L+E 多 17,000.11（恰为 exit_value），且逐期不收敛。
            disposed_book_value = book_value
            net_profit += exit_cash - disposed_book_value
            asset_disposed = True
        retained_earnings = round(retained_earnings + net_profit, 2)
        fixed_asset_net = 0.0 if asset_disposed else book_value
        debt_wan = round(float(debt_row.get("closing_principal_wan") or 0.0), 2)
        equity_wan = round(equity + retained_earnings, 2)
        total_assets = round(cash + fixed_asset_net, 2)
        total_le = round(debt_wan + equity_wan, 2)
        bucket = annual[model_year]
        for key, value in {
            "hotel_revenue_wan": hotel_revenue, "lease_revenue_wan": lease_revenue,
            "revenue_wan": revenue,
            "operating_cost_wan": operating_cost, "tax_wan": tax,
            "income_tax_wan": tax,
            "maintenance_capex_wan": maintenance_capex, "project_cf_wan": project_cf,
            "equity_cf_wan": equity_cf, "debt_service_wan": debt_row["debt_service_wan"],
            "interest_wan": debt_row["interest_wan"],
            "vat_wan": vat,
            "surtax_wan": surtax,
            "depreciation_wan": depreciation_month,
            "net_profit_wan": net_profit,
        }.items():
            bucket[key] += value
        bucket["loss_carryforward_wan"] = loss_carryforward
        bucket["year"] = model_year + 1
        bucket["cash_wan"] = cash
        bucket["fixed_asset_net_wan"] = fixed_asset_net
        bucket["total_assets_wan"] = total_assets
        bucket["debt_wan"] = debt_wan
        bucket["equity_wan"] = equity_wan
        bucket["total_liabilities_equity_wan"] = total_le
        monthly_rows.append({
            "month": index + 1, "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
            "active_days": owner_days, "hotel_days": hotel_days,
            "calendar_days": calendar_row["calendar_days"],
            "operating_days": round(hotel_operating_days, 8),
            "workdays": calendar_row["workdays"],
            "calendar_basis": calendar_row["basis"],
            "operating_mode": operating_mode, "hotel_revenue_wan": hotel_revenue,
            "adr": drivers["adr"][index], "occupancy": drivers["occupancy"][index],
            "room_revenue_wan": room_revenue, "ancillary_revenue_wan": ancillary,
            "hotel_cost_wan": hotel_cost, "lease_revenue_wan": lease_revenue,
            "lease_adjustment_wan": lease_adjustment,
            "revenue_wan": revenue,
            "payroll_wan": payroll_cost, "utilities_wan": utilities_cost,
            "consumables_wan": consumables_cost, "ota_commission_wan": ota_cost,
            "owner_opex_wan": owner_cost,
            "operating_cost_wan": operating_cost,
            "tax_wan": tax, "income_tax_wan": tax,
            "vat_wan": vat, "surtax_wan": surtax,
            "loss_carryforward_wan": loss_carryforward,
            "interest_wan": debt_row["interest_wan"],
            "depreciation_wan": depreciation_month,
            "net_profit_wan": net_profit,
            "maintenance_capex_wan": maintenance_capex,
            "project_cf_wan": project_cf, "equity_cf_wan": equity_cf,
            "debt_service_wan": debt_row["debt_service_wan"], "dscr": cfads / debt_row["debt_service_wan"] if debt_row["debt_service_wan"] else None,
            "cash_wan": cash,
            "fixed_asset_net_wan": fixed_asset_net,
            "total_assets_wan": total_assets,
            "debt_wan": debt_wan,
            "equity_wan": equity_wan,
            "total_liabilities_equity_wan": total_le,
        })
        cursor = _add_months(cursor, 1)
    monthly_income_statement = [{key: row.get(key) for key in (
        "month", "period_start", "period_end", "hotel_revenue_wan", "lease_revenue_wan",
        "operating_cost_wan", "depreciation_wan", "interest_wan", "income_tax_wan", "net_profit_wan",
    )} | {"revenue_wan": row["hotel_revenue_wan"] + row["lease_revenue_wan"]} for row in monthly_rows]
    monthly_balance_sheet = [{key: row.get(key) for key in (
        "month", "period_start", "period_end", "cash_wan", "fixed_asset_net_wan",
        "total_assets_wan", "debt_wan", "equity_wan", "total_liabilities_equity_wan",
    )} for row in monthly_rows]
    reconciliation_fields = (
        "hotel_revenue_wan", "lease_revenue_wan", "revenue_wan", "operating_cost_wan",
        "income_tax_wan", "maintenance_capex_wan", "project_cf_wan", "equity_cf_wan",
        "debt_service_wan", "interest_wan", "vat_wan", "surtax_wan", "depreciation_wan", "net_profit_wan",
    )
    annual_reconciliation = []
    for year_index, annual_row in enumerate(annual):
        month_rows = monthly_rows[year_index * 12:(year_index + 1) * 12]
        for field in reconciliation_fields:
            monthly_total = sum(float(row.get(field) or 0.0) for row in month_rows)
            annual_total = float(annual_row.get(field) or 0.0)
            difference = annual_total - monthly_total
            annual_reconciliation.append({
                "year_index": year_index + 1,
                "field": field,
                "monthly_total": monthly_total,
                "annual_total": annual_total,
                "difference": difference,
                "status": "passed" if abs(difference) <= 1e-8 else "failed",
            })
    if any(item["status"] != "passed" for item in annual_reconciliation):
        raise AcquisitionModelError("monthly facts do not reconcile to annual summary")
    project_annual = [-total_cost, *[row["project_cf_wan"] for row in annual]]
    equity_annual = [-equity, *[row["equity_cf_wan"] for row in annual]]
    monthly_irr = _safe_irr(project_monthly)
    monthly_equity_irr = _safe_irr(equity_monthly)
    annual_irr = _safe_irr(project_annual) if monthly_irr is None else None
    annual_equity_irr = _safe_irr(equity_annual) if monthly_equity_irr is None else None
    monthly_rate = (1 + discount_rate) ** (1 / 12) - 1
    annual_dscr = [
        (row["project_cf_wan"] + row["debt_service_wan"]) / row["debt_service_wan"]
        if row["debt_service_wan"] else None
        for row in annual
    ]
    return {
        "available": True, "model_version": "acquisition_model.v3", "spec_version": LATEST_SPEC_VERSION,
        "scenario_id": scenario_id, "confirmation_status": spec.get("confirmation_status"),
        "validation_status": "calculated", "operating_mode": operating_mode,
        "calculation_granularity": "monthly", "purchase_price_wan": purchase_price,
        "transaction_tax_wan": transaction_tax, "total_acquisition_cost_wan": total_cost,
        "monthly_timeline": monthly_rows, "annual_summary": annual,
        "monthly_income_statement": monthly_income_statement,
        "monthly_balance_sheet": monthly_balance_sheet,
        "monthly_driver_manifest": driver_manifest,
        "operating_calendar": {"manifest": calendar_manifest, "periods": operating_calendar},
        "annual_reconciliation": annual_reconciliation,
        "project_cashflows_monthly_wan": project_monthly, "equity_cashflows_monthly_wan": equity_monthly,
        "project_cashflows_wan": project_annual, "equity_cashflows_wan": equity_annual,
        "debt_schedule_monthly": debt_rows, "debt_schedule": {"monthly": debt_rows},
        "depreciation_schedule": depreciation,
        "hotel_operation": {"available": operating_mode == "mixed_owner_operator", "monthly": monthly_rows},
        "lease_portfolio": {"monthly": monthly_rows},
        "owner_revenue_wan": [row["lease_revenue_wan"] if operating_mode == "owner_lessor" else row["hotel_revenue_wan"] + row["lease_revenue_wan"] for row in annual],
        "owner_operating_cost_wan": [row["operating_cost_wan"] for row in annual],
        "project_cfads_wan": [row["project_cf_wan"] + row["debt_service_wan"] for row in annual],
        "net_exit_value_wan": net_exit,
        "indicators": {
            "project_irr_pct": (
                ((1 + monthly_irr) ** 12 - 1) * 100
                if monthly_irr is not None
                else annual_irr * 100 if annual_irr is not None else None
            ),
            "equity_irr_pct": (
                ((1 + monthly_equity_irr) ** 12 - 1) * 100
                if monthly_equity_irr is not None
                else annual_equity_irr * 100 if annual_equity_irr is not None else None
            ),
            "npv_wan": sum(value / ((1 + monthly_rate) ** index) for index, value in enumerate(project_monthly)),
            "static_payback_years": payback_period(project_annual, rate=discount_rate).static_years,
            "dynamic_payback_years": payback_period(project_annual, rate=discount_rate).dynamic_years,
            "minimum_dscr": min((value for value in annual_dscr if value is not None), default=None),
            "minimum_monthly_dscr": min((row["dscr"] for row in monthly_rows if row["dscr"] is not None), default=None),
            "minimum_icr": min((row["project_cf_wan"] / row["interest_wan"] for row in annual if row["interest_wan"] > 0), default=None),
        },
        "assumptions": [
            "月度自然月计算；交割、开业与租约起止按实际有效天数计量",
            "租约到期后不默认续租；无有效合同的配套租赁不计入确认收入",
            "mixed_owner_operator 合并酒店自营与配套租赁收入，不将酒店可承受租金计作业主收入",
            "利润表净利润已扣建设期后利息与税金及附加；维护性资本支出资本化进固定资产，退出年资产出账并确认处置损益",
            *(
                [
                    "ota_commission 按取值判别语义："
                    + "、".join(
                        "比率（客房收入×系数）" if kind == "rate" else "金额（万元/期）"
                        for kind in sorted(ota_semantics_seen)
                    )
                    + "。若与预期不符请改用另一量级重报——该字段 [0,1] 判为比率、>1 判为金额。"
                ]
                if ota_semantics_seen
                else []
            ),
        ],
    }

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "AcquisitionModelError",
    "Any",
    "LATEST_SPEC_VERSION",
    "_add_months",
    "_date_value",
    "_depreciation_schedule",
    "_month_end",
    "_month_overlap_days",
    "_month_start",
    "_monthly_debt_schedule",
    "_monthly_lease_income",
    "_number",
    "_run_monthly_acquisition_model",
    "_safe_irr",
    "_series",
    "calendar",
    "payback_period",
    "resolve_monthly_driver",
    "resolve_operating_calendar",
]
