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


class AcceptanceBlockersReachStateTest(unittest.TestCase):
    """技术验收的阻断项必须进入状态折叠，不能只躺在 acceptance 里。

    run["blockers"] 只含**验收之前**算出的码；组件缺失、manifest/hash 缺失、
    谱系断裂、审查未跑起来都只写进 acceptance。状态折叠若不读它，就会出现
    "工件可读 + acceptance.blocked" 仍报 delivery_state=ready。
    """

    def _run(self, technical_status: str, blockers: list[str]) -> dict:
        return {
            "stage": "preview_ready",
            "blockers": [],
            "artifact_uris": ["lvke://finance-tables/workspaces/w/packages/p/xlsx"],
            "domain_results": {"xlsx_status": "ok", "technical_preview_ready": True},
            "acceptance": {
                "technical": {"status": technical_status, "blockers": blockers},
                "internal": {"status": "blocked"},
                "formal": {"status": "blocked"},
            },
        }

    def test_blocked_acceptance_prevents_ready(self) -> None:
        from lvke_mcp.servers.lvke_zero_material_delivery._service.lifecycle import (
            _acceptance_blockers,
        )

        run = self._run("blocked", ["required_component_missing:report_docx"])
        states = _artifact_states(run)
        self.assertNotEqual(_delivery_state(run, states), "ready")
        collected = _acceptance_blockers(run)
        self.assertIn("required_component_missing:report_docx", collected)
        self.assertIn("technical_acceptance_blocked", collected)

    def test_failed_acceptance_prevents_ready(self) -> None:
        run = self._run("failed", ["finance_run_consistency_failed"])
        self.assertNotEqual(_delivery_state(run, _artifact_states(run)), "ready")

    def test_passed_acceptance_still_allows_ready(self) -> None:
        """反面：验收通过时不得被新判据误伤。"""

        run = self._run("passed", [])
        self.assertEqual(_delivery_state(run, _artifact_states(run)), "ready")

    def test_passed_with_limitations_still_allows_ready(self) -> None:
        run = self._run("passed_with_limitations", [])
        self.assertEqual(_delivery_state(run, _artifact_states(run)), "ready")


class PreviewFeasibilityValidationTest(unittest.TestCase):
    """预览阶段 feasibility_validation_id 恒空是已确认设计，不是漏项。

    零材料预览链不创建 fdr_*（由晋升后的 feasibility_start 创建）。空值必须
    既不伪造一个对象，也不产生一条无从消除的限制项——否则每条正常预览都带噪声。
    """

    def test_absent_feasibility_run_yields_no_codes(self) -> None:
        from lvke_mcp.servers.lvke_zero_material_delivery._service.technical_acceptance import (
            _feasibility_technical,
        )

        self.assertEqual(_feasibility_technical("any-workspace", ""), [])

    def test_schema_documents_why_the_field_is_empty(self) -> None:
        """契约必须写明空值含义，否则调用方会读成"校验被跳过"。"""

        specs = {item.name: item for item in build_server().tool_specs}
        schema = getattr(specs["delivery_status"], "output_schema", None) or getattr(
            specs["delivery_status"], "outputSchema", None
        )
        field = (
            schema["properties"]["acceptance"]["properties"]["technical"]
            ["properties"]["feasibility_validation_id"]
        )
        description = str(field.get("description") or "")
        self.assertIn("预览阶段", description)
        self.assertIn("不表示校验被跳过", description)


