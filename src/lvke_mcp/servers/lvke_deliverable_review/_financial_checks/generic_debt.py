"""通用债务与偿债能力检查组。"""

from __future__ import annotations

from typing import Any


from .base import (
    _different,
    _finding,
    _number,
    _tolerance,
)


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
