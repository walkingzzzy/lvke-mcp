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
        "读取 DeliveryRun 阶段、进度、blocker 和恢复令牌。",
        _schema({"delivery_run_id": _SAFE_ID}, ["delivery_run_id"]),
        service.status,
        _OUTPUT,
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
        "聚合读取同一 DeliveryRun 的交付 Resource URI 和 manifest 引用。",
        _schema({"delivery_run_id": _SAFE_ID}, ["delivery_run_id"]),
        service.get_artifacts,
        _OUTPUT,
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
    server.register_tool(
        "delivery_list_resources",
        "分页列举当前 workspace 的不可变零材料交付 Resources。",
        _schema(
            {
                "resource_type": {
                    "type": "string",
                    "enum": [
                        "DeliveryIntent",
                        "AssumptionPackage",
                        "DeliveryRun",
                        "TechnicalReport",
                        "AssumptionRegister",
                        "GapRegister",
                        "EvidenceManifest",
                        "RunManifest",
                    ],
                },
                "cursor": {"type": "string", "maxLength": 8192},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            [],
        ),
        service.list_resources,
        _OUTPUT,
        read,
    )
    server.register_tool(
        "delivery_read_resource",
        "在 workspace 作用域内读取零材料交付 Resource。",
        _schema(
            {
                "uri": {
                    "type": "string",
                    "pattern": r"^lvke://(?:zero-material-delivery|finance-tables)/workspaces/",
                    "maxLength": 8192,
                }
            },
            ["uri"],
        ),
        service.read_resource,
        _OUTPUT,
        read,
    )

    def list_standard_resources():
        return [
            types.Resource(
                uri=item["uri"],
                name=item["name"],
                mimeType=item["mime_type"],
            )
            for item in service.standard_resource_entries()
        ]

    def read_standard_resource(uri: str):
        resolved = service.resolve_resource(uri)
        if resolved is None:
            return None
        content, mime_type = resolved
        return ReadResourceContents(content, mime_type)

    server.register_resource_provider(list_standard_resources, read_standard_resource)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
