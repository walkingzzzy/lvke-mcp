"""Official-SDK MCP server for zero-material estimate-preview delivery."""

from __future__ import annotations

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.runtime.schemas import make_tool_output_schema
from lvke_mcp.servers.lvke_zero_material_delivery import service

SERVER_NAME = "lvke-zero-material-delivery"
SERVER_VERSION = service.SERVICE_VERSION
logger = get_logger(SERVER_NAME)

_SAFE_ID = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"}
_KEY = {"type": "string", "minLength": 1, "maxLength": 200}
_TEXT = {"type": "string", "maxLength": 4000}
_OUTPUT = make_tool_output_schema(
    {
        "delivery_intent": {"type": "object"},
        "delivery_run": {"type": "object"},
        "assumption_package": {"type": "object"},
        "validation_complete": {"type": "boolean"},
        "input_evidence_complete": {"type": "boolean"},
    },
    required=("resource_uris", "warnings", "blockers", "next_actions"),
)


_ARTIFACT_STATE = {
    "type": "object",
    "properties": {
        "uri": {"type": "string"},
        "artifact_kind": {"type": "string"},
        "usable": {"type": "boolean"},
        "validation_status": {"type": "string"},
        "release_grade": {
            "type": "string",
            "enum": ["technical_preview", "unavailable", "formal"],
        },
        "blocking_reasons": {"type": "array", "items": {"type": "string"}},
        "is_deliverable": {
            "type": "boolean",
            "description": "false 表示中间对象（spec/context），既非可用交付物也不算失败",
        },
    },
    "required": ["uri", "usable", "validation_status", "release_grade"],
}
# 查询成功 ≠ 交付可用：两者必须是不同字段，否则 status=ok 会被读成"工件可交付"。
_DELIVERY_STATE_OUTPUT = make_tool_output_schema(
    {
        "delivery_run": {"type": "object"},
        "query_success": {
            "type": "boolean",
            "description": "本次查询是否成功；与交付状态无关",
        },
        "domain_status": {
            "type": "string",
            "enum": ["ready", "partial", "blocked"],
            "description": "交付域真实状态；ready 之外一律不得视为可交付",
        },
        "delivery_state": {
            "type": "string",
            "enum": ["ready", "partial", "blocked", "in_progress", "cancelled"],
        },
        "artifacts": {"type": "array", "items": _ARTIFACT_STATE},
        "usable_artifact_count": {"type": "integer", "minimum": 0},
        "unusable_artifact_uris": {"type": "array", "items": {"type": "string"}},
        "technical_preview_ready": {"type": "boolean"},
        "validation_complete": {"type": "boolean"},
        "input_evidence_complete": {"type": "boolean"},
    },
    required=(
        "resource_uris",
        "warnings",
        "blockers",
        "next_actions",
    ),
    # 这五个字段只有在真的读到 DeliveryRun 时才算得出。此前它们无条件必填，
    # 于是"运行不存在"这种诚实拒绝会撞上自己的 schema，被 transport 改写成
    # invalid_tool_output + system_success=False —— 调用方看到"服务器坏了"，
    # 而 delivery_run_not_found 这个真正有用的码被丢掉。成功路径仍强制要求。
    required_on_success=(
        "query_success",
        "domain_status",
        "delivery_state",
        "artifacts",
        "technical_preview_ready",
    ),
)


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_id": _SAFE_ID,
            **properties,
        },
        "required": ["workspace_id", *required],
    }


_TRANSITION_BRANCHES = {
    "cancel": "delivery_cancel",
    "resume": "delivery_resume",
}


