"""非法标识符必须是业务阻断，不是系统故障——且要在真实工具入口上成立。

``require_safe_id`` 抛 ``ValueError``，而工具入口的兜底 ``except Exception`` 会把它
包成 ``internal_error`` + ``system_success=False``。调用方于是看到"服务器坏了"，真实
情况却是"传的 ID 格式不对"。运行时探针实测 42 个入口跨 9 个域有此降级。

**为什么守卫不解析异常消息**：``require_safe_id`` 在存储层内部调用，字段名用的是存储
自己的形参名。实测 52 次触发里 41 次报的都是通用 ``object_id``，而调用方传的是
``analysis_task_id`` / ``proposal_id`` / ``url_audit_id``。据此生成错误码会指向调用方
根本没提交过的字段，等于换一种方式误导排查。所以守卫在派发前用同一条 ``_SAFE_ID``
规则自查入参，错误码指向调用方**实际提交**的参数名。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from importlib import import_module
from typing import Any

from lvke_mcp.runtime.input_guards import (
    find_rejected_identifier,
    identifier_rejection_payload,
)

# _SAFE_ID 会拒的真实形态：遍历、分隔符、空白、非法首字符、超长。
_REJECTED = ("../etc/passwd", "bad/id", "", "   ", "-leading", "a" * 200, "a b")


class FieldSelectionTest(unittest.TestCase):
    """守卫必须指名调用方实际提交的字段，且不误伤非标识符入参。"""

    def test_reports_the_callers_own_field_name(self) -> None:
        for bad in _REJECTED:
            with self.subTest(value=bad):
                self.assertEqual(
                    "analysis_task_id",
                    find_rejected_identifier(
                        {"workspace_id": "ws-1", "analysis_task_id": bad},
                        {"properties": {"analysis_task_id": {"type": "string"}}},
                    ),
                )

    def test_valid_identifiers_pass(self) -> None:
        self.assertIsNone(
            find_rejected_identifier(
                {"workspace_id": "ws-1", "analysis_task_id": "task_1.a-b"},
                {"properties": {"analysis_task_id": {"type": "string"}}},
            )
        )

    def test_workspace_id_is_reported_first(self) -> None:
        """工作区决定隔离边界，比业务对象 ID 更根本，应优先报它。"""

        self.assertEqual(
            "workspace_id",
            find_rejected_identifier(
                {"workspace_id": "../other", "analysis_task_id": "also/bad"},
                {"properties": {"analysis_task_id": {"type": "string"}}},
            ),
        )

    def test_free_text_fields_are_not_treated_as_identifiers(self) -> None:
        """idempotency_key 等允许任意文本，套用 _SAFE_ID 会造成误拒。"""

        arguments = {
            "workspace_id": "ws-1",
            "idempotency_key": "run 2026-08-13 #1",
            "trace_id": "mcp/abc def",
            "query": "投资 总额?",
            "message": "任意中文说明。",
        }
        schema = {"properties": {key: {"type": "string"} for key in arguments}}
        self.assertIsNone(find_rejected_identifier(arguments, schema))

    def test_id_list_fields_are_checked_elementwise(self) -> None:
        self.assertEqual(
            "evidence_pack_ids",
            find_rejected_identifier(
                {"workspace_id": "ws-1", "evidence_pack_ids": ["ok_1", "../bad"]},
                {"properties": {"evidence_pack_ids": {"type": "array"}}},
            ),
        )

    def test_non_string_id_fields_are_left_to_their_own_schema(self) -> None:
        """数值型 *_id（如 input_revision_id）不套字符串规则。"""

        self.assertIsNone(
            find_rejected_identifier(
                {"workspace_id": "ws-1", "input_revision_id": 3},
                {"properties": {"input_revision_id": {"type": "integer"}}},
            )
        )


class RejectionPayloadTest(unittest.TestCase):
    def test_payload_attributes_the_fault_to_the_caller(self) -> None:
        payload = identifier_rejection_payload("analysis_task_id", "lvke-data-analysis")
        # 归属：调用方改参数即可，不是服务端事故。
        self.assertTrue(payload["system_success"])
        self.assertTrue(payload["transport_success"])
        self.assertFalse(payload["success"])
        self.assertFalse(payload["business_success"])
        self.assertEqual("blocked", payload["status"])
        self.assertEqual(
            "lvke-data-analysis.invalid_analysis_task_id", payload["code"]
        )
        self.assertEqual(["invalid_analysis_task_id"], payload["blockers"])
        self.assertTrue(payload["next_actions"])


class TransportEntryTest(unittest.TestCase):
    """真实工具入口：守卫必须在派发路径上生效，而不只在直调时生效。"""

    _CASES = (
        ("lvke_mcp.servers.lvke_data_analysis.server", "analysis_status",
         {"analysis_task_id": "../etc/passwd"}, "analysis_task_id"),
        ("lvke_mcp.servers.lvke_deep_research.server", "dr_get_plan",
         {"task_id": "../etc/passwd"}, "task_id"),
        ("lvke_mcp.servers.lvke_finance_tables.server", "tables_get_package",
         {"finance_tables_package_id": "../etc/passwd"}, "finance_tables_package_id"),
        ("lvke_mcp.servers.lvke_project_planning.server", "planning_get_object",
         {"object_type": "ProjectContext", "object_id": "../etc/passwd"}, "object_id"),
    )

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-guard-entry-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self._servers: dict[str, Any] = {}

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _server(self, module: str) -> Any:
        if module not in self._servers:
            loaded = import_module(module)
            self._servers[module] = getattr(loaded, "SERVER", None) or loaded.build_server()
        return self._servers[module]

    def test_entry_returns_business_block_not_internal_error(self) -> None:
        for module, tool, args, field in self._CASES:
            with self.subTest(tool=tool):
                server = self._server(module)
                result = asyncio.run(
                    server._call_tool_async(  # noqa: SLF001
                        tool, {"workspace_id": "ws-guard", **args}, True
                    )
                )
                structured = getattr(result, "structured_content", None)
                self.assertIsNotNone(
                    structured,
                    f"{tool} 的标识符拒绝被改写成了传输层错误："
                    f"{json.loads(result.content[0].text).get('code')}",
                )
                self.assertEqual(f"invalid_{field}", structured["blockers"][0])
                self.assertTrue(structured["system_success"])
                self.assertFalse(structured["success"])
                self.assertEqual("blocked", structured["status"])

    def test_guard_does_not_intercept_wellformed_identifiers(self) -> None:
        """判别力：格式合法但对象不存在，必须走到业务层报 not_found。"""

        server = self._server("lvke_mcp.servers.lvke_data_analysis.server")
        result = asyncio.run(
            server._call_tool_async(  # noqa: SLF001
                "analysis_status",
                {"workspace_id": "ws-guard", "analysis_task_id": "atask_missing"},
                True,
            )
        )
        structured = getattr(result, "structured_content", None)
        self.assertIsNotNone(structured)
        self.assertEqual("analysis_task_not_found", structured.get("code"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
