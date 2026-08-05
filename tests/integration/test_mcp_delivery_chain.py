from __future__ import annotations

import os
import tempfile
import unittest

from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_PACKAGE_STORE
from lvke_mcp.adapters.report_repository import REVISION_STORE
from lvke_mcp.adapters.project_planning_repository import PROJECT_CONTEXT_STORE
from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
from lvke_mcp.domains.finance.model_application import run_model
from lvke_mcp.domains.finance import tables_service
from lvke_mcp.domains.reports import application as report_application
from lvke_mcp.servers.lvke_feasibility_delivery import service as delivery_service
from lvke_mcp.runtime.storage import sha256_json


class McpDeliveryChainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-chain-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "mcp-chain-test"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_finance_tables_report_and_delivery_bind_same_run(self) -> None:
        scenario = next(
            item
            for item in build_industry_scenarios("tourism_catering")
            if item["archetype_id"] == "scenic_area" and item["variant_id"] == "base"
        )
        run = run_model({
            "workspace_id": self.workspace,
            "spec": scenario["spec"],
            "input_revision": scenario["finance"],
            "mode": "estimate_preview",
            "idempotency_key": "chain-finance-run",
        })
        self.assertTrue(run["success"], run)
        run_id = run["run_id"]
        tables = tables_service.render(self.workspace, run_id)
        self.assertTrue(tables["success"], tables)
        package_id = tables["finance_tables_package_id"]

        evidence = EVIDENCE_STORE.put(
            self.workspace,
            {
                "evidence_track": "real",
                "claims": [{"claim": "market demand", "locator": "source-1#page:1"}],
                "source_locators": [{"resource_uri": "lvke://source-1", "locator": "page:1"}],
            },
            producer="test.evidence",
        )
        research = RESEARCH_PACKAGE_STORE.put(
            self.workspace,
            {"task_id": "dr-chain", "status": "done", "artifact_names": ["report", "sources"]},
            producer="test.research",
            status="done",
            source_ids=[evidence["object_id"]],
        )
        prepared = report_application.prepare({
            "workspace_id": self.workspace,
            "evidence_pack_ids": [evidence["object_id"]],
            "research_package_ids": [research["object_id"]],
            "finance_binding": {
                "kind": "generic_feasibility",
                "run_id": run_id,
                "package_id": package_id,
            },
            "outline": [{"title": f"第{i}章", "section_id": f"sec_chapter_{i}"} for i in range(1, 10)],
        })
        self.assertTrue(prepared["success"], prepared)
        started = report_application.start({
            "workspace_id": self.workspace,
            "report_preparation_id": prepared["report_preparation_id"],
            "document_snapshot": {"content": "# 第1章\n市场需求 [source-1#page:1]\n"},
        })
        self.assertTrue(started["success"], started)
        revision = REVISION_STORE.get(self.workspace, started["report_revision_id"])
        self.assertIsNotNone(revision)
        upstream = (revision or {}).get("payload", {}).get("upstream", {})
        self.assertEqual(upstream["run_id"], run_id)
        self.assertEqual(upstream["finance_tables_package_id"], package_id)

        project = PROJECT_CONTEXT_STORE.put(
            self.workspace,
            {"object_type": "ProjectContext", "status": "confirmed", "project_name": "chain"},
            producer="test.project", status="confirmed",
        )
        delivery = delivery_service.start({
            "workspace_id": self.workspace,
            "project_context_id": project["object_id"],
            "delivery_mode": "review_candidate",
            "idempotency_key": "chain-delivery-start",
        })
        self.assertTrue(delivery["success"], delivery)
        rejected = delivery_service.stage({
            "workspace_id": self.workspace,
            "delivery_run_id": delivery["delivery_run_id"],
            "stage": "research",
            "status": "completed",
            "input_refs": [project["object_id"]],
            "output_refs": ["research-does-not-exist"],
            "basis_hash": sha256_json({"stage": "research"}),
            "idempotency_key": "chain-fake-research",
        })
        self.assertFalse(rejected["success"], rejected)
        self.assertEqual(rejected["code"], "stage_reference_invalid")


if __name__ == "__main__":
    unittest.main()
