from __future__ import annotations

import unittest

from lvke_mcp.domains.finance import finance_model as fm
from lvke_mcp.domains.finance.checks import run_checks
from lvke_mcp.domains.finance.revenue_models import expand

_SPEC = {
    "revenue": {"model": "flat", "annual_revenue_wan": 6660},
    "cost": {"cost_items": {"经营成本": 1560.21}},
    "tax": {"income_tax_rate": 0.25, "vat_rate": 0.13, "vat_input_rate": 0.1},
}


def _inputs(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "total_investment_wan": 26200,
        "capital_own_wan": 10480,
        "loan_wan": 15720,
        "invest_breakdown": {
            "construction_wan": 23800,
            "interest_wan": 900,
            "working_capital_wan": 1500,
        },
        "build_period_months": 24,
        "calc_period_years": 12,
        "loan_years": 12,
        "loan_rate": 0.038,
        "discount_rate": 0.08,
        "is_operating": True,
        "annual_revenue_wan": 6660,
        "cost_items": {"经营成本": 1560.21},
    }
    base.update(over)
    return base


class FlatRampRegressionTest(unittest.TestCase):
    """``flat`` 收入模型必须消费 ``ramp``。

    活体实测缺陷：调用方传 ``ramp=[0.6,0.85,1]``，``expanded.revenue_by_year``
    仍为达产年全额十连发。值原样留在 ``revenue_spec`` 里（schema 放行），
    且无任何 warning/quality_issue —— 读起来像"没传"，实际是传了被丢。
    后果是爬坡期收入被高估，而该 ledger 直接喂 FinanceSpec。

    根因属「同一语义两处实现只改一侧」：``_product_sales`` / ``_tourism`` /
    ``_gov_payment`` / ``_rail_transit`` 都调 ``_ramp``，只有 ``_flat`` 漏接。
    """

    def test_flat_consumes_ramp(self) -> None:
        out = expand(
            {"revenue": {"model": "flat", "annual_revenue_wan": 6660,
                         "ramp": [0.6, 0.85, 1]}},
            10,
        )
        self.assertEqual(out["revenue_by_year"][:3], [3996.0, 5661.0, 6660.0])
        # 末值填满：ramp 短于 op_years 时按 _ramp 语义补齐，不得回落全额
        self.assertEqual(out["revenue_by_year"][3:], [6660.0] * 7)
        self.assertIn("达产率曲线", out["note"])

    def test_flat_without_ramp_stays_backward_compatible(self) -> None:
        """反向：修 fail-closed 的真风险是误杀正常链，这条钉住无 ramp 的旧行为。"""
        out = expand({"revenue": {"model": "flat", "annual_revenue_wan": 6660}}, 10)
        self.assertEqual(out["revenue_by_year"], [6660.0] * 10)
        self.assertNotIn("达产率曲线", out["note"])

    def test_flat_ramp_zero_first_year_not_treated_as_missing(self) -> None:
        """首年 0 爬坡（当年不投产）必须生效，不能被 ``or []`` 之类的假值判断吞掉。"""
        out = expand(
            {"revenue": {"model": "flat", "annual_revenue_wan": 1000, "ramp": [0, 0.5, 1]}},
            3,
        )
        self.assertEqual(out["revenue_by_year"], [0.0, 500.0, 1000.0])


class FundingStructureCrossTableTest(unittest.TestCase):
    """附表11 建设期融资结构必须与附表4 资金筹措同源。

    活体实测缺陷：``funding_annual_schedule`` 未提供时，``_build_financial_plan``
    的回退分支把**全部筹资**塞进 ``capital_own``、``loan_draw`` 置 0，于是同一
    run 的附表4 印「资本金 11200 / 贷款 16800」、附表11 印「资本金 26200 /
    贷款 0」。两张表各自"自洽"，单表校验均 ``valid=true``、
    ``structure_coverage=1``，而整包 22 条勾稽**无一条比对二者**，
    ``funding_balance_ok=false`` 也不进 blockers。
    """

    def test_fallback_splits_by_real_funding_structure(self) -> None:
        result = fm.compute_financials(_inputs(), spec=_SPEC)
        build_rows = [
            row for row in result["annual"]["financial_plan"]
            if row["phase"] == "建设期"
        ]
        self.assertTrue(build_rows)
        # 回退分支才是缺陷现场，确认走的正是它
        self.assertEqual(
            {row["funding_plan_source"] for row in build_rows},
            {"estimate_fallback"},
        )
        plan_equity = round(sum(row["capital_own"] for row in build_rows), 2)
        plan_loan = round(sum(row["loan_draw"] for row in build_rows), 2)
        self.assertAlmostEqual(plan_equity, result["funding"]["capital"], places=2)
        self.assertAlmostEqual(plan_loan, result["funding"]["loan"], places=2)
        # 旧行为的特征值：资本金吃掉全部筹资、贷款为 0
        self.assertNotEqual(plan_loan, 0.0)
        for row in build_rows:
            self.assertAlmostEqual(
                round(row["capital_own"] + row["loan_draw"] + row["gov_subsidy"], 2),
                row["finance_in"],
                places=2,
            )

    def test_cross_table_check_present_and_passes_when_consistent(self) -> None:
        result = fm.compute_financials(_inputs(), spec=_SPEC)
        hits = [
            c for c in run_checks(result)
            if c["rule"] == "附表11建设期融资结构=附表4资金筹措"
        ]
        self.assertEqual(len(hits), 1, "跨表勾稽必须存在，否则同类漂移再无人报")
        self.assertTrue(hits[0]["ok"], hits[0]["detail"])
        self.assertTrue(hits[0]["blocking"])

    def test_cross_table_check_catches_legacy_drift(self) -> None:
        """喂旧行为的特征数据，勾稽必须报 false —— 否则等于没加这条规则。"""
        drifted = {
            "funding": {"capital": 11200, "loan": 16800, "subsidy": 0},
            "annual": {"financial_plan": [
                {"phase": "建设期", "capital_own": 13100, "loan_draw": 0, "gov_subsidy": 0},
                {"phase": "建设期", "capital_own": 13100, "loan_draw": 0, "gov_subsidy": 0},
            ]},
        }
        hits = [
            c for c in run_checks(drifted)
            if c["rule"] == "附表11建设期融资结构=附表4资金筹措"
        ]
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0]["ok"])
        self.assertIn("26,200.00", hits[0]["detail"])

    def test_cross_table_check_skipped_without_financial_plan(self) -> None:
        """非经营性项目不构造 financial_plan，此时不得凭空报一条失败勾稽。"""
        hits = [
            c for c in run_checks({"funding": {"capital": 100, "loan": 0}})
            if c["rule"] == "附表11建设期融资结构=附表4资金筹措"
        ]
        self.assertEqual(hits, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
