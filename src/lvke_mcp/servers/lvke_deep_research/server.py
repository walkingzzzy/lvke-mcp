"""lvke-deep-research MCP server 入口(stdio)。

把 Lvke 的 Deep Research 证据与工件流程封装成 Agent 可调用的 MCP 工具。

设计要点（对齐《Claude_Code应用化开发方案》§6.1 与深度审查 §4.4）：

- 当前 Agent 负责搜索、研究判断与文字；MCP 负责 brief、来源 locator、
  lineage、partial 状态和 research package。``dr_start`` 不配置或调用第二个
  LLM；Agent 完成研究后用 ``dr_submit`` 固化带引用的发现。
- **硬规则（只收紧不放宽）**：
  · 预算耗尽→partial，绝不假 done（引擎已保证，本层不覆盖 status）。
  · 人工采信闸门 research_review 代码级 = False，本 server **不代替人工点头**，
    也不暴露一个名为 confirm、实际上只读的误导性工具。
  · checkpoint 只在真实存在时进资源清单，绝不给死链 URI。
  · 密钥走 env，不进产物。

启动方式::

    python -m lvke_mcp.servers.lvke_deep_research.server
"""

from __future__ import annotations

import json
from typing import Any

from mcp import types

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.runtime.responses import err, ok
from lvke_mcp.servers.lvke_deep_research import package_service

SERVER_NAME = "lvke-deep-research"
SERVER_VERSION = "0.3.0"
logger = get_logger(SERVER_NAME)

# ── 输出契约（envelope 风格同 lvke-finance-tables）─────────────────────────
# 每个工具都返回统一 envelope：status/resource_uris/warnings/blockers/
# next_actions；success/data/source/code/message 保持与既有响应包装兼容。

_ENVELOPE_PROPS: dict[str, Any] = {
    "success": {"type": "boolean"},
    "status": {"type": "string"},
    "resource_uris": {"type": "array", "items": {"type": "string"}},
    "warnings": {"type": "array", "items": {"type": "string"}},
    "blockers": {"type": "array", "items": {"type": "string"}},
    "next_actions": {"type": "array", "items": {"type": "string"}},
    "source": {"type": "string"},
    "code": {"type": "string"},
    "message": {"type": "string"},
    "detail": {},
}
_ENVELOPE_REQUIRED = ("success", "status", "resource_uris", "warnings", "blockers", "next_actions")


def _output_schema(
    extra: dict[str, Any] | None = None,
    *,
    success_requires: list[str] | None = None,
) -> dict[str, Any]:
    """构造单个工具的专属 outputSchema。

    ``success_requires`` 列出成功（success=True）时必须存在的工具特有字段；
    失败响应只需满足 envelope 基线，err 路径不被误伤。
    """

    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": True,
        "properties": {**_ENVELOPE_PROPS, **(extra or {})},
        "required": list(_ENVELOPE_REQUIRED),
    }
    if success_requires:
        schema["if"] = {"properties": {"success": {"const": True}}, "required": ["success"]}
        schema["then"] = {"required": list(success_requires)}
    return schema


