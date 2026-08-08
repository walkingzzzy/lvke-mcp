"""仓库根、四个 store、异步复审状态、信封与写入原语、URI 构造。"""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from lvke_mcp.runtime.storage import JSONArtifactStore, require_safe_id, sha256_json
from lvke_mcp.runtime.workspace import workspace_root
from lvke_mcp.servers.lvke_deliverable_review.contracts import require_write_context
from lvke_mcp.servers.lvke_deliverable_review.store import STORE


REPO_ROOT = Path(__file__).resolve().parents[3]


PACKAGE_CONFIG_DIR = REPO_ROOT / "config"


PREPARATION_STORE = JSONArtifactStore(
    "deliverable-review", "preparations", "rvprep", "preparations"
)


EXPORT_STORE = JSONArtifactStore(
    "deliverable-review", "exports", "rvexp", "exports"
)


STANDARD_APPLICABILITY_STORE = JSONArtifactStore(
    "deliverable-review", "standard_applicabilities", "stdapp", "standard-applicabilities"
)


STANDARD_EVIDENCE_STORE = JSONArtifactStore(
    "deliverable-review", "standard_evidence", "stdev", "standard-evidence"
)


_REPORT_ARTIFACT_DOMAINS = {"generic_feasibility", "asset_acquisition"}


_ASYNC_THREADS: dict[tuple[str, str], threading.Thread] = {}


_ASYNC_LOCK = threading.Lock()


def _ok(**data: Any) -> dict[str, Any]:
    status = str(data.pop("status", "ok"))
    business_success = status in {"ok", "accepted"}
    return {
        "success": business_success, "transport_success": True,
        "system_success": True, "business_success": business_success,
        "completed": status == "ok", "outcome": status, "status": status,
        **data, "resource_uris": list(data.get("resource_uris") or []),
        "warnings": list(data.get("warnings") or []),
        "blockers": list(data.get("blockers") or []),
        "next_actions": list(data.get("next_actions") or []),
    }


def _blocked(code: str, message: str, **data: Any) -> dict[str, Any]:
    blockers = list(data.pop("blockers", []) or [code])
    return _ok(status="blocked", code=code, message=message, blockers=blockers, **data)


