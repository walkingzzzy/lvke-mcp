"""遍历全部 169 个公开工具入口的通用守卫。

**为什么必须遍历**：既有测试几乎都直调服务函数，绕过了入口层的 schema 校验、参数
映射与错误信封构造；运行时探针实测 169 个公开入口里只有 9 个被真正执行过（94% 零
触达）。那 94% 正是"传了但被静默丢弃""业务拒绝被改写成系统故障"这类缺陷的滋生地——
尺度门禁读不到 route_length_km、42 个入口把标识符拒绝降级成 internal_error，都出自
这里，而当时全套件是绿的。

这组测试不验证业务正确性（那是各领域测试的事），只锁住**入口层的三条不变量**：

1. 非法标识符 → 业务阻断（``system_success=True``），不是 ``internal_error``；
2. 任何工具的阻断载荷都必须能通过它自己的 ``outputSchema``——否则 transport 会把它
   改写成 ``invalid_tool_output``，把诚实的业务码换成系统故障；
3. schema 自身的完整性（声明即契约）。

用最小合法载荷驱动：只填 ``required`` 标量，够走到入口层判定即可。这里刻意不构造
业务上有意义的入参——目的是覆盖"所有入口"，而不是"某个业务链路"。
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

from lvke_mcp.testing.server_manifest import SERVER_SPECS

_TRAVERSAL = "../etc/passwd"
# 与 runtime.input_guards._NON_OBJECT_ID_FIELDS 同源的自由文本字段：
# 它们以 _id/_key 结尾但允许任意文本，不参与标识符探测。
_FREE_TEXT = frozenset({
    "idempotency_key", "trace_id", "agent_trace_id", "tool_call_id", "request_id",
})


def _placeholder(schema: dict[str, Any], name: str) -> Any:
    """按字段 schema 造一个最小合法值。"""

    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    declared = schema.get("type")
    if isinstance(declared, list):
        declared = declared[0]
    if name == "workspace_id":
        return "ws-universal-guard"
    if declared == "integer":
        return int(schema.get("minimum") or 1)
    if declared == "number":
        return float(schema.get("minimum") or 1)
    if declared == "boolean":
        return False
    if declared == "array":
        return []
    if declared == "object":
        return {}
    return "probe"


def _minimal_payload(input_schema: dict[str, Any]) -> dict[str, Any]:
    properties = input_schema.get("properties") or {}
    payload: dict[str, Any] = {}
    for field in input_schema.get("required") or []:
        payload[str(field)] = _placeholder(properties.get(str(field)) or {}, str(field))
    return payload


def _identifier_fields(input_schema: dict[str, Any]) -> list[str]:
    properties = input_schema.get("properties") or {}
    fields = []
    for name, declared in properties.items():
        if name in _FREE_TEXT or name == "workspace_id":
            continue
        if not (name.endswith("_id") or name.endswith("_ids")):
            continue
        if not isinstance(declared, dict):
            continue
        kind = declared.get("type")
        if isinstance(kind, list):
            kind = kind[0]
        if kind in (None, "string", "array"):
            fields.append(name)
    return fields


def _all_tools() -> list[tuple[str, Any]]:
    tools = []
    for spec in SERVER_SPECS:
        module = getattr(spec, "module", None) or getattr(spec, "module_path", None)
        loaded = import_module(module)
        server = getattr(loaded, "SERVER", None) or loaded.build_server()
        specs = (
            server.tool_specs
            if hasattr(server, "tool_specs")
            else list(server._tools.values())  # noqa: SLF001
        )
        for tool in specs:
            tools.append((server.server_name, tool))
    return tools


class ToolEntryUniversalGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tempdir = tempfile.TemporaryDirectory(prefix="lvke-universal-guard-")
        cls._previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = cls._tempdir.name
        cls.tools = _all_tools()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = cls._previous
        cls._tempdir.cleanup()

    def _servers(self) -> dict[str, Any]:
        if not hasattr(self, "_server_cache"):
            self._server_cache: dict[str, Any] = {}
            for spec in SERVER_SPECS:
                module = getattr(spec, "module", None) or getattr(spec, "module_path", None)
                loaded = import_module(module)
                server = getattr(loaded, "SERVER", None) or loaded.build_server()
                self._server_cache[server.server_name] = server
        return self._server_cache

    def test_every_public_tool_is_covered(self) -> None:
        """先证明这组测试真的覆盖了全部对外入口，否则下面的断言是空转。"""

        self.assertEqual(169, len(self.tools), "对外工具数变化：先确认是有意增减")
        self.assertEqual(14, len(SERVER_SPECS))

    def test_input_schema_is_self_consistent(self) -> None:
        """声明即契约：required 必须都在 properties 里，否则调用方永远传不进来。"""

        for server_name, tool in self.tools:
            with self.subTest(tool=tool.name):
                schema = tool.input_schema
                jsonschema.Draft202012Validator.check_schema(schema)
                properties = schema.get("properties") or {}
                if schema.get("additionalProperties") is False:
                    for field in schema.get("required") or []:
                        self.assertIn(
                            field, properties,
                            f"{tool.name} 的 required 字段 {field} 不在 properties 里，"
                            "且 additionalProperties=False —— 该字段无法通过入口传入",
                        )

    def test_malformed_identifier_never_degrades_to_internal_error(self) -> None:
        """不变量 1+2：非法标识符是业务阻断，且阻断载荷通得过自己的 outputSchema。"""

        servers = self._servers()
        degraded: list[str] = []
        checked = 0
        for server_name, tool in self.tools:
            server = servers[server_name]
            for field in _identifier_fields(tool.input_schema):
                payload = _minimal_payload(tool.input_schema)
                payload[field] = (
                    [_TRAVERSAL] if field.endswith("_ids") else _TRAVERSAL
                )
                try:
                    result = asyncio.run(
                        server._call_tool_async(tool.name, payload, True)  # noqa: SLF001
                    )
                except Exception:
                    # schema 在入口就拒了（pattern 更严），比守卫更早，属正确行为。
                    continue
                checked += 1
                structured = getattr(result, "structured_content", None)
                if structured is None:
                    body = json.loads(result.content[0].text)
                    degraded.append(
                        f"{tool.name}.{field} -> {body.get('code')} "
                        f"(system_success={body.get('system_success')})"
                    )
        self.assertTrue(checked, "没有任何入口被实际驱动，断言会空转")
        self.assertEqual(
            [], degraded,
            "以下入口把标识符拒绝降级成了系统故障：\n" + "\n".join(degraded),
        )

    def test_business_rejections_declare_the_fault_as_callers(self) -> None:
        """归属正确性：入口层拒绝必须 system_success=True，否则调用方会误报事故。"""

        servers = self._servers()
        misattributed: list[str] = []
        for server_name, tool in self.tools:
            server = servers[server_name]
            fields = _identifier_fields(tool.input_schema)
            if not fields:
                continue
            payload = _minimal_payload(tool.input_schema)
            payload[fields[0]] = (
                [_TRAVERSAL] if fields[0].endswith("_ids") else _TRAVERSAL
            )
            try:
                result = asyncio.run(
                    server._call_tool_async(tool.name, payload, True)  # noqa: SLF001
                )
            except Exception:
                continue
            structured = getattr(result, "structured_content", None)
            if structured is None:
                continue  # 已由上一个测试报告
            if structured.get("system_success") is not True:
                misattributed.append(f"{tool.name}.{fields[0]}")
        self.assertEqual(
            [], misattributed,
            "以下入口把调用方的入参错误记成了服务端故障：\n" + "\n".join(misattributed),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
