from __future__ import annotations

import unittest
from unittest.mock import patch

from lvke_mcp.domains.finance import tables_service
from lvke_mcp.domains.finance.run_service import DELIVERY_TABLE_KEYS
from lvke_mcp.domains.reports import application as report_application
from lvke_mcp.domains.reports import validation as report_validation
from lvke_mcp.runtime.storage import sha256_json


class ReportAndFinanceRegressionTest(unittest.TestCase):
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

        self.assertFalse(result["valid"])
        self.assertIn("finance_binding_blocked", result["blockers"])
        self.assertFalse(result["readiness"]["publishable"])
        self.assertIn("finance_binding_blocked", result["readiness"]["blocking_issues"])
        self.assertIn(
            "finance_binding_blocked",
            {item["code"] for item in result["readiness"]["blockers"]},
        )

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

    def test_partial_finance_package_is_draft_ready_but_not_formal_ready(self) -> None:
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
        self.assertFalse(result["formal_ready"])
        self.assertTrue(result["ready"])
        self.assertIn("finance_tables_package_not_formal", result["formal_blockers"])
        self.assertFalse(stored["formal_ready"])


if __name__ == "__main__":
    unittest.main()
