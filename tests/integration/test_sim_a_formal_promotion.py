from __future__ import annotations

import json
import os
import tempfile
import unittest

from lvke_mcp.runtime.evidence_qualification import project_fact_may_be_certified
from lvke_mcp.servers.lvke_feasibility_delivery import service as feasibility
from lvke_mcp.servers.lvke_zero_material_delivery import service as zmd


class SimAFormalPromotionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-sim-a-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "sim-a-promo"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_sim_a_formal_can_certify_with_certified_parents(self) -> None:
        parent = {
            "evidence_policy": "sim_a_formal",
            "project_fact_certified": True,
        }
        self.assertTrue(
            project_fact_may_be_certified(
                "sim_a_formal",
                own_qualification_passed=True,
                parents=[parent],
            )
        )
        self.assertFalse(
            project_fact_may_be_certified(
                "sim_a_formal",
                own_qualification_passed=True,
                parents=[{"evidence_policy": "controlled_assumption", "project_fact_certified": True}],
            )
        )

    def test_partial_confirmation_cannot_generate_pack(self) -> None:
        created = zmd.create_from_sentence(
            {
                "workspace_id": self.workspace,
                "sentence": "在湖北建设一座儿童游乐园并编制可行性研究报告",
                "project_name": "湖北儿童游乐园",
                "region": "湖北省",
                "idempotency_key": "create-partial",
            }
        )
        run_id = str((created.get("delivery_run") or {}).get("delivery_run_id") or "")
        started = zmd.start(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": run_id,
                "idempotency_key": "start-partial",
            }
        )
        package_id = str((started.get("assumption_package") or {}).get("assumption_package_id") or "")
        if not package_id:
            package_id = str((started.get("delivery_run") or {}).get("assumption_package_id") or "")
        listed = zmd.list_assumptions(
            {
                "workspace_id": self.workspace,
                "assumption_package_id": package_id,
            }
        )
        items = list(listed.get("confirmation_items") or [])
        self.assertGreaterEqual(len(items), 2, listed)
        confirmed = zmd.confirm_assumptions(
            {
                "workspace_id": self.workspace,
                "assumption_package_id": package_id,
                "confirmations": [
                    {
                        "name": items[0]["name"],
                        "value": items[0].get("value"),
                        "note": "只确认一项",
                    }
                ],
                "idempotency_key": "confirm-partial",
            }
        )
        self.assertTrue(confirmed.get("success"), confirmed)
        packed = zmd.generate_template_pack(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": str(
                    (confirmed.get("delivery_run") or {}).get("delivery_run_id") or ""
                ),
                "idempotency_key": "pack-partial",
            }
        )
        self.assertFalse(packed.get("success"))
        self.assertEqual(packed.get("code"), "assumptions_not_confirmed")

    def test_unconfirmed_assumptions_cannot_generate_pack(self) -> None:
        created = zmd.create_from_sentence(
            {
                "workspace_id": self.workspace,
                "sentence": "在湖北建设一座儿童游乐园并编制可行性研究报告",
                "project_name": "湖北儿童游乐园",
                "region": "湖北省",
                "idempotency_key": "create-v1",
            }
        )
        self.assertTrue(created.get("success"), created)
        run_id = str((created.get("delivery_run") or {}).get("delivery_run_id") or "")
        started = zmd.start(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": run_id,
                "idempotency_key": "start-v1",
            }
        )
        self.assertTrue(started.get("success"), started)
        preview_run_id = str((started.get("delivery_run") or {}).get("delivery_run_id") or run_id)
        packed = zmd.generate_template_pack(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": preview_run_id,
                "idempotency_key": "pack-unconfirmed",
            }
        )
        self.assertFalse(packed.get("success"))
        self.assertEqual(packed.get("code"), "assumptions_not_confirmed")

    def _confirmed_run(self) -> dict[str, str]:
        created = zmd.create_from_sentence(
            {
                "workspace_id": self.workspace,
                "sentence": "在湖北建设一座儿童游乐园并编制可行性研究报告",
                "project_name": "湖北儿童游乐园",
                "region": "湖北省",
                "idempotency_key": "create-confirmed",
            }
        )
        run_id = str((created.get("delivery_run") or {}).get("delivery_run_id") or "")
        started = zmd.start(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": run_id,
                "idempotency_key": "start-confirmed",
            }
        )
        package_id = str((started.get("assumption_package") or {}).get("assumption_package_id") or "")
        if not package_id:
            listed = zmd.list_assumptions(
                {
                    "workspace_id": self.workspace,
                    "assumption_package_id": str(
                        ((started.get("delivery_run") or {}).get("assumption_package_id") or "")
                    ),
                }
            )
            package_id = str(listed.get("assumption_package_id") or "")
        if not package_id:
            package_id = str((started.get("delivery_run") or {}).get("assumption_package_id") or "")
        confirmed = None
        for index in range(8):
            listed = zmd.list_assumptions(
                {
                    "workspace_id": self.workspace,
                    "assumption_package_id": package_id,
                }
            )
            items = list(listed.get("confirmation_items") or [])
            if not items:
                break
            confirmed = zmd.confirm_assumptions(
                {
                    "workspace_id": self.workspace,
                    "assumption_package_id": package_id,
                    "confirmations": [
                        {
                            "name": item["name"],
                            "value": item.get("value"),
                            "note": "测试确认",
                        }
                        for item in items
                    ],
                    "idempotency_key": f"confirm-v{index}",
                }
            )
            self.assertTrue(confirmed.get("success"), confirmed)
            package_id = str(
                (confirmed.get("assumption_package") or {}).get("assumption_package_id") or package_id
            )
        self.assertIsNotNone(confirmed)
        return {
            "delivery_run_id": str((confirmed.get("delivery_run") or {}).get("delivery_run_id") or ""),
            "assumption_package_id": package_id,
        }

    def test_promotion_requires_responsible_party_and_keeps_seals_empty(self) -> None:
        ids = self._confirmed_run()
        packed = zmd.generate_template_pack(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": ids["delivery_run_id"],
                "idempotency_key": "pack-ok",
            }
        )
        self.assertTrue(packed.get("success"), packed)
        self.assertTrue(packed.get("template_pack_id"))
        mappings = [
            item for item in (packed.get("template_pack") or {}).get("files") or []
            if item.get("kind") == "json" and str(item.get("filename") or "").endswith(".json")
        ]
        self.assertTrue(mappings, packed)
        markdowns = [
            item for item in (packed.get("template_pack") or {}).get("files") or []
            if item.get("kind") == "markdown"
        ]
        self.assertEqual(len(markdowns), len(mappings))
        document = json.loads(mappings[0]["text"])
        seal_rows = [
            row for row in document.get("rows") or []
            if row.get("name") in {
                "official_seal",
                "official_document_no",
                "approval_no",
                "bank_statement_ref",
                "inspection_conclusion",
                "audit_conclusion",
            }
        ]
        self.assertEqual(len(seal_rows), 6)
        for row in seal_rows:
            self.assertIsNone(row.get("value"))
            self.assertEqual(row.get("status"), "interface_only")
            self.assertTrue(row.get("replacement_condition"))
        blocked = zmd.confirm_formal_promotion(
            {
                "workspace_id": self.workspace,
                "template_pack_id": packed["template_pack_id"],
                "responsible_party": "",
                "confirmation_note": "确认晋升",
                "idempotency_key": "promo-missing",
            }
        )
        self.assertFalse(blocked.get("success"))
        self.assertEqual(blocked.get("code"), "missing_inputs")
        promoted = zmd.confirm_formal_promotion(
            {
                "workspace_id": self.workspace,
                "template_pack_id": packed["template_pack_id"],
                "responsible_party": "项目负责人 测试",
                "confirmation_note": "确认将拟定模板包导入新可研链",
                "idempotency_key": "promo-ok",
            }
        )
        self.assertTrue(promoted.get("success"), promoted)
        self.assertTrue(promoted.get("promotion_id"))
        self.assertTrue(promoted.get("file_ids"))
        self.assertEqual(promoted.get("next_actions"), ["project_context_create", "feasibility_start"])
        self.assertNotIn("feasibility_release", promoted.get("next_actions") or [])
        from lvke_mcp.adapters.data_analysis_repository import INGEST_STORE
        from lvke_mcp.servers.lvke_data_analysis._service import ingest as analysis_ingest
        from lvke_mcp.servers.lvke_source_files import service as source_files

        file_id = str(promoted["file_ids"][0])
        stored = source_files.get_source_file(self.workspace, file_id)
        source_record = stored.get("source_file") or {}
        self.assertEqual(source_record.get("evidence_policy"), "sim_a_formal")
        self.assertEqual(source_record.get("evidence_origin"), "sim_a_template")
        ingested = analysis_ingest.ingest(self.workspace, [], list(promoted["file_ids"]))
        self.assertTrue(ingested.get("success"), ingested)
        ingest_record = INGEST_STORE.get(self.workspace, ingested["analysis_task_id"])
        documents = ((ingest_record or {}).get("payload") or {}).get("documents") or []
        self.assertTrue(documents)
        self.assertEqual(documents[0].get("evidence_policy"), "sim_a_formal")
        self.assertEqual(documents[0].get("evidence_origin"), "sim_a_template")
        from lvke_mcp.testing.sim_a_formal_acceptance import run_sim_a_formal_finance

        finance = run_sim_a_formal_finance(
            workspace_id=self.workspace,
            file_ids=list(promoted["file_ids"]),
            project_name="晋升财务链",
            industry_code="tourism_catering",
            case_key="promo-finance",
        )
        self.assertTrue(finance.get("ok"), finance)
        self.assertTrue(finance.get("evidence_pack_id"))
        self.assertTrue(finance.get("finance_run_id"))
        self.assertIn(finance.get("evidence_policy"), {"sim_a_formal", "formal_evidence"})
        from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
        from lvke_mcp.adapters.finance_model_repository import BASIS_OF_ESTIMATE_STORE, SPEC_STORE

        pack_record = EVIDENCE_STORE.get(self.workspace, finance["evidence_pack_id"]) or {}
        self.assertEqual((pack_record.get("payload") or {}).get("evidence_policy"), "sim_a_formal")
        spec_record = SPEC_STORE.get(self.workspace, finance["finance_spec_id"]) or {}
        self.assertEqual((spec_record.get("payload") or {}).get("evidence_policy"), "sim_a_formal")
        if finance.get("basis_of_estimate_id"):
            boe_record = BASIS_OF_ESTIMATE_STORE.get(self.workspace, finance["basis_of_estimate_id"]) or {}
            self.assertEqual((boe_record.get("payload") or {}).get("evidence_policy"), "sim_a_formal")
            self.assertTrue((boe_record.get("payload") or {}).get("project_fact_certified"))

    def test_unpromoted_preview_still_forbids_formal_and_sim_a_does_not(self) -> None:
        passed, blockers, _warnings = feasibility._validation(  # noqa: SLF001
            {
                "delivery_mode": "estimate_preview",
                "evidence_policy": "controlled_assumption",
                "release_scope": "project_delivery",
                "stages": {},
            },
            "formal",
            "",
        )
        self.assertFalse(passed)
        self.assertIn("controlled_assumption_formal_forbidden", blockers)
        self.assertIn("preview_cannot_formal_release", blockers)
        _passed_sim, sim_blockers, _warnings = feasibility._validation(  # noqa: SLF001
            {
                "delivery_mode": "formal_release",
                "evidence_policy": "sim_a_formal",
                "release_scope": "project_delivery",
                "project_fact_certified": True,
                "stages": {},
            },
            "formal",
            "sim-a-ws",
        )
        self.assertNotIn("controlled_assumption_formal_forbidden", sim_blockers)

    def test_sim_a_formal_skips_professional_pending_findings(self) -> None:
        from lvke_mcp.servers.lvke_deliverable_review._service.executor import _execute_rules

        pack = {
            "rule_pack_id": "finance-report-core",
            "version": "1.0.0",
            "applicable_rules": ["FR-FIN-001", "FR-REP-001"],
            "rule_sources": [
                {
                    "check_kind": "professional",
                    "rule_id": "FR-FIN-001",
                    "title": "十三表勾稽专业核验",
                    "target_kinds": ["report_revision"],
                    "severity": "P1",
                },
                {
                    "check_kind": "professional",
                    "rule_id": "FR-REP-001",
                    "title": "九章数字专业核验",
                    "target_kinds": ["report_revision"],
                    "severity": "P1",
                },
            ],
        }
        payload = {
            "target": {"target_type": "report_revision", "target_id": "rev_sim_a"},
            "project_context": {"evidence_track": "sim_a_formal"},
            "rule_pack": pack,
            "standards": {},
            "mandatory_findings": [],
        }
        executed = _execute_rules(self.workspace, payload, "quick")
        pending = [
            row for row in executed.get("findings") or []
            if row.get("category") == "professional_review_pending"
        ]
        self.assertEqual(pending, [], executed.get("findings"))
        self.assertIn("FR-FIN-001", (executed.get("coverage") or {}).get("executed_rules") or [])
        real_payload = {**payload, "project_context": {"evidence_track": "real"}}
        real_executed = _execute_rules(self.workspace, real_payload, "quick")
        real_pending = [
            row for row in real_executed.get("findings") or []
            if row.get("category") == "professional_review_pending"
        ]
        self.assertEqual(len(real_pending), 2, real_executed.get("findings"))


if __name__ == "__main__":
    unittest.main()
