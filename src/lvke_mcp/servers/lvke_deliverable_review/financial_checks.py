"""Deterministic finance checks shared by run, package, and combined reviews."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from lvke_mcp.servers.lvke_deliverable_review import rules


BUILTIN_RULES = {
    "FIN.DEPRECIATION.RECALC",
    "FIN.TAX.RECALC",
    "FIN.DEBT.ROLLFORWARD",
    "FIN.DEBT.COVERAGE",
    "FIN.WORKING_CAPITAL.DRIVER",
    "FIN.PERIOD.RECONCILIATION",
    "FIN.SENSITIVITY.RERUN",
}


def _minimum_capital_pct(run: dict[str, Any]) -> tuple[float | None, str]:
    explicit = _number(
        (run.get("funding") or {}).get("minimum_capital_pct")
        or (run.get("spec") or {}).get("minimum_capital_pct")
        or (run.get("raw") or {}).get("minimum_capital_pct")
    )
    if explicit is not None:
        return (explicit * 100.0 if 0 < explicit <= 1 else explicit), "explicit"
    industry = str(run.get("industry") or (run.get("spec") or {}).get("industry") or "")
    try:
        import yaml

        path = Path(__file__).resolve().parents[2] / "config" / "finance_params.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, TypeError):
        return None, ""
    current = document.get("min_capital_ratio_2019") or {}
    legacy = document.get("min_capital_ratio") or {}
    candidates = [
        (str(key), _number(value))
        for mapping in (current, legacy)
        for key, value in mapping.items()
        if str(key) and str(key) in industry and _number(value) is not None
    ]
    if candidates:
        key, ratio = max(candidates, key=lambda item: len(item[0]))
        return float(ratio) * 100.0, f"config:{key}"
    default = _number(legacy.get("一般工业"))
    if default is not None and industry:
        return default * 100.0, "config:一般工业"
    return None, ""


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tolerance(*values: float | None) -> float:
    scale = max((abs(value) for value in values if value is not None), default=0.0)
    return max(0.01, scale * 1e-7)


def _different(actual: float | None, expected: float | None) -> bool:
    return actual is None or expected is None or abs(actual - expected) > _tolerance(actual, expected)


def _source_basis(
    source_rule: dict[str, Any] | None,
    standard_basis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not source_rule:
        return deepcopy(standard_basis)
    standard = source_rule.get("standard") or {}
    package_id = str(standard.get("package_id") or "")
    artifact_id = str(standard.get("artifact_id") or "")
    matching = next(
        (
            row for row in standard_basis
            if str(row.get("standard_package_id") or "") == package_id
            and str(row.get("artifact_id") or "") == artifact_id
        ),
        {},
    )
    return [{
        **deepcopy(matching),
        "standard_package_id": package_id,
        "artifact_id": artifact_id,
        "content_hash": standard.get("sha256") or matching.get("content_hash"),
        "locator": standard.get("locator"),
        "quote": standard.get("quote"),
    }]


def _finding(
    rule_id: str,
    severity: str,
    message: str,
    *,
    category: str,
    target_id: str,
    location: dict[str, Any],
    standard_basis: list[dict[str, Any]],
    source_rules: dict[str, dict[str, Any]],
    expected: Any = None,
    actual: Any = None,
    difference: Any = None,
    tolerance: Any = None,
    remediation: str = "修正结构化财务输入并生成新 run 后复测",
) -> dict[str, Any]:
    source_rule = source_rules.get(rule_id)
    item = rules.finding(
        rule_id,
        severity,
        message,
        category=category,
        blocking=bool((source_rule or {}).get("blocking", severity in {"P0", "P1"})),
        expected=expected,
        actual=actual,
        difference=difference,
        tolerance=tolerance,
        target_location={"run_id": target_id, **location},
        standard_basis=_source_basis(source_rule, standard_basis),
        review_area="finance",
        remediation=remediation,
    )
    return item


def _generic_period_checks(
    run: dict[str, Any],
    target_id: str,
    source_rules: dict[str, dict[str, Any]],
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], set[str], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    executed: set[str] = set()
    metrics: dict[str, Any] = {"period_rows_checked": 0, "period_mismatches": 0}
    annual = run.get("annual") or {}

    collections: list[tuple[str, Iterable[dict[str, Any]], Any]] = [
        (
            "income_statement",
            annual.get("income_statement") or [],
            lambda row: (
                _number(row.get("revenue"))
                - _number(row.get("operating_cost"))
                - _number(row.get("depreciation"))
                - _number(row.get("tax_surtax"))
                if all(
                    _number(row.get(key)) is not None
                    for key in ("revenue", "operating_cost", "depreciation", "tax_surtax")
                )
                else None,
                _number(row.get("ebit")),
                "ebit",
            ),
        ),
        (
            "total_cost",
            annual.get("total_cost") or [],
            lambda row: (
                sum(
                    float(_number(row.get(key)) or 0.0)
                    for key in ("operating_cost", "depreciation", "amortization", "interest")
                ),
                _number(row.get("total_cost")),
                "total_cost",
            ),
        ),
        (
            "project_cashflow",
            annual.get("project_cashflow") or [],
            lambda row: (
                sum(
                    float(_number(row.get(key)) or 0.0) * sign
                    for key, sign in (
                        ("revenue", 1.0),
                        ("op_cash_cost", -1.0),
                        ("tax_surtax", -1.0),
                        ("income_tax", -1.0),
                        ("construction", -1.0),
                        ("wc_change", -1.0),
                        ("recover", 1.0),
                    )
                ),
                _number(row.get("net_cashflow")),
                "net_cashflow",
            ),
        ),
        (
            "capital_cashflow",
            annual.get("capital_cashflow") or [],
            lambda row: (
                _number(row.get("cash_inflow")) - _number(row.get("cash_outflow"))
                if _number(row.get("cash_inflow")) is not None
                and _number(row.get("cash_outflow")) is not None
                else None,
                _number(row.get("net_cashflow")),
                "net_cashflow",
            ),
        ),
        (
            "financial_plan",
            annual.get("financial_plan") or [],
            lambda row: (
                _number(row.get("operating_net"))
                + _number(row.get("finance_in"))
                - _number(row.get("invest_out"))
                - _number(row.get("debt_service"))
                if all(
                    _number(row.get(key)) is not None
                    for key in ("operating_net", "finance_in", "invest_out", "debt_service")
                )
                else None,
                _number(row.get("net_cashflow")),
                "net_cashflow",
            ),
        ),
    ]
    checked = 0
    for collection_name, rows, calculator in collections:
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            expected, actual, field = calculator(row)
            if expected is None or actual is None:
                continue
            checked += 1
            metrics["period_rows_checked"] += 1
            if not _different(actual, expected):
                continue
            metrics["period_mismatches"] += 1
            item = _finding(
                "FIN.PERIOD.RECONCILIATION",
                "P0",
                "财务表逐期勾稽不一致",
                category="financial_recalculation",
                target_id=target_id,
                location={
                    "table_code": collection_name,
                    "period": row.get("year", row.get("period", index)),
                    "field": field,
                },
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected=round(expected, 6),
                actual=actual,
                difference=round(actual - expected, 6),
                tolerance=_tolerance(actual, expected),
            )
            item["calculation_trace"] = [
                f"{collection_name}.{field} independent row recalculation",
                f"actual({actual}) - expected({round(expected, 6)})",
            ]
            findings.append(item)
    if checked:
        executed.add("FIN.PERIOD.RECONCILIATION")
    else:
        incomplete.append("rule_input_unavailable:FIN.PERIOD.RECONCILIATION")
    return findings, incomplete, executed, metrics


def _generic_depreciation_checks(
    run: dict[str, Any],
    target_id: str,
    source_rules: dict[str, dict[str, Any]],
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], set[str], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    executed: set[str] = set()
    rows = ((run.get("annual") or {}).get("depreciation_table") or [])
    raw = run.get("raw") or {}
    metrics = {"depreciation_rows_checked": 0, "class_count": 0}
    if not rows:
        fixed_asset = _number((run.get("investment") or {}).get("fixed_asset"))
        if fixed_asset and fixed_asset > 0:
            findings.append(_finding(
                "AT-FA-001",
                "P0",
                "存在固定资产但缺少逐期折旧表",
                category="depreciation",
                target_id=target_id,
                location={"field": "annual.depreciation_table"},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected="逐期折旧基数、寿命、残值和折旧额",
                actual=None,
            ))
            executed.update({"AT-FA-001", "FIN.DEPRECIATION.RECALC"})
        else:
            incomplete.extend([
                "rule_input_unavailable:AT-FA-001",
                "rule_input_unavailable:FIN.DEPRECIATION.RECALC",
            ])
        return findings, incomplete, executed, metrics

    cumulative = 0.0
    depreciation_mismatches: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        original = _number(row.get("original_value"))
        salvage_rate = _number(row.get("salvage_rate"))
        years = _number(row.get("dep_years"))
        actual = _number(row.get("depreciation"))
        if original is None or salvage_rate is None or years in (None, 0) or actual is None:
            incomplete.append(f"depreciation_row_inputs_missing:{index}")
            continue
        class_rows = row.get("classes") or []
        classified = (
            row.get("depreciation_basis") == "classified"
            and isinstance(class_rows, list)
            and any(isinstance(item, dict) for item in class_rows)
        )
        if classified:
            expected = sum(
                float(_number(item.get("depreciation")) or 0.0)
                for item in class_rows
                if isinstance(item, dict)
            )
        else:
            schedule_period = index + 1
            expected = (
                original * (1.0 - salvage_rate) / years
                if schedule_period <= years
                else 0.0
            )
        metrics["depreciation_rows_checked"] += 1
        cumulative += actual
        annual_mismatch = _different(actual, expected)
        stored_cumulative = _number(row.get("cumulative_depreciation"))
        rounding_tolerance = max(0.01, 0.005 * (index + 1) + 0.005)
        cumulative_mismatch = (
            stored_cumulative is not None
            and abs(stored_cumulative - cumulative) > rounding_tolerance
        )
        stored_net = _number(row.get("net_value"))
        expected_net = original - cumulative
        net_mismatch = (
            stored_net is not None
            and abs(stored_net - expected_net) > rounding_tolerance
        )
        if annual_mismatch or cumulative_mismatch or net_mismatch:
            depreciation_mismatches.append({
                "period": row.get("year", index + 1),
                "annual_mismatch": annual_mismatch,
                "cumulative_mismatch": cumulative_mismatch,
                "net_mismatch": net_mismatch,
                "rounding_tolerance": round(rounding_tolerance, 6),
                "expected": {
                    "depreciation": round(expected, 6),
                    "cumulative_depreciation": round(cumulative, 6),
                    "net_value": round(expected_net, 6),
                },
                "actual": {
                    "depreciation": actual,
                    "cumulative_depreciation": stored_cumulative,
                    "net_value": stored_net,
                },
            })

    if depreciation_mismatches:
        material = any(item["annual_mismatch"] for item in depreciation_mismatches)
        finding = _finding(
            "AT-FA-001",
            "P0" if material else "P1",
            (
                "固定资产折旧基数、残值或寿命复算不一致"
                if material
                else "固定资产累计折旧或净值超出逐期舍入容差"
            ),
            category="depreciation",
            target_id=target_id,
            location={
                "table_code": "depreciation",
                "affected_periods": [item["period"] for item in depreciation_mismatches],
            },
            standard_basis=standard_basis,
            source_rules=source_rules,
            expected="逐期折旧额汇总后的累计折旧与净值",
            actual=depreciation_mismatches,
        )
        finding["calculation_trace"] = [
            "annual depreciation is rounded to 0.01 万元 before accumulation",
            "cumulative tolerance = 0.005 * period_count + 0.005",
            "net_value = original_value - sum(displayed annual depreciation)",
        ]
        finding["affected_periods"] = [item["period"] for item in depreciation_mismatches]
        findings.append(finding)

    classes = raw.get("depreciation_classes") or []
    metrics["class_count"] = len(classes) if isinstance(classes, list) else 0
    for index, asset_class in enumerate(classes if isinstance(classes, list) else []):
        if not isinstance(asset_class, dict):
            continue
        name = str(asset_class.get("name") or asset_class.get("type") or "")
        if "土地" in name and any(
            (_number(asset_class.get(key)) or 0.0) > 0
            for key in ("annual_depreciation_wan", "depreciation")
        ):
            findings.append(_finding(
                "AT-FA-001",
                "P0",
                "土地被计入折旧",
                category="depreciation",
                target_id=target_id,
                location={"field": f"raw.depreciation_classes[{index}]", "asset_class": name},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected=0,
                actual=asset_class,
            ))
    life_meta = raw.get("dep_life_meta") or {}
    if life_meta.get("source") == "default_op_years" and not classes:
        findings.append(_finding(
            "AT-FA-001",
            "P1",
            "折旧寿命由运营期默认推导，缺少资产分类和剩余寿命证据",
            category="depreciation",
            target_id=target_id,
            location={"field": "raw.dep_life_meta"},
            standard_basis=standard_basis,
            source_rules=source_rules,
            expected="按资产组件和证据确认使用寿命",
            actual=life_meta,
        ))
    if metrics["depreciation_rows_checked"]:
        executed.update({"AT-FA-001", "FIN.DEPRECIATION.RECALC"})
    else:
        incomplete.extend([
            "rule_input_unavailable:AT-FA-001",
            "rule_input_unavailable:FIN.DEPRECIATION.RECALC",
        ])
    return findings, incomplete, executed, metrics


def _generic_debt_checks(
    run: dict[str, Any],
    target_id: str,
    source_rules: dict[str, dict[str, Any]],
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], set[str], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    executed: set[str] = set()
    funding = run.get("funding") or {}
    loan = _number(funding.get("loan"))
    rows = ((run.get("annual") or {}).get("debt_service") or [])
    metrics: dict[str, Any] = {"debt_rows_checked": 0, "minimum_dscr": None, "minimum_icr": None}
    if loan is None:
        incomplete.extend([
            "rule_input_unavailable:FIN.DEBT.ROLLFORWARD",
            "rule_input_unavailable:FIN.DEBT.COVERAGE",
            "rule_input_unavailable:FR-FIN-002",
            "rule_input_unavailable:FR-LOAN-002",
            "rule_input_unavailable:FR-LOAN-004",
        ])
        return findings, incomplete, executed, metrics
    if loan <= _tolerance(loan):
        executed.update({
            "FIN.DEBT.ROLLFORWARD", "FIN.DEBT.COVERAGE", "FR-FIN-002",
            "FR-LOAN-002", "FR-LOAN-004",
        })
        return findings, incomplete, executed, metrics
    if not rows:
        findings.append(_finding(
            "FR-FIN-002",
            "P0",
            "存在债务融资但缺少逐期还本付息、DSCR 和 ICR",
            category="debt_service",
            target_id=target_id,
            location={"field": "annual.debt_service"},
            standard_basis=standard_basis,
            source_rules=source_rules,
            expected="逐期借款余额、利息、还本、偿债来源、DSCR、ICR",
            actual=None,
        ))
        executed.update({"FR-FIN-002", "FR-LOAN-002", "FR-LOAN-004"})
        incomplete.extend([
            "rule_input_unavailable:FIN.DEBT.ROLLFORWARD",
            "rule_input_unavailable:FIN.DEBT.COVERAGE",
        ])
        return findings, incomplete, executed, metrics

    previous_end: float | None = None
    active_rows = 0
    dscr_values: list[float] = []
    icr_values: list[float] = []
    income_rows = {
        int(row.get("year")): row
        for row in ((run.get("annual") or {}).get("income_statement") or [])
        if isinstance(row, dict) and _number(row.get("year")) is not None
    }
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        begin = _number(row.get("begin"))
        principal = _number(row.get("principal"))
        interest = _number(row.get("interest"))
        end = _number(row.get("end"))
        if None in {begin, principal, interest, end}:
            incomplete.append(f"debt_row_inputs_missing:{index}")
            continue
        metrics["debt_rows_checked"] += 1
        due = float(principal) + float(interest)
        if due > _tolerance(due):
            active_rows += 1
        expected_end = float(begin) - float(principal)
        mismatch_reasons: list[str] = []
        if _different(float(end), expected_end):
            mismatch_reasons.append("end_balance")
        if previous_end is not None and _different(float(begin), previous_end):
            mismatch_reasons.append("opening_rollforward")
        rate = _number(funding.get("loan_rate"))
        if rate is not None and float(interest) > 0 and _different(float(interest), float(begin) * rate):
            mismatch_reasons.append("interest")
        previous_end = float(end)
        if mismatch_reasons:
            item = _finding(
                "FIN.DEBT.ROLLFORWARD",
                "P0",
                "借款余额、还本或利息逐期滚动不一致",
                category="debt_service",
                target_id=target_id,
                location={"table_code": "debt-service", "period": row.get("year", index + 1)},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected={"end": round(expected_end, 6), "prior_end": previous_end, "rate": rate},
                actual={"begin": begin, "principal": principal, "interest": interest, "end": end},
            )
            item["calculation_trace"] = [
                "end = begin - principal",
                "next.begin = prior.end",
                "interest = begin * annual_rate",
            ]
            findings.append(item)

        if due <= _tolerance(due):
            continue
        dscr = _number(row.get("dscr"))
        icr = _number(row.get("icr"))
        if dscr is None or icr is None:
            findings.append(_finding(
                "FR-FIN-002",
                "P0",
                "有还本付息期间缺少 DSCR 或 ICR",
                category="debt_service",
                target_id=target_id,
                location={"table_code": "debt-service", "period": row.get("year", index + 1)},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected={"dscr": "number", "icr": "number"},
                actual={"dscr": dscr, "icr": icr},
            ))
            continue
        dscr_values.append(dscr)
        icr_values.append(icr)
        repay_profit = _number(row.get("repay_source_profit"))
        repay_dep = _number(row.get("repay_source_dep"))
        repay_amort = _number(row.get("repay_source_amort"))
        if None not in {repay_profit, repay_dep, repay_amort}:
            expected_dscr = (
                float(repay_profit) + float(repay_dep) + float(repay_amort) + float(interest)
            ) / due
            if _different(dscr, expected_dscr):
                findings.append(_finding(
                    "FIN.DEBT.COVERAGE",
                    "P0",
                    "DSCR 与逐期偿债资金来源独立复算不一致",
                    category="debt_service",
                    target_id=target_id,
                    location={"table_code": "debt-service", "period": row.get("year", index + 1), "field": "dscr"},
                    standard_basis=standard_basis,
                    source_rules=source_rules,
                    expected=round(expected_dscr, 6),
                    actual=dscr,
                    difference=round(dscr - expected_dscr, 6),
                    tolerance=_tolerance(dscr, expected_dscr),
                ))
        year = int(_number(row.get("year")) or index + 1)
        ebit = _number((income_rows.get(year) or {}).get("ebit"))
        if ebit is not None and interest > _tolerance(interest):
            expected_icr = ebit / interest
            if _different(icr, expected_icr):
                findings.append(_finding(
                    "FIN.DEBT.COVERAGE",
                    "P0",
                    "ICR 与息税前利润和利息独立复算不一致",
                    category="debt_service",
                    target_id=target_id,
                    location={"table_code": "debt-service", "period": row.get("year", index + 1), "field": "icr"},
                    standard_basis=standard_basis,
                    source_rules=source_rules,
                    expected=round(expected_icr, 6),
                    actual=icr,
                    difference=round(icr - expected_icr, 6),
                    tolerance=_tolerance(icr, expected_icr),
                ))
        if dscr < 1.0 or icr < 1.0:
            findings.append(_finding(
                "FR-LOAN-002",
                "P0",
                "偿债备付率或利息备付率低于 1",
                category="debt_service",
                target_id=target_id,
                location={"table_code": "debt-service", "period": row.get("year", index + 1)},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected={"dscr_min": 1.0, "icr_min": 1.0},
                actual={"dscr": dscr, "icr": icr},
                remediation="重构融资或偿债计划，并在报告中披露下行情景和资金接续方案",
            ))

    expected_years = int(_number(funding.get("loan_years")) or 0)
    if expected_years and active_rows != expected_years:
        findings.append(_finding(
            "FR-LOAN-004",
            "P0",
            "还本付息期间与贷款期限不匹配",
            category="debt_service",
            target_id=target_id,
            location={"field": "funding.loan_years"},
            standard_basis=standard_basis,
            source_rules=source_rules,
            expected=expected_years,
            actual=active_rows,
        ))
    if previous_end is not None and abs(previous_end) > _tolerance(previous_end):
        findings.append(_finding(
            "FR-LOAN-004",
            "P0",
            "测算期末仍有未偿还借款余额",
            category="debt_service",
            target_id=target_id,
            location={"table_code": "debt-service", "field": "end"},
            standard_basis=standard_basis,
            source_rules=source_rules,
            expected=0.0,
            actual=previous_end,
        ))
    metrics["minimum_dscr"] = min(dscr_values) if dscr_values else None
    metrics["minimum_icr"] = min(icr_values) if icr_values else None
    if metrics["debt_rows_checked"]:
        executed.update({
            "FIN.DEBT.ROLLFORWARD", "FIN.DEBT.COVERAGE", "FR-FIN-002",
            "FR-LOAN-002", "FR-LOAN-004",
        })
    return findings, incomplete, executed, metrics


def _generic_working_capital_checks(
    run: dict[str, Any],
    target_id: str,
    source_rules: dict[str, dict[str, Any]],
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], set[str], dict[str, Any]]:
    working = ((run.get("annual") or {}).get("working_capital") or {})
    metrics = {"method": working.get("method") if isinstance(working, dict) else None}
    if not isinstance(working, dict) or not working:
        return [], ["rule_input_unavailable:FIN.WORKING_CAPITAL.DRIVER"], set(), metrics
    findings: list[dict[str, Any]] = []
    current_assets = _number(working.get("current_assets"))
    current_liabilities = _number(working.get("current_liabilities", working.get("payable")))
    net = _number(working.get("net_working_capital", working.get("total")))
    if None not in {current_assets, current_liabilities, net}:
        expected = float(current_assets) - float(current_liabilities)
        if _different(net, expected):
            findings.append(_finding(
                "FIN.WORKING_CAPITAL.DRIVER",
                "P0",
                "流动资金净额与流动资产、流动负债不勾稽",
                category="working_capital",
                target_id=target_id,
                location={"table_code": "working-capital"},
                standard_basis=standard_basis,
                source_rules=source_rules,
                expected=round(expected, 6),
                actual=net,
                difference=round(float(net) - expected, 6),
                tolerance=_tolerance(net, expected),
            ))
    if str(working.get("method") or "") == "ratio_backsolve":
        findings.append(_finding(
            "FIN.WORKING_CAPITAL.DRIVER",
            "P1",
            "流动资金采用汇总比例反解，未形成可审计周转驱动",
            category="working_capital",
            target_id=target_id,
            location={"table_code": "working-capital", "field": "method"},
            standard_basis=standard_basis,
            source_rules=source_rules,
            expected="周转天数或其他已确认业务驱动",
            actual="ratio_backsolve",
            remediation="补充应收、存货、现金、应付的周转天数及来源后重算",
        ))
    return findings, [], {"FIN.WORKING_CAPITAL.DRIVER"}, metrics


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

    debt = result.get("debt_schedule") or []
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


def review_finance_run(
    run: dict[str, Any],
    *,
    target_id: str,
    target_type: str,
    applicable_rules: Iterable[str],
    source_rule_rows: Iterable[dict[str, Any]],
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], set[str], dict[str, Any]]:
    """Run deterministic checks and report only rules whose inputs were evaluated."""

    applicable = set(applicable_rules)
    source_rules = {
        str(row.get("rule_id") or ""): row
        for row in source_rule_rows
        if str(row.get("rule_id") or "") in applicable
        and row.get("check_kind") == "deterministic"
    }
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    executed: set[str] = set()
    metrics: dict[str, Any] = {}
    if not run or not run.get("available"):
        return findings, ["bound_finance_run_unavailable"], executed, metrics

    is_acquisition = target_type.startswith("acquisition_") or bool(run.get("result")) and (
        str(run.get("model_version") or "").startswith("acquisition_model")
        or str((run.get("result") or {}).get("model_version") or "").startswith("acquisition_model")
    )
    if is_acquisition:
        rows, missing, done, check_metrics = _acquisition_checks(
            run, target_id, source_rules, standard_basis,
        )
        findings.extend(rows)
        incomplete.extend(missing)
        executed.update(done)
        metrics.update(check_metrics)
    else:
        for checker in (
            _generic_period_checks,
            _generic_depreciation_checks,
            _generic_debt_checks,
            _generic_working_capital_checks,
            _generic_sensitivity_checks,
            _generic_tax_and_source_checks,
            _generic_finance_source_checks,
        ):
            rows, missing, done, check_metrics = checker(
                run, target_id, source_rules, standard_basis,
            )
            findings.extend(rows)
            incomplete.extend(missing)
            executed.update(done)
            metrics.update(check_metrics)

    allowed = applicable | set(source_rules)
    executed.intersection_update(allowed)
    incomplete = [
        reason for reason in incomplete
        if not reason.startswith("rule_input_unavailable:")
        or reason.removeprefix("rule_input_unavailable:") in allowed
    ]
    return findings, sorted(set(incomplete)), executed, metrics
