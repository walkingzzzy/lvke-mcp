"""业务拒绝不得被自己的 outputSchema 改写成系统故障。

把只有成功路径才算得出的字段（已索引字符数、交付状态、工件清单）无条件放进
``required``，会让诚实的"对象不存在"撞上自己的 outputSchema；transport 于是返回
``invalid_tool_output`` + ``system_success=False``。调用方看到"服务器坏了"，而真实
情况是"这个 ID 不存在"，原始业务码也一并丢失 —— 这正是 2026-08-12 实机审计报出的
两个服务契约缺陷。

反方向同样要锁住：修复不得把字段从 ``required`` 里删掉了事，否则成功路径也不再
保证带上这些字段，"摄入成功≠检索得到"的可诊断性就被放弃了。因此这里既测错误路径
放行，也测成功路径缺字段必须被拒。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from importlib import import_module
from typing import Any

import jsonschema

_CASES = (
    (
        "lvke_mcp.servers.lvke_data_analysis.server",
        "analysis_status",
        {"analysis_task_id": "no-such-task"},
        "analysis_task_not_found",
    ),
    (
        "lvke_mcp.servers.lvke_data_analysis.server",
        "analysis_query",
        {"analysis_task_id": "no-such-task", "query": "x"},
        "analysis_task_not_found",
    ),
    (
        "lvke_mcp.servers.lvke_zero_material_delivery.server",
        "delivery_status",
        {"delivery_run_id": "no-such-run"},
        "delivery_run_not_found",
    ),
    (
        "lvke_mcp.servers.lvke_zero_material_delivery.server",
        "delivery_get_artifacts",
        {"delivery_run_id": "no-such-run"},
        "delivery_run_not_found",
    ),
)


class OutputSchemaErrorPathTest(unittest.TestCase):
    """走真实入口：业务拒绝必须保留自己的 code 与故障归属。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-outputschema-")
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

    def test_missing_object_keeps_its_business_code(self) -> None:
        for module, tool, args, expected_code in _CASES:
            with self.subTest(tool=tool):
                server = self._server(module)
                result = asyncio.run(
                    server._call_tool_async(  # noqa: SLF001
                        tool, {"workspace_id": "ws-outputschema", **args}, True
                    )
                )
                structured = getattr(result, "structured_content", None)
                # structured_content 为 None 就是被改写成了错误结果。
                self.assertIsNotNone(
                    structured,
                    f"{tool} 的业务拒绝被改写成了传输层错误："
                    f"{json.loads(result.content[0].text).get('code')}",
                )
                self.assertEqual(expected_code, structured.get("code"))
                self.assertEqual("blocked", structured.get("status"))
                # 输入指向一个不存在的对象，不是服务器故障。
                self.assertTrue(structured.get("system_success"))
                self.assertTrue(structured.get("transport_success"))
                self.assertFalse(structured.get("success"))

    def test_error_payload_validates_against_the_tools_own_schema(self) -> None:
        """错误载荷必须真的通过该工具自己的 outputSchema，而不是靠 transport 兜底。"""

        for module, tool, args, _code in _CASES:
            with self.subTest(tool=tool):
                server = self._server(module)
                spec = server._tools[tool]  # noqa: SLF001
                result = asyncio.run(
                    server._call_tool_async(  # noqa: SLF001
                        tool, {"workspace_id": "ws-outputschema", **args}, True
                    )
                )
                jsonschema.validate(
                    getattr(result, "structured_content"), spec.output_schema
                )


class SuccessPathContractStillEnforcedTest(unittest.TestCase):
    """成功路径的字段契约不得因为修复错误路径而被放弃。"""

    def _schema(self, module: str, tool: str) -> dict[str, Any]:
        loaded = import_module(module)
        server = getattr(loaded, "SERVER", None) or loaded.build_server()
        return server._tools[tool].output_schema  # noqa: SLF001

    def test_success_without_business_fields_is_rejected(self) -> None:
        envelope = {
            "resource_uris": [], "warnings": [], "blockers": [], "next_actions": [],
        }
        expectations = (
            ("lvke_mcp.servers.lvke_data_analysis.server", "analysis_status",
             {"indexed_char_count": 1, "indexed_cjk_char_count": 1,
              "indexed_document_count": 1}),
            ("lvke_mcp.servers.lvke_zero_material_delivery.server", "delivery_status",
             {"query_success": True, "domain_status": "ready", "delivery_state": "ready",
              "artifacts": [], "technical_preview_ready": False,
              # 分级验收三段状态同属成功路径硬契约。
              "acceptance": {"technical": {"status": "passed"},
                             "internal": {"status": "pending"},
                             "formal": {"status": "blocked"}}}),
        )
        for module, tool, business_fields in expectations:
            schema = self._schema(module, tool)
            validator = jsonschema.Draft202012Validator(schema)
            with self.subTest(tool=tool, case="缺字段必须被拒"):
                with self.assertRaises(jsonschema.ValidationError):
                    validator.validate({"success": True, "status": "ok", **envelope})
            with self.subTest(tool=tool, case="带齐字段必须通过"):
                validator.validate(
                    {"success": True, "status": "ok", **envelope, **business_fields}
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
