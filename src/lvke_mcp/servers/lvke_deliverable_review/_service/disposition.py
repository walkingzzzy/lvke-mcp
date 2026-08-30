"""finding 处置状态机：开放校验、证据精度与复测关闭判定。"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.adapters.source_files_repository import SourceFileError, resolve_citation_fragment
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

# Report revisions are addressed by their own `sec_*` ids, not by chapter
# number; see `lvke_mcp.domains.reports.read_model`.
_SECTION_ID_RE = re.compile(r"^sec_[a-z0-9][a-z0-9_-]{2,79}$")
_REVISION_ID_PREFIX = "rrv_"


class _EvidenceError(Exception):
    """Carries a stable evidence blocker code back to the caller."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _normalized_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    digest = text.removeprefix("sha256:")
    return f"sha256:{digest}" if re.fullmatch(r"[0-9a-f]{64}", digest) else ""


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


def _resolve_report_revision_evidence(
    workspace_id: str,
    *,
    source_id: str,
    locator: Any,
    source_hash: Any,
    supplied_fragment: Any,
    supplied_fragment_hash: Any,
) -> dict[str, Any]:
    """Bind one evidence row to a section of an immutable report revision.

    Mirrors `_assessment_evidence`'s immutable-object binding rather than
    routing through `resolve_citation_fragment`: a revision's `content_hash`
    is `sha256_json(payload)` over the whole envelope, whereas the citation
    resolver hashes source text, and pushing revision knowledge into the
    source-files adapter would invert the adapter/domain layering.
    """

    from lvke_mcp.adapters.report_repository import REVISION_STORE
    from lvke_mcp.domains.reports.read_model import get_section

    record = REVISION_STORE.get(workspace_id, source_id)
    if not isinstance(record, dict):
        raise _EvidenceError("report_revision_not_found")
    claimed_hash = _normalized_hash(source_hash)
    actual_hash = _normalized_hash(record.get("content_hash"))
    if not claimed_hash or not actual_hash or claimed_hash != actual_hash:
        raise _EvidenceError("report_revision_hash_mismatch")

    section_id = (
        str(locator.get("section_id") or "")
        if isinstance(locator, dict)
        else str(locator or "").removeprefix("section:")
    )
    if not _SECTION_ID_RE.fullmatch(section_id):
        raise _EvidenceError("report_revision_locator_invalid")
    fetched = get_section(workspace_id, source_id, section_id)
    if fetched.get("success") is not True:
        raise _EvidenceError(str(fetched.get("code") or "report_revision_section_not_found"))
    # A section absent from the document body would otherwise bind an empty
    # fragment and rubber-stamp closure.
    if fetched.get("found_in_document") is not True:
        raise _EvidenceError("report_revision_section_not_in_document")
    fragment = str(fetched.get("content") or "")
    if not fragment:
        raise _EvidenceError("report_revision_section_empty")
    fragment_hash = str(fetched.get("content_hash") or "")

    claimed_fragment = str(supplied_fragment or "")
    if claimed_fragment and claimed_fragment.strip() != fragment.strip():
        raise _EvidenceError("report_revision_fragment_mismatch")
    claimed_fragment_hash = _normalized_hash(supplied_fragment_hash)
    if claimed_fragment_hash and claimed_fragment_hash != fragment_hash:
        raise _EvidenceError("report_revision_fragment_hash_mismatch")

    return {
        "source_id": source_id,
        "source_hash": actual_hash,
        "locator": {"section_id": section_id},
        "fragment_text": fragment,
        "fragment_hash": fragment_hash,
        "source_kind": "immutable_object",
        "binding_status": "resolved",
        "semantic_support_status": "agent_or_manual_review_required",
    }


