from __future__ import annotations

import os
import tempfile
import unittest

import lvke_mcp.servers.lvke_asset_acquisition.service as service


def _solar_spec() -> dict:
    """结构完整的光伏收购候选 spec（通过 validate_spec 的候选档校验）。"""

    return {
        "version": "finance_spec.v3",
        "asset_type": "solar_power",
        "transaction": {
            "calculation_granularity": "annual",
            "acquisition_type": "asset",
            "purchase_price": 5200,
            "model_start_date": "2026-01-01",
        },
        "solar_operation": {
            "installed_capacity_mw": 10,
            "tariff_yuan_per_kwh": 0.42,
            "utilization_hours": 1150,
            "remaining_operating_years": 18,
            "annual_opex_wan": 180,
            "curtailment_rate": 0.02,
            "maintenance_capex_wan": 40,
        },
    }


class AcquisitionChainEndToEndTest(unittest.TestCase):
    """收购域全链首个端到端测试。

    此前该域 12 个工具**全部零行为测试** —— 只有 baseline 契约快照，那只证明
    schema 没变。既有 `test_asset_acquisition_artifact_gates.py` 用手搭 state +
    patch 覆盖出件门禁，不穿过真实的 save → confirm → run → render → export 链。

    本测试钉住的是**分档语义**：候选 spec 能算、但算出来的东西不得取得正式资格。
    这条最容易在重构中被悄悄放松（把 partial 改成 ok、把 blocked 改成放行）。
    """

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-acq-chain-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "acquisition-chain"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _saved_spec_id(self) -> str:
        saved = service.save_spec(self.workspace, _solar_spec(), "acq-save")
        self.assertTrue(saved.get("success"), saved)
        return str(saved["spec_id"])

    def test_candidate_spec_passes_structural_validation(self) -> None:
        result = service.validate_spec(_solar_spec())
        self.assertTrue(result.get("success"), result)
        self.assertEqual(result.get("status"), "ok", result.get("warnings"))
        self.assertEqual(result.get("blockers"), [])

    def test_confirm_demands_more_than_structural_validity(self) -> None:
        """validate_spec=ok 不代表可确认：确认档要求正式交付完整性。

        两个档位用同一份 spec 得出不同结论是**刻意**的 —— 但错误必须可诊断，
        所以 details 要逐项列出缺什么，不能只说"校验未通过"。
        """

        confirmed = service.confirm_spec(
            self.workspace, self._saved_spec_id(), "端到端测试", "acq-confirm",
        )
        self.assertFalse(confirmed.get("success"), confirmed)
        self.assertEqual(confirmed.get("code"), "SPEC_VALIDATION_FAILED")
        details = confirmed.get("details") or []
        self.assertTrue(details, "确认被拒必须给出逐项缺口，否则调用方无法定位")
        joined = " ".join(str(item) for item in details)
        for expected in ("decision_thresholds", "project_parties", "asset_scope"):
            self.assertIn(expected, joined, details)

    def test_unconfirmed_spec_can_run_but_is_marked_candidate(self) -> None:
        """未确认候选**可以**跑模型（产品自述意图），但必须自报 candidate。

        这里同时守住两侧：不许因未确认就拒绝计算（那会让估算预览无法使用），
        也不许算完就装作正式（那会让预览件冒充交付物）。
        """

        run = service.run_model(
            self.workspace, self._saved_spec_id(), 0.08, "base", "acq-run",
        )
        self.assertTrue(run.get("success"), run)
        self.assertTrue(str(run.get("run_id") or "").startswith("acqrun_"), run)
        self.assertEqual(run.get("spec_confirmation_status"), "candidate")
        warnings = " ".join(str(item) for item in run.get("warnings") or [])
        self.assertIn("尚未确认", warnings, run.get("warnings"))

    def test_render_marks_preview_and_refuses_formal_use(self) -> None:
        run_id = service.run_model(
            self.workspace, self._saved_spec_id(), 0.08, "base", "acq-run-render",
        )["run_id"]
        rendered = service.render_tables(self.workspace, run_id, "acq-render")
        self.assertTrue(rendered.get("success"), rendered)
        # 渲染成功但降为 partial，且标记不可正式使用 —— 标记由 package 字段派生，
        # 不是 export 层参数控制（否则省略参数就能把预览件当正式件出）。
        self.assertEqual(rendered.get("status"), "partial")
        self.assertEqual(rendered.get("release_grade"), "technical_preview")
        self.assertFalse(rendered.get("formal_usable"))

    def test_formal_artifact_is_refused_for_candidate_run(self) -> None:
        """预览 run 出正式件必须被拒，且是**预期拒绝**而非系统故障。"""

        run_id = service.run_model(
            self.workspace, self._saved_spec_id(), 0.08, "base", "acq-run-artifact",
        )["run_id"]
        artifact = service.generate_artifact(self.workspace, run_id, "acq-artifact")
        self.assertFalse(artifact.get("success"), artifact)
        self.assertEqual(artifact.get("code"), "FORMAL_ARTIFACT_QUALIFICATION_REQUIRED")
        # 系统层面没出错：这是业务门禁，不该被当成 500。
        self.assertTrue(artifact.get("system_success", True), artifact)

    def test_csv_export_blocks_on_incomplete_package(self) -> None:
        """列级完整性/勾稽未过时 CSV 导出阻断，并给出可定位的错误码。"""

        run_id = service.run_model(
            self.workspace, self._saved_spec_id(), 0.08, "base", "acq-run-csv",
        )["run_id"]
        package_id = service.render_tables(
            self.workspace, run_id, "acq-render-csv",
        )["acquisition_tables_package_id"]
        exported = service.export_tables_csv(self.workspace, package_id, "acq-csv")
        self.assertFalse(exported.get("success"), exported)
        self.assertEqual(exported.get("code"), "TABLE_PACKAGE_INCOMPLETE")
        self.assertIn("TABLE_PACKAGE_INCOMPLETE", exported.get("blockers") or [])

    def test_run_is_idempotent_by_key(self) -> None:
        spec_id = self._saved_spec_id()
        first = service.run_model(self.workspace, spec_id, 0.08, "base", "acq-idem")
        second = service.run_model(self.workspace, spec_id, 0.08, "base", "acq-idem")
        self.assertEqual(first["run_id"], second["run_id"], "同幂等键必须复用同一 run")


if __name__ == "__main__":
    unittest.main()
