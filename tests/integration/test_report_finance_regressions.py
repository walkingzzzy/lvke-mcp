from __future__ import annotations

import unittest
from unittest.mock import patch

from lvke_mcp.domains.finance import tables_application, tables_service
from lvke_mcp.domains.finance.advanced_analysis import (
    build_balance_sheet_schedule,
    run_monte_carlo,
)
from lvke_mcp.domains.finance.run_service import DELIVERY_TABLE_KEYS
from lvke_mcp.domains.reports import application as report_application
from lvke_mcp.domains.reports import read_model as report_read_model
from lvke_mcp.domains.reports import validation as report_validation
from lvke_mcp.domains.reports._doc_service.structure import (
    merge_single_chapter_proposal,
    validate_report_structure,
)
from lvke_mcp.runtime.storage import sha256_json


class ReportAndFinanceRegressionTest(unittest.TestCase):
    def test_balance_sheet_does_not_double_count_cip_intangibles_or_terminal_disposal(self) -> None:
        run = {
            "params": {"build_years": 1},
            "investment": {"fixed_asset": 1000, "working_capital": 100},
            "funding": {"capital": 1000, "loan": 0, "subsidy": 0},
            "raw": {"terminal_recovery": 120},
            "annual": {
                "financial_plan": [
                    {"period": 1, "phase": "建设期", "cumulative": 0},
                    {"period": 2, "phase": "运营期", "cumulative": 1040},
                ],
                "depreciation_table": [{"net_value": 100}],
                "amortization_table": [{"base": 0, "amortization": 0}],
                "profit_distribution": [{"net_profit": 20}],
                "debt_service": [{"end": 0}],
            },
        }
        schedule = build_balance_sheet_schedule(run)

        self.assertTrue(schedule["formal_ready"], schedule)
        self.assertEqual(schedule["rows"][0]["net_intangible_assets_wan"], 0.0)
        self.assertEqual(schedule["rows"][0]["equity_reconciliation_delta_wan"], 0.0)
        self.assertEqual(schedule["rows"][1]["cumulative_retained_earnings_wan"], 40.0)
        self.assertEqual(schedule["rows"][1]["equity_reconciliation_delta_wan"], 0.0)

    def test_monte_carlo_keeps_available_scenarios_with_risk_breaches(self) -> None:
        summary = run_monte_carlo(
            distributions=[{
                "field": "revenue_scale", "distribution": "uniform", "low": 0.9, "high": 1.1,
            }],
            sample_count=10,
            seed=7,
            rerun=lambda scales: {
                "available": True,
                "indicators": {
                    "project_irr_pct": scales["revenue_scale"] * 10,
                    "npv_wan": scales["revenue_scale"] * 100,
                },
                "risk_breaches": ["icr_below_threshold"],
            },
        )

        self.assertTrue(summary["available"])
        self.assertEqual(summary["successful_sample_count"], 10)
        self.assertEqual(summary["failure_categories"], {})
    def test_numbered_heading_is_read_and_replaced_without_duplication(self) -> None:
        base = (
            "# 报告\n\n"
            "## 6. 财务分析\n\n旧财务内容\n\n"
            "## 7. 风险分析\n\n旧风险内容\n\n"
            "## 8. 结论与建议\n\n旧结论\n"
        )

        span = report_read_model.section_span(base, "风险分析")
        self.assertIsNotNone(span)
        self.assertIn("旧风险内容", str((span or {})["content"]))

        merged = merge_single_chapter_proposal(base, "风险分析", "新的风险内容")
        self.assertIsNotNone(merged)
        self.assertEqual(str(merged).count("## 7. 风险分析"), 1)
        self.assertIn("新的风险内容", str(merged))
        self.assertNotIn("旧风险内容", str(merged))

        structure = validate_report_structure(
            str(merged),
            expected_chapters=["财务分析", "风险分析", "结论与建议"],
        )
        self.assertTrue(structure["ok"], structure)

    def test_duplicate_or_out_of_order_chapters_fail_structure_validation(self) -> None:
        duplicate = (
            "## 1. 项目概况\n\nA\n\n"
            "## 2. 风险分析\n\nB\n\n"
            "## 7. 风险分析\n\nC\n"
        )
        result = validate_report_structure(
            duplicate, expected_chapters=["项目概况", "风险分析"]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["duplicate_chapters"], ["风险分析"])
        self.assertIsNone(report_read_model.section_span(duplicate, "风险分析"))

        out_of_order = "## 2. 风险分析\n\nB\n\n## 1. 项目概况\n\nA\n"
        result = validate_report_structure(
            out_of_order, expected_chapters=["项目概况", "风险分析"]
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["out_of_order"])

    def test_report_validate_synchronizes_outer_blockers_into_readiness(self) -> None:
        record = {
            "object_id": "rev_public",
            "resource_uri": "lvke://reports/workspaces/ws/revisions/rev_public",
            "payload": {
                "native_revision_id": "rev_native",
                "basis_hash": "sha256:" + "a" * 64,
                "document_snapshot": {
                    "content": "# 报告\n\n财务结论 [source#page:1]",
                    "report_type": "generic_feasibility",
                },
                "upstream": {"run_id": "run-1", "outline": []},
            },
        }
        with (
            patch.object(
                report_validation,
                "resolve_revision_record",
                return_value=(record, False),
            ),
            patch.object(
                report_validation,
                "supplied_document_snapshot",
                return_value=record["payload"]["document_snapshot"],
            ),
            patch.object(
                report_validation.doc,
                "validate_report_structure",
                return_value={"ok": True, "issues": []},
            ),
            patch.object(
                report_validation.finance_gate,
                "verify_narrative_numbers",
                return_value={"ok": True},
            ),
            patch.object(
                report_validation.finance_gate,
                "assert_publish_finance_binding",
                return_value={"blockers": [{"code": "finance_binding_blocked"}]},
            ),
            patch.object(
                report_validation.report_artifacts,
                "build_readiness",
                return_value={
                    "publishable": True,
                    "blocking_issues": [],
                    "blockers": [],
                    "warnings": [],
                },
            ),
            patch.object(report_validation.PREPARATION_STORE, "list", return_value=[]),
        ):
            result = report_validation.validate_report("ws", "rev_public")

        self.assertTrue(result["valid"])
        self.assertFalse(result["quality_valid"])
        self.assertIn("finance_binding_blocked", result["quality_issues"])
        self.assertEqual(result["blockers"], [])
        self.assertTrue(result["readiness"]["publishable"])
        self.assertEqual(result["readiness"]["blocking_issues"], [])
        self.assertIn(
            "finance_binding_blocked",
            {item["code"] for item in result["readiness"]["quality_issues"]},
        )

    def test_acquisition_preview_records_release_limitation_without_blocking(self) -> None:
        record = {
            "object_id": "rev_public",
            "resource_uri": "lvke://reports/workspaces/ws/revisions/rev_public",
            "payload": {
                "native_revision_id": "rev_native",
                "basis_hash": "sha256:" + "a" * 64,
                "report_preparation_id": "prep-1",
                "document_snapshot": {
                    "content": "# 报告\n\n收购价 2000 万元 [source#page:1]",
                    "report_type": "generic_feasibility",
                },
                "upstream": {
                    "run_id": "acqrun_preview",
                    "finance_tables_package_id": "package-preview",
                    "finance_binding": {
                        "kind": "asset_acquisition",
                        "run_id": "acqrun_preview",
                        "package_id": "package-preview",
                    },
                    "outline": [],
                },
            },
        }
        with (
            patch.object(report_validation, "resolve_revision_record", return_value=(record, False)),
            patch.object(
                report_validation,
                "supplied_document_snapshot",
                return_value=record["payload"]["document_snapshot"],
            ),
            patch.object(
                report_validation.doc,
                "validate_report_structure",
                return_value={"ok": True, "issues": []},
            ),
            patch.object(
                report_validation.finance_gate,
                "verify_narrative_numbers",
                return_value={"ok": True},
            ),
            patch(
                "lvke_mcp.domains.asset_acquisition.backend.get_run",
                return_value={"delivery_mode": "estimate_preview"},
            ),
            patch.object(
                report_validation.finance_gate,
                "assert_acquisition_report_finance_binding",
                return_value={
                    "ok": True,
                    "blockers": [],
                    "warnings": [{"code": "finance_acquisition_preview_only", "message": "preview"}],
                    "validation_level": "preview",
                    "formal_release_eligible": False,
                },
            ),
            patch.object(
                report_validation.report_artifacts,
                "build_readiness",
                return_value={
                    "publishable": True,
                    "blocking_issues": [],
                    "blockers": [],
                    "warnings": [],
                },
            ) as readiness,
            patch.object(
                report_validation.PREPARATION_STORE,
                "list",
                return_value=[{"object_id": "prep-1", "created_at": "2026-08-13T12:00:00Z"}],
            ),
        ):
            result = report_validation.validate_report("ws", "rev_public")

        self.assertTrue(result["success"])
        self.assertTrue(result["technical_ready"])
        self.assertTrue(result["formal_release_eligible"])
        self.assertTrue(result["readiness"]["publishable"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["finance_binding"]["validation_level"], "preview")
        self.assertIn("preview", result["warnings"])
        self.assertEqual(readiness.call_args.kwargs["expected_chapters"], [])

    def test_report_readiness_keeps_partial_validation_actionable(self) -> None:
        revision = {
            "object_id": "rrv_partial",
            "payload": {},
        }
        validation = {
            "success": True,
            "status": "partial",
            "run_id": "run-1",
            "finance_tables_package_id": "ftp-1",
            "basis_hash": "sha256:" + "a" * 64,
            "readiness": {"publishable": True},
            "resource_uris": ["lvke://reports/workspaces/ws/revisions/rrv_partial"],
            "warnings": ["质量提示：research_package_required"],
            "blockers": [],
            "quality_issues": ["research_package_required"],
            "next_actions": ["可直接导出"],
        }
        with (
            patch.object(report_application.REVISION_STORE, "get", return_value=revision),
            patch(
                "lvke_mcp.domains.reports._service.generation.validate",
                return_value=validation,
            ),
        ):
            result = report_application.readiness("ws", "rrv_partial")

        self.assertTrue(result["success"])
        self.assertTrue(result["business_success"])
        self.assertTrue(result["completed"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["quality_issues"], ["research_package_required"])
        self.assertEqual(result["release_limitations"], ["research_package_required"])

    def test_render_manifest_describes_structured_tables_not_stale_run_manifest(self) -> None:
        structured = {
            key: {
                "columns": [{"key": "value", "label": "值"}],
                "rows": [[index]],
            }
            for index, key in enumerate(DELIVERY_TABLE_KEYS, start=1)
        }
        captured: dict = {}
        fake_record = {
            "object_id": "ftp_package",
            "resource_uri": "lvke://finance-tables/workspaces/ws/packages/ftp_package",
            "payload": {},
        }

        def put(_workspace: str, payload: dict, **_kwargs: object) -> dict:
            captured.update(payload)
            fake_record["payload"] = payload
            return fake_record

        with (
            patch(
                "lvke_mcp.domains.finance.run_service.render_workspace_finance_tables",
                return_value={
                    "ok": True,
                    "template_version": "finance_tables.v3",
                    "table_bundle_hash": "sha256:" + "b" * 64,
                    "table_manifest": [
                        {"table_id": key} for key in DELIVERY_TABLE_KEYS[:-1]
                    ],
                },
            ),
            patch.object(tables_service, "_structured_delivery_tables", return_value=structured),
            patch.object(
                tables_service,
                "_load_run",
                return_value={"available": True, "evidence_policy": "formal_evidence"},
            ),
            patch.object(
                tables_service,
                "_delivery_assessment",
                return_value={
                    "valid": True,
                    "validation_complete": False,
                    "blockers": [],
                    "warnings": [],
                },
            ),
            patch.object(tables_service.PACKAGE_STORE, "put", side_effect=put),
        ):
            result = tables_service.render("ws", "run-1")

        self.assertTrue(result["success"])
        manifest = captured["table_manifest"]
        self.assertEqual(
            [item["table_id"] for item in manifest],
            list(DELIVERY_TABLE_KEYS),
        )
        debt = next(item for item in manifest if item["table_id"] == "debt-service")
        self.assertEqual(debt["run_id"], "run-1")
        self.assertEqual(debt["content_hash"], sha256_json(structured["debt-service"]))

    def test_structured_package_ignores_stale_legacy_missing_keys(self) -> None:
        structured = {
            key: {
                "columns": [{"key": "value", "label": "值"}],
                "rows": [[index]],
            }
            for index, key in enumerate(DELIVERY_TABLE_KEYS, start=1)
        }
        validation = tables_application.validate_render({
            "tables": structured,
            "table_manifest": tables_application.structured_table_manifest(
                "run-1",
                "finance_tables.v3",
                structured,
            ),
            "missing_delivery_keys": list(DELIVERY_TABLE_KEYS),
        })

        self.assertTrue(validation["valid"], validation)
        self.assertEqual(validation["missing_delivery_keys"], [])
        self.assertNotIn("renderer_missing_delivery_keys", validation["blockers"])

    def test_partial_finance_package_is_generated_with_quality_diagnostics(self) -> None:
        evidence = {"basis_hash": "sha256:" + "c" * 64, "payload": {}}
        research = {"status": "done", "basis_hash": "sha256:" + "d" * 64}
        package = {
            "object_id": "ftp_partial",
            "status": "partial",
            "basis_hash": "sha256:" + "e" * 64,
            "payload": {"run_id": "run-1", "validation_complete": False},
        }
        stored = {}

        def put(_workspace: str, payload: dict, **_kwargs: object) -> dict:
            stored.update(payload)
            return {
                "object_id": "rprep_1",
                "basis_hash": "sha256:" + "f" * 64,
                "resource_uri": "lvke://reports/workspaces/ws/preparations/rprep_1",
            }

        with (
            patch.object(report_application.EVIDENCE_STORE, "get", return_value=evidence),
            patch.object(report_application.RESEARCH_STORE, "get", return_value=research),
            patch.object(report_application.TABLE_STORE, "get", return_value=package),
            patch(
                "lvke_mcp.domains.finance.run_service.get_workspace_finance_run",
                return_value={"available": True, "spec_hash": "sha256:" + "1" * 64},
            ),
            patch.object(report_application.PREPARATION_STORE, "put", side_effect=put),
        ):
            result = report_application.prepare({
                "workspace_id": "ws",
                "evidence_pack_ids": ["ev-1"],
                "research_package_ids": ["research-1"],
                "finance_binding": {
                    "kind": "generic_feasibility",
                    "run_id": "run-1",
                    "package_id": "ftp_partial",
                },
            })

        self.assertTrue(result["success"])
        self.assertTrue(result["draft_ready"])
        self.assertTrue(result["formal_ready"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["formal_blockers"], [])
        self.assertIn("finance_tables_package_not_formal", result["quality_issues"])
        self.assertTrue(stored["formal_ready"])
        self.assertIn("finance_tables_package_not_formal", stored["quality_issues"])


if __name__ == "__main__":
    unittest.main()