_PREPARE_OUTPUT = _output_schema(
    {
        "research_brief": {"type": "object"},
        "plan_items": {"type": "array", "items": {"type": "object"}},
        "budget": {"type": "object"},
        "expected_deliverables": {"type": "array", "items": {"type": "string"}},
    },
    success_requires=["research_brief", "plan_items", "budget", "expected_deliverables"],
)
_START_OUTPUT = _output_schema(
    {
        "data": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string"},
                "profile": {},
                "hint": {"type": "string"},
            },
            "required": ["task_id", "status"],
        },
    },
    success_requires=["data"],
)
_CONTINUE_OUTPUT = _output_schema(
    {
        "task_id": {"type": "string"},
        "continued_from_task_id": {"type": "string"},
        "quality_thresholds_relaxed": {"type": "boolean"},
    },
    success_requires=["task_id", "continued_from_task_id", "quality_thresholds_relaxed"],
)
_STATUS_OUTPUT = _output_schema(
    {
        "data": {
            "type": "object",
            "properties": {
                "task_id": {},
                "status": {"type": "string"},
                "round_no": {},
                "budget": {},
                "quality": {},
                "updated_at": {},
                "is_terminal": {"type": "boolean"},
                "note": {"type": "string"},
            },
            "required": ["status", "is_terminal", "note"],
        },
    },
    success_requires=["data"],
)
_CANCEL_OUTPUT = _output_schema(
    {
        "data": {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "task_id": {},
                "cancel_requested": {"type": "boolean"},
                "reason": {"type": "string"},
            },
        },
    },
    success_requires=["data"],
)
_REPORT_OUTPUT = _output_schema(
    {
        "data": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "report_md": {"type": "string"},
                "citation_audit": {},
                "quality": {},
                "note": {"type": "string"},
            },
            "required": ["task_id", "report_md", "note"],
        },
    },
    success_requires=["data"],
)
_EVIDENCE_OUTPUT = _output_schema(
    {
        "data": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "evidence_graph": {},
                "sources": {},
                "references": {},
            },
            "required": ["task_id"],
        },
    },
    success_requires=["data"],
)
_BUNDLE_OUTPUT = _output_schema(
    {
        "research_package_id": {"type": "string"},
        "task_id": {"type": "string"},
        "basis_hash": {"type": "string"},
        # 各 artifact 名到 Resource URI 的映射（只含真实存在的产物）。
        "resources": {"type": "object", "additionalProperties": {"type": "string"}},
    },
    success_requires=["research_package_id", "task_id", "resources"],
)
_RESOURCE_LIST_OUTPUT = _output_schema(
    {"resources": {"type": "array", "items": {"type": "object"}}},
    success_requires=["resources"],
)
_RESOURCE_READ_OUTPUT = _output_schema(
    {
        "uri": {"type": "string"},
        "mime_type": {"type": "string"},
        "content": {},
    },
    success_requires=["uri", "mime_type", "content"],
)
_PLAN_OUTPUT = _output_schema(
    {
        "task_id": {"type": "string"},
        "plan_revision_id": {"type": "string"},
        "proposal_id": {"type": "string"},
        "basis_hash": {"type": "string"},
        "content_hash": {"type": "string"},
        "plan": {"type": "object"},
        "replayed": {"type": "boolean"},
    }
)
_EVENT_OUTPUT = _output_schema(
    {
        "task_id": {"type": "string"},
        "events": {"type": "array", "items": {"type": "object"}},
        "next_cursor": {"type": ["string", "null"]},
        "has_more": {"type": "boolean"},
    }
)
_CHECKPOINT_OUTPUT = _output_schema(
    {
        "task_id": {"type": "string"},
        "checkpoint_id": {"type": "string"},
        "basis_hash": {"type": "string"},
        "resume_token": {"type": "string"},
        "expires_at": {"type": "string"},
    }
)
_RESUME_OUTPUT = _output_schema(
    {
        "task_id": {"type": "string"},
        "resumed_from_task_id": {"type": "string"},
        "checkpoint_id": {"type": "string"},
        "plan_revision_id": {"type": "string"},
        "plan_basis_hash": {"type": "string"},
        "replayed": {"type": "boolean"},
    }
)

_SOURCE_ALLOWED_USE = {
    "type": "string",
    "enum": [
        "discovery",
        "fact_extraction",
        "evidence_candidate",
        "technical_validation",
        "narrative_context",
        "estimate_preview",
    ],
}


def _source_descriptor(source_type: str, description: str) -> dict[str, Any]:
    evidence_track_schema: dict[str, Any] = (
        {"type": "string", "const": "technical_fixture"}
        if source_type == "technical_fixture"
        else {
            "type": "string",
            "enum": ["real", "technical_fixture", "controlled_assumption"],
        }
    )
    return {
        "type": "object",
        "description": description,
        "additionalProperties": False,
        "properties": {
            "source_type": {"type": "string", "const": source_type},
            "object_id": {"type": "string", "minLength": 1, "maxLength": 160},
            "resource_uri": {"type": "string", "pattern": r"^lvke://.+"},
            "content_hash": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
            "locator": {"type": "string", "minLength": 1, "maxLength": 2000},
            "evidence_track": evidence_track_schema,
            "allowed_uses": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": _SOURCE_ALLOWED_USE,
            },
            "title": {"type": "string", "maxLength": 500},
        },
        "required": [
            "source_type",
            "object_id",
            "resource_uri",
            "content_hash",
            "locator",
            "evidence_track",
            "allowed_uses",
        ],
    }


