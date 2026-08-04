"""Strict stdio MCP runtime built on the official MCP Python SDK."""

from __future__ import annotations

import base64
import hashlib
import json
import inspect
import os
import secrets
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from io import TextIOWrapper
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Literal

import anyio
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from mcp import types
from mcp.server.caching import CacheHint
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import MCPError
from mcp.shared.message import SessionMessage
from mcp.types.version import (
    HANDSHAKE_PROTOCOL_VERSIONS,
    MODERN_PROTOCOL_VERSIONS,
    SUPPORTED_PROTOCOL_VERSIONS,
)

from lvke_mcp.runtime.errors import error_call_result, protocol_error_response
from lvke_mcp.runtime.coordination import build_coordination, coordination_schema


def _audit_hash(value: Any) -> str:
    """Return a stable request hash without leaking request contents."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        encoded = repr(value)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resolve_build_commit() -> str:
    """Resolve a source-checkout commit without host-application metadata."""

    configured = str(os.getenv("LVKE_MCP_GIT_SHA") or "").strip()
    if configured:
        return configured
    try:
        git_dir = Path(__file__).resolve().parents[2] / ".git"
        if git_dir.is_file():
            marker = git_dir.read_text(encoding="utf-8").strip()
            if marker.startswith("gitdir:"):
                git_dir = (git_dir.parent / marker.split(":", 1)[1].strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(":", 1)[1].strip()
        loose = git_dir / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()
        for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith(("#", "^")):
                sha, name = line.split(" ", 1)
                if name == ref:
                    return sha
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


_BUILD_COMMIT = _resolve_build_commit()
_BUILD_TIME = str(os.getenv("LVKE_MCP_BUILD_TIME") or "source-checkout")
_RUNTIME_INSTANCE = secrets.token_hex(6)


def _schema_validation_message(exc: ValidationError) -> str:
    """Expose a useful field path and JSON Schema constraint without a traceback."""

    path = [str(part) for part in exc.absolute_path]
    if exc.validator == "required" and isinstance(exc.instance, dict):
        required = exc.validator_value if isinstance(exc.validator_value, list) else []
        missing = next((str(name) for name in required if name not in exc.instance), "")
        if missing:
            path.append(missing)
    location = ".".join(path) or "<root>"
    constraint = " ".join(str(exc.message).split())[:1000]
    schema = exc.schema if isinstance(exc.schema, dict) else {}
    example = schema.get("examples", [None])[0] if schema.get("examples") else schema.get("default")
    expected_type = schema.get("type")
    if example is None and expected_type:
        example = {
            "string": "example",
            "integer": 1,
            "number": 1.0,
            "boolean": True,
            "array": [],
            "object": {},
        }.get(expected_type if isinstance(expected_type, str) else "")
    repair = "补充该字段并按工具 schema 提交" if exc.validator == "required" else "按该字段的类型、枚举和边界修正后重试"
    example_text = f" Example: {json.dumps(example, ensure_ascii=False)}." if example is not None else ""
    return (
        f"Schema validation failed at '{location}': {constraint}. "
        f"Remediation: {repair}.{example_text}"
    )

ToolResult = dict[str, Any]
ToolHandlerResult = ToolResult | types.InputRequiredResult
ToolHandler = Callable[
    [dict[str, Any]],
    ToolHandlerResult | Awaitable[ToolHandlerResult],
]
TaskOperation = Callable[[Any], Any | Awaitable[Any]]
ResourceLister = Callable[[], Iterable[types.Resource] | Awaitable[Iterable[types.Resource]]]
ResourceReader = Callable[
    [str],
    ReadResourceContents
    | Iterable[ReadResourceContents]
    | Awaitable[ReadResourceContents | Iterable[ReadResourceContents] | None]
    | None,
]

_CLIENT_REQUEST_METHODS = {
    schema.get("properties", {}).get("method", {}).get("const")
    for request_type in types.ClientRequest.__args__
    if (schema := request_type.model_json_schema())
}
_CLIENT_NOTIFICATION_METHODS = {
    schema.get("properties", {}).get("method", {}).get("const")
    for notification_type in types.ClientNotification.__args__
    if (schema := notification_type.model_json_schema())
}
_KNOWN_CLIENT_METHODS = (_CLIENT_REQUEST_METHODS | _CLIENT_NOTIFICATION_METHODS) - {None}
_KNOWN_CLIENT_METHODS.update(
    {"tasks/get", "tasks/list", "tasks/cancel", "tasks/result"}
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    handler: ToolHandler
    annotations: types.ToolAnnotations | None
    task_support: Literal["forbidden", "optional", "required"]


@dataclass(frozen=True)
class ResourceProvider:
    lister: ResourceLister
    reader: ResourceReader


@dataclass(frozen=True)
class TaskAdapter:
    """Persistent domain task bridge; the transport never owns task state."""

    get_task: TaskOperation
    list_tasks: TaskOperation
    cancel_task: TaskOperation
    get_result: TaskOperation


class OfficialStdioServer:
    """Registration facade that keeps business handlers independent of the SDK."""

    def __init__(self, server_name: str, server_version: str, logger) -> None:
        self.server_name = server_name
        self.server_version = server_version
        self.logger = logger
        self._tools: dict[str, ToolSpec] = {}
        self._resource_providers: list[ResourceProvider] = []
        self._task_adapter: TaskAdapter | None = None
        self.sdk_server = Server(
            server_name,
            version=server_version,
            cache_hints={
                "server/discover": CacheHint(ttl_ms=30_000, scope="private"),
                "tools/list": CacheHint(ttl_ms=30_000, scope="private"),
                "resources/list": CacheHint(ttl_ms=30_000, scope="private"),
            },
            on_list_tools=self._sdk_list_tools,
            on_call_tool=self._sdk_call_tool,
            on_list_resources=self._sdk_list_resources,
            on_read_resource=self._sdk_read_resource,
        )
        self.sdk_server.middleware.insert(0, self._strict_protocol_middleware)

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
        output_schema: dict[str, Any] | None = None,
        annotations: types.ToolAnnotations | None = None,
        task_support: Literal["forbidden", "optional", "required"] = "forbidden",
    ) -> None:
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        Draft202012Validator.check_schema(input_schema)
        if output_schema is not None:
            Draft202012Validator.check_schema(output_schema)
        if task_support not in {"forbidden", "optional", "required"}:
            raise ValueError(f"invalid task_support: {task_support}")
        if annotations is None:
            annotations = types.ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                openWorldHint=False,
            )
        self._tools[name] = ToolSpec(
            name,
            description,
            input_schema,
            output_schema,
            handler,
            annotations,
            task_support,
        )

    def register_resource_provider(
        self,
        lister: ResourceLister,
        reader: ResourceReader,
    ) -> None:
        """Register a dynamic read-only resource provider.

        Providers resolve immutable domain objects from their ``lvke://`` URI.  The
        business store remains outside the MCP process, so resources are still
        readable after a server restart.
        """

        self._resource_providers.append(ResourceProvider(lister=lister, reader=reader))

    @property
    def tool_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools.values())

    async def _strict_protocol_middleware(self, ctx, call_next):
        """Reject ambiguous legacy negotiation while leaving modern routing to MCP 2."""

        if ctx.method == "initialize":
            params = ctx.params or {}
            version = params.get("protocolVersion")
            if version not in HANDSHAKE_PROTOCOL_VERSIONS:
                raise MCPError(
                    types.INVALID_PARAMS,
                    "Unsupported MCP protocol version",
                    {"supportedProtocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS)},
                )
            client_info = params.get("clientInfo")
            if not isinstance(client_info, dict) or not all(
                isinstance(client_info.get(field), str)
                and bool(client_info[field].strip())
                for field in ("name", "version")
            ):
                raise MCPError(types.INVALID_PARAMS, "Invalid clientInfo")
        result = await call_next(ctx)
        if (
            ctx.method == "initialize"
            and self._task_adapter is not None
            and (ctx.params or {}).get("protocolVersion") == "2025-11-25"
            and isinstance(result, dict)
        ):
            capabilities = dict(result.get("capabilities") or {})
            capabilities["tasks"] = self._task_capability().model_dump(
                by_alias=True,
                exclude_none=True,
            )
            result = {**result, "capabilities": capabilities}
        return result

    def register_task_adapter(self, adapter: TaskAdapter) -> None:
        """Enable the legacy Tasks methods over a persistent domain adapter."""

        if self._task_adapter is not None:
            raise ValueError("task adapter already registered")
        self._task_adapter = adapter
        self.sdk_server.extensions["io.modelcontextprotocol/tasks"] = {
            "legacyProtocolVersion": "2025-11-25",
            "methods": ["tasks/get", "tasks/list", "tasks/cancel", "tasks/result"],
        }

        async def get_task(_ctx, params):
            return await self._resolve_task_operation(adapter.get_task, params)

        async def list_tasks(_ctx, params):
            return await self._resolve_task_operation(adapter.list_tasks, params)

        async def cancel_task(_ctx, params):
            return await self._resolve_task_operation(adapter.cancel_task, params)

        async def get_result(_ctx, params):
            return await self._resolve_task_operation(adapter.get_result, params)

        self.sdk_server.add_request_handler(
            "tasks/get",
            types.GetTaskRequestParams,
            get_task,
        )
        self.sdk_server.add_request_handler(
            "tasks/list",
            types.PaginatedRequestParams,
            list_tasks,
        )
        self.sdk_server.add_request_handler(
            "tasks/cancel",
            types.CancelTaskRequestParams,
            cancel_task,
        )
        self.sdk_server.add_request_handler(
            "tasks/result",
            types.GetTaskPayloadRequestParams,
            get_result,
        )

    @staticmethod
    async def _resolve_task_operation(operation: TaskOperation, params: Any) -> Any:
        result = operation(params)
        return await result if inspect.isawaitable(result) else result

    @staticmethod
    def _task_capability() -> types.ServerTasksCapability:
        return types.ServerTasksCapability(
            list=types.TasksListCapability(),
            cancel=types.TasksCancelCapability(),
            requests=types.ServerTasksRequestsCapability(
                tools=types.TasksToolsCapability(
                    call=types.TasksCallCapability(),
                )
            ),
        )

    async def _sdk_list_tools(
        self,
        ctx: ServerRequestContext,
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        include_structured = self._supports_structured_content(ctx.protocol_version)
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    inputSchema=self._public_input_schema(spec.input_schema),
                    outputSchema=(
                        self._public_output_schema(spec.output_schema)
                        if include_structured
                        else None
                    ),
                    annotations=spec.annotations,
                    execution=types.ToolExecution(taskSupport=spec.task_support),
                )
                for spec in self._tools.values()
            ]
        )

    async def _sdk_call_tool(
        self,
        ctx: ServerRequestContext,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult | types.InputRequiredResult:
        return await self._call_tool_async(
            params.name,
            params.arguments or {},
            self._supports_structured_content(ctx.protocol_version),
            protocol_version=ctx.protocol_version,
        )

    async def _sdk_list_resources(
        self,
        _ctx: ServerRequestContext,
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListResourcesResult:
        resources: list[types.Resource] = []
        for provider in self._resource_providers:
            listed = provider.lister()
            if inspect.isawaitable(listed):
                listed = await listed
            resources.extend(list(listed))
        return types.ListResourcesResult(resources=resources)

    async def _sdk_read_resource(
        self,
        _ctx: ServerRequestContext,
        params: types.ReadResourceRequestParams,
    ) -> types.ReadResourceResult:
        uri_text = str(params.uri)
        for provider in self._resource_providers:
            resolved = provider.reader(uri_text)
            if inspect.isawaitable(resolved):
                resolved = await resolved
            if resolved is None:
                continue
            items = [resolved] if isinstance(resolved, ReadResourceContents) else list(resolved)
            contents: list[types.TextResourceContents | types.BlobResourceContents] = []
            for item in items:
                if isinstance(item.content, bytes):
                    contents.append(
                        types.BlobResourceContents(
                            uri=uri_text,
                            blob=base64.b64encode(item.content).decode("ascii"),
                            mimeType=item.mime_type,
                            _meta=item.meta,
                        )
                    )
                else:
                    contents.append(
                        types.TextResourceContents(
                            uri=uri_text,
                            text=item.content,
                            mimeType=item.mime_type,
                            _meta=item.meta,
                        )
                    )
            return types.ReadResourceResult(contents=contents)
        raise MCPError(types.INVALID_PARAMS, "Unknown resource")

    @staticmethod
    def _validate(instance: Any, schema: dict[str, Any]) -> None:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        error = next(validator.iter_errors(instance), None)
        if error is not None:
            raise error

    def _call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        include_structured: bool,
    ) -> types.CallToolResult:
        spec = self._tools.get(name)
        started_at = datetime.now().astimezone()
        if spec is None:
            raise MCPError(types.INVALID_PARAMS, "Unknown tool")
        try:
            self._validate(arguments, spec.input_schema)
        except ValidationError as exc:
            self.logger.info(
                "tool %s input rejected at %s",
                name,
                "/".join(map(str, exc.path)),
            )
            raise MCPError(
                types.INVALID_PARAMS,
                _schema_validation_message(exc),
            ) from None

        try:
            result = spec.handler(arguments)
            if inspect.isawaitable(result):
                raise RuntimeError(
                    "async tool handler requires _call_tool_async or MCP transport"
                )
        except Exception:  # noqa: BLE001
            trace_id = f"mcp_{uuid.uuid4().hex}"
            self.logger.exception(
                "tool %s raised an unhandled exception trace_id=%s",
                name,
                trace_id,
            )
            return self._error_result(
                f"{self.server_name}.internal_error",
                "工具执行失败",
                trace_id=trace_id,
            )

        built = self._build_tool_result(
            spec,
            name,
            result,
            include_structured,
            audit={"started_at": started_at, "input_hash": _audit_hash(arguments)},
        )
        if isinstance(built, types.InputRequiredResult):  # pragma: no cover
            return self._legacy_input_required_result()
        return built

    async def _call_tool_async(
        self,
        name: str,
        arguments: dict[str, Any],
        include_structured: bool,
        *,
        protocol_version: str | None = None,
    ) -> types.CallToolResult | types.InputRequiredResult:
        """Async transport path while retaining ``_call_tool`` for sync tests."""

        spec = self._tools.get(name)
        started_at = datetime.now().astimezone()
        if spec is None:
            raise MCPError(types.INVALID_PARAMS, "Unknown tool")
        try:
            self._validate(arguments, spec.input_schema)
        except ValidationError as exc:
            self.logger.info(
                "tool %s input rejected at %s",
                name,
                "/".join(map(str, exc.path)),
            )
            raise MCPError(
                types.INVALID_PARAMS,
                _schema_validation_message(exc),
            ) from None

        try:
            result = spec.handler(arguments)
            if inspect.isawaitable(result):
                result = await result
        except Exception:  # noqa: BLE001
            trace_id = f"mcp_{uuid.uuid4().hex}"
            self.logger.exception(
                "tool %s raised an unhandled exception trace_id=%s",
                name,
                trace_id,
            )
            return self._error_result(
                f"{self.server_name}.internal_error",
                "工具执行失败",
                trace_id=trace_id,
            )
        return self._build_tool_result(
            spec,
            name,
            result,
            include_structured,
            protocol_version=protocol_version,
            audit={"started_at": started_at, "input_hash": _audit_hash(arguments)},
        )

    def _build_tool_result(
        self,
        spec: ToolSpec,
        name: str,
        result: Any,
        include_structured: bool,
        *,
        protocol_version: str | None = None,
        audit: dict[str, Any] | None = None,
    ) -> types.CallToolResult | types.InputRequiredResult:
        if isinstance(result, types.InputRequiredResult):
            if protocol_version in MODERN_PROTOCOL_VERSIONS:
                return result
            return self._legacy_input_required_result()
        if not isinstance(result, dict):
            return self._error_result(
                f"{self.server_name}.invalid_tool_output",
                "工具输出格式无效",
            )
        # Tool handlers are business-facing and may legitimately expose a policy
        # effective date.  MCP content and structuredContent, however, must be
        # JSON values.  Normalize only explicit temporal scalars at the transport
        # boundary; do not use ``default=str`` because that would silently leak
        # arbitrary implementation objects into a public MCP response.
        result = self._json_safe(result)
        if spec.output_schema is not None:
            try:
                self._validate(result, spec.output_schema)
            except (ValidationError, SchemaError):
                trace_id = f"mcp_{uuid.uuid4().hex}"
                self.logger.exception(
                    "tool %s output failed schema validation trace_id=%s",
                    name,
                    trace_id,
                )
                return self._error_result(
                    f"{self.server_name}.invalid_tool_output",
                    "工具输出未通过 outputSchema 校验",
                    trace_id=trace_id,
                )
        result = self._attach_runtime_metadata(result)
        audit = audit or {}
        finished_at = datetime.now().astimezone()
        started_at = audit.get("started_at")
        duration_ms = None
        if isinstance(started_at, datetime):
            duration_ms = max(0, round((finished_at - started_at).total_seconds() * 1000, 3))
            started_at = started_at.isoformat()
        result.setdefault("started_at", started_at or finished_at.isoformat())
        result.setdefault("finished_at", finished_at.isoformat())
        result.setdefault("duration_ms", duration_ms if duration_ms is not None else 0)
        result.setdefault("input_hash", str(audit.get("input_hash") or _audit_hash({})))
        result.setdefault("trace_id", str(audit.get("trace_id") or f"mcp_{uuid.uuid4().hex}"))
        result.setdefault("basis_hash", result.get("basis_hash") or result.get("run_basis_hash"))
        result.setdefault("lineage", result.get("lineage") or {})
        result.setdefault("content_hash", result.get("content_hash") or result.get("table_bundle_hash"))
        try:
            payload = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            self.logger.exception("tool %s returned a non-JSON output", name)
            return self._error_result(
                f"{self.server_name}.invalid_tool_output",
                "工具输出格式无效",
            )
        content = [types.TextContent(type="text", text=payload)]
        return types.CallToolResult(
            content=content,
            structured_content=result if include_structured else None,
            is_error=(
                result.get("success") is False
                and str(result.get("status") or "failed") == "failed"
            ),
        )

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        """Return JSON-compatible output without mutating business results."""

        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [cls._json_safe(item) for item in value]
        return value

    def _attach_runtime_metadata(self, result: dict[str, Any]) -> dict[str, Any]:
        payload = dict(result)
        raw_status = str(
            payload.get("status")
            or ("failed" if payload.get("success") is False else "ok")
        ).strip().lower()
        canonical_statuses = {
            "ok", "accepted", "partial", "empty", "missing_inputs", "blocked",
            "incomplete", "failed", "upstream_failure",
        }
        successful_terminal = {"applied", "released", "completed", "done", "cancelled"}
        active_statuses = {"pending", "queued", "running", "started", "processing"}
        if raw_status in canonical_statuses:
            status = raw_status
        elif raw_status in successful_terminal:
            status = "ok"
            payload.setdefault("domain_status", raw_status)
        elif raw_status in active_statuses:
            status = "accepted"
            payload.setdefault("task_status", raw_status)
        else:
            status = "blocked" if payload.get("success") is False else "ok"
            payload.setdefault("domain_status", raw_status)
        system_success = bool(payload.get(
            "system_success",
            payload.get("transport_success", status != "failed"),
        )) and status != "failed"
        business_success = status in {"ok", "accepted"}
        payload["status"] = status
        payload["success"] = business_success
        payload["business_success"] = business_success
        payload["system_success"] = system_success
        payload["transport_success"] = system_success
        payload["completed"] = status == "ok"
        payload["outcome"] = status
        if not business_success:
            payload.setdefault("code", f"{self.server_name}.{status}")
            payload.setdefault("message", {
                "partial": "工具仅完成部分业务结果",
                "empty": "未找到可用业务结果",
                "missing_inputs": "缺少完成业务所需输入",
                "blocked": "业务门禁阻断当前操作",
                "incomplete": "业务结果尚不完整",
                "failed": "工具执行失败",
                "upstream_failure": "上游服务未能提供结果",
            }.get(status, "业务未成功完成"))
        for field in ("resource_uris", "warnings", "blockers", "next_actions"):
            if payload.get(field) is None:
                payload[field] = []
            else:
                payload.setdefault(field, [])
        payload.setdefault("service_version", self.server_version)
        payload.setdefault("build_commit", _BUILD_COMMIT)
        payload.setdefault("build_time", _BUILD_TIME)
        payload.setdefault("schema_version", "mcp-envelope.v2")
        payload.setdefault("runtime_instance", _RUNTIME_INSTANCE)
        payload["coordination"] = build_coordination(payload, server_name=self.server_name)
        return payload

    @staticmethod
    def _public_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """Expose MCP 2's required object root without weakening branch schemas."""

        if schema.get("type") == "object":
            return schema
        return {"type": "object", **schema}

    @staticmethod
    def _public_output_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
        """Advertise the coordination contract without breaking legacy schemas."""

        if schema is None:
            return None
        public = dict(schema)
        properties = dict(public.get("properties") or {})
        properties["coordination"] = coordination_schema()
        properties.update({
            "started_at": {"type": "string", "format": "date-time"},
            "finished_at": {"type": "string", "format": "date-time"},
            "duration_ms": {"type": "number", "minimum": 0},
            "input_hash": {"type": ["string", "null"]},
            "basis_hash": {"type": ["string", "null"]},
            "content_hash": {"type": ["string", "null"]},
            "lineage": {"type": "object", "additionalProperties": True},
            "trace_id": {"type": "string", "minLength": 1},
        })
        public["properties"] = properties
        # Existing handlers are validated before runtime metadata is attached;
        # allowing the additive field keeps old strict schemas compatible.
        if public.get("additionalProperties") is False:
            public["additionalProperties"] = True
        return public

    def _error_result(
        self,
        code: str,
        message: str,
        *,
        trace_id: str | None = None,
    ) -> types.CallToolResult:
        return error_call_result(code, message, trace_id=trace_id, server_name=self.server_name)

    @staticmethod
    def _legacy_input_required_result() -> types.CallToolResult:
        payload = {
            "success": False,
            "business_success": False,
            "system_success": True,
            "transport_success": True,
            "status": "blocked",
            "code": "mcp.input_required_not_supported",
            "message": "当前客户端协议不支持交互式补充输入",
            "retryable": False,
            "resource_uris": [],
            "warnings": [],
            "blockers": ["modern_protocol_required"],
            "next_actions": ["使用 MCP 2026-07-28 请求封装重试"],
        }
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False),
                )
            ],
            structured_content=payload,
            is_error=False,
        )

    @staticmethod
    def _supports_structured_content(protocol_version: str) -> bool:
        return protocol_version in {
            "2025-06-18",
            "2025-11-25",
            *MODERN_PROTOCOL_VERSIONS,
        }

    def streamable_http_app(self, **kwargs):
        """Build the shared MCP 2 Streamable HTTP adapter without changing stdio."""

        return self.sdk_server.streamable_http_app(**kwargs)

    async def run_stdio(self) -> None:
        async with strict_stdio_server() as (read_stream, write_stream):
            await self.sdk_server.run(
                read_stream,
                write_stream,
                self.sdk_server.create_initialization_options(),
            )

    def serve_forever(self) -> None:
        anyio.run(self.run_stdio)


