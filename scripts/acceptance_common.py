"""Shared helpers for G1/G2/G3 live MCP acceptance scripts."""

from __future__ import annotations

import json
from typing import Any

from lvke_mcp.testing.protocol_testkit import (
    initialize_message,
    initialized_notification,
    run_raw,
    tool_call,
)

PROTOCOL = "2025-11-25"


def parse_tool_response(response: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Return (business_payload, protocol_error_code).

    JSON-RPC input/output validation errors (e.g. -32602) are **not** business
    rejections and must not be classified as EXPECTED_REJECTION.
    """
    if isinstance(response.get("error"), dict):
        code = str(response["error"].get("code") or "protocol_error")
        message = str(response["error"].get("message") or "")
        return {
            "success": False,
            "status": "protocol_error",
            "code": code,
            "message": message,
            "trace_id": "",
        }, code
    result = response.get("result") or {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured, None
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                parsed = json.loads(str(item.get("text") or "{}"))
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed, None
    return {}, None


def call_tool(
    module: str,
    name: str,
    arguments: dict[str, Any],
    *,
    timeout: float = 90,
    data_dir: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    responses, _stderr = run_raw(
        module,
        [
            initialize_message(1, PROTOCOL),
            initialized_notification(),
            tool_call(2, name, arguments),
        ],
        timeout=timeout,
        data_dir=data_dir,
    )
    return parse_tool_response(responses[1])


_BUSINESS_REJECTION_SUFFIXES = (
    ".missing_inputs",
    ".blocked",
    ".not_found",
    ".spec_not_found",
    ".fact_pack_not_found",
    ".invalid_tool_output",
)

_BUSINESS_REJECTION_CODES = frozenset({
    "prior_run_not_found",
    "run_creation_failed",
    "FORMAL_ARTIFACT_QUALIFICATION_REQUIRED",
    "EVIDENCE_BINDING_STALE",
    "idempotency_conflict",
})


def classify_outcome(payload: dict[str, Any], *, protocol_error: str | None = None) -> str:
    if protocol_error is not None:
        # JSON-RPC/schema failures are transport/tool-contract failures, never
        # business rejections. Keep the exact code in ``protocol_error`` while
        # using the four release classifications required by acceptance.
        return "UPSTREAM_FAILURE"
    if not payload:
        return "UPSTREAM_FAILURE"
    status = str(payload.get("status") or "").lower()
    code = str(payload.get("code") or "")
    bare = code.split(".")[-1] if code else ""
    system_ok = payload.get("system_success", payload.get("transport_success", True))

    if bare in _BUSINESS_REJECTION_CODES or code in _BUSINESS_REJECTION_CODES:
        return "EXPECTED_REJECTION"
    if any(code.endswith(suffix) for suffix in _BUSINESS_REJECTION_SUFFIXES):
        return "EXPECTED_REJECTION"
    if status == "protocol_error":
        return "UPSTREAM_FAILURE"
    if status == "upstream_failure":
        return "UPSTREAM_FAILURE"
    if payload.get("success") is True or status in {"ok", "partial", "accepted"}:
        return "PASS"
    if status in {"blocked", "missing_inputs", "empty"} and system_ok is not False:
        return "EXPECTED_REJECTION"
    if status == "failed" and code.endswith(".invalid_tool_output"):
        return "UPSTREAM_FAILURE"
    if status in {"incomplete", "failed"} and code:
        return "EXPECTED_REJECTION"
    if not status and not code and payload.get("success") is None:
        return "UPSTREAM_FAILURE"
    return "EXPECTED_REJECTION"


def object_id_from_payload(payload: dict[str, Any]) -> str:
    for key in (
        "run_id",
        "object_id",
        "context_id",
        "spec_id",
        "delivery_run_id",
        "review_id",
        "review_preparation_id",
        "report_preparation_id",
        "report_revision_id",
        "proposal_id",
        "source_file_id",
        "file_id",
        "analysis_id",
        "analysis_task_id",
        "evidence_pack_id",
        "discovery_set_id",
        "package_id",
        "build_scale_case_id",
        "cost_driver_set_id",
        "task_id",
    ):
        value = payload.get(key)
        if value:
            return str(value)
    return ""
