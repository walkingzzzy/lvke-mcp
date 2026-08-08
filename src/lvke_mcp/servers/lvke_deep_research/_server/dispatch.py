"""lvke-deep-research 的 envelope 包装、参数解析与工具 dispatch 包装函数。

这些函数原样搬自 ``server.py``：逻辑、签名与行为均未改动。
"""

from __future__ import annotations

from typing import Any

from lvke_mcp.runtime.responses import err, ok
from lvke_mcp.runtime.storage import paginate_resource_entries
from lvke_mcp.domains.research import application as package_service

from . import SERVER_NAME, logger


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
            # status 此前硬编码为 partial：即使 dr_confirm_quality 已固化 completed
            # 修订，本工具仍报 partial，与 dr_get_bundle 互相矛盾。改为沿用 bundle
            # 选出的同一 package 的真实状态。
            package_status = str(
                bundled.get("status")
                or (record or {}).get("status")
                or "partial"
            )
            note = (
                "Agent 撰写的研究报告；财务数字不得取自本报告。"
                if package_status in {"completed", "done"}
                else "Agent 撰写的 partial 研究；财务数字不得取自本报告。"
            )
            return _ok_env({"task_id": task_id, "report_md": artifacts.get("report", ""), "citation_audit": artifacts.get("citation_audit"), "quality": artifacts.get("quality"), "note": note}, source=f"{SERVER_NAME}.dr_get_report", status=package_status, warnings=list(((record or {}).get("payload") or {}).get("limitations") or []))
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
            # 同 dr_get_report：不再硬编码 partial，沿用同一 package 的真实状态。
            package_status = str(
                bundled.get("status")
                or (record or {}).get("status")
                or "partial"
            )
            return _ok_env({"task_id": task_id, "evidence_graph": artifacts.get("evidence"), "sources": artifacts.get("sources"), "references": artifacts.get("sources")}, source=f"{SERVER_NAME}.dr_get_evidence", status=package_status)
        return _err_env(
            f"{SERVER_NAME}.task_not_found",
            "未找到 MCP 自有研究任务",
            status="blocked",
        )
    except Exception:  # noqa: BLE001
        logger.exception("dr_get_evidence failed")
        return _err_env(f"{SERVER_NAME}.get_evidence_failed", "取证据失败")


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


def _list_scoped_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    payload = _ok_env(None, source=f"{SERVER_NAME}.dr_list_resources")
    entries = package_service.list_resources(workspace_id)
    if resource_type:
        entries = [
            item for item in entries
            if f"/{resource_type.strip('/')}/" in str(item.get("uri") or "")
        ]
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _err_env(
            f"{SERVER_NAME}.{str(exc)}",
            "Resource 分页游标无效或列表已变化",
            status="blocked",
        )
    payload.update(page)
    payload["resource_uris"] = [str(item.get("uri") or "") for item in page["resources"]]
    return payload