_MIXED_SOURCE_DESCRIPTOR = {
    "oneOf": [
        _source_descriptor("source_snapshot", "不可变网页或项目文件快照"),
        _source_descriptor("evidence_pack", "已固化证据包"),
        _source_descriptor("archive_chapter", "历史档案章节"),
        _source_descriptor("reviewed_knowledge", "已复核知识发布版"),
        _source_descriptor("policy_record", "政策记录"),
        _source_descriptor("industry_record", "行业记录"),
        _source_descriptor("technical_fixture", "仅限技术金标验证的 fixture"),
    ]
}


def _ok_env(
    data: Any,
    *,
    source: str,
    status: str = "ok",
    resource_uris: list[str] | None = None,
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    next_actions: list[str] | None = None,
) -> dict[str, Any]:
    """成功响应 = 既有 ok() 包装 + envelope 字段。"""

    payload = ok(data, source=source)
    payload.update(
        {
            "status": status,
            "resource_uris": list(resource_uris or []),
            "warnings": list(warnings or []),
            "blockers": list(blockers or []),
            "next_actions": list(next_actions or []),
        }
    )
    return payload


def _err_env(
    code: str,
    message: str,
    *,
    detail: str | None = None,
    status: str = "failed",
    next_actions: list[str] | None = None,
) -> dict[str, Any]:
    """失败/业务阻断响应；blocked 不应被 MCP 传输层标记为工具错误。"""

    payload = err(code, message, detail=detail)
    payload.update(
        {
            "status": status,
            "resource_uris": [],
            "warnings": [],
            "blockers": [code.rsplit(".", 1)[-1]],
            "next_actions": list(next_actions or []),
        }
    )
    return payload


def _ws(args: dict) -> str | None:
    wsid = args.get("workspace_id") or args.get("doc_id") or args.get("project_id")
    if not isinstance(wsid, str) or not wsid.strip():
        return None
    return wsid.strip()


def _tool_dr_start(args: dict) -> dict:
    """Create an Agent-operated research session without a nested LLM."""
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    topic = args.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        return _err_env(f"{SERVER_NAME}.invalid_argument", "topic 必填")
    try:
        result = package_service.start_agent({**args, "workspace_id": wsid, "topic": topic.strip()})
        if result.get("success") is False:
            return result
        return _ok_env(
            {
                "task_id": result.get("task_id"),
                "status": result.get("status"),
                "profile": result.get("profile"),
                "hint": result["hint"],
                "content_fingerprint": result.get("content_fingerprint"),
                "replayed": bool(result.get("replayed")),
                "reused": bool(result.get("reused")),
                "idempotency_expires_at": result.get("idempotency_expires_at"),
            },
            source=f"{SERVER_NAME}.dr_start",
            status=str(result.get("status") or "agent_collecting"),
            resource_uris=[str(result.get("resource_uri") or "")],
            next_actions=["使用数据采集/分析 MCP 整理依据，由 Agent 撰写研究后调用 dr_submit"],
        )
    except Exception:  # noqa: BLE001
        logger.exception("dr_start failed")
        return _err_env(f"{SERVER_NAME}.start_failed", "启动研究失败")


