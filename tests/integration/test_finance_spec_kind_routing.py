from __future__ import annotations

import unittest

from jsonschema import Draft202012Validator

from lvke_mcp.domains.finance.parameter_resolver import finance_spec_candidate_schema
from lvke_mcp.domains.finance.spec import (
    ACQUISITION_FINANCE_KIND,
    GENERIC_FINANCE_KIND,
    infer_finance_kind,
    migrate_spec_to_v3,
    validate_for_formal,
)
from lvke_mcp.servers.lvke_finance_model.server import build_server


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


class FinanceSpecKindPublicContractTest(unittest.TestCase):
    """The discriminator must be reachable through the published tool schemas.

    Domain routing was correct while the exported候选 schema still omitted
    ``finance_kind`` under ``additionalProperties: False``, so clients could
    never declare it and every v3 spec fell back to inference.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = finance_spec_candidate_schema()
        cls.server = build_server()

    def _tool_errors(self, tool: str, args: dict[str, object]) -> list[str]:
        schema = self.server._tools[tool].input_schema
        return [error.message for error in Draft202012Validator(schema).iter_errors(args)]

    def test_candidate_schema_declares_finance_kind_enum(self) -> None:
        node = self.schema["properties"].get("finance_kind")
        self.assertIsNotNone(node, "对外候选 schema 必须声明 finance_kind")
        self.assertEqual(
            sorted(node["enum"]),
            sorted([GENERIC_FINANCE_KIND, ACQUISITION_FINANCE_KIND]),
        )

    def test_candidate_schema_still_rejects_unknown_fields(self) -> None:
        self.assertFalse(self.schema["additionalProperties"])
        errors = [
            error.message
            for error in Draft202012Validator(self.schema).iter_errors(
                {"revenue": {}, "definitely_not_a_field": 1}
            )
        ]
        self.assertTrue(errors)

    def test_generic_v3_passes_every_spec_carrying_tool_schema(self) -> None:
        spec = {
            "version": "finance_spec.v3",
            "finance_kind": GENERIC_FINANCE_KIND,
            "revenue": {"model": "gov_payment", "annual_gov_payment_wan": 25900},
        }
        cases = {
            "finance_validate_spec": {"spec": spec},
            "finance_prepare_spec": {"workspace_id": "w1", "spec": spec},
            "finance_run_model": {
                "workspace_id": "w1",
                "spec": spec,
                "idempotency_key": "k" * 10,
            },
        }
        for tool, args in cases.items():
            with self.subTest(tool=tool):
                self.assertEqual(self._tool_errors(tool, args), [])

    def test_acquisition_v3_passes_the_same_tool_schemas(self) -> None:
        spec = {
            "version": "finance_spec.v3",
            "finance_kind": ACQUISITION_FINANCE_KIND,
            "asset_type": "hotel_lease",
            "revenue": {"model": "lease_portfolio"},
        }
        self.assertEqual(
            self._tool_errors("finance_prepare_spec", {"workspace_id": "w1", "spec": spec}),
            [],
        )

    def test_v2_specs_remain_byte_compatible(self) -> None:
        spec = {"version": "finance_spec.v2", "revenue": {"model": "product_sales"}}
        self.assertEqual(
            self._tool_errors("finance_prepare_spec", {"workspace_id": "w1", "spec": spec}),
            [],
        )


if __name__ == "__main__":
    unittest.main()
