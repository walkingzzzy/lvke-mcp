"""不可变研究包投影：bundle 固化与 Resource 列举/解析。"""

from __future__ import annotations

from __future__ import annotations

from typing import Any


from lvke_mcp.adapters.research_repository import AGENT_SESSION_STORE, CHECKPOINT_STORE, EVENT_STORE, PACKAGE_STORE, PLAN_PROPOSAL_STORE, PLAN_STORE, QUALITY_REVIEW_STORE

from .base import _failure

from .agent_lifecycle import (
    agent_status,
    select_task_package,
)

from .checkpoints import (
    load_checkpoint,
)


def bundle(workspace_id: str, task_id: str) -> dict[str, Any]:
    agent = agent_status(workspace_id, task_id)
    if agent is not None:
        if agent.get("status") == "cancelled":
            return _failure("task_cancelled", "研究会话已取消，不能生成研究包")
        record = select_task_package(workspace_id, task_id)
        if record is None:
            return _failure("task_not_terminal", "Agent 尚未提交研究发现；先调用 dr_submit")
        payload = record.get("payload") or {}
        base = record["resource_uri"]
        resources = {name: f"{base}/{name}" for name in payload.get("artifact_names") or []}
        package_status = str(record.get("status") or payload.get("status") or "partial")
        return {
            "success": True, "status": package_status, "research_package_id": record["object_id"],
            "task_id": task_id, "basis_hash": record["basis_hash"], "resources": resources,
            "resource_uris": [base, *resources.values()],
            "warnings": list(payload.get("limitations") or []), "blockers": [],
            "next_actions": ([] if package_status in {"completed", "done"} else ["调用 dr_confirm_quality；下游不得将 partial 研究用于正式市场案例"]),
        }
    return _failure("task_not_found", "未找到 MCP 自有研究任务")

def list_resources(workspace_id: str) -> list[dict[str, Any]]:
    """枚举单一工作区的研究包资源。

    MCP ``resources/list`` 没有工作区参数，也没有可供本地 stdio server 使用的
    授权 scope，因此不得在该协议入口枚举动态工作区对象。调用方必须通过显式
    携带 ``workspace_id`` 的 ``dr_list_resources`` 工具进入这里。
    """
    entries: list[dict[str, Any]] = []
    try:
        records = PACKAGE_STORE.list(workspace_id)
    except (OSError, ValueError):
        return []
    for record in records:
        base = str(record.get("resource_uri") or "").rstrip("/")
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        object_id = str(record.get("object_id") or "")
        if not base or not object_id:
            continue
        status = str(record.get("status") or "")
        task_id = str(payload.get("task_id") or "")
        entries.append(
            {
                "uri": base,
                "name": object_id,
                "description": f"研究包快照（status={status}，task_id={task_id}）",
                "mime_type": "application/json",
            }
        )
        for artifact in payload.get("artifact_names") or []:
            name = str(artifact)
            if not name:
                continue
            entries.append(
                {
                    "uri": f"{base}/{name}",
                    "name": f"{object_id}/{name}",
                    "description": f"研究包 {object_id} 的 {name} artifact",
                    "mime_type": "text/markdown" if name == "report" else "application/json",
                }
            )
    object_stores = (
        (QUALITY_REVIEW_STORE, "研究质量确认"),
        (AGENT_SESSION_STORE, "Agent 研究会话"),
        (PLAN_STORE, "研究计划不可变 revision"),
        (PLAN_PROPOSAL_STORE, "研究计划提案"),
        (EVENT_STORE, "研究结构化事件"),
        (CHECKPOINT_STORE, "研究恢复 checkpoint"),
    )
    for store, description in object_stores:
        try:
            records = store.list(workspace_id)
        except (OSError, ValueError):
            continue
        for record in records:
            uri = str(record.get("resource_uri") or "")
            object_id = str(record.get("object_id") or "")
            if uri and object_id:
                entries.append(
                    {
                        "uri": uri,
                        "name": object_id,
                        "description": description,
                        "mime_type": "application/json",
                    }
                )
    return sorted(entries, key=lambda entry: str(entry.get("uri") or ""))

def resolve_resource(
    uri: str,
    workspace_id: str,
) -> tuple[Any, str] | None:
    """Resolve a resource only when its URI belongs to the requested workspace."""

    base_prefix = "lvke://deep-research/workspaces/"
    if not uri.startswith(base_prefix):
        return None
    parts = uri[len(base_prefix) :].split("/")
    if len(parts) not in {3, 4}:
        return None
    if parts[0] != str(workspace_id or "").strip():
        return None
    stores = {
        "packages": PACKAGE_STORE,
        "quality-reviews": QUALITY_REVIEW_STORE,
        "sessions": AGENT_SESSION_STORE,
        "plan-revisions": PLAN_STORE,
        "plan-proposals": PLAN_PROPOSAL_STORE,
        "events": EVENT_STORE,
        "checkpoints": CHECKPOINT_STORE,
    }
    store = stores.get(parts[1])
    if store is None or (len(parts) == 4 and parts[1] != "packages"):
        return None
    try:
        record = store.get(parts[0], parts[2])
    except ValueError:
        return None
    if record is None:
        return None
    if len(parts) == 3:
        return record, "application/json"
    name = parts[3]
    if name not in (record.get("payload") or {}).get("artifact_names", []):
        return None
    agent_artifacts = (record.get("payload") or {}).get("agent_artifacts")
    if isinstance(agent_artifacts, dict):
        value = agent_artifacts.get(name)
        if value is None:
            return None
        return value, "text/markdown" if name == "report" and isinstance(value, str) else "application/json"
    task_id = str((record.get("payload") or {}).get("task_id") or "")
    value = load_checkpoint(parts[0], task_id) if name == "checkpoint" else None
    if value is None:
        return None
    return value, "text/markdown" if name == "report" and isinstance(value, str) else "application/json"
