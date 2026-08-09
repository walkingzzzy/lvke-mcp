"""城市轨道交通收入必须三分：票务 / 非票 / 财政支持。

此前 urban_rail 套用 ``gov_payment``，把票款、非票和补贴压成单一"年政府付费"，
于是附表5 没有量价树，``income_product_tree`` 与 ``income_formula_driven`` 双双阻断，
且票价/客流敏感性根本无从计算。

修复后走专用 ``rail_transit`` 模型：
- 票务 = 客运量 × 平均清分票价 × 爬坡
- 非票 = 票务 × 情景比例（low 5% / base 10% / high 15%）
- 财政支持单列，绝不混入票价或摊成保证利润
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.domains.finance import revenue_models, tables_service
from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
from lvke_mcp.domains.finance.model_application import run_model
from lvke_mcp.domains.finance.spec import validate


def _rail_revenue(**overrides: object) -> dict:
    revenue = {
        "model": "rail_transit",
        "annual_passenger_trips": 5000.0,
        "passenger_unit": "万人次",
        "average_fare_yuan": 3.2,
        "ridership_ramp": [0.6, 0.8, 1.0],
        "non_fare_scenario": "base",
        "annual_fiscal_support_wan": 8000.0,
        "fiscal_support_ramp": [0.6, 0.8, 1.0],
    }
    revenue.update(overrides)
    return revenue


class RailTransitRevenueModelTest(unittest.TestCase):
    def test_farebox_is_ridership_times_clearing_fare_times_ramp(self) -> None:
        expanded = revenue_models.expand({"revenue": _rail_revenue()}, 3)
        # 5000 万人次 × 3.2 元 = 16000 万元达产票务收入
        self.assertEqual(expanded["farebox_by_year"], [9600.0, 12800.0, 16000.0])

    def test_non_fare_is_pegged_to_farebox_by_scenario(self) -> None:
        for scenario, rate in (("low", 0.05), ("base", 0.10), ("high", 0.15)):
            with self.subTest(scenario=scenario):
                expanded = revenue_models.expand(
                    {"revenue": _rail_revenue(non_fare_scenario=scenario)}, 3
                )
                self.assertEqual(expanded["non_fare_revenue_rate"], rate)
                self.assertEqual(
                    expanded["non_fare_by_year"][2],
                    round(expanded["farebox_by_year"][2] * rate, 2),
                )

    def test_explicit_rate_overrides_scenario(self) -> None:
        expanded = revenue_models.expand(
            {
                "revenue": _rail_revenue(
                    non_fare_scenario="low", non_fare_revenue_rate=0.12
                )
            },
            3,
        )
        self.assertEqual(expanded["non_fare_revenue_rate"], 0.12)

    def test_fiscal_support_stays_a_separate_line(self) -> None:
        expanded = revenue_models.expand({"revenue": _rail_revenue()}, 3)
        self.assertEqual(expanded["fiscal_support_by_year"], [4800.0, 6400.0, 8000.0])
        # 补贴绝不摊进票价：票务收入不含补贴。
        self.assertEqual(expanded["farebox_by_year"][2], 16000.0)
        # 三类之和等于营业收入，不重不漏。
        for index, total in enumerate(expanded["revenue_by_year"]):
            self.assertEqual(
                total,
                round(
                    expanded["farebox_by_year"][index]
                    + expanded["non_fare_by_year"][index]
                    + expanded["fiscal_support_by_year"][index],
                    2,
                ),
            )

    def test_zero_fiscal_support_is_allowed(self) -> None:
        expanded = revenue_models.expand(
            {"revenue": _rail_revenue(annual_fiscal_support_wan=0.0)}, 3
        )
        self.assertEqual(expanded["fiscal_support_by_year"], [0.0, 0.0, 0.0])
        self.assertGreater(expanded["revenue_by_year"][0], 0.0)


class RailTransitSpecValidationTest(unittest.TestCase):
    def _spec(self, **overrides: object) -> dict:
        return {
            "version": "finance_spec.v1",
            "revenue": _rail_revenue(**overrides),
            "cost": {"cost_items": {"a": 1.0, "b": 2.0, "c": 3.0}},
        }

    def test_valid_rail_spec_passes(self) -> None:
        valid, errors = validate(self._spec())
        self.assertTrue(valid, errors)

    def test_missing_ridership_is_reported(self) -> None:
        valid, errors = validate(self._spec(annual_passenger_trips=0.0))
        self.assertFalse(valid)
        self.assertTrue(
            any("annual_passenger_trips" in str(e) for e in errors), errors
        )

    def test_missing_clearing_fare_is_reported(self) -> None:
        valid, errors = validate(self._spec(average_fare_yuan=0.0))
        self.assertFalse(valid)
        self.assertTrue(any("average_fare_yuan" in str(e) for e in errors), errors)

    def test_missing_ramp_is_reported(self) -> None:
        spec = self._spec()
        spec["revenue"].pop("ridership_ramp")
        valid, errors = validate(spec)
        self.assertFalse(valid)
        self.assertTrue(any("ridership_ramp" in str(e) for e in errors), errors)

    def test_negative_fiscal_support_is_rejected(self) -> None:
        valid, _ = validate(self._spec(annual_fiscal_support_wan=-1.0))
        self.assertFalse(valid)

    def test_bad_non_fare_scenario_is_rejected(self) -> None:
        valid, _ = validate(self._spec(non_fare_scenario="whatever"))
        self.assertFalse(valid)


class RailTransitDeliveryTableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-rail-revenue-")
        self.previous_data_dir = os.environ.get("LVKE_MCP_DATA_DIR")
        self.previous_golden_root = os.environ.get("LVKE_GOLDEN_DATA_ROOT")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        os.environ["LVKE_GOLDEN_DATA_ROOT"] = str(
            Path(__file__).resolve().parents[2] / "docs"
        )
        self.workspace = "rail-revenue-test"
        self.scenario = next(
            item
            for item in build_industry_scenarios("transport_logistics")
            if item["archetype_id"] == "urban_rail" and item["variant_id"] == "base"
        )

    def tearDown(self) -> None:
        for name, previous in (
            ("LVKE_MCP_DATA_DIR", self.previous_data_dir),
            ("LVKE_GOLDEN_DATA_ROOT", self.previous_golden_root),
        ):
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self.tempdir.cleanup()

    def test_urban_rail_scenario_uses_the_rail_model(self) -> None:
        revenue = self.scenario["spec"]["revenue"]
        self.assertEqual(revenue["model"], "rail_transit")
        self.assertGreater(revenue["annual_passenger_trips"], 0)
        self.assertGreater(revenue["average_fare_yuan"], 0)

    def _render(self) -> dict:
        result = run_model(
            {
                "workspace_id": self.workspace,
                "spec": self.scenario["spec"],
                "input_revision": self.scenario["finance"],
                "mode": "estimate_preview",
                "idempotency_key": "rail-revenue-run",
            }
        )
        self.assertTrue(result["success"], result)
        return tables_service.render(self.workspace, result["run_id"])

    def test_income_product_tree_gate_is_satisfied(self) -> None:
        rendered = self._render()
        blockers = rendered.get("blockers") or []
        self.assertNotIn("workbook_semantic:income_product_tree", blockers)
        self.assertNotIn("workbook_semantic:income_formula_driven", blockers)

    def _product_tree(self) -> list[dict]:
        rendered = self._render()
        package = tables_service.get_table(
            self.workspace,
            rendered["finance_tables_package_id"],
            "income-statement",
        )
        return (package.get("content") or {}).get("product_tree") or []

    def test_product_tree_splits_three_revenue_categories(self) -> None:
        categories = [item.get("revenue_category") for item in self._product_tree()]
        self.assertIn("farebox", categories)
        self.assertIn("non_fare", categories)
        self.assertIn("fiscal_support", categories)

    def test_farebox_line_keeps_quantity_and_clearing_fare(self) -> None:
        farebox = next(
            item
            for item in self._product_tree()
            if item.get("revenue_category") == "farebox"
        )
        self.assertGreater(float(farebox["capacity"]), 0)
        self.assertGreater(float(farebox["price_per_unit"]), 0)
        self.assertGreater(len(farebox["ramp"]), 1)


if __name__ == "__main__":
    unittest.main()
