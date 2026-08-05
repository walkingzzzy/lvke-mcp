from __future__ import annotations

import os
import tempfile
import unittest

from lvke_mcp.adapters.research_repository import PACKAGE_STORE
from lvke_mcp.domains.research import application


class DeepResearchQualityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-dr-quality-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "dr-quality-test"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_submit_persists_quality_summary_and_market_bindings(self) -> None:
        started = application.start_agent({
            "workspace_id": self.workspace,
            "topic": "区域产业项目市场规模",
            "industry": "制造业",
            "region": "湖北",
            "plan_items": [{"field": "market_size", "required": True}],
            "idempotency_key": "dr-start-quality",
        })
        self.assertTrue(started["success"], started)
        task_id = started["task_id"]
        submitted = application.submit_agent({
            "workspace_id": self.workspace,
            "task_id": task_id,
            "report_md": "市场规模见来源定位。",
            "citations": [{
                "source_id": "source-1",
                "resource_uri": "lvke://data-acquisition/workspaces/dr-quality-test/snapshots/source-1",
                "locator": "page:1",
                "content_hash": "sha256:" + "b" * 64,
            }],
            "source_snapshot_ids": ["source-1"],
            "quality_summary": {
                "query_rounds": 3,
                "usable_source_count": 1,
                "citation_coverage": 0.8,
                "missing_fields": ["target_share"],
                "conflicts": [{"field": "market_size", "sources": ["source-1", "source-2"]}],
            },
            "market_field_bindings": [{
                "field": "market_size",
                "value": 1200,
                "unit": "万元",
                "locator": "source-1#page:1",
                "source_snapshot_id": "source-1",
            }],
        })
        self.assertTrue(submitted["success"], submitted)
        self.assertEqual(submitted["status"], "partial")
        record = PACKAGE_STORE.get(self.workspace, submitted["research_package_id"])
        self.assertIsNotNone(record)
        artifacts = (record or {}).get("payload", {}).get("agent_artifacts", {})
        self.assertEqual(artifacts["quality_summary"]["query_rounds"], 3)
        self.assertEqual(artifacts["quality_summary"]["missing_fields"], ["target_share"])
        self.assertEqual(artifacts["market_field_bindings"][0]["field"], "market_size")

    def test_submit_without_quality_fields_keeps_legacy_package_shape(self) -> None:
        started = application.start_agent({
            "workspace_id": self.workspace,
            "topic": "政策资料",
            "idempotency_key": "dr-start-legacy",
        })
        submitted = application.submit_agent({
            "workspace_id": self.workspace,
            "task_id": started["task_id"],
            "report_md": "研究摘要。",
            "citations": [{"locator": "page:1"}],
            "source_snapshot_ids": ["source-1"],
        })
        self.assertTrue(submitted["success"], submitted)
        record = PACKAGE_STORE.get(self.workspace, submitted["research_package_id"])
        artifacts = (record or {}).get("payload", {}).get("agent_artifacts", {})
        self.assertNotIn("quality_summary", artifacts)
        self.assertNotIn("market_field_bindings", artifacts)


if __name__ == "__main__":
    unittest.main()
