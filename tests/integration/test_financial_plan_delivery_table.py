from __future__ import annotations

import os
import tempfile
import unittest

import lvke_mcp.servers.lvke_finance_model.service as finance_service
import lvke_mcp.servers.lvke_finance_tables.service as tables_service
from lvke_mcp.domains.finance._run_service.base import (
    DELIVERY_TABLE_KEYS,
    ENGINE_DELIVERY_COUNT,
    REFERENCE_SOURCE_SHEET_COUNT,
    REVIEW_WORKBOOK_SHEET_COUNT,
)
from lvke_mcp.domains.finance.reference_schema import (
    validate_reference_contract,
    validate_reference_sources,
)


class FinancialPlanDeliveryTableTest(unittest.TestCase):
    """附表11 财务计划现金流量表进入交付集。

    2023 大纲 financial_sustainability 要求此表；附表9/10 只覆盖项目投资与资本金
    两个口径，给不出「各期期末现金、累计盈余、是否存在资金缺口」。引擎一直算了
    ``annual.financial_plan``（并被 checks.py 的「财务计划无资金缺口年」使用），
    但此前它是不占编号的内部控制表，从未进入 XLSX/CSV 交付件。

    这里同时钉住三件容易回退的事：
    1. 交付集数量与编号（附表11 是最大号，13 张表只排到附表10——6-1/6-2/6-3 是
       附表6 子表）；
    2. 参考来源 15 / 审查工作簿 16 两个数**不随之变化**——该表在甲方底稿里不存在；
    3. 列键必须取自运行时生产者 ``annual._build_financial_plan``，不是同语义的
       死代码 ``statements.financial_plan_rows``（键名不同，按后者写会让 5 列恒为
       None，已实测踩过）。
    """

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-fin-plan-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "financial-plan-delivery"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _rendered(self) -> dict:
        prepared = finance_service.prepare_spec({"workspace_id": self.workspace})
        run = finance_service.run_model({
            "workspace_id": self.workspace,
            "spec_id": prepared["spec_id"],
            "idempotency_key": "financial-plan-run",
        })
        self.assertTrue(run.get("success"), run)
        rendered = tables_service.render(self.workspace, run["run_id"])
        self.assertTrue(rendered.get("success"), rendered)
        table = tables_service.get_table(
            self.workspace, rendered["finance_tables_package_id"], "financial-plan",
        )
        self.assertTrue(table.get("success"), table)
        return table["content"]

    def test_delivery_set_contains_financial_plan_as_sheet_eleven(self) -> None:
        self.assertIn("financial-plan", DELIVERY_TABLE_KEYS)
        self.assertEqual(DELIVERY_TABLE_KEYS[-1], "financial-plan")
        self.assertEqual(ENGINE_DELIVERY_COUNT, 14)

    def test_reference_sheet_counts_do_not_grow(self) -> None:
        """附表11 在甲方底稿中不存在，故参考侧计数不得随交付集增加。"""

        self.assertEqual(REFERENCE_SOURCE_SHEET_COUNT, 15)
        self.assertEqual(REVIEW_WORKBOOK_SHEET_COUNT, 16)

    def test_frozen_reference_contract_still_validates(self) -> None:
        """无底稿表以显式声明通过冻结契约校验，而不是靠编造 artifact 哈希。"""

        contract = validate_reference_contract()
        self.assertTrue(contract.get("ok"), contract.get("issues"))
        sources = validate_reference_sources()
        self.assertTrue(sources.get("ok"), sources.get("issues"))

    def test_rendered_table_has_real_values_for_both_phases(self) -> None:
        content = self._rendered()
        self.assertEqual(content["delivery_no"], "附表11")
        self.assertEqual(content["title"], "财务计划现金流量表")
        rows = content.get("rows") or []
        self.assertGreater(len(rows), 1, content)
        phases = {row[1] for row in rows if len(row) > 1}
        self.assertIn("建设期", phases)
        self.assertIn("运营期", phases)
        # 列键写错时症状是「有行但整列 None」，所以逐列断言至少有一个非 None。
        for index, label in enumerate(content["column_labels"]):
            self.assertTrue(
                any(row[index] is not None for row in rows),
                f"列 {label} 全为 None，列键可能取自死代码 statements.financial_plan_rows",
            )

    def test_reference_grade_is_reachable(self) -> None:
        """达不到 reference 级会卡住 all_tables_reference_grade，进而断掉正式交付链。"""

        content = self._rendered()
        self.assertEqual(content.get("grade"), "reference", content.get("structure_gaps"))
        self.assertTrue(content.get("reference_structure"))
        self.assertEqual(content.get("structure_gaps"), [])

    def test_csv_export_includes_the_table(self) -> None:
        prepared = finance_service.prepare_spec({"workspace_id": self.workspace})
        run = finance_service.run_model({
            "workspace_id": self.workspace,
            "spec_id": prepared["spec_id"],
            "idempotency_key": "financial-plan-csv",
        })
        exported = tables_service.export_csv(
            self.workspace, run["run_id"], validation_scope="technical",
        )
        self.assertTrue(exported.get("success"), exported)
        names = " ".join(str(item) for item in exported.get("resource_uris") or [])
        self.assertIn("financial-plan", names)


if __name__ == "__main__":
    unittest.main()
