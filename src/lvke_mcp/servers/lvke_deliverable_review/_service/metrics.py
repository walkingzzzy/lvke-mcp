"""workspace 指标聚合。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lvke_mcp.runtime.storage import require_safe_id, utc_now
from lvke_mcp.servers.lvke_deliverable_review.contracts import DEPLOYMENT_MODES
from lvke_mcp.servers.lvke_deliverable_review.store import STORE

from .base import (
    _blocked,
    _message,
    _metrics_uri,
    _ok,
    _parse_timestamp,
)

from .events import (
    _project_events,
)


def _metric_rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _metric_percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return round(ordered[index], 6)


def _workspace_metrics_payload(
    workspace_id: str,
    *,
    deployment_mode: str = "",
    started_after: str = "",
    started_before: str = "",
) -> dict[str, Any]:
    if deployment_mode and deployment_mode not in DEPLOYMENT_MODES:
        raise ValueError("review_deployment_mode_invalid")
    after = _parse_timestamp(started_after)
    before = _parse_timestamp(started_before)
    if started_after and after is None:
        raise ValueError("metrics_started_after_invalid")
    if started_before and before is None:
        raise ValueError("metrics_started_before_invalid")
    if after and before and after > before:
        raise ValueError("metrics_time_range_invalid")

    states: list[dict[str, Any]] = []
    raw_events: dict[str, list[dict[str, Any]]] = {}
    for review_id in STORE.review_ids(workspace_id):
        try:
            state = _project_events(workspace_id, review_id)
        except ValueError:
            continue
        created = _parse_timestamp(state.get("created_at"))
        if deployment_mode and state.get("deployment_mode") != deployment_mode:
            continue
        if after and (created is None or created < after):
            continue
        if before and (created is None or created > before):
            continue
        states.append(state)
        raw_events[review_id] = STORE.events(workspace_id, review_id)

    completed = [state for state in states if state.get("validation_complete")]
    shadow = [state for state in states if state.get("deployment_mode") == "shadow"]
    completed_shadow = [state for state in shadow if state.get("validation_complete")]
    applicable_rule_count = 0
    executed_rule_count = 0
    high_risk_rule_count = 0
    omitted_high_risk_rule_count = 0
    uncheckable_review_count = 0
    uncheckable_reason_count = 0
    finding_count = 0
    false_positive_appeal_count = 0
    retested_finding_count = 0
    passed_retested_finding_count = 0
    durations: list[float] = []
    known_comparison_count = 0
    disagreement_count = 0

    for state in completed:
        coverage = state.get("coverage") or {}
        applicable = set(coverage.get("applicable_rules") or [])
        executed = set(coverage.get("executed_rules") or [])
        applicable_rule_count += len(applicable)
        executed_rule_count += len(applicable & executed)
        source_severity = {
            str(row.get("rule_id") or ""): str(row.get("severity") or "").upper()
            for row in ((state.get("rule_pack") or {}).get("rule_sources") or [])
            if isinstance(row, dict)
        }
        high_risk = {
            rule_id for rule_id in applicable
            if source_severity.get(str(rule_id)) in {"P0", "P1"}
        }
        high_risk_rule_count += len(high_risk)
        omitted_high_risk_rule_count += len(high_risk - executed)
        reasons = list(state.get("incomplete_reasons") or [])
        if reasons:
            uncheckable_review_count += 1
            uncheckable_reason_count += len(reasons)

        findings = list(state.get("findings") or [])
        finding_count += len(findings)
        for finding in findings:
            appealed = str(finding.get("status") or "") == "false_positive_appeal"
            if not appealed:
                appealed = any(
                    str(history.get("new_status") or "") == "false_positive_appeal"
                    or str(history.get("disposition") or "") in {
                        "reject", "rejected", "false_positive", "false_positive_appeal",
                    }
                    for history in finding.get("history") or []
                    if isinstance(history, dict)
                )
            false_positive_appeal_count += int(appealed)

        events = raw_events.get(str(state.get("review_id") or ""), [])
        running_times = [
            _parse_timestamp((event.get("payload") or {}).get("started_at") or event.get("created_at"))
            for event in events if event.get("event_type") == "review_running"
        ]
        completed_times = [
            _parse_timestamp((event.get("payload") or {}).get("completed_at") or event.get("created_at"))
            for event in events
            if event.get("event_type") in {"review_completed", "review_failed"}
        ]
        starts = [value for value in running_times if value is not None]
        finishes = [value for value in completed_times if value is not None]
        if starts and finishes:
            seconds = (min(finishes) - min(starts)).total_seconds()
            if seconds >= 0:
                durations.append(seconds)
        for event in events:
            if event.get("event_type") != "finding_retested":
                continue
            payload = event.get("payload") or {}
            retested_finding_count += 1
            passed_retested_finding_count += int(payload.get("retest_passed") is True)

    for state in completed_shadow:
        difference = str((state.get("shadow_comparison") or {}).get("validation_difference") or "")
        if difference == "unavailable" or not difference:
            continue
        known_comparison_count += 1
        disagreement_count += int(difference in {
            "legacy_pass_unified_block", "legacy_block_unified_pass",
        })

    shadow_starts = [
        value for value in (_parse_timestamp(state.get("created_at")) for state in shadow)
        if value is not None
    ]
    generated_at = utc_now()
    generated_at_dt = _parse_timestamp(generated_at) or datetime.now(timezone.utc)
    first_shadow_at = min(shadow_starts) if shadow_starts else None
    shadow_elapsed_days = (
        round(max(0.0, (generated_at_dt - first_shadow_at).total_seconds()) / 86400, 6)
        if first_shadow_at else 0.0
    )
    shadow_days = sorted({value.date().isoformat() for value in shadow_starts})
    duration_requirement_met = bool(
        first_shadow_at and shadow_elapsed_days >= 14 and completed_shadow
    )
    high_risk_rate = _metric_rate(
        omitted_high_risk_rule_count,
        high_risk_rule_count,
    )
    warnings: list[str] = []
    if high_risk_rate is None:
        warnings.append("筛选范围内没有声明严重度的适用 P0/P1 规则，遗漏率不可计算")
    if shadow and not duration_requirement_met:
        warnings.append("影子观察期尚未达到连续 14 天，不得据此启用强制门禁")

    indicators = {
        "rule_coverage": {
            "rate": _metric_rate(executed_rule_count, applicable_rule_count),
            "executed_rule_count": executed_rule_count,
            "applicable_rule_count": applicable_rule_count,
            "definition": "已执行适用规则数 / 适用规则数（按审查运行加权）",
        },
        "uncheckable": {
            "rate": _metric_rate(uncheckable_review_count, len(completed)),
            "review_count": uncheckable_review_count,
            "completed_review_count": len(completed),
            "reason_count": uncheckable_reason_count,
            "definition": "存在 incomplete_reasons 的已完成审查数 / 已完成审查数",
        },
        "p0_p1_omission": {
            "rate": high_risk_rate,
            "omitted_rule_count": omitted_high_risk_rule_count,
            "applicable_declared_rule_count": high_risk_rule_count,
            "definition": "未执行的适用 P0/P1 声明规则数 / 适用 P0/P1 声明规则数；不冒充人工金标假阴性率",
        },
        "false_positive_appeal": {
            "rate": _metric_rate(false_positive_appeal_count, finding_count),
            "appealed_finding_count": false_positive_appeal_count,
            "finding_count": finding_count,
            "definition": "曾进入误报申诉流程的 finding 数 / finding 总数",
        },
        "remediation_retest_pass": {
            "rate": _metric_rate(passed_retested_finding_count, retested_finding_count),
            "passed_finding_count": passed_retested_finding_count,
            "retested_finding_count": retested_finding_count,
            "definition": "复测未再复现且规则确已执行的 finding 数 / 已复测 finding 数",
        },
        "review_duration_seconds": {
            "sample_count": len(durations),
            "mean": round(sum(durations) / len(durations), 6) if durations else None,
            "p50": _metric_percentile(durations, 0.50),
            "p95": _metric_percentile(durations, 0.95),
            "max": round(max(durations), 6) if durations else None,
        },
        "shadow_gate_disagreement": {
            "rate": _metric_rate(disagreement_count, known_comparison_count),
            "disagreement_count": disagreement_count,
            "comparable_review_count": known_comparison_count,
            "definition": "旧工程校验与统一自动审查结论不一致的影子审查数 / 可比较影子审查数",
        },
    }
    return {
        "schema_version": "deliverable_review_metrics.v1",
        "workspace_id": workspace_id,
        "generated_at": generated_at,
        "filters": {
            "deployment_mode": deployment_mode or None,
            "started_after": started_after or None,
            "started_before": started_before or None,
        },
        "review_count": len(states),
        "completed_review_count": len(completed),
        "shadow_review_count": len(shadow),
        "completed_shadow_review_count": len(completed_shadow),
        "indicators": indicators,
        "shadow_period": {
            "first_review_at": first_shadow_at.isoformat() if first_shadow_at else None,
            "observed_at": generated_at,
            "elapsed_days": shadow_elapsed_days,
            "distinct_review_days": shadow_days,
            "minimum_required_days": 14,
            "duration_requirement_met": duration_requirement_met,
            "auto_enforcement_allowed": False,
            "governance_decision_eligible": bool(
                duration_requirement_met and completed_shadow and known_comparison_count
            ),
            "recommendation": (
                "eligible_for_governance_decision"
                if duration_requirement_met and completed_shadow and known_comparison_count
                else "continue_shadow"
            ),
        },
        "warnings": warnings,
    }


def workspace_metrics(args: dict[str, Any] | str) -> dict[str, Any]:
    """Shadow-mode exit metrics, deliberately NOT registered as a public tool.

    The metrics payload is already reachable by MCP clients: ``resolve_resource``
    serves ``lvke://deliverable-review/workspaces/{ws}/metrics/current`` through
    ``lvke_read_resource`` (verified end to end). Registering a tool would add
    public surface for data that is already available, so this stays an internal
    entry point whose only extra capability is the optional
    ``deployment_mode`` / ``started_after`` / ``started_before`` filtering.
    """

    if isinstance(args, str):
        args = {"workspace_id": args}
    workspace_id = str(args.get("workspace_id") or "")
    try:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        metrics = _workspace_metrics_payload(
            workspace_id,
            deployment_mode=str(args.get("deployment_mode") or ""),
            started_after=str(args.get("started_after") or ""),
            started_before=str(args.get("started_before") or ""),
        )
    except ValueError as exc:
        return _blocked(str(exc), _message(str(exc)))
    return _ok(
        metrics=metrics,
        resource_uris=[_metrics_uri(workspace_id)],
        warnings=metrics.get("warnings") or [],
        blockers=[],
        next_actions=[],
    )
