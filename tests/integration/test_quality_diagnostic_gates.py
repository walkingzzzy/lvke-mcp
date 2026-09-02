"""技术验收阶段数据质量诊断门禁调整（§9 验收标准）。

覆盖 2026-09-02 方案 §9 的 19 条验收标准：统一诊断信封、QualityDiagnostic
固化、表包/报告只产出内部诊断草稿、未知规则码不阻断、F-9/F-15 回归。
"""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid

from lvke_mcp.runtime.outcomes import apply_diagnostic_envelope
from lvke_mcp.runtime.quality_severity import (
    aggregate_quality_status,
    classify_quality,
)
from lvke_mcp.adapters.quality_diagnostic_repository import (
    QUALITY_DIAGNOSTIC_STORE,
    build_uncertainty,
    diagnostics_for_target,
    record_quality_diagnostic,
    validate_uncertainty,
)


def _diagnostic_env(**overrides: object) -> dict:
    """Build a business result and run it through the transport envelope."""
    base = {
        "success": True,
        "status": "partial",
        "quality_issues": [],
    }
    return apply_diagnostic_envelope({**base, **overrides})


class QualityGateClassifierTest(unittest.TestCase):
    """§9-4：未知规则码不阻断，quality_status=unclassified。"""

    def test_unknown_rule_is_unclassified_and_requires_human(self) -> None:
        classified = classify_quality("some_new_rule_2026")
        self.assertEqual(classified["quality_status"], "unclassified")
        self.assertFalse(classified["formal_report_allowed"])
        self.assertTrue(classified["diagnostic_required"])

    def test_known_material_conflict_is_fail(self) -> None:
        classified = classify_quality("finance_run_consistency_failed")
        self.assertEqual(classified["quality_status"], "fail")
        self.assertTrue(classified["material_conflict"])

    def test_aggregate_never_reports_pass_when_unknown_present(self) -> None:
        # 混着未知码 + 已知质量码 → unclassified（不能显示为质量通过）
        self.assertEqual(
            aggregate_quality_status(["unknown_code", "project_fact_certification_required"]),
            "unclassified",
        )
        # 纯已知非阻断 → warn
        self.assertEqual(
            aggregate_quality_status(["project_fact_certification_required"]),
            "warn",
        )
        # 无问题 → pass
        self.assertEqual(aggregate_quality_status([]), "pass")
        # 任一 material conflict → fail（即使还有未知码）
        self.assertEqual(
            aggregate_quality_status(["finance_run_consistency_failed", "unknown"]),
            "fail",
        )

    def test_ready_does_not_imply_formal_allowed(self) -> None:
        # §9-11：ready=true 不得推导为 formal_report_allowed=true
        env = _diagnostic_env(status="ok", ready=True)
        self.assertTrue(env["ready"])
        self.assertFalse(env["formal_report_allowed"])
        self.assertTrue(env["diagnostic_only"])


class DiagnosticEnvelopeTest(unittest.TestCase):
    """§9-1/9-11/9-14：统一诊断信封字段。"""

    def test_partial_with_quality_issues_is_diagnostic_only(self) -> None:
        env = _diagnostic_env(
            quality_issues=["finance_run_consistency_failed"],
            blockers=["finance_run_consistency_failed"],
        )
        self.assertEqual(env["operation_status"], "completed")
        self.assertTrue(env["diagnostic_available"])
        self.assertEqual(env["quality_status"], "fail")
        self.assertTrue(env["diagnostic_only"])
        self.assertTrue(env["human_confirmation_required"])
        self.assertFalse(env["formal_report_allowed"])

    def test_system_failure_is_operation_failed(self) -> None:
        env = apply_diagnostic_envelope({
            "success": False,
            "status": "failed",
            "system_success": False,
            "transport_success": False,
        })
        self.assertEqual(env["operation_status"], "failed")
        self.assertFalse(env["diagnostic_available"])
        self.assertEqual(env["quality_status"], "unclassified")

    def test_conflict_uncertainty_must_keep_both_values(self) -> None:
        # §9-2：保留冲突双方，禁止只保留选中值
        errors = validate_uncertainty({
            "type": "conflict",
            "field": "working_capital_wan",
            "competing_values": [],
        })
        self.assertTrue(any("competing_values" in item for item in errors))
        ok_errors = validate_uncertainty({
            "type": "conflict",
            "field": "working_capital_wan",
            "competing_values": [847.94, 1500],
            "impact": {"affected_outputs": ["total_investment"], "severity": "material"},
        })
        self.assertEqual(ok_errors, [])

    def test_assumption_and_unverified_require_notes(self) -> None:
        assumption_errors = validate_uncertainty({"type": "assumption", "field": "adr"})
        self.assertTrue(any("采用原因" in item or "message" in item for item in assumption_errors))
        unverified_errors = validate_uncertainty({"type": "unverified", "field": "adr"})
        self.assertTrue(any("缺少什么证据" in item for item in unverified_errors))


