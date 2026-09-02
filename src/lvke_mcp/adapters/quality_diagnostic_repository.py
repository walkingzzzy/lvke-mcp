"""Persistence and shaping for cross-domain ``QualityDiagnostic`` objects.

技术验收阶段的诊断对象：只对“影响数值可信度的冲突”固化，不把普通提示都写成
对象。FinanceRun / FinanceTablesPackage / ReportRevision 三个域共用同一 store，
按 ``target_type`` + ``target_id`` 归属，因此跨存储机制（FinanceRun 走引擎
JSON 文件、表包/报告走 ``JSONArtifactStore``）的诊断能落在同一查询面。
"""

from __future__ import annotations

from typing import Any, Iterable

from lvke_mcp.runtime.storage import JSONArtifactStore, require_safe_id, sha256_json, utc_now

QUALITY_DIAGNOSTIC_STORE = JSONArtifactStore(
    "quality-diagnostics", "diagnostics", "qd", "diagnostics"
)

#: 不确定性的四类统一语义（§3）。
UNCERTAINTY_TYPES = frozenset({"fact", "assumption", "unverified", "conflict"})
#: 置信度三档。
CONFIDENCE_LEVELS = frozenset({"confirmed", "provisional", "unknown"})
#: 影响严重度。
IMPACT_SEVERITIES = frozenset({"material", "moderate", "minor"})
#: 计算继续态。
CALCULATION_STATUSES = frozenset({"continued_with_conflict", "unavailable"})
#: 诊断对象生命周期（不可变，只允许追加新版本或 resolved 关联）。
DIAGNOSTIC_STATUSES = frozenset({"open", "acknowledged", "resolved"})

_TARGET_TYPES = frozenset({
    "finance_run",
    "acquisition_run",
    "finance_tables",
    "report_revision",
})

#: ``target_type`` 到“可绑定上游对象类型”的归类面。同字段名 target_id 在
#: 不同 target_type 下命名空间不同，固化前须显式声名，禁止跨域串读。
_TARGET_KIND_LABEL = {
    "finance_run": "FinanceRun",
    "acquisition_run": "AcquisitionRun",
    "finance_tables": "FinanceTablesPackage",
    "report_revision": "ReportRevision",
}


def validate_uncertainty(uncertainty: dict[str, Any], *, index: int = 0) -> list[str]:
    """Return the validation problems for one structured uncertainty (empty = ok)."""

    errors: list[str] = []
    text = str(uncertainty.get("type") or "").strip()
    if text not in UNCERTAINTY_TYPES:
        errors.append(f"uncertainties[{index}].type 必须是 {sorted(UNCERTAINTY_TYPES)}")
    if not str(uncertainty.get("field") or "").strip():
        errors.append(f"uncertainties[{index}].field 必填")
    confidence = str(uncertainty.get("confidence") or "")
    if confidence and confidence not in CONFIDENCE_LEVELS:
        errors.append(f"uncertainties[{index}].confidence 必须是 {sorted(CONFIDENCE_LEVELS)}")
    impact = uncertainty.get("impact") or {}
    if isinstance(impact, dict) and impact.get("severity") not in IMPACT_SEVERITIES:
        errors.append(f"uncertainties[{index}].impact.severity 必须是 {sorted(IMPACT_SEVERITIES)}")
    if text == "conflict" and not uncertainty.get("competing_values"):
        errors.append(
            f"uncertainties[{index}].competing_values 必须保留冲突双方，禁止只保留选中值"
        )
    if text == "assumption" and not str(uncertainty.get("message") or "").strip():
        errors.append(f"uncertainties[{index}].assumption 必须写明采用原因")
    if text == "unverified" and not str(uncertainty.get("required_action") or "").strip():
        errors.append(f"uncertainties[{index}].unverified 必须写明缺少什么证据")
    if text == "fact" and not uncertainty.get("source_refs"):
        errors.append(f"uncertainties[{index}].fact 必须绑定来源或对象引用")
    return errors


def build_uncertainty(
    uncertainty_type: str,
    field: str,
    *,
    value: Any = None,
    competing_values: Iterable[Any] = (),
    source_refs: Iterable[str] = (),
    confidence: str = "provisional",
    affected_outputs: Iterable[str] = (),
    severity: str = "moderate",
    message: str = "",
    required_action: str = "",
    uncertainty_id: str = "",
) -> dict[str, Any]:
    """Build one structured uncertainty item (schema from plan §3).

    ``conflict`` items must carry ``competing_values``; ordinary items carry the
    adopted ``value`` and any ``source_refs`` for traceability.
    """

    impact = {
        "affected_outputs": sorted({str(item) for item in affected_outputs if str(item)}),
        "severity": severity,
    }
    item: dict[str, Any] = {
        "type": uncertainty_type,
        "field": field,
        "confidence": confidence,
        "impact": impact,
        "message": message,
    }
    if uncertainty_id:
        item["uncertainty_id"] = uncertainty_id
    if value is not None:
        item["value"] = value
    competing = [item for item in competing_values]
    if competing:
        item["competing_values"] = competing
    if source_refs:
        item["source_refs"] = sorted({str(ref) for ref in source_refs if str(ref)})
    if required_action:
        item["required_action"] = required_action
    return item