def _tool_dr_status(args: dict) -> dict:
    """轮询研究进度：返回 status/轮次/预算/质量门等结构化状态供 Claude 翻译成人话。"""
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    try:
        agent_task = package_service.agent_status(
            wsid,
            str(args.get("task_id") or ""),
        )
        if agent_task is not None:
            return _ok_env(
                agent_task,
                source=f"{SERVER_NAME}.dr_status",
                status=str(agent_task["status"]),
                warnings=["status=partial 是诚实产出，不得向下游描述为完成"] if agent_task["status"] == "partial" else [],
                next_actions=["调用 dr_get_bundle"] if agent_task["is_terminal"] else ["完成研究后调用 dr_submit"],
            )
        return _err_env(
            f"{SERVER_NAME}.task_not_found",
            "未找到 MCP 自有研究任务",
            status="blocked",
        )
    except Exception:  # noqa: BLE001
        logger.exception("dr_status failed")
        return _err_env(f"{SERVER_NAME}.status_failed", "查询进度失败")


def _tool_dr_cancel(args: dict) -> dict:
    """用户喊停时中止研究。"""
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    try:
        agent_task = package_service.agent_status(
            wsid,
            str(args.get("task_id") or ""),
        )
        if agent_task is not None:
            cancelled = package_service.cancel_agent(
                wsid,
                str(args.get("task_id") or ""),
                reason=str(args.get("reason") or ""),
            )
            if cancelled.get("success") is not True:
                return cancelled
            return _ok_env(
                {
                    "ok": True,
                    "task_id": cancelled.get("task_id"),
                    "cancel_requested": True,
                    "reason": "研究会话已进入 cancelled 终态",
                    "cancelled_at": cancelled.get("cancelled_at"),
                    "replayed": bool(cancelled.get("replayed")),
                },
                source=f"{SERVER_NAME}.dr_cancel",
                status="cancelled",
                resource_uris=list(cancelled.get("resource_uris") or []),
            )
        return _err_env(
            f"{SERVER_NAME}.task_not_found",
            "未找到 MCP 自有研究任务",
            status="blocked",
        )
    except Exception:  # noqa: BLE001
        logger.exception("dr_cancel failed")
        return _err_env(f"{SERVER_NAME}.cancel_failed", "取消研究失败")


def _tool_dr_get_report(args: dict) -> dict:
    """取研究报告正文（Markdown）+ 引用审计结论。仅在任务终态可读。"""
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    task_id = str(args.get("task_id") or "")
    if not task_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "task_id 必填")
    try:
        agent_task = package_service.agent_status(wsid, task_id)
        if agent_task is not None:
            if agent_task.get("status") == "cancelled":
                return _err_env(f"{SERVER_NAME}.task_cancelled", "研究会话已取消", status="blocked")
            bundled = package_service.bundle(wsid, task_id)
            if not bundled.get("success"):
                return _err_env(f"{SERVER_NAME}.report_unavailable", "Agent 尚未提交研究报告；先调用 dr_submit")
            record = package_service.PACKAGE_STORE.get(
                wsid,
                str(bundled.get("research_package_id") or ""),
            )
            artifacts = ((record or {}).get("payload") or {}).get("agent_artifacts") or {}
            return _ok_env({"task_id": task_id, "report_md": artifacts.get("report", ""), "citation_audit": artifacts.get("citation_audit"), "quality": artifacts.get("quality"), "note": "Agent 撰写的 partial 研究；财务数字不得取自本报告。"}, source=f"{SERVER_NAME}.dr_get_report", status="partial", warnings=list(((record or {}).get("payload") or {}).get("limitations") or []))
        return _err_env(
            f"{SERVER_NAME}.task_not_found",
            "未找到 MCP 自有研究任务",
            status="blocked",
        )
    except Exception:  # noqa: BLE001
        logger.exception("dr_get_report failed")
        return _err_env(f"{SERVER_NAME}.get_report_failed", "取报告失败")