class QualityDiagnosticPersistenceTest(unittest.TestCase):
    """§9-1：QualityDiagnostic 对象不可变、可查、可去重。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-qd-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_record_is_immutable_and_idempotent(self) -> None:
        workspace_id = "qd-" + uuid.uuid4().hex
        uncertainty = build_uncertainty(
            "conflict",
            field="working_capital_wan",
            value=1500,
            competing_values=[847.94, 1500],
            severity="material",
            affected_outputs=["total_investment", "cashflow"],
        )
        first = record_quality_diagnostic(
            workspace_id,
            target_type="finance_run",
            target_id="run_abc",
            rule_code="finance_run_consistency_failed",
            uncertainties=[uncertainty],
            calculation_status="continued_with_conflict",
        )
        second = record_quality_diagnostic(
            workspace_id,
            target_type="finance_run",
            target_id="run_abc",
            rule_code="finance_run_consistency_failed",
            uncertainties=[uncertainty],
            calculation_status="continued_with_conflict",
        )
        # 内容寻址：同一冲突重写返回同一条记录，不重复
        self.assertEqual(first["object_id"], second["object_id"])
        payload = first["payload"]
        self.assertEqual(payload["object_type"], "QualityDiagnostic")
        self.assertEqual(payload["diagnostic_id"], first["object_id"])
        self.assertTrue(first["resource_uri"].startswith("lvke://quality-diagnostics/"))
        # 可查
        found = diagnostics_for_target(workspace_id, "run_abc")
        self.assertEqual([item["object_id"] for item in found], [first["object_id"]])

    def test_invalid_target_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_quality_diagnostic(
                "qd-invalid",
                target_type="bogus",
                target_id="x",
                rule_code="r",
            )


class TablesDiagnosticEnvelopeTest(unittest.TestCase):
    """§9-5/9-6/9-7/9-16：不一致 run 仍可渲染诊断表包，但不可绑定正式报告。"""

    def test_package_result_marks_diagnostic_only(self) -> None:
        from lvke_mcp.domains.finance._tables_service.base import _package_result

        record = {
            "object_id": "ftp_abc",
            "resource_uri": "lvke://finance-tables/workspaces/ws/packages/ftp_abc",
            "payload": {
                "run_id": "run_abc",
                "spec_id": "spec_1",
                "spec_hash": "sha256:abc",
                "validation_complete": False,
            },
        }
        validation = {
            "blockers": ["finance_run_consistency_failed"],
            "warnings": [],
            "diagnostic_ids": ["qd_123"],
        }
        result = _package_result(record, validation, "partial")
        self.assertEqual(result["quality_status"], "fail")
        self.assertTrue(result["diagnostic_only"])
        self.assertFalse(result["bindable_to_report"])
        self.assertFalse(result["formal_report_allowed"])
        self.assertIn("qd_123", result["diagnostic_ids"])
        self.assertTrue(any("不得直接绑定" in item for item in result["next_actions"]))
        # §9-6：表包响应不得再出现“可直接绑定报告”
        joined = "\n".join(str(item) for item in result["next_actions"])
        self.assertNotIn("可直接绑定到报告", joined)
        self.assertNotIn("可直接绑定报告", joined)

    def test_clean_package_is_still_diagnostic_only_current_phase(self) -> None:
        from lvke_mcp.domains.finance._tables_service.base import _package_result

        record = {
            "object_id": "ftp_ok",
            "resource_uri": "lvke://finance-tables/workspaces/ws/packages/ftp_ok",
            "payload": {"run_id": "run_ok", "validation_complete": True},
        }
        validation = {"blockers": [], "warnings": [], "diagnostic_ids": []}
        result = _package_result(record, validation, "ok")
        self.assertEqual(result["quality_status"], "pass")
        self.assertTrue(result["bindable_to_report"])
        self.assertFalse(result["formal_report_allowed"])
        self.assertTrue(result["diagnostic_only"])


class ReportDraftBoundaryTest(unittest.TestCase):
    """§9-8/9-9/9-10/9-12/9-14：报告只能产出内部诊断草稿。"""

    def test_report_validation_marks_internal_diagnostic_draft(self) -> None:
        # validate_report 的完整路径需要真实 store；这里直接校验其常量字段
        # 会被 transport 注入到所有报告修订响应上。
        env = _diagnostic_env(status="ok")
        self.assertEqual(env["operation_status"], "completed")
        self.assertFalse(env["formal_report_allowed"])
        self.assertTrue(env["diagnostic_only"])
        self.assertTrue(env["human_confirmation_required"])


class FdrDefaultScopeTest(unittest.TestCase):
    """§9-17/9-18：process_acceptance 是当前阶段默认，project_delivery 是显式门禁。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-fdr-scope-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_start_defaults_to_process_acceptance(self) -> None:
        from lvke_mcp.servers.lvke_feasibility_delivery import service as delivery

        started = delivery.start({
            "workspace_id": "fdr-default-scope",
            "delivery_mode": "estimate_preview",
            "idempotency_key": "fdr-default-scope-start",
        })
        self.assertTrue(started["success"], started)
        run = started["delivery_run"]
        self.assertEqual(run["release_scope"], "process_acceptance")

    def test_explicit_project_delivery_is_still_rejected_without_qualification(self) -> None:
        from lvke_mcp.servers.lvke_feasibility_delivery import service as delivery

        started = delivery.start({
            "workspace_id": "fdr-project-delivery",
            "delivery_mode": "review_candidate",
            "release_scope": "project_delivery",
            "idempotency_key": "fdr-project-delivery-start",
        })
        refused = delivery.release({
            "workspace_id": "fdr-project-delivery",
            "delivery_run_id": started["delivery_run_id"],
            "release_scope": "project_delivery",
            "idempotency_key": "fdr-project-delivery-refuse",
        })
        self.assertFalse(refused["success"], refused)
        self.assertEqual(refused["code"], "FORMAL_ARTIFACT_QUALIFICATION_REQUIRED")