@asynccontextmanager
async def strict_stdio_server(stdin=None, stdout=None):
    """Official line transport with standard parse/invalid-request replies."""

    if stdin is None:
        stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8"))
    if stdout is None:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))

    read_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_reader = anyio.create_memory_object_stream(0)
    write_lock = anyio.Lock()

    async def write_payload(response: dict[str, Any]) -> None:
        async with write_lock:
            await stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            await stdout.flush()

    async def send_protocol_error(
        code: int,
        message: str,
        request_id: str | int | None = None,
    ) -> None:
        await write_payload(protocol_error_response(code, message, request_id))

    async def stdin_reader() -> None:
        async with read_writer:
            async for line in stdin:
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    await send_protocol_error(types.PARSE_ERROR, "Parse error")
                    continue
                if not isinstance(raw, dict):
                    await send_protocol_error(types.INVALID_REQUEST, "Invalid Request")
                    continue
                method = raw.get("method")
                if isinstance(method, str) and method not in _KNOWN_CLIENT_METHODS:
                    await write_payload(
                        protocol_error_response(
                            types.METHOD_NOT_FOUND,
                            "Method not found",
                            raw.get("id"),
                        )
                    )
                    continue
                try:
                    message = types.jsonrpc_message_adapter.validate_python(
                        raw,
                        by_name=False,
                    )
                except Exception:  # noqa: BLE001
                    request_id = raw.get("id")
                    valid_envelope = (
                        raw.get("jsonrpc") == "2.0"
                        and isinstance(raw.get("method"), str)
                        and isinstance(request_id, (str, int))
                    )
                    await send_protocol_error(
                        types.INVALID_PARAMS if valid_envelope else types.INVALID_REQUEST,
                        "Invalid request parameters" if valid_envelope else "Invalid Request",
                        request_id if valid_envelope else None,
                    )
                    continue
                await read_writer.send(SessionMessage(message))

    async def stdout_writer() -> None:
        async with write_reader:
            async for session_message in write_reader:
                payload = session_message.message.model_dump_json(
                    by_alias=True,
                    exclude_none=True,
                )
                async with write_lock:
                    await stdout.write(payload + "\n")
                    await stdout.flush()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stdin_reader)
        task_group.start_soon(stdout_writer)
        yield read_stream, write_stream
