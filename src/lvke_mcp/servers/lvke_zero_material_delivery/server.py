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
_MISSING_INPUT = {
    "type": "object",
    "properties": {
        "field": {"type": "string"},
        "section": {"type": "string", "description": "所属配置章节 / 小节"},
        "critical": {"type": "boolean", "description": "关键字段未答不得取得正式候选资格"},
        "unit": {"type": "string"},
        "minimum": {"type": "number"},
        "maximum": {"type": "number"},
        "controlled_assumption_source": {"type": "string"},
        "current_seed_value": {},
        "impact": {"type": "string", "description": "对技术验收与正式候选的影响"},
        "status": {"type": "string", "enum": ["pending", "skipped", "answered"]},
        "priority": {"type": "integer", "minimum": 1},
        "required_by_profile": {
            "type": "boolean",
            "description": "false 表示所选配置并未把该字段列为必填；"
            "跳过它只需披露，不阻断正式候选资格",
        },
        # 行业未解析/歧义路径产出的是"待澄清项"形状：只有 field + reason +
        # candidates，没有 critical/status/impact（那三个是字段级假设清单专属）。
        # 这两个键此前不在 properties 里、而四个字段又无条件必填，于是该路径的
        # 响应整条过不了 outputSchema，调用方只拿到 invalid_tool_output：
        # candidates 与 next_actions 全被吞掉，自恢复路径彻底不可见。
        # 实测 23/23 常见行业都走这条路，等于整条入口不可用。
        "reason": {"type": "string", "description": "行业未解析或歧义的具体原因"},
        # candidates 实际是对象数组（`{industry_code, industry_label}`），不是
        # 字符串数组——写成 string 会继续判非法输出。这里按真实载荷声明，并允许
        # 未来扩展键（additionalProperties 默认放开），避免再次因形状假设而阻断。
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "industry_code": {"type": "string"},
                    "industry_label": {"type": "string"},
                },
            },
            "description": "可供调用方选择的行业候选，每项含 industry_code 与 industry_label",
        },
    },
    # 只强制 field。其余按分支各自成立：字段级假设清单带
    # critical/status/impact，待澄清项带 reason/candidates。无条件必填四项会把
    # 错误路径判成非法输出——这正是 outputschema-rejects-error-path 的同类错误。
    "required": ["field"],
}

#: 当前仍处于跳过状态的字段。形状取自 ``lifecycle.confirm_assumptions`` 实际
#: 落库的载荷（field + reason），不是按字段名推断的。
_SKIPPED_FIELD = {
    "type": "object",
    "properties": {
        "field": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["field"],
}

#: 跳过决策的全量历史，只增不减。``resolution`` 区分"仍跳过"与"已补答"——
#: ``skipped_fields`` 只反映当前状态，回答一个此前跳过的字段会把它从那里移除，
#: 于是决策变更本身无处可查。
_SKIP_HISTORY_ENTRY = {
    "type": "object",
    "properties": {
        "field": {"type": "string"},
        "reason": {"type": "string"},
        "resolution": {"type": "string", "enum": ["skipped", "answered"]},
        "skipped_in_run_id": {"type": "string"},
        "skipped_from_assumption_package_id": {"type": "string"},
        "answered_in_assumption_package_id": {"type": "string"},
    },
    "required": ["field"],
}

_REPORT_PROFILE = {
    "type": "object",
    "description": "本次运行冻结的报告配置；历史运行保持可重放",
    "properties": {
        "profile_id": {"type": "string"},
        "template_set_id": {"type": "string"},
        "profile_version": {"type": "string"},
        "profile_content_hash": {"type": "string"},
        "profile_manifest_hash": {"type": "string"},
        "report_type": {"type": "string"},
        "selection_method": {"type": "string"},
        "selection_reasons": {"type": "array", "items": {"type": "string"}},
    },
}

_DOMAIN_RESULT = {
    "type": "object",
    "properties": {
        "domain": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["passed", "passed_with_limitations", "failed", "not_determinable"],
        },
        "blockers": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "checked": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["domain", "status"],
}

# 三段验收严格分离：technical 由系统自动判定，internal 只聚合人工按领域确认，
# formal 是资格而不是动作。任何一段都不得由调用方自报。
_ACCEPTANCE = {
    "type": "object",
    "properties": {
        "technical": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": list(service.TECHNICAL_STATUSES)},
                "review_preparation_id": {"type": "string"},
                "review_id": {"type": "string"},
                "review_package_id": {"type": "string"},
                "feasibility_validation_id": {
                    "type": "string",
                    "description": "已绑定的 fdr_* 可研交付运行；预览阶段恒为空"
                    "（fdr_* 由晋升后的 feasibility_start 创建），空值表示"
                    "当前处于预览阶段，不表示校验被跳过",
                },
                "domain_results": {"type": "array", "items": _DOMAIN_RESULT},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status"],
        },
        "internal": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": list(service.INTERNAL_STATUSES)},
                "review_id": {"type": "string"},
                "domain_confirmations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "dimension": {"type": "string"},
                            "status": {"type": "string"},
                            "role_declaration": {"type": "string"},
                            "review_statement": {"type": "string"},
                            "limitations_accepted": {"type": "array", "items": {"type": "string"}},
                            "dimension_confirmation_id": {"type": "string"},
                            "confirmed_at": {"type": "string"},
                            "identity_or_credential_verified": {
                                "type": "boolean",
                                "description": "恒为 false：责任声明不是身份、资质或电子签名认证",
                            },
                        },
                        "required": ["dimension", "status"],
                    },
                },
                "role_declarations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "各领域提交的责任声明文本；不是已验证身份或签名",
                },
                "latest_confirmation_at": {"type": "string"},
                "missing_dimensions": {"type": "array", "items": {"type": "string"}},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status"],
        },
        "formal": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": list(service.FORMAL_STATUSES)},
                "promotion_id": {"type": "string"},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status"],
        },
    },
    "required": ["technical", "internal", "formal"],
}

