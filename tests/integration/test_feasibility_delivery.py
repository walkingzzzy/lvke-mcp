from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lvke_mcp.adapters.project_planning_repository import PROJECT_CONTEXT_STORE, MARKET_CASE_STORE
from lvke_mcp.adapters.research_repository import PACKAGE_STORE
from lvke_mcp.runtime import resource_registry
from lvke_mcp.servers.lvke_feasibility_delivery import service


class FeasibilityDeliveryTest(unittest.TestCase):

    def test_delivery_uses_runtime_registry_for_cross_domain_read_projections(self) -> None:
        source = Path("src/lvke_mcp/servers/lvke_feasibility_delivery/service.py").read_text()
        self.assertNotIn("lvke_mcp.servers.lvke_deliverable_review", source)
        self.assertNotIn("lvke_mcp.servers.lvke_knowledge_governance", source)
        review_projection = {"review_status": "ok", "target": {"target_id": "target-1"}}
        with patch.object(
            service.resource_registry,
            "get_review",
            return_value={"review": review_projection},
        ) as review_read:
            resolved = service._resolve_object(
                "registry-ws",
                "lvke://deliverable-review/workspaces/registry-ws/reviews/review-1",
            )
        review_read.assert_called_once_with("registry-ws", "review-1")
        self.assertEqual(resolved["kind"], "ReviewRun")
        self.assertEqual(resolved["payload"], review_projection)

        review_service = SimpleNamespace(
            get_review=lambda args: {"review": {"review_id": args["review_id"]}},
        )
        knowledge_service = SimpleNamespace(
            get_candidate=lambda args: {"candidate_id": args["candidate_id"]},
        )
        with patch.object(
            resource_registry,
            "_module",
            side_effect=[review_service, knowledge_service],
        ):
            self.assertEqual(
                resource_registry.get_review("registry-ws", "review-2")["review"]["review_id"],
                "review-2",
            )
            self.assertEqual(
                resource_registry.get_knowledge_candidate("registry-ws", "candidate-1")["candidate_id"],
                "candidate-1",
            )

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

    def test_low_quality_release_still_creates_restricted_record(self) -> None:
        started = service.start({
            "workspace_id": self.workspace,
            "delivery_mode": "review_candidate",
            "idempotency_key": "release-low-quality-start",
        })

        validated = service.validate({
            "workspace_id": self.workspace,
            "delivery_run_id": started["delivery_run_id"],
            "scope": "formal",
        })
        self.assertTrue(validated["success"], validated)
        self.assertEqual(validated["status"], "partial")
        self.assertFalse(validated["validation"]["quality_passed"])
        self.assertEqual(validated["blockers"], [])
        self.assertTrue(validated["quality_issues"])

        # 过程验收允许"带限制放行"：阶段链未走完属置信度不足，可产出把全部
        # 缺口写进 release_limitations 的记录。
        released = service.release({
            "workspace_id": self.workspace,
            "delivery_run_id": started["delivery_run_id"],
            "release_scope": "process_acceptance",
            "release_note": "低质量发布记录回归",
            "idempotency_key": "release-low-quality-record",
        })
        self.assertTrue(released["success"], released)
        self.assertTrue(released["completed"], released)
        self.assertEqual(released["status"], "partial")
        self.assertFalse(released["quality_valid"])
        self.assertEqual(released["blockers"], [])
        self.assertTrue(released["quality_issues"])
        self.assertTrue(released["release_id"].startswith("fdrp_"))
        self.assertEqual(
            released["release"]["release_limitations"],
            released["quality_issues"],
        )
        self.assertEqual(released["delivery_run"]["status"], "released")

    def test_project_delivery_release_refuses_a_low_quality_chain(self) -> None:
        """正式项目交付不接受"带限制放行"：这是与过程验收的分界线。

        上一个用例证明过程验收可以带限制出件；这里证明同一条低质量链换成
        project_delivery 就必须被拒，否则"过程验收"与"正式交付"两级就没有
        实际区别了。
        """

        started = service.start({
            "workspace_id": self.workspace,
            "delivery_mode": "review_candidate",
            "idempotency_key": "release-project-delivery-start",
        })
        refused = service.release({
            "workspace_id": self.workspace,
            "delivery_run_id": started["delivery_run_id"],
            "release_scope": "project_delivery",
            "idempotency_key": "release-project-delivery-refused",
        })
        self.assertFalse(refused["success"], refused)
        self.assertEqual("blocked", refused["status"])
        self.assertEqual(
            "FORMAL_ARTIFACT_QUALIFICATION_REQUIRED", refused["code"]
        )
        self.assertIsNone(refused.get("release_id"))
        self.assertTrue(refused["quality_issues"])

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