def conflict_values_from_text(detail: str, *, fallback: Any = None) -> list[Any]:
    """Extract both sides of a human-readable conflict detail.

    Domain checks currently expose conflict details as text (for example
    ``"847.94 vs 1500"``).  A persisted ``conflict`` uncertainty must retain
    both values; this helper keeps that invariant at the boundary while still
    allowing older checks that only supplied a message to be diagnosed.
    """

    import re

    text = str(detail or "")
    values: list[Any] = []
    for token in re.findall(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?", text):
        try:
            number = float(token)
            values.append(int(number) if number.is_integer() else number)
        except ValueError:
            continue
    if len(values) >= 2:
        return values
    if fallback is not None:
        return [fallback, text or "unresolved"]
    return [text or "unresolved", "unresolved"]


def record_quality_diagnostic(
    workspace_id: str,
    *,
    target_type: str,
    target_id: str,
    rule_code: str,
    uncertainties: Iterable[dict[str, Any]] = (),
    affected_outputs: Iterable[str] = (),
    calculation_status: str = "continued_with_conflict",
    recommended_actions: Iterable[str] = (),
    basis_hash: str = "",
    input_snapshot_refs: Iterable[str] = (),
    status: str = "open",
    human_confirmation_required: bool = True,
) -> dict[str, Any]:
    """Persist one immutable QualityDiagnostic for a material data conflict.

    Object IDs are content-addressed from the diagnostic body (target + rule +
    conflicts), so re-recording the same conflict returns the existing record
    instead of duplicating; later remediation creates a new record or a
    ``resolved`` linkage rather than overwriting the original.

    Returns the full store record with ``diagnostic_id`` / ``resource_uri``.
    """

    workspace_id = require_safe_id(str(workspace_id), "workspace_id")
    target_type = str(target_type or "").strip()
    if target_type not in _TARGET_TYPES:
        raise ValueError(
            f"invalid target_type {target_type!r}; must be one of {sorted(_TARGET_TYPES)}"
        )
    target_id = require_safe_id(str(target_id), "target_id")
    rule_code = str(rule_code or "").strip()
    if not rule_code:
        raise ValueError("rule_code is required")
    if calculation_status not in CALCULATION_STATUSES:
        raise ValueError(
            f"invalid calculation_status {calculation_status!r}; "
            f"must be one of {sorted(CALCULATION_STATUSES)}"
        )
    if status not in DIAGNOSTIC_STATUSES:
        raise ValueError(
            f"invalid status {status!r}; must be one of {sorted(DIAGNOSTIC_STATUSES)}"
        )

    uncertainty_items = [dict(item) for item in uncertainties if isinstance(item, dict)]
    problems: list[str] = []
    for index, item in enumerate(uncertainty_items):
        problems.extend(validate_uncertainty(item, index=index))
    if problems:
        raise ValueError("; ".join(problems))

    body = {
        "object_type": "QualityDiagnostic",
        "workspace_id": workspace_id,
        "target_type": target_type,
        "target_kind": _TARGET_KIND_LABEL[target_type],
        "target_id": target_id,
        "rule_code": rule_code,
        "uncertainties": uncertainty_items,
        "input_snapshot_refs": sorted({
            str(ref) for ref in input_snapshot_refs if str(ref)
        }),
        "affected_outputs": sorted({str(item) for item in affected_outputs if str(item)}),
        "calculation_status": calculation_status,
        "recommended_actions": [str(item) for item in recommended_actions if str(item)],
        "human_confirmation_required": bool(human_confirmation_required),
        "created_at": utc_now(),
    }
    if basis_hash:
        body["basis_hash"] = str(basis_hash)

    # Content-addressed identity from the diagnostic body (id-independent fields).
    identity = sha256_json({
        "target_type": target_type,
        "target_id": target_id,
        "rule_code": rule_code,
        "uncertainties": uncertainty_items,
        "input_snapshot_refs": body["input_snapshot_refs"],
        "affected_outputs": body["affected_outputs"],
        "calculation_status": calculation_status,
    })
    object_id = f"qd_{identity.removeprefix('sha256:')[:24]}"
    payload = {**body, "diagnostic_id": object_id, "status": status}
    return QUALITY_DIAGNOSTIC_STORE.put(
        workspace_id,
        payload,
        producer="lvke-mcp.quality-diagnostic",
        status=status,
        source_ids=[target_id],
        basis={
            "target_type": target_type,
            "target_kind": body["target_kind"],
            "target_id": target_id,
            "rule_code": rule_code,
            "identity_hash": identity,
        },
        object_id=object_id,
    )


def diagnostics_for_target(
    workspace_id: str,
    target_id: str,
) -> list[dict[str, Any]]:
    """List QualityDiagnostic records for a target across its storage record ids."""

    workspace_id = require_safe_id(str(workspace_id), "workspace_id")
    return [
        record
        for record in QUALITY_DIAGNOSTIC_STORE.list(workspace_id)
        if str((record.get("payload") or {}).get("target_id") or "") == str(target_id)
    ]
