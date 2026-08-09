from __future__ import annotations

import unittest

from lvke_mcp.domains.finance.spec import (
    ACQUISITION_FINANCE_KIND,
    GENERIC_FINANCE_KIND,
    infer_finance_kind,
    migrate_spec_to_v3,
    validate_for_formal,
)


def _base_spec(**overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "version": "finance_spec.v3",
        "confirmation_status": "confirmed",
        "selected_scenario_id": "base",
        "revenue": {},
        "cost": {},
        "tax": {},
    }
    spec.update(overrides)
    return spec


class FinanceSpecKindRoutingTest(unittest.TestCase):
    """A v3 spec is not an acquisition merely because it is v3.

    Routing on ``version`` alone made every v3 spec default to
    ``asset_type=hotel_lease``, so a city-rail feasibility spec was asked for
    guest rooms, ADR, lease units, buyer/seller parties and historical
    statements at confirm time.  The discriminator is ``finance_kind``.
    """

    def test_generic_v3_does_not_load_acquisition_validators(self) -> None:
        rail = _base_spec(
            selected_scenario_id="transport_logistics.urban_rail.base",
            revenue={"model": "gov_payment", "annual_gov_payment_wan": 25900},
            cost={"total_cost_rate": 0.55},
        )
        self.assertEqual(infer_finance_kind(rail), GENERIC_FINANCE_KIND)

        ok, errors = validate_for_formal(rail)
        self.assertTrue(ok, f"generic v3 should pass formal validation: {errors}")
        joined = " ".join(errors)
        for forbidden in ("hotel_operation", "lease_portfolio", "transaction", "project_parties"):
            self.assertNotIn(forbidden, joined)

    def test_generic_v3_migration_does_not_inject_acquisition_skeleton(self) -> None:
        migrated = migrate_spec_to_v3(
            {"version": "finance_spec.v2", "revenue": {}, "cost": {}, "tax": {}}
        )
        self.assertEqual(migrated["finance_kind"], GENERIC_FINANCE_KIND)
        for key in ("asset_type", "hotel_operation", "lease_portfolio", "transaction"):
            self.assertNotIn(key, migrated)

    def test_hotel_v3_still_fails_closed_on_its_own_domain(self) -> None:
        hotel = _base_spec(asset_type="hotel_lease")
        self.assertEqual(infer_finance_kind(hotel), ACQUISITION_FINANCE_KIND)

        ok, errors = validate_for_formal(hotel)
        self.assertFalse(ok)
        joined = " ".join(errors)
        self.assertIn("hotel_operation", joined)

    def test_solar_v3_still_fails_closed_on_its_own_domain(self) -> None:
        solar = _base_spec(asset_type="solar_power")
        self.assertEqual(infer_finance_kind(solar), ACQUISITION_FINANCE_KIND)

        ok, errors = validate_for_formal(solar)
        self.assertFalse(ok)
        joined = " ".join(errors)
        self.assertIn("solar_operation", joined)
        self.assertNotIn("hotel_operation", joined)

    def test_explicit_finance_kind_overrides_inference(self) -> None:
        declared = _base_spec(finance_kind=ACQUISITION_FINANCE_KIND)
        self.assertEqual(infer_finance_kind(declared), ACQUISITION_FINANCE_KIND)

    def test_acquisition_payload_is_inferred_without_asset_type(self) -> None:
        # Legacy specs predate ``finance_kind``; real acquisition data must
        # still route to the acquisition validators.
        legacy = _base_spec(transaction={"purchase_price": 12000})
        self.assertEqual(infer_finance_kind(legacy), ACQUISITION_FINANCE_KIND)


if __name__ == "__main__":
    unittest.main()
