from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from lvke_mcp.servers.lvke_asset_acquisition import server as acquisition_server
from lvke_mcp.servers.lvke_asset_acquisition import service
from lvke_mcp.servers.lvke_zero_material_delivery._service.orchestration import (
    _solar_acquisition_spec,
)

_CONTROLLED_ASSUMPTION_FIELDS = (
    "field", "value", "unit", "basis", "impact", "sensitivity", "validation_condition",
)


def _preview_spec() -> dict:
    """A controlled-assumption solar acquisition candidate, not a formal input."""
    return copy.deepcopy(
        _solar_acquisition_spec(
            {"sentence": "江夏光伏资产收购"},
            {"fields": []},
        )
    )


class AcquisitionPreviewContractTest(unittest.TestCase):
    """A controlled-assumption preview must be declarable through the public schema.

    The domain already routed ``delivery_mode=estimate_preview`` past
    ``validate_for_formal``, but the published spec schema never declared the
    field, so no caller could reach that branch and every controlled-assumption
    candidate died at confirm time with ``SPEC_VALIDATION_FAILED``.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = acquisition_server.build_server()

    def _tool_errors(self, tool: str, args: dict) -> list[str]:
        schema = self.server._tools[tool].input_schema
        return [error.message for error in Draft202012Validator(schema).iter_errors(args)]

    def test_public_schema_accepts_declared_preview_markers(self) -> None:
        self.assertEqual(self._tool_errors("acquisition_validate_spec", {"spec": _preview_spec()}), [])

    def test_public_schema_rejects_unknown_delivery_mode(self) -> None:
        spec = _preview_spec()
        spec["delivery_mode"] = "formal_release"
        self.assertTrue(self._tool_errors("acquisition_validate_spec", {"spec": spec}))

    def test_public_schema_requires_every_controlled_assumption_field(self) -> None:
        spec = _preview_spec()
        spec["controlled_assumptions"][0].pop("validation_condition")
        self.assertTrue(self._tool_errors("acquisition_validate_spec", {"spec": spec}))

    def test_validate_reports_preview_eligibility_without_formal_release(self) -> None:
        result = service.validate_spec(_preview_spec())
        self.assertTrue(result["valid"])
        self.assertTrue(result["preview_eligible"])
        self.assertEqual(result["delivery_mode"], "estimate_preview")
        self.assertFalse(result["formal_valid"])
        self.assertFalse(result["formal_release_eligible"])
        self.assertFalse(result["project_fact_certified"])

    def test_validate_offers_the_preview_route_when_formal_inputs_are_missing(self) -> None:
        spec = _preview_spec()
        spec.pop("delivery_mode")
        spec.pop("controlled_assumptions")
        result = service.validate_spec(spec)
        self.assertTrue(result["valid"])
        self.assertFalse(result["preview_eligible"])
        self.assertFalse(result["formal_release_eligible"])
        self.assertTrue(
            any("delivery_mode=estimate_preview" in item for item in result["next_actions"]),
            result["next_actions"],
        )


class AcquisitionPreviewLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = tempfile.TemporaryDirectory(prefix="lvke-acquisition-preview-")
        self.previous_data = os.environ.get("LVKE_MCP_DATA_DIR")
        self.previous_deliverable = os.environ.get("LVKE_DELIVERABLE_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.data.name
        os.environ["LVKE_DELIVERABLE_DIR"] = str(Path(self.data.name) / "deliverables")
        self.workspace = "acquisition-preview"

    def tearDown(self) -> None:
        for name, previous in (
            ("LVKE_MCP_DATA_DIR", self.previous_data),
            ("LVKE_DELIVERABLE_DIR", self.previous_deliverable),
        ):
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self.data.cleanup()

    def _confirmed(self, spec: dict) -> dict:
        saved = service.save_spec(self.workspace, spec, "preview-save")
        self.assertTrue(saved.get("spec_id"), saved)
        return service.confirm_spec(
            self.workspace, str(saved["spec_id"]), "受控假设技术预览", "preview-confirm",
            confirmation_scope="project_candidate",
        )

    def test_preview_candidate_confirms_and_runs_without_formal_eligibility(self) -> None:
        confirmed = self._confirmed(_preview_spec())
        self.assertTrue(confirmed["success"], confirmed)
        self.assertEqual(confirmed["confirmation_scope"], "estimate_preview")
        self.assertFalse(confirmed["formal_release_eligible"])
        self.assertTrue(confirmed["warnings"])

        run = service.run_model(
            self.workspace, str(confirmed["spec_id"]), 0.08, "base", "preview-run",
        )
        self.assertTrue(run["success"], run)
        self.assertEqual(run["delivery_mode"], "estimate_preview")
        self.assertEqual(run["model_version"], "acquisition_model.solar.v1")
        self.assertFalse(run["formal_spec_valid"])
        self.assertTrue(run["warnings"])

    def test_preview_run_still_refuses_formal_artifacts(self) -> None:
        confirmed = self._confirmed(_preview_spec())
        run = service.run_model(
            self.workspace, str(confirmed["spec_id"]), 0.08, "base", "preview-run",
        )
        rejected = service.generate_artifact(
            self.workspace, str(run["run_id"]), "preview-artifact",
        )
        self.assertFalse(rejected["success"])
        self.assertFalse(rejected["business_success"])
        self.assertTrue(rejected["system_success"])
        self.assertEqual(rejected["code"], "FORMAL_ARTIFACT_QUALIFICATION_REQUIRED")

    def test_incomplete_controlled_assumptions_stay_blocked(self) -> None:
        spec = _preview_spec()
        for item in spec["controlled_assumptions"]:
            item.pop("validation_condition", None)
        blocked = self._confirmed(spec)
        self.assertFalse(blocked["success"])
        self.assertEqual(blocked["code"], "SPEC_VALIDATION_FAILED")


if __name__ == "__main__":
    unittest.main()