class StartResponseHonestyTest(unittest.TestCase):
    """delivery_start 的信封必须反映技术验收结论，不能只看验收前的码。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-start-honesty-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "start-honesty"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_failed_review_start_is_not_wrapped_as_a_successful_preview(self) -> None:
        """审查启动失败时不得报 success/completed/preview_ready=True。

        此前 blocking_codes 只含验收**前**算出的码，于是同一个响应里顶层报成功、
        acceptance.technical.status=failed —— 而调用方最可能读的是顶层。
        """

        from unittest.mock import patch

        created = service.create_from_sentence(
            {
                "workspace_id": self.workspace,
                "sentence": "在湖北新建一座儿童游乐园",
                "region": "湖北省",
                "idempotency_key": "sh-1",
            }
        )
        with patch(
            "lvke_mcp.runtime.service_gateway.review_start",
            return_value={
                "success": False,
                "code": "review_engine_down",
                "review_id": "",
                "quality_issues": [],
            },
        ):
            started = service.start(
                {
                    "workspace_id": self.workspace,
                    "delivery_run_id": created["delivery_run"]["delivery_run_id"],
                    "idempotency_key": "sh-2",
                }
            )
        technical = (started.get("acceptance") or {}).get("technical") or {}
        self.assertEqual(technical.get("status"), "failed", started)
        # 顶层必须与 acceptance 一致。
        self.assertFalse(started.get("success"), started)
        self.assertFalse(started.get("completed"), started)
        self.assertFalse(started.get("technical_preview_ready"), started)
        self.assertEqual(started.get("status"), "blocked", started)
        # 验收阻断码必须出现在顶层 blockers，而不是只躺在 acceptance 里。
        for code in technical.get("blockers") or []:
            self.assertIn(code, started.get("blockers") or [], code)


class ImmutableRunViewTest(unittest.TestCase):
    """返回的 delivery_run 必须能通过它自带的 content_hash 复算。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-immutable-view-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "immutable-view"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_refreshed_acceptance_does_not_invalidate_the_run_hash(self) -> None:
        from unittest.mock import patch

        from lvke_mcp.runtime.storage import sha256_json
        from lvke_mcp.servers.lvke_zero_material_delivery._service.acceptance import (
            REQUIRED_DIMENSIONS,
        )
        from lvke_mcp.servers.lvke_zero_material_delivery._service.base import RUN_STORE

        payload = {
            "object_type": "DeliveryRun",
            "stage": "preview_ready",
            "intent_id": "i",
            "assumption_package_id": "a",
            "blockers": [],
            "artifact_uris": [],
            "domain_results": {},
            "report_profile": {},
            "missing_inputs": [],
            "skipped_fields": [],
            "release_limitations": [],
            "acceptance": {
                # 有 review_id，刷新时必然去读领域确认。
                "technical": {"status": "passed", "review_id": "review_x", "limitations": []},
                "internal": {"status": "pending", "missing_dimensions": ["compliance"]},
                "formal": {"status": "blocked"},
            },
        }
        record = RUN_STORE.put(
            self.workspace, payload, producer="hash-test", status="ok", basis=payload
        )

        def confirmed(args: dict) -> dict:
            dimension = args["dimension"]
            return {
                "success": True,
                "dimension_result": {
                    "dimension": dimension,
                    "status": "passed",
                    "confirmation_id": f"rvdim_{dimension}",
                    "role_declaration": f"{dimension} 负责人",
                    "review_statement": "已复核",
                    "limitations_accepted": [],
                    "incomplete_reasons": [],
                    "confirmed_at": "2026-08-30T00:00:00Z",
                },
            }

        with patch(
            "lvke_mcp.runtime.service_gateway.review_get_dimension",
            side_effect=confirmed,
        ):
            result = service.status(
                {
                    "workspace_id": self.workspace,
                    "delivery_run_id": record["object_id"],
                }
            )
        self.assertEqual(len(REQUIRED_DIMENSIONS), 7)
        # 顶层是实时状态。
        self.assertEqual(result["acceptance"]["internal"]["status"], "passed")
        run = result["delivery_run"]
        # 嵌套是落库快照，必须与自带 hash 相符。
        body = {
            key: value
            for key, value in run.items()
            if key
            not in (
                "delivery_run_id",
                "workspace_id",
                "basis_hash",
                "content_hash",
                "created_at",
                "resource_uri",
            )
        }
        self.assertEqual(sha256_json(body), run["content_hash"])
        # 契约必须指明该读哪一个，否则两个不同的值就是矛盾。
        self.assertEqual(
            result.get("acceptance_source"), "top_level_acceptance_is_current"
        )


