"""通用敏感性、税务口径与财务来源绑定检查组。"""

from __future__ import annotations

from typing import Any


from .base import (
    _different,
    _finding,
    _minimum_capital_pct,
    _number,
    _tolerance,
)


def _generic_sensitivity_checks(
    run: dict[str, Any],
    target_id: str,
    source_rules: dict[str, dict[str, Any]],
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], set[str], dict[str, Any]]:
    sensitivity = run.get("sensitivity") or {}
    indicators = run.get("indicators") or {}
    metrics: dict[str, Any] = {"factors_checked": 0, "scenario_method": None}
    if not isinstance(sensitivity, dict) or not sensitivity:
        return [], ["rule_input_unavailable:FIN.SENSITIVITY.RERUN"], set(), metrics
    findings: list[dict[str, Any]] = []
    method = str(sensitivity.get("method") or "")
    if method != "full_model_rerun":
        findings.append(_finding(
            "FIN.SENSITIVITY.RERUN",
            "P0",
            "敏感性分析未证明执行完整模型重算",
            category="sensitivity",
            target_id=target_id,
            location={"field": "sensitivity.method"},
            standard_basis=standard_basis,
            source_rules=source_rules,
            expected="full_model_rerun",
            actual=method or None,
        ))
    base_irr = _number(indicators.get("project_irr_pct"))
    base_npv = _number(indicators.get("npv_wan"))
    for factor, rows in sensitivity.items():
        if factor in {"method", "deltas"} or not isinstance(rows, list):
            continue
        base_rows = [
            row for row in rows
            if isinstance(row, dict) and abs(_number(row.get("delta")) or 0.0) <= 1e-12
        ]
        metrics["factors_checked"] += 1
        if len(base_rows) != 1:
            findings.append(_finding(
                "FIN.SENSITIVITY.RERUN",
                "P0",
                "敏感性因子缺少唯一基准情景",
                category="sensitivity",
                target_id=target_id,
                location={"field": f"sensitivity.{factor}"},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected="exactly one delta=0 row",
                actual=len(base_rows),
            ))
            continue
        base = base_rows[0]
        if (
            (base_irr is not None and _different(_number(base.get("irr_pct")), base_irr))
            or (base_npv is not None and _different(_number(base.get("npv_wan")), base_npv))
        ):
            findings.append(_finding(
                "FIN.SENSITIVITY.RERUN",
                "P0",
                "敏感性基准情景与主模型指标不一致",
                category="sensitivity",
                target_id=target_id,
                location={"field": f"sensitivity.{factor}.base"},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected={"irr_pct": base_irr, "npv_wan": base_npv},
                actual=base,
            ))
        signatures = {
            (_number(row.get("irr_pct")), _number(row.get("npv_wan")))
            for row in rows if isinstance(row, dict)
        }
        if len(rows) > 1 and len(signatures) <= 1:
            findings.append(_finding(
                "FIN.SENSITIVITY.RERUN",
                "P0",
                "敏感性情景仅替换展示标签，财务结果未变化",
                category="sensitivity",
                target_id=target_id,
                location={"field": f"sensitivity.{factor}"},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected="不同输入情景产生不同 IRR/NPV",
                actual=list(signatures),
            ))
    scenarios = run.get("scenarios") or {}
    if isinstance(scenarios, dict):
        metrics["scenario_method"] = scenarios.get("method")
        if scenarios and scenarios.get("method") != "full_model_rerun":
            findings.append(_finding(
                "FIN.SENSITIVITY.RERUN",
                "P0",
                "情景分析未证明执行完整模型重算",
                category="sensitivity",
                target_id=target_id,
                location={"field": "scenarios.method"},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected="full_model_rerun",
                actual=scenarios.get("method"),
            ))
    return findings, [], {"FIN.SENSITIVITY.RERUN"}, metrics


