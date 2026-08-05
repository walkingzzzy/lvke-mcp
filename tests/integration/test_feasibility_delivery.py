from __future__ import annotations

import os
import tempfile
import unittest
import hashlib

from lvke_mcp.servers.lvke_feasibility_delivery import service


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

    def test_full_stage_lifecycle_and_release(self) -> None:
        started = service.start({
            "workspace_id": self.workspace,
            "project_context_id": "pc-1",
            "delivery_mode": "review_candidate",
            "idempotency_key": "start-1",
        })
        self.assertTrue(started["success"])
        self.assertEqual(started["current_stage"], "research")
        run_id = started["delivery_run_id"]

        for stage in (
            "research", "market", "option", "scale", "drivers", "finance_spec",
            "finance_run", "finance_tables", "report", "review",
        ):
            result = service.stage({
                "workspace_id": self.workspace,
                "delivery_run_id": run_id,
                "stage": stage,
                "status": "completed",
                "input_refs": [f"input-{stage}"],
                "output_refs": [f"output-{stage}"],
                "basis_hash": "sha256:" + hashlib.sha256(stage.encode()).hexdigest(),
                "idempotency_key": f"stage-{stage}",
            })
            self.assertTrue(result["success"], result)
            run_id = result["delivery_run_id"]

        self.assertEqual(service.status({"workspace_id": self.workspace, "delivery_run_id": run_id})["current_stage"], "released")
        validation = service.validate({
            "workspace_id": self.workspace,
            "delivery_run_id": run_id,
            "scope": "formal",
        })
        self.assertTrue(validation["success"], validation)
        released = service.release({
            "workspace_id": self.workspace,
            "delivery_run_id": run_id,
            "release_note": "integration test",
            "idempotency_key": "release-1",
        })
        self.assertTrue(released["success"], released)
        self.assertEqual(released["status"], "released")
        self.assertTrue(released["release_id"])

    def test_partial_checkpoint_resume_and_stale_reopen(self) -> None:
        started = service.start({
            "workspace_id": self.workspace,
            "delivery_mode": "review_candidate",
            "idempotency_key": "start-2",
        })
        run_id = started["delivery_run_id"]
        project = service.stage({
            "workspace_id": self.workspace,
            "delivery_run_id": run_id,
            "stage": "project",
            "status": "completed",
            "output_refs": ["pc-2"],
            "basis_hash": "sha256:" + "2" * 64,
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

        completed = service.stage({
            "workspace_id": self.workspace,
            "delivery_run_id": resumed["delivery_run_id"],
            "stage": "research",
            "status": "completed",
            "input_refs": ["research-input-2"],
            "output_refs": ["research-complete"],
            "basis_hash": "sha256:" + "3" * 64,
            "idempotency_key": "research-complete-2",
        })
        market = service.stage({
            "workspace_id": self.workspace,
            "delivery_run_id": completed["delivery_run_id"],
            "stage": "market",
            "status": "completed",
            "input_refs": ["market-input-2"],
            "output_refs": ["market-2"],
            "basis_hash": "sha256:" + "4" * 64,
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