def _tool_dr_get_evidence(args: dict) -> dict:
    """取证据图谱 + 来源清单（供研报正文引用与人工采信）。"""
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    task_id = str(args.get("task_id") or "")
    if not task_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "task_id 必填")
    try:
        agent_task = package_service.agent_status(wsid, task_id)
        if agent_task is not None:
            if agent_task.get("status") == "cancelled":
                return _err_env(f"{SERVER_NAME}.task_cancelled", "研究会话已取消", status="blocked")
            bundled = package_service.bundle(wsid, task_id)
            if not bundled.get("success"):
                return _err_env(f"{SERVER_NAME}.evidence_unavailable", "Agent 尚未提交带 locator 的研究依据；先调用 dr_submit")
            record = package_service.PACKAGE_STORE.get(
                wsid,
                str(bundled.get("research_package_id") or ""),
            )
            artifacts = ((record or {}).get("payload") or {}).get("agent_artifacts") or {}
            return _ok_env({"task_id": task_id, "evidence_graph": artifacts.get("evidence"), "sources": artifacts.get("sources"), "references": artifacts.get("sources")}, source=f"{SERVER_NAME}.dr_get_evidence", status="partial")
        return _err_env(
            f"{SERVER_NAME}.task_not_found",
            "未找到 MCP 自有研究任务",
            status="blocked",
        )
    except Exception:  # noqa: BLE001
        logger.exception("dr_get_evidence failed")
        return _err_env(f"{SERVER_NAME}.get_evidence_failed", "取证据失败")


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    _ws_schema = {"type": "string", "description": "工作区 ID（必填）"}
    _task_schema = {"type": "string", "description": "dr_start 返回的 task_id"}
    _basis_schema = {
        "type": "string",
        "pattern": r"^sha256:[0-9a-f]{64}$",
        "description": "dr_get_plan 返回的当前不可变 basis_hash",
    }

    # annotations 如实声明副作用：
    # - 只读：dr_prepare（纯计算）、dr_status/dr_get_report/dr_get_evidence（只读任务产物）
    # - 写入：dr_start（建立 Agent 研究会话）、dr_submit/dr_get_bundle（固化研究包）
    # - 破坏性：dr_cancel（中止后原任务不可恢复运行）
    _read_only = types.ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    _agent_write = types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
    _cancel = types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    )
    _bundle_write = types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    server.register_tool(
        name="dr_prepare",
        description="准备研究 brief、子问题、澄清项、预算和预期交付物；不启动网络消耗。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "topic": {"type": "string", "minLength": 1},
                "industry": {"type": "string"}, "region": {"type": "string"},
                "objective": {"type": "string"},
                "known_materials": {"type": "array", "items": {"type": "string"}},
                "profile": {"type": "string", "enum": ["quick", "deep", "deep_assist", "deep_standard", "deep_max"], "default": "deep_standard"},
                "budget": {"type": "object"},
            },
            "required": ["workspace_id", "topic"],
        },
        handler=package_service.prepare,
        output_schema=_PREPARE_OUTPUT,
        annotations=_read_only,
    )

    server.register_tool(
        name="dr_start",
        description=(
            "建立由当前 Agent 执行的 DR 会话；MCP 不配置或调用内置 LLM。"
            "Agent 用数据采集/分析工具取得依据后，通过 dr_submit 固化研究。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "topic": {"type": "string", "description": "研究主题（必填）"},
                "industry": {"type": "string"},
                "region": {"type": "string"},
                "subqueries": {"type": "array", "items": {"type": "string"}},
                "chapters": {"type": "array", "items": {"type": "string"}},
                "profile": {"type": "string", "enum": ["quick", "deep", "deep_assist", "deep_standard", "deep_max"], "default": "quick"},
                "verify_urls": {"type": "boolean", "default": False},
                "research_brief": {"type": "object"},
                "budget": {"type": "object"},
                "source_policy": {"type": "object"},
                "output_contract": {"type": "object"},
                "analysis_inputs": {"type": "array", "items": {"type": "object"}},
                "plan_items": {"type": "array", "items": {"type": "object"}},
                "idempotency_key": {"type": "string", "description": "同一研究意图幂等键，避免重复烧预算"},
            },
            "required": ["workspace_id", "topic"],
        },
        handler=_tool_dr_start,
        output_schema=_START_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_get_plan",
        description="读取任务的当前或指定不可变 ResearchPlanRevision。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "plan_revision_id": {"type": "string"},
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=lambda args: package_service.get_plan(
            args["workspace_id"],
            args["task_id"],
            plan_revision_id=str(args.get("plan_revision_id") or ""),
        ),
        output_schema=_PLAN_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_propose_plan_revision",
        description="创建研究计划修订提案；提案不会改写当前 revision。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "expected_basis_hash": _basis_schema,
                "changes": {
                    "type": "object",
                    "additionalProperties": False,
                    "minProperties": 1,
                    "properties": {
                        "research_brief": {"type": "object"},
                        "plan_items": {"type": "array", "items": {"type": "object"}},
                        "budget": {"type": "object"},
                        "quality_state": {"type": "object"},
                        "pending_work": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "reason": {"type": "string", "maxLength": 2000},
            },
            "required": ["workspace_id", "task_id", "expected_basis_hash", "changes"],
        },
        handler=package_service.propose_plan_revision,
        output_schema=_PLAN_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_apply_plan_revision",
        description="基于未变更的 basis 应用计划提案，生成新的不可变 revision。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "proposal_id": {"type": "string", "minLength": 1},
                "expected_basis_hash": _basis_schema,
            },
            "required": ["workspace_id", "task_id", "proposal_id", "expected_basis_hash"],
        },
        handler=package_service.apply_plan_revision,
        output_schema=_PLAN_OUTPUT,
        annotations=_bundle_write,
    )
    server.register_tool(
        name="dr_add_sources",
        description="将显式分类、带 hash/locator 的混合来源绑定到新计划 revision。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "expected_basis_hash": _basis_schema,
                "sources": {
                    "type": "array", "minItems": 1, "maxItems": 200,
                    "items": _MIXED_SOURCE_DESCRIPTOR,
                },
            },
            "required": ["workspace_id", "task_id", "expected_basis_hash", "sources"],
        },
        handler=package_service.add_sources,
        output_schema=_PLAN_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_remove_sources",
        description="在新计划 revision 中排除来源并记录原因；不删除原始快照或旧 revision。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "expected_basis_hash": _basis_schema,
                "source_object_ids": {
                    "type": "array", "minItems": 1, "maxItems": 200,
                    "uniqueItems": True, "items": {"type": "string", "minLength": 1},
                },
                "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
            "required": ["workspace_id", "task_id", "expected_basis_hash", "source_object_ids", "reason"],
        },
        handler=package_service.remove_sources,
        output_schema=_PLAN_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_list_events",
        description="按游标读取结构化研究事件；不返回 chain-of-thought 或隐藏推理。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "after_cursor": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=lambda args: package_service.list_events(
            args["workspace_id"],
            args["task_id"],
            after_cursor=str(args.get("after_cursor") or ""),
            limit=int(args.get("limit") or 50),
        ),
        output_schema=_EVENT_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_create_checkpoint",
        description="固化当前计划、预算、来源、质量和待办，返回有期限的签名恢复令牌。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "expected_basis_hash": _basis_schema,
                "reason": {"type": "string", "maxLength": 2000},
                "expires_in_seconds": {"type": "integer", "minimum": 60, "maximum": 604800, "default": 86400},
            },
            "required": ["workspace_id", "task_id", "expected_basis_hash"],
        },
        handler=package_service.create_checkpoint,
        output_schema=_CHECKPOINT_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_resume",
        description="校验签名令牌并从 checkpoint 创建新研究任务；原任务与历史版本不变。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "resume_token": {"type": "string", "pattern": r"^drresume\.v1\..+"},
                "supplemental_questions": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": {"type": "string", "maxLength": 200},
            },
            "required": ["workspace_id", "resume_token"],
        },
        handler=package_service.resume_from_checkpoint,
        output_schema=_RESUME_OUTPUT,
        annotations=_bundle_write,
    )
    server.register_tool(
        name="dr_submit",
        description="将 Agent 撰写、带 source locator 的研究发现固化为 partial research_package_id；不把 Agent 文本冒充独立质量审计完成。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema, "task_id": _task_schema,
                "report_md": {"type": "string", "minLength": 1},
                "citations": {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": True}},
                "evidence_pack_ids": {"type": "array", "items": {"type": "string"}},
                "source_snapshot_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["workspace_id", "task_id", "report_md", "citations"],
        },
        handler=package_service.submit_agent,
        output_schema=_BUNDLE_OUTPUT,
        annotations=_bundle_write,
    )
    server.register_tool(
        name="dr_continue",
        description="从 needs_clarification/partial/blocked checkpoint 开新续研任务；保留 lineage 且不降低质量阈值。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema, "task_id": _task_schema,
                "supplemental_questions": {"type": "array", "items": {"type": "string"}},
                "clarifications": {"type": "object"}, "additional_budget": {"type": "object"},
                "industry": {"type": "string"}, "region": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=package_service.continue_task,
        output_schema=_CONTINUE_OUTPUT,
        annotations=_agent_write,
    )
    server.register_tool(
        name="dr_status",
        description="轮询研究进度：返回 status/轮次/预算/质量门。status=partial 是诚实产出非失败；done 才可采信。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=_tool_dr_status,
        output_schema=_STATUS_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_cancel",
        description="用户喊停时中止研究任务。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
                "reason": {"type": "string", "maxLength": 2000},
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=_tool_dr_cancel,
        output_schema=_CANCEL_OUTPUT,
        annotations=_cancel,
    )
    server.register_tool(
        name="dr_get_report",
        description="取研究报告正文（Markdown）+ 引用审计 + 质量门结论。仅任务终态可读。财务数字不得取自本报告。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=_tool_dr_get_report,
        output_schema=_REPORT_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_get_evidence",
        description="取证据图谱 + 来源清单 + 引用，供研报正文引用与人工采信。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=_tool_dr_get_evidence,
        output_schema=_EVIDENCE_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_get_bundle",
        description=(
            "终态固化 research_package_id，并返回报告、证据、来源、质量与引用审计 Resource URI；"
            "只登记真实存在的 artifact（含 checkpoint），缺失的诚实省略。"
        ),
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "task_id": _task_schema,
            },
            "required": ["workspace_id", "task_id"],
        },
        handler=lambda args: package_service.bundle(args["workspace_id"], args["task_id"]),
        output_schema=_BUNDLE_OUTPUT,
        annotations=_bundle_write,
    )

    server.register_tool(
        name="dr_list_resources",
        description="按工作区列举已固化研究包与 artifact；不会枚举其他工作区元数据。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"workspace_id": _ws_schema},
            "required": ["workspace_id"],
        },
        handler=lambda args: _list_scoped_resources(
            args["workspace_id"]
        ),
        output_schema=_RESOURCE_LIST_OUTPUT,
        annotations=_read_only,
    )
    server.register_tool(
        name="dr_read_resource",
        description="在显式工作区作用域内读取研究包 Resource；跨工作区 URI 统一按不存在处理。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": _ws_schema,
                "uri": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "uri"],
        },
        handler=lambda args: _read_scoped_resource(
            args["workspace_id"], args["uri"]
        ),
        output_schema=_RESOURCE_READ_OUTPUT,
        annotations=_read_only,
    )

    # Protocol-level resources/list and resources/read carry no workspace scope.
    # Keep them empty and force dynamic access through the scoped tools above.
    server.register_resource_provider(lambda: [], lambda _uri: None)
    return server


def _read_scoped_resource(
    workspace_id: str,
    uri: str,
) -> dict[str, Any]:
    resolved = package_service.resolve_resource(uri, workspace_id)
    if resolved is None:
        return _err_env(
            f"{SERVER_NAME}.resource_not_found",
            "资源不存在或不属于当前工作区",
            next_actions=["调用 dr_list_resources 获取当前工作区可读 URI"],
        )
    value, mime = resolved
    payload = _ok_env(
        None,
        source=f"{SERVER_NAME}.dr_read_resource",
        resource_uris=[uri],
    )
    payload.update({"uri": uri, "mime_type": mime, "content": value})
    return payload


def _list_scoped_resources(workspace_id: str) -> dict[str, Any]:
    payload = _ok_env(None, source=f"{SERVER_NAME}.dr_list_resources")
    payload["resources"] = package_service.list_resources(workspace_id)
    return payload


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
