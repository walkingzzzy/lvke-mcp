"""引擎侧研究准备与 lineage 级续研。"""

from __future__ import annotations

from __future__ import annotations

from typing import Any


from lvke_mcp.adapters.research_repository import AGENT_SESSION_STORE

from .base import _failure, _normalize_profile

from .agent_lifecycle import (
    agent_status,
    start_agent,
)


def prepare(args: dict[str, Any]) -> dict[str, Any]:
    topic = str(args.get("topic") or "").strip()
    industry = str(args.get("industry") or "").strip()
    region = str(args.get("region") or "").strip()
    objective = str(args.get("objective") or "形成可追溯的专题研究报告与证据包").strip()
    known_materials = [str(item).strip() for item in (args.get("known_materials") or []) if str(item).strip()]
    clarification = []
    if not region:
        clarification.append("研究地域范围未明确")
    if not industry:
        clarification.append("行业/项目类型未明确")
    questions = [
        f"{topic}的政策与合规约束是什么？",
        f"{topic}的市场规模、供需与竞争格局如何？",
        f"{topic}的建设条件、技术路径与主要风险是什么？",
        f"{topic}有哪些反证、不确定性和数据缺口？",
    ]
    if region:
        questions = [question.replace(f"{topic}", f"{region}{topic}") for question in questions]
    profile = _normalize_profile(str(args.get("profile") or "deep_standard"))
    default_budget = {
        "max_search_calls": 20 if profile == "quick" else 80,
        "max_rounds": 1 if profile == "quick" else 4,
    }
    budget = {**default_budget, **(args.get("budget") or {})}
    return {
        "success": True,
        "status": "partial" if clarification else "ok",
        "research_brief": {
            "topic": topic,
            "objective": objective,
            "scope": {"industry": industry, "region": region},
            "required_dimensions": ["政策", "市场", "技术", "风险", "反证"],
            "source_priorities": ["政府/统计", "监管/标准", "权威行业资料", "多源交叉验证"],
            "open_questions": clarification,
            "known_materials": known_materials,
            "status": "needs_clarification" if clarification else "ready",
        },
        "plan_items": [{"subquery": question, "priority": "normal"} for question in questions],
        "budget": budget,
        "expected_deliverables": ["report", "sources", "evidence", "quality", "citation_audit", "checkpoint"],
        "resource_uris": [],
        "warnings": clarification,
        "blockers": [],
        "next_actions": ["可补充澄清后调用 dr_start；也可接受限制并启动，最终状态可能为 partial"],
    }

def continue_task(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    agent = agent_status(workspace_id, task_id)
    if agent is not None:
        if agent.get("status") == "cancelled":
            return _failure("task_cancelled", "研究会话已取消，不能续研")
        if not agent.get("is_terminal"):
            return _failure("task_not_continuable", "Agent 研究尚未提交；请先完成 dr_submit")
        started = start_agent({
            **args,
            "workspace_id": workspace_id,
            "topic": str(((AGENT_SESSION_STORE.get(workspace_id, task_id) or {}).get("payload") or {}).get("topic") or "补充研究"),
            "continued_from_task_id": task_id,
        })
        return {
            "success": True, "status": started["status"], "task_id": started["task_id"],
            "continued_from_task_id": task_id, "quality_thresholds_relaxed": False,
            "resource_uris": [started["resource_uri"]],
            "warnings": ["新 Agent 研究会话保留原 partial lineage；continue 不会把原结果改写为 done"],
            "blockers": [], "next_actions": ["补充来源后调用 dr_submit"],
        }
    if task_id.startswith("drs_"):
        return _failure("task_not_found", "未找到研究任务")
    # 旧引擎任务仅保留 status/report/evidence/bundle 只读兼容；
    # 任何新研究或续研都必须由 Agent 会话编排。
    return {
        "success": False, "status": "blocked",
        "code": "legacy_task_read_only",
        "message": "旧 Deep Research 任务只读，不再支持续研创建",
        "resource_uris": [], "warnings": [],
        "blockers": ["legacy_task_read_only"],
        "next_actions": ["调用 dr_start 创建新 Agent 编排会话，并在 research_brief 中记录旧 task_id"],
    }
