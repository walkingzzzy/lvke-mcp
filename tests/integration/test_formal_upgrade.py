"""验证正式版升级链：parent_run_id 链接、formal_grade 版本号、结构化 diff。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.domains.finance import tables_service
from lvke_mcp.domains.finance.formal_upgrade import promote_to_formal
from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
from lvke_mcp.domains.finance.model_application import run_model
from lvke_mcp.domains.finance.run_store import load_run
from lvke_mcp.servers.lvke_finance_model.server import build_server


class FormalUpgradeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-fu-")
        self.previous_data_dir = os.environ.get("LVKE_MCP_DATA_DIR")
        self.previous_golden_root = os.environ.get("LVKE_GOLDEN_DATA_ROOT")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        os.environ["LVKE_GOLDEN_DATA_ROOT"] = str(
            Path(__file__).resolve().parents[2] / "docs"
        )
        self.workspace = "fu-test"
        scenario = next(
            item
            for item in build_industry_scenarios("transport_logistics")
            if item["archetype_id"] == "urban_rail" and item["variant_id"] == "base"
        )
        self.scenario = scenario
        # First run (v1)
        result = run_model({
            "workspace_id": self.workspace,
            "spec": scenario["spec"],
            "input_revision": scenario["finance"],
            "mode": "estimate_preview",
            "idempotency_key": "fu-run-v1",
        })
        self.assertTrue(result["success"], result)
        self.v1_run_id = result["run_id"]
        # Render tables for v1
        tables_service.render(self.workspace, self.v1_run_id)

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

    def test_promote_to_formal_creates_linked_run(self) -> None:
        # Run a second computation (v2 candidate)
        result = run_model({
            "workspace_id": self.workspace,
            "spec": self.scenario["spec"],
            "input_revision": self.scenario["finance"],
            "mode": "estimate_preview",
            "idempotency_key": "fu-run-v2",
        })
        self.assertTrue(result["success"], result)
        v2_fin = result.get("data", {})

        # Promote to formal: link v2 -> v1
        promoted = promote_to_formal(
            self.workspace,
            prior_run_id=self.v1_run_id,
            new_fin=v2_fin,
        )
        self.assertTrue(promoted["success"], promoted)
        self.assertIsNotNone(promoted["run_id"])
        self.assertEqual(promoted["prior_run_id"], self.v1_run_id)
        self.assertEqual(promoted["formal_grade"], "v2")
        self.assertEqual(promoted["version_sequence"], 2)

        # Verify the persisted record has parent_run_id and formal_grade
        record = load_run(self.workspace, promoted["run_id"])
        self.assertIsNotNone(record)
        self.assertEqual(record.get("parent_run_id"), self.v1_run_id)
        self.assertEqual(record.get("formal_grade"), "v2")

    def test_promote_to_formal_produces_structured_diff(self) -> None:
        result = run_model({
            "workspace_id": self.workspace,
            "spec": self.scenario["spec"],
            "input_revision": self.scenario["finance"],
            "mode": "estimate_preview",
            "idempotency_key": "fu-run-v3",
        })
        self.assertTrue(result["success"], result)
        v3_fin = result.get("data", {})

        promoted = promote_to_formal(
            self.workspace,
            prior_run_id=self.v1_run_id,
            new_fin=v3_fin,
        )
        self.assertTrue(promoted["success"], promoted)
        diff = promoted.get("diff", {})
        self.assertIn("has_changes", diff)
        self.assertIn("indicators", diff)
        self.assertIn("assumptions", diff)
        self.assertIn("field_diff", diff)
        self.assertIn("prior_run_id", diff)
        self.assertIn("prior_formal_grade", diff)

    def test_promote_to_formal_fails_on_missing_prior(self) -> None:
        promoted = promote_to_formal(
            self.workspace,
            prior_run_id="nonexistent-run",
            new_fin={},
        )
        self.assertFalse(promoted["success"])
        self.assertEqual(promoted["code"], "prior_run_not_found")

    def test_promote_to_formal_mcp_rejects_missing_prior_with_valid_output(self) -> None:
        server = build_server()
        spec = server._tools["finance_promote_to_formal"]
        result = spec.handler({
            "workspace_id": self.workspace,
            "prior_run_id": "nonexistent-run",
            "new_fin": {},
        })
        server._validate(result, spec.output_schema)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "prior_run_not_found")
        self.assertEqual(result["status"], "blocked")

    def test_formal_grade_increments_correctly(self) -> None:
        """v1 -> v2 -> v3 chain."""
        result_v2 = run_model({
            "workspace_id": self.workspace,
            "spec": self.scenario["spec"],
            "input_revision": self.scenario["finance"],
            "mode": "estimate_preview",
            "idempotency_key": "fu-run-vchain-2",
        })
        v2 = promote_to_formal(
            self.workspace,
            prior_run_id=self.v1_run_id,
            new_fin=result_v2.get("data", {}),
        )
        self.assertEqual(v2["formal_grade"], "v2")

        result_v3 = run_model({
            "workspace_id": self.workspace,
            "spec": self.scenario["spec"],
            "input_revision": self.scenario["finance"],
            "mode": "estimate_preview",
            "idempotency_key": "fu-run-vchain-3",
        })
        v3 = promote_to_formal(
            self.workspace,
            prior_run_id=v2["run_id"],
            new_fin=result_v3.get("data", {}),
        )
        self.assertEqual(v3["formal_grade"], "v3")
        self.assertEqual(v3["prior_run_id"], v2["run_id"])
    def test_mcp_tool_exposes_formal_upgrade_envelope(self) -> None:
        candidate = run_model({
            "workspace_id": self.workspace,
            "spec": self.scenario["spec"],
            "input_revision": self.scenario["finance"],
            "mode": "estimate_preview",
            "idempotency_key": "fu-run-mcp-tool",
        })
        self.assertTrue(candidate["success"], candidate)

        server = build_server()
        self.assertIn("finance_promote_to_formal", server._tools)
        spec = server._tools["finance_promote_to_formal"]
        result = spec.handler({
            "workspace_id": self.workspace,
            "prior_run_id": self.v1_run_id,
            "new_fin": candidate.get("data", {}),
            "idempotency_key": "fu-promote-mcp-tool",
        })
        server._validate(result, spec.output_schema)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["formal_grade"], "v2")
        self.assertEqual(
            result["resource_uris"],
            [f"lvke://finance-model/workspaces/{self.workspace}/runs/{result['run_id']}"],
        )


if __name__ == "__main__":
    unittest.main()