from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.servers.lvke_deliverable_review import rules
from lvke_mcp.servers.lvke_deliverable_review._service.base import (
    _review_envelope_status,
)
from lvke_mcp.servers.lvke_deliverable_review._service.events import _project_events
from lvke_mcp.servers.lvke_deliverable_review._service.lifecycle import get_review
from lvke_mcp.servers.lvke_deliverable_review.contracts import normalize_project_context
from lvke_mcp.servers.lvke_deliverable_review.store import STORE


class ReviewReleaseVerdictsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-review-verdict-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    @staticmethod
    def _append_passed_review(
        workspace_id: str,
        review_id: str,
        *,
        evidence_track: str,
        review_purpose: str,
        standards: dict | None = None,
        evidence_metadata: dict | None = None,
    ) -> dict:
        STORE.append(workspace_id, review_id, "review_created", {
            "project_context": {
                "evidence_track": evidence_track,
                "review_purpose": review_purpose,
                "release_scope": review_purpose,
            },
            "deployment_mode": "enforced",
            "standards": standards or {"packages": [], "incomplete": []},
            "evidence_metadata": evidence_metadata or {},
        })
        STORE.append(workspace_id, review_id, "review_completed", {
            "completed_at": "2026-08-09T00:00:00Z",
            "findings": [],
            "incomplete_reasons": [],
            "coverage": {},
            "overall_verdict": "pass",
        })
        return _project_events(workspace_id, review_id)

    def test_project_delivery_never_passes_with_release_blocker(self) -> None:
        state = self._append_passed_review(
            "review-project-delivery",
            "review_project_delivery",
            evidence_track="controlled_assumption",
            review_purpose="project_delivery",
        )
        self.assertEqual(state["technical_verdict"], "pass")
        self.assertEqual(state["release_verdict"], "fail")
        self.assertEqual(state["overall_verdict"], "fail")
        self.assertIn("controlled_assumption_release_forbidden", state["blockers"])
        self.assertNotIn(
            "standard_methodology_full_text_required:PKG-STD-011",
            state["blockers"],
        )

    def test_sim_a_formal_project_delivery_does_not_require_nested_fact_flag(self) -> None:
        state = self._append_passed_review(
            "review-sim-a-formal",
            "review_sim_a_formal",
            evidence_track="sim_a_formal",
            review_purpose="project_delivery",
            evidence_metadata={
                "evidence_policy": "sim_a_formal",
                "project_fact_certified": False,
            },
        )
        self.assertEqual(state["technical_verdict"], "pass")
        self.assertEqual(state["release_verdict"], "pass")
        self.assertEqual(state["overall_verdict"], "pass")
        self.assertNotIn("project_fact_certification_required", state["blockers"])

    def test_failed_release_verdict_is_returned_as_successful_partial_review(self) -> None:
        workspace_id = "review-partial-envelope"
        review_id = "review_partial_envelope"
        state = self._append_passed_review(
            workspace_id,
            review_id,
            evidence_track="controlled_assumption",
            review_purpose="project_delivery",
        )

        self.assertEqual(_review_envelope_status(state), "partial")
        result = get_review({"workspace_id": workspace_id, "review_id": review_id})

        self.assertTrue(result["success"])
        self.assertTrue(result["business_success"])
        self.assertTrue(result["completed"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["blockers"], [])
        self.assertIn(
            "controlled_assumption_release_forbidden",
            result["quality_issues"],
        )
        self.assertEqual(result["release_verdict"], "incomplete")

    def test_process_acceptance_keeps_controlled_assumption_as_limitation(self) -> None:
        state = self._append_passed_review(
            "review-process-acceptance",
            "review_process_acceptance",
            evidence_track="controlled_assumption",
            review_purpose="process_acceptance",
        )
        self.assertEqual(state["technical_verdict"], "pass")
        self.assertEqual(state["release_verdict"], "pass")
        self.assertEqual(state["overall_verdict"], "pass")
        self.assertNotIn("controlled_assumption_release_forbidden", state["blockers"])

    def test_project_delivery_requires_explicit_formal_certified_evidence(self) -> None:
        state = self._append_passed_review(
            "review-formal-delivery",
            "review_formal_delivery",
            evidence_track="real",
            review_purpose="project_delivery",
            evidence_metadata={
                "evidence_policy": "formal_evidence",
                "project_fact_certified": True,
            },
        )
        self.assertEqual(state["technical_verdict"], "pass")
        self.assertEqual(state["release_verdict"], "pass")
        self.assertEqual(state["overall_verdict"], "pass")
        self.assertEqual(state["blockers"], [])

    def test_review_purpose_and_release_scope_are_canonical_aliases(self) -> None:
        context = normalize_project_context(
            {
                "evidence_track": "source_reconstructed",
                "release_scope": "process_acceptance",
            },
            target_type="finance_run",
        )
        self.assertEqual(context["review_purpose"], "process_acceptance")
        self.assertEqual(context["release_scope"], "process_acceptance")
        with self.assertRaisesRegex(ValueError, "review_purpose_release_scope_mismatch"):
            normalize_project_context(
                {
                    "review_purpose": "project_delivery",
                    "release_scope": "process_acceptance",
                },
                target_type="finance_run",
            )

    def test_pkg_std_011_is_framework_only_for_process_acceptance(self) -> None:
        repo_root = Path(__file__).resolve().parents[2] / "src" / "lvke_mcp"
        process = rules.standards_snapshot(
            repo_root,
            ["PKG-STD-011"],
            review_purpose="process_acceptance",
        )
        delivery = rules.standards_snapshot(
            repo_root,
            ["PKG-STD-011"],
            review_purpose="project_delivery",
        )
        self.assertEqual(process["framework_only"], ["PKG-STD-011"])
        self.assertNotIn("PKG-STD-011", process["incomplete"])
        self.assertIn("PKG-STD-011", delivery["incomplete"])
        self.assertEqual(delivery["framework_only"], [])

    def test_missing_methodology_blocks_only_project_delivery_verdict(self) -> None:
        standards = {
            "packages": [{"package_id": "PKG-STD-011", "gate_status": "incomplete"}],
            "incomplete": ["PKG-STD-011"],
        }
        state = self._append_passed_review(
            "review-methodology-delivery",
            "review_methodology_delivery",
            evidence_track="real",
            review_purpose="project_delivery",
            standards=standards,
        )
        self.assertEqual(state["technical_verdict"], "pass")
        self.assertEqual(state["release_verdict"], "fail")
        self.assertIn(
            "standard_methodology_full_text_required:PKG-STD-011",
            state["blockers"],
        )


if __name__ == "__main__":
    unittest.main()
