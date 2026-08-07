"""lvke-project-planning lifecycle 拆分：候选存取基座。

``_candidate`` / ``_selection`` / ``_put_candidate`` 是所有业务组的公共
基座；``_payload`` 是 record → payload 的通用投影。放在独立模块避免
各业务组互相引用形成环。
"""

from __future__ import annotations

from typing import Any

from lvke_mcp.domains.project_planning import application as service


def _payload(record: dict[str, Any] | None) -> dict[str, Any]:
    return dict((record or {}).get("payload") or {})


def _candidate(
    store: Any,
    workspace_id: str,
    object_id: str,
    object_type: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    record = store.get(workspace_id, object_id)
    payload = _payload(record)
    if record is None or payload.get("object_type") != object_type:
        return None, service._blocked(
            f"{object_type.lower()}_not_found", f"{object_type} 不存在或不属于当前作用域"
        )
    if payload.get("status") not in {"candidate", "calculated"}:
        return None, service._blocked(
            f"{object_type.lower()}_not_candidate", f"{object_type} 不是可确认候选"
        )
    return record, None


def _selection(
    candidate_ids: list[str],
    selected_candidate_id: str,
    rejected_candidate_ids: list[str],
    selection_reason: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    known = set(candidate_ids)
    if selected_candidate_id not in known:
        return None, service._blocked("planning_candidate_not_found", "选定候选不存在")
    if set(rejected_candidate_ids) != known - {selected_candidate_id}:
        return None, service._blocked(
            "planning_rejected_candidates_incomplete", "必须明确列出全部舍弃候选"
        )
    reason = str(selection_reason or "").strip()
    if len(reason) < 10:
        return None, service._blocked(
            "planning_selection_reason_insufficient", "选择理由至少 10 个字符"
        )
    return {
        "selected_candidate_id": selected_candidate_id,
        "rejected_candidate_ids": sorted(rejected_candidate_ids),
        "selection_reason": reason,
        "aggregation": "none",
    }, None


def _put_candidate(
    store: Any,
    workspace_id: str,
    payload: dict[str, Any],
    *,
    producer: str,
    parent_ids: list[str],
    basis: dict[str, Any],
    id_field: str,
) -> dict[str, Any]:
    record = store.put(
        workspace_id,
        payload,
        producer=producer,
        status=str(payload.get("status") or "candidate"),
        source_ids=parent_ids,
        basis=basis,
    )
    return service._envelope(
        success=True,
        status="ok",
        resource_uris=[record["resource_uri"]],
        object_id=record["object_id"],
        **{
            id_field: record["object_id"],
            id_field.removesuffix("_id"): service._planning_view(record, id_field),
        },
        basis_hash=record["basis_hash"],
        content_hash=record["content_hash"],
        idempotent_replay=False,
    )