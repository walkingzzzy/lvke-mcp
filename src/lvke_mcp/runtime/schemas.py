"""公共工具输出 envelope 的 JSON Schema 构造器（方案 §5.4 / §13）。

方案 5.4 要求每个工具定义自己的结构化输出，但至少保留统一的状态
envelope：``status`` / ``object_id`` / ``task_id`` / ``resource_uris`` /
``warnings`` / ``blockers`` / ``next_actions``。业务缺项、partial、阻断
和系统错误由 ``status`` 与传输层 ``system_success`` 明确区分。

本模块提供构造能力；公共 Server 传输层还会补齐四个数组字段，并用
``success`` / ``business_success`` 只表示业务是否接受，``system_success``
明确系统调用结果。新工具与后续收敛统一经
:func:`make_tool_output_schema` 生成专属 outputSchema，避免再出现跨工具
共享的宽泛 ``success/data`` 包。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator
from lvke_mcp.runtime.coordination import coordination_schema

# 方案 5.4 的状态枚举：业务缺项/部分完成/阻断/失败各自成态，
# 不允许把它们压扁成一个布尔。
STATUS_VALUES: tuple[str, ...] = (
    "ok",
    "accepted",
    "partial",
    "empty",
    "missing_inputs",
    "blocked",
    "incomplete",
    "failed",
    "upstream_failure",
)

# 这些状态下工具没有产出业务结果，因此 required_on_success 字段不适用。
# 'partial' 不在此列：部分成功仍然产出了结果，字段该在就得在。
_NON_SUCCESS_STATUS_VALUES: frozenset[str] = frozenset({
    "missing_inputs",
    "blocked",
    "incomplete",
    "failed",
    "upstream_failure",
    "empty",
})

_STRING_ARRAY: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "default": [],
}


def envelope_properties(
    status_values: Iterable[str] = STATUS_VALUES,
) -> dict[str, Any]:
    """返回公共 envelope 的 properties 片段（每次全新副本，可安全修改）。"""

    return {
        "success": {
            "type": "boolean",
            "description": "业务是否成功；仅 ok/accepted 为 true",
        },
        "business_success": {
            "type": "boolean",
            "description": "与 success 同义的显式业务成功字段",
        },
        "system_success": {
            "type": "boolean",
            "description": "MCP 处理器和协议执行是否成功",
        },
        "transport_success": {
            "type": "boolean",
            "description": "与 system_success 同义的传输兼容字段",
        },
        "status": {
            "type": "string",
            "enum": list(status_values),
            "description": "业务状态；partial/missing_inputs/blocked 不是系统错误",
        },
        "object_id": {"type": "string"},
        "task_id": {"type": "string"},
        "resource_uris": dict(_STRING_ARRAY),
        "warnings": dict(_STRING_ARRAY),
        "blockers": dict(_STRING_ARRAY),
        "next_actions": dict(_STRING_ARRAY),
        "trace_id": {"type": "string", "minLength": 1},
        "started_at": {"type": "string", "format": "date-time"},
        "finished_at": {"type": "string", "format": "date-time"},
        "duration_ms": {"type": "number", "minimum": 0},
        "input_hash": {"type": ["string", "null"]},
        "basis_hash": {"type": ["string", "null"]},
        "content_hash": {"type": ["string", "null"]},
        "lineage": {"type": "object", "additionalProperties": True},
        "coordination": coordination_schema(),
    }


def make_tool_output_schema(
    tool_properties: Mapping[str, Any] | None = None,
    required: Iterable[str] = (),
    *,
    status_values: Iterable[str] = STATUS_VALUES,
    additional_properties: bool = True,
    description: str | None = None,
    required_on_success: Iterable[str] = (),
) -> dict[str, Any]:
    """为单个工具生成"envelope + 专属字段"的 outputSchema。

    Args:
        tool_properties: 工具特有字段的 schema 片段（如 run_id、
            evidence_pack_id）；与 envelope 同名时以工具定义为准。
        required: 无论成功或失败都必须出现的字段（信封字段放这里）。
        status_values: 允许收窄状态枚举（如只读工具没有 missing_inputs）。
        additional_properties: 默认放开以兼容既有载荷的增量字段；
            契约收紧到位的工具可置 False。
        description: 可选的 schema 描述。
        required_on_success: **只在成功路径**必须出现的业务字段。

    ``required_on_success`` 存在的原因：把只有成功路径才算得出的字段（已索引
    字符数、交付状态、工件清单）无条件放进 ``required``，会让诚实的业务拒绝
    （"对象不存在"）撞上自己的 outputSchema —— transport 于是把它改写成
    ``invalid_tool_output`` + ``system_success=False``，调用方看到"服务器坏了"，
    而真实情况是"这个 ID 不存在"，原始业务码也一并丢失。

    这些字段仍然是成功路径的硬契约：用 ``if status ∈ 成功态 then required``
    表达，而不是从 ``required`` 里删掉了事——后者会连成功路径的约束一起放弃。
    """

    properties = envelope_properties(status_values)
    if tool_properties:
        properties.update({str(key): dict(value) for key, value in tool_properties.items()})
    required_fields = ["success", "status"]
    for field in required:
        name = str(field)
        if name not in required_fields:
            required_fields.append(name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required_fields,
        "additionalProperties": additional_properties,
    }
    success_only = [str(field) for field in required_on_success]
    success_only = [name for name in success_only if name not in required_fields]
    if success_only:
        # 成功态取自本 schema 实际允许的状态枚举，避免与 status_values 收窄后失配。
        allowed_statuses = list(properties.get("status", {}).get("enum") or STATUS_VALUES)
        success_statuses = [
            value for value in allowed_statuses
            if value not in _NON_SUCCESS_STATUS_VALUES
        ]
        schema["allOf"] = [{
            "if": {
                "properties": {
                    "success": {"const": True},
                    "status": {"enum": success_statuses},
                },
                "required": ["success", "status"],
            },
            "then": {"required": success_only},
        }]
    if description:
        schema["description"] = description
    Draft202012Validator.check_schema(schema)
    return schema


_LIGHTWEIGHT_OUTPUT_FIELDS: tuple[str, ...] = (
    "success",
    "business_success",
    "system_success",
    "transport_success",
    "status",
    "resource_uris",
    "warnings",
    "blockers",
    "next_actions",
    "trace_id",
    "input_hash",
    "basis_hash",
    "content_hash",
    "lineage",
    "build_commit",
    "build_time",
    "plugin_version",
    "build_metadata_complete",
)


def make_lightweight_output_schema(
    *,
    schema_uri: str,
    status_values: Iterable[str] = STATUS_VALUES,
) -> dict[str, Any]:
    """Compact outputSchema for ``tools/list``.

    Full per-tool schema remains authoritative for server-side validation and
    is published at ``schema_uri`` (``lvke://schemas/{server}/{tool}/output``).
    """

    envelope = envelope_properties(status_values)
    properties = {name: envelope[name] for name in _LIGHTWEIGHT_OUTPUT_FIELDS if name in envelope}
    properties["build_commit"] = {"type": "string"}
    properties["build_time"] = {"type": "string"}
    properties["plugin_version"] = {"type": "string"}
    properties["build_metadata_complete"] = {"type": "boolean"}
    schema: dict[str, Any] = {
        "type": "object",
        "description": (
            "轻量 envelope 投影；完整 outputSchema 通过 "
            "x-lvke-output-schema-uri Resource 读取。"
        ),
        "properties": properties,
        "required": ["success", "status"],
        "additionalProperties": True,
        "x-lvke-output-schema-uri": schema_uri,
    }
    Draft202012Validator.check_schema(schema)
    return schema