def _resolve_precise_evidence(workspace_id: str, rows: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve disposition evidence against immutable source content."""

    if not isinstance(rows, list) or not rows:
        return [], ["evidence_required"]
    resolved_rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f"evidence_invalid:{index}")
            continue
        source_id = str(row.get("file_id") or row.get("source_id") or "").strip()
        locator = row.get("locator")
        if locator in (None, ""):
            locator = next((row.get(key) for key in ("page", "paragraph", "cell", "range") if row.get(key) not in (None, "")), "")
        source_hash = row.get("source_hash") or row.get("content_hash") or row.get("sha256")
        if not source_id or not locator or not source_hash:
            issues.append(f"evidence_fields_missing:{index}")
            continue
        if source_id.startswith(_REVISION_ID_PREFIX):
            try:
                resolved_rows.append({
                    **dict(row),
                    **_resolve_report_revision_evidence(
                        workspace_id,
                        source_id=source_id,
                        locator=locator,
                        source_hash=source_hash,
                        supplied_fragment=row.get("fragment_text") or row.get("text") or "",
                        supplied_fragment_hash=row.get("fragment_hash") or "",
                    ),
                })
            except _EvidenceError as exc:
                issues.append(f"{exc.code}:{index}")
            except Exception as exc:  # noqa: BLE001 - disposition must fail closed
                issues.append(f"report_revision_resolution_failed:{index}:{type(exc).__name__}")
            continue
        try:
            resolved = resolve_citation_fragment(
                workspace_id,
                source_id=source_id,
                locator=locator,
                source_hash=source_hash,
                supplied_fragment=row.get("fragment_text") or row.get("text") or "",
                supplied_fragment_hash=row.get("fragment_hash") or "",
            )
        except SourceFileError as exc:
            issues.append(f"{exc.detail.get('code', 'citation_error')}:{index}")
            continue
        except (TypeError, ValueError) as exc:
            issues.append(f"citation_locator_invalid:{index}:{type(exc).__name__}")
            continue
        except Exception as exc:  # noqa: BLE001 - disposition must fail closed
            issues.append(f"citation_resolution_failed:{index}:{type(exc).__name__}")
            continue
        resolved_rows.append({
            **dict(row),
            "source_id": resolved["source_id"],
            "source_hash": resolved["source_hash"],
            "locator": resolved["locator"],
            "fragment_text": resolved["fragment_text"],
            "fragment_hash": resolved["fragment_hash"],
            "binding_status": resolved["binding_status"],
        })
    return resolved_rows, sorted(set(issues))


def _evidence_is_precise(rows: Any) -> bool:
    """Compatibility helper; mutation paths use `_resolve_precise_evidence`."""

    return isinstance(rows, list) and bool(rows) and all(
        isinstance(row, dict)
        and bool(str(row.get("file_id") or row.get("source_id") or "").strip())
        and bool(row.get("locator") or row.get("page") or row.get("paragraph") or row.get("cell") or row.get("range"))
        and bool(row.get("source_hash") or row.get("content_hash") or row.get("sha256"))
        for row in rows
    )


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
        resolved_evidence: list[dict[str, Any]] = []
        evidence_issues: list[str] = []
        if new_status in {"false_positive_appeal", "resolved"} or (
            new_status in {"waiver_requested", "waived"}
            and finding.get("severity") == "P1"
            and finding.get("waiver_allowed") is not False
        ):
            resolved_evidence, evidence_issues = _resolve_precise_evidence(workspace_id, evidence)
        # MCP 本地工具:不做角色授权校验。
        if new_status == "false_positive_appeal":
            reason = str(args.get("false_positive_reason") or "").strip()
            if not reason or evidence_issues or not resolved_evidence:
                return _blocked("false_positive_evidence_required", "误报申诉必须提供可解析且带整源/片段 hash 的证据", evidence_blockers=evidence_issues)
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
            if not all(
                str(args.get(key) or "").strip()
                for key in (
                    "waiver_impact",
                    "waiver_compensating_controls",
                    "waiver_responsible_party",
                )
            ):
                return _blocked(
                    "waiver_conditions_required",
                    "P1 豁免必须记录影响、补偿措施和责任人",
                )
            if evidence_issues or not resolved_evidence:
                return _blocked("waiver_evidence_required", "P1 豁免申请必须绑定可解析且带整源/片段 hash 的证据", evidence_blockers=evidence_issues)
        if new_status == "resolved":
            if not str(args.get("closure_basis") or "").strip():
                return _blocked("closure_basis_required", "关闭 finding 必须提供关闭依据")
            if "before_value" not in args or "after_value" not in args:
                return _blocked("before_after_values_required", "关闭 finding 必须记录整改前值与整改后值")
            if evidence_issues or not resolved_evidence:
                return _blocked("closure_evidence_required", "关闭 finding 必须绑定可解析且带整源/片段 hash 的整改证据", evidence_blockers=evidence_issues)
            if finding_blocks(finding):
                retest_review_id = str(args.get("retest_review_id") or "")
                if not _successful_retest_closes_finding(
                    state, finding_id, retest_review_id,
                ):
                    return _blocked("successful_retest_required", "阻断 finding 只能由更新目标版本的成功复测关闭")
        payload = {
            "finding_id": finding_id, "disposition": disposition, "new_status": new_status,
            "note": note, "remediation_evidence": deepcopy(resolved_evidence or evidence),
        }
        for key in (
            "closure_basis", "before_value", "after_value", "false_positive_reason",
            "waiver_scope", "waiver_expires_at", "waiver_invalidation_conditions", "retest_review_id",
            "waiver_impact", "waiver_compensating_controls", "waiver_responsible_party",
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
