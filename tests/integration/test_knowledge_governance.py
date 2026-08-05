from __future__ import annotations

import os
import tempfile
import unittest

from lvke_mcp.servers.lvke_knowledge_governance import service


class KnowledgeGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-kg-test-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "kg-test"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _candidate(self, title: str) -> str:
        record = service.CANDIDATE_STORE.put(
            self.workspace,
            {
                "title": title,
                "content": "reviewable knowledge",
                "candidate_type": "procedure",
                "rubric_assessment_id": "rubric-1",
                "evidence_bindings": [{
                    "resource_uri": "lvke://source-files/workspaces/kg-test/snapshots/src-1",
                    "locator": "page:1",
                    "content_hash": "sha256:" + "a" * 64,
                    "evidence_track": "real",
                }],
            },
            producer="test",
        )
        return record["object_id"]

    def test_rejected_candidate_cannot_publish(self) -> None:
        candidate_id = self._candidate("rejected")
        review = service.review_candidate({
            "workspace_id": self.workspace,
            "candidate_id": candidate_id,
            "decision": "rejected",
            "reason": "evidence is insufficient",
            "idempotency_key": "review-rejected",
        })
        self.assertTrue(review["success"])
        release = service.publish_release({
            "workspace_id": self.workspace,
            "candidate_id": candidate_id,
            "review_id": review["knowledge_review_id"],
            "idempotency_key": "release-rejected",
        })
        self.assertFalse(release["success"])
        self.assertEqual(release["code"], "knowledge_review_not_accepted")

    def test_accepted_candidate_publishes_immutable_release(self) -> None:
        candidate_id = self._candidate("accepted")
        review = service.review_candidate({
            "workspace_id": self.workspace,
            "candidate_id": candidate_id,
            "decision": "accepted",
            "reason": "evidence and rubric are complete",
            "idempotency_key": "review-accepted",
        })
        release = service.publish_release({
            "workspace_id": self.workspace,
            "candidate_id": candidate_id,
            "review_id": review["knowledge_review_id"],
            "idempotency_key": "release-accepted",
        })
        self.assertTrue(release["success"], release)
        candidate = service.get_candidate({"workspace_id": self.workspace, "candidate_id": candidate_id})
        self.assertEqual(candidate["candidate_status"], "published")
        self.assertEqual(len(candidate["releases"]), 1)


if __name__ == "__main__":
    unittest.main()