class SkipFieldValidationTest(unittest.TestCase):
    """跳过项必须是真实字段：审计记录不能包含无从核对的条目。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-skip-validate-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "skip-validate"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _package_id(self) -> str:
        created = service.create_from_sentence(
            {
                "workspace_id": self.workspace,
                "sentence": "在湖北新建一座儿童游乐园",
                "region": "湖北省",
                "idempotency_key": "sk-1",
            }
        )
        started = service.start(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": created["delivery_run"]["delivery_run_id"],
                "idempotency_key": "sk-2",
            }
        )
        return started["assumption_package"]["assumption_package_id"]

    def test_unknown_skip_field_is_refused(self) -> None:
        result = service.confirm_assumptions(
            {
                "workspace_id": self.workspace,
                "assumption_package_id": self._package_id(),
                "confirmations": [],
                "skip_fields": [{"field": "totally_unknown", "reason": "瞎写的"}],
                "idempotency_key": "sk-bad",
            }
        )
        self.assertFalse(result.get("success"), result)
        self.assertEqual(result.get("code"), "unknown_skip_field")
        self.assertEqual(result.get("unknown_fields"), ["totally_unknown"])

    def test_real_field_can_still_be_skipped(self) -> None:
        result = service.confirm_assumptions(
            {
                "workspace_id": self.workspace,
                "assumption_package_id": self._package_id(),
                "confirmations": [],
                "skip_fields": [{"field": "loan_rate", "reason": "用户跳过"}],
                "idempotency_key": "sk-ok",
            }
        )
        self.assertTrue(result.get("success"), result)
        self.assertEqual(
            [
                item["field"]
                for item in (result.get("delivery_run") or {}).get("skipped_fields") or []
            ],
            ["loan_rate"],
        )


class ReviewStartFailOpenTest(unittest.TestCase):
    """审查没跑起来必须 fail-closed —— 缺席的结论不是通过的结论。"""

    def test_review_startup_failures_are_all_blocking(self) -> None:
        from lvke_mcp.runtime.quality_severity import is_blocking

        for code in (
            "review_process_acceptance_unavailable:OSError",
            "review_process_acceptance_start_failed:review_not_found",
            "review_process_acceptance_prepare_failed:target_invalid",
            "review_process_acceptance_review_id_missing",
            "review_process_acceptance_target_missing",
            "review_technical_verdict_missing",
            "review_technical_verdict_not_pass:incomplete",
            "review_suite_draft_failed:x",
            "review_suite_confirm_failed:y",
        ):
            with self.subTest(code=code):
                self.assertTrue(is_blocking(code), code)

    def test_verdict_judgement_separates_structural_from_real_causes(self) -> None:
        """verdict 非 pass 要区分根因：零材料结构性事实放行，真原因阻断。

        零材料 external 过程验收下 technical_verdict 恒为 incomplete（缺
        base_data、external 禁发布、内部验收尚未 finalize）。在 verdict 层面
        整体判死会让每一条正常链都进 failed；只看"是否 pass"又会放过真问题。
        """

        from lvke_mcp.runtime.quality_severity import split_quality_codes
        from lvke_mcp.servers.lvke_zero_material_delivery._service import (
            technical_acceptance as ta,
        )

        def judge(started: dict) -> list[str]:
            codes: list[str] = []
            verdict = str(started.get("technical_verdict") or "")
            if not started.get("success"):
                codes.append("review_process_acceptance_start_failed:x")
            if not verdict:
                codes.append("review_technical_verdict_missing")
            elif verdict != "pass":
                reasons = {
                    str(item) for item in started.get("quality_issues") or [] if str(item)
                }
                residual = sorted(
                    reasons - ta._STRUCTURAL_REVIEW_INCOMPLETE_REASONS  # noqa: SLF001
                )
                if reasons and not residual:
                    codes.append(
                        f"review_technical_verdict_structurally_incomplete:{verdict}"
                    )
                else:
                    codes.append(f"review_technical_verdict_not_pass:{verdict}")
                    codes.extend(f"review_incomplete_reason:{i}" for i in residual)
            return split_quality_codes(codes)[0]

        structural = {
            "success": True,
            "technical_verdict": "incomplete",
            "quality_issues": [
                "review_package_role_missing:base_data",
                "external_review_release_forbidden",
                "review_suite_not_finalized",
            ],
        }
        self.assertEqual(judge(structural), [], "零材料常态不得被判失败")

        for name, started in {
            "结构性混真原因": {
                "success": True,
                "technical_verdict": "incomplete",
                "quality_issues": [
                    "review_package_role_missing:base_data",
                    "standards_snapshot_unavailable",
                ],
            },
            "verdict_fail": {
                "success": True,
                "technical_verdict": "fail",
                "quality_issues": ["blocking_finding:f1"],
            },
            # 边界：非 pass 但一条原因都没给，仍必须阻断（不能因"没原因"而放行）。
            "非pass且无原因": {
                "success": True,
                "technical_verdict": "incomplete",
                "quality_issues": [],
            },
            "verdict缺失": {"success": True, "technical_verdict": ""},
            "启动失败": {"success": False, "technical_verdict": "pass"},
        }.items():
            with self.subTest(case=name):
                self.assertTrue(judge(started), f"{name} 必须阻断")

    def test_post_run_quality_hints_stay_non_blocking(self) -> None:
        """审查真的跑完后报出的质量提示仍按质量项处理，不得误升为阻断。"""

        from lvke_mcp.runtime.quality_severity import is_blocking

        for code in (
            "review_quality_issue:external_review_release_forbidden",
            "review_quality_issue:review_suite_not_finalized",
            # 零材料必然缺 base_data，属结构性披露。
            "review_suite_role_missing:base_data",
        ):
            with self.subTest(code=code):
                self.assertFalse(is_blocking(code), code)


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


class AcquisitionRouteSeverityTest(unittest.TestCase):
    """收购路线必须与通用财务路线走同一个严重性判定入口。

    此前 ``_execute_solar_acquisition_preview`` 把 ``blockers`` 硬编码成 ``[]``、
    ``business_success``/``completed`` 恒为 ``True``,并且只据 quality_issues 判
    ``partial`` vs ``ok``——永远到不了 ``blocked``。于是 validate_spec 报出的口径
    非法问题被一并降级,同一个问题在通用路线报 blocked、在收购路线报"可交付"。
    """

    def test_source_declares_no_hardcoded_severity(self) -> None:
        import inspect

        from lvke_mcp.servers.lvke_zero_material_delivery._service import orchestration

        source = inspect.getsource(orchestration._execute_solar_acquisition_preview)
        self.assertIn("split_quality_codes", source)
        self.assertNotIn('"blockers": []', source)
        self.assertNotIn('"business_success": True', source)
        self.assertNotIn('"completed": True', source)

    def test_illegal_evidence_codes_are_classified_as_blocking(self) -> None:
        """收购 spec 校验会报出的口径非法码,必须判为阻断而不是质量提示。"""
        from lvke_mcp.runtime.quality_severity import split_quality_codes

        illegal = [
            "project_scale_inconsistent:capacity_mw",
            "controlled_assumption_formal_forbidden",
            "source_reconstructed_cannot_certify_project_fact",
            "reconstruction_records_missing",
            "finance_run_failed",
        ]
        blocking, quality = split_quality_codes(illegal)
        self.assertEqual(sorted(illegal), sorted(blocking))
        # 阻断项同时留在 quality_issues 里,便于随件披露,但不得只留在那里。
        self.assertEqual(sorted(illegal), sorted(quality))

    def test_confidence_codes_stay_non_blocking(self) -> None:
        """证据待补是置信度不足,允许产出带限制说明的过程验收件。"""
        from lvke_mcp.runtime.quality_severity import split_quality_codes

        blocking, quality = split_quality_codes(
            ["research_evidence_pending", "project_fact_evidence_pending"]
        )
        self.assertEqual([], blocking)
        self.assertEqual(
            ["project_fact_evidence_pending", "research_evidence_pending"], quality
        )

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
        # 这些字段是**成功路径**的硬契约，用 if success then required 表达：
        # 无条件必填会让"运行不存在"这类诚实拒绝撞上自己的 schema，被 transport
        # 改写成 invalid_tool_output + system_success=False（见
        # tests/integration/test_output_schema_error_paths.py）。断言方式因此从
        # "在 required 里"改为"成功载荷缺任一字段必须被拒"——守的是同一件事，
        # 但不会连业务拒绝一起判非法。
        validator = jsonschema.Draft202012Validator(schema)
        success_payload = {
            "success": True,
            "status": "ok",
            "resource_uris": [],
            "warnings": [],
            "blockers": [],
            "next_actions": [],
            "query_success": True,
            "domain_status": "ready",
            "delivery_state": "ready",
            "artifacts": [],
            "technical_preview_ready": False,
            # 分级验收三段状态同属成功路径硬契约：读得到运行就必然算得出。
            "acceptance": {
                "technical": {"status": "passed"},
                "internal": {"status": "pending"},
                "formal": {"status": "blocked"},
            },
        }
        validator.validate(success_payload)
        for field in (
            "query_success",
            "domain_status",
            "delivery_state",
            "artifacts",
            "acceptance",
        ):
            with self.subTest(field=field):
                incomplete = {
                    key: value
                    for key, value in success_payload.items()
                    if key != field
                }
                with self.assertRaises(jsonschema.ValidationError):
                    validator.validate(incomplete)


if __name__ == "__main__":
    unittest.main()
