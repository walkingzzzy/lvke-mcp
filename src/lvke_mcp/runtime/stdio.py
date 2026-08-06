"""轻量级 MCP stdio 调度器(fallback)。

当本机未安装官方 ``mcp`` SDK 时,本模块提供一个最小化、协议兼容的
JSON-RPC over stdio 调度器:

- 支持 ``initialize`` / ``tools/list`` / ``tools/call`` / ``resources/list`` 核心 RPC
- ``tools/call`` 把结果包成标准 ``CallToolResult``(``content`` + 可选
  ``isError``),让 hermes 端复用现有 ``call_tool`` 解析逻辑
- 通过 ``register_tool(name, handler, schema, description)`` 注册工具,
  调用约定: ``handler(arguments: dict) -> dict``;返回的 dict 会被
  ``json.dumps`` 后塞进 ``content[0].text``

设计目标:让单元测试与本地调试不依赖外网 / pip 安装,同时保持与官方
SDK 的语义一致。生产环境一旦装上 ``mcp`` 包,各 server 的 ``server.py``
可优先使用官方 SDK,本模块作为兜底。
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from lvke_mcp.runtime.coordination import build_coordination, coordination_schema

ToolHandler = Callable[[dict], dict]


def _validate_tool_input(schema: dict, instance: dict) -> str | None:
    """校验入参是否符合 input_schema，返回错误消息（若有）。

    与 transport.py 的 _schema_validation_message 功能对齐，但不依赖官方 mcp SDK。
    """
    try:
        from jsonschema import Draft202012Validator
        Draft202012Validator(schema).validate(instance)
        return None
    except Exception as exc:
        # ValidationError 的 absolute_path / message / schema 可用
        if hasattr(exc, "absolute_path") and hasattr(exc, "message"):
            path = ".".join(str(p) for p in exc.absolute_path) or "<root>"
            constraint = " ".join(str(exc.message).split())[:500]
            return f"Schema validation failed at '{path}': {constraint}. 补充该字段并按工具 schema 提交."
        return f"入参校验失败: {str(exc)[:200]}"


@dataclass
class ToolSpec:
    """单个工具的元数据 + 处理函数。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    handler: ToolHandler


