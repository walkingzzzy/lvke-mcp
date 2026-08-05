from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.domains.finance import revenue_models
from lvke_mcp.domains.finance.hengli_reference import scenario_matrix
from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
from lvke_mcp.domains.finance.model_application import run_model
from lvke_mcp.domains.finance.run_service import DELIVERY_TABLE_KEYS
from lvke_mcp.domains.finance import tables_service


class FinanceFixtureAcceptanceTest(unittest.TestCase):
    """Replay the customer sample shapes through the real MCP application APIs.

    The fixture inputs are explicitly synthetic technical fixtures. They verify
    model wiring and table contracts, not project evidence or a formal release.
    """

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-finance-fixture-")
        self.previous_data_dir = os.environ.get("LVKE_MCP_DATA_DIR")
        self.previous_golden_root = os.environ.get("LVKE_GOLDEN_DATA_ROOT")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        os.environ["LVKE_GOLDEN_DATA_ROOT"] = str(Path(__file__).resolve().parents[2] / "docs")
        self.workspace = "finance-fixture-test"

    def tearDown(self) -> None:
        if self.previous_data_dir is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous_data_dir
        if self.previous_golden_root is None:
            os.environ.pop("LVKE_GOLDEN_DATA_ROOT", None)
        else:
            os.environ["LVKE_GOLDEN_DATA_ROOT"] = self.previous_golden_root
        self.tempdir.cleanup()

    @staticmethod
    def _scenario(industry: str, archetype: str) -> dict:
        return copy.deepcopy(
            next(
                item
                for item in build_industry_scenarios(industry)
                if item["archetype_id"] == archetype and item["variant_id"] == "base"
            )
        )

    def _run(
        self,
        scenario: dict,
        key: str,
        *,
        expect_delivery_tables: bool = True,
    ) -> tuple[dict, dict | None]:
        result = run_model({
            "workspace_id": self.workspace,
            "spec": scenario["spec"],
            "input_revision": scenario["finance"],
            "mode": "estimate_preview",
            "idempotency_key": key,
        })
        self.assertTrue(result["success"], result)
        self.assertEqual(result["status"], "ok", result)
        run_id = result["run_id"]
        data = result["data"]
        self.assertTrue(data["available"], result)
        self.assertTrue(data["consistency_ok"], result)
        if expect_delivery_tables:
            self.assertTrue(set(DELIVERY_TABLE_KEYS).issubset(data["tables"]), scenario["scenario_id"])
        self.assertIsNotNone(data["annual"]["interest_during_construction"])
        self.assertIsNotNone(data["annual"]["working_capital"])
        if not expect_delivery_tables:
            return result, None
        technical = tables_service.validate(self.workspace, run_id, validation_scope="technical")
        self.assertTrue(technical["success"], technical)
        self.assertTrue(technical["validation"]["valid"], technical)
        package = tables_service.render(self.workspace, run_id)
        self.assertTrue(package["success"], package)
        self.assertEqual(package["run_id"], run_id)
        self.assertEqual(len(package["table_manifest"]), len(DELIVERY_TABLE_KEYS))
        return result, package

    def test_customer_project_shapes(self) -> None:
        cases = {
            "product_sales": self._scenario("agriculture_food", "grain_processing"),
            "real_estate": self._scenario("construction_real_estate", "residential"),
            "tourism": self._scenario("tourism_catering", "scenic_area"),
        }
        # The two customer examples are scheduled revenue contracts. Reuse the
        # same deterministic annual series as the source scenario so only the
        # revenue contract changes.
        lease = self._scenario("agriculture_food", "grain_processing")
        lease_years = lease["finance"]["calc_period_years"] - ((lease["build_period_months"] + 11) // 12)
        lease["spec"]["revenue"] = {
            "model": "lease_portfolio",
            "annual_schedule_wan": revenue_models.expand(lease["spec"], lease_years)["revenue_by_year"],
        }
        cases["factory_lease"] = lease

        inventory = self._scenario("construction_real_estate", "residential")
        inventory_years = inventory["finance"]["calc_period_years"] - ((inventory["build_period_months"] + 11) // 12)
        inventory["spec"]["revenue"] = {
            "model": "inventory_sales",
            "annual_schedule_wan": revenue_models.expand(inventory["spec"], inventory_years)["revenue_by_year"],
        }
        cases["cemetery_sale"] = inventory

        for key, scenario in cases.items():
            result, _package = self._run(scenario, f"fixture-{key}")
            indicators = result["data"]["indicators"]
            self.assertIsNotNone(indicators.get("project_irr_pct"), key)
            self.assertIsNotNone(result["data"]["annual"].get("capital_irr_pct"), key)

    def test_non_operating_funding_balance_is_explicit(self) -> None:
        scenario = self._scenario("government_public_service", "municipal_road")
        # Non-operating projects use an availability/payment contract rather
        # than an operating sales series; the fixture still needs a positive
        # nominal revenue input to pass the common MCP input contract.
        scenario["finance"]["annual_revenue_wan"] = 1.0
        result, _package = self._run(
            scenario,
            "fixture-non-operating",
            expect_delivery_tables=False,
        )
        balance = result["data"]["annual"]["non_operating_balance"]
        self.assertTrue(balance["balanced"])
        self.assertIn("sources", balance)
        self.assertIn("uses", balance)

    def test_hengli_reference_boundary_is_preserved(self) -> None:
        reference = scenario_matrix()
        self.assertTrue(reference["valid"], reference)
        self.assertEqual(
            [item["purchase_price_wan"] for item in reference["scenarios"]],
            [2000, 2200, 2400, 2600, 2800, 3000],
        )
        replay = reference["replay"]
        self.assertTrue(replay["complete_project_track"])
        self.assertFalse(replay["complete"])
        self.assertIn("independent_corrected_track_required", reference)


if __name__ == "__main__":
    unittest.main()
