from __future__ import annotations

import os
import tempfile
import unittest
import hashlib

from lvke_mcp.servers.lvke_feasibility_delivery import service
from lvke_mcp.adapters.project_planning_repository import PROJECT_CONTEXT_STORE, MARKET_CASE_STORE
from lvke_mcp.adapters.research_repository import PACKAGE_STORE


class FeasibilityDeliveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-fdr-test-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "fdr-test"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_fake_stage_references_cannot_formal_release(self) -> None:
        started = service.start({
            "workspace_id": self.workspace,
            "project_context_id": "pc-1",
            "delivery_mode": "review_candidate",
            "idempotency_key": "start-1",
        })
        self.assertFalse(started["success"], started)
        self.assertEqual(started["code"], "project_context_not_found")

        empty = service.start({
            "workspace_id": self.workspace,
            "delivery_mode": "review_candidate",
            "idempotency_key": "start-fake-stage",
        })
        rejected = service.stage({
            "workspace_id": self.workspace,
            "delivery_run_id": empty["delivery_run_id"],
            "stage": "project",
            "status": "completed",
            "output_refs": ["pc-does-not-exist"],
            "basis_hash": "sha256:" + "1" * 64,
            "idempotency_key": "fake-project-stage",
        })
        self.assertFalse(rejected["success"], rejected)
        self.assertEqual(rejected["code"], "stage_reference_invalid")

    def test_next_actions_are_executable_descriptors(self) -> None:
        started = service.start({
            "workspace_id": self.workspace,
            "delivery_mode": "review_candidate",
            "idempotency_key": "next-actions-start",
        })
        result = service.next_actions({
            "workspace_id": self.workspace,
            "delivery_run_id": started["delivery_run_id"],
        })
        self.assertTrue(result["success"], result)
        self.assertTrue(result["next_actions"])
        action = result["next_actions"][0]
        self.assertIn("tool", action)
        self.assertIn("arguments", action)
        self.assertIn("reason", action)
        self.assertEqual(action["arguments"]["workspace_id"], self.workspace)

    def test_partial_checkpoint_resume_and_stale_reopen(self) -> None:
        started = service.start({
            "workspace_id": self.workspace,
            "delivery_mode": "review_candidate",
            "idempotency_key": "start-2",
        })
        run_id = started["delivery_run_id"]
        project_record = PROJECT_CONTEXT_STORE.put(
            self.workspace,
            {"object_type": "ProjectContext", "status": "confirmed", "project_name": "test"},
            producer="test", status="confirmed",
        )
        project = service.stage({
            "workspace_id": self.workspace,
            "delivery_run_id": run_id,
            "stage": "project",
            "status": "completed",
            "output_refs": [project_record["object_id"]],
            "basis_hash": project_record["basis_hash"],
            "idempotency_key": "project-2",
        })
        partial = service.stage({
            "workspace_id": self.workspace,
            "delivery_run_id": project["delivery_run_id"],
            "stage": "research",
            "status": "partial",
            "output_refs": ["research-partial"],
            "next_actions": ["dr_continue"],
            "idempotency_key": "research-partial-2",
        })
        self.assertEqual(partial["status"], "partial")
        checkpoint = service.checkpoint({
            "workspace_id": self.workspace,
            "delivery_run_id": partial["delivery_run_id"],
            "reason": "source coverage incomplete",
            "idempotency_key": "checkpoint-2",
        })
        resumed = service.resume({
            "workspace_id": self.workspace,
            "checkpoint_id": checkpoint["checkpoint_id"],
            "idempotency_key": "resume-2",
        })
        self.assertTrue(resumed["success"])
        self.assertEqual(resumed["current_stage"], "research")

        research_record = PACKAGE_STORE.put(
            self.workspace,
            {"status": "completed", "quality_review_id": "qr-test", "quality_review_status": "passed"},
            producer="test", status="completed", source_ids=[project_record["object_id"]],
        )
        completed = service.stage({
            "workspace_id": self.workspace,
            "delivery_run_id": resumed["delivery_run_id"],
            "stage": "research",
            "status": "completed",
            "input_refs": [project_record["object_id"]],
            "output_refs": [research_record["object_id"]],
            "basis_hash": research_record["basis_hash"],
            "idempotency_key": "research-complete-2",
        })
        market_record = MARKET_CASE_STORE.put(
            self.workspace,
            {"object_type": "MarketSizingCase", "status": "confirmed", "parent_object_ids": [research_record["object_id"]]},
            producer="test", status="confirmed", source_ids=[research_record["object_id"]],
        )
        market = service.stage({
            "workspace_id": self.workspace,
            "delivery_run_id": completed["delivery_run_id"],
            "stage": "market",
            "status": "completed",
            "input_refs": [research_record["object_id"]],
            "output_refs": [market_record["object_id"]],
            "basis_hash": market_record["basis_hash"],
            "idempotency_key": "market-2",
        })
        reopened = service.stage({
            "workspace_id": self.workspace,
            "delivery_run_id": market["delivery_run_id"],
            "stage": "research",
            "status": "in_progress",
            "reopen": True,
            "idempotency_key": "research-reopen-2",
        })
        self.assertTrue(reopened["success"])
        self.assertIn("market", reopened["stale_stages"])

    def test_idempotency_conflict_is_blocked(self) -> None:
        args = {
            "workspace_id": self.workspace,
            "delivery_mode": "review_candidate",
            "idempotency_key": "same-key",
        }
        first = service.start(args)
        self.assertTrue(first["success"])
        conflict = service.start({**args, "delivery_mode": "estimate_preview"})
        self.assertFalse(conflict["success"])
        self.assertEqual(conflict["code"], "idempotency_conflict")


if __name__ == "__main__":
    unittest.main()