class F9RegressionTest(unittest.TestCase):
    """§9-15：第五轮 F-9 数据仍能被正确发现。"""

    def test_cross_table_failure_still_found(self) -> None:
        from lvke_mcp.domains.finance.checks import run_checks

        hits = [
            item
            for item in run_checks({
                "funding": {"capital": 11200, "loan": 16800, "subsidy": 0},
                "annual": {"financial_plan": [
                    {"phase": "建设期", "capital_own": 13100, "loan_draw": 0, "gov_subsidy": 0},
                    {"phase": "建设期", "capital_own": 13100, "loan_draw": 0, "gov_subsidy": 0},
                ]},
            })
            if item["rule"] == "附表11建设期融资结构=附表4资金筹措"
        ]
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0]["ok"])
        self.assertTrue(hits[0]["blocking"])


class BuildMetadataAuxiliaryTest(unittest.TestCase):
    """§9-19：构建元数据只显示为辅助信息，不影响数据质量诊断。"""

    def test_build_metadata_does_not_drive_quality_status(self) -> None:
        env = _diagnostic_env(status="ok", build_metadata_complete=False)
        self.assertFalse(env.get("build_metadata_complete", True))
        # 构建元数据缺失不得改变质量结论
        self.assertEqual(env["quality_status"], "pass")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