@dataclass
class StdioServer:
    """最小化的 MCP stdio 调度器。"""

    server_name: str
    server_version: str = "0.1.0"
    logger: logging.Logger | None = None
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        """注册一个工具。

        Args:
            name: 工具名(在 ``tools/list`` 中以此暴露)。
            description: 给 LLM 看的工具描述(中文)。
            input_schema: JSON Schema 对象(``type: object`` + ``properties``)。
            handler: 同步函数,签名 ``(arguments: dict) -> dict``。
        """

        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            handler=handler,
        )

    # ── JSON-RPC handlers ───────────────────────────────────────────────

    def _handle_initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": self.server_name,
                "version": self.server_version,
            },
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False},
            },
        }

    def _handle_resources_list(self, params: dict) -> dict:
        return {"resources": []}

    def _handle_tools_list(self, params: dict) -> dict:
        def public_output_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
            if schema is None:
                return None
            public = dict(schema)
            properties = dict(public.get("properties") or {})
            properties["coordination"] = coordination_schema()
            public["properties"] = properties
            if public.get("additionalProperties") is False:
                public["additionalProperties"] = True
            return public

        return {
            "tools": [
                ({
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                } | ({"outputSchema": public_output_schema(t.output_schema)} if t.output_schema else {}))
                for t in self._tools.values()
            ]
        }

    def _handle_tools_call(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}

        def error_payload(
            code: str,
            message: str,
            *,
            trace_id: str | None = None,
            caller_fault: bool = False,
        ) -> dict[str, Any]:
            """构造错误响应。

            ``caller_fault=True`` 用于入参非法这类**调用方错误**：传输与服务端
            都是健康的，只有业务未完成。把它标成 system/transport 失败会把客户
            端参数错误伪装成服务端故障，与本仓库 handler 层
            ``invalid_argument`` 的既有约定（system_success/transport_success
            均为 true）不一致，也会误导运维定位方向。
            """
            payload: dict[str, Any] = {
                "success": False,
                "business_success": False,
                "system_success": bool(caller_fault),
                "transport_success": bool(caller_fault),
                "status": "blocked" if caller_fault else "failed",
                "code": code,
                "message": message,
                "retryable": False,
                "trace_id": trace_id or f"mcp_{uuid.uuid4().hex}",
                "resource_uris": [],
                "warnings": [],
                "blockers": [code],
                "next_actions": [],
            }
            payload["coordination"] = build_coordination(payload, server_name=self.server_name)
            return payload

        if name not in self._tools:
            payload = error_payload(f"{self.server_name}.unknown_tool", "未注册的工具")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False),
                    }
                ],
                "isError": True,
            }
        # NEW-P1-D 修复：在执行 handler 之前校验入参。
        # 本模块此前只校验 output_schema（见下方），却对外声明了 input_schema
        # （tools/list 的 inputSchema）。类型错误的入参会直接进入 handler 并
        # 崩成 internal_error/system_success=false，把「调用方参数错误」伪装成
        # 「服务端故障」，与官方 transport 的 -32602 行为不一致。
        spec = self._tools[name]
        if spec.input_schema:
            invalid = _validate_tool_input(spec.input_schema, arguments)
            if invalid is not None:
                payload = error_payload(
                    f"{self.server_name}.invalid_argument",
                    invalid,
                    caller_fault=True,
                )
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(payload, ensure_ascii=False),
                        }
                    ],
                    "isError": True,
                }
        try:
            result = self._tools[name].handler(arguments)
        except Exception as exc:  # noqa: BLE001 - 包成 CallToolResult
            trace_id = f"mcp_{uuid.uuid4().hex}"
            if self.logger is not None:
                self.logger.exception("tool %s 抛出未捕获异常 trace_id=%s", name, trace_id)
            payload = error_payload(f"{self.server_name}.internal_error", "工具执行失败", trace_id=trace_id)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False),
                    }
                ],
                "isError": True,
            }

        spec = self._tools[name]
        if spec.output_schema:
            try:
                from jsonschema import validate

                validate(instance=result, schema=spec.output_schema)
            except Exception as exc:  # noqa: BLE001
                trace_id = f"mcp_{uuid.uuid4().hex}"
                if self.logger is not None:
                    self.logger.exception(
                        "tool %s 输出校验失败 trace_id=%s", name, trace_id
                    )
                payload = error_payload(f"{self.server_name}.invalid_tool_output", "工具输出未通过 outputSchema 校验", trace_id=trace_id)
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False),
                    }],
                    "isError": True,
                }
        # A business gate is a valid MCP response, not a transport failure.
        # Keep ``isError`` for execution/protocol failures while the envelope
        # carries ``success=false,status=blocked`` for agent decision-making.
        if not isinstance(result, dict):
            result = {
                "success": False,
                "status": "failed",
                "code": f"{self.server_name}.invalid_tool_output",
                "message": "工具输出格式无效",
            }
        raw_status = str(result.get("status") or ("failed" if result.get("success") is False else "ok")).lower()
        if raw_status in {"running", "queued", "pending", "started", "processing"}:
            result = dict(result)
            result.setdefault("task_status", raw_status)
            result["status"] = "accepted"
        elif raw_status in {"applied", "released", "completed", "done", "cancelled"}:
            result = dict(result)
            result.setdefault("domain_status", raw_status)
            result["status"] = "ok"
        else:
            result = dict(result)
            result.setdefault("status", raw_status)
            if raw_status not in {
                "ok", "accepted", "partial", "empty", "missing_inputs", "blocked",
                "incomplete", "failed", "upstream_failure",
            }:
                result.setdefault("domain_status", raw_status)
                result["status"] = "blocked" if result.get("success") is False else "ok"
        status = str(result.get("status") or "ok")
        business_success = status in {"ok", "accepted"}
        result["success"] = business_success
        result["business_success"] = business_success
        result["system_success"] = status != "failed"
        result["transport_success"] = result["system_success"]
        result["completed"] = status == "ok"
        result["outcome"] = status
        result.setdefault("resource_uris", [])
        result.setdefault("warnings", [])
        result.setdefault("blockers", [])
        result.setdefault("next_actions", [])
        result.setdefault("coordination", build_coordination(result, server_name=self.server_name))
        is_err = bool(
            result.get("success") is False
            and str(result.get("status") or "failed") == "failed"
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False),
                }
            ],
            "structuredContent": result,
            "isError": is_err,
        }

    # ── 入口 ────────────────────────────────────────────────────────────

    def dispatch(self, request: dict) -> dict | None:
        """处理一个 JSON-RPC 请求,返回响应字典(notification 时为 ``None``)。"""

        method = request.get("method")
        params = request.get("params") or {}
        req_id = request.get("id")
        if method == "initialize":
            result: Any = self._handle_initialize(params)
        elif method == "tools/list":
            result = self._handle_tools_list(params)
        elif method == "resources/list":
            result = self._handle_resources_list(params)
        elif method == "tools/call":
            result = self._handle_tools_call(params)
        elif method == "notifications/initialized" or req_id is None:
            return None  # notification — 无响应
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"method not found: {method}",
                },
            }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def serve_forever(self, *, input_stream=None, output_stream=None) -> None:
        """在 stdio 上按行读取 JSON-RPC,按行写回响应。

        Args:
            input_stream: 默认 ``sys.stdin``。可注入用于测试。
            output_stream: 默认 ``sys.stdout``。可注入用于测试。
        """

        in_ = input_stream or sys.stdin
        out = output_stream or sys.stdout
        for line in iter(in_.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                if self.logger is not None:
                    self.logger.warning("非法 JSON 输入: %s", line[:200])
                continue
            resp = self.dispatch(req)
            if resp is None:
                continue
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()


def feed_requests(server: StdioServer, requests: Iterable[dict]) -> list[dict]:
    """单测辅助:把一串请求喂给 server,收集响应列表。"""

    out: list[dict] = []
    for req in requests:
        resp = server.dispatch(req)
        if resp is not None:
            out.append(resp)
    return out