_OUTPUT = make_tool_output_schema(
    {
        "delivery_intent": {"type": "object"},
        "delivery_run": {"type": "object"},
        "assumption_package": {"type": "object"},
        "report_profile": _REPORT_PROFILE,
        "missing_inputs": {"type": "array", "items": _MISSING_INPUT},
        "gap_summary": {"type": "object"},
        "acceptance": _ACCEPTANCE,
        "skipped_fields": {"type": "array", "items": _SKIPPED_FIELD},
        "skip_history": {"type": "array", "items": _SKIP_HISTORY_ENTRY},
        "release_limitations": {"type": "array", "items": {"type": "string"}},
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
        "delivery_run": {
            "type": "object",
            "description": "落库原样的不可变记录，内容与其 content_hash 相符可复算；"
            "其中 acceptance 是落库时的快照，实时验收状态读顶层 acceptance",
        },
        "acceptance_source": {
            "type": "string",
            "enum": ["top_level_acceptance_is_current"],
            "description": "指明实时验收状态的读取位置",
        },
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
        "acceptance": _ACCEPTANCE,
        "report_profile": _REPORT_PROFILE,
        "missing_inputs": {"type": "array", "items": _MISSING_INPUT},
        "skipped_fields": {"type": "array", "items": _SKIPPED_FIELD},
        "skip_history": {"type": "array", "items": _SKIP_HISTORY_ENTRY},
        "release_limitations": {"type": "array", "items": {"type": "string"}},
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
        # 分级验收状态属于成功路径硬契约：读得到运行就必然算得出三段状态。
        "acceptance",
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
                "report_profile_id": {
                    "type": "string",
                    "maxLength": 160,
                    "description": "覆盖系统推荐的报告配置；缺省按行业/项目类型确定性推荐",
                },
                "template_set_id": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "按 template_set_id 覆盖报告配置；与 report_profile_id 指向不同配置时阻断",
                },
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
                "skip_fields": {
                    "type": "array",
                    "maxItems": 40,
                    "description": "显式跳过的字段；按受控假设取值并计入交付限制，"
                    "关键字段跳过不得因此取得正式候选资格",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "field": {"type": "string", "minLength": 1, "maxLength": 128},
                            "reason": {"type": "string", "maxLength": 1000},
                        },
                        "required": ["field"],
                    },
                },
                "confirmations": {
                    "type": "array",
                    # 允许"只跳过不确认"：追问阶段用户可以跳过全部非关键项。
                    # minItems=1 会让纯跳过调用在 schema 层就被拒。
                    "minItems": 0,
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
        "delivery_generate_template_pack",
        "根据已确认假设与适用标准需求生成拟定模板包；确定性模板填空，不调用 LLM。"
        "只产出 estimate_preview / technical_preview，两段验收均从 pending 起步。",
        _schema(
            {
                "delivery_run_id": _SAFE_ID,
                "project_type": {
                    "type": "string",
                    "enum": ["generic_feasibility", "asset_acquisition"],
                    "default": "generic_feasibility",
                },
                "report_profile_id": {"type": "string", "maxLength": 160},
                "template_set_id": {"type": "string", "maxLength": 200},
                "confirmed_assumption_package_id": {
                    **_SAFE_ID,
                    "description": "调用方已确认答案快照的乐观并发断言；"
                    "与运行当前绑定不一致时阻断，缺省则沿用运行绑定的那份",
                },
                "idempotency_key": _KEY,
            },
            ["delivery_run_id", "idempotency_key"],
        ),
        service.generate_template_pack,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "delivery_confirm_formal_promotion",
        "确认拟定模板包晋升为 sim_a_formal 证据并导入新可研链资料；不调用 feasibility_release。"
        "技术验收与内部七域验收均通过后才受理；晋升后仍保留 sim_a_template 模拟来源。",
        _schema(
            {
                "template_pack_id": _SAFE_ID,
                "responsible_party": {"type": "string", "minLength": 1, "maxLength": 200},
                "confirmation_note": {"type": "string", "minLength": 1, "maxLength": 2000},
                "idempotency_key": _KEY,
            },
            [
                "template_pack_id",
                "responsible_party",
                "confirmation_note",
                "idempotency_key",
            ],
        ),
        service.confirm_formal_promotion,
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
