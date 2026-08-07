"""资产收购专用检查组。"""

from __future__ import annotations

from typing import Any


from .base import (
    _different,
    _finding,
    _number,
    _tolerance,
)


def _acquisition_checks(
    run: dict[str, Any],
    target_id: str,
    source_rules: dict[str, dict[str, Any]],
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], set[str], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    executed: set[str] = set()
    metrics: dict[str, Any] = {"acquisition": True}
    spec = run.get("spec") or {}
    transaction = spec.get("transaction") or run.get("transaction") or {}
    result = run.get("result") or run

    purchase_price = _number(result.get("purchase_price_wan"))
    transaction_tax = _number(result.get("transaction_tax_wan"))
    total_cost = _number(result.get("total_acquisition_cost_wan"))
    if None in {purchase_price, transaction_tax, total_cost}:
        incomplete.append("rule_input_unavailable:FIN.INVESTMENT.BALANCE")
    else:
        executed.add("FIN.INVESTMENT.BALANCE")
        expected_total = float(purchase_price) + float(transaction_tax)
        if _different(total_cost, expected_total):
            findings.append(_finding(
                "FIN.INVESTMENT.BALANCE", "P0", "收购总成本与收购价、交易税费不平",
                category="financial_recalculation", target_id=target_id,
                location={"field": "result.total_acquisition_cost_wan"},
                standard_basis=standard_basis, source_rules=source_rules,
                expected=round(expected_total, 6), actual=total_cost,
                difference=round(float(total_cost) - expected_total, 6),
                tolerance=_tolerance(total_cost, expected_total),
            ))

    financing_ratio = _number(transaction.get("financing_ratio"))
    if total_cost is None or financing_ratio is None:
        incomplete.append("rule_input_unavailable:FIN.FUNDING.BALANCE")
    else:
        executed.add("FIN.FUNDING.BALANCE")
        debt_amount = total_cost * financing_ratio
        equity_amount = total_cost - debt_amount
        if _different(debt_amount + equity_amount, total_cost):
            findings.append(_finding(
                "FIN.FUNDING.BALANCE", "P0", "收购债务和资本金与总收购成本不平",
                category="financial_recalculation", target_id=target_id,
                location={"field": "spec.transaction.financing_ratio"},
                standard_basis=standard_basis, source_rules=source_rules,
                expected=total_cost, actual=debt_amount + equity_amount,
            ))

    project_cashflows = result.get("project_cashflows_wan") or []
    indicators = result.get("indicators") or {}
    if "FR-FIN-001" in source_rules:
        missing = [
            key for key, value in {
                "project_cashflows_wan": project_cashflows,
                "project_irr_pct": indicators.get("project_irr_pct"),
                "npv_wan": indicators.get("npv_wan"),
                "owner_revenue_wan": result.get("owner_revenue_wan"),
                "owner_operating_cost_wan": result.get("owner_operating_cost_wan"),
            }.items() if value in (None, "", [])
        ]
        executed.add("FR-FIN-001")
        if missing:
            findings.append(_finding(
                "FR-FIN-001", "P0", "收购盈利指标缺少可复算收入、成本或现金流基础",
                category="financial_recalculation", target_id=target_id,
                location={"fields": missing}, standard_basis=standard_basis,
                source_rules=source_rules, expected="complete profitability inputs",
                actual={"missing": missing},
            ))
    if project_cashflows and all(_number(value) is not None for value in project_cashflows):
        try:
            from lvke_mcp.domains.finance.calculations import irr, npv

            numeric_cashflows = [float(value) for value in project_cashflows]
            independent_irr = irr(numeric_cashflows) * 100.0
            discount_rate = _number(run.get("discount_rate"))
            reported_irr = _number(indicators.get("project_irr_pct"))
            reported_npv = _number(indicators.get("npv_wan"))
            if reported_irr is not None and _different(reported_irr, independent_irr):
                findings.append(_finding(
                    "FR-FIN-001", "P0", "收购项目 IRR 与独立现金流复算不一致",
                    category="financial_recalculation", target_id=target_id,
                    location={"field": "result.indicators.project_irr_pct"},
                    standard_basis=standard_basis, source_rules=source_rules,
                    expected=round(independent_irr, 6), actual=reported_irr,
                    difference=round(reported_irr - independent_irr, 6), tolerance=0.01,
                ))
            if discount_rate is not None and reported_npv is not None:
                independent_npv = npv(numeric_cashflows, discount_rate)
                if _different(reported_npv, independent_npv):
                    findings.append(_finding(
                        "FR-FIN-001", "P0", "收购项目 NPV 与独立现金流复算不一致",
                        category="financial_recalculation", target_id=target_id,
                        location={"field": "result.indicators.npv_wan"},
                        standard_basis=standard_basis, source_rules=source_rules,
                        expected=round(independent_npv, 6), actual=reported_npv,
                        difference=round(reported_npv - independent_npv, 6),
                        tolerance=_tolerance(reported_npv, independent_npv),
                    ))
        except ValueError:
            incomplete.append("independent_acquisition_irr_unavailable")

    depreciation = result.get("depreciation_schedule") or {}
    classes = depreciation.get("classes") or []
    if classes:
        executed.update({"AT-FA-001", "FIN.DEPRECIATION.RECALC"})
        aggregate = [0.0] * len(depreciation.get("annual_depreciation_wan") or [])
        for index, asset_class in enumerate(classes):
            basis = _number(asset_class.get("basis_wan"))
            life = int(_number(asset_class.get("depreciation_years")) or 0)
            residual = _number(asset_class.get("residual_rate"))
            schedule = asset_class.get("annual_depreciation_wan") or []
            scope_id = str(asset_class.get("scope_id") or "")
            if "land" in scope_id.lower() or "土地" in scope_id:
                if any(abs(_number(value) or 0.0) > 1e-9 for value in schedule):
                    findings.append(_finding(
                        "AT-FA-001", "P0", "土地资产被计提折旧", category="depreciation",
                        target_id=target_id, location={"asset_class": scope_id},
                        standard_basis=standard_basis, source_rules=source_rules,
                        expected=0.0, actual=schedule,
                    ))
                continue
            if basis is None or not life or residual is None or len(schedule) < life:
                findings.append(_finding(
                    "AT-FA-001", "P0", "收购资产折旧类别缺少基数、寿命、残值或完整期间",
                    category="depreciation", target_id=target_id,
                    location={"asset_class": scope_id or index}, standard_basis=standard_basis,
                    source_rules=source_rules, expected="complete class depreciation inputs", actual=asset_class,
                ))
                continue
            expected = basis * (1.0 - residual) / life
            for period, value in enumerate(schedule):
                actual = _number(value)
                if period < len(aggregate) and actual is not None:
                    aggregate[period] += actual
                if period < life and _different(actual, expected):
                    findings.append(_finding(
                        "AT-FA-001", "P0", "收购资产分类折旧复算不一致", category="depreciation",
                        target_id=target_id, location={"asset_class": scope_id or index, "period": period + 1},
                        standard_basis=standard_basis, source_rules=source_rules,
                        expected=round(expected, 6), actual=actual,
                    ))
        reported = depreciation.get("annual_depreciation_wan") or []
        if len(aggregate) == len(reported) and any(
            _different(_number(actual), expected) for actual, expected in zip(reported, aggregate)
        ):
            findings.append(_finding(
                "AT-FA-001", "P0", "分类折旧合计与总折旧计划不一致", category="depreciation",
                target_id=target_id, location={"field": "depreciation_schedule.annual_depreciation_wan"},
                standard_basis=standard_basis, source_rules=source_rules,
                expected=aggregate, actual=reported,
            ))
    elif transaction.get("asset_scope"):
        executed.update({"AT-FA-001", "FIN.DEPRECIATION.RECALC"})
        depreciable = [
            row for row in transaction.get("asset_scope") or []
            if isinstance(row, dict) and row.get("included") is not False
            and str(row.get("type") or "").lower() not in {"land", "土地"}
        ]
        if depreciable:
            findings.append(_finding(
                "AT-FA-001", "P0", "收购资产存在可折旧项目但未生成分类折旧计划",
                category="depreciation", target_id=target_id,
                location={"field": "depreciation_schedule.classes"}, standard_basis=standard_basis,
                source_rules=source_rules, expected="class schedules", actual=[],
            ))
    else:
        incomplete.extend([
            "rule_input_unavailable:AT-FA-001",
            "rule_input_unavailable:FIN.DEPRECIATION.RECALC",
        ])

    debt_value = result.get("debt_schedule") or []
    if isinstance(debt_value, dict):
        monthly_debt = [
            row for row in (debt_value.get("monthly") or [])
            if isinstance(row, dict)
        ]
        debt = []
        for offset in range(0, len(monthly_debt), 12):
            period = monthly_debt[offset:offset + 12]
            if not period:
                continue
            debt.append({
                "year": offset // 12 + 1,
                "opening_principal_wan": period[0].get("opening_principal_wan"),
                "interest_wan": sum(_number(row.get("interest_wan")) or 0.0 for row in period),
                "principal_wan": sum(_number(row.get("principal_wan")) or 0.0 for row in period),
                "debt_service_wan": sum(_number(row.get("debt_service_wan")) or 0.0 for row in period),
                "closing_principal_wan": period[-1].get("closing_principal_wan"),
            })
    else:
        debt = [row for row in debt_value if isinstance(row, dict)]
    purchase_total = _number(result.get("total_acquisition_cost_wan"))
    expected_opening = (
        purchase_total * financing_ratio
        if purchase_total is not None and financing_ratio is not None else None
    )
    if financing_ratio is not None and financing_ratio <= 1e-12:
        executed.update({
            "FIN.DEBT.ROLLFORWARD", "FIN.DEBT.COVERAGE", "FR-FIN-002",
            "FR-LOAN-002", "FR-LOAN-004",
        })
    elif debt:
        executed.update({
            "FIN.DEBT.ROLLFORWARD", "FIN.DEBT.COVERAGE", "FR-FIN-002",
            "FR-LOAN-002", "FR-LOAN-004",
        })
        prior_end = expected_opening
        dscrs: list[float] = []
        icrs: list[float] = []
        revenues = result.get("owner_revenue_wan") or []
        operating_costs = result.get("owner_operating_cost_wan") or []
        depreciation_values = (
            (result.get("depreciation_schedule") or {}).get("annual_depreciation_wan") or []
        )
        tax_spec = spec.get("tax") or {}
        income_tax_rate = _number(tax_spec.get("income_tax_rate")) or 0.0
        holiday_years = int(_number(tax_spec.get("tax_holiday_years")) or 0)
        half_years = int(_number(tax_spec.get("tax_half_years")) or 0)
        hotel_years = ((result.get("hotel_operation") or {}).get("years") or [])
        for index, row in enumerate(debt):
            opening = _number(row.get("opening_principal_wan"))
            principal = _number(row.get("principal_wan"))
            interest = _number(row.get("interest_wan"))
            service = _number(row.get("debt_service_wan"))
            if None in {opening, principal, interest, service}:
                incomplete.append(f"acquisition_debt_row_inputs_missing:{index}")
                continue
            expected_service = float(principal) + float(interest)
            if _different(service, expected_service) or (
                prior_end is not None and _different(opening, prior_end)
            ):
                findings.append(_finding(
                    "FIN.DEBT.ROLLFORWARD", "P0", "收购借款余额或还本付息复算不一致",
                    category="debt_service", target_id=target_id,
                    location={"table_code": "debt-service", "period": row.get("year", index + 1)},
                    standard_basis=standard_basis, source_rules=source_rules,
                    expected={"opening": prior_end, "debt_service": round(expected_service, 6)},
                    actual=row,
                ))
            prior_end = float(opening) - float(principal)
            revenue = _number(revenues[index]) if index < len(revenues) else None
            operating_cost = (
                _number(operating_costs[index]) if index < len(operating_costs) else None
            )
            depreciation_value = (
                _number(depreciation_values[index])
                if index < len(depreciation_values) else None
            )
            if None not in {revenue, operating_cost, depreciation_value}:
                ebitda = float(revenue) - float(operating_cost)
                if index < holiday_years:
                    effective_rate = 0.0
                elif index < holiday_years + half_years:
                    effective_rate = income_tax_rate / 2.0
                else:
                    effective_rate = income_tax_rate
                taxable = max(ebitda - float(depreciation_value) - float(interest), 0.0)
                maintenance = _number(
                    (hotel_years[index] if index < len(hotel_years) else {}).get(
                        "maintenance_capex_wan"
                    )
                ) or 0.0
                recurring_cfads = ebitda - taxable * effective_rate - maintenance
                if service > _tolerance(service):
                    dscrs.append(recurring_cfads / service)
                if interest > _tolerance(interest):
                    icrs.append(ebitda / interest)
            else:
                incomplete.append(f"acquisition_coverage_inputs_missing:{index}")
        reported_dscr = _number((result.get("indicators") or {}).get("minimum_dscr"))
        reported_icr = _number((result.get("indicators") or {}).get("minimum_icr"))
        expected_dscr = min(dscrs) if dscrs else None
        expected_icr = min(icrs) if icrs else None
        if _different(reported_dscr, expected_dscr) or _different(reported_icr, expected_icr):
            findings.append(_finding(
                "FIN.DEBT.COVERAGE", "P0", "收购项目最低 DSCR/ICR 与逐期现金流复算不一致",
                category="debt_service", target_id=target_id,
                location={"field": "result.indicators.minimum_dscr"}, standard_basis=standard_basis,
                source_rules=source_rules, expected={"minimum_dscr": expected_dscr, "minimum_icr": expected_icr},
                actual={"minimum_dscr": reported_dscr, "minimum_icr": reported_icr},
            ))
        if (expected_dscr is not None and expected_dscr < 1.0) or (
            expected_icr is not None and expected_icr < 1.0
        ):
            findings.append(_finding(
                "FR-LOAN-002", "P0", "收购项目偿债覆盖率低于 1", category="debt_service",
                target_id=target_id, location={"field": "result.indicators"},
                standard_basis=standard_basis, source_rules=source_rules,
                expected={"minimum_dscr": 1.0, "minimum_icr": 1.0},
                actual={"minimum_dscr": expected_dscr, "minimum_icr": expected_icr},
            ))
        tenor = int(_number(transaction.get("tenor")) or 0)
        active = sum(
            1 for row in debt
            if (_number(row.get("debt_service_wan")) or 0.0) > _tolerance(_number(row.get("debt_service_wan")))
        )
        if tenor and active != tenor:
            findings.append(_finding(
                "FR-LOAN-004", "P0", "收购借款期限与还款计划不匹配", category="debt_service",
                target_id=target_id, location={"field": "spec.transaction.tenor"},
                standard_basis=standard_basis, source_rules=source_rules,
                expected=tenor, actual=active,
            ))
    elif financing_ratio is None:
        incomplete.extend([
            "rule_input_unavailable:FIN.DEBT.ROLLFORWARD",
            "rule_input_unavailable:FIN.DEBT.COVERAGE",
            "rule_input_unavailable:FR-FIN-002",
            "rule_input_unavailable:FR-LOAN-002",
            "rule_input_unavailable:FR-LOAN-004",
        ])
    else:
        findings.append(_finding(
            "FR-FIN-002", "P0", "收购交易包含债务融资但未生成还本付息计划",
            category="debt_service", target_id=target_id,
            location={"field": "result.debt_schedule"}, standard_basis=standard_basis,
            source_rules=source_rules, expected="debt schedule", actual=[],
        ))
        executed.add("FR-FIN-002")

    pre_tax_cashflows = result.get("project_pre_tax_cashflows_wan") or []
    after_tax_cashflows = result.get("project_cashflows_wan") or []
    tax_schedule = result.get("tax_schedule") or {}
    tax_rows = tax_schedule.get("project_income_tax_wan") or []
    if (
        len(pre_tax_cashflows) == len(after_tax_cashflows)
        and len(tax_rows) == max(0, len(after_tax_cashflows) - 1)
        and after_tax_cashflows
    ):
        executed.add("FIN.TAX.RECALC")
        for index, actual_tax in enumerate(tax_rows):
            expected_tax = (
                float(_number(pre_tax_cashflows[index + 1]) or 0.0)
                - float(_number(after_tax_cashflows[index + 1]) or 0.0)
            )
            numeric_tax = _number(actual_tax)
            if _different(numeric_tax, expected_tax):
                findings.append(_finding(
                    "FIN.TAX.RECALC", "P0", "收购项目逐期所得税与税前/税后现金流不一致",
                    category="tax", target_id=target_id,
                    location={"field": "result.tax_schedule.project_income_tax_wan", "period": index + 1},
                    standard_basis=standard_basis, source_rules=source_rules,
                    expected=round(expected_tax, 6), actual=numeric_tax,
                    difference=(round(numeric_tax - expected_tax, 6) if numeric_tax is not None else None),
                    tolerance=_tolerance(numeric_tax, expected_tax),
                ))
    else:
        incomplete.append("rule_input_unavailable:FIN.TAX.RECALC")

    sensitivity = result.get("sensitivity") or run.get("sensitivity") or {}
    scenarios = result.get("scenarios") or run.get("scenarios") or {}
    if sensitivity or scenarios:
        methods = {
            str(value.get("method") or "")
            for value in (sensitivity, scenarios) if isinstance(value, dict) and value
        }
        executed.add("FIN.SENSITIVITY.RERUN")
        if methods != {"full_model_rerun"}:
            findings.append(_finding(
                "FIN.SENSITIVITY.RERUN", "P0", "收购敏感性或情景分析未证明完整模型重算",
                category="sensitivity", target_id=target_id,
                location={"field": "result.sensitivity"}, standard_basis=standard_basis,
                source_rules=source_rules, expected="full_model_rerun", actual=sorted(methods),
            ))
    else:
        incomplete.append("rule_input_unavailable:FIN.SENSITIVITY.RERUN")

    if "AT-CIT-001" in source_rules:
        tax = spec.get("tax") or {}
        rate = _number(tax.get("income_tax_rate"))
        if rate is None:
            incomplete.append("rule_input_unavailable:AT-CIT-001")
        else:
            executed.add("AT-CIT-001")
            evidence = tax.get("incentive_evidence") or tax.get("legal_basis") or tax.get("evidence_ids")
            if abs(rate - 0.25) > 1e-9 and not evidence:
                findings.append(_finding(
                    "AT-CIT-001", "P0", "收购测算所得税率偏离 25% 且缺少优惠依据",
                    category="tax", target_id=target_id, location={"field": "spec.tax.income_tax_rate"},
                    standard_basis=standard_basis, source_rules=source_rules,
                    expected={"default_rate": 0.25, "or": "verified incentive evidence"}, actual=rate,
                ))

    if "AT-DEED-001" in source_rules:
        asset_scope = transaction.get("asset_scope") or []
        property_transfer = any(
            isinstance(row, dict) and row.get("included") is not False
            and str(row.get("type") or "").lower() in {"land", "building", "土地", "房屋", "不动产"}
            for row in asset_scope
        )
        if not property_transfer:
            executed.add("AT-DEED-001")
        else:
            taxes = transaction.get("transaction_taxes") or {}
            deed_tax = _number(taxes.get("deed_tax", taxes.get("deed_tax_wan")))
            purchase_price = _number(transaction.get("purchase_price"))
            rate = _number(taxes.get("deed_tax_rate"))
            if deed_tax is None or purchase_price is None:
                findings.append(_finding(
                    "AT-DEED-001", "P0", "土地或房屋收购缺少契税金额和成交价计税依据",
                    category="tax", target_id=target_id,
                    location={"field": "spec.transaction.transaction_taxes"},
                    standard_basis=standard_basis, source_rules=source_rules,
                    expected="purchase price and deed tax", actual=taxes,
                ))
                executed.add("AT-DEED-001")
            else:
                if rate is None and purchase_price:
                    rate = deed_tax / purchase_price
                executed.add("AT-DEED-001")
                if rate is None:
                    findings.append(_finding(
                        "AT-DEED-001", "P0", "契税成交价计税依据无效",
                        category="tax", target_id=target_id,
                        location={"field": "spec.transaction.purchase_price"},
                        standard_basis=standard_basis, source_rules=source_rules,
                        expected="positive transaction price", actual=purchase_price,
                    ))
                    rate = 0.0
                expected = purchase_price * rate
                if not 0.03 - 1e-12 <= rate <= 0.05 + 1e-12 or _different(deed_tax, expected):
                    findings.append(_finding(
                        "AT-DEED-001", "P0", "契税税率或成交价计税金额复算不一致",
                        category="tax", target_id=target_id,
                        location={"field": "spec.transaction.transaction_taxes"},
                        standard_basis=standard_basis, source_rules=source_rules,
                        expected={"rate_range": [0.03, 0.05], "deed_tax": round(expected, 6)},
                        actual={"rate": rate, "deed_tax": deed_tax},
                    ))

    if "AT-LVAT-001" in source_rules:
        taxes = transaction.get("transaction_taxes") or {}
        applicable = bool(taxes.get("land_value_added_tax_applicable"))
        if not applicable:
            executed.add("AT-LVAT-001")
        else:
            added = _number(taxes.get("land_value_added_wan"))
            deductions = _number(taxes.get("land_value_deductions_wan"))
            actual_tax = _number(taxes.get("land_value_added_tax_wan"))
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
                        "AT-LVAT-001", "P0", "土地增值税四级超率累进复算不一致",
                        category="tax", target_id=target_id,
                        location={"field": "spec.transaction.transaction_taxes.land_value_added_tax_wan"},
                        standard_basis=standard_basis, source_rules=source_rules,
                        expected=round(expected_tax, 6), actual=actual_tax,
                        difference=round(float(actual_tax) - expected_tax, 6),
                        tolerance=_tolerance(actual_tax, expected_tax),
                    ))
    return findings, incomplete, executed, metrics
