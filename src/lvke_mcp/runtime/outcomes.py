from __future__ import annotations

from typing import Any

from lvke_mcp.runtime.quality_severity import aggregate_quality_status


CANONICAL_STATUSES = frozenset({
    "ok", "accepted", "partial", "empty", "missing_inputs", "blocked",
    "incomplete", "failed", "upstream_failure",
})
SUCCESSFUL_TERMINAL_STATUSES = frozenset({
    "applied", "released", "process_accepted", "completed", "done", "cancelled",
})
ACTIVE_STATUSES = frozenset({"pending", "queued", "running", "started", "processing"})
COMPLETED_STATUSES = frozenset({"ok", "partial", "empty"})
SUCCESSFUL_STATUSES = frozenset({*COMPLETED_STATUSES, "accepted"})


def normalize_operation_outcome(result: dict[str, Any], *, server_name: str) -> dict[str, Any]:
    """Project domain status without conflating operation, quality and viability."""

    payload = dict(result)
    raw_status = str(
        payload.get("status")
        or ("failed" if payload.get("success") is False else "ok")
    ).strip().lower()
    if raw_status in CANONICAL_STATUSES:
        status = raw_status
    elif raw_status in SUCCESSFUL_TERMINAL_STATUSES:
        status = "ok"
        payload.setdefault("domain_status", raw_status)
    elif raw_status in ACTIVE_STATUSES:
        status = "accepted"
        payload.setdefault("task_status", raw_status)
    else:
        status = "blocked" if payload.get("success") is False else "ok"
        payload.setdefault("domain_status", raw_status)

    system_success = bool(payload.get(
        "system_success",
        payload.get("transport_success", status != "failed"),
    )) and status != "failed"
    operation_success = status in SUCCESSFUL_STATUSES
    payload.update({
        "status": status,
        "success": operation_success,
        "business_success": operation_success,
        "system_success": system_success,
        "transport_success": system_success,
        "completed": status in COMPLETED_STATUSES,
        "outcome": status,
    })
    if status == "empty":
        payload.setdefault("result_available", False)
    elif status in {"ok", "partial"}:
        payload.setdefault("result_available", True)
    if not operation_success:
        payload.setdefault("code", f"{server_name}.{status}")
        payload.setdefault("message", {
            "missing_inputs": "缺少完成业务所需输入",
            "blocked": "当前操作无法执行",
            "incomplete": "业务操作尚未完成",
            "failed": "工具执行失败",
            "upstream_failure": "上游服务未能提供结果",
        }.get(status, "业务操作未成功完成"))
    return payload


# ── 技术验收阶段统一诊断信封 ──────────────────────────────────────────────
#
# 所有工具在响应边界统一补齐三态字段（operation_status / diagnostic_available /
# quality_status）与诊断标记（uncertainties / quality_issues / diagnostic_only /
# human_confirmation_required / formal_report_allowed）。
#
# 已有字段释义调整（本阶段）：
#   - success=true 只表示工具执行成功
#   - business_success=true 只表示业务操作完成
#   - ready=true 只表示有诊断结果可继续处理
# 不得再将这些字段解读为“数据质量通过”或“正式研报可发布”。


def apply_diagnostic_envelope(result: dict[str, Any]) -> dict[str, Any]:
    """Layer the technical-acceptance diagnostic envelope onto a tool result.

    Fills only missing fields; explicit values set by domain handlers are
    preserved. ``operation_status`` is ``failed`` only for system failure; any
    business outcome (including ``blocked``) is ``completed``. ``quality_status``
    is aggregated from text quality codes via :func:`aggregate_quality_status`.
    """

    payload = dict(result)
    system_failure = (
        payload.get("system_success") is False
        and str(payload.get("status") or "").strip().lower() == "failed"
    )

    # ── operation_status：工具是否执行完成（failed 仅系统异常）──
    operation_status = "failed" if system_failure else "completed"

    # ── diagnostic_available：是否产生了可供分析的结果 ──
    status_text = str(payload.get("status") or "").strip().lower()
    if system_failure:
        diagnostic_available = False
    elif payload.get("result_available") is False:
        diagnostic_available = False
    else:
        diagnostic_available = bool(
            status_text in {"ok", "partial", "empty"}
            or payload.get("result_available")
            or payload.get("quality_issues")
            or payload.get("uncertainties")
            or payload.get("quality_status")
        )

    # ── quality_status：数据质量结论 ──
    # 收集文本质量码聚合；结构化项（dict/list）与标量值不参与聚合。
    collected_codes: list[str] = []
    for field in ("blockers", "quality_issues", "blocking_issues"):
        items = payload.get(field)
        if not isinstance(items, (list, tuple)):
            continue
        for item in items:
            if isinstance(item, str) and item.strip():
                collected_codes.append(item)
    if system_failure:
        quality_status = "unclassified"
    elif collected_codes:
        quality_status = aggregate_quality_status(collected_codes)
    else:
        quality_status = "pass"

    # ── 写入缺省值（不覆盖工具已显式设置的值）──
    payload.setdefault("operation_status", operation_status)
    payload.setdefault("diagnostic_available", diagnostic_available)
    payload.setdefault("quality_status", quality_status)
    payload.setdefault("uncertainties", [])
    payload.setdefault("quality_issues", [])
    # 当前阶段定位为“数据质量诊断与内部验收平台”，人工确认在线下完成：
    # 所有产物保守标记为 diagnostic_only，formal_report_allowed 恒 False。
    payload.setdefault("diagnostic_only", True)
    payload.setdefault("human_confirmation_required", True)
    payload.setdefault("formal_report_allowed", False)
    return payload