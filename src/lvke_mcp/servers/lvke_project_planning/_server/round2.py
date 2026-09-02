"""Round-two aggregate routes for the project-planning server.

``_install_round2_aggregates`` captures the legacy tool specs in a closure
before popping their public names, so the eight aggregate handlers keep
dispatching to the original handlers with their original idempotency
operation namespaces.
"""

from __future__ import annotations

import copy
import json

from mcp import types

from lvke_mcp.runtime.transport import OfficialStdioServer

from .dispatch_tables import (
    _COMPARE_BRANCHES,
    _CONFIRM_BRANCHES,
    _CREATE_BRANCHES,
    _PREPARE_BRANCHES,
    _VALIDATE_BRANCHES,
    CREATE_OPERATION_BY_KIND,
    PREPARE_OPERATION_BY_KIND,
)
from .schema_parts import (
    _KEY,
    _OUTPUT,
    _STRING,
    _schema,
)


def _branch_payload_schema(input_schema: dict, excluded: set[str]) -> dict:
    """Return the strict branch-only portion of a legacy tool schema."""

    properties = {
        key: copy.deepcopy(value)
        for key, value in input_schema.get("properties", {}).items()
        if key not in excluded
    }
    required = [
        key for key in input_schema.get("required", []) if key not in excluded
    ]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def _discriminated_payload_schema(
    discriminator: str,
    branch_specs: dict[str, object],
    *,
    common_properties: dict,
    common_required: list[str],
    excluded_by_kind: dict[str, set[str]],
) -> dict:
    schema = _schema(
        {
            discriminator: {"type": "string", "enum": list(branch_specs)},
            **common_properties,
            "payload": {"type": "object"},
        },
        [discriminator, *common_required, "payload"],
    )
    schema["allOf"] = [
        {
            "if": {
                "properties": {discriminator: {"const": kind}},
                "required": [discriminator],
            },
            "then": {
                "properties": {
                    "payload": _branch_payload_schema(
                        spec.input_schema, excluded_by_kind[kind]
                    )
                }
            },
        }
        for kind, spec in branch_specs.items()
    ]
    return schema


def _public_discriminated_payload_schema(
    server: OfficialStdioServer,
    tool_name: str,
    schema: dict,
) -> dict:
    """Inline each branch's argument names without duplicating huge subtrees."""

    public = copy.deepcopy(schema)
    schema_uri = server._tool_schema_uri(tool_name)  # noqa: SLF001
    for index, branch in enumerate(public.get("allOf", [])):
        payload = branch["then"]["properties"]["payload"]
        projected_properties = {}
        for property_name, property_schema in payload.get("properties", {}).items():
            size = len(json.dumps(property_schema, ensure_ascii=False, separators=(",", ":")))
            if size <= 256:
                projected_properties[property_name] = property_schema
                continue
            projected_properties[property_name] = server._compact_public_schema(  # noqa: SLF001
                property_schema,
                schema_uri=schema_uri,
                pointer=(
                    f"#/allOf/{index}/then/properties/payload/properties/"
                    f"{property_name}"
                ),
            )
        projected = {
            "type": "object",
            "additionalProperties": False,
            "properties": projected_properties,
            "required": list(payload.get("required", [])),
        }
        if payload.get("anyOf"):
            projected["anyOf"] = copy.deepcopy(payload["anyOf"])
        if payload.get("oneOf"):
            projected["oneOf"] = copy.deepcopy(payload["oneOf"])
        branch["then"]["properties"]["payload"] = projected
    return public


