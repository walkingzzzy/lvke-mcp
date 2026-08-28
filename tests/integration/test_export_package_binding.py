"""XLSX/CSV 必须绑定同一 finance_tables_package_id。

此前两个导出工具都不接受 package_id，各自内部重新 ``render()``；package payload
内嵌可变的跨工件门禁状态（validation），content hash 随之漂移，于是主包是
ftp_431… 而 XLSX 落成 ftp_548…，绑的是它自己新造的包。

修复后：给定 package_id 即消费既有包，并在工件上记录 source_package_id /
source_run_id / manifest_hash；package 与 run 不匹配时 fail-closed。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.domains.finance import tables_application, tables_service
from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
from lvke_mcp.domains.finance.model_application import run_model
from lvke_mcp.domains.finance.run_service import (
    DELIVERY_TABLE_KEYS,
    delivery_table_contract_hash,
)


def _scenario(industry: str, archetype: str) -> dict:
    return next(
        item
        for item in build_industry_scenarios(industry)
        if item["archetype_id"] == archetype and item["variant_id"] == "base"
    )


class ExportPackageBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-export-binding-")
        self.previous_data_dir = os.environ.get("LVKE_MCP_DATA_DIR")
        self.previous_golden_root = os.environ.get("LVKE_GOLDEN_DATA_ROOT")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        os.environ["LVKE_GOLDEN_DATA_ROOT"] = str(
            Path(__file__).resolve().parents[2] / "docs"
        )
        self.workspace = "export-binding-test"
        scenario = _scenario("agriculture_food", "grain_processing")
        result = run_model(
            {
                "workspace_id": self.workspace,
                "spec": scenario["spec"],
                "input_revision": scenario["finance"],
                "mode": "estimate_preview",
                "idempotency_key": "export-binding-run",
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

    def test_package_records_the_spec_it_was_built_from(self) -> None:
        """package 必须自证绑定哪个 confirmed Spec。

        CSV/XLSX 都从 package 派生，只记 run_id 时脱离 MCP 响应看工件无法反查口径。
        """

        run = tables_application.get_run(self.workspace, self.run_id)
        package = tables_service.get_package(self.workspace, self.package_id)
        payload = package.get("payload") if isinstance(package.get("payload"), dict) else package

        self.assertEqual(str(run.get("spec_hash") or ""), payload.get("spec_hash"))
        self.assertEqual(str(run.get("spec_id") or ""), payload.get("spec_id"))
        self.assertTrue(payload.get("spec_hash"), "package 未记录 spec_hash")
        self.assertEqual(payload.get("engine_delivery_count"), 13)
        self.assertEqual(payload.get("reference_source_sheet_count"), 15)
        self.assertEqual(payload.get("review_workbook_sheet_count"), 16)
        self.assertEqual(payload.get("table_contract_hash"), delivery_table_contract_hash())

    def test_xlsx_reuses_the_given_package(self) -> None:
        exported = tables_service.export_xlsx(
            self.workspace, self.run_id, "", self.package_id, "technical"
        )
        self.assertEqual(exported["finance_tables_package_id"], self.package_id)
        self.assertTrue(exported["source_package_reused"])

    def test_csv_reuses_the_given_package(self) -> None:
        exported = tables_service.export_csv(
            self.workspace, self.run_id, "", self.package_id, "technical"
        )
        self.assertEqual(exported["finance_tables_package_id"], self.package_id)
        self.assertTrue(exported["source_package_reused"])
        self.assertEqual(
            [item["table_code"] for item in exported["csv_manifest"]],
            list(DELIVERY_TABLE_KEYS),
        )
        self.assertTrue(all(
            item["table_contract_hash"] == delivery_table_contract_hash()
            for item in exported["csv_manifest"]
        ))
    def test_exports_record_their_provenance(self) -> None:
        xlsx = tables_service.export_xlsx(
            self.workspace, self.run_id, "", self.package_id, "technical"
        )
        self.assertEqual(xlsx["source_package_id"], self.package_id)
        self.assertEqual(xlsx["source_run_id"], self.run_id)
        self.assertTrue(xlsx["manifest_hash"])

        csv_export = tables_service.export_csv(
            self.workspace, self.run_id, "", self.package_id, "technical"
        )
        self.assertEqual(csv_export["source_package_id"], self.package_id)
        self.assertEqual(csv_export["source_run_id"], self.run_id)

    def test_xlsx_and_csv_bind_the_same_package_as_the_main_pack(self) -> None:
        xlsx = tables_service.export_xlsx(
            self.workspace, self.run_id, "", self.package_id, "technical"
        )
        csv_export = tables_service.export_csv(
            self.workspace, self.run_id, "", self.package_id, "technical"
        )
        self.assertEqual(
            {
                self.package_id,
                xlsx["finance_tables_package_id"],
                csv_export["finance_tables_package_id"],
            },
            {self.package_id},
        )

    def test_unknown_package_fails_closed(self) -> None:
        exported = tables_service.export_xlsx(
            self.workspace, self.run_id, "", "ftp_deadbeefdeadbeefdeadbeef"
        )
        self.assertEqual(exported["code"], "finance_tables_package_not_found")

    def test_package_bound_to_another_run_is_rejected(self) -> None:
        other = _scenario("tourism_catering", "scenic_area")
        second = run_model(
            {
                "workspace_id": self.workspace,
                "spec": other["spec"],
                "input_revision": other["finance"],
                "mode": "estimate_preview",
                "idempotency_key": "export-binding-run-2",
            }
        )
        exported = tables_service.export_xlsx(
            self.workspace, second["run_id"], "", self.package_id
        )
        self.assertEqual(exported["code"], "finance_tables_package_run_mismatch")

    def test_omitting_package_id_still_exports(self) -> None:
        # 兼容旧调用：不传 package_id 仍可导出，但会新渲染一个包。
        exported = tables_service.export_xlsx(
            self.workspace, self.run_id, validation_scope="technical"
        )
        self.assertTrue(exported["finance_tables_package_id"])
        self.assertFalse(exported["source_package_reused"])


if __name__ == "__main__":
    unittest.main()
