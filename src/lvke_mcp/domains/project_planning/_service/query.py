"""lvke-project-planning application 拆分：状态查询与资源表面。

``get_planning_object`` 是唯一跨域读取入口：按对象类型路由到
context/market 的读取函数，其余类型走 Store 映射。资源表面（list /
resolve / read）基于 repository 的 ``RESOURCE_STORES`` 全量枚举。
"""

from __future__ import annotations

import json
from typing import Any

from lvke_mcp.runtime.storage import paginate_resource_entries
from lvke_mcp.adapters.project_planning_repository import (
    BUILD_SCALE_STORE,
    COST_DRIVER_STORE,
    INPUT_APPLICABILITY_STORE,
    LABOR_PLAN_STORE,
    MARKET_CASE_STORE,
    OPTION_COMPARISON_STORE,
    POLICY_BASIS_STORE,
    REVENUE_DRIVER_STORE,
    RESOURCE_STORES as _RESOURCE_STORES,
)

from .base import (
    _applicability_view,
    _blocked,
    _envelope,
    _planning_view,
)
from .context import get_project_context
from .market import get_market_case


def get_planning_object(
    workspace_id: str,
    object_type: str,
    object_id: str,
) -> dict[str, Any]:
    # Keep the legacy ProjectContext and MarketSizingCase response shapes while
    # routing them through the single public planning_get_object entry point.
    if object_type == "ProjectContext":
        return get_project_context(workspace_id, object_id)
    if object_type == "MarketSizingCase":
        return get_market_case(workspace_id, object_id)
    mapping = {
        "InputApplicability": (
            INPUT_APPLICABILITY_STORE,
            "input_applicability_id",
        ),
        "RevenueDriverSet": (REVENUE_DRIVER_STORE, "revenue_driver_set_id"),
        "BuildScaleCase": (BUILD_SCALE_STORE, "build_scale_case_id"),
        "CostDriverSet": (COST_DRIVER_STORE, "cost_driver_set_id"),
        "LaborPlan": (LABOR_PLAN_STORE, "labor_plan_id"),
        "OptionComparison": (OPTION_COMPARISON_STORE, "option_comparison_id"),
        "PolicyBasis": (POLICY_BASIS_STORE, "policy_basis_id"),
    }
    selected = mapping.get(object_type)
    if selected is None:
        return _blocked("planning_object_type_invalid", "未知 planning 对象类型")
    store, id_field = selected
    record = store.get(workspace_id, object_id)
    if record is None:
        return _blocked("planning_object_not_found", "planning 对象不存在或不属于当前作用域")
    view = (
        _applicability_view(record)
        if object_type == "InputApplicability"
        else _planning_view(record, id_field)
    )
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[record["resource_uri"]],
        object_id=object_id,
        object_type=object_type,
        planning_object=view,
        basis_hash=record["basis_hash"],
        content_hash=record["content_hash"],
    )


def list_resources(
    workspace_id: str,
    *,
    resource_type: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    allowed = {kind for _store, kind in _RESOURCE_STORES}
    if resource_type and resource_type not in allowed:
        return _blocked(
            "resource_type_invalid",
            "未知 Resource 类型过滤条件",
        )
    entries: list[dict[str, Any]] = []
    for store, kind in _RESOURCE_STORES:
        if resource_type and kind != resource_type:
            continue
        for record in store.list(workspace_id):
            entries.append(
                {
                    "uri": record["resource_uri"],
                    "name": record["object_id"],
                    "resource_type": kind,
                    "mime_type": "application/json",
                    "created_at": record["created_at"],
                    "content_hash": record["content_hash"],
                }
            )
    try:
        page = paginate_resource_entries(entries, cursor=cursor, limit=limit)
    except ValueError as exc:
        return _blocked(
            str(exc),
            "Resource 分页游标无效或列表已变化",
        )
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[entry["uri"] for entry in page["resources"]],
        resources=page["resources"],
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
        snapshot_hash=page["snapshot_hash"],
    )


def resolve_resource(
    uri: str,
    workspace_id: str,
) -> dict[str, Any] | None:
    expected = f"lvke://project-planning/workspaces/{workspace_id}/"
    if not str(uri).startswith(expected):
        return None
    for store, _kind in _RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None:
            return record
    return None


def read_resource(
    workspace_id: str,
    uri: str,
) -> dict[str, Any]:
    record = resolve_resource(uri, workspace_id)
    if record is None:
        return _blocked(
            "resource_not_found",
            "资源不存在或不属于当前工作区",
        )
    return _envelope(
        success=True,
        status="ok",
        resource_uris=[uri],
        uri=uri,
        mime_type="application/json",
        content=json.dumps(record, ensure_ascii=False, indent=2),
        content_hash=record["content_hash"],
    )