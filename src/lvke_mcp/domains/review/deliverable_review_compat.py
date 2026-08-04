"""Compatibility metadata for legacy engineering validation surfaces."""

from __future__ import annotations

from typing import Any


def full_review_requirement(
    workspace_id: str = "",
    target_type: str = "",
    target_id: str = "",
    *,
    artifact_domain: str = "",
    include_review_id_alias: bool = True,
) -> dict[str, Any]:
    """Return compatibility metadata for a non-authorizing quality check.

    MCP quality checks are diagnostic. They do not represent authentication,
    professional sign-off, or a release permission boundary.
    """

    result: dict[str, Any] = {
        "full_review_required": False,
        "deliverable_review_id": None,
        "deliverable_review_status": "not_required",
        "deliverable_formally_deliverable": True,
    }
    if include_review_id_alias:
        result["review_id"] = None
    if not all((workspace_id, target_type, target_id)):
        return result

    try:
        from lvke_mcp.servers.lvke_deliverable_review import service

        state = service.latest_review_for_target(
            workspace_id,
            target_type,
            target_id,
            artifact_domain=artifact_domain,
        )
    except Exception:  # noqa: BLE001 - compatibility lookup must fail closed
        result["deliverable_review_status"] = "not_required"
        return result
    if not state:
        return result

    review_id = str(state.get("review_id") or "") or None
    result.update({
        "deliverable_review_id": review_id,
        "deliverable_review_status": str(
            state.get("review_status") or "unknown"
        ),
        "deliverable_formally_deliverable": True,
    })
    if include_review_id_alias:
        result["review_id"] = review_id
    return result


__all__ = ["full_review_requirement"]