def _generic_tax_and_source_checks(
    run: dict[str, Any],
    target_id: str,
    source_rules: dict[str, dict[str, Any]],
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], set[str], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    executed: set[str] = set()
    metrics: dict[str, Any] = {}
    spec = run.get("spec") or {}
    raw = run.get("raw") or {}
    indicators = run.get("indicators") or {}
    tax = spec.get("tax") or {}

    income_tax_rate = _number(tax.get("income_tax_rate"))
    if income_tax_rate is None:
        profit = _number(indicators.get("profit_before"))
        income_tax = _number(indicators.get("income_tax"))
        if profit not in (None, 0) and income_tax is not None:
            income_tax_rate = income_tax / profit
    metrics["income_tax_rate"] = income_tax_rate
    if income_tax_rate is None:
        incomplete.append("rule_input_unavailable:AT-CIT-001")
    else:
        executed.add("AT-CIT-001")
        evidence = (
            tax.get("incentive_evidence")
            or spec.get("tax_incentive_evidence")
            or tax.get("legal_basis")
            or tax.get("evidence_ids")
        )
        # Two-decimal tax/profit rows can back-solve to 24.9995% even when the
        # configured statutory rate is exactly 25%. Treat sub-basis-point drift
        # as display rounding, not an unsupported tax incentive.
        if abs(income_tax_rate - 0.25) > 1e-4 and not evidence:
            findings.append(_finding(
                "AT-CIT-001",
                "P0",
                "企业所得税率偏离 25%，但未绑定优惠资格和法定依据",
                category="tax",
                target_id=target_id,
                location={"field": "spec.tax.income_tax_rate"},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected={"default_rate": 0.25, "or": "verified incentive evidence"},
                actual=income_tax_rate,
            ))

    tax_rows_checked = 0
    if income_tax_rate is not None:
        for index, row in enumerate(((run.get("annual") or {}).get("profit_distribution") or [])):
            if not isinstance(row, dict):
                continue
            taxable_profit = _number(row.get("total_profit"))
            actual_tax = _number(row.get("income_tax"))
            if taxable_profit is None or actual_tax is None:
                continue
            tax_rows_checked += 1
            expected_tax = max(taxable_profit, 0.0) * income_tax_rate
            if _different(actual_tax, expected_tax):
                findings.append(_finding(
                    "FIN.TAX.RECALC",
                    "P0",
                    "逐期企业所得税与应纳税所得额复算不一致",
                    category="tax",
                    target_id=target_id,
                    location={
                        "table_code": "profit-distribution",
                        "period": row.get("year", index + 1),
                        "field": "income_tax",
                    },
                    standard_basis=standard_basis,
                    source_rules=source_rules,
                    expected=round(expected_tax, 6),
                    actual=actual_tax,
                    difference=round(actual_tax - expected_tax, 6),
                    tolerance=_tolerance(actual_tax, expected_tax),
                ))

    vat_rate = _number(tax.get("vat_rate", indicators.get("vat_rate")))
    metrics["vat_rate"] = vat_rate
    vat_basis = (
        tax.get("vat_transaction_type")
        or tax.get("vat_rate_basis")
        or tax.get("taxable_transaction_type")
    )
    if vat_rate is None:
        incomplete.append("rule_input_unavailable:AT-VAT-001")
    else:
        if not any(abs(vat_rate - allowed) <= 1e-9 for allowed in (0.0, 0.06, 0.09, 0.13)):
            executed.add("AT-VAT-001")
            findings.append(_finding(
                "AT-VAT-001",
                "P0",
                "增值税税率不在现行法定档次内",
                category="tax",
                target_id=target_id,
                location={"field": "spec.tax.vat_rate"},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected=[0.0, 0.06, 0.09, 0.13],
                actual=vat_rate,
            ))
        elif not vat_basis:
            incomplete.append("rule_input_unavailable:AT-VAT-001")
        else:
            executed.add("AT-VAT-001")
        revenue = _number(indicators.get("revenue"))
        vat_output = _number(indicators.get("vat_output"))
        if revenue is not None and vat_output is not None:
            tax_rows_checked += 1
            expected_vat = revenue * vat_rate
            if _different(vat_output, expected_vat):
                findings.append(_finding(
                    "FIN.TAX.RECALC",
                    "P0",
                    "增值税销项税额与不含税收入、税率复算不一致",
                    category="tax",
                    target_id=target_id,
                    location={"field": "indicators.vat_output"},
                    standard_basis=standard_basis,
                    source_rules=source_rules,
                    expected=round(expected_vat, 6),
                    actual=vat_output,
                    difference=round(vat_output - expected_vat, 6),
                    tolerance=_tolerance(vat_output, expected_vat),
                ))

    metrics["tax_rows_checked"] = tax_rows_checked
    if tax_rows_checked:
        executed.add("FIN.TAX.RECALC")
    else:
        incomplete.append("rule_input_unavailable:FIN.TAX.RECALC")

    if "AT-UCT-001" in source_rules:
        surcharge_on_vat = raw.get("surtax_on_vat")
        raw_component_policy = raw.get("surtax_component_policy")
        component_policy = raw_component_policy if isinstance(raw_component_policy, dict) else {}
        urban_rate = _number(component_policy.get("urban_maintenance_rate"))
        education_rate = _number(component_policy.get("education_surcharge_rate"))
        local_education_rate = _number(component_policy.get("local_education_surcharge_rate"))
        tax_base = component_policy.get("base")
        if surcharge_on_vat is None or not isinstance(raw_component_policy, dict):
            incomplete.append("rule_input_unavailable:AT-UCT-001")
        else:
            executed.add("AT-UCT-001")
            if (
                surcharge_on_vat is not True
                or tax_base != "vat_and_consumption_tax_payable"
                or urban_rate not in {0.01, 0.05, 0.07}
            ):
                findings.append(_finding(
                    "AT-UCT-001",
                    "P1",
                    "城建税未按实际缴纳增值税/消费税及地区税率计算",
                    category="tax",
                    target_id=target_id,
                    location={"field": "raw.surtax_on_vat"},
                    standard_basis=standard_basis,
                    source_rules=source_rules,
                    expected={"tax_base": "vat_and_consumption_tax_payable", "urban_maintenance_rate": [0.01, 0.05, 0.07]},
                    actual={"surtax_on_vat": surcharge_on_vat, "tax_base": tax_base, "urban_maintenance_rate": urban_rate},
                ))
            if education_rate != 0.03:
                findings.append(_finding(
                    "FIN.TAX.EDUCATION_SURCHARGE", "P1",
                    "教育费附加未按3%独立建模",
                    category="tax", target_id=target_id,
                    location={"field": "raw.surtax_component_policy.education_surcharge_rate"},
                    standard_basis=standard_basis, source_rules=source_rules,
                    expected=0.03, actual=education_rate,
                ))
            if local_education_rate != 0.02:
                findings.append(_finding(
                    "FIN.TAX.LOCAL_EDUCATION_SURCHARGE", "P1",
                    "地方教育附加未按2%独立建模",
                    category="tax", target_id=target_id,
                    location={"field": "raw.surtax_component_policy.local_education_surcharge_rate"},
                    standard_basis=standard_basis, source_rules=source_rules,
                    expected=0.02, actual=local_education_rate,
                ))

    if "AT-LVAT-001" in source_rules:
        land_tax = tax.get("land_value_added_tax") or {}
        applicable = land_tax.get("applicable")
        if applicable is False:
            executed.add("AT-LVAT-001")
        elif applicable is not True:
            incomplete.append("rule_input_unavailable:AT-LVAT-001")
        else:
            added = _number(land_tax.get("value_added_wan"))
            deductions = _number(land_tax.get("deductions_wan"))
            actual_tax = _number(land_tax.get("tax_wan"))
            if None in {added, deductions, actual_tax} or not deductions:
                incomplete.append("rule_input_unavailable:AT-LVAT-001")
            else:
                ratio = float(added) / float(deductions)
                if ratio <= 0.5:
                    expected_tax = float(added) * 0.30
                elif ratio <= 1.0:
                    expected_tax = float(added) * 0.40 - float(deductions) * 0.05
                elif ratio <= 2.0:
                    expected_tax = float(added) * 0.50 - float(deductions) * 0.15
                else:
                    expected_tax = float(added) * 0.60 - float(deductions) * 0.35
                executed.add("AT-LVAT-001")
                if _different(actual_tax, expected_tax):
                    findings.append(_finding(
                        "AT-LVAT-001",
                        "P0",
                        "土地增值税四级超率累进复算不一致",
                        category="tax",
                        target_id=target_id,
                        location={"field": "spec.tax.land_value_added_tax.tax_wan"},
                        standard_basis=standard_basis,
                        source_rules=source_rules,
                        expected=round(expected_tax, 6),
                        actual=actual_tax,
                        difference=round(float(actual_tax) - expected_tax, 6),
                        tolerance=_tolerance(actual_tax, expected_tax),
                    ))

    investment = run.get("investment") or {}
    capitalized = _number((raw.get("asset_map") or {}).get("capitalized_interest"))
    interest = _number(investment.get("interest"))
    idc_rows = ((run.get("annual") or {}).get("interest_during_construction") or [])
    build_years = int(_number((run.get("params") or {}).get("build_years")) or 0)
    if interest is None and capitalized is None:
        incomplete.append("rule_input_unavailable:AT-BC-001")
    else:
        executed.add("AT-BC-001")
        idc_total = sum(_number(row.get("interest")) or 0.0 for row in idc_rows if isinstance(row, dict))
        invalid_periods = [
            row.get("period") for row in idc_rows
            if isinstance(row, dict) and build_years and int(_number(row.get("period")) or 0) > build_years
        ]
        expected_interest = interest if interest is not None else capitalized
        if invalid_periods or (
            idc_rows and expected_interest is not None and _different(idc_total, expected_interest)
        ):
            findings.append(_finding(
                "AT-BC-001",
                "P1",
                "借款费用资本化期间或金额与建设期利息不一致",
                category="borrowing_cost",
                target_id=target_id,
                location={"table_code": "interest-during-construction"},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected={"construction_interest": expected_interest, "latest_period": build_years},
                actual={"schedule_total": idc_total, "post_construction_periods": invalid_periods},
            ))

    return findings, incomplete, executed, metrics


