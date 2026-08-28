from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from lvke_mcp.servers.lvke_feasibility_delivery import service as delivery
from lvke_mcp.servers.lvke_feasibility_delivery.contracts import STAGES


class FeasibilityReleaseScopeSemanticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-release-scope-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    @staticmethod
    def _structurally_complete_run(**overrides: object) -> dict:
        stages: dict[str, dict] = {}
        previous_output = ""
        for name in STAGES[:-1]:
            output = f"{name}-output"
            stages[name] = {
                "status": "completed",
                "input_refs": [previous_output] if previous_output else [],
                "output_refs": [output],
                "basis_hash": "sha256:" + name.encode().hex().ljust(64, "0")[:64],
                "warnings": [],
                "blockers": [],
            }
            previous_output = output
        return {
            "delivery_mode": "estimate_preview",
            "release_scope": "process_acceptance",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
            "stages": stages,
            **overrides,
        }

    def test_technical_validation_exposes_an_empty_stage_chain(self) -> None:
        """空阶段链必须被如实拆穿，但按质量项而非口径阻断项处理。

        产品口径：阶段链"还没走完"是置信度不足，允许产出把全部缺口写进
        release_limitations 的过程验收件；只有口径非法（规模不一致、重建
        来源缺记录、受控假设走正式发布）才进 blockers 并阻断。所以这里断
        的是"每一项缺口都出现在 quality_issues 且顶层如实暴露"，而不是
        要求 success=False。
        """

        started = delivery.start({
            "workspace_id": "empty-technical-chain",
            "delivery_mode": "estimate_preview",
            "release_scope": "process_acceptance",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
            "idempotency_key": "empty-chain-start",
        })
        result = delivery.validate({
            "workspace_id": "empty-technical-chain",
            "delivery_run_id": started["delivery_run_id"],
            "scope": "technical",
        })
        # 结构缺口不阻断，但绝不能被隐藏：quality_passed 必须为 False。
        self.assertFalse(result["validation"]["quality_passed"], result)
        self.assertEqual("partial", result["status"])
        for code in (
            "project_pending",
            "research_output_refs_missing",
            "review_basis_hash_missing",
        ):
            with self.subTest(code=code):
                self.assertIn(code, result["quality_issues"])
        # 结构缺口不得出现在 blockers（那是口径非法专用）。
        self.assertEqual([], result["blockers"], result["blockers"])

    def test_technical_scope_relaxes_only_formal_qualification(self) -> None:
        run = self._structurally_complete_run()
        workspace = "scope-relaxation-probe"
        technical_ok, technical_blockers, _ = delivery._validation(  # noqa: SLF001
            run, "technical", workspace,
        )
        formal_ok, formal_blockers, _ = delivery._validation(  # noqa: SLF001
            run, "formal", workspace,
        )
        # 两个 scope 都拆穿这条合成链的结构问题，technical 并不放宽它们。
        self.assertFalse(technical_ok)
        self.assertFalse(formal_ok)
        # technical 放宽的恰好只有正式证据资格这一类，且不多也不少。
        self.assertEqual(
            {"preview_cannot_formal_release", "controlled_assumption_formal_forbidden"},
            set(formal_blockers) - set(technical_blockers),
        )
        self.assertIn("preview_cannot_formal_release", formal_blockers)
        self.assertIn("controlled_assumption_formal_forbidden", formal_blockers)

    def test_process_acceptance_release_uses_technical_validation(self) -> None:
        started = delivery.start({
            "workspace_id": "process-release-routing",
            "delivery_mode": "estimate_preview",
            "release_scope": "process_acceptance",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
            "idempotency_key": "process-release-start",
        })
        with patch.object(
            delivery, "_validation", return_value=(True, [], ["formal_evidence_not_established"]),
        ) as mocked:
            released = delivery.release({
                "workspace_id": "process-release-routing",
                "delivery_run_id": started["delivery_run_id"],
                "release_scope": "process_acceptance",
                "idempotency_key": "process-release",
            })
        self.assertTrue(released["success"], released)
        self.assertEqual(mocked.call_args.args[1], "technical")
        self.assertEqual(released["validation_scope"], "technical")
        self.assertEqual(released["release_scope"], "process_acceptance")
        self.assertIn("formal_evidence_not_established", released["warnings"])

    def test_project_delivery_release_uses_formal_validation(self) -> None:
        started = delivery.start({
            "workspace_id": "project-release-routing",
            "delivery_mode": "formal_release",
            "release_scope": "project_delivery",
            "evidence_policy": "formal_evidence",
            "project_fact_certified": True,
            "idempotency_key": "project-release-start",
        })
        with patch.object(delivery, "_validation", return_value=(True, [], [])) as mocked:
            released = delivery.release({
                "workspace_id": "project-release-routing",
                "delivery_run_id": started["delivery_run_id"],
                "release_scope": "project_delivery",
                "idempotency_key": "project-release",
            })
        self.assertTrue(released["success"], released)
        self.assertEqual(mocked.call_args.args[1], "formal")
        self.assertEqual(released["validation_scope"], "formal")

    def test_project_delivery_returns_specific_fact_certification_rejection(self) -> None:
        started = delivery.start({
            "workspace_id": "project-fact-rejection",
            "delivery_mode": "formal_release",
            "release_scope": "project_delivery",
            "evidence_policy": "formal_evidence",
            "project_fact_certified": False,
            "idempotency_key": "project-fact-start",
        })
        with patch.object(
            delivery,
            "_validation",
            return_value=(False, ["project_fact_certification_required"], []),
        ):
            rejected = delivery.release({
                "workspace_id": "project-fact-rejection",
                "delivery_run_id": started["delivery_run_id"],
                "release_scope": "project_delivery",
                "idempotency_key": "project-fact-release",
            })
        self.assertFalse(rejected["success"])
        self.assertEqual(rejected["code"], "project_fact_certification_required")

    def test_malformed_identifiers_are_business_blocks_not_server_faults(self) -> None:
        """非法标识符是输入拒绝，不该降级成 internal_error。

        require_safe_id 抛 ValueError，而各入口都在 try 之外调用它，transport
        因此把一次输入校验拒绝报成通用服务器故障，丢掉具体业务码与对象标识。
        """

        cases = (
            ("validate", {"workspace_id": "!!bad", "delivery_run_id": "dr_x"},
             "invalid_workspace_id"),
            ("status", {"workspace_id": "ok-ws", "delivery_run_id": ""},
             "invalid_delivery_run_id"),
            ("release", {"workspace_id": "../etc", "delivery_run_id": "dr_x",
                         "idempotency_key": "k"}, "invalid_workspace_id"),
        )
        for entry, args, expected_code in cases:
            with self.subTest(entry=entry):
                result = getattr(delivery, entry)(args)
                self.assertFalse(result["success"])
                self.assertEqual("blocked", result["status"])
                self.assertEqual(expected_code, result["code"])
                self.assertIn(expected_code, result["blockers"])
                # 业务拒绝不得自称系统故障。
                self.assertIsNot(False, result.get("system_success"))

    def test_object_chain_cannot_be_skipped_by_omitting_the_workspace(self) -> None:
        """没有 workspace 就无法核对上游对象，必须阻断而不是静默跳过。"""

        stages = {
            name: {
                "status": "completed",
                "output_refs": ["lvke://fake/object"],
                "input_refs": ["lvke://fake/input"],
                "basis_hash": "sha256:" + "0" * 64,
                "blockers": [],
            }
            for name in STAGES
        }
        run = {
            "stages": stages,
            "evidence_policy": "formal_evidence",
            "release_scope": "process_acceptance",
        }

        ok, blockers, _ = delivery._validation(run, "technical", "")
        self.assertFalse(ok)
        self.assertIn("object_chain_not_verifiable_without_workspace", blockers)

        # 传入真实 workspace 时，伪造的 refs 必须被逐项拆穿。
        ok_with_ws, blockers_with_ws, _ = delivery._validation(
            run, "technical", "chain-skip-guard"
        )
        self.assertFalse(ok_with_ws)
        self.assertTrue(
            any("_ref_not_found" in item for item in blockers_with_ws),
            blockers_with_ws,
        )
        self.assertNotIn(
            "object_chain_not_verifiable_without_workspace", blockers_with_ws
        )


if __name__ == "__main__":
    unittest.main()
