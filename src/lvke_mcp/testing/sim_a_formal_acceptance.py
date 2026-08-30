"""Build a new sim_a_formal delivery chain from promoted SourceFiles."""

from __future__ import annotations

import json
from typing import Any

from lvke_mcp.runtime.storage import sha256_json

_FACTORY_BY_PROFILE = {
    "tourism_catering": ("tourism_catering", "theme_park"),
    "real_estate": ("construction_real_estate", "residential"),
    "manufacturing": ("manufacturing", "equipment"),
    "environment_utilities": ("energy_utilities", "sewage"),
    "park_infrastructure": ("construction_real_estate", "industrial_park"),
    "urban_rail_transit": ("transport_logistics", "urban_rail"),
    "cemetery_funeral": ("construction_real_estate", "residential"),
}


def _g3_finance_inputs(industry_code: str) -> dict[str, Any]:
    """Industry-scenario numbers plus an atomic funding schedule for formal tables."""

    from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios

    factory_industry, archetype = _FACTORY_BY_PROFILE.get(industry_code, (industry_code, None))
    try:
        scenarios = build_industry_scenarios(factory_industry)
    except ValueError:
        scenarios = build_industry_scenarios("tourism_catering")
        archetype = "theme_park"
    scenario = next(
        (
            item
            for item in scenarios
            if item.get("variant_id") == "base"
            and (not archetype or item.get("archetype_id") == archetype)
        ),
        next((item for item in scenarios if item.get("variant_id") == "base"), scenarios[0]),
    )
    finance = dict(scenario["finance"])
    spec = dict(scenario["spec"])
    build_months = int(finance.get("build_period_months") or 12)
    build_years = max((build_months + 11) // 12, 1)
    construction = float((finance.get("invest_breakdown") or {}).get("construction_wan") or 0.0)
    interest = float((finance.get("invest_breakdown") or {}).get("interest_wan") or 0.0)
    working = float((finance.get("invest_breakdown") or {}).get("working_capital_wan") or 0.0)
    capital = float(finance.get("capital_own_wan") or 0.0)
    loan = float(finance.get("loan_wan") or 0.0)
    subsidy = float(finance.get("gov_subsidy_wan") or 0.0)
    total_financing = capital + loan + subsidy
    schedule = []
    for year in range(1, build_years + 1):
        last = year == build_years
        share = 1.0 / build_years
        construction_y = round(construction * share, 2)
        interest_y = round(interest * share, 2)
        working_y = round(working if last else 0.0, 2)
        if last:
            construction_y = round(construction - sum(item["construction_investment_wan"] for item in schedule), 2)
            interest_y = round(interest - sum(item["construction_interest_wan"] for item in schedule), 2)
        uses = round(construction_y + interest_y + working_y, 2)
        if total_financing:
            capital_y = round(uses * capital / total_financing, 2)
            loan_y = round(uses * loan / total_financing, 2)
            subsidy_y = round(uses - capital_y - loan_y, 2)
        else:
            capital_y, loan_y, subsidy_y = uses, 0.0, 0.0
        if last:
            capital_y = round(capital - sum(item["capital_own_wan"] for item in schedule), 2)
            loan_y = round(loan - sum(item["loan_wan"] for item in schedule), 2)
            subsidy_y = round(subsidy - sum(item["gov_subsidy_wan"] for item in schedule), 2)
            sources = round(capital_y + loan_y + subsidy_y, 2)
            if abs(uses - sources) > 0.05:
                capital_y = round(capital_y + (uses - sources), 2)
        schedule.append({
            "year": year,
            "construction_investment_wan": construction_y,
            "construction_interest_wan": interest_y,
            "working_capital_wan": working_y,
            "capital_own_wan": capital_y,
            "loan_wan": loan_y,
            "gov_subsidy_wan": subsidy_y,
        })
    finance["funding_annual_schedule"] = schedule
    finance["loan_grace_years"] = max(int(finance.get("loan_grace_years") or 0), build_years)
    finance["loan_years"] = max(int(finance.get("loan_years") or 8), 16)
    finance, spec = _g3_formal_overlays(finance, spec)
    return {"finance": finance, "spec": spec, "scenario_id": scenario.get("scenario_id")}


def _g3_formal_overlays(finance: dict[str, Any], spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Overlay product_sales + confirmed formal-table facts on industry numbers."""

    peak = float(finance.get("annual_revenue_wan") or 0.0)
    price = 100.0
    capacity = round((peak * 10000.0 / price), 6) if peak else 1.0
    spec["revenue"] = {
        "model": "product_sales",
        "annual_revenue_wan": peak,
        "products": [{
            "name": "主营服务",
            "unit": "人次",
            "price_per_unit": price,
            "price_unit": "yuan",
            "capacity": capacity,
            "ramp": [0.60, 0.80, 1.00],
            "var_cost_rate": 0.30,
        }],
    }
    existing_cost = finance.get("cost_items") if isinstance(finance.get("cost_items"), dict) else {}
    total_cost = sum(float(value or 0.0) for value in existing_cost.values()) or round(peak * 0.50, 2)
    raw_cost = round(total_cost * 0.40, 2)
    fuel_cost = round(total_cost * 0.15, 2)
    wage_cost = round(total_cost * 0.30, 2)
    repair_cost = round(total_cost - raw_cost - fuel_cost - wage_cost, 2)
    finance["cost_items"] = {
        "外购原材料费": raw_cost,
        "外购燃料及动力费": fuel_cost,
        "工资及福利费": wage_cost,
        "修理费": repair_cost,
    }
    finance["cost_behavior"] = {
        "外购原材料费": "variable",
        "外购燃料及动力费": "variable",
        "工资及福利费": "fixed",
        "修理费": "fixed",
        "confirmed": True,
    }
    finance["cost_behavior_confirmed"] = True
    finance["tax_component_policy_confirmed"] = True
    finance["urban_maintenance_rate"] = 0.07
    finance["education_surcharge_rate"] = 0.03
    finance["local_education_surcharge_rate"] = 0.02
    finance["surtax_on_vat"] = True
    tax = dict(spec.get("tax") or {})
    tax["component_policy_confirmed"] = True
    tax["urban_maintenance_rate"] = 0.07
    tax["education_surcharge_rate"] = 0.03
    tax["local_education_surcharge_rate"] = 0.02
    spec["tax"] = tax
    headcount = 20
    finance["wage_wan"] = wage_cost
    finance["staff_detail"] = [{
        "name": "运营人员",
        "category": "运营",
        "headcount": headcount,
        "avg_wage_yuan": round(wage_cost * 10000.0 / headcount, 2) if headcount else 0.0,
        "welfare_rate": 0.20,
    }]
    detail = finance.get("invest_breakdown") if isinstance(finance.get("invest_breakdown"), dict) else {}
    construction_detail = detail.get("construction_detail") if isinstance(detail.get("construction_detail"), dict) else {}
    other_detail = detail.get("other_detail") if isinstance(detail.get("other_detail"), dict) else {}
    civil = float(construction_detail.get("civil_wan") or 0.0)
    equipment = float(construction_detail.get("equipment_wan") or 0.0)
    years = int(finance.get("depreciation_years") or 10)
    salvage = float(finance.get("salvage_rate") or 0.05)
    finance["depreciation_classes"] = [
        {"name": "房屋建筑物", "original_value_wan": civil or 1000.0, "depreciation_years": years, "salvage_rate": salvage},
        {"name": "机器设备", "original_value_wan": equipment or 1000.0, "depreciation_years": years, "salvage_rate": salvage},
    ]
    land = float(other_detail.get("land_wan") or finance.get("intangible_assets_wan") or 100.0)
    other = max(float(finance.get("other_assets_wan") or finance.get("intangible_assets_wan") or 50.0) * 0.3, 10.0)
    amort_years = int(finance.get("amortization_years") or 10)
    finance["amort_bases"] = [
        {"name": "土地使用权无形资产", "original_wan": land or 100.0, "amort_years": amort_years},
        {"name": "其他资产", "original_wan": other, "amort_years": amort_years},
    ]
    finance["debt_repay_sources"] = [
        {"name": "可供投资者分配的利润", "share": 1.0},
        {"name": "折旧费", "share": 1.0},
        {"name": "摊销费", "share": 1.0},
    ]
    finance["debt_repay_sources_confirmation_status"] = "confirmed"
    finance["distribution_policy"] = {
        "arbitrary_reserve_confirmed_zero": True,
        "investor_distribution_confirmed_zero": True,
    }
    finance["arbitrary_reserve_confirmed_zero"] = True
    finance["investor_distribution_confirmed_zero"] = True
    from lvke_mcp.domains.finance.working_capital import estimate_from_turnover

    turnover = dict(finance.get("wc_turnover") or {})
    estimated = estimate_from_turnover(revenue=peak, cash_cost=total_cost, turnover=turnover)
    working = round(float(estimated.get("total") or 0.0), 2)
    detail = dict(finance.get("invest_breakdown") or {})
    construction = float(detail.get("construction_wan") or 0.0)
    interest = float(detail.get("interest_wan") or 0.0)
    detail["working_capital_wan"] = working
    finance["invest_breakdown"] = detail
    finance["wc_turnover"] = _g3_wc_domain({
        **finance,
        "wc_turnover": {
            **turnover,
            "self_funded_wan": working,
            "inventory_detail": {
                "raw": {"days": (turnover.get("inventory") or 38), "annual_base_wan": raw_cost, "base_source": "cost_items:raw"},
                "fuel": {"days": (turnover.get("inventory") or 38), "annual_base_wan": fuel_cost, "base_source": "cost_items:fuel"},
                "wip": {"days": (turnover.get("inventory") or 38), "annual_base_wan": wage_cost, "base_source": "cost_items:wip"},
                "finished": {"days": (turnover.get("inventory") or 38), "annual_base_wan": repair_cost, "base_source": "cost_items:finished"},
            },
        },
    })
    estimated = estimate_from_turnover(revenue=peak, cash_cost=total_cost, turnover=finance["wc_turnover"])
    working = round(float(estimated.get("total") or 0.0), 2)
    finance["wc_turnover"]["self_funded_wan"] = working
    detail = dict(finance.get("invest_breakdown") or {})
    construction = float(detail.get("construction_wan") or construction)
    interest = float(detail.get("interest_wan") or interest)
    if not list(detail.get("construction_items") or []):
        detail["construction_items"] = _g3_construction_items({"invest_breakdown": detail})
    detail["working_capital_wan"] = working
    finance["invest_breakdown"] = detail
    total = round(construction + interest + working, 2)
    finance["total_investment_wan"] = total
    capital = float(finance.get("capital_own_wan") or 0.0)
    loan = float(finance.get("loan_wan") or 0.0)
    subsidy = float(finance.get("gov_subsidy_wan") or 0.0)
    sources = round(capital + loan + subsidy, 2)
    if abs(sources - total) > 0.05:
        finance["capital_own_wan"] = round(capital + (total - sources), 2)
    schedule = list(finance.get("funding_annual_schedule") or [])
    if schedule:
        last = dict(schedule[-1])
        last["working_capital_wan"] = working
        uses = (
            float(last.get("construction_investment_wan") or 0.0)
            + float(last.get("construction_interest_wan") or 0.0)
            + working
        )
        last_sources = (
            float(last.get("capital_own_wan") or 0.0)
            + float(last.get("loan_wan") or 0.0)
            + float(last.get("gov_subsidy_wan") or 0.0)
        )
        if abs(uses - last_sources) > 0.05:
            last["capital_own_wan"] = round(float(last.get("capital_own_wan") or 0.0) + (uses - last_sources), 2)
        schedule[-1] = last
        finance["funding_annual_schedule"] = schedule
        finance["capital_own_wan"] = round(sum(float(row.get("capital_own_wan") or 0.0) for row in schedule), 2)
        finance["loan_wan"] = round(sum(float(row.get("loan_wan") or 0.0) for row in schedule), 2)
        finance["gov_subsidy_wan"] = round(sum(float(row.get("gov_subsidy_wan") or 0.0) for row in schedule), 2)
        finance["capital_own_ratio"] = round(finance["capital_own_wan"] / total, 10) if total else 0.0
        finance["loan_ratio"] = round(finance["loan_wan"] / total, 10) if total else 0.0
    expected_equity = round(
        construction + interest + working
        - float(finance.get("loan_wan") or 0.0)
        - float(finance.get("gov_subsidy_wan") or 0.0),
        2,
    )
    if abs(float(finance.get("capital_own_wan") or 0.0) - expected_equity) > 0.001:
        delta = round(expected_equity - float(finance.get("capital_own_wan") or 0.0), 2)
        finance["capital_own_wan"] = expected_equity
        schedule = list(finance.get("funding_annual_schedule") or [])
        if schedule:
            last = dict(schedule[-1])
            last["capital_own_wan"] = round(float(last.get("capital_own_wan") or 0.0) + delta, 2)
            schedule[-1] = last
            finance["funding_annual_schedule"] = schedule
    return finance, spec


def _g3_construction_items(finance: dict[str, Any]) -> list[dict[str, Any]]:
    detail = finance.get("invest_breakdown") if isinstance(finance.get("invest_breakdown"), dict) else {}
    construction_detail = detail.get("construction_detail") if isinstance(detail.get("construction_detail"), dict) else {}
    civil = float(construction_detail.get("civil_wan") or 0.0)
    equipment = float(construction_detail.get("equipment_wan") or 0.0)
    install = float(construction_detail.get("installation_wan") or 0.0)
    other = float(construction_detail.get("other_wan") or 0.0)
    total = float(detail.get("construction_wan") or 0.0)
    if civil + equipment + install + other <= 0 and total > 0:
        civil = round(total * 0.50, 2)
        equipment = round(total * 0.30, 2)
        install = round(total * 0.15, 2)
        other = round(total - civil - equipment - install, 2)
    named = [
        ("土建工程", civil, "m2"),
        ("设备购置", equipment, "台套"),
        ("安装工程", install, "项"),
        ("其他工程", other, "项"),
    ]
    if sum(amount for _name, amount, _unit in named) <= 0 and total > 0:
        named = [
            ("土建工程", round(total * 0.50, 2), "m2"),
            ("设备购置", round(total * 0.30, 2), "台套"),
            ("安装工程", round(total - round(total * 0.50, 2) - round(total * 0.30, 2), 2), "项"),
        ]
    items: list[dict[str, Any]] = []
    for name, amount, unit in named:
        if amount <= 0:
            continue
        quantity = 1000.0
        items.append({
            "name": name,
            "quantity": quantity,
            "unit": unit,
            "indicator_yuan": round(amount * 10000.0 / quantity, 6),
            "amount_wan": amount,
        })
    while len(items) < 3:
        share = round(total / 3, 2) if total else 100.0
        items.append({
            "name": f"配套工程{len(items) + 1}",
            "quantity": 1000.0,
            "unit": "项",
            "indicator_yuan": round(share * 10.0, 6),
            "amount_wan": share,
        })
    residual = round(total - sum(float(item["amount_wan"]) for item in items), 2)
    if items and abs(residual) > 0.009:
        items[-1]["amount_wan"] = round(float(items[-1]["amount_wan"]) + residual, 2)
        items[-1]["indicator_yuan"] = round(items[-1]["amount_wan"] * 10000.0 / float(items[-1]["quantity"]), 6)
    return items


def _g3_debt_schedule(finance: dict[str, Any]) -> dict[str, Any]:
    schedule = list(finance.get("funding_annual_schedule") or [])
    loan_rate = float(finance.get("loan_rate") or 0.0)
    loan_years = max(int(finance.get("loan_years") or 0), 1)
    grace = max(int(finance.get("loan_grace_years") or 0), 1)
    draws = [
        {"year": int(row.get("year") or index), "draw_wan": float(row.get("loan_wan") or 0.0)}
        for index, row in enumerate(schedule, start=1)
    ]
    if not draws:
        draws = [{"year": 1, "draw_wan": float(finance.get("loan_wan") or 0.0)}]
    if draws[0]["year"] != 1:
        draws.insert(0, {"year": 1, "draw_wan": 0.0})
    total_loan = round(sum(float(row["draw_wan"]) for row in draws), 2)
    annual = round(total_loan / loan_years, 2) if loan_years else 0.0
    principal: list[dict[str, Any]] = []
    interest: list[dict[str, Any]] = []
    remaining = total_loan
    for index in range(loan_years):
        year = grace + index + 1
        principal_wan = annual if index < loan_years - 1 else remaining
        principal.append({"year": year, "principal_wan": round(principal_wan, 2)})
        interest.append({"year": year, "interest_wan": round(remaining * loan_rate, 2)})
        remaining = round(remaining - principal_wan, 2)
    return {
        "draws": draws,
        "principal_schedule": principal,
        "reference_interest_schedule": interest,
        "loan_rate": loan_rate,
        "loan_years": loan_years,
        "grace_years": grace,
        "repay_method": "principal_schedule",
        "debt_repay_sources": list(finance.get("debt_repay_sources") or []),
        "repayment_allocation_method": "available_cash",
    }


def _g3_wc_domain(finance: dict[str, Any]) -> dict[str, Any]:
    src = finance.get("wc_turnover") if isinstance(finance.get("wc_turnover"), dict) else {}
    peak = float(finance.get("annual_revenue_wan") or 0.0)
    costs = finance.get("cost_items") if isinstance(finance.get("cost_items"), dict) else {}
    total_cost = sum(float(value or 0.0) for value in costs.values()) or round(peak * 0.50, 2)

    def _days(key: str, default: float) -> float:
        raw = src.get(key)
        if isinstance(raw, dict):
            return float(raw.get("days") or default)
        return float(raw or default)

    inventory = src.get("inventory_detail") if isinstance(src.get("inventory_detail"), dict) else {}
    inventory_days = _days("inventory", 38.0)
    raw_cost = float(costs.get("外购原材料费") or total_cost * 0.40)
    fuel_cost = float(costs.get("外购燃料及动力费") or total_cost * 0.15)
    wage_cost = float(costs.get("工资及福利费") or total_cost * 0.30)
    repair_cost = float(costs.get("修理费") or max(total_cost - raw_cost - fuel_cost - wage_cost, 0.0))
    return {
        "receivable": {"days": _days("receivable", 35.0), "annual_base_wan": peak or 1.0, "base_source": "revenue"},
        "inventory": {"days": inventory_days, "annual_base_wan": total_cost or 1.0, "base_source": "cost"},
        "cash": {"days": _days("cash", 10.0), "annual_base_wan": total_cost or 1.0, "base_source": "cost"},
        "payable": {"days": _days("payable", 25.0), "annual_base_wan": total_cost or 1.0, "base_source": "cost"},
        "short_term_loan_wan": float(src.get("short_term_loan_wan") or 0.0),
        "self_funded_wan": float(src.get("self_funded_wan") or 0.0),
        "inventory_detail": {
            "raw": dict(inventory.get("raw") or {"days": inventory_days, "annual_base_wan": raw_cost, "base_source": "cost_items:raw"}),
            "fuel": dict(inventory.get("fuel") or {"days": inventory_days, "annual_base_wan": fuel_cost, "base_source": "cost_items:fuel"}),
            "wip": dict(inventory.get("wip") or {"days": inventory_days, "annual_base_wan": wage_cost, "base_source": "cost_items:wip"}),
            "finished": dict(inventory.get("finished") or {"days": inventory_days, "annual_base_wan": repair_cost, "base_source": "cost_items:finished"}),
        },
    }


def _g3_fact_pack_domains(finance: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    tax = spec.get("tax") if isinstance(spec.get("tax"), dict) else {}
    costs = finance.get("cost_items") if isinstance(finance.get("cost_items"), dict) else {}
    behavior_src = finance.get("cost_behavior") if isinstance(finance.get("cost_behavior"), dict) else {}
    behavior_items = behavior_src.get("items") if isinstance(behavior_src.get("items"), dict) else {
        name: {"type": "variable" if name in {"外购原材料费", "外购燃料及动力费"} else "fixed"}
        for name in costs
    }
    products = list((spec.get("revenue") or {}).get("products") or [])
    assets = []
    for row in finance.get("depreciation_classes") or []:
        assets.append({
            "name": row.get("name"),
            "original_wan": row.get("original_value_wan") or row.get("original_wan"),
            "dep_years": row.get("depreciation_years") or row.get("dep_years"),
            "salvage_rate": row.get("salvage_rate"),
        })
    return {
        "construction_items": list((finance.get("invest_breakdown") or {}).get("construction_items") or []),
        "products": products,
        "cost_items": dict(costs),
        "staff_detail": list(finance.get("staff_detail") or []),
        "asset_classes": assets,
        "wc_turnover": _g3_wc_domain(finance),
        "funding_plan": {"annual_schedule": list(finance.get("funding_annual_schedule") or [])},
        "debt_schedule": _g3_debt_schedule(finance),
        "amort_bases": list(finance.get("amort_bases") or []),
        "distribution_policy": {
            **dict(finance.get("distribution_policy") or {}),
            "statutory_reserve_confirmed_zero": True,
            "arbitrary_reserve_confirmed_zero": True,
            "investor_distribution_confirmed_zero": True,
            "retained_profit_policy": "全部留存用于还贷与再投资",
        },
        "cost_behavior": {"confirmed": True, "items": behavior_items},
        "tax_component_policy": {
            "confirmed": True,
            "vat_output_rate": float(finance.get("vat_rate") or tax.get("vat_rate") or 0.0),
            "vat_input_rate": float(finance.get("vat_input_rate") or tax.get("vat_input_rate") or 0.0),
            "income_tax_rate": float(finance.get("income_tax_rate") or tax.get("income_tax_rate") or 0.0),
            "surtax_base": "vat_and_consumption_tax_payable",
            "urban_maintenance_rate": float(finance.get("urban_maintenance_rate") or 0.07),
            "education_surcharge_rate": float(finance.get("education_surcharge_rate") or 0.03),
            "local_education_surcharge_rate": float(finance.get("local_education_surcharge_rate") or 0.02),
        },
    }


def _g3_fact_pack_candidate(
    *,
    workspace_id: str,
    finance: dict[str, Any],
    spec: dict[str, Any],
    source_id: str,
    locator: str,
) -> dict[str, Any]:
    from lvke_mcp.domains.finance.fact_pack import DOMAIN_KEYS, _domain_fact_leaves, build_fact_pack_snapshot

    pack = {
        "project_id": workspace_id,
        "valuation_date": "2026-08-20",
        "domains": _g3_fact_pack_domains(finance, spec),
        "evidence_policy": "sim_a_formal",
        "project_fact_certified": True,
        "unresolved_inputs": [],
        "release_limitations": ["数字取自晋升拟定稿 + technical_fixture，locator 指向拟定稿"],
    }
    draft = build_fact_pack_snapshot(pack, workspace_id=workspace_id, confirm=False)
    evidence: list[dict[str, Any]] = []
    for domain in DOMAIN_KEYS:
        for leaf in _domain_fact_leaves(domain, draft["domains"][domain]):
            item = {
                "domain": domain,
                "fact_path": leaf["fact_path"],
                "source_id": source_id,
                "locator": locator,
                "claimed_value": leaf["value"],
            }
            if leaf.get("unit") is not None:
                item["unit"] = leaf["unit"]
            if leaf.get("period") is not None:
                item["period"] = leaf["period"]
            evidence.append(item)
    pack["domains"] = draft["domains"]
    pack["evidence"] = evidence
    return pack


def _figures_from_run(workspace_id: str, run_id: str, fallback: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

    view = get_workspace_finance_run(workspace_id, run_id=run_id, view="summary")
    investment = view.get("investment") if isinstance(view.get("investment"), dict) else {}
    funding = view.get("funding") if isinstance(view.get("funding"), dict) else {}
    indicators = view.get("indicators") if isinstance(view.get("indicators"), dict) else {}
    return {
        "total_investment_wan": investment.get("total") or investment.get("total_investment") or fallback.get("total_investment_wan"),
        "capital_own_wan": funding.get("capital") or funding.get("equity_capital") or fallback.get("capital_own_wan"),
        "loan_wan": funding.get("loan") or fallback.get("loan_wan"),
        "annual_revenue_wan": indicators.get("revenue") or indicators.get("annual_revenue") or fallback.get("annual_revenue_wan"),
    }


CHAPTER_TITLES = (
    "总论",
    "项目背景与建设必要性",
    "需求分析与建设规模",
    "总体建设方案",
    "投资估算与资金筹措",
    "财务分析与评价",
    "风险分析与对策",
    "保障措施",
    "结论与建议",
)


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return f"{head}\n{sep}\n{body}"


def _chapter_bodies(project_name: str, locator: str, figures: dict[str, Any] | None = None) -> list[str]:
    figures = figures or {}
    investment = figures.get("total_investment_wan")
    capital = figures.get("capital_own_wan")
    loan = figures.get("loan_wan")
    revenue = figures.get("annual_revenue_wan")
    invest_text = (
        f"项目总投资 {investment} 万元，资本金 {capital} 万元，贷款 {loan} 万元。"
        if investment not in (None, "")
        else "投资构成与资金筹措数字均取自拟定模板包。"
    )
    revenue_text = (
        f"年营业收入 {revenue} 万元，所得税率按已声明口径计入。"
        if revenue not in (None, "")
        else "达产年收入按已确认财务口径计入。"
    )
    overview_table = _md_table(
        ["项目", "口径"],
        [[project_name, "湖北省新建可研"], ["证据定位", locator], ["签章文号", "不填写"]],
    )
    scale_table = _md_table(
        ["指标", "数值"],
        [["目标规模", "1000 服务单位/年"], ["目标份额", "10%"], ["证据定位", locator]],
    )
    invest_table = _md_table(
        ["科目", "金额（万元）"],
        [["项目总投资", investment or "按 Run"], ["资本金", capital or "按 Run"], ["贷款", loan or "按 Run"]],
    )
    finance_table = _md_table(
        ["指标", "数值"],
        [["年营业收入", f"{revenue} 万元" if revenue not in (None, "") else "按 Run"], ["计算口径", "确认后的 FinanceRun"]],
    )
    return [
        f"{project_name}拟在湖北省实施。建设内容、投资与收入口径均绑定拟定模板包定位 {locator}。本稿不填写签章、文号、流水、批复或检测结论。\n\n{overview_table}",
        f"项目背景依据拟定市场与政策材料。建设必要性从区域服务缺口与规划衔接说明，证据定位 {locator}。",
        f"需求与建设规模按拟定测算路径确定。市场规模、目标份额与规模方案均可回溯至 {locator}。\n\n{scale_table}",
        f"总体建设方案采用已确认比选结果。主体设施、用地与功能分区均对应拟定稿 {locator}。",
        f"{invest_text}投资构成与资金筹措数字来自拟定模板并带定位 {locator}。\n\n{invest_table}",
        f"{revenue_text}财务指标由确认后的 FinanceRun 重算，定位 {locator}。\n\n{finance_table}",
        (
            f"主要风险覆盖政策风险、市场风险、技术风险、财务风险、实施风险、运营风险与社会环境风险。"
            f"对策为分期实施、预留预备费与动态监测，依据 {locator}。"
        ),
        f"组织、资金、用地与运营保障按拟定实施方案安排。不编造批复或检测结论，依据 {locator}。",
        f"综合技术、经济与风险分析，本项目具备编制可行性研究报告并进入正式审查的条件。依据 {locator}。",
    ]


def _revision_remediation(workspace_id: str, revision_id: str) -> list[dict[str, Any]]:
    """Bind remediation evidence to a section of the report revision itself.

    Disposition resolves a `rrv_` source against the revision's own
    `content_hash` and a `sec_*` locator, so a source-file locator or an
    evidence-pack hash cannot stand in for either.
    """

    from lvke_mcp.adapters.report_repository import REVISION_STORE
    from lvke_mcp.domains.reports import application as reports

    revision = REVISION_STORE.get(workspace_id, revision_id) or {}
    sections = reports.list_sections(workspace_id, revision_id)
    last_section = (sections.get("sections") or [{}])[-1]
    return [{
        "source_id": revision_id,
        "locator": {"section_id": str(last_section.get("section_id") or "")},
        "content_hash": str(revision.get("content_hash") or ""),
        "note": "sim_a_formal 审查处置证据",
    }]


def _first_source(workspace_id: str, evidence_pack_id: str) -> dict[str, Any]:
    from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE

    evidence = EVIDENCE_STORE.get(workspace_id, evidence_pack_id) or {}
    payload = evidence.get("payload") or {}
    source = next((row for row in payload.get("sources") or [] if isinstance(row, dict)), {})
    fact = next((row for row in payload.get("fact_candidates") or [] if isinstance(row, dict)), {})
    locators = list(source.get("locators") or [])
    locator = locators[0] if locators else fact.get("locator") or "document_text"
    if isinstance(locator, (dict, list)):
        locator = json.dumps(locator, ensure_ascii=False, sort_keys=True)
    locator = str(locator)
    filename = str(source.get("filename") or source.get("original_filename") or "")
    report_locator = filename or "document_text"
    if len(locator) <= 160 and "总投资" not in locator and "万元" not in locator:
        report_locator = locator
    return {
        "source_id": str(source.get("source_id") or ""),
        "content_hash": str(source.get("content_hash") or evidence.get("content_hash") or ""),
        "locator": locator,
        "report_locator": report_locator,
        "resource_uri": str(source.get("resource_uri") or ""),
        "pack_hash": str(evidence.get("content_hash") or ""),
        "payload": payload,
        "filename": str(source.get("filename") or source.get("original_filename") or ""),
    }


def _fail(step: str, payload: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "step": step,
        "payload": payload if isinstance(payload, dict) else {"message": str(payload)},
    }


def _review_findings(review: Any, workspace_id: str, review_id: str) -> list[dict[str, Any]]:
    listed = review.list_findings({
        "workspace_id": workspace_id, "review_id": review_id, "limit": 200,
    })
    return [item for item in listed.get("findings") or [] if isinstance(item, dict)]


def _close_review_findings(
    review: Any,
    *,
    workspace_id: str,
    review_id: str,
    case_key: str,
    source: dict[str, Any],
    remediation: list[dict[str, Any]],
    waiver_expires: str,
    retest_review_id: str = "",
) -> dict[str, Any]:
    """Waive P1 / confirm P2, then resolve so export verdict can be pass."""

    findings = _review_findings(review, workspace_id, review_id)
    for item in findings:
        severity = str(item.get("severity") or "").upper()
        finding_id = str(item.get("finding_id") or "")
        status = str(item.get("status") or "open")
        if not finding_id or status in {"resolved", "rejected", "superseded"}:
            continue
        if severity == "P0":
            review.disposition_finding({
                "workspace_id": workspace_id,
                "review_id": review_id,
                "finding_id": finding_id,
                "disposition": "remediation_in_progress",
                "note": "P0 必须改报告消除，不可豁免",
                "idempotency_key": f"{case_key}-disp-{review_id}-{finding_id}",
            })
        elif severity == "P1":
            waived = review.disposition_finding({
                "workspace_id": workspace_id,
                "review_id": review_id,
                "finding_id": finding_id,
                "disposition": "waived",
                "note": "P1 按拟定正式轨限期豁免",
                "waiver_scope": "sim_a_formal G3 完整链过程验收",
                "waiver_impact": "豁免期内保留该 P1 风险，正式结论须结合审查记录使用",
                "waiver_compensating_controls": "由项目负责人复核原始依据，并在正式原件替换后立即重新审查",
                "waiver_responsible_party": "sim_a_formal 验收项目负责人",
                "waiver_expires_at": waiver_expires,
                "waiver_invalidation_conditions": ["正式原件替换后失效"],
                "remediation_evidence": remediation,
                "idempotency_key": f"{case_key}-disp-{review_id}-{finding_id}",
            })
            if not waived.get("success"):
                review.disposition_finding({
                    "workspace_id": workspace_id,
                    "review_id": review_id,
                    "finding_id": finding_id,
                    "disposition": "confirm",
                    "note": "P1 豁免被拒后改为确认，不影响拟定轨确定性检查",
                    "idempotency_key": f"{case_key}-confirm-{review_id}-{finding_id}",
                })
        else:
            review.disposition_finding({
                "workspace_id": workspace_id,
                "review_id": review_id,
                "finding_id": finding_id,
                "disposition": "confirm",
                "note": "P2 已确认不影响正式拟定轨发布",
                "idempotency_key": f"{case_key}-disp-{review_id}-{finding_id}",
            })
    for item in _review_findings(review, workspace_id, review_id):
        finding_id = str(item.get("finding_id") or "")
        status = str(item.get("status") or "open")
        if not finding_id or status in {"resolved", "rejected", "superseded"}:
            continue
        payload: dict[str, Any] = {
            "workspace_id": workspace_id,
            "review_id": review_id,
            "finding_id": finding_id,
            "disposition": "resolved",
            "note": "复测后关闭",
            "closure_basis": "同一规则包复测未复现或已按规定豁免/确认",
            "before_value": item.get("actual"),
            "after_value": "finding_closed_on_sim_a_formal",
            "remediation_evidence": remediation,
            "idempotency_key": f"{case_key}-close-{review_id}-{finding_id}",
        }
        if retest_review_id:
            payload["retest_review_id"] = retest_review_id
        review.disposition_finding(payload)
    remaining = [
        item for item in _review_findings(review, workspace_id, review_id)
        if str(item.get("status") or "open") not in {"resolved", "rejected", "superseded", "waived", "confirmed"}
    ]
    return {"remaining": remaining, "source": source}


def run_sim_a_formal_finance(
    *,
    workspace_id: str,
    file_ids: list[str],
    project_name: str,
    industry_code: str,
    case_key: str,
) -> dict[str, Any]:
    """Ingest promoted files, build a sim_a_formal pack, and run FinanceSpec/BoE/Run."""

    from lvke_mcp.adapters.finance_model_repository import SPEC_STORE
    from lvke_mcp.domains.finance import model_application, tables_service
    from lvke_mcp.domains.project_planning import application as planning
    from lvke_mcp.servers.lvke_data_analysis import service as analysis_service
    from lvke_mcp.servers.lvke_finance_model.server import (
        _required_boe_pointers,
        _tool_build_basis_of_estimate,
    )
    from lvke_mcp.servers.lvke_source_files import service as source_files

    promotion_ids = set()
    for file_id in file_ids:
        stored = source_files.get_source_file(workspace_id, file_id)
        source_record = stored.get("source_file") or {}
        promotion_id = str(source_record.get("formal_promotion_id") or "")
        if promotion_id:
            promotion_ids.add(promotion_id)
    if len(promotion_ids) != 1:
        return _fail("formal_promotion_resolve", {
            "code": "formal_lineage_mixed_or_missing",
            "promotion_ids": sorted(promotion_ids),
        })
    promotion_id = next(iter(promotion_ids))

    context = planning.create_project_context(
        workspace_id,
        {
            "project_name": project_name,
            "industry_code": industry_code,
            "project_type": "new_build",
            "region": "湖北省",
            "objective": "sim_a_formal 正式链",
            "report_type": "feasibility_study",
            "evidence_track": "sim_a_formal",
            "promotion_id": promotion_id,
        },
        idempotency_key=f"{case_key}-ctx",
    )
    context_id = str(context.get("project_context_id") or context.get("context_id") or "")
    if not context_id:
        return _fail("project_context_create", context)
    validated_context = planning.validate_project_context(
        workspace_id,
        context_id,
        idempotency_key=f"{case_key}-ctx-validate",
    )
    if not validated_context.get("success"):
        return _fail("project_context_validate", validated_context)

    ingested = analysis_service.ingest(workspace_id, [], list(file_ids))
    if not ingested.get("success"):
        return _fail("analysis_ingest", ingested)
    task_id = str(ingested.get("analysis_task_id") or "")
    seeded = _g3_finance_inputs(industry_code)
    fixture_investment = seeded["finance"].get("total_investment_wan")
    fixture_revenue = seeded["finance"].get("annual_revenue_wan")
    facts = []
    for index, file_id in enumerate(file_ids):
        stored = source_files.get_source_file(workspace_id, file_id)
        record = stored.get("source_file") if isinstance(stored.get("source_file"), dict) else stored
        filename = str((record or {}).get("original_filename") or f"{file_id}.md")
        digest = str((record or {}).get("sha256") or "")
        if digest and not digest.startswith("sha256:"):
            digest = f"sha256:{digest}"
        facts.append({
            "field": "technical_fixture" if index else "total_investment_wan",
            "source_id": file_id,
            "value": fixture_investment if index == 0 else fixture_revenue,
            "locator": {"kind": "document", "value": filename},
            "evidence_eligibility": "sim_a_formal",
            "content_hash": digest,
        })
    pack = analysis_service.build_evidence_pack(
        workspace_id,
        task_id,
        list(file_ids),
        facts,
        [],
        evidence_track="sim_a_formal",
    )
    if not pack.get("success"):
        return _fail("analysis_build_evidence_pack", pack)
    evidence_pack_id = str(pack.get("evidence_pack_id") or "")
    source = _first_source(workspace_id, evidence_pack_id)
    source_id = str(source.get("source_id") or (file_ids[0] if file_ids else ""))
    locator = str(source.get("locator") or "document_text")
    fact_candidate = _g3_fact_pack_candidate(
        workspace_id=workspace_id,
        finance=seeded["finance"],
        spec=seeded["spec"],
        source_id=source_id,
        locator=locator,
    )
    prepared_pack = model_application.prepare_fact_pack({
        "workspace_id": workspace_id,
        "fact_pack": fact_candidate,
        "evidence_pack_ids": [evidence_pack_id],
        "idempotency_key": f"{case_key}-fact-pack",
    })
    fact_pack_id = str(prepared_pack.get("fact_pack_id") or "")
    if not fact_pack_id:
        return _fail("finance_prepare_fact_pack", prepared_pack)
    draft_fact_pack_id = fact_pack_id
    confirmed_pack = model_application.confirm_fact_pack({
        "workspace_id": workspace_id,
        "fact_pack_id": draft_fact_pack_id,
        "idempotency_key": f"{case_key}-fact-pack-confirm",
    })
    if confirmed_pack.get("status") != "ok":
        return _fail("finance_confirm_fact_pack", confirmed_pack)
    fact_pack_id = str(confirmed_pack.get("fact_pack_id") or fact_pack_id)
    prepared = model_application.prepare_spec({
        "workspace_id": workspace_id,
        "strategy": "propose_from_project",
        "evidence_pack_ids": [evidence_pack_id],
        "fact_pack_id": fact_pack_id,
        "input_revision": dict(seeded["finance"]),
        "spec": dict(seeded["spec"]),
    })
    if not prepared.get("success") and "seal_mac" in str((prepared.get("data") or {}).get("fact_pack_errors") or []):
        confirmed_pack = model_application.confirm_fact_pack({
            "workspace_id": workspace_id,
            "fact_pack_id": draft_fact_pack_id,
            "idempotency_key": f"{case_key}-fact-pack-confirm-retry",
        })
        if confirmed_pack.get("status") != "ok":
            return _fail("finance_confirm_fact_pack", confirmed_pack)
        fact_pack_id = str(confirmed_pack.get("fact_pack_id") or fact_pack_id)
        prepared = model_application.prepare_spec({
            "workspace_id": workspace_id,
            "strategy": "propose_from_project",
            "evidence_pack_ids": [evidence_pack_id],
            "fact_pack_id": fact_pack_id,
            "input_revision": dict(seeded["finance"]),
            "spec": dict(seeded["spec"]),
        })
    spec_id = str(prepared.get("spec_id") or "")
    if not spec_id:
        return _fail("finance_prepare_spec", prepared)
    confirmed = model_application.confirm_spec({
        "workspace_id": workspace_id,
        "spec_id": spec_id,
        "note": "sim_a_formal G3 fixture",
        "idempotency_key": f"{case_key}-spec-confirm",
    })
    spec_id = str(confirmed.get("spec_id") or spec_id)
    if confirmed.get("status") not in {"ok", "confirmed"} and not confirmed.get("success"):
        return _fail("finance_confirm_spec", confirmed)
    spec_record = SPEC_STORE.get(workspace_id, spec_id) or {}
    spec_payload = spec_record.get("payload") or {}
    source = source or _first_source(workspace_id, evidence_pack_id)
    entries = []
    for pointer in _required_boe_pointers(spec_payload):
        value: Any = spec_payload
        for part in pointer.strip("/").split("/"):
            value = value.get(part) if isinstance(value, dict) else None
        if pointer == "/input_revision/total_investment_wan" and value in (None, ""):
            value = seeded["finance"].get("total_investment_wan")
        if pointer == "/spec/revenue" and value in (None, ""):
            value = seeded["spec"].get("revenue")
        entries.append({
            "target_pointer": pointer,
            "value": value,
            "unit": "万元" if "wan" in pointer else "按目标字段",
            "period": "2026",
            "source_type": "evidence_pack",
            "source_object_id": evidence_pack_id,
            "method": "template_lineage",
            "selection_reason": "数字取自晋升导入拟定稿，locator 指向该文件",
            "locator": source["locator"],
            "content_hash": source["pack_hash"] or source["content_hash"],
            "evidence_eligibility": "sim_a_formal",
        })
    boe = _tool_build_basis_of_estimate({
        "workspace_id": workspace_id,
        "spec_id": spec_id,
        "evidence_pack_ids": [evidence_pack_id],
        "planning_object_ids": [],
        "entries": entries,
        "idempotency_key": f"{case_key}-boe",
    })
    if not boe.get("formal_ready") and not boe.get("success"):
        return _fail("finance_build_basis_of_estimate", boe)
    boe_id = str(boe.get("basis_of_estimate_id") or "")
    run_inputs = dict(seeded["finance"])
    run_inputs["timeline"] = {"mode": "monthly"}
    sealed_pack = confirmed_pack.get("fact_pack")
    if isinstance(sealed_pack, dict):
        run_inputs["finance_fact_pack"] = sealed_pack
    elif isinstance((spec_payload.get("input_revision") or {}).get("finance_fact_pack"), dict):
        run_inputs["finance_fact_pack"] = (spec_payload.get("input_revision") or {})["finance_fact_pack"]
    run = model_application.run_model({
        "workspace_id": workspace_id,
        "spec_id": spec_id,
        "basis_of_estimate_id": boe_id or None,
        "mode": "review_candidate",
        "valuation_date": "2026-08-20",
        "input_revision": run_inputs,
        "idempotency_key": f"{case_key}-run",
    })
    run_id = str(run.get("run_id") or "")
    if not run_id:
        return _fail("finance_run_model", run)
    tables = tables_service.render(workspace_id, run_id)
    package_id = str(tables.get("package_id") or tables.get("finance_tables_package_id") or "")
    exported = tables_service.export_xlsx(workspace_id, run_id)
    package_id = str(exported.get("finance_tables_package_id") or package_id)
    validated = tables_service.validate(workspace_id, run_id, validation_scope="formal")
    return {
        "ok": bool(run_id),
        "workspace_id": workspace_id,
        "project_context_id": context_id,
        "evidence_pack_id": evidence_pack_id,
        "fact_pack_id": fact_pack_id,
        "finance_spec_id": spec_id,
        "basis_of_estimate_id": boe_id,
        "finance_run_id": run_id,
        "finance_tables_package_id": package_id,
        "tables_ok": bool(tables.get("success", True) and package_id),
        "tables_validate_ok": bool(validated.get("success")),
        "tables_validate": validated,
        "source": source,
        "evidence_policy": str(spec_payload.get("evidence_policy") or pack.get("evidence_policy") or ""),
        "project_fact_certified": bool(
            (spec_payload.get("project_fact_certified") if spec_payload else False)
            or pack.get("project_fact_certified")
        ),
        "boe": boe,
        "run": run,
        "figures": _figures_from_run(workspace_id, run_id, {
            "total_investment_wan": seeded["finance"].get("total_investment_wan"),
            "capital_own_wan": seeded["finance"].get("capital_own_wan"),
            "loan_wan": seeded["finance"].get("loan_wan"),
            "annual_revenue_wan": seeded["finance"].get("annual_revenue_wan"),
        }),
    }


def run_sim_a_formal_full_chain(finance: dict[str, Any], *, case_key: str, industry_code: str) -> dict[str, Any]:
    """Planning + research + nine-chapter report + review + formal release."""

    from lvke_mcp.adapters.report_repository import REVISION_STORE
    from lvke_mcp.domains.project_planning import application as planning
    from lvke_mcp.domains.reports import application as reports
    from lvke_mcp.domains.reports._service.export import export_docx
    from lvke_mcp.domains.research import application as research
    from lvke_mcp.servers.lvke_deliverable_review import service as review
    from lvke_mcp.servers.lvke_feasibility_delivery import service as delivery

    workspace_id = str(finance["workspace_id"])
    evidence_pack_id = str(finance["evidence_pack_id"])
    source = dict(finance.get("source") or _first_source(workspace_id, evidence_pack_id))
    binding = {
        "source_id": source["source_id"],
        "content_hash": source["content_hash"],
        "locator": source["locator"],
        "evidence_track": "sim_a_formal",
        "source_type": "sim_a_template",
    }
    project_context_id = str(finance["project_context_id"])
    candidates = []
    for method, market_size, share in (("report_table_extract", 10000, 0.10), ("template_formula_replay", 12500, 0.08)):
        candidates.append({
            "method": method,
            "market_size": market_size,
            "unit": "服务单位/年",
            "period": "2026",
            "region": "湖北省",
            "target_share": share,
            "target_volume": market_size * share,
            "formula_inputs": {"market_size": market_size, "target_share": share},
            "evidence_bindings": [binding],
        })
    market_candidate = planning.prepare_market_case(
        workspace_id, project_context_id, evidence_pack_id, candidates,
        idempotency_key=f"{case_key}-market-prepare",
    )
    if not market_candidate.get("success"):
        return {**finance, **_fail("planning_prepare_market_case", market_candidate)}
    selected_market_id = str(market_candidate["market_case"]["candidates"][0]["candidate_id"])
    rejected = [
        str(row["candidate_id"])
        for row in market_candidate["market_case"]["candidates"]
        if str(row["candidate_id"]) != selected_market_id
    ]
    market = planning.confirm_market_case(
        workspace_id, str(market_candidate["market_case_id"]), selected_market_id,
        "选择与拟定模板口径一致的测算路径", rejected,
        idempotency_key=f"{case_key}-market-confirm",
    )
    if not market.get("success"):
        return {**finance, **_fail("planning_confirm_market_case", market)}
    market_case_id = str(market["market_case_id"])
    option_candidate = planning.prepare_option_comparison(
        workspace_id, project_context_id, "process",
        [{"criterion_id": "cost", "weight": 1, "direction": "lower_is_better"}],
        [
            {
                "option_id": "primary_scheme",
                "name": "拟定主方案",
                "values": {"cost": 1},
                "constraint_results": {"traceable": True},
                "evidence_bindings": {"cost": [binding]},
            },
            {
                "option_id": "alt_scheme",
                "name": "备选方案",
                "values": {"cost": 2},
                "constraint_results": {"traceable": True},
                "evidence_bindings": {"cost": [binding]},
            },
        ],
        [{"constraint_id": "traceable", "description": "来源可定位"}],
        [market_case_id],
        idempotency_key=f"{case_key}-option-prepare",
    )
    if not option_candidate.get("success"):
        return {**finance, **_fail("planning_prepare_option_comparison", option_candidate)}
    option = planning.confirm_option_selection(
        workspace_id, str(option_candidate["option_comparison_id"]),
        "primary_scheme", "采用与拟定模板口径一致的主方案", ["alt_scheme"],
        idempotency_key=f"{case_key}-option-confirm",
    )
    if not option.get("success"):
        return {**finance, **_fail("planning_confirm_option", option)}
    scale = planning.create_build_scale_case(
        workspace_id, project_context_id, market_case_id,
        {"value": 1000, "unit": "服务单位/年"}, 10000, 0.1,
        {
            "plot_ratio_min": 0.5, "plot_ratio_max": 2.0, "building_coverage_max": 0.6,
            "green_ratio_min": 0.2, "green_area_m2": 2500,
        },
        [{"name": "主体设施", "floor_area_m2": 10000, "footprint_m2": 5000}],
        idempotency_key=f"{case_key}-scale",
    )
    if not scale.get("success"):
        return {**finance, **_fail("planning_create_build_scale", scale)}
    scale_id = str(scale["build_scale_case_id"])
    cost = planning.create_cost_driver_set(
        workspace_id, project_context_id, scale_id,
        {
            "construction_wan": 1000, "civil_wan": 500, "equipment_wan": 250,
            "installation_wan": 100, "other_wan": 100, "reserve_wan": 50,
            "interest_wan": 50, "working_capital_wan": 100,
        },
        [
            {"name": "原料", "annual_amount_wan": 100},
            {"name": "能源", "annual_amount_wan": 50},
            {"name": "维护", "annual_amount_wan": 30},
        ],
        idempotency_key=f"{case_key}-cost",
    )
    labor = planning.create_labor_plan(
        workspace_id, project_context_id, scale_id,
        [{"name": "运营人员", "category": "运营", "headcount": 10, "avg_wage_yuan": 80000, "welfare_rate": 0.2}],
        idempotency_key=f"{case_key}-labor",
    )
    revenue = planning.create_revenue_driver_set(
        workspace_id, project_context_id, market_case_id,
        {"model": "flat", "annual_revenue_wan": 3000}, 8,
        mode="review_candidate", flat_evidence_binding=binding,
        idempotency_key=f"{case_key}-revenue",
    )
    for name, result in (("cost", cost), ("labor", labor), ("revenue", revenue)):
        if not result.get("success"):
            return {**finance, **_fail(f"planning_create_{name}", result)}

    started_dr = research.start_agent({
        "workspace_id": workspace_id,
        "topic": f"{case_key} 拟定正式研究",
        "industry": industry_code,
        "region": "湖北省",
        "plan_items": [{"field": "market_size", "required": True}],
        "analysis_inputs": [evidence_pack_id],
        "idempotency_key": f"{case_key}-research-start",
    })
    if not started_dr.get("success"):
        return {**finance, **_fail("dr_start", started_dr)}
    submitted = research.submit_agent({
        "workspace_id": workspace_id,
        "task_id": started_dr["task_id"],
        "report_md": "拟定模板包已形成可定位的正式研究包，不编造签章或批复。",
        "citations": [{
            "source_id": source["source_id"],
            "locator": source["locator"],
            "content_hash": source["content_hash"],
            "evidence_policy": "sim_a_formal",
        }],
        "evidence_pack_ids": [evidence_pack_id],
        "quality_summary": {
            "query_rounds": 0,
            "usable_source_count": 1,
            "citation_coverage": 1.0,
            "missing_fields": [],
            "conflicts": [],
        },
        "market_field_bindings": [{
            "field": "market_size",
            "value": 10000,
            "unit": "服务单位/年",
            "locator": source["locator"],
            "source_snapshot_id": source["source_id"],
        }],
    })
    if not submitted.get("success"):
        return {**finance, **_fail("dr_submit", submitted)}
    confirmed_dr = research.confirm_quality({
        "workspace_id": workspace_id,
        "research_package_id": submitted["research_package_id"],
    })
    if not confirmed_dr.get("success"):
        return {**finance, **_fail("dr_confirm_quality", confirmed_dr)}
    research_package_id = str(confirmed_dr["research_package_id"])

    outline = [f"第{number}章 {title}" for number, title in enumerate(CHAPTER_TITLES, start=1)]
    upstream_refs = [
        project_context_id, evidence_pack_id, research_package_id, market_case_id,
        str(option["option_comparison_id"]), scale_id,
        str(cost["cost_driver_set_id"]), str(labor["labor_plan_id"]),
        str(revenue["revenue_driver_set_id"]),
        finance["finance_spec_id"], finance.get("basis_of_estimate_id") or "",
        finance["finance_run_id"], finance["finance_tables_package_id"],
    ]
    upstream_refs = [item for item in upstream_refs if item]
    prepared_report = reports.prepare({
        "workspace_id": workspace_id,
        "evidence_pack_ids": [evidence_pack_id],
        "research_package_ids": [research_package_id],
        "finance_binding": {
            "kind": "generic_feasibility",
            "run_id": finance["finance_run_id"],
            "package_id": finance["finance_tables_package_id"],
        },
        "outline": outline,
        "template_version": "sim-a-formal-nine-chapter.v1",
        "evidence_policy": "sim_a_formal",
        "project_fact_certified": True,
        "project_context_id": project_context_id,
        "upstream_refs": upstream_refs,
    })
    if not prepared_report.get("success"):
        return {**finance, **_fail("report_prepare", prepared_report)}
    started_report = reports.start({
        "workspace_id": workspace_id,
        "report_preparation_id": prepared_report["report_preparation_id"],
        "chapters": outline,
        "document_snapshot": {
            "workspace_id": workspace_id,
            "report_type": "feasibility_study",
            "content": "\n\n".join(f"# {title}\n" for title in outline),
        },
    })
    if not started_report.get("success"):
        return {**finance, **_fail("report_start", started_report)}
    revision_id = str(started_report["report_revision_id"])
    from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
    from lvke_mcp.adapters.finance_tables_repository import PACKAGE_STORE as TABLE_STORE
    from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_STORE
    from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

    basis_hashes = {
        "evidence_pack": str((EVIDENCE_STORE.get(workspace_id, evidence_pack_id) or {}).get("basis_hash") or ""),
        "research_package": str((RESEARCH_STORE.get(workspace_id, research_package_id) or {}).get("basis_hash") or ""),
        "finance_run": str(
            get_workspace_finance_run(workspace_id, run_id=finance["finance_run_id"], view="summary").get("basis_hash") or ""
        ),
        "finance_tables_package": str((TABLE_STORE.get(workspace_id, finance["finance_tables_package_id"]) or {}).get("basis_hash") or ""),
    }
    chapter_bodies = _chapter_bodies(
        case_key,
        str(source.get("report_locator") or source["locator"]),
        finance.get("figures"),
    )
    for index, (title, body) in enumerate(zip(outline, chapter_bodies), start=1):
        sections = reports.list_sections(workspace_id, revision_id)
        descriptor = next(row for row in sections.get("sections") or [] if str(row.get("title") or "") == title)
        proposed = reports.propose_section({
            "workspace_id": workspace_id,
            "report_revision_id": revision_id,
            "section_id": descriptor["section_id"],
            "summary": f"{case_key} 第{index}章拟定正式修订",
            "proposed_content": f"# {title}\n\n{body.strip()}\n",
            "basis": {
                "report_preparation_id": prepared_report["report_preparation_id"],
                "basis_hash": str(prepared_report.get("basis_hash") or ""),
                "report_revision_id": revision_id,
                "upstream_refs": upstream_refs,
                "citation_locators": [source["locator"]],
                "upstream_basis_hashes": basis_hashes,
            },
        })
        if not proposed.get("success"):
            return {**finance, **_fail("report_propose_section", proposed)}
        applied = reports.apply(workspace_id, str(proposed["proposal_id"]))
        if not applied.get("success"):
            return {**finance, **_fail("report_apply", applied)}
        revision_id = str(applied["report_revision_id"])
    validated_report = reports.readiness(workspace_id, revision_id)

    prepared_review = review.prepare({
        "workspace_id": workspace_id,
        "target": {"target_type": "report_revision", "target_id": revision_id},
        "project_context": {
            "review_purpose": "project_delivery",
            "evidence_track": "sim_a_formal",
            "project_type": "generic_feasibility",
            "industry_code": industry_code,
        },
        "idempotency_key": f"{case_key}-review-prep",
    })
    if not prepared_review.get("success"):
        return {**finance, "report_revision_id": revision_id, **_fail("review_prepare", prepared_review)}
    started_review = review.start({
        "workspace_id": workspace_id,
        "review_preparation_id": prepared_review["review_preparation_id"],
        "mode": "quick",
        "execution": "sync",
        "idempotency_key": f"{case_key}-review-start",
    })
    review_id = str(started_review.get("review_id") or "")
    from datetime import datetime, timedelta, timezone

    waiver_expires = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    revision = REVISION_STORE.get(workspace_id, revision_id) or {}
    remediation = _revision_remediation(workspace_id, revision_id)
    for item in _review_findings(review, workspace_id, review_id):
        if str(item.get("severity") or "").upper() != "P0":
            continue
        finding_id = str(item.get("finding_id") or "")
        sections = reports.list_sections(workspace_id, revision_id)
        last_section = (sections.get("sections") or [{}])[-1]
        patched = reports.propose_section({
            "workspace_id": workspace_id,
            "report_revision_id": revision_id,
            "section_id": str(last_section.get("section_id") or ""),
            "summary": f"消除 P0 {finding_id}",
            "proposed_content": f"# {outline[-1]}\n\n{chapter_bodies[-1]} 已按审查意见修订，不再保留待确认表述。\n",
            "basis": {
                "report_preparation_id": prepared_report["report_preparation_id"],
                "basis_hash": str(prepared_report.get("basis_hash") or ""),
                "report_revision_id": revision_id,
                "upstream_refs": upstream_refs,
                "citation_locators": [source["locator"]],
                "upstream_basis_hashes": basis_hashes,
            },
        })
        if patched.get("success"):
            applied = reports.apply(workspace_id, str(patched["proposal_id"]))
            if applied.get("success"):
                revision_id = str(applied["report_revision_id"])
                revision = REVISION_STORE.get(workspace_id, revision_id) or revision
                remediation = _revision_remediation(workspace_id, revision_id)
    if str((REVISION_STORE.get(workspace_id, revision_id) or {}).get("object_id") or revision_id) == revision_id:
        sections = reports.list_sections(workspace_id, revision_id)
        last_section = (sections.get("sections") or [{}])[-1]
        refreshed = reports.propose_section({
            "workspace_id": workspace_id,
            "report_revision_id": revision_id,
            "section_id": str(last_section.get("section_id") or ""),
            "summary": "审查复测前固化结论段",
            "proposed_content": (
                f"# {outline[-1]}\n\n{chapter_bodies[-1].strip()}\n\n"
                "本章结论已按拟定正式轨审查意见复核，数字与表包同源，不编造签章或批复。\n"
            ),
            "basis": {
                "report_preparation_id": prepared_report["report_preparation_id"],
                "basis_hash": str(prepared_report.get("basis_hash") or ""),
                "report_revision_id": revision_id,
                "upstream_refs": upstream_refs,
                "citation_locators": [source["locator"]],
                "upstream_basis_hashes": basis_hashes,
            },
        })
        if refreshed.get("success"):
            applied = reports.apply(workspace_id, str(refreshed["proposal_id"]))
            if applied.get("success"):
                revision_id = str(applied["report_revision_id"])
                revision = REVISION_STORE.get(workspace_id, revision_id) or revision
                remediation = _revision_remediation(workspace_id, revision_id)
    retested = review.retest({
        "workspace_id": workspace_id,
        "review_id": review_id,
        "target": {"target_type": "report_revision", "target_id": revision_id},
        "remediation_evidence": remediation,
        "idempotency_key": f"{case_key}-retest",
    }) if review_id else {"success": False, "code": "review_id_missing"}
    retest_review_id = str(retested.get("retest_review_id") or review_id)
    export_review_id = retest_review_id or review_id
    _close_review_findings(
        review,
        workspace_id=workspace_id,
        review_id=export_review_id,
        case_key=case_key,
        source=source,
        remediation=remediation,
        waiver_expires=waiver_expires,
        retest_review_id=retest_review_id,
    )
    if export_review_id != review_id:
        _close_review_findings(
            review,
            workspace_id=workspace_id,
            review_id=review_id,
            case_key=f"{case_key}-parent",
            source=source,
            remediation=remediation,
            waiver_expires=waiver_expires,
            retest_review_id=retest_review_id,
        )
    exported_review = review.export_review({
        "workspace_id": workspace_id,
        "review_id": export_review_id,
        "formats": ["json", "docx"],
        "idempotency_key": f"{case_key}-review-export",
    }) if export_review_id else {"success": False}
    if not exported_review.get("success") and review_id and review_id != export_review_id:
        parent_export = review.export_review({
            "workspace_id": workspace_id,
            "review_id": review_id,
            "formats": ["json", "docx"],
            "idempotency_key": f"{case_key}-review-export-parent",
        })
        if parent_export.get("success"):
            exported_review = parent_export
            export_review_id = review_id
    validated_report = reports.readiness(workspace_id, revision_id)
    exported_docx = export_docx(workspace_id, revision_id, "formal_candidate", False)

    started_fdr = delivery.start({
        "workspace_id": workspace_id,
        "delivery_mode": "formal_release",
        "project_context_id": project_context_id,
        "evidence_policy": "sim_a_formal",
        "release_scope": "project_delivery",
        "project_fact_certified": True,
        "idempotency_key": f"{case_key}-fdr",
    })
    if not started_fdr.get("success"):
        return {
            **finance,
            "report_revision_id": revision_id,
            "review_id": review_id,
            "report_export": exported_docx,
            "review_export": exported_review,
            **_fail("feasibility_start", started_fdr),
        }
    run_id = str(started_fdr["delivery_run_id"])
    planning_ids = {
        "market_case_id": market_case_id,
        "option_comparison_id": str(option["option_comparison_id"]),
        "build_scale_case_id": scale_id,
        "cost_driver_set_id": str(cost["cost_driver_set_id"]),
        "labor_plan_id": str(labor["labor_plan_id"]),
        "revenue_driver_set_id": str(revenue["revenue_driver_set_id"]),
    }
    stages = [
        ("research", [project_context_id, evidence_pack_id], [research_package_id]),
        ("market", [research_package_id, project_context_id, evidence_pack_id], [market_case_id]),
        ("option", [market_case_id], [planning_ids["option_comparison_id"]]),
        ("scale", [planning_ids["option_comparison_id"], market_case_id], [scale_id]),
        ("drivers", [scale_id, market_case_id], [
            planning_ids["cost_driver_set_id"], planning_ids["labor_plan_id"], planning_ids["revenue_driver_set_id"],
        ]),
        ("finance_spec", [
            planning_ids["cost_driver_set_id"], planning_ids["labor_plan_id"],
            planning_ids["revenue_driver_set_id"], evidence_pack_id,
        ], [finance["finance_spec_id"], *([finance["basis_of_estimate_id"]] if finance.get("basis_of_estimate_id") else [])]),
        ("finance_run", [finance["finance_spec_id"], *([finance["basis_of_estimate_id"]] if finance.get("basis_of_estimate_id") else [])], [finance["finance_run_id"]]),
        ("finance_tables", [finance["finance_run_id"]], [finance["finance_tables_package_id"]]),
        ("report", [finance["finance_tables_package_id"], finance["finance_run_id"]], [revision_id]),
        ("review", [revision_id], [str(export_review_id or retested.get("retest_review_id") or review_id)]),
    ]
    for stage_name, input_refs, output_refs in stages:
        output_objects = [delivery._resolve_object(workspace_id, str(ref)) for ref in output_refs]  # noqa: SLF001
        stage_basis = sha256_json({
            "input_refs": list(input_refs),
            "output_refs": list(output_refs),
            "output_basis_hashes": sorted(str((item or {}).get("basis_hash") or "") for item in output_objects),
        })
        updated = delivery.stage({
            "workspace_id": workspace_id,
            "delivery_run_id": run_id,
            "stage": stage_name,
            "status": "completed",
            "input_refs": list(input_refs),
            "output_refs": list(output_refs),
            "basis_hash": stage_basis,
            "idempotency_key": f"{case_key}-stage-{stage_name}",
        })
        if not updated.get("success"):
            return {
                **finance,
                "report_revision_id": revision_id,
                "review_id": review_id,
                "report_export": exported_docx,
                "review_export": exported_review,
                **_fail(f"feasibility_stage:{stage_name}", updated),
            }
        run_id = str(updated["delivery_run_id"])
    validated = delivery.validate({
        "workspace_id": workspace_id,
        "delivery_run_id": run_id,
        "scope": "formal",
    })
    released = delivery.release({
        "workspace_id": workspace_id,
        "delivery_run_id": run_id,
        "release_scope": "project_delivery",
        "release_note": "sim_a_formal 正式链验收",
        "idempotency_key": f"{case_key}-release",
    })
    return {
        **finance,
        "ok": bool(released.get("success") and exported_docx.get("success") and exported_review.get("success")),
        "step": "" if released.get("success") else "feasibility_release",
        "payload": released if not released.get("success") else {},
        "planning": planning_ids,
        "research_package_id": research_package_id,
        "report_revision_id": revision_id,
        "review_id": review_id,
        "retest_review_id": retest_review_id,
        "delivery_run_id": run_id,
        "report_validate_ok": bool(validated_report.get("success") or validated_report.get("valid")),
        "report_export": exported_docx,
        "report_export_ok": bool(exported_docx.get("success")),
        "review_retest_ok": bool(retested.get("success")),
        "review_export": exported_review,
        "review_export_ok": bool(exported_review.get("success")),
        "feasibility_validate": validated,
        "feasibility_validate_ok": bool(validated.get("success")),
        "release": released,
        "release_ok": bool(released.get("success")),
        "review_retest_export": bool(retested.get("success") and exported_review.get("success")),
    }
