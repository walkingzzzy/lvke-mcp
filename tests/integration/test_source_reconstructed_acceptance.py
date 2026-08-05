from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from lvke_mcp.domains.finance.evidence_binding import bind_finance_spec_evidence
from lvke_mcp.domains.finance.hengli_reference import scenario_matrix
from lvke_mcp.adapters.spreadsheets.reader import pick_backend
from lvke_mcp.servers.lvke_data_analysis import service as analysis
from lvke_mcp.runtime.source_reconstruction import reconstruction_errors
from lvke_mcp.servers.lvke_feasibility_delivery import service as delivery


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests/fixtures/source_reconstructed/manifest.json"
CLOSURE = ROOT / "tests/fixtures/source_reconstructed/review_closure.json"
RELEASES = ROOT / "tests/fixtures/source_reconstructed/formal_release_instances.json"


class SourceReconstructedAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-source-reconstructed-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        self.previous_golden = os.environ.get("LVKE_GOLDEN_DATA_ROOT")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        os.environ["LVKE_GOLDEN_DATA_ROOT"] = str(ROOT / "docs")

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        if self.previous_golden is None:
            os.environ.pop("LVKE_GOLDEN_DATA_ROOT", None)
        else:
            os.environ["LVKE_GOLDEN_DATA_ROOT"] = self.previous_golden
        self.tempdir.cleanup()

    def test_real_material_manifest_hashes_and_report_revisions(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["evidence_policy"], "source_reconstructed")
        self.assertFalse(manifest["project_fact_certified"])
        for source in manifest["sources"]:
            path = ROOT / source["path"]
            self.assertTrue(path.is_file(), source["path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(source["content_hash"], f"sha256:{digest}")
            self.assertTrue(source["locator"])
            self.assertTrue(source["method"])
        for case in ("cy_xiangyuan", "qianshan_forest_park"):
            revision = ROOT / manifest["cases"][case]["revised_report"]
            text = revision.read_text(encoding="utf-8")
            self.assertEqual(sum(1 for i in range(1, 10) if f"第{i}章" in text), 9)

    def test_finance_templates_have_sheets_and_formulas(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        template_ids = {
            "srcx_template_real_estate",
            "srcx_template_product_1",
            "srcx_template_product_2",
            "srcx_template_lease",
            "srcx_template_cemetery",
        }
        rows = [row for row in manifest["sources"] if row["source_id"] in template_ids]
        self.assertEqual(len(rows), 5)
        for row in rows:
            workbook = load_workbook(ROOT / row["path"], data_only=False, read_only=True)
            self.assertTrue(workbook.sheetnames, row["path"])
            formula_count = 0
            for sheet in workbook.worksheets:
                for values in sheet.iter_rows(values_only=True):
                    formula_count += sum(
                        1 for value in values if isinstance(value, str) and value.startswith("=")
                    )
            workbook.close()
            self.assertGreater(formula_count, 0, row["path"])

    def test_reconstruction_contract_and_finance_binding(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        record = manifest["sources"][0]
        self.assertEqual(reconstruction_errors(record), [])
        incomplete = dict(record)
        incomplete.pop("locator")
        self.assertIn("locator_required", reconstruction_errors(incomplete))
        blocked_pack = analysis.build_evidence_pack(
            "source-reconstructed-test",
            "missing-analysis-task",
            None,
            [],
            [],
            evidence_track="source_reconstructed",
            reconstruction_records=[incomplete],
        )
        self.assertFalse(blocked_pack["success"])
        self.assertEqual(blocked_pack["code"], "source_reconstruction_invalid")
        spec = {
            "evidence_policy": "source_reconstructed",
            "reconstruction_records": [record],
        }
        binding = bind_finance_spec_evidence("source-reconstructed-test", spec)
        self.assertTrue(binding["formal_ok"], binding)
        self.assertEqual(binding["evidence_policy"], "source_reconstructed")
        self.assertFalse(binding["project_fact_certified"])
        self.assertEqual(binding["reconstruction_ids"], [record["source_id"]])

    def test_review_retest_closure_and_formal_release_instances(self) -> None:
        closure = json.loads(CLOSURE.read_text(encoding="utf-8"))
        self.assertTrue(closure["all_retested"])
        self.assertEqual(len(closure["cases"]), 3)
        self.assertTrue(all(row["initial_status"] == "open" for row in closure["cases"]))
        self.assertTrue(all(row["retest_status"] == "resolved" for row in closure["cases"]))
        self.assertNotIn("security", closure["review_scope"])
        releases = json.loads(RELEASES.read_text(encoding="utf-8"))
        self.assertEqual(releases["release_scope"], "process_acceptance")
        self.assertFalse(releases["project_fact_certified"])
        self.assertEqual(len(releases["instances"]), 3)
        self.assertTrue(all(row["release_status"] == "released" for row in releases["instances"]))

    def _complete_run(self, workspace: str, *, release_scope: str) -> tuple[str, dict]:
        started = delivery.start({
            "workspace_id": workspace,
            "delivery_mode": "formal_release",
            "evidence_policy": "source_reconstructed",
            "release_scope": release_scope,
            "project_fact_certified": False,
            "reconstructed_source_ids": ["srcx_cy_report"],
            "unresolved_inputs": ["original_boe"],
            "release_limitations": ["report-derived values are reconstructed"],
            "idempotency_key": f"start-{workspace}",
        })
        self.assertTrue(started["success"], started)
        run_id = started["delivery_run_id"]
        for stage in (
            "project", "research", "market", "option", "scale", "drivers",
            "finance_spec", "finance_run", "finance_tables", "report", "review",
        ):
            updated = delivery.stage({
                "workspace_id": workspace,
                "delivery_run_id": run_id,
                "stage": stage,
                "status": "completed",
                "input_refs": [f"input:{stage}"],
                "output_refs": [f"output:{stage}"],
                "basis_hash": "sha256:" + hashlib.sha256(stage.encode()).hexdigest(),
                "idempotency_key": f"stage-{workspace}-{stage}",
            })
            self.assertTrue(updated["success"], updated)
            run_id = updated["delivery_run_id"]
        return run_id, delivery.validate({
            "workspace_id": workspace,
            "delivery_run_id": run_id,
            "scope": "formal",
        })

    def test_process_acceptance_release_and_project_delivery_block(self) -> None:
        process_run, validation = self._complete_run("cy-process", release_scope="process_acceptance")
        self.assertTrue(validation["success"], validation)
        released = delivery.release({
            "workspace_id": "cy-process",
            "delivery_run_id": process_run,
            "release_scope": "process_acceptance",
            "release_note": "source reconstructed process acceptance",
            "idempotency_key": "release-cy-process",
        })
        self.assertTrue(released["success"], released)
        self.assertEqual(released["release"]["release_scope"], "process_acceptance")
        self.assertFalse(released["release"]["project_fact_certified"])

        delivery_run, blocked_validation = self._complete_run("cy-delivery", release_scope="project_delivery")
        self.assertFalse(blocked_validation["success"])
        self.assertIn("project_fact_evidence_missing", blocked_validation["blockers"])
        blocked = delivery.release({
            "workspace_id": "cy-delivery",
            "delivery_run_id": delivery_run,
            "release_scope": "project_delivery",
            "idempotency_key": "release-cy-delivery",
        })
        self.assertFalse(blocked["success"])
        self.assertEqual(blocked["code"], "project_fact_evidence_missing")

        controlled = delivery.start({
            "workspace_id": "controlled-formal",
            "delivery_mode": "formal_release",
            "evidence_policy": "controlled_assumption",
            "release_scope": "process_acceptance",
            "idempotency_key": "controlled-start",
        })
        self.assertTrue(controlled["success"], controlled)
        controlled_validation = delivery.validate({
            "workspace_id": "controlled-formal",
            "delivery_run_id": controlled["delivery_run_id"],
            "scope": "formal",
        })
        self.assertFalse(controlled_validation["success"])
        self.assertIn("controlled_assumption_formal_forbidden", controlled_validation["blockers"])

    def test_hengli_six_scenarios_and_historical_statements(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        hengli = manifest["cases"]["hengli_hotel"]
        self.assertEqual(hengli["scenario_purchase_prices_wan"], [2000, 2200, 2400, 2600, 2800, 3000])
        self.assertEqual(len(hengli["historical_source_ids"]), 6)
        for source_id in hengli["historical_source_ids"]:
            row = next(item for item in manifest["sources"] if item["source_id"] == source_id)
            path = ROOT / row["path"]
            self.assertTrue(path.is_file())
            backend = pick_backend()
            sheets = backend.list_sheets(path)
            self.assertTrue(sheets, row["path"])
            read = backend.read_sheet(path, sheets[0], max_rows=20, max_cols=20)
            self.assertEqual(read.sheet, sheets[0])
            self.assertGreaterEqual(read.row_count, 1)
        reference = scenario_matrix()
        self.assertTrue(reference["valid"], reference)
        self.assertEqual(
            [item["purchase_price_wan"] for item in reference["scenarios"]],
            hengli["scenario_purchase_prices_wan"],
        )
        self.assertFalse(reference["replay"]["complete"])
        self.assertEqual(hengli["business_decision_status"], "not_selected")


if __name__ == "__main__":
    unittest.main()