def _generic_finance_source_checks(
    run: dict[str, Any],
    target_id: str,
    source_rules: dict[str, dict[str, Any]],
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], set[str], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    executed: set[str] = set()
    metrics: dict[str, Any] = {}
    indicators = run.get("indicators") or {}
    operating = run.get("operating") or {}

    if "FR-FIN-001" in source_rules:
        required = {
            "cashflows": operating.get("cashflows"),
            "project_irr_pct": indicators.get("project_irr_pct"),
            "npv_wan": indicators.get("npv_wan"),
            "revenue": indicators.get("revenue", operating.get("revenue")),
            "op_cost": indicators.get("op_cost", operating.get("op_cost")),
        }
        missing = [key for key, value in required.items() if value in (None, "", [])]
        executed.add("FR-FIN-001")
        if missing:
            findings.append(_finding(
                "FR-FIN-001",
                "P0",
                "经营性项目盈利指标缺少可复算现金流、收入或成本基础",
                category="financial_recalculation",
                target_id=target_id,
                location={"fields": missing},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected=sorted(required),
                actual={"missing": missing},
            ))

    if "FR-FIN-003" in source_rules:
        plan = ((run.get("annual") or {}).get("financial_plan") or [])
        if not plan:
            incomplete.append("rule_input_unavailable:FR-FIN-003")
        else:
            executed.add("FR-FIN-003")
            negative = [
                {
                    "period": row.get("period"),
                    "net_cashflow": row.get("net_cashflow"),
                    "cumulative": row.get("cumulative"),
                }
                for row in plan if isinstance(row, dict)
                and (bool(row.get("gap")) or (_number(row.get("cumulative")) or 0.0) < 0)
            ]
            continuation = (
                (run.get("spec") or {}).get("cash_continuation_plan")
                or (run.get("raw") or {}).get("cash_continuation_plan")
            )
            if negative and not continuation:
                findings.append(_finding(
                    "FR-FIN-003",
                    "P1",
                    "财务计划出现资金缺口或累计盈余为负，且未披露接续方案",
                    category="financial_sustainability",
                    target_id=target_id,
                    location={"table_code": "financial-plan"},
                    standard_basis=standard_basis,
                    source_rules=source_rules,
                    expected="non-negative cash balance or documented continuation plan",
                    actual=negative,
                ))

    if "FR-FIN-004" in source_rules:
        applicability = run.get("benchmark_applicability") or {}
        if not applicability:
            incomplete.append("rule_input_unavailable:FR-FIN-004")
        else:
            executed.add("FR-FIN-004")
            nature = str(applicability.get("nature") or run.get("invest_type") or "")
            hard_gate = applicability.get("benchmark_is_mandatory")
            if nature in {"enterprise", "企业投资"} and hard_gate is not False:
                findings.append(_finding(
                    "FR-FIN-004",
                    "P1",
                    "企业投资项目错误地将行业基准收益率作为强制达标门槛",
                    category="benchmark_applicability",
                    target_id=target_id,
                    location={"field": "benchmark_applicability"},
                    standard_basis=standard_basis,
                    source_rules=source_rules,
                    expected={"benchmark_is_mandatory": False},
                    actual=applicability,
                ))

    if "FR-FIN-005" in source_rules:
        funding = run.get("funding") or {}
        capital_pct = _number(funding.get("capital_pct"))
        minimum_pct, minimum_source = _minimum_capital_pct(run)
        metrics["minimum_capital_pct"] = minimum_pct
        metrics["minimum_capital_pct_source"] = minimum_source
        if capital_pct is None or minimum_pct is None:
            incomplete.append("rule_input_unavailable:FR-FIN-005")
        else:
            executed.add("FR-FIN-005")
            if capital_pct + 1e-9 < minimum_pct:
                findings.append(_finding(
                    "FR-FIN-005",
                    "P0",
                    "项目资本金比例低于已确认适用下限",
                    category="funding",
                    target_id=target_id,
                    location={"field": "funding.capital_pct"},
                    standard_basis=standard_basis,
                    source_rules=source_rules,
                    expected=minimum_pct,
                    actual=capital_pct,
                    difference=round(capital_pct - minimum_pct, 6),
                    tolerance=0.0,
                ))
            arrival_evidence = (
                funding.get("capital_arrival_evidence")
                or (run.get("spec") or {}).get("capital_arrival_evidence")
                or (run.get("raw") or {}).get("funding_annual_schedule")
            )
            if not arrival_evidence:
                findings.append(_finding(
                    "FR-FIN-005",
                    "P0",
                    "资本金比例虽满足测算下限，但缺少实际到位计划或证据",
                    category="funding",
                    target_id=target_id,
                    location={"field": "funding.capital_arrival_evidence"},
                    standard_basis=standard_basis,
                    source_rules=source_rules,
                    expected="dated capital contribution schedule with evidence",
                    actual=None,
                ))

    return findings, incomplete, executed, metrics