def _install_round2_aggregates(
    server: OfficialStdioServer,
    read: types.ToolAnnotations,
    write: types.ToolAnnotations,
) -> dict[str, dict]:
    """Install the eight round-two routes and remove their legacy public names."""

    legacy_names = {
        *(name for name, _field in _VALIDATE_BRANCHES.values()),
        *(name for name, _field in _COMPARE_BRANCHES.values()),
        *(name for name, _field in _CONFIRM_BRANCHES.values()),
        *_PREPARE_BRANCHES.values(),
        *_CREATE_BRANCHES.values(),
    }
    legacy = {name: server._tools[name] for name in legacy_names}  # noqa: SLF001
    server._round2_legacy_specs = legacy  # type: ignore[attr-defined]  # noqa: SLF001

    def dispatch_target(args: dict, branches: dict[str, tuple[str, str]], key: str):
        legacy_name, id_field = branches[str(args[key])]
        return legacy[legacy_name].handler(
            {"workspace_id": args["workspace_id"], id_field: args["target_id"]}
        )

    validate_schema = _schema(
        {
            "object_kind": {"type": "string", "enum": list(_VALIDATE_BRANCHES)},
            "target_id": _STRING,
        },
        ["object_kind", "target_id"],
    )
    server.register_tool(
        "planning_validate",
        "按对象类型校验规划对象，保留各类型原有证据、算术和完整性门禁。",
        validate_schema,
        lambda a: dispatch_target(a, _VALIDATE_BRANCHES, "object_kind"),
        _OUTPUT,
        read,
    )

    compare_schema = _schema(
        {
            "object_kind": {"type": "string", "enum": list(_COMPARE_BRANCHES)},
            "target_id": _STRING,
        },
        ["object_kind", "target_id"],
    )
    server.register_tool(
        "planning_compare",
        "按对象类型比较规划候选；不合并候选、不隐式选择或计算平均值。",
        compare_schema,
        lambda a: dispatch_target(a, _COMPARE_BRANCHES, "object_kind"),
        _OUTPUT,
        read,
    )

    confirm_specs = {
        kind: legacy[name] for kind, (name, _field) in _CONFIRM_BRANCHES.items()
    }
    confirm_schema = _discriminated_payload_schema(
        "object_kind",
        confirm_specs,
        common_properties={"target_id": _STRING, "idempotency_key": _KEY},
        common_required=["target_id", "idempotency_key"],
        excluded_by_kind={
            kind: {"workspace_id", id_field, "idempotency_key"}
            for kind, (_name, id_field) in _CONFIRM_BRANCHES.items()
        },
    )

    def dispatch_confirm(args: dict):
        kind = str(args["object_kind"])
        legacy_name, id_field = _CONFIRM_BRANCHES[kind]
        mapped = {
            "workspace_id": args["workspace_id"],
            id_field: args["target_id"],
            "idempotency_key": args["idempotency_key"],
            **dict(args["payload"]),
        }
        return legacy[legacy_name].handler(mapped)

    server.register_tool(
        "planning_confirm",
        "按对象类型执行显式确认；选择、舍弃清单与确认理由按分支严格校验。",
        confirm_schema,
        dispatch_confirm,
        _OUTPUT,
        write,
        public_input_schema=_public_discriminated_payload_schema(
            server, "planning_confirm", confirm_schema
        ),
    )

    def install_payload_tool(
        public_name: str,
        branches: dict[str, str],
        operation_map: dict[str, str],
        description: str,
    ) -> dict:
        branch_specs = {kind: legacy[name] for kind, name in branches.items()}
        schema = _discriminated_payload_schema(
            "object_kind",
            branch_specs,
            common_properties={
                "project_context_id": _STRING,
                "idempotency_key": _KEY,
            },
            common_required=["project_context_id", "idempotency_key"],
            excluded_by_kind={
                kind: {"workspace_id", "project_context_id", "idempotency_key"}
                for kind in branches
            },
        )

        def dispatch(args: dict):
            kind = str(args["object_kind"])
            # Resolve through an explicit map so historical idempotency operation
            # namespaces never depend on mechanical name construction.
            legacy_name = branches[kind]
            assert operation_map[kind]
            return legacy[legacy_name].handler(
                {
                    "workspace_id": args["workspace_id"],
                    "project_context_id": args["project_context_id"],
                    "idempotency_key": args["idempotency_key"],
                    **dict(args["payload"]),
                }
            )

        server.register_tool(
            public_name,
            description,
            schema,
            dispatch,
            _OUTPUT,
            write,
            public_input_schema=_public_discriminated_payload_schema(
                server, public_name, schema
            ),
        )
        return schema

    prepare_schema = install_payload_tool(
        "planning_prepare",
        _PREPARE_BRANCHES,
        PREPARE_OPERATION_BY_KIND,
        "按对象类型固化规划候选；各类候选与上游对象字段执行完整分支约束。",
    )
    create_schema = install_payload_tool(
        "planning_create",
        _CREATE_BRANCHES,
        CREATE_OPERATION_BY_KIND,
        "按对象类型直接创建不可变规划对象；不补默认业务事实或放宽原门禁。",
    )

    for name in legacy_names:
        server._tools.pop(name)  # noqa: SLF001

    return {
        "planning_validate": validate_schema,
        "planning_confirm": confirm_schema,
        "planning_prepare": prepare_schema,
        "planning_create": create_schema,
    }
