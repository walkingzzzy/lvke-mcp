from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lvke_mcp.domains.asset_acquisition import resources as acquisition_resources
from lvke_mcp.domains.reports._artifacts import formal_gate
from lvke_mcp.domains.reports._service import export as report_export
from lvke_mcp.domains.reports._service import generation as report_generation
from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.runtime import resource_registry
from lvke_mcp.servers.lvke_deliverable_review._financial_checks.acquisition import (
    _acquisition_checks,
)
from lvke_mcp.servers.lvke_deliverable_review._report_checks.claims import (
    build_claim_graph,
)
from lvke_mcp.servers.lvke_deliverable_review._service import target_resolve
from lvke_mcp.servers.lvke_project_planning._lifecycle import build_scale
from lvke_mcp.domains.project_planning._service import base as planning_base


class AcceptanceRegressionTest(unittest.TestCase):
    def test_option_comparison_inherits_reconstructed_basis_policy(self) -> None:
        context = {
            "payload": {
                "evidence_track": "technical_fixture",
                "evidence_policy": "technical_fixture",
                "project_fact_certified": False,
            }
        }
        market = {
            "payload": {
                "evidence_track": "source_reconstructed",
                "evidence_policy": "source_reconstructed",
                "project_fact_certified": False,
            }
        }

        track, policy, certified = planning_base._planning_evidence_qualification(
            context,
            market,
        )

        self.assertEqual(track, "source_reconstructed")
        self.assertEqual(policy, "source_reconstructed")
        self.assertFalse(certified)

    def test_scale_solver_enforces_market_cap_and_plot_ratio_minimum(self) -> None:
        common = {
            "target_unit": "MWh/year",
            "land": build_scale.Decimal("25000"),
            "intensity": build_scale.Decimal("1"),
            "floor": build_scale.Decimal("10000"),
            "footprint": build_scale.Decimal("5000"),
            "green": build_scale.Decimal("2500"),
            "constraints": {
                "plot_ratio_min": 0.5,
                "plot_ratio_max": 1,
                "building_coverage_max": 0.25,
                "green_ratio_min": 0.1,
            },
            "market_selected": {
                "computed_target_volume": 11500,
                "unit": "MWh/year",
            },
        }
        below_min = build_scale._scale_violations(
            target=build_scale.Decimal("10000"),
            **common,
        )
        over_market = build_scale._scale_violations(
            target=build_scale.Decimal("12000"),
            **common,
        )

        self.assertIn("plot_ratio_constraint_failed", below_min)
        self.assertIn("build_capacity_exceeds_selected_market", over_market)

    def test_formal_gate_classifies_acquisition_binding_before_generic_checks(self) -> None:
        basis = {
            "doc_kind": "feasibility",
            "report_type": "gov10",
            "finance": {"run_kind": "asset_acquisition"},
        }
        with self.assertRaises(formal_gate.DeliverableArtifactError) as raised:
            formal_gate._assert_formal_basis(basis, {})

        self.assertEqual(raised.exception.code, "FORMAL_ARTIFACT_TYPE_UNSUPPORTED")

    def test_solar_asset_context_resolves_energy_planning_constraints(self) -> None:
        context = {
            "object_id": "pctx-solar",
            "payload": {
                "industry_code": "D4416",
                "project_type": "acquisition",
                "transaction_structure": "asset_transfer",
                "asset_type": "solar_power",
            },
        }
        with patch.object(
            build_scale.service.PROJECT_CONTEXT_STORE,
            "get",
            return_value=context,
        ):
            result = build_scale.get_industry_constraints("ws-a", "pctx-solar")

        self.assertTrue(result["success"])
        self.assertEqual(result["matched_industry_key"], "能源")
        self.assertEqual(result["evidence_eligibility"], "technical_fixture")

    def test_unitless_dscr_and_target_rate_claims_are_distinct(self) -> None:
        claims = build_claim_graph(
            "上述结果低于8%的目标收益率及1.2的最低偿债备付率",
            target_id="revision-1",
        )
        self.assertEqual(
            [(row["value"], row["metric"]) for row in claims],
            [(8.0, "discount_rate"), (1.2, "dscr")],
        )

    def test_investment_breakdown_claims_keep_canonical_metric_names(self) -> None:
        claims = build_claim_graph(
            "本轮建设投资按建筑工程费 300.00 万元、设备及工器具购置费 "
            "500.00 万元、安装工程费 100.00 万元、工程建设其他费 "
            "50.00 万元、基本预备费 50.00 万元构成。",
            target_id="revision-1",
        )

        self.assertEqual(
            [(row["value"], row["metric"]) for row in claims],
            [
                (300.0, "civil_cost"),
                (500.0, "equipment_cost"),
                (100.0, "installation_cost"),
                (50.0, "other_investment_cost"),
                (50.0, "contingency"),
            ],
        )

    def test_asset_resource_rejects_uri_workspace_mismatch_and_encodes_binary(self) -> None:
        uri = "lvke://asset-acquisition/workspaces/ws-a/table-packages/pkg-1/csv/table"
        with patch.object(
            acquisition_resources,
            "resolve_resource",
            return_value=(b"a,b\r\n1,2\r\n", "text/csv; charset=utf-8"),
        ):
            result = resource_registry.read_resource("ws-a", uri)
            self.assertTrue(result["success"])
            self.assertEqual(result["content_encoding"], "base64")
            self.assertEqual(
                base64.b64decode(result["content"]).decode("utf-8"),
                "a,b\r\n1,2\r\n",
            )
        denied = resource_registry.read_resource("ws-b", uri)
        self.assertFalse(denied["success"])
        self.assertIn("RESOURCE_WORKSPACE_MISMATCH", denied["blockers"])

    def test_asset_resource_listing_uses_global_cursor_and_limit(self) -> None:
        with (
            patch.object(
                acquisition_resources.backend,
                "list_specs",
                return_value=[],
            ),
            patch.object(
                acquisition_resources.backend,
                "list_runs",
                return_value=[
                    {"run_id": "run-a", "created_at": "2026-01-01"},
                    {"run_id": "run-b", "created_at": "2026-01-02"},
                    {"run_id": "run-c", "created_at": "2026-01-03"},
                ],
            ),
            patch.object(
                acquisition_resources.backend,
                "list_scenario_matrices",
                return_value=[],
            ),
            patch.object(
                acquisition_resources.backend,
                "list_artifacts",
                return_value=[],
            ),
            patch.object(acquisition_resources.tables.PACKAGE_STORE, "list", return_value=[]),
        ):
            result = resource_registry.list_resources(
                "ws-a",
                "asset-acquisition",
                resource_type="run",
                limit=2,
            )
        self.assertTrue(result["success"])
        self.assertEqual(len(result["resources"]), 2)
        self.assertTrue(result["has_more"])
        self.assertIsNotNone(result["next_cursor"])

    def test_scenario_matrix_is_a_full_model_rerun_input(self) -> None:
        matrix = {
            "matrix_id": "matrix-1",
            "status": "succeeded",
            "method": "full_model_rerun",
            "rows": [{"scenario_spec_hash": "sha256:a", "result_hash": "sha256:b"}],
        }
        findings, incomplete, executed, _metrics = _acquisition_checks(
            {"available": True, "result": {"scenario_matrices": [matrix]}},
            "acqrun-1",
            {"FIN.SENSITIVITY.RERUN": {"rule_id": "FIN.SENSITIVITY.RERUN"}},
            [],
        )
        self.assertNotIn("rule_input_unavailable:FIN.SENSITIVITY.RERUN", incomplete)
        self.assertIn("FIN.SENSITIVITY.RERUN", executed)
        self.assertFalse(any(row["rule_id"] == "FIN.SENSITIVITY.RERUN" for row in findings))

    def test_basis_capture_accepts_immutable_report_snapshot(self) -> None:
        snapshot = {
            "workspace_id": "ws-a",
            "revision_id": "native-old",
            "content": "# old revision",
            "report_type": "generic_feasibility",
        }
        with (
            patch.object(
                formal_gate,
                "_document_snapshot",
                return_value=(
                    {"revision_id": "native-old", "content_hash": "sha256:old"},
                    snapshot["content"],
                    {"report_type": "generic_feasibility", "doc_kind": "feasibility"},
                ),
            ),
            patch.object(formal_gate, "_fresh_readiness", return_value={}),
            patch.object(formal_gate, "_source_basis_snapshot", return_value={}),
            patch.object(formal_gate, "_json_snapshot", return_value=({}, None)),
            patch.object(formal_gate, "_appendix_files_snapshot", return_value=[]),
            patch.object(formal_gate, "_strict_finance_gate", return_value={"ok": True}),
        ):
            basis, content, _context = formal_gate._capture_basis(
                "ws-a",
                template_version="feasibility-report.v1",
                report_revision_id="rrv-old",
                document_snapshot=snapshot,
            )
        self.assertEqual(content, "# old revision")
        self.assertEqual(basis["report_revision_id"], "rrv-old")
        self.assertEqual(basis["document"]["revision_id"], "native-old")

    def test_report_export_passes_exact_public_revision_snapshot(self) -> None:
        record = {
            "object_id": "rrv-old",
            "payload": {
                "document_snapshot": {
                    "workspace_id": "ws-a",
                    "revision_id": "native-old",
                    "content": "# old revision",
                },
                "upstream": {"run_id": "run-old"},
            },
        }
        with tempfile.TemporaryDirectory(prefix="lvke-report-export-") as root:
            artifact_root = Path(root)
            (artifact_root / "report.docx").write_bytes(b"docx")
            with (
                patch.object(report_export, "_resolve_revision_record", return_value=(record, False)),
                patch(
                    "lvke_mcp.domains.reports.artifacts._create_revision_bound_draft_export",
                    return_value={"artifact_id": "deliverable_" + "a" * 32, "kind": "draft", "files": []},
                ) as create,
                patch("lvke_mcp.domains.reports.artifacts._artifact_root", return_value=artifact_root),
                patch(
                    "lvke_mcp.domains.reports.docx_fonts.audit_docx_fonts",
                    return_value={"invalid_locale_font_count": 0, "portable_cjk_fonts": ["font"]},
                ),
            ):
                result = report_export.export_docx("ws-a", "rrv-old", "draft")
        self.assertTrue(result["success"])
        self.assertEqual(result["report_revision_id"], "rrv-old")
        create.assert_called_once_with(
            "ws-a",
            report_revision_id="rrv-old",
            document_snapshot=record["payload"]["document_snapshot"],
            expected_run_id="run-old",
        )

    def test_report_status_revision_lookup_is_idempotent(self) -> None:
        payload = {
            "task_id": "task-1",
            "native_revision_id": "native-1",
            "task_status": "agent_drafted",
        }
        record = {
            "producer": "lvke-report-generation.report_status",
            "created_at": "2026-01-01T00:00:00Z",
            "content_hash": sha256_json(payload),
            "payload": payload,
        }
        with patch.object(report_generation.REVISION_STORE, "list", return_value=[record]):
            replay = report_generation._existing_status_revision(
                "ws-a",
                task_id="task-1",
                native_revision_id="native-1",
                task_status="agent_drafted",
                payload=payload,
            )
        self.assertIs(replay, record)

    def test_legacy_artifact_with_multiple_public_revisions_fails_closed(self) -> None:
        records = [
            {
                "object_id": "rrv-1",
                "created_at": "2026-01-01T00:00:00Z",
                "payload": {"native_revision_id": "native-1", "upstream": {}},
            },
            {
                "object_id": "rrv-2",
                "created_at": "2026-01-02T00:00:00Z",
                "payload": {"native_revision_id": "native-1", "upstream": {}},
            },
        ]
        with (
            patch("lvke_mcp.adapters.report_repository.REVISION_STORE.list", return_value=records),
        ):
            _snapshot, _bindings, blockers = target_resolve._linked_generic_report_revision(
                "ws-a",
                {"document_revision_id": "native-1"},
            )
        self.assertEqual(blockers, ["report_artifact_revision_ambiguous"])

    def test_combined_lineage_allows_multiple_runs_but_rejects_report_mismatch(self) -> None:
        coherent = [
            {"target_type": "finance_run", "target_id": "run-a", "bindings": {}},
            {"target_type": "acquisition_run", "target_id": "run-b", "bindings": {}},
            {
                "target_type": "report_revision",
                "target_id": "rrv-a",
                "bindings": {"finance_run_id": "run-a", "report_revision_id": "rrv-a"},
            },
        ]
        self.assertEqual(target_resolve._combined_lineage_blockers(coherent), [])
        incoherent = [
            *coherent[:2],
            {
                "target_type": "report_revision",
                "target_id": "rrv-x",
                "bindings": {"finance_run_id": "run-x", "report_revision_id": "rrv-x"},
            },
        ]
        self.assertIn(
            "combined_report_finance_run_mismatch",
            target_resolve._combined_lineage_blockers(incoherent),
        )


if __name__ == "__main__":
    unittest.main()
