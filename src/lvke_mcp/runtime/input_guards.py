"""标识符拒绝的统一收口：非法 ID 是业务阻断，不是系统故障。

``storage.require_safe_id`` 对遍历形状或越界的标识符抛 ``ValueError``，而工具入口的
兜底 ``except Exception`` 会把它包成 ``internal_error`` + ``system_success=False``。
调用方于是看到"服务器坏了"，真实情况却是"你传的 ID 格式不对" —— 既丢了可诊断性，
也谎报了故障归属。运行时探针实测 42 个入口跨 9 个域有此降级。

**为什么不靠解析 ValueError 的消息**：``require_safe_id`` 是在存储层内部调用的，
字段名用的是存储自己的形参名。实测 52 次触发里 **41 次报的都是通用的
``object_id``**，而调用方传的是 ``analysis_task_id`` / ``proposal_id`` /
``url_audit_id``……。据此生成错误码会给出调用方根本没提交过的字段名，等于换了一种
方式误导排查。

因此这里在**派发之前**用同一条 ``_SAFE_ID`` 规则检查入参，自己定位越界字段。好处是
错误码指向调用方真正提交的参数名，且不依赖任何下游异常文案；代价是要显式声明
"哪些入参是标识符"，见 ``_is_identifier_field``。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from lvke_mcp.runtime.storage import _SAFE_ID

# 这些字段名以 _id/_ids 结尾但并非工作区对象标识符：它们有自己的格式规则，
# 交给各工具的 schema 与业务校验处理，不套用 _SAFE_ID。
_NON_OBJECT_ID_FIELDS: frozenset[str] = frozenset({
    "idempotency_key",
    "trace_id",
    "agent_trace_id",
    "tool_call_id",
    "request_id",
})


def _is_identifier_field(name: str, schema: Mapping[str, Any] | None) -> bool:
    """仅当字段既按命名约定是对象 ID、又声明为字符串/字符串数组时才校验。

    命名约定单独不够：``idempotency_key`` 之类允许任意文本。schema 类型单独也不够：
    ``query``、``message`` 都是字符串但不是标识符。
    """

    if name in _NON_OBJECT_ID_FIELDS or name == "workspace_id":
        return False
    if not (name.endswith("_id") or name.endswith("_ids")):
        return False
    if not isinstance(schema, Mapping):
        return True
    declared = schema.get("type")
    if isinstance(declared, list):
        return "string" in declared or "array" in declared
    return declared in (None, "string", "array")


def _offending_values(value: Any) -> Iterable[str]:
    """产出该入参里所有违反 _SAFE_ID 的字符串值。"""

    if isinstance(value, str):
        if not _SAFE_ID.fullmatch(value.strip()):
            yield value
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and not _SAFE_ID.fullmatch(item.strip()):
                yield item


def find_rejected_identifier(
    arguments: Mapping[str, Any],
    input_schema: Mapping[str, Any] | None = None,
) -> str | None:
    """返回第一个格式非法的标识符字段名；全部合法时返回 ``None``。

    workspace_id 优先检查：它决定数据隔离边界，比业务对象 ID 更根本，
    报它比报一个下游字段更有助于定位。
    """

    if not isinstance(arguments, Mapping):
        return None
    properties = {}
    if isinstance(input_schema, Mapping):
        candidate = input_schema.get("properties")
        if isinstance(candidate, Mapping):
            properties = candidate

    workspace = arguments.get("workspace_id")
    if isinstance(workspace, str) and next(_offending_values(workspace), None) is not None:
        return "workspace_id"

    for name in sorted(arguments):
        if not _is_identifier_field(name, properties.get(name)):
            continue
        if next(_offending_values(arguments[name]), None) is not None:
            return name
    return None


def identifier_rejection_payload(field: str, server_name: str) -> dict[str, Any]:
    """构造标识符拒绝的业务信封（``system_success=True``）。"""

    code = f"invalid_{field}"
    return {
        "success": False,
        "business_success": False,
        # 入参不合法是调用方的问题，不是服务器故障。这两个标志决定调用方
        # 是"改参数重试"还是"上报服务端事故"，不能混。
        "system_success": True,
        "transport_success": True,
        "status": "blocked",
        "code": f"{server_name}.{code}",
        "message": f"标识符 {field} 不符合安全格式要求",
        "retryable": False,
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [
            f"修正 {field}：只允许字母、数字、下划线、点和短横线，"
            "首字符必须是字母或数字，长度不超过 128"
        ],
    }
