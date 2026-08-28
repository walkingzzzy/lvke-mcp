from __future__ import annotations

import unittest

from lvke_mcp.domains.asset_acquisition._model.monthly_engine import _run_monthly_acquisition_model
from lvke_mcp.domains.finance import assets as fin_assets
from lvke_mcp.domains.finance._finance_model.engine import (
    _apply_monthly_aggregates_to_annual,
    _monthly_detail_from_annual,
)


class P4MonthlyTaxBalanceTest(unittest.TestCase):
    def test_ddb_declines_by_year_and_tax_book_split(self) -> None:
        year_one = fin_assets.depreciation_charge(
            1200.0, 4, salvage_rate=0.0, method="double_declining",
            year_index=1, tax_method="straight_line",
        )
        year_two = fin_assets.depreciation_charge(
            1200.0, 4, salvage_rate=0.0, method="double_declining",
            year_index=2, tax_method="straight_line",
        )
        self.assertGreater(year_one["book_depreciation_wan"], year_two["book_depreciation_wan"])
        self.assertEqual(year_one["tax_depreciation_wan"], year_two["tax_depreciation_wan"])
        self.assertNotEqual(year_one["temporary_difference_wan"], 0.0)

    def test_classified_schedule_exposes_tax_depreciation(self) -> None:
        schedule = fin_assets.classified_depreciation_schedule(
            [{
                "name": "设备",
                "original_value_wan": 800.0,
                "depreciation_years": 4,
                "salvage_rate": 0.0,
                "depreciation_method": "double_declining",
                "tax_depreciation_method": "straight_line",
            }],
            op_years=4,
        )
        self.assertIn("tax_depreciation", schedule["rows"][0])
        self.assertNotEqual(schedule["rows"][0]["temporary_difference"], 0.0)

    def test_monthly_detail_rolls_tax_and_does_not_divide_construction(self) -> None:
        result = {
            "params": {"build_years": 1},
            "timeline": {
                "mode": "monthly",
                "monthly_periods": [
                    {"year_index": 1, "month_index": month, "phase": "建设期"}
                    for month in range(1, 13)
                ] + [
                    {"year_index": 2, "month_index": month, "phase": "运营期"}
                    for month in range(1, 13)
                ],
            },
            "annual": {
                "financial_plan": [
                    {"phase": "建设期", "construction_investment": 120.0},
                    {"phase": "运营期"},
                ],
                "income_statement": [{
                    "revenue": 1200.0,
                    "operating_cost": 600.0,
                    "depreciation": 120.0,
                    "tax_depreciation": 120.0,
                    "income_tax_rate": 0.25,
                }],
            },
        }
        detail = _monthly_detail_from_annual(result)
        build = [row for row in detail if row["year_index"] == 1]
        operate = [row for row in detail if row["year_index"] == 2]
        self.assertEqual(len(build), 12)
        self.assertEqual(sum(row["revenue_wan"] for row in build), 0.0)
        self.assertAlmostEqual(sum(row["construction_investment_wan"] for row in build), 120.0)
        self.assertAlmostEqual(sum(row["revenue_wan"] for row in operate), 1200.0)
        self.assertAlmostEqual(sum(row["income_tax_wan"] for row in operate), 120.0)
        _apply_monthly_aggregates_to_annual(result, detail)
        self.assertTrue(result["annual"]["monthly_sourced"])
        self.assertAlmostEqual(result["annual"]["income_statement"][0]["income_tax"], 120.0)

    def test_monthly_loop_honors_period_overrides_not_year_average(self) -> None:
        result = {
            "params": {"build_years": 1},
            "timeline": {
                "mode": "monthly",
                "monthly_periods": [
                    {
                        "year_index": 1,
                        "month_index": month,
                        "phase": "建设期",
                        "construction_investment_wan": 70.0 if month == 1 else 0.0,
                    }
                    for month in range(1, 13)
                ],
            },
            "annual": {
                "financial_plan": [{"phase": "建设期", "construction_investment": 120.0}],
                "income_statement": [],
            },
        }
        detail = _monthly_detail_from_annual(result)
        self.assertAlmostEqual(detail[0]["construction_investment_wan"], 70.0)
        self.assertAlmostEqual(sum(row["construction_investment_wan"] for row in detail[1:]), 0.0)
        _apply_monthly_aggregates_to_annual(result, detail)
        self.assertAlmostEqual(result["annual"]["financial_plan"][0]["construction_investment"], 70.0)

    def test_acquisition_balance_sheet_rolls_from_engine(self) -> None:
        spec = {
            "confirmation_status": "confirmed",
            "operating_mode": "owner_lessor",
            "transaction": {
                "model_start_date": "2026-01-01",
                "closing_date": "2026-01-01",
                "operating_mode": "owner_lessor",
                "purchase_price": 1000.0,
                "transaction_taxes": {"deed": 30.0},
                "financing_ratio": 0.4,
                "interest_rate": 0.05,
                "tenor": 2,
                "exit_year": 2,
                "repayment": "equal_principal",
            },
            "lease_portfolio": {
                "projection_years": 2,
                "units": [{
                    "start_date": "2026-01-01",
                    "end_date": "2027-12-31",
                    "base_rent_wan": 240.0,
                    "pricing_unit": "annual_total",
                }],
            },
            "tax": {"income_tax_rate": 0.25, "vat_rate": 0.06, "surtax_rate": 0.12},
            "cost": {"annual_owner_operating_cost_wan": 24.0},
        }
        result = _run_monthly_acquisition_model(spec, discount_rate=0.08, scenario_id="base")
        annual = result["annual_summary"]
        self.assertTrue(annual)
        last = annual[-1]
        self.assertGreater(last["cash_wan"] + last["fixed_asset_net_wan"], 0.0)
        self.assertEqual(last.get("year"), 2)
        self.assertIn("total_assets_wan", last)
        self.assertIn("debt_wan", last)
        if last.get("vat_wan"):
            self.assertGreater(last["vat_wan"], 0.0)

    def test_acquisition_vat_and_loss_carryforward_follow_declared_rates(self) -> None:
        from lvke_mcp.domains.asset_acquisition._tables.build import _build_tables

        taxed = {
            "confirmation_status": "confirmed",
            "operating_mode": "owner_lessor",
            "transaction": {
                "model_start_date": "2026-01-01",
                "closing_date": "2026-01-01",
                "operating_mode": "owner_lessor",
                "purchase_price": 800.0,
                "financing_ratio": 0.5,
                "interest_rate": 0.05,
                "tenor": 2,
                "exit_year": 2,
                "repayment": "equal_principal",
            },
            "lease_portfolio": {
                "projection_years": 2,
                "units": [{
                    "start_date": "2026-01-01",
                    "end_date": "2027-12-31",
                    "base_rent_wan": 180.0,
                    "pricing_unit": "annual_total",
                }],
            },
            "tax": {"income_tax_rate": 0.25, "vat_rate": 0.06, "surtax_rate": 0.12},
            "cost": {"annual_owner_operating_cost_wan": 12.0},
        }
        taxed_result = _run_monthly_acquisition_model(taxed, discount_rate=0.08, scenario_id="vat")
        self.assertGreater(sum(row["vat_wan"] for row in taxed_result["annual_summary"]), 0.0)
        untaxed = dict(taxed)
        untaxed["tax"] = {"income_tax_rate": 0.0, "vat_rate": 0.0, "surtax_rate": 0.0}
        untaxed_result = _run_monthly_acquisition_model(untaxed, discount_rate=0.08, scenario_id="no-vat")
        self.assertEqual(sum(row["vat_wan"] for row in untaxed_result["annual_summary"]), 0.0)
        tables = _build_tables(
            {"run_id": "acq-test", "result": taxed_result},
            taxed,
        )
        last = tables["balance_sheet"][-1]
        self.assertIsNotNone(last["cash_wan"])
        self.assertIsNotNone(last["fixed_asset_net_wan"])
        self.assertIsNotNone(last["total_assets_wan"])
        self.assertIsNotNone(last["debt_wan"])
        self.assertIsNotNone(last["equity_wan"])
        self.assertIsNotNone(last["total_liabilities_equity_wan"])

    def test_v2_annual_hotel_emits_balance_sheet_fields(self) -> None:
        from lvke_mcp.domains.asset_acquisition._model.entry import run_acquisition_model
        from lvke_mcp.domains.asset_acquisition._model.balance_sheet import projection_consistency_ok
        from lvke_mcp.domains.asset_acquisition._tables.build import _build_tables

        spec = {
            "version": "finance_spec.v3",
            "confirmation_status": "confirmed",
            "asset_type": "hotel_lease",
            "calculation_granularity": "annual",
            "revenue": {"model": "flat"},
            "transaction": {
                "acquisition_type": "asset",
                "model_start_date": "2026-01-01",
                "closing_date": "2026-01-01",
                "purchase_price": 800.0,
                "financing_ratio": 0.4,
                "interest_rate": 0.05,
                "tenor": 2,
                "exit_year": 2,
                "repayment": "equal_principal",
                "calculation_granularity": "annual",
            },
            "hotel_operation": {"rooms": 10, "adr": 400.0, "occupancy": 0.6},
            "lease_portfolio": {
                "projection_years": 2,
                "units": [{
                    "unit_id": "U1",
                    "start_date": "2026-01-01",
                    "end_date": "2027-12-31",
                    "base_rent_wan": 180.0,
                    "pricing_unit": "annual_total",
                }],
            },
            "tax": {"income_tax_rate": 0.25},
            "cost": {"annual_owner_operating_cost_wan": 12.0},
        }
        result = run_acquisition_model(spec, discount_rate=0.08, scenario_id="base")
        self.assertTrue(result.get("annual_summary"))
        self.assertTrue(projection_consistency_ok(result))
        tables = _build_tables({"run_id": "acq-v2", "result": result}, spec)
        last = tables["balance_sheet"][-1]
        self.assertIsNotNone(last["cash_wan"])
        self.assertIsNotNone(last["total_assets_wan"])
        self.assertIsNotNone(last["total_liabilities_equity_wan"])

    def test_solar_annual_emits_balance_sheet_fields(self) -> None:
        from lvke_mcp.domains.asset_acquisition._model.solar_engine import _run_solar_acquisition_model
        from lvke_mcp.domains.asset_acquisition._tables.build import _build_tables

        spec = {
            "asset_type": "solar_power",
            "confirmation_status": "confirmed",
            "transaction": {
                "model_start_date": "2026-01-01",
                "closing_date": "2026-01-01",
                "purchase_price": 2000.0,
                "financing_ratio": 0.5,
                "interest_rate": 0.05,
                "tenor": 2,
                "exit_year": 2,
                "repayment": "equal_principal",
            },
            "solar_operation": {
                "installed_capacity_mw": 10.0,
                "annual_generation_mwh": 12000.0,
                "tariff_yuan_per_kwh": 0.4,
                "remaining_operating_years": 2,
                "annual_opex_wan": 80.0,
            },
            "tax": {"income_tax_rate": 0.25},
        }
        result = _run_solar_acquisition_model(spec, discount_rate=0.08, scenario_id="solar")
        last = result["annual_summary"][-1]
        self.assertIsNotNone(last["cash_wan"])
        tables = _build_tables({"run_id": "acq-solar", "result": result}, spec)
        self.assertIsNotNone(tables["balance_sheet"][-1]["total_assets_wan"])

    def test_operating_turnover_defaults_fill_inventory(self) -> None:
        from lvke_mcp.domains.finance.working_capital import (
            ensure_operating_turnover,
            turnover_component_present,
        )

        filled, injected = ensure_operating_turnover(
            {"receivable": 20, "cash": 8, "payable": 15},
            is_operating=True,
        )
        self.assertIn("inventory", injected)
        self.assertTrue(turnover_component_present(filled, "inventory"))
        already, none = ensure_operating_turnover(
            {"inventory_detail": {"raw": {"days": 10, "annual_base_wan": 1}}},
            is_operating=True,
        )
        self.assertNotIn("inventory", none)
        self.assertTrue(turnover_component_present(already, "inventory"))
        from lvke_mcp.domains.finance.working_capital import needs_operating_turnover_defaults

        self.assertFalse(needs_operating_turnover_defaults({
            "is_operating": True,
            "invest_breakdown": {"working_capital_wan": 0.0},
        }))
        self.assertTrue(needs_operating_turnover_defaults({
            "is_operating": True,
            "invest_breakdown": {"working_capital_wan": 100.0},
        }))


if __name__ == "__main__":
    unittest.main()
