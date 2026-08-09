"""delivery_status 不得用 status=ok 掩盖 artifact_failed。

此前顶层返回 ``status=ok``、``blockers=[]``，而内部 ``domain_results`` 其实是
artifact_failed，同时把 DOCX 与 XLSX 的 URI 一并列出——调用方据此以为工件可交付。

修复后：
- ``query_success`` 只说明"这次查询成功"，与交付状态严格分离
- ``domain_status`` 严格三态 ``ready|partial|blocked``
- 每个工件带 ``usable`` / ``validation_status`` / ``release_grade``
- 中间对象（spec/context）标 ``is_deliverable=false``，既不假称可用也不算交付失败
"""

from __future__ import annotations

import os
import tempfile
import unittest

import jsonschema

from lvke_mcp.servers.lvke_zero_material_delivery import service
from lvke_mcp.servers.lvke_zero_material_delivery._service.lifecycle import (
    _artifact_kind,
    _artifact_states,
    _delivery_state,
    _domain_status,
)
from lvke_mcp.servers.lvke_zero_material_delivery.server import build_server


class ArtifactClassificationTest(unittest.TestCase):
    def test_kinds_are_resolved_by_collection_not_domain(self) -> None:
        cases = {
            "lvke://finance-model/workspaces/w/runs/fr_a": "finance_run",
            "lvke://finance-model/workspaces/w/specs/fsp_a": "finance_spec",
            "lvke://finance-tables/workspaces/w/packages/ftp_a": "finance_tables_package",
            "lvke://finance-tables/workspaces/w/packages/ftp_a/xlsx": "xlsx",
            "lvke://finance-tables/workspaces/w/packages/ftp_a/csv/investment": "csv",
            "lvke://project-planning/workspaces/w/project-contexts/pctx_a": "project_context",
        }
        for uri, expected in cases.items():
            with self.subTest(uri=uri):
                self.assertEqual(_artifact_kind(uri), expected)

    def test_failed_deliverable_is_marked_unusable(self) -> None:
        run = {
            "stage": "tables_ready",
            "blockers": ["xlsx_export_failed"],
            "artifact_uris": ["lvke://finance-tables/workspaces/w/packages/ftp_a/xlsx"],
            "domain_results": {"xlsx_status": "failed"},
        }
        states = _artifact_states(run)
        self.assertEqual(len(states), 1)
        self.assertFalse(states[0]["usable"])
        self.assertEqual(states[0]["validation_status"], "failed")
        self.assertEqual(states[0]["release_grade"], "unavailable")
        self.assertTrue(states[0]["is_deliverable"])

    def test_passing_deliverable_is_preview_grade_never_formal(self) -> None:
        run = {
            "stage": "tables_ready",
            "blockers": [],
            "artifact_uris": ["lvke://finance-tables/workspaces/w/packages/ftp_a/xlsx"],
            "domain_results": {"xlsx_status": "ok"},
        }
        states = _artifact_states(run)
        self.assertTrue(states[0]["usable"])
        # 零材料链恒为预览级，绝不因为单个工件通过就抬到 formal。
        self.assertEqual(states[0]["release_grade"], "technical_preview")

    def test_intermediate_objects_are_not_counted_as_failed_deliverables(self) -> None:
        run = {
            "stage": "finance_ready",
            "blockers": [],
            "artifact_uris": ["lvke://finance-model/workspaces/w/specs/fsp_a"],
            "domain_results": {},
        }
        states = _artifact_states(run)
        self.assertFalse(states[0]["is_deliverable"])
        self.assertEqual(states[0]["validation_status"], "not_a_deliverable")
        self.assertEqual(states[0]["blocking_reasons"], [])


class DeliveryStateTest(unittest.TestCase):
    def test_ready_requires_usable_deliverables_and_no_blockers(self) -> None:
        states = [
            {"uri": "u", "usable": True, "is_deliverable": True},
        ]
        self.assertEqual(_delivery_state({"stage": "preview_ready", "blockers": []}, states), "ready")

    def test_unusable_deliverable_forces_partial(self) -> None:
        states = [{"uri": "u", "usable": False, "is_deliverable": True}]
        self.assertEqual(
            _delivery_state({"stage": "preview_ready", "blockers": []}, states), "partial"
        )

    def test_blockers_without_deliverables_is_blocked(self) -> None:
        self.assertEqual(
            _delivery_state({"stage": "finance_ready", "blockers": ["x_failed"]}, []),
            "blocked",
        )

    def test_cancelled_run_reports_cancelled(self) -> None:
        self.assertEqual(_delivery_state({"stage": "cancelled", "blockers": []}, []), "cancelled")

    def test_domain_status_is_strictly_three_valued(self) -> None:
        for state, expected in (
            ("ready", "ready"),
            ("partial", "partial"),
            ("in_progress", "partial"),
            ("blocked", "blocked"),
            ("cancelled", "blocked"),
        ):
            with self.subTest(state=state):
                self.assertEqual(_domain_status(state), expected)


class DeliveryStatusEndToEndTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-status-honesty-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "status-honesty-test"
        created = service.create_from_sentence(
            {
                "workspace_id": self.workspace,
                "sentence": (
                    "为某市做一条50公里、10站、设计速度120km/h、"
                    "2028至2032年建设的城市轨道交通线路可行性研究"
                ),
                "idempotency_key": "status-honesty-sentence",
            }
        )
        started = service.start(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": created["delivery_run"]["delivery_run_id"],
                "idempotency_key": "status-honesty-start",
            }
        )
        self.run_id = started.get("delivery_run", {}).get(
            "delivery_run_id", created["delivery_run"]["delivery_run_id"]
        )

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _status(self) -> dict:
        return service.status(
            {"workspace_id": self.workspace, "delivery_run_id": self.run_id}
        )

    def test_query_success_is_separate_from_delivery_state(self) -> None:
        status = self._status()
        self.assertTrue(status["query_success"])
        self.assertNotEqual(status["domain_status"], "ready")

    def test_top_level_status_no_longer_claims_ok(self) -> None:
        status = self._status()
        # 内部有 blocker 时顶层绝不能是 ok。
        self.assertTrue(status["blockers"])
        self.assertNotEqual(status["status"], "ok")

    def test_scale_blocker_surfaces_at_top_level(self) -> None:
        status = self._status()
        self.assertIn("project_scale_inconsistent", status["blockers"])

    def test_every_artifact_carries_usability_fields(self) -> None:
        status = self._status()
        for artifact in status["artifacts"]:
            with self.subTest(uri=artifact["uri"]):
                self.assertIn("usable", artifact)
                self.assertIn("validation_status", artifact)
                self.assertIn("release_grade", artifact)

    def test_no_artifact_claims_usable_while_preview_is_not_ready(self) -> None:
        status = self._status()
        self.assertFalse(status["technical_preview_ready"])
        self.assertEqual(status["usable_artifact_count"], 0)

    def test_warnings_explain_why_nothing_is_deliverable(self) -> None:
        status = self._status()
        self.assertTrue(status["warnings"])
        self.assertTrue(
            any("technical_preview_ready=false" in item for item in status["warnings"]),
            status["warnings"],
        )

    def test_get_artifacts_reports_the_same_honesty(self) -> None:
        artifacts = service.get_artifacts(
            {"workspace_id": self.workspace, "delivery_run_id": self.run_id}
        )
        self.assertTrue(artifacts["query_success"])
        self.assertNotEqual(artifacts["domain_status"], "ready")
        self.assertEqual(artifacts["usable_artifact_count"], 0)
        for artifact in artifacts["artifacts"]:
            self.assertIn("release_grade", artifact)

    def test_both_tools_validate_against_their_output_schema(self) -> None:
        specs = {item.name: item for item in build_server().tool_specs}
        payloads = {
            "delivery_status": self._status(),
            "delivery_get_artifacts": service.get_artifacts(
                {"workspace_id": self.workspace, "delivery_run_id": self.run_id}
            ),
        }
        for name, payload in payloads.items():
            with self.subTest(tool=name):
                spec = specs[name]
                schema = getattr(spec, "output_schema", None) or getattr(
                    spec, "outputSchema", None
                )
                jsonschema.validate(payload, schema)

    def test_output_schema_pins_domain_status_to_three_values(self) -> None:
        specs = {item.name: item for item in build_server().tool_specs}
        spec = specs["delivery_status"]
        schema = getattr(spec, "output_schema", None) or getattr(
            spec, "outputSchema", None
        )
        self.assertEqual(
            schema["properties"]["domain_status"]["enum"],
            ["ready", "partial", "blocked"],
        )
        for field in ("query_success", "domain_status", "delivery_state", "artifacts"):
            self.assertIn(field, schema["required"])


if __name__ == "__main__":
    unittest.main()
