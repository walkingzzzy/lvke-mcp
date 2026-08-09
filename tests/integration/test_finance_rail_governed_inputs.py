from __future__ import annotations

import copy
import os
import tempfile
import unittest

from lvke_mcp.adapters.finance_model_repository import SPEC_STORE
from lvke_mcp.domains.finance import finance_model, revenue_models
from lvke_mcp.domains.finance._model_application.spec_cases import prepare_spec
from lvke_mcp.domains.finance._run_service.base import compute_input_hash, compute_spec_hash
from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
from lvke_mcp.domains.finance.model_application import run_model
from lvke_mcp.domains.finance.parameter_resolver import canonicalize_finance_inputs
from lvke_mcp.domains.finance.rail_validation import (
    rail_transit_missing_inputs,
    revenue_input_complete,
)
from lvke_mcp.domains.finance.spec import validate


def _growth_path() -> list[float]:
    values = [1.0]
    for year in range(2, 31):
        rate = 0.05 if year <= 6 else 0.02
        values.append(values[-1] * (1.0 + rate))
    return values


def _rail_scenario() -> dict:
    return copy.deepcopy(next(
        item
        for item in build_industry_scenarios("transport_logistics")
        if item["archetype_id"] == "urban_rail" and item["variant_id"] == "base"
    ))


class RailTransitGovernedInputTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-rail-governed-")
        self.previous_data_dir = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        if self.previous_data_dir is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous_data_dir
        self.tempdir.cleanup()

    def test_missing_passenger_unit_is_a_specific_missing_input(self) -> None:
        scenario = _rail_scenario()
        scenario["spec"]["revenue"].pop("passenger_unit")
        result = run_model({
            "workspace_id": "rail-missing-unit",
            "spec": scenario["spec"],
            "input_revision": scenario["finance"],
            "idempotency_key": "rail-missing-unit-key",
        })
        self.assertEqual(result["status"], "missing_inputs")
        self.assertIn("revenue.passenger_unit", result["missing_inputs"])
        self.assertIsNone(result["run_id"])

    def test_nested_finance_inputs_revenue_is_validated_as_rail(self) -> None:
        scenario = _rail_scenario()
        nested_spec = {"finance_inputs": {"revenue": scenario["spec"]["revenue"]}}
        self.assertTrue(revenue_input_complete(nested_spec, scenario["finance"]))
        self.assertEqual(
            rail_transit_missing_inputs(
                nested_spec,
                scenario["finance"],
                build_period_months=scenario["build_period_months"],
            ),
            [],
        )

        nested_spec["finance_inputs"]["revenue"].pop("passenger_unit")
        self.assertIn(
            "revenue.passenger_unit",
            rail_transit_missing_inputs(nested_spec, scenario["finance"]),
        )

    def test_invalid_renewal_salvage_rate_is_rejected_without_exception(self) -> None:
        for value in ("not-a-number", None, True, 1.0, -0.01):
            with self.subTest(salvage_rate=value):
                normalized, _ledger, rejected = canonicalize_finance_inputs({
                    "renewal_capex_plan": [{
                        "year": 15,
                        "amount_wan": 1000.0,
                        "depreciation_years": 10,
                        "salvage_rate": value,
                    }],
                })
                self.assertNotIn("renewal_capex_plan", normalized)
                self.assertEqual(
                    [item["reason"] for item in rejected],
                    ["invalid_renewal_capex_plan"],
                )

    def test_rail_domain_inputs_never_fall_back_to_generic_defaults(self) -> None:
        field_cases = {
            "build_period_months": "build_period_months",
            "calc_period_years": "calc_period_years",
            "capital_own_ratio": "capital_own_ratio",
            "loan_ratio": "loan_ratio",
            "loan_rate": "loan_rate",
            "loan_years": "loan_years",
            "discount_rate": "discount_rate",
            "cost_items": "cost_items_or_operating_cost_by_year",
            "fiscal_support_policy": "fiscal_support_policy",
        }
        for index, (field, expected) in enumerate(field_cases.items()):
            with self.subTest(field=field):
                scenario = _rail_scenario()
                scenario["finance"].pop(field, None)
                result = run_model({
                    "workspace_id": f"rail-missing-{index}",
                    "spec": scenario["spec"],
                    "input_revision": scenario["finance"],
                    "idempotency_key": f"rail-required-{index}-key",
                })
                self.assertEqual(result["status"], "missing_inputs")
                self.assertIn(expected, result["missing_inputs"])
                self.assertIsNone(result["run_id"])

    def test_30_year_ridership_growth_is_not_truncated_at_1_5(self) -> None:
        ramp = _growth_path()
        spec = {
            "version": "finance_spec.v2",
            "revenue": {
                "model": "rail_transit",
                "annual_passenger_trips": 120_000 * 365,
                "passenger_unit": "人次",
                "average_fare_yuan": 3.0,
                "ridership_ramp": ramp,
                "non_fare_scenario": "base",
                "annual_fiscal_support_wan": 0.0,
            },
            "cost": {"cost_items": {"运营维护": 1.0}},
        }
        valid, errors = validate(spec)
        self.assertTrue(valid, errors)
        expanded = revenue_models.expand(spec, 30)
        self.assertEqual(len(expanded["ridership_ramp"]), 30)
        self.assertGreater(expanded["ridership_ramp"][-1], 1.5)
        self.assertAlmostEqual(expanded["ridership_ramp"][-1], 1.05**5 * 1.02**24)
        self.assertEqual(expanded["annual_passenger_trips_persons"], 43_800_000)
        self.assertEqual(expanded["farebox_by_year"][0], 13_140.0)
        self.assertEqual(
            expanded["farebox_by_year"][-1],
            round(13_140.0 * ramp[-1], 2),
        )

    def test_fare_multipliers_only_change_fare_linked_revenue(self) -> None:
        spec = {
            "revenue": {
                "model": "rail_transit",
                "annual_passenger_trips": 10_000_000,
                "passenger_unit": "人次",
                "average_fare_yuan": 4.0,
                "ridership_ramp": [1.0, 1.0, 1.0],
                "fare_multiplier_by_year": [1.0, 1.1, 1.2],
                "non_fare_scenario": "base",
                "annual_fiscal_support_wan": 100.0,
                "fiscal_support_ramp": [1.0],
            }
        }
        expanded = revenue_models.expand(spec, 3)
        self.assertEqual(expanded["farebox_by_year"], [4000.0, 4400.0, 4800.0])
        self.assertEqual(expanded["non_fare_by_year"], [400.0, 440.0, 480.0])
        self.assertEqual(expanded["fiscal_support_by_year"], [100.0, 100.0, 100.0])

    def test_renewal_capex_hits_cash_depreciation_and_terminal_book_value(self) -> None:
        scenario = _rail_scenario()
        scenario["finance"]["renewal_capex_plan"] = [{
            "year": 15,
            "name": "信号系统更新",
            "amount_wan": 1000.0,
            "depreciation_years": 10,
            "salvage_rate": 0.0,
        }]
        result = finance_model.compute_financials(
            scenario["finance"],
            build_period_months=scenario["build_period_months"],
            spec=scenario["spec"],
        )
        renewal = result["raw"]["renewal_capex_schedule"]
        self.assertEqual(renewal["capex_by_year"][14], 1000.0)
        self.assertEqual(renewal["depreciation_by_year"][14], 100.0)
        self.assertEqual(renewal["terminal_book_value_wan"], 600.0)
        operation_rows = [
            row for row in result["annual"]["project_cashflow"]
            if row.get("phase") == "运营期"
        ]
        self.assertEqual(operation_rows[14]["renewal_capex"], 1000.0)
        self.assertEqual(operation_rows[14]["construction"], 1000.0)
        self.assertEqual(
            result["raw"]["terminal_meta"]["renewal_terminal_book_value"],
            600.0,
        )
        self.assertEqual(result["benchmark_rate"], 0.05)
        self.assertEqual(
            [item["discount_rate"] for item in result["discount_rate_scenarios"]],
            [0.04, 0.05, 0.06],
        )
        self.assertEqual(
            result["project_metadata"]["scenario_id"],
            "transport_logistics.urban_rail.base",
        )
        blocking = [
            item for item in finance_model.check_consistency(result)
            if not item.get("ok") and item.get("blocking", True)
        ]
        self.assertEqual(blocking, [])

    def test_gap_support_covers_only_the_cash_and_debt_service_gap(self) -> None:
        scenario = _rail_scenario()
        scenario["finance"]["fiscal_support_policy"] = {
            "mode": "actual_cash_and_debt_service_gap",
            "include_debt_service": True,
        }
        result = finance_model.compute_financials(
            scenario["finance"],
            build_period_months=scenario["build_period_months"],
            spec=scenario["spec"],
        )
        schedule = result["raw"]["fiscal_support_by_year"]
        self.assertTrue(any(value > 0 for value in schedule))
        for row in result["annual"]["debt_service"]:
            due = float(row.get("principal") or 0.0) + float(row.get("interest") or 0.0)
            expected = max(due - float(row.get("repay_available_before_support") or 0.0), 0.0)
            self.assertAlmostEqual(float(row.get("fiscal_support") or 0.0), expected, places=2)
            if due > 0:
                self.assertLessEqual(float(row.get("repay_surplus") or 0.0), 0.01)
        profit_revenue = [
            row["revenue"] for row in result["annual"]["profit_distribution"]
        ]
        self.assertLess(max(profit_revenue), scenario["spec"]["revenue"]["annual_revenue_wan"])
        operation_plan = [
            row for row in result["annual"]["financial_plan"]
            if row.get("phase") == "运营期"
        ]
        self.assertEqual(
            [row["finance_in"] for row in operation_plan],
            schedule,
        )

    def test_prepare_reuses_the_original_confirmed_spec_identity(self) -> None:
        workspace_id = "confirmed-spec-reuse"
        scenario = _rail_scenario()
        spec = scenario["spec"]
        inputs, _ledger, rejected = canonicalize_finance_inputs(scenario["finance"])
        self.assertEqual(rejected, [])
        spec_hash = compute_spec_hash(spec)
        input_hash = compute_input_hash(
            inputs,
            invest_type="",
            build_period_months=inputs["build_period_months"],
            industry="",
        )
        candidate = SPEC_STORE.put(
            workspace_id,
            {
                "spec": spec,
                "spec_hash": spec_hash,
                "input_revision": inputs,
                "input_hash": input_hash,
                "confirmation_status": "candidate",
                "parent_object_ids": [],
            },
            producer="test.prepare",
        )
        confirmed = SPEC_STORE.put(
            workspace_id,
            {
                "spec": spec,
                "spec_hash": spec_hash,
                "input_revision": inputs,
                "input_hash": input_hash,
                "confirmation_status": "confirmed",
                "parent_spec_id": candidate["object_id"],
                "parent_object_ids": [candidate["object_id"]],
            },
            producer="test.confirm",
            source_ids=[candidate["object_id"]],
        )
        prepared = prepare_spec({
            "workspace_id": workspace_id,
            "strategy": "reuse_confirmed",
        })
        self.assertTrue(prepared["success"], prepared)
        self.assertEqual(prepared["spec_id"], confirmed["object_id"])
        self.assertTrue(prepared["data"]["reused_confirmed"])
        self.assertEqual(prepared["spec_hash"], spec_hash)

    def test_invalid_confirmed_hash_or_lineage_is_not_reused(self) -> None:
        workspace_id = "confirmed-spec-invalid"
        scenario = _rail_scenario()
        invalid = SPEC_STORE.put(
            workspace_id,
            {
                "spec": scenario["spec"],
                "spec_hash": "sha256:invalid",
                "input_revision": scenario["finance"],
                "input_hash": "sha256:invalid",
                "confirmation_status": "confirmed",
                "parent_spec_id": "fsp_missing_parent",
                "parent_object_ids": ["fsp_missing_parent"],
            },
            producer="test.invalid-confirm",
            source_ids=["fsp_missing_parent"],
        )
        prepared = prepare_spec({
            "workspace_id": workspace_id,
            "strategy": "reuse_confirmed",
        })
        self.assertFalse(prepared["success"])
        self.assertIsNone(prepared.get("spec_id"))
        self.assertNotIn(invalid["object_id"], prepared.get("resource_uris") or [])

    def test_coerce_never_downgrades_rail_to_flat_to_skip_the_gate(self) -> None:
        """城轨缺客流/票价时 coerce 不得改写 model。

        改写成 flat 会让 rail_transit_missing_inputs() 的判别式失配而返回空，
        整套城轨必填门禁被静默跳过，最终产出一个"成功"但无客流依据的 Run。
        """

        from lvke_mcp.domains.finance.spec import coerce_llm_spec

        incomplete = {"revenue": {"model": "rail_transit", "passenger_unit": "万人次"}}
        # requirement 提供已知营收，是历史上触发 flat 回退的那条路径。
        coerced = coerce_llm_spec(
            copy.deepcopy(incomplete), {"finance": {"annual_revenue_wan": 5000.0}}
        )

        self.assertEqual("rail_transit", (coerced.get("revenue") or {}).get("model"))
        missing = rail_transit_missing_inputs(coerced, {})
        for field in (
            "revenue.annual_passenger_trips",
            "revenue.average_fare_yuan",
            "revenue.ridership_ramp",
            "fiscal_support_policy",
        ):
            self.assertIn(field, missing)

        # 即便 input_revision 自带营收让通用完整性判定短路，城轨门禁仍须独立报缺。
        self.assertTrue(revenue_input_complete(coerced, {"annual_revenue_wan": 5000.0}))
        self.assertEqual(
            missing, rail_transit_missing_inputs(coerced, {"annual_revenue_wan": 5000.0})
        )

    def test_non_rail_models_still_fall_back_to_flat(self) -> None:
        """flat 回退是其他行业的正常容错，收紧城轨不得波及它们。"""

        from lvke_mcp.domains.finance.spec import coerce_llm_spec

        for model in ("product_sales", "property_sales", "tourism"):
            with self.subTest(model=model):
                coerced = coerce_llm_spec(
                    {"revenue": {"model": model}},
                    {"finance": {"annual_revenue_wan": 5000.0}},
                )
                revenue = coerced.get("revenue") or {}
                self.assertEqual("flat", revenue.get("model"))
                self.assertEqual(5000.0, revenue.get("annual_revenue_wan"))


if __name__ == "__main__":
    unittest.main()
