"""CSV 导出需要一个诚实的过程验收出口，而不是把正式门禁删掉。

此前正式门禁未过时 CSV 完全导不出，于是"想要过程文件"的压力全落在门禁本身。
修复后 ``validation_scope='technical'`` 是独立出口：产物照出，但每个文件首行写入
不可正式使用标记，``validation_complete`` 恒为 false，``release_grade`` 为
``technical_preview``。formal 档不放宽任何一条。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.domains.finance import tables_service
from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
from lvke_mcp.domains.finance.model_application import run_model

_BANNER_HEAD = "【技术预览·不可正式使用】"


class CsvTechnicalPreviewScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-csv-scope-")
        self.previous_data_dir = os.environ.get("LVKE_MCP_DATA_DIR")
        self.previous_golden_root = os.environ.get("LVKE_GOLDEN_DATA_ROOT")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        os.environ["LVKE_GOLDEN_DATA_ROOT"] = str(
            Path(__file__).resolve().parents[2] / "docs"
        )
        self.workspace = "csv-scope-test"
        scenario = next(
            item
            for item in build_industry_scenarios("transport_logistics")
            if item["archetype_id"] == "urban_rail" and item["variant_id"] == "base"
        )
        result = run_model(
            {
                "workspace_id": self.workspace,
                "spec": scenario["spec"],
                "input_revision": scenario["finance"],
                "mode": "estimate_preview",
                "idempotency_key": "csv-scope-run",
            }
        )
        self.assertTrue(result["success"], result)
        self.run_id = result["run_id"]
        self.package_id = tables_service.render(self.workspace, self.run_id)[
            "finance_tables_package_id"
        ]

    def tearDown(self) -> None:
        for name, previous in (
            ("LVKE_MCP_DATA_DIR", self.previous_data_dir),
            ("LVKE_GOLDEN_DATA_ROOT", self.previous_golden_root),
        ):
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self.tempdir.cleanup()

    def _export(self, scope: str) -> dict:
        return tables_service.export_csv(
            self.workspace, self.run_id, "", self.package_id, scope
        )

    def test_invalid_scope_is_rejected(self) -> None:
        exported = self._export("whatever")
        self.assertEqual(exported["code"], "validation_scope_invalid")

    def test_technical_scope_produces_fourteen_files(self) -> None:
        exported = self._export("technical")
        self.assertTrue(exported.get("csv_resource_uris"), exported)
        directory = Path(exported["deliverable_path"])
        self.assertEqual(len(sorted(directory.glob("*.csv"))), 14)

    def test_every_technical_file_carries_the_in_file_marking(self) -> None:
        exported = self._export("technical")
        directory = Path(exported["deliverable_path"])
        for path in sorted(directory.glob("*.csv")):
            with self.subTest(file=path.name):
                first_line = path.read_text(encoding="utf-8-sig").splitlines()[0]
                self.assertIn(_BANNER_HEAD, first_line)

    def test_technical_export_never_claims_formal_grade(self) -> None:
        exported = self._export("technical")
        self.assertEqual(exported["validation_scope"], "technical")
        self.assertEqual(exported["release_grade"], "technical_preview")
        self.assertTrue(exported["technical_preview"])
        self.assertTrue(exported["not_for_formal_use"])
        self.assertFalse(exported["validation_complete"])
        self.assertEqual(exported["delivery_mode"], "draft")
        self.assertIn("csv_technical_preview_not_releasable", exported["blockers"])

    def test_technical_manifest_records_its_scope(self) -> None:
        exported = self._export("technical")
        from lvke_mcp.adapters.finance_tables_repository import CSV_EXPORT_STORE

        record = CSV_EXPORT_STORE.get(self.workspace, exported["csv_manifest_id"])
        payload = (record or {}).get("payload") or {}
        self.assertEqual(payload["validation_scope"], "technical")
        self.assertEqual(payload["release_grade"], "technical_preview")

    def test_technical_export_still_verifies_its_own_integrity(self) -> None:
        exported = self._export("technical")
        integrity = exported.get("csv_integrity") or {}
        self.assertTrue(integrity.get("valid"), integrity)
        self.assertEqual(integrity["verified_table_count"], 13)

    def test_technical_export_still_binds_the_same_package(self) -> None:
        exported = self._export("technical")
        self.assertEqual(exported["source_package_id"], self.package_id)
        self.assertEqual(exported["source_run_id"], self.run_id)
        self.assertTrue(exported["manifest_hash"])

    def test_formal_scope_stays_closed_when_the_gate_fails(self) -> None:
        # 本 run 尚未通过正式财务门禁（缺 BoE / 分年资金计划），
        # formal 档必须继续拒绝，绝不因为存在 technical 出口而放宽。
        rendered = tables_service.render(self.workspace, self.run_id)
        self.assertFalse(rendered.get("validation_complete"))
        exported = self._export("formal")
        self.assertFalse(exported.get("validation_complete"))
        self.assertNotEqual(exported.get("release_grade"), "technical_preview")

    def test_formal_default_does_not_mark_files(self) -> None:
        before = sorted(Path(self.tempdir.name).rglob("*.csv"))
        exported = self._export("formal")
        self.assertEqual(exported["status"], "blocked")
        self.assertEqual(exported["code"], "tables_validation_failed")
        self.assertEqual(exported["resource_uris"], [])
        self.assertNotIn("csv_resource_uris", exported)
        self.assertNotIn("deliverable_path", exported)
        self.assertEqual(sorted(Path(self.tempdir.name).rglob("*.csv")), before)


if __name__ == "__main__":
    unittest.main()