def _install_transition_aggregate(
    server: OfficialStdioServer,
    annotations: types.ToolAnnotations,
) -> None:
    legacy = {
        name: server._tools[name]  # noqa: SLF001
        for name in _TRANSITION_BRANCHES.values()
    }
    server._round2_legacy_specs = legacy  # type: ignore[attr-defined]  # noqa: SLF001
    schema = _schema(
        {
            "operation": {"type": "string", "enum": list(_TRANSITION_BRANCHES)},
            "delivery_run_id": _SAFE_ID,
            "reason": _TEXT,
            "idempotency_key": _KEY,
        },
        ["operation", "delivery_run_id", "idempotency_key"],
    )
    schema["allOf"] = [
        {
            "if": {
                "properties": {"operation": {"const": "cancel"}},
                "required": ["operation"],
            },
            "then": {"required": ["reason"]},
        }
    ]

    def dispatch(args: dict) -> dict:
        operation = str(args["operation"])
        mapped = {
            "workspace_id": args["workspace_id"],
            "delivery_run_id": args["delivery_run_id"],
            "idempotency_key": args["idempotency_key"],
        }
        if "reason" in args:
            mapped["reason"] = args["reason"]
        return legacy[_TRANSITION_BRANCHES[operation]].handler(mapped)

    server.register_tool(
        "delivery_transition",
        "取消 DeliveryRun 或从已取消运行创建恢复快照；操作分支保留原状态门禁。",
        schema,
        dispatch,
        _OUTPUT,
        annotations,
    )
    for name in legacy:
        server._tools.pop(name)  # noqa: SLF001


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read = types.ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    write = types.ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    server.register_tool(
        "delivery_create_from_sentence",
        "从一句话创建零材料 DeliveryIntent 和初始 DeliveryRun；行业歧义时返回结构化 missing_inputs。",
        _schema(
            {
                "sentence": {"type": "string", "minLength": 2, "maxLength": 4000},
                "project_name": {"type": "string", "maxLength": 160},
                "region": {"type": "string", "maxLength": 160},
                "industry": {"type": "string", "maxLength": 160},
                "project_nature": {"type": "string", "maxLength": 160},
                "report_type": {"type": "string", "maxLength": 160},
                "idempotency_key": _KEY,
            },
            ["sentence", "idempotency_key"],
        ),
        service.create_from_sentence,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "delivery_start",
        "从持久化运行快照启动或继续零材料交付；每个阶段生成新的不可变对象。",
        _schema(
            {"delivery_run_id": _SAFE_ID, "idempotency_key": _KEY},
            ["delivery_run_id", "idempotency_key"],
        ),
        service.start,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "delivery_status",
        "读取 DeliveryRun 阶段、进度、blocker 和恢复令牌。"
        "query_success 表示本次查询成功，domain_status/delivery_state 才是交付真实状态；"
        "每个工件自带 usable、validation_status 与 release_grade。",
        _schema({"delivery_run_id": _SAFE_ID}, ["delivery_run_id"]),
        service.status,
        _DELIVERY_STATE_OUTPUT,
        read,
    )
    server.register_tool(
        "delivery_get",
        "按不可变对象 ID 读取 DeliveryIntent、AssumptionPackage 或 DeliveryRun。",
        _schema({"object_id": _SAFE_ID}, ["object_id"]),
        service.get_delivery,
        _OUTPUT,
        read,
    )
    server.register_tool(
        "delivery_list_assumptions",
        "读取字段级假设并按敏感度返回前 5 至 10 个确认项。",
        _schema(
            {
                "assumption_package_id": _SAFE_ID,
                "limit": {"type": "integer", "minimum": 5, "maximum": 10, "default": 10},
            },
            ["assumption_package_id"],
        ),
        service.list_assumptions,
        _OUTPUT,
        read,
    )
    server.register_tool(
        "delivery_confirm_assumptions",
        "确认关键参数并创建新的 AssumptionPackage 和 DeliveryRun，不覆盖旧版本。",
        _schema(
            {
                "assumption_package_id": _SAFE_ID,
                "confirmations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string", "minLength": 1, "maxLength": 128},
                            "value": {"type": ["number", "integer", "string", "boolean"]},
                            "source_ref": {"type": "string", "maxLength": 1000},
                            "note": {"type": "string", "maxLength": 1000},
                        },
                        "required": ["name", "value"],
                    },
                },
                "idempotency_key": _KEY,
            },
            ["assumption_package_id", "confirmations", "idempotency_key"],
        ),
        service.confirm_assumptions,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "delivery_get_artifacts",
        "聚合读取同一 DeliveryRun 的交付 Resource URI 和 manifest 引用；"
        "每个工件带 usable、validation_status 与 release_grade，URI 可读不等于可交付。",
        _schema({"delivery_run_id": _SAFE_ID}, ["delivery_run_id"]),
        service.get_artifacts,
        _DELIVERY_STATE_OUTPUT,
        read,
    )
    server.register_tool(
        "delivery_cancel",
        "创建 cancelled DeliveryRun 快照；不删除已生成对象或工件。",
        _schema(
            {"delivery_run_id": _SAFE_ID, "reason": _TEXT, "idempotency_key": _KEY},
            ["delivery_run_id", "reason", "idempotency_key"],
        ),
        service.cancel,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "delivery_resume",
        "从 cancelled DeliveryRun 创建恢复快照，不依赖聊天历史。",
        _schema(
            {"delivery_run_id": _SAFE_ID, "reason": _TEXT, "idempotency_key": _KEY},
            ["delivery_run_id", "idempotency_key"],
        ),
        service.resume,
        _OUTPUT,
        write,
    )
    _install_transition_aggregate(server, write)

    def read_standard_resource(uri: str):
        resolved = service.resolve_resource(uri)
        if resolved is None:
            return None
        content, mime_type = resolved
        return ReadResourceContents(content, mime_type)

    # 协议层 lister 拿不到 workspace 身份，因此**无法**按租户过滤：任何非空实现
    # 都会把别的 workspace 的对象 URI 暴露给当前客户端。与其余 12 个 server 一致
    # 留空，工作区内的枚举走 lvke_list_resources(domain=...) → service.list_resources，
    # 那条路径有显式 workspace_id 并逐条校验归属。
    server.register_resource_provider(lambda: [], read_standard_resource)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
