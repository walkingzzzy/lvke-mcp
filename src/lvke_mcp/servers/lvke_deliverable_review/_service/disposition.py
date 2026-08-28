"""finding 处置状态机：开放校验、证据精度与复测关闭判定。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.servers.lvke_deliverable_review.contracts import finding_blocks
from lvke_mcp.servers.lvke_deliverable_review.store import STORE

from .base import (
    _blocked,
    _finding_uri,
    _message,
    _next_actions,
    _ok,
    _parse_timestamp,
    _write,
)

from .events import (
    _project,
)


def _require_open_review(
    workspace_id: str,
    review_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        state = _project(workspace_id, review_id)
    except ValueError:
        return None, _blocked("review_not_found", _message("review_not_found"))
    if state.get("invalidated"):
        return None, _blocked("review_invalidated", "目标或审查依据已变化，旧审查不可继续处置", review_id=review_id)
    if not state.get("validation_complete"):
        return None, _blocked("review_not_ready", "校验引擎尚未形成 findings", review_id=review_id)
    return state, None


def _evidence_is_precise(rows: Any) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    for row in rows:
        if not isinstance(row, dict):
            return False
        source_id = str(row.get("file_id") or row.get("source_id") or row.get("url") or "")
        locator = str(
            row.get("locator") or row.get("page") or row.get("paragraph")
            or row.get("cell") or row.get("range") or ""
        )
        content_hash = str(row.get("content_hash") or row.get("sha256") or "")
        if not source_id or not locator or not content_hash:
            return False
    return True


def _target_version_scope(value: dict[str, Any]) -> dict[str, Any]:
    target = value.get("target") if isinstance(value.get("target"), dict) else value
    target_spec = (
        value.get("target_spec")
        if isinstance(value.get("target_spec"), dict)
        else target
    )
    target_type = str(target.get("target_type") or "")
    scope: dict[str, Any] = {"target_type": target_type}
    if target_type == "report_artifact":
        artifact_domain = str(target_spec.get("artifact_domain") or "")
        scope["artifact_domain"] = artifact_domain
    elif target_type == "finance_xlsx":
        # External workbooks have no repository-managed supersession chain;
        # target_id is therefore their caller-owned stable logical identity.
        scope["logical_target_id"] = str(target_spec.get("target_id") or "")
    elif target_type == "finance_xlsx_source":
        # A retest uploads a corrected workbook, so source_file_id necessarily
        # changes; lineage keys on the caller-owned stable logical identity,
        # exactly as finance_xlsx keys on target_id rather than the file path.
        scope["logical_target_id"] = str(target_spec.get("target_id") or "")
    elif target_type == "combined_deliverable":
        component_scopes = [
            _target_version_scope(component)
            for component in (target_spec.get("components") or [])
            if isinstance(component, dict)
        ]
        scope["components"] = sorted(
            component_scopes,
            key=sha256_json,
        )
    return scope


def _retest_target_scope_matches(
    parent: dict[str, Any],
    resolved: dict[str, Any],
) -> bool:
    return _target_version_scope(parent) == _target_version_scope(resolved)


def _successful_retest_closes_finding(
    state: dict[str, Any],
    finding_id: str,
    retest_review_id: str,
) -> bool:
    review_id = str(state.get("review_id") or "")
    return any(
        row.get("completed") is True
        and str(row.get("parent_review_id") or "") == review_id
        and str(row.get("child_review_id") or "") == retest_review_id
        and finding_id in {
            str(item) for item in row.get("closed_finding_ids") or []
        }
        and finding_id not in {
            str(item) for item in row.get("remaining_finding_ids") or []
        }
        for row in state.get("retests") or []
    )


def disposition_finding(args: dict[str, Any]) -> dict[str, Any]:
    def execute(workspace_id: str) -> dict[str, Any]:
        review_id = str(args.get("review_id") or "")
        finding_id = str(args.get("finding_id") or "")
        state, blocked = _require_open_review(
            workspace_id, review_id,
        )
        if blocked is not None or state is None:
            return blocked or _blocked("review_not_found", _message("review_not_found"))
        finding = next((row for row in state["findings"] if row.get("finding_id") == finding_id), None)
        if finding is None:
            return _blocked("finding_not_found", _message("finding_not_found"))
        disposition = str(args.get("disposition") or "").strip()
        aliases = {
            "confirm": "confirmed", "confirmed": "confirmed",
            "remediate": "remediation_in_progress", "remediation_in_progress": "remediation_in_progress",
            "reject": "false_positive_appeal", "rejected": "false_positive_appeal",
            "false_positive": "false_positive_appeal", "false_positive_appeal": "false_positive_appeal",
            "appeal_waiver": "waiver_requested", "compliance_waiver": "waiver_requested",
            "waiver_requested": "waiver_requested",
            "approve_waiver": "waived", "waived": "waived",
            "resolve": "resolved", "resolved": "resolved",
        }
        new_status = aliases.get(disposition)
        if new_status is None:
            return _blocked("disposition_invalid", "disposition 必须为确认、驳回申诉、整改中、误报申诉、合规豁免申请或整改关闭")
        note = str(args.get("note") or "").strip()
        if not note:
            return _blocked("disposition_note_required", "finding 处置必须提供依据说明")
        evidence = args.get("remediation_evidence") or args.get("evidence") or []
        # MCP 本地工具:不做角色授权校验。
        if new_status == "false_positive_appeal":
            reason = str(args.get("false_positive_reason") or "").strip()
            if not reason or not _evidence_is_precise(evidence):
                return _blocked("false_positive_evidence_required", "误报申诉必须提供理由及带哈希和精确定位的证据")
        if new_status in {"waiver_requested", "waived"}:
            if finding.get("severity") == "P0" or finding.get("waiver_allowed") is False:
                return _blocked("p0_waiver_forbidden", "P0 finding 不可豁免")
            if finding.get("severity") != "P1":
                return _blocked("waiver_not_applicable", "合规豁免仅用于规则允许的 P1 finding")
            expiry = _parse_timestamp(args.get("waiver_expires_at"))
            if expiry is None or expiry <= datetime.now(timezone.utc):
                return _blocked("waiver_expiry_required", "P1 豁免必须设置未来有效期")
            if not str(args.get("waiver_scope") or "").strip() or not list(args.get("waiver_invalidation_conditions") or []):
                return _blocked("waiver_scope_required", "P1 豁免必须限定范围并声明失效条件")
            if not _evidence_is_precise(evidence):
                return _blocked("waiver_evidence_required", "P1 豁免申请必须绑定精确证据")
        if new_status == "resolved":
            if not str(args.get("closure_basis") or "").strip():
                return _blocked("closure_basis_required", "关闭 finding 必须提供关闭依据")
            if "before_value" not in args or "after_value" not in args:
                return _blocked("before_after_values_required", "关闭 finding 必须记录整改前值与整改后值")
            if not _evidence_is_precise(evidence):
                return _blocked("closure_evidence_required", "关闭 finding 必须绑定带哈希和精确定位的整改证据")
            if finding_blocks(finding):
                retest_review_id = str(args.get("retest_review_id") or "")
                if not _successful_retest_closes_finding(
                    state, finding_id, retest_review_id,
                ):
                    return _blocked("successful_retest_required", "阻断 finding 只能由更新目标版本的成功复测关闭")
        payload = {
            "finding_id": finding_id, "disposition": disposition, "new_status": new_status,
            "note": note, "remediation_evidence": deepcopy(evidence),
        }
        for key in (
            "closure_basis", "before_value", "after_value", "false_positive_reason",
            "waiver_scope", "waiver_expires_at", "waiver_invalidation_conditions", "retest_review_id",
        ):
            if key in args:
                payload[key] = deepcopy(args.get(key))
        STORE.append(workspace_id, review_id, "finding_disposition_recorded", payload)
        current = _project(workspace_id, review_id, check_freshness=False)
        updated = next(row for row in current["findings"] if row.get("finding_id") == finding_id)
        return _ok(
            review_id=review_id, finding_id=finding_id, finding_status=updated.get("status"),
            review_status=current["review_status"], overall_verdict=current["overall_verdict"],
            validation_status=current["validation_status"],
            validation_complete=current["validation_complete"], blockers=current["blockers"],
            resource_uris=[_finding_uri(workspace_id, review_id, finding_id)],
            next_actions=_next_actions(current),
        )
    return _write("review_disposition_finding", args, execute)
