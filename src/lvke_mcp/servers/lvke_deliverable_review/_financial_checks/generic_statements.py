"""通用报表期间、折旧与营运资金检查组。"""

from __future__ import annotations

from typing import Any, Iterable


from .base import (
    _different,
    _finding,
    _number,
    _tolerance,
)


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

    # 摊销必须按年份从附表6（total_cost）取：income_statement 行本身不带
    # amortization 字段，而引擎的 EBIT 口径是
    # revenue − (operating_cost + depreciation + amortization) − tax_surtax
    # （domains/finance/_finance_model/annual.py 的 tc_y / pb_y）。此处若漏摊销，
    # 每个计提年都会产出一条差额恒等于当年摊销额的假 P0——高置信度、阻断发布，
    # 且把排查方向指向财务引擎，而引擎是对的。
    _amortization_by_year: dict[Any, float] = {}
    for cost_row in annual.get("total_cost") or []:
        if not isinstance(cost_row, dict):
            continue
        year_key = cost_row.get("year", cost_row.get("period"))
        amortization = _number(cost_row.get("amortization"))
        if year_key is not None and amortization is not None:
            _amortization_by_year[year_key] = amortization

    def _income_statement_ebit(row: dict[str, Any]) -> tuple[float | None, float | None, str]:
        additive = ("revenue", "operating_cost", "depreciation", "tax_surtax")
        if any(_number(row.get(key)) is None for key in additive):
            return None, _number(row.get("ebit")), "ebit"
        year_key = row.get("year", row.get("period"))
        # 缺同年摊销时不猜 0：那等于默认"没有摊销"，会把真实的漏提摊销
        # 伪装成勾稽通过。没有依据就不重算这一行。
        if year_key not in _amortization_by_year:
            return None, _number(row.get("ebit")), "ebit"
        expected = (
            _number(row.get("revenue"))
            - _number(row.get("operating_cost"))
            - _number(row.get("depreciation"))
            - _amortization_by_year[year_key]
            - _number(row.get("tax_surtax"))
        )
        return expected, _number(row.get("ebit")), "ebit"

    collections: list[tuple[str, Iterable[dict[str, Any]], Any]] = [
        (
            "income_statement",
            annual.get("income_statement") or [],
            _income_statement_ebit,
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
