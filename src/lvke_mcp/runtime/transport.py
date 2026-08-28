"""Strict stdio MCP runtime built on the official MCP Python SDK."""

from __future__ import annotations

import base64
import copy
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
from lvke_mcp.runtime.schemas import make_lightweight_output_schema, make_tool_output_schema
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
from lvke_mcp.runtime.coordination import build_coordination
from lvke_mcp.runtime.build_metadata import build_metadata
from lvke_mcp.runtime.input_guards import (
    find_rejected_identifier,
    identifier_rejection_payload,
)
from lvke_mcp.runtime.outcomes import normalize_operation_outcome


def _audit_hash(value: Any) -> str:
    """Return a stable request hash without leaking request contents."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        encoded = repr(value)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# 构建元数据由 lvke_mcp.runtime.build_metadata 统一解析：14 个服务共享同一
# commit / build_time / plugin_version 快照，缺失时显式暴露
# build_metadata_incomplete 而不是写 source-checkout 占位串。
_BUILD_METADATA = build_metadata()
_RUNTIME_INSTANCE = secrets.token_hex(6)
# Keep tools/list below the hard context budget while preserving every
# top-level argument and its scalar constraints.  Larger nested objects remain
# available through their full immutable schema Resource.
_PUBLIC_SCHEMA_INLINE_LIMIT = 2 * 1024
_PUBLIC_SCHEMA_DOC_KEYS = frozenset({"description", "examples", "example", "title", "$comment"})


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
    public_input_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResourceProvider:
    lister: ResourceLister
    reader: ResourceReader


@dataclass(frozen=True)
class SchemaResource:
    uri: str
    name: str
    title: str
    description: str
    schema: dict[str, Any]


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
        self._schema_resources: dict[str, SchemaResource] = {}
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
        public_input_schema: dict[str, Any] | None = None,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        Draft202012Validator.check_schema(input_schema)
        if public_input_schema is not None:
            Draft202012Validator.check_schema(public_input_schema)
        if output_schema is not None:
            Draft202012Validator.check_schema(output_schema)
        effective_output = output_schema or make_tool_output_schema()
        if task_support not in {"forbidden", "optional", "required"}:
            raise ValueError(f"invalid task_support: {task_support}")
        if annotations is None:
            annotations = types.ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                openWorldHint=False,
            )
        self._tools[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=effective_output,
            handler=handler,
            annotations=annotations,
            task_support=task_support,
            public_input_schema=public_input_schema,
        )
        output_uri = self._tool_output_schema_uri(name)
        if output_uri not in self._schema_resources:
            self.register_schema_resource(
                output_uri,
                effective_output,
                name=f"{self.server_name}.{name}.output-schema",
                title=f"{name} complete output schema",
                description="服务端实际执行的完整 output JSON Schema。",
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

    def register_schema_resource(
        self,
        uri: str,
        schema: dict[str, Any],
        *,
        name: str,
        title: str,
        description: str = "服务端执行的完整 JSON Schema。",
    ) -> None:
        """Publish a stable, read-only alias for an authoritative schema.

        The schema remains the same object used by the server-side validator;
        this registration only gives clients a durable Resource URI that does
        not depend on a particular tool name.
        """

        if not uri.startswith("lvke://schemas/"):
            raise ValueError("schema resource URI must start with lvke://schemas/")
        if uri in self._schema_resources:
            raise ValueError(f"schema resource already registered: {uri}")
        Draft202012Validator.check_schema(schema)
        self._schema_resources[uri] = SchemaResource(
            uri=uri,
            name=name,
            title=title,
            description=description,
            schema=schema,
        )

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
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    inputSchema=self._public_input_schema(
                        spec.name,
                        spec.input_schema,
                        public_schema=spec.public_input_schema,
                    ),
                    outputSchema=self._public_output_schema(spec),
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
        resources: list[types.Resource] = [
            types.Resource(
                name=resource.name,
                title=resource.title,
                uri=resource.uri,
                description=resource.description,
                mimeType="application/schema+json",
            )
            for resource in self._schema_resources.values()
        ]
        resources.extend([
            types.Resource(
                name=f"{self.server_name}.{spec.name}.input-schema",
                title=f"{spec.name} complete input schema",
                uri=self._tool_input_schema_uri(spec.name),
                description="服务端实际执行的完整 JSON Schema；tools/list 只发布紧凑投影。",
                mimeType="application/schema+json",
            )
            for spec in self._tools.values()
            if self._schema_size(spec.input_schema) > _PUBLIC_SCHEMA_INLINE_LIMIT
            and self._tool_input_schema_uri(spec.name) not in self._schema_resources
        ])
        # output-schema 已在 register_tool 时写入 _schema_resources，此处不再重复追加。
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
        static_schema = self._schema_resources.get(uri_text)
        if static_schema is not None:
            return types.ReadResourceResult(
                contents=[
                    types.TextResourceContents(
                        uri=uri_text,
                        text=json.dumps(
                            static_schema.schema,
                            ensure_ascii=False,
                            indent=2,
                        ),
                        mimeType="application/schema+json",
                    )
                ]
            )
        schema_name, schema_kind = self._tool_name_from_schema_uri(uri_text)
        if schema_name is not None:
            spec = self._tools.get(schema_name)
            if spec is None:
                raise MCPError(types.INVALID_PARAMS, "Unknown schema resource")
            schema_payload = (
                spec.output_schema if schema_kind == "output" else spec.input_schema
            )
            if schema_payload is None:
                raise MCPError(types.INVALID_PARAMS, "Unknown schema resource")
            return types.ReadResourceResult(
                contents=[
                    types.TextResourceContents(
                        uri=uri_text,
                        text=json.dumps(schema_payload, ensure_ascii=False, indent=2),
                        mimeType="application/schema+json",
                    )
                ]
            )
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

        rejected = self._identifier_rejection(spec, name, arguments)
        if rejected is not None:
            return rejected

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

        rejected = self._identifier_rejection(
            spec, name, arguments, protocol_version=protocol_version
        )
        if rejected is not None:
            return rejected

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
        payload = normalize_operation_outcome(result, server_name=self.server_name)
        for field in ("resource_uris", "warnings", "blockers", "next_actions"):
            if payload.get(field) is None:
                payload[field] = []
            else:
                payload.setdefault(field, [])
        payload.setdefault("service_version", self.server_version)
        for key, value in _BUILD_METADATA.envelope_fields().items():
            payload.setdefault(key, value)
        payload.setdefault("schema_version", "mcp-envelope.v2")
        payload.setdefault("runtime_instance", _RUNTIME_INSTANCE)
        payload["coordination"] = build_coordination(payload, server_name=self.server_name)
        return payload

    def _tool_input_schema_uri(self, tool_name: str) -> str:
        return f"lvke://schemas/{self.server_name}/{tool_name}/input"

    def _tool_output_schema_uri(self, tool_name: str) -> str:
        return f"lvke://schemas/{self.server_name}/{tool_name}/output"

    def _tool_schema_uri(self, tool_name: str) -> str:
        """Backward-compatible alias for input schema URI."""

        return self._tool_input_schema_uri(tool_name)

    def _tool_name_from_schema_uri(self, uri: str) -> tuple[str | None, str | None]:
        prefix = f"lvke://schemas/{self.server_name}/"
        if not uri.startswith(prefix):
            return None, None
        remainder = uri[len(prefix) :]
        if remainder.endswith("/input"):
            value = remainder[: -len("/input")]
            return (value if value and "/" not in value else None), "input"
        if remainder.endswith("/output"):
            value = remainder[: -len("/output")]
            return (value if value and "/" not in value else None), "output"
        return None, None

    def _public_output_schema(self, spec: ToolSpec) -> dict[str, Any]:
        return make_lightweight_output_schema(
            schema_uri=self._tool_output_schema_uri(spec.name),
        )

    @staticmethod
    def _schema_size(schema: dict[str, Any]) -> int:
        return len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")))

    def _compact_public_schema(
        self,
        value: Any,
        *,
        schema_uri: str,
        pointer: str = "#",
        root: bool = False,
    ) -> Any:
        if isinstance(value, list):
            return [
                self._compact_public_schema(
                    item,
                    schema_uri=schema_uri,
                    pointer=f"{pointer}/{index}",
                )
                for index, item in enumerate(value)
            ]
        if not isinstance(value, dict):
            return value

        declared_schema_uri = value.get("x-lvke-schema-uri")
        if (
            isinstance(declared_schema_uri, str)
            and declared_schema_uri.startswith("lvke://schemas/")
            and declared_schema_uri != schema_uri
        ):
            schema_uri = declared_schema_uri
            pointer = "#"

        size = self._schema_size(value)
        if not root and size > _PUBLIC_SCHEMA_INLINE_LIMIT:
            compact: dict[str, Any] = {
                "type": value.get("type", "object"),
                "x-lvke-schema-uri": schema_uri,
                "x-lvke-schema-pointer": pointer,
            }
            for key in (
                "default",
                "enum",
                "const",
                "format",
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "minLength",
                "maxLength",
                "pattern",
                "minItems",
                "maxItems",
                "uniqueItems",
            ):
                if key in value:
                    compact[key] = value[key]
            if value.get("type") == "array" and "items" in value:
                compact["items"] = self._compact_public_schema(
                    value["items"],
                    schema_uri=schema_uri,
                    pointer=f"{pointer}/items",
                )
            return compact

        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in _PUBLIC_SCHEMA_DOC_KEYS:
                continue
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            if root and key == "properties" and isinstance(item, dict):
                # The public contract must retain every top-level argument name.
                # Large schemas are compacted one property at a time so the
                # properties map itself is never replaced by an opaque stub.
                result[key] = {
                    property_name: self._compact_public_schema(
                        property_schema,
                        schema_uri=schema_uri,
                        pointer=(
                            f"{pointer}/{escaped}/"
                            f"{str(property_name).replace('~', '~0').replace('/', '~1')}"
                        ),
                    )
                    for property_name, property_schema in item.items()
                }
            else:
                result[key] = self._compact_public_schema(
                    item,
                    schema_uri=schema_uri,
                    pointer=f"{pointer}/{escaped}",
                )
        if root and size > _PUBLIC_SCHEMA_INLINE_LIMIT:
            result["x-lvke-schema-uri"] = schema_uri
        return result

    def _public_input_schema(
        self,
        tool_name: str,
        schema: dict[str, Any],
        *,
        public_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Publish a compact projection while retaining the full validator."""

        # A deliberately authored projection is already bounded and preserves
        # the fields a client needs to form a first valid call.  Do not run it
        # through the generic size compressor: that would turn its nested
        # discriminators back into the opaque stubs it exists to avoid.
        if public_schema is not None:
            result = copy.deepcopy(public_schema)
            result.setdefault("x-lvke-schema-uri", self._tool_schema_uri(tool_name))
            return result

        selected = schema
        rooted = selected if selected.get("type") == "object" else {"type": "object", **selected}
        return self._compact_public_schema(
            rooted,
            schema_uri=self._tool_schema_uri(tool_name),
            root=True,
        )

    @staticmethod
    def _constant_output_fields(output_schema: dict[str, Any] | None) -> dict[str, Any]:
        """取出 outputSchema 中必填且被钉成常量的字段。

        有些工具的必填字段与"这次调用成功没有"无关，而是工具本身的恒定属性
        （如 ``finance_generate_package`` 的 ``deprecated: {"const": true}``）。
        这类字段在任何响应里都该出现，包括入参被拒时，因此由此处补齐，
        而不是把它们从 required 里挪走。
        """

        if not isinstance(output_schema, dict):
            return {}
        properties = output_schema.get("properties")
        required = output_schema.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list):
            return {}
        constants: dict[str, Any] = {}
        for field in required:
            declared = properties.get(str(field))
            if isinstance(declared, dict) and "const" in declared:
                constants[str(field)] = declared["const"]
        return constants

    def _identifier_rejection(
        self,
        spec: ToolSpec,
        name: str,
        arguments: dict[str, Any],
        *,
        protocol_version: str | None = None,
    ) -> types.CallToolResult | None:
        """把非法标识符在派发前转成业务阻断，而不是让它降级成系统故障。

        ``require_safe_id`` 抛的 ``ValueError`` 若一路冒到下面的兜底 except，会被
        记成 ``internal_error`` + ``system_success=False``——那是在谎报故障归属：
        入参不合法是调用方要改参数，不是服务端事故。这里在调 handler 之前用同一条
        ``_SAFE_ID`` 规则自查，因此错误码能指向调用方**实际提交**的字段名
        （存储层的 ValueError 只会报它自己的形参名，实测 41/52 次都是通用的
        ``object_id``，据此生成的码会指向调用方没提交过的字段）。

        返回 ``None`` 表示无越界字段，调用方继续正常派发。
        """

        field = find_rejected_identifier(arguments, spec.input_schema)
        if field is None:
            return None
        self.logger.info("tool %s rejected identifier field %s", name, field)
        payload = {
            **identifier_rejection_payload(field, self.server_name),
            **self._constant_output_fields(spec.output_schema),
        }
        # 仍走 _build_tool_result：该工具自己的 outputSchema 必须能容纳这个阻断
        # 载荷，否则就会重演"业务拒绝撞自己 schema"的老问题。
        built = self._build_tool_result(
            spec,
            name,
            payload,
            True,
            protocol_version=protocol_version,
            audit={
                "started_at": datetime.now().astimezone(),
                "input_hash": _audit_hash(arguments),
            },
        )
        if isinstance(built, types.InputRequiredResult):  # pragma: no cover
            return self._legacy_input_required_result()
        return built

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
