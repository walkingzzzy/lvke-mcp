"""域深度评估与域内事实叶子/数值锚点。"""

from __future__ import annotations

from typing import Any

from .base import (
    DOMAIN_KEYS,
    _record,
    _rows,
)

from .completeness import (
    _inventory_complete,
    _nonnegative_present,
    _positive,
    _stable_item_id,
    _turnover_component_complete,
    _year_sequence_issues,
)


def assess_domain_depth(domains: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    construction = _rows(domains.get("construction_items"))
    qty_indicator = sum(
        1 for row in construction
        if str(row.get("name") or "").strip()
        and _positive(row.get("quantity"))
        and _positive(row.get("indicator_yuan") or row.get("indicator"))
    )
    checks["construction_items"] = {
        "ok": len(construction) >= 3 and qty_indicator >= 3,
        "detail_count": len(construction),
        "quantity_indicator_pairs": qty_indicator,
        "required": "≥3 项且工程量×指标成对",
    }

    products = _rows(domains.get("products"))
    valid_products = [
        row for row in products
        if str(row.get("name") or "").strip()
        and _positive(row.get("price_per_unit"))
        and _positive(row.get("capacity"))
        and isinstance(row.get("ramp"), list)
        and len(row.get("ramp") or []) >= 2
    ]
    checks["products"] = {
        "ok": bool(valid_products),
        "detail_count": len(valid_products),
        "required": "≥1 个产品，含单价、达产数量和≥2点爬坡",
    }

    costs = _record(domains.get("cost_items"))
    valid_costs = {str(k): v for k, v in costs.items() if str(k).strip() and _positive(v)}
    checks["cost_items"] = {
        "ok": len(valid_costs) >= 3,
        "detail_count": len(valid_costs),
        "required": "≥3 个成本项目",
    }

    staff = _rows(domains.get("staff_detail"))
    valid_staff = [
        row for row in staff
        if str(row.get("category") or row.get("name") or "").strip()
        and _positive(row.get("headcount"))
        and _positive(row.get("avg_wage_yuan"))
    ]
    checks["staff_detail"] = {
        "ok": bool(valid_staff),
        "detail_count": len(valid_staff),
        "required": "≥1 类人员，含定员和人均年工资",
    }

    assets = _rows(domains.get("asset_classes"))
    valid_assets = [
        row for row in assets
        if str(row.get("name") or "").strip()
        and _positive(row.get("original_wan") or row.get("original_value_wan"))
        and _positive(row.get("dep_years") or row.get("depreciation_years"))
    ]
    checks["asset_classes"] = {
        "ok": len(valid_assets) >= 2,
        "detail_count": len(valid_assets),
        "required": "≥2 个资产类别，含原值和折旧年限",
    }

    wc = _record(domains.get("wc_turnover"))
    short_term_loan = wc.get("short_term_loan_wan")
    self_funded = wc.get("self_funded_wan")
    checks["wc_turnover"] = {
        "ok": (
            _turnover_component_complete(wc.get("receivable"))
            and _turnover_component_complete(wc.get("cash"))
            and _turnover_component_complete(wc.get("payable"))
            and _inventory_complete(wc.get("inventory_detail"))
            and _nonnegative_present(short_term_loan)
            and _nonnegative_present(self_funded)
            and float(short_term_loan or 0.0) + float(self_funded or 0.0) > 0
        ),
        "required": "应收/现金/应付 + 原料/燃料/在产品/产成品周转 + 短贷/自筹来源",
    }

    funding = _record(domains.get("funding_plan"))
    schedule = _rows(funding.get("annual_schedule") or funding.get("schedule"))
    funding_year_issues = _year_sequence_issues(
        schedule, label="funding_plan", expected_start=1,
    )
    funding_balance_issues: list[str] = []
    for row in schedule:
        atomic_fields = (
            "construction_investment_wan", "construction_interest_wan",
            "working_capital_wan", "capital_own_wan", "loan_wan", "gov_subsidy_wan",
        )
        if any(row.get(key) in (None, "") for key in atomic_fields):
            funding_balance_issues.append(f"funding_plan year={row.get('year')} 缺原子字段")
            continue
        uses = sum(float(row.get(key) or 0.0) for key in atomic_fields[:3])
        sources = sum(float(row.get(key) or 0.0) for key in atomic_fields[3:])
        if abs(uses - sources) > 0.05:
            funding_balance_issues.append(
                f"funding_plan year={row.get('year')} 用途 {uses} != 来源 {sources}"
            )
    valid_schedule = [
        row for row in schedule
        if (
            _nonnegative_present(row.get("construction_investment_wan"))
            and _nonnegative_present(row.get("construction_interest_wan"))
            and _nonnegative_present(row.get("working_capital_wan"))
        )
        and (
            _positive(row.get("capital_own_wan"))
            or _positive(row.get("loan_wan"))
            or _positive(row.get("gov_subsidy_wan"))
        )
    ]
    checks["funding_plan"] = {
        "ok": bool(valid_schedule) and not funding_year_issues and not funding_balance_issues,
        "detail_count": len(valid_schedule),
        "year_issues": funding_year_issues,
        "balance_issues": funding_balance_issues,
        "required": "≥1 个建设期投资用途+资金来源分年",
    }

    debt = _record(domains.get("debt_schedule"))
    draws = _rows(debt.get("draws") or debt.get("schedule"))
    principal_schedule = _rows(debt.get("principal_schedule"))
    interest_schedule = _rows(debt.get("reference_interest_schedule"))
    debt_year_issues = _year_sequence_issues(draws, label="debt_schedule.draws", expected_start=1)
    debt_year_issues.extend(_year_sequence_issues(principal_schedule, label="debt_schedule.principal_schedule"))
    debt_year_issues.extend(_year_sequence_issues(interest_schedule, label="debt_schedule.reference_interest_schedule"))
    repay_sources = _rows(debt.get("debt_repay_sources") or debt.get("repay_sources"))
    valid_draws = [row for row in draws if _positive(row.get("draw_wan") or row.get("loan_wan"))]
    valid_sources = [
        row for row in repay_sources
        if str(row.get("name") or "").strip()
        and (
            _positive(row.get("share"))
            or _positive(row.get("annual_wan"))
            or any(_positive(value) for value in (row.get("annual_schedule_wan") or row.get("schedule_wan") or []))
        )
    ]
    checks["debt_schedule"] = {
        "ok": (
            bool(valid_draws)
            and len(valid_sources) >= 3
            and _positive(debt.get("loan_rate"))
            and _positive(debt.get("loan_years"))
            and not debt_year_issues
        ),
        "draw_count": len(valid_draws),
        "repay_source_count": len(valid_sources),
        "year_issues": debt_year_issues,
        "required": "提款计划、利率/期限及≥3个偿债资金来源",
    }

    amort = _rows(domains.get("amort_bases"))
    valid_amort = [
        row for row in amort
        if str(row.get("name") or "").strip()
        and _positive(row.get("original_wan") or row.get("original_value_wan"))
        and _positive(row.get("amort_years") or row.get("amortization_years"))
    ]
    amort_names = [str(row.get("name") or "") for row in valid_amort]
    amort_classes_ok = (
        any("土地" in name for name in amort_names)
        and any("其他" in name for name in amort_names)
    )
    checks["amort_bases"] = {
        "ok": len(valid_amort) >= 2 and amort_classes_ok,
        "detail_count": len(valid_amort),
        "required": "土地使用权+其他资产两类摊销基础，含原值和摊销年限",
    }

    distribution = _record(domains.get("distribution_policy"))
    _statutory_ok = (
        distribution.get("statutory_reserve_rate") is not None
        or distribution.get("statutory_reserve_confirmed_zero") is True
    )
    _arbitrary_ok = (
        distribution.get("arbitrary_reserve_confirmed_zero") is True
        or distribution.get("arbitrary_reserve_rate") is not None
    )
    _investor_ok = (
        distribution.get("investor_distribution_confirmed_zero") is True
        or distribution.get("investor_distribution_rate") is not None
        or isinstance(distribution.get("investor_distribution_schedule_wan"), list)
    )
    _retained_ok = bool(str(distribution.get("retained_profit_policy") or "").strip())
    _distribution_missing = [
        label
        for label, passed in (
            ("statutory_reserve_rate|statutory_reserve_confirmed_zero", _statutory_ok),
            ("arbitrary_reserve_rate|arbitrary_reserve_confirmed_zero", _arbitrary_ok),
            (
                "investor_distribution_rate|investor_distribution_confirmed_zero"
                "|investor_distribution_schedule_wan",
                _investor_ok,
            ),
            ("retained_profit_policy", _retained_ok),
        )
        if not passed
    ]
    checks["distribution_policy"] = {
        "ok": not _distribution_missing,
        "missing_fields": _distribution_missing,
        "required": "法定/任意公积金、投资方分配与留存政策（零值须显式确认）",
    }

    behavior = _record(domains.get("cost_behavior"))
    behavior_items = _record(behavior.get("items") or behavior)
    behavior_items.pop("confirmed", None)
    cost_names = set(valid_costs)
    uncovered_costs = sorted(cost_names - set(behavior_items))
    behavior_issues: list[str] = []
    if not cost_names:
        behavior_issues.append("cost_items 为空，无成本项可分类")
    for name in sorted(cost_names - set(uncovered_costs)):
        rule = behavior_items.get(name)
        if isinstance(rule, str):
            kind = rule.lower()
            rule = {"type": kind}
        else:
            rule = _record(rule)
            kind = str(rule.get("type") or rule.get("behavior") or "").lower()
        if kind not in {"fixed", "variable", "mixed"}:
            behavior_issues.append(f"{name}.type 必须为 fixed/variable/mixed（当前 {kind or '空'}）")
            continue
        if kind == "mixed":
            if not _nonnegative_present(rule.get("variable_share")):
                behavior_issues.append(f"{name}.variable_share 缺失或为负")
            if not str(rule.get("driver_fact_path") or "").strip():
                behavior_issues.append(f"{name}.driver_fact_path 缺失")
    behavior_confirmed = behavior.get("confirmed") is True
    if not behavior_confirmed:
        behavior_issues.append("cost_behavior.confirmed 未显式置为 true")
    checks["cost_behavior"] = {
        "ok": not uncovered_costs and not behavior_issues,
        "uncovered_costs": uncovered_costs,
        "missing_fields": behavior_issues,
        "required": "每个成本项确认 fixed/variable/mixed；mixed 含比例和驱动 fact_path",
    }

    tax_policy = _record(domains.get("tax_component_policy"))
    checks["tax_component_policy"] = {
        "ok": bool(
            tax_policy.get("confirmed") is True
            and _nonnegative_present(tax_policy.get("vat_output_rate"))
            and _nonnegative_present(tax_policy.get("vat_input_rate"))
            and _nonnegative_present(tax_policy.get("income_tax_rate"))
            and str(tax_policy.get("surtax_base") or "")
            == "vat_and_consumption_tax_payable"
            and _nonnegative_present(tax_policy.get("urban_maintenance_rate"))
            and _nonnegative_present(tax_policy.get("education_surcharge_rate"))
            and _nonnegative_present(tax_policy.get("local_education_surcharge_rate"))
        ),
        "required": "销项/进项/所得税率及以实际应纳增值税与消费税合计为基数的三项附加税率",
    }

    passed = sum(1 for item in checks.values() if item.get("ok"))
    missing_domains = [key for key in DOMAIN_KEYS if not checks[key].get("ok")]
    return {
        "ok": passed == len(DOMAIN_KEYS),
        "coverage": round(passed / len(DOMAIN_KEYS), 4),
        "passed": passed,
        "required": len(DOMAIN_KEYS),
        "by_domain": checks,
        "missing_domains": missing_domains,
        "missing_detail": [_domain_failure_detail(key, checks[key]) for key in missing_domains],
    }


_DOMAIN_DIAGNOSTIC_KEYS = (
    "missing_fields",
    "detail_count",
    "quantity_indicator_pairs",
    "draw_count",
    "repay_source_count",
    "year_issues",
    "balance_issues",
    "unclassified",
    "uncovered_costs",
)


def _domain_failure_detail(domain: str, check: dict[str, Any]) -> dict[str, Any]:
    """Preserve the per-field diagnostics a domain check already computed.

    深度检查内部已算出 draw_count/year_issues/missing_fields 等定位信息，
    此前在汇总时被压成裸域名。这里原样透出，使调用方能知道缺哪个字段，
    而不是只看到“域未通过”。
    """
    detail: dict[str, Any] = {
        "domain": domain,
        "required": check.get("required"),
    }
    for key in _DOMAIN_DIAGNOSTIC_KEYS:
        value = check.get(key)
        if value not in (None, "", [], {}):
            detail[key] = value
    return detail


def _domain_fact_leaves(domain: str, value: Any) -> list[dict[str, Any]]:
    """Enumerate labeled numeric fact leaves that formal evidence must each bind.

    Each leaf: {"fact_path": str, "value": float, "unit": str|None, "period": Any}.
    fact_path is a stable address (e.g. construction_items[0].quantity).
    """
    leaves: list[dict[str, Any]] = []

    def _add(fact_path: str, raw: Any, *, unit: Any = None, period: Any = None) -> None:
        if raw in (None, "", [], {}):
            return
        if isinstance(raw, (dict, list, tuple)):
            return
        normalized: Any
        if isinstance(raw, bool):
            normalized = raw
        else:
            try:
                normalized = float(raw)
            except (TypeError, ValueError):
                normalized = str(raw).strip()
        leaves.append({
            "fact_path": fact_path,
            "value": normalized,
            "unit": (str(unit) if unit not in (None, "") else None),
            "period": period,
        })

    if domain in {
        "construction_items", "products", "staff_detail", "asset_classes", "amort_bases",
    }:
        for idx, row in enumerate(_rows(value)):
            ident = _stable_item_id(domain, row, idx)
            for key in (
                "amount_wan", "quantity", "indicator_yuan", "indicator",
                "price_per_unit", "capacity", "headcount", "avg_wage_yuan",
                "original_wan", "original_value_wan", "dep_years", "depreciation_years",
                "amort_years", "amortization_years",
            ):
                if row.get(key) not in (None, ""):
                    _add(f"{domain}[item_id={ident}].{key}", row.get(key), unit=row.get("unit"))
        return leaves
    if domain == "cost_items":
        for name, raw in _record(value).items():
            _add(f"cost_items[{name}]", raw)
        return leaves
    if domain == "wc_turnover":
        row = _record(value)
        for key in (
            "receivable", "cash", "payable", "inventory",
            "short_term_loan_wan", "self_funded_wan",
        ):
            component = row.get(key)
            if isinstance(component, dict) and key in {"receivable", "cash", "payable"}:
                base = component.get("annual_base_wan")
                if base is None:
                    base = component.get("base_wan")
                for field, raw, unit in (
                    ("days", component.get("days"), "天"),
                    ("base_wan", base, "万元"),
                    ("base_source", component.get("base_source"), None),
                ):
                    if raw not in (None, ""):
                        _add(f"wc_turnover.{key}.{field}", raw, unit=unit)
            elif component not in (None, ""):
                _add(f"wc_turnover.{key}", component)
        inv = _record(row.get("inventory_detail"))
        for key in ("raw", "fuel", "wip", "finished"):
            component = _record(inv.get(key))
            component_base = component.get("annual_base_wan")
            if component_base is None:
                component_base = component.get("base_wan")
            for field, raw, unit in (
                ("base_wan", component_base, "万元"),
                ("days", component.get("days"), "天"),
            ):
                if raw not in (None, ""):
                    _add(
                        f"wc_turnover.inventory_detail.{key}.{field}",
                        raw,
                        unit=unit,
                    )
            if component.get("base_source") not in (None, ""):
                _add(
                    f"wc_turnover.inventory_detail.{key}.base_source",
                    component.get("base_source"),
                )
        return leaves
    if domain == "funding_plan":
        funding = _record(value)
        for row in _rows(funding.get("annual_schedule") or funding.get("schedule")):
            period = row.get("year") or row.get("period")
            for key in (
                "construction_investment_wan", "construction_interest_wan",
                "capital_own_wan", "loan_wan", "gov_subsidy_wan",
                "working_capital_wan",
            ):
                if row.get(key) not in (None, ""):
                    _add(f"funding_plan[year={period}].{key}", row.get(key), period=period)
        return leaves
    if domain == "debt_schedule":
        debt = _record(value)
        for key in ("loan_rate", "loan_years"):
            if debt.get(key) not in (None, ""):
                _add(f"debt_schedule.{key}", debt.get(key))
        for row in _rows(debt.get("draws") or debt.get("schedule")):
            period = row.get("year") or row.get("period")
            draw = row.get("draw_wan") or row.get("loan_wan")
            if draw not in (None, ""):
                _add(f"debt_schedule.draws[year={period}].draw_wan", draw, period=period)
        for row in _rows(debt.get("principal_schedule")):
            period = row.get("year") or row.get("period")
            _add(
                f"debt_schedule.principal_schedule[year={period}].principal_wan",
                row.get("principal_wan"), period=period,
            )
        for row in _rows(debt.get("reference_interest_schedule")):
            period = row.get("year") or row.get("period")
            _add(
                f"debt_schedule.reference_interest_schedule[year={period}].interest_wan",
                row.get("interest_wan"), period=period,
            )
        for idx, row in enumerate(_rows(debt.get("debt_repay_sources") or debt.get("repay_sources"))):
            ident = _stable_item_id("debt_repay_sources", row, idx)
            for key in ("share", "annual_wan"):
                if row.get(key) not in (None, ""):
                    _add(f"debt_schedule.repay_sources[item_id={ident}].{key}", row.get(key))
            schedule = row.get("annual_schedule_wan") or row.get("schedule_wan")
            if isinstance(schedule, list):
                for period, amount in enumerate(schedule, start=1):
                    _add(
                        f"debt_schedule.repay_sources[item_id={ident}].annual_schedule_wan[year={period}]",
                        amount,
                        unit="万元",
                        period=period,
                    )
        if debt.get("repayment_allocation_method") not in (None, ""):
            _add("debt_schedule.repayment_allocation_method", debt.get("repayment_allocation_method"))
        return leaves
    if domain == "distribution_policy":
        for key, raw in _record(value).items():
            _add(f"distribution_policy.{key}", raw)
        return leaves
    if domain == "cost_behavior":
        behavior = _record(value)
        items = _record(behavior.get("items") or behavior)
        for name, raw_rule in items.items():
            if name == "confirmed":
                continue
            rule = {"type": raw_rule} if isinstance(raw_rule, str) else _record(raw_rule)
            for key in ("type", "variable_share", "driver_fact_path"):
                if rule.get(key) not in (None, ""):
                    _add(f"cost_behavior.items[{name}].{key}", rule.get(key))
        _add("cost_behavior.confirmed", behavior.get("confirmed"))
        return leaves
    if domain == "tax_component_policy":
        for key, raw in _record(value).items():
            _add(f"tax_component_policy.{key}", raw)
        return leaves
    return leaves


def _domain_numeric_anchors(domain: str, value: Any) -> list[float]:
    """Backward-compatible numeric anchor list (values only)."""
    return [
        float(leaf["value"])
        for leaf in _domain_fact_leaves(domain, value)
        if isinstance(leaf.get("value"), (int, float)) and not isinstance(leaf.get("value"), bool)
    ]
