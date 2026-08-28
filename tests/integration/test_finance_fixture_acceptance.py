from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.adapters.spreadsheets._finance_export.base import _DELIVERY_SHEETS
from lvke_mcp.domains.finance import revenue_models
from lvke_mcp.domains.finance.hengli_reference import scenario_matrix
from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
from lvke_mcp.domains.finance.finance_model import check_consistency
from lvke_mcp.domains.finance.model_application import run_model
from lvke_mcp.domains.finance.run_service import (
    DELIVERY_TABLE_KEYS,
    ENGINE_DELIVERY_COUNT,
    REFERENCE_SOURCE_SHEET_COUNT,
    REVIEW_WORKBOOK_SHEET_COUNT,
    delivery_count_semantics,
    delivery_table_contract,
    delivery_table_contract_hash,
)
from lvke_mcp.domains.finance import tables_service
from lvke_mcp.domains.finance.working_capital import estimate_from_turnover


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

    def test_delivery_contract_separates_engine_and_reference_counts(self) -> None:
        contract = delivery_table_contract()
        counts = delivery_count_semantics()

        self.assertEqual(ENGINE_DELIVERY_COUNT, 13)
        self.assertEqual(len(DELIVERY_TABLE_KEYS), ENGINE_DELIVERY_COUNT)
        self.assertEqual(len(contract), ENGINE_DELIVERY_COUNT)
        self.assertEqual(
            [item["table_code"] for item in contract],
            list(DELIVERY_TABLE_KEYS),
        )
        self.assertEqual([item["order"] for item in contract], list(range(1, 14)))
        self.assertEqual(list(_DELIVERY_SHEETS), list(DELIVERY_TABLE_KEYS))
        self.assertEqual(
            list(_DELIVERY_SHEETS.values()),
            [item["delivery_no"] for item in contract],
        )
        self.assertTrue(all(item["required_columns"] for item in contract))
        self.assertTrue(all(item["formula_dependencies"] for item in contract))
        self.assertTrue(all(item["reconciliation_rules"] for item in contract))
        self.assertTrue(delivery_table_contract_hash().startswith("sha256:"))
        self.assertEqual(counts, {
            "engine_delivery_count": 13,
            "reference_source_sheet_count": 15,
            "review_workbook_sheet_count": 16,
        })
        self.assertEqual(REFERENCE_SOURCE_SHEET_COUNT, 15)
        self.assertEqual(REVIEW_WORKBOOK_SHEET_COUNT, 16)

        schema_path = (
            Path(__file__).resolve().parents[2]
            / "src/lvke_mcp/domains/finance/docs/reference_table_schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        reference_counts = schema["source"]["count_semantics"]
        self.assertEqual(reference_counts, {
            **counts,
            "note": reference_counts["note"],
        })
        self.assertEqual(len(schema["source"]["canonical_tables"]), 15)
        self.assertEqual(schema["source"]["workbook_provenance"]["sheet_count"], 16)

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
        self.assertIn(result["status"], {"ok", "partial"}, result)
        if result["status"] == "partial":
            self.assertTrue(result["data"].get("quality_issues"), result)
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
        self.assertEqual(package["engine_delivery_count"], ENGINE_DELIVERY_COUNT)
        self.assertEqual(package["reference_source_sheet_count"], 15)
        self.assertEqual(package["review_workbook_sheet_count"], 16)
        self.assertEqual(package["table_contract_hash"], delivery_table_contract_hash())
        self.assertEqual(len(package["table_manifest"]), ENGINE_DELIVERY_COUNT)
        self.assertEqual(
            [item["table_code"] for item in package["table_manifest"]],
            list(DELIVERY_TABLE_KEYS),
        )
        self.assertTrue(all(
            item["contract_hash"] == delivery_table_contract_hash()
            for item in package["table_manifest"]
        ))
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

    def test_table_9_uses_explicit_terminal_working_capital_recovery(self) -> None:
        for suffix, recovery in (("zero", 0.0), ("partial", 125.0)):
            scenario = self._scenario("agriculture_food", "grain_processing")
            scenario["finance"]["terminal_working_capital_recover_wan"] = recovery
            result, _package = self._run(
                scenario,
                f"fixture-terminal-wc-{suffix}",
            )
            data = result["data"]
            check = next(
                row
                for row in check_consistency(data)
                if row["rule"] == "附表9组成合计=净现金流"
            )
            self.assertTrue(check["ok"], check)
            terminal = data["annual"]["project_cashflow"][-1]
            expected = round(
                recovery + float(data["raw"].get("terminal_recovery") or 0.0),
                2,
            )
            self.assertEqual(terminal["recover"], expected)

    def test_working_capital_accepts_sealed_fact_pack_base_fields(self) -> None:
        result = estimate_from_turnover(
            revenue=99999.0,
            cash_cost=88888.0,
            turnover={
                "receivable": {"days": 60, "base_wan": 12544.340412},
                "cash": {"days": 30, "base_wan": 440.3592},
                "payable": {"days": 90, "base_wan": 7440.409324},
                "inventory_detail": {
                    "raw": {"days": 30, "base_wan": 7317.08412},
                    "fuel": {"days": 30, "base_wan": 123.3252},
                    "wip": {"days": 15, "base_wan": 8004.340416},
                    "finished": {"days": 15, "base_wan": 12544.340424},
                },
            },
        )
        self.assertEqual(result["net_working_capital"], 1743.55)
        self.assertEqual(result["bases"]["receivable"], 12544.340412)
        self.assertEqual(result["bases"]["payable"], 7440.409324)

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
