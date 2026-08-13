"""审查侧逐期勾稽的口径必须与财务引擎一致。

引擎的 EBIT 口径（``domains/finance/_finance_model/annual.py`` 的 ``tc_y`` / ``pb_y``）是

    revenue − (operating_cost + depreciation + amortization) − tax_surtax

而 ``income_statement`` 行**不带** ``amortization`` 字段，摊销只在附表6
（``total_cost``）里。审查侧一旦漏掉这一项，每个计提年都会产出一条差额恒等于当年
摊销额的 P0：confidence=1.0、blocking=true、判正式交付 fail，并把排查方向指向财务
引擎——而引擎是对的。2026-08-12 的实机审计就因此报出"10 个期间不一致"。

这组测试同时锁住相反方向的失败：修复不得把规则改成"取不到摊销就跳过"，那会让
``period_rows_checked`` 归零、门禁静默失效——比假 P0 更危险。
"""

from __future__ import annotations

import unittest

from lvke_mcp.servers.lvke_deliverable_review._financial_checks.generic_statements import (
    _generic_period_checks,
)

_AMORTIZATION = 120.0


def _run(*, amortization: float | None = _AMORTIZATION, ebit_offset: float = 0.0) -> dict:
    """两年期最小 run；EBIT 按引擎口径（含摊销）填写。"""

    rows = []
    costs = []
    for year, (revenue, op_cost, dep, surtax) in enumerate(
        [(5000.0, 1500.0, 800.0, 300.0), (6000.0, 1700.0, 800.0, 360.0)], start=1
    ):
        amount = 0.0 if amortization is None else amortization
        rows.append({
            "year": year,
            "revenue": revenue,
            "operating_cost": op_cost,
            "depreciation": dep,
            "tax_surtax": surtax,
            "ebit": round(revenue - op_cost - dep - amount - surtax + ebit_offset, 2),
        })
        cost_row = {
            "year": year,
            "operating_cost": op_cost,
            "depreciation": dep,
            "interest": 0.0,
        }
        if amortization is not None:
            cost_row["amortization"] = amortization
            cost_row["total_cost"] = round(op_cost + dep + amortization, 2)
        else:
            cost_row["total_cost"] = round(op_cost + dep, 2)
        costs.append(cost_row)
    return {"annual": {"income_statement": rows, "total_cost": costs}}


def _ebit_findings(run: dict) -> list[dict]:
    findings, _incomplete, _executed, _metrics = _generic_period_checks(run, "run_x", {}, [])
    return [
        row for row in findings
        if (row.get("target_location") or {}).get("field") == "ebit"
    ]


class PeriodReconciliationAmortizationTest(unittest.TestCase):
    def test_engine_consistent_ebit_raises_no_finding(self) -> None:
        """按引擎口径（含摊销）自洽的表不得报 P0。"""

        run = _run()
        findings = _ebit_findings(run)
        self.assertEqual([], findings, findings)

    def test_rule_still_executes_and_counts_rows(self) -> None:
        """修复不得退化成"取不到就跳过"：规则必须真的跑过并计数。"""

        _f, incomplete, executed, metrics = _generic_period_checks(_run(), "run_x", {}, [])
        self.assertIn("FIN.PERIOD.RECONCILIATION", executed)
        # 同一规则同时核对 income_statement 与 total_cost：2 年 × 2 张表 = 4 行。
        self.assertEqual(4, metrics["period_rows_checked"])
        self.assertEqual(0, metrics["period_mismatches"])
        self.assertNotIn(
            "rule_input_unavailable:FIN.PERIOD.RECONCILIATION", incomplete
        )

    def test_real_ebit_error_is_still_caught(self) -> None:
        """门禁要有判别力：真错（EBIT 少 50）必须仍然报出来。"""

        findings = _ebit_findings(_run(ebit_offset=-50.0))
        self.assertEqual(2, len(findings))
        for row in findings:
            self.assertEqual("P0", row["severity"])
            self.assertTrue(row["blocking"])
            self.assertAlmostEqual(-50.0, row["difference"], places=2)

    def test_amortization_omission_would_have_produced_constant_offset(self) -> None:
        """回归的判别性证明：差额恒等于摊销额，说明旧公式漏的正是这一项。

        用"表里 EBIT 按不含摊销的旧口径填写"来模拟旧实现的期望值；此时正确的
        审查口径必须把它判为不一致，且差额恒为 +摊销额。
        """

        findings = _ebit_findings(_run(ebit_offset=_AMORTIZATION))
        self.assertEqual(2, len(findings))
        for row in findings:
            self.assertAlmostEqual(_AMORTIZATION, row["difference"], places=2)

    def test_missing_amortization_row_is_not_treated_as_zero(self) -> None:
        """缺同年摊销数据时不得默认 0——那会把漏提摊销伪装成勾稽通过。"""

        run = _run(amortization=None)
        # total_cost 行没有 amortization 字段：EBIT 那一行不参与重算，
        # 因此不报 P0；total_cost 自身的勾稽仍照常核对（2 行）。
        _f, _incomplete, _executed, metrics = _generic_period_checks(run, "run_x", {}, [])
        self.assertEqual([], _ebit_findings(run))
        self.assertEqual(0, metrics["period_mismatches"])
        self.assertEqual(2, metrics["period_rows_checked"])

    def test_amortization_is_joined_by_year_not_by_position(self) -> None:
        """按年份连接，不靠行序：total_cost 倒序时结果必须不变。"""

        run = _run()
        run["annual"]["total_cost"] = list(reversed(run["annual"]["total_cost"]))
        self.assertEqual([], _ebit_findings(run))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
