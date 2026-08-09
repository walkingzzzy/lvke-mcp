"""附表4 分年资金计划：回退结果不得进正式表包。

此前两处检查都失效：
- ``funding_year_plan`` 只匹配列标签，而"建设期第N年"列头由渲染器无条件生成，
  比例摊分回退同样带这些标签，于是回退数据拿到"已展示分年计划"的合格判定；
- ``funding_uses_sources_balance`` 能识别回退，但只落在
  ``independent_recalc_checks`` 里，只影响 grade，永不升级为 blocker。

结果是附表4 可以用回退数据一路通过正式门禁。修复后两项都进 gate_blockers，
但保持在正式层——technical scope 的过程验收仍应放行。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.adapters.spreadsheets._finance_export.delivery_tables import (
    _FUNDING_FALLBACK_SOURCES,
    _has_year_columns,
)
from lvke_mcp.domains.finance import tables_service
from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
from lvke_mcp.domains.finance.model_application import run_model


class FundingFallbackDetectionTest(unittest.TestCase):
    def test_both_fallback_markers_are_recognised(self) -> None:
        # 两套命名空间都必须认：annual.py 产 estimate_fallback，
        # builders.py 产 proportional_spread_fallback。
        self.assertIn("proportional_spread_fallback", _FUNDING_FALLBACK_SOURCES)
        self.assertIn("estimate_fallback", _FUNDING_FALLBACK_SOURCES)

    def test_year_columns_are_detected_by_label(self) -> None:
        self.assertTrue(
            _has_year_columns({"column_labels": ["序号", "项目", "建设期第1年", "合计"]})
        )
        self.assertTrue(_has_year_columns({"column_labels": ["分年投资"]}))

    def test_missing_year_columns_are_detected(self) -> None:
        self.assertFalse(_has_year_columns({"column_labels": ["序号", "金额"]}))
        self.assertFalse(_has_year_columns(None))


class FundingPlanFormalGateTest(unittest.TestCase):
    """列标签存在但来源是回退时，正式门禁必须拦下，技术门禁必须放行。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-funding-gate-")
        self.previous_data_dir = os.environ.get("LVKE_MCP_DATA_DIR")
        self.previous_golden_root = os.environ.get("LVKE_GOLDEN_DATA_ROOT")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        os.environ["LVKE_GOLDEN_DATA_ROOT"] = str(
            Path(__file__).resolve().parents[2] / "docs"
        )
        self.workspace = "funding-gate-test"

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

    def _validated(self) -> tuple[dict, dict]:
        scenario = next(
            item
            for item in build_industry_scenarios("agriculture_food")
            if item["archetype_id"] == "grain_processing" and item["variant_id"] == "base"
        )
        result = run_model(
            {
                "workspace_id": self.workspace,
                "spec": scenario["spec"],
                "input_revision": scenario["finance"],
                "mode": "estimate_preview",
                "idempotency_key": "funding-gate-probe",
            }
        )
        self.assertTrue(result["success"], result)
        run_id = result["run_id"]
        technical = tables_service.validate(
            self.workspace, run_id, validation_scope="technical"
        )
        formal = tables_service.validate(
            self.workspace, run_id, validation_scope="formal"
        )
        return technical, formal

    def test_technical_scope_still_passes(self) -> None:
        technical, _ = self._validated()
        self.assertTrue(technical["validation"]["valid"], technical)
        self.assertTrue(technical["success"], technical)

    def test_formal_scope_blocks_fallback_funding_plan(self) -> None:
        _, formal = self._validated()
        blockers = list(formal["validation"]["blockers"])
        funding = [item for item in blockers if item.startswith("funding_plan_not_formal:")]
        self.assertEqual(
            sorted(funding),
            [
                "funding_plan_not_formal:funding_uses_sources_balance",
                "funding_plan_not_formal:funding_year_plan",
            ],
            blockers,
        )
        self.assertFalse(formal["validation"]["validation_complete"])
        self.assertFalse(formal["success"])

    def test_funding_blockers_carry_actionable_guidance(self) -> None:
        _, formal = self._validated()
        actionable = formal["validation"].get("funding_plan_blockers_actionable") or []
        self.assertTrue(actionable)
        joined = " ".join(actionable)
        self.assertIn("funding_annual_schedule", joined)


if __name__ == "__main__":
    unittest.main()