def _write(operation: str, args: dict[str, Any], callback: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    try:
        workspace_id, _, key = require_write_context(args)
        scoped_operation = operation

        def execute_once() -> dict[str, Any]:
            cached = STORE.idempotent(workspace_id, scoped_operation, key, args)
            if cached is not None:
                return cached
            response = callback(workspace_id)
            STORE.remember(workspace_id, scoped_operation, key, args, response)
            return response

        with STORE.mutation_guard(workspace_id, scoped_operation, key):
            review_id = str(args.get("review_id") or "").strip()
            if review_id:
                with STORE.mutation_guard(
                    workspace_id,
                    "review_mutation",
                    review_id,
                ):
                    return execute_once()
            return execute_once()
    except ValueError as exc:
        code = str(exc)
        return _blocked(code, _message(code))


def _message(code: str) -> str:
    messages = {
        "workspace_id_required": "缺少 workspace_id",
        "idempotency_key_required": "写操作必须提供有效 idempotency_key",
        "idempotency_key_conflict": "同一幂等键已用于不同请求",
        "target_required": "缺少审查目标", "target_type_invalid": "目标类型不受支持",
        "target_id_required": "目标必须包含 target_id", "preparation_not_found": "审查准备对象不存在",
        "review_not_found": "审查运行不存在", "finding_not_found": "finding 不存在",
        "retest_operation_conflict": "复测操作事件与已固化的操作意图冲突",
        "retest_preparation_unavailable": "复测绑定的审查准备对象不可用",
        "retest_child_review_unavailable": "复测子审查不可用",
        "project_type_invalid": "project_type 不受支持",
        "transaction_structure_invalid": "transaction_structure 不受支持",
        "transaction_structure_project_type_mismatch": "交易结构与项目类型不匹配",
        "asset_type_invalid": "asset_type 不受支持",
        "evidence_track_invalid": "evidence_track 不受支持",
        "standard_catalog_invalid": "标准适用性目录不可用",
        "standard_applicability_not_found": "标准适用性对象不存在",
        "standard_requirement_not_found": "标准需求不存在或不适用于当前项目",
        "standard_evidence_resource_invalid": "标准证据必须是当前工作区内受支持的不可变 Resource",
        "standard_evidence_hash_mismatch": "标准证据内容 hash 与不可变 Resource 不一致",
        "standard_evidence_track_mismatch": "标准证据轨与适用性对象不一致",
    }
    return messages.get(code, code.replace("_", " "))


def _safe_file(workspace_id: str, raw: str) -> Path | None:
    try:
        candidate = Path(raw).expanduser().resolve()
        root = workspace_root(require_safe_id(workspace_id, "workspace_id")).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def _review_uri(workspace_id: str, review_id: str) -> str:
    return f"lvke://deliverable-review/workspaces/{workspace_id}/reviews/{review_id}"


def _metrics_uri(workspace_id: str) -> str:
    return f"lvke://deliverable-review/workspaces/{workspace_id}/metrics/current"


def _finding_uri(workspace_id: str, review_id: str, finding_id: str) -> str:
    return f"{_review_uri(workspace_id, review_id)}/findings/{finding_id}"


def _severity(value: Any, *, blocking: bool = False) -> str:
    text = str(value or "").lower()
    if text in {"p0", "critical", "fatal"}:
        return "P0"
    if text in {"p1", "high", "error", "major"} or blocking:
        return "P1"
    if text in {"p3", "low", "info", "minor"}:
        return "P3"
    return "P2"


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _flatten_numbers(value: Any, *, path: str = "", output: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    output = output if output is not None else []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"target_snapshot", "spec_json", "result_snapshot"} and path:
                continue
            _flatten_numbers(item, path=f"{path}.{key}".strip("."), output=output)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _flatten_numbers(item, path=f"{path}[{index}]", output=output)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        output.append({"path": path, "value": float(value)})
    return output


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finding_match_key(row: dict[str, Any]) -> str:
    volatile_keys = {
        "run_id", "target_id", "report_revision_id", "document",
        "workbook", "file_path", "formula",
    }

    def stable_location(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: stable_location(item)
                for key, item in value.items()
                if key not in volatile_keys
            }
        if isinstance(value, list):
            return [stable_location(item) for item in value]
        return value

    location = stable_location(row.get("target_location") or {})
    return sha256_json({
        "rule_id": row.get("rule_id"), "category": row.get("category"),
        "location": location, "source_issue_id": row.get("source_issue_id"),
    })


def _finding_coverage_rule_id(row: dict[str, Any]) -> str:
    explicit = str(row.get("coverage_rule_id") or "")
    if explicit:
        return explicit
    rule_id = str(row.get("rule_id") or "")
    if rule_id in {
        "FIN.XLSX.EMPTY_FORMULA_CACHE",
        "FIN.XLSX.RECALCULATED.ERROR",
    }:
        return "FIN.XLSX.RECALC"
    if rule_id.startswith("FIN.XLSX."):
        return "FIN.XLSX.INTEGRITY"
    return rule_id


def _classify_retest_operations(
    events: list[dict[str, Any]],
    review_id: str,
) -> dict[str, Any]:
    operation_rows: dict[str, list[dict[str, Any]]] = {}
    operation_event_types = {
        "retest_started",
        "retest_prepared",
        "retest_child_started",
        "finding_retested",
        "retest_linked",
        "retest_completed",
        "retest_failed",
    }
    for event in events:
        if event.get("event_type") not in operation_event_types:
            continue
        operation_id = str((event.get("payload") or {}).get("operation_id") or "")
        if operation_id:
            operation_rows.setdefault(operation_id, []).append(event)

    completed: set[str] = set()
    pending: set[str] = set()
    failed: dict[str, dict[str, Any]] = {}
    invalid: dict[str, str] = {}
    for operation_id, rows in operation_rows.items():
        intents = [row for row in rows if row.get("event_type") == "retest_started"]
        failures = [row for row in rows if row.get("event_type") == "retest_failed"]
        completions = [row for row in rows if row.get("event_type") == "retest_completed"]
        links = [row for row in rows if row.get("event_type") == "retest_linked"]
        findings = [row for row in rows if row.get("event_type") == "finding_retested"]
        if failures:
            if len(failures) != 1:
                invalid[operation_id] = "duplicate_failure_event"
            else:
                failed[operation_id] = deepcopy(failures[0].get("payload") or {})
            continue
        if intents:
            if len(intents) != 1:
                invalid[operation_id] = "duplicate_intent_event"
                continue
            if not completions:
                pending.add(operation_id)
                continue
            intent = intents[0].get("payload") or {}
            parent_completions = [
                row for row in completions
                if (row.get("payload") or {}).get("side") == "parent"
            ]
            expected_finding_ids = {
                str(item) for item in intent.get("expected_finding_ids") or []
            }
            actual_finding_ids = [
                str((row.get("payload") or {}).get("finding_id") or "")
                for row in findings
            ]
            valid_link = links[0].get("payload") or {} if len(links) == 1 else {}
            completion_payload = (
                parent_completions[0].get("payload") or {}
                if len(parent_completions) == 1 else {}
            )
            valid = bool(
                len(completions) == 1
                and len(parent_completions) == 1
                and len(links) == 1
                and str(intent.get("parent_review_id") or "") == review_id
                and str(valid_link.get("parent_review_id") or "") == review_id
                and len(actual_finding_ids) == len(expected_finding_ids)
                and set(actual_finding_ids) == expected_finding_ids
                and {
                    str(item)
                    for item in completion_payload.get("expected_finding_ids") or []
                } == expected_finding_ids
                and completion_payload.get("link_hash") == sha256_json(valid_link)
                and completion_payload.get("completed") is True
            )
            if valid:
                completed.add(operation_id)
            else:
                invalid[operation_id] = "incomplete_parent_completion"
            continue
        if not completions:
            pending.add(operation_id)
            continue
        child_completions = [
            row for row in completions
            if (row.get("payload") or {}).get("side") == "child"
        ]
        valid_link = links[0].get("payload") or {} if len(links) == 1 else {}
        completion_payload = (
            child_completions[0].get("payload") or {}
            if len(child_completions) == 1 else {}
        )
        valid = bool(
            len(completions) == 1
            and len(child_completions) == 1
            and len(links) == 1
            and str(valid_link.get("child_review_id") or "") == review_id
            and completion_payload.get("link_hash") == sha256_json(valid_link)
            and completion_payload.get("completed") is True
        )
        if valid:
            completed.add(operation_id)
        else:
            invalid[operation_id] = "incomplete_child_completion"
    return {
        "completed": completed,
        "pending": pending,
        "failed": failed,
        "invalid": invalid,
    }


def _gate_difference(legacy_verdict: str, unified_verdict: str) -> str:
    if legacy_verdict not in {"pass", "fail"} or unified_verdict not in {"pass", "fail"}:
        return "unavailable"
    if legacy_verdict == unified_verdict:
        return f"both_{legacy_verdict}"
    if legacy_verdict == "pass":
        return "legacy_pass_unified_block"
    return "legacy_block_unified_pass"


def _shadow_comparison(state: dict[str, Any], validation_complete: bool) -> dict[str, Any]:
    legacy = state.get("legacy_gate_snapshot") or {}
    automated = str(state.get("automated_gate_verdict") or "unknown")
    current = "pass" if validation_complete and automated == "pass" else (
        "fail" if automated in {"pass", "fail"} else "unknown"
    )
    legacy_validation = str((legacy.get("validation") or {}).get("verdict") or "unknown")
    return {
        "schema_version": "deliverable_review_shadow_comparison.v2",
        "legacy_snapshot_hash": legacy.get("content_hash"),
        "legacy_validation_verdict": legacy_validation,
        "automated_validation_verdict": automated,
        "current_validation_verdict": current,
        "validation_difference": _gate_difference(legacy_validation, current),
    }


def _review_envelope_status(state: dict[str, Any]) -> str:
    """Map the projected review verdict to the public business envelope."""

    if state.get("incomplete_reasons") or state.get("overall_verdict") == "incomplete":
        return "incomplete"
    if (
        state.get("invalidated")
        or state.get("overall_verdict") == "fail"
        or state.get("active_blocking_finding_ids")
        or state.get("active_blocking_finding_ids")
    ):
        return "blocked"
    return "ok"


def _next_actions(state: dict[str, Any]) -> list[str]:
    if state.get("invalidated"):
        return ["目标或审查依据已变化；调用 review_retest 创建新版本审查"]
    if state.get("pending_retest_operation_ids"):
        return ["使用原 idempotency_key 重试 review_retest 以恢复未完成的复测"]
    if state.get("active_failed_retest_operations") or state.get("invalid_retest_operations"):
        return ["排除复测失败原因后，使用新 idempotency_key 重新发起复测"]
    if state.get("incomplete_reasons"):
        return ["补齐无法完成的核查条件后，以新目标版本调用 review_retest"]
    if state.get("active_blocking_finding_ids"):
        return ["处置阻断 findings，完成整改后调用 review_retest"]
    if state.get("deployment_mode") == "shadow":
        return ["影子校验仅记录新旧规则差异；继续采集指标"]
    return []
