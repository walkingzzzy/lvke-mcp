from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from lvke_mcp.servers.lvke_feasibility_delivery import service as delivery
from lvke_mcp.servers.lvke_feasibility_delivery.contracts import STAGES


class FeasibilityReleaseScopeSemanticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-release-scope-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    @staticmethod
    def _structurally_complete_run(**overrides: object) -> dict:
        stages: dict[str, dict] = {}
        previous_output = ""
        for name in STAGES[:-1]:
            output = f"{name}-output"
            stages[name] = {
                "status": "completed",
                "input_refs": [previous_output] if previous_output else [],
                "output_refs": [output],
                "basis_hash": "sha256:" + name.encode().hex().ljust(64, "0")[:64],
                "warnings": [],
                "blockers": [],
            }
            previous_output = output
        return {
            "delivery_mode": "estimate_preview",
            "release_scope": "process_acceptance",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
            "stages": stages,
            **overrides,
        }

    def test_technical_validation_rejects_an_empty_stage_chain(self) -> None:
        started = delivery.start({
            "workspace_id": "empty-technical-chain",
            "delivery_mode": "estimate_preview",
            "release_scope": "process_acceptance",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
            "idempotency_key": "empty-chain-start",
        })
        result = delivery.validate({
            "workspace_id": "empty-technical-chain",
            "delivery_run_id": started["delivery_run_id"],
            "scope": "technical",
        })
        self.assertFalse(result["success"], result)
        self.assertIn("project_pending", result["blockers"])
        self.assertIn("research_output_refs_missing", result["blockers"])
        self.assertIn("review_basis_hash_missing", result["blockers"])

    def test_technical_scope_relaxes_only_formal_qualification(self) -> None:
        run = self._structurally_complete_run()
        technical_ok, technical_blockers, _ = delivery._validation(  # noqa: SLF001
            run, "technical",
        )
        formal_ok, formal_blockers, _ = delivery._validation(  # noqa: SLF001
            run, "formal",
        )
        self.assertTrue(technical_ok, technical_blockers)
        self.assertFalse(formal_ok)
        self.assertIn("preview_cannot_formal_release", formal_blockers)
        self.assertIn("controlled_assumption_formal_forbidden", formal_blockers)

    def test_process_acceptance_release_uses_technical_validation(self) -> None:
        started = delivery.start({
            "workspace_id": "process-release-routing",
            "delivery_mode": "estimate_preview",
            "release_scope": "process_acceptance",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
            "idempotency_key": "process-release-start",
        })
        with patch.object(
            delivery, "_validation", return_value=(True, [], ["formal_evidence_not_established"]),
        ) as mocked:
            released = delivery.release({
                "workspace_id": "process-release-routing",
                "delivery_run_id": started["delivery_run_id"],
                "release_scope": "process_acceptance",
                "idempotency_key": "process-release",
            })
        self.assertTrue(released["success"], released)
        self.assertEqual(mocked.call_args.args[1], "technical")
        self.assertEqual(released["validation_scope"], "technical")
        self.assertEqual(released["release_scope"], "process_acceptance")
        self.assertIn("formal_evidence_not_established", released["warnings"])

    def test_project_delivery_release_uses_formal_validation(self) -> None:
        started = delivery.start({
            "workspace_id": "project-release-routing",
            "delivery_mode": "formal_release",
            "release_scope": "project_delivery",
            "evidence_policy": "formal_evidence",
            "project_fact_certified": True,
            "idempotency_key": "project-release-start",
        })
        with patch.object(delivery, "_validation", return_value=(True, [], [])) as mocked:
            released = delivery.release({
                "workspace_id": "project-release-routing",
                "delivery_run_id": started["delivery_run_id"],
                "release_scope": "project_delivery",
                "idempotency_key": "project-release",
            })
        self.assertTrue(released["success"], released)
        self.assertEqual(mocked.call_args.args[1], "formal")
        self.assertEqual(released["validation_scope"], "formal")

    def test_project_delivery_returns_specific_fact_certification_rejection(self) -> None:
        started = delivery.start({
            "workspace_id": "project-fact-rejection",
            "delivery_mode": "formal_release",
            "release_scope": "project_delivery",
            "evidence_policy": "formal_evidence",
            "project_fact_certified": False,
            "idempotency_key": "project-fact-start",
        })
        with patch.object(
            delivery,
            "_validation",
            return_value=(False, ["project_fact_certification_required"], []),
        ):
            rejected = delivery.release({
                "workspace_id": "project-fact-rejection",
                "delivery_run_id": started["delivery_run_id"],
                "release_scope": "project_delivery",
                "idempotency_key": "project-fact-release",
            })
        self.assertFalse(rejected["success"])
        self.assertEqual(rejected["code"], "project_fact_certification_required")


if __name__ == "__main__":
    unittest.main()
