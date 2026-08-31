"""验证后置校验编排器能聚合四维校验并返回结构化报告（不阻断）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.domains.finance import tables_service
from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
from lvke_mcp.domains.finance.model_application import run_model
from lvke_mcp.domains.finance.post_generation_validation import validate_post_generation
from lvke_mcp.servers.lvke_finance_model.server import build_server


class PostGenerationValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-pgv-")
        self.previous_data_dir = os.environ.get("LVKE_MCP_DATA_DIR")
        self.previous_golden_root = os.environ.get("LVKE_GOLDEN_DATA_ROOT")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        os.environ["LVKE_GOLDEN_DATA_ROOT"] = str(
            Path(__file__).resolve().parents[2] / "docs"
        )
        self.workspace = "pgv-test"
        scenario = next(
            item
            for item in build_industry_scenarios("transport_logistics")
            if item["archetype_id"] == "urban_rail" and item["variant_id"] == "base"
        )
        result = run_model({
            "workspace_id": self.workspace,
            "spec": scenario["spec"],
            "input_revision": scenario["finance"],
            "mode": "estimate_preview",
            "idempotency_key": "pgv-run",
        })
        self.assertTrue(result["success"], result)
        self.run_id = result["run_id"]
        # Render tables so we have a package to validate
        tables_service.render(self.workspace, self.run_id)

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

    def test_validate_post_generation_technical_scope(self) -> None:
        result = validate_post_generation(
            self.workspace,
            self.run_id,
            validation_scope="technical",
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["validation_scope"], "technical")
        self.assertIn("dimensions", result)
        self.assertIn("technical", result["dimensions"])
        self.assertIn("standard", result["dimensions"])
        # viability should always be present
        self.assertIn("viability", result["dimensions"])
        # evidence is absent when spec is not provided
        self.assertNotIn("evidence", result["dimensions"])
        self.assertIn("overall_status", result)
        self.assertIn("blockers", result)
        self.assertIn("quality_issues", result)
        self.assertIn("warnings", result)

    def test_validate_post_generation_formal_scope(self) -> None:
        result = validate_post_generation(
            self.workspace,
            self.run_id,
            validation_scope="formal",
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["validation_scope"], "formal")

    def test_validate_post_generation_with_spec(self) -> None:
        scenario = next(
            item
            for item in build_industry_scenarios("transport_logistics")
            if item["archetype_id"] == "urban_rail" and item["variant_id"] == "base"
        )
        result = validate_post_generation(
            self.workspace,
            self.run_id,
            validation_scope="technical",
            spec=scenario["spec"],
        )
        self.assertTrue(result["success"], result)
        self.assertIn("evidence", result["dimensions"],
                       "evidence dimension should be present when spec is provided")

    def test_validate_post_generation_never_blocks(self) -> None:
        # Even with a non-existent run_id, the orchestrator should not block
        result = validate_post_generation(
            self.workspace,
            "nonexistent-run-id",
            validation_scope="technical",
        )
        self.assertTrue(result["success"], result)
        # run 不存在时该维度"没有结论"，而不是"校验器崩了"：两者的处置不同
        # （前者补 run_id，后者修代码），所以状态用 not_determinable，并显式
        # 声明 conclusion_available=False。同时它不得进 blockers —— blockers
        # 表示"这份交付有问题"，缺 run 不是交付质量问题。
        viability = result["dimensions"]["viability"]
        self.assertEqual(viability["status"], "not_determinable")
        self.assertFalse(viability["conclusion_available"])
        self.assertEqual(viability["blockers"], [])
        self.assertIn("viability", result["incomplete_dimensions"])
        self.assertFalse(result["validation_complete"])

    def test_all_dimensions_are_structured(self) -> None:
        result = validate_post_generation(
            self.workspace,
            self.run_id,
            validation_scope="technical",
        )
        for name, dim in result["dimensions"].items():
            with self.subTest(dimension=name):
                self.assertIn("valid", dim)
                self.assertIn("status", dim)
                self.assertIn("blockers", dim)
                self.assertIn("quality_issues", dim)
                self.assertIn("warnings", dim)
                self.assertIn("details", dim)
    def test_mcp_tool_exposes_validation_envelope(self) -> None:
        server = build_server()
        self.assertIn("finance_validate_post_generation", server._tools)
        spec = server._tools["finance_validate_post_generation"]
        result = spec.handler({
            "workspace_id": self.workspace,
            "run_id": self.run_id,
            "validation_scope": "technical",
        })
        server._validate(result, spec.output_schema)
        self.assertTrue(result["success"], result)
        self.assertIn(result["status"], {"ok", "partial"})
        self.assertEqual(
            result["resource_uris"],
            [f"lvke://finance-model/workspaces/{self.workspace}/runs/{self.run_id}"],
        )


if __name__ == "__main__":
    unittest.main()