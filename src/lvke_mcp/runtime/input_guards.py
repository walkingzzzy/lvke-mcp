"""Universal input guards applied at the tool entry layer.

Catches systematic defects that would otherwise degrade to internal_error:
- Identifier rejections from require_safe_id (ValueError)
- Missing required output fields on business error paths
"""

from __future__ import annotations

import functools
import re
from typing import Any, Awaitable, Callable

# Map ValueError messages from require_safe_id to structured error codes
_ID_FIELD_PATTERN = re.compile(r"invalid ([A-Za-z_][A-Za-z0-9_]*)")

# Common identifier field names and their canonical error codes
_CANONICAL_ID_CODES = {
    "workspace_id": "invalid_workspace_id",
    "analysis_task_id": "invalid_analysis_task_id",
    "task_id": "invalid_task_id",
    "delivery_run_id": "invalid_delivery_run_id",
    "run_id": "invalid_run_id",
    "spec_id": "invalid_spec_id",
    "object_id": "invalid_object_id",
    "project_context_id": "invalid_project_context_id",
    "report_revision_id": "invalid_report_revision_id",
    "checkpoint_id": "invalid_checkpoint_id",
    "proposal_id": "invalid_proposal_id",
    "research_package_id": "invalid_research_package_id",
    "before_assessment_id": "invalid_assessment_id",
    "after_assessment_id": "invalid_assessment_id",
    "fact_pack_id": "invalid_fact_pack_id",
    "basis_of_estimate_id": "invalid_basis_of_estimate_id",
    "target_id": "invalid_target_id",
    "finance_tables_package_id": "invalid_finance_tables_package_id",
    "option_comparison_id": "invalid_option_comparison_id",
    "report_preparation_id": "invalid_report_preparation_id",
    "acquisition_tables_package_id": "invalid_acquisition_tables_package_id",
    "discovery_set_id": "invalid_discovery_set_id",
    "url_audit_id": "invalid_url_audit_id",
    "source_snapshot_id": "invalid_source_snapshot_id",
    "visual_capture_id": "invalid_visual_capture_id",
}


def _parse_id_field_from_error(message: str) -> str | None:
    """Extract the field name from ``require_safe_id``'s ``invalid {field}`` message.

    Matching is deliberately exact-and-whitelisted rather than a loose
    ``invalid (\\w+)`` search.  Several unrelated call sites raise messages of
    the same shape -- ``invalid IRR scan range``, ``invalid delivery stage``,
    ``invalid reference_table_schema: ...`` -- and a loose match would relabel a
    genuine computation or state error as a caller-side identifier rejection.
    That is the very failure mode this guard exists to remove, so only messages
    that are exactly ``invalid <known-identifier-field>`` are converted; anything
    else propagates untouched.
    """

    match = _ID_FIELD_PATTERN.fullmatch(message.strip())
    if match is None:
        return None
    field = match.group(1)
    return field if field in _CANONICAL_ID_CODES else None


def _identifier_rejection_envelope(field: str, server_name: str) -> dict[str, Any]:
    """Build a business-level error envelope for identifier rejection."""
    code = _CANONICAL_ID_CODES.get(field, f"invalid_{field}")
    return {
        "success": False,
        "business_success": False,
        "system_success": True,  # Input validation is not a system fault
        "transport_success": True,
        "status": "blocked",
        "code": f"{server_name}.{code}",
        "message": f"标识符 {field} 不符合安全格式要求",
        "retryable": False,
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [f"检查 {field} 格式：仅允许字母数字下划线点横线，1-128字符"],
    }


def guard_identifier_rejections(
    server_name: str,
) -> Callable[[Callable], Callable]:
    """Decorator: catch require_safe_id ValueError and return business error.

    Prevents identifier rejections from leaking as internal_error with
    system_success=False. The storage layer's require_safe_id raises ValueError
    on traversal-shaped or otherwise malformed identifiers; catching at the
    transport boundary converts this to a clean business-level block.
    """

    def decorator(handler: Callable) -> Callable:
        @functools.wraps(handler)
        def sync_wrapper(args: dict[str, Any]) -> dict[str, Any]:
            try:
                return handler(args)
            except ValueError as exc:
                field = _parse_id_field_from_error(str(exc))
                if field:
                    return _identifier_rejection_envelope(field, server_name)
                # Not an identifier rejection; re-raise for the outer handler
                raise

        @functools.wraps(handler)
        async def async_wrapper(args: dict[str, Any]) -> dict[str, Any]:
            try:
                result = handler(args)
                if isinstance(result, Awaitable):
                    result = await result
                return result
            except ValueError as exc:
                field = _parse_id_field_from_error(str(exc))
                if field:
                    return _identifier_rejection_envelope(field, server_name)
                raise

        # Return async wrapper if handler is async, else sync
        import inspect
        if inspect.iscoroutinefunction(handler):
            return async_wrapper
        return sync_wrapper

    return decorator
