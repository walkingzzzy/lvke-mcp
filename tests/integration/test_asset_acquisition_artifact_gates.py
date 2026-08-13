from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lvke_mcp.domains.asset_acquisition._backend import artifacts
from lvke_mcp.domains.asset_acquisition._backend import report_data
from lvke_mcp.domains.asset_acquisition._backend.store import _artifacts_root, _load, _save, _state_guard
from lvke_mcp.servers.lvke_asset_acquisition import service


class AcquisitionArtifactGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = tempfile.TemporaryDirectory(prefix="lvke-acquisition-artifact-gate-")
        self.previous_data = os.environ.get("LVKE_MCP_DATA_DIR")
        self.previous_deliverable = os.environ.get("LVKE_DELIVERABLE_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.data.name
        os.environ["LVKE_DELIVERABLE_DIR"] = str(Path(self.data.name) / "deliverables")

    def tearDown(self) -> None:
        if self.previous_data is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous_data
        if self.previous_deliverable is None:
            os.environ.pop("LVKE_DELIVERABLE_DIR", None)
        else:
            os.environ["LVKE_DELIVERABLE_DIR"] = self.previous_deliverable
        self.data.cleanup()

    def _run(self, *, mode: str, formal_ok: bool = False) -> dict:
        run_id = f"acqrun_{mode}"
        spec_id = f"spec_{mode}"
        spec = {
            "version": "finance_spec.v3",
            "asset_type": "hotel_lease",
            "delivery_mode": mode,
        }
        run = {
            "run_id": run_id,
            "status": "succeeded",
            "consistency_ok": True,
            "delivery_mode": mode,
            "validation_status": "passed",
            "formal_spec_valid": formal_ok,
            "evidence_formal_ok": formal_ok,
            "evidence_binding_hash": "sha256:evidence",
            "evidence_binding_version": "finance_evidence_binding.v3",
            "spec_id": spec_id,
            "spec_hash": artifacts._hash(spec),
            "input_hash": "sha256:input",
            "spec_snapshot_hash": "sha256:snapshot",
            "model_version": "acquisition_model.v3",
            "issues": [],
            "result": {},
        }
        with _state_guard("artifact-gate"):
            state = _load("artifact-gate")
            state["specs"][spec_id] = {"spec": spec}
            state["runs"][run_id] = run
            _save("artifact-gate", state)
        return run

    def test_preview_is_blocked_before_staging(self) -> None:
        run = self._run(mode="estimate_preview")
        with patch.object(artifacts, "_bind_spec_evidence", return_value={
            "binding_hash": run["evidence_binding_hash"],
            "binding_version": run["evidence_binding_version"],
            "formal_ok": False,
        }):
            result = artifacts.generate_artifacts("artifact-gate", run["run_id"], idempotency_key="preview")
        self.assertEqual(result["error"], "FORMAL_ARTIFACT_QUALIFICATION_REQUIRED")
        self.assertEqual(result["details"]["delivery_mode"], "estimate_preview")
        self.assertTrue(result["next_actions"])
        root = _artifacts_root("artifact-gate")
        self.assertFalse(root.exists())

    def test_process_acceptance_is_blocked_before_staging(self) -> None:
        run = self._run(mode="process_acceptance")
        with patch.object(artifacts, "_bind_spec_evidence", return_value={
            "binding_hash": run["evidence_binding_hash"],
            "binding_version": run["evidence_binding_version"],
            "formal_ok": False,
        }):
            result = service.generate_artifact("artifact-gate", run["run_id"], "process")
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "FORMAL_ARTIFACT_QUALIFICATION_REQUIRED")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["system_success"])
        self.assertTrue(result["transport_success"])
        self.assertFalse(result["business_success"])
        self.assertEqual(result["details"]["delivery_mode"], "process_acceptance")
        self.assertTrue(result["next_actions"])
        self.assertFalse(_artifacts_root("artifact-gate").exists())

    def test_stale_evidence_is_stable_and_does_not_stage(self) -> None:
        run = self._run(mode="formal_candidate", formal_ok=True)
        current = {"binding_hash": "sha256:current", "binding_version": "finance_evidence_binding.v3", "formal_ok": True}
        with patch.object(artifacts, "_bind_spec_evidence", return_value=current):
            result = artifacts.generate_artifacts("artifact-gate", run["run_id"], idempotency_key="stale")
        self.assertEqual(result["error"], "EVIDENCE_BINDING_STALE")
        self.assertEqual(result["details"]["current_binding_hash"], "sha256:current")
        self.assertFalse(_artifacts_root("artifact-gate").exists())

    def test_exception_after_staging_cleans_directory(self) -> None:
        run = self._run(mode="formal_candidate", formal_ok=True)
        with patch.object(artifacts, "_bind_spec_evidence", return_value={
            "binding_hash": run["evidence_binding_hash"],
            "binding_version": run["evidence_binding_version"],
            "formal_ok": True,
        }), patch.object(artifacts, "build_acquisition_report_data", side_effect=RuntimeError("fixture")):
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                artifacts.generate_artifacts("artifact-gate", run["run_id"], idempotency_key="cleanup")
        root = _artifacts_root("artifact-gate")
        self.assertFalse(list(root.glob(".*.staging-*")))

    def test_formal_artifact_succeeds_and_replays_idempotently(self) -> None:
        run = self._run(mode="formal_candidate", formal_ok=True)
        current = {
            "binding_hash": run["evidence_binding_hash"],
            "binding_version": run["evidence_binding_version"],
            "formal_ok": True,
            "status": "qualified",
            "bindings": [],
        }
        with patch.object(artifacts, "_bind_spec_evidence", return_value=current), patch.object(
            report_data, "_current_evidence_matches_run", return_value=(True, current)
        ):
            first = artifacts.generate_artifacts(
                "artifact-gate", run["run_id"], idempotency_key="formal-success"
            )
            replay = artifacts.generate_artifacts(
                "artifact-gate", run["run_id"], idempotency_key="formal-success"
            )
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(len(first["files"]), 5)
        self.assertTrue(Path(first["directory"]).is_dir())
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["artifact_id"], first["artifact_id"])

    def test_binding_exception_cleans_directory_and_marks_failed(self) -> None:
        run = self._run(mode="formal_candidate", formal_ok=True)
        current = {
            "binding_hash": run["evidence_binding_hash"],
            "binding_version": run["evidence_binding_version"],
            "formal_ok": True,
            "status": "qualified",
            "bindings": [],
        }
        with patch.object(artifacts, "_bind_spec_evidence", return_value=current), patch.object(
            report_data, "_current_evidence_matches_run", return_value=(True, current)
        ), patch.object(
            artifacts, "_bind_succeeded_artifact", side_effect=RuntimeError("binding fixture")
        ):
            with self.assertRaisesRegex(RuntimeError, "binding fixture"):
                artifacts.generate_artifacts(
                    "artifact-gate", run["run_id"], idempotency_key="binding-error"
                )
        root = _artifacts_root("artifact-gate")
        self.assertFalse(root.exists())
        state = _load("artifact-gate")
        failed = list(state["artifacts"].values())
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["status"], "failed")
        self.assertEqual(failed[0]["error"]["code"], "ARTIFACT_BINDING_FAILED")


if __name__ == "__main__":
    unittest.main()
