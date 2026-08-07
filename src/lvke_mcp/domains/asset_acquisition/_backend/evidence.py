"""证据轨判定与绑定：预览/过程验收 spec 识别、重建记录校验与缺口、证据阻断。"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any


from lvke_mcp.domains.finance.spec import validate_for_formal

from .base import (
    _UNTRUSTED_EVIDENCE_ASSERTION_KEYS,
)


def _is_estimate_preview_spec(spec: Mapping[str, Any]) -> bool:
    if str(spec.get("delivery_mode") or "") != "estimate_preview":
        return False
    assumptions = spec.get("controlled_assumptions")
    if not isinstance(assumptions, list) or not assumptions:
        return False
    required = {
        "field", "value", "unit", "basis", "impact", "sensitivity",
        "validation_condition",
    }
    return all(
        isinstance(item, dict)
        and required <= set(item)
        and all(item.get(key) not in (None, "") for key in required)
        for item in assumptions
    )


# P1-018：两组必填键提成常量，让校验、诊断与 server 层输入 schema 共用一份定义。
RECONSTRUCTION_RECORD_FIELDS = (
    "reconstruction_id", "source_uri", "content_hash", "locator", "source_kind",
    "method", "limitations",
)


PROCESS_ACCEPTANCE_BASIS_FIELDS = (
    "field", "value", "source_ref", "locator", "content_hash", "method", "limitation",
)


def _valid_reconstruction_records(value: Any) -> bool:
    records = value if isinstance(value, list) else []
    required = set(RECONSTRUCTION_RECORD_FIELDS)
    return bool(records) and all(
        isinstance(item, Mapping)
        and required <= set(item)
        and all(item.get(field) not in (None, "") for field in required - {"limitations"})
        and isinstance(item.get("limitations"), list)
        and str(item.get("content_hash") or "").startswith("sha256:")
        for item in records
    )


def _valid_process_acceptance_basis(value: Any) -> bool:
    records = value if isinstance(value, list) else []
    required = set(PROCESS_ACCEPTANCE_BASIS_FIELDS)
    return bool(records) and all(
        isinstance(item, Mapping)
        and required <= set(item)
        and all(item.get(field) not in (None, "") for field in required)
        and str(item.get("content_hash") or "").startswith("sha256:")
        for item in records
    )


def _record_gaps(
    value: Any,
    label: str,
    fields: tuple[str, ...],
    *,
    list_fields: tuple[str, ...] = (),
) -> list[str]:
    """Describe why a reconstruction/basis record array fails its contract.

    ``list_fields`` names keys that must be arrays rather than non-empty scalars,
    mirroring how the matching ``_valid_*`` predicate exempts them from the blank
    check.
    """

    if not isinstance(value, list) or not value:
        return [f"{label} 缺失或为空数组，process_acceptance 要求至少一条记录"]
    gaps: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            gaps.append(f"{label}[{index}] 不是对象")
            continue
        missing = [field for field in fields if field not in item]
        if missing:
            gaps.append(f"{label}[{index}] 缺少必填键：{'、'.join(missing)}")
        blank = [
            field for field in fields
            if field not in list_fields and field in item and item.get(field) in (None, "")
        ]
        if blank:
            gaps.append(f"{label}[{index}] 以下键为空值：{'、'.join(blank)}")
        for field in list_fields:
            if field in item and not isinstance(item.get(field), list):
                gaps.append(f"{label}[{index}].{field} 必须是数组")
        content_hash = str(item.get("content_hash") or "")
        if content_hash and not content_hash.startswith("sha256:"):
            gaps.append(f"{label}[{index}].content_hash 必须以 sha256: 开头")
    return gaps


def process_acceptance_gaps(spec: Mapping[str, Any]) -> list[str]:
    """List the unmet ``process_acceptance`` conditions, most actionable first.

    ``_is_process_acceptance_spec`` is exactly ``not process_acceptance_gaps(spec)``,
    so the gate itself is unchanged — this only names what is missing so the caller
    knows which field to supply instead of reading an opaque error code.
    """

    gaps: list[str] = []
    if str(spec.get("confirmation_scope") or "") != "process_acceptance":
        gaps.append("spec.confirmation_scope 必须为 process_acceptance")
    if str(spec.get("evidence_policy") or "") != "source_reconstructed":
        gaps.append(
            "spec.evidence_policy 必须为 source_reconstructed"
            "（process_acceptance 只用于来源重建资料的流程验收）"
        )
    if spec.get("project_fact_certified") is not False:
        gaps.append("spec.project_fact_certified 必须显式为 false，不认证项目事实")
    if str(spec.get("business_decision_status") or "") != "not_selected":
        gaps.append("spec.business_decision_status 必须为 not_selected")
    gaps.extend(_record_gaps(
        spec.get("reconstruction_records"), "reconstruction_records",
        RECONSTRUCTION_RECORD_FIELDS,
        list_fields=("limitations",),
    ))
    gaps.extend(_record_gaps(
        spec.get("process_acceptance_basis"), "process_acceptance_basis",
        PROCESS_ACCEPTANCE_BASIS_FIELDS,
    ))
    return gaps


def _is_process_acceptance_spec(spec: Mapping[str, Any]) -> bool:
    return not process_acceptance_gaps(spec)


def _sanitize_client_evidence_claims(value: Any) -> Any:
    """Remove client-authored integrity facts before hashing or persistence.

    Evidence identifiers and locators remain assertions that the server resolves.
    Pre-built bindings, source hashes, parse attempts and review revisions are
    authority data and are always reconstructed from the workspace source state.
    """

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key == "evidence_bindings" or key in _UNTRUSTED_EVIDENCE_ASSERTION_KEYS:
                continue
            cleaned[str(key)] = _sanitize_client_evidence_claims(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_client_evidence_claims(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_client_evidence_claims(item) for item in value]
    return copy.deepcopy(value)


def _bind_spec_evidence(
    workspace_id: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    from lvke_mcp.domains.finance.evidence_binding import bind_finance_spec_evidence

    return bind_finance_spec_evidence(
        workspace_id,
        spec,
    )


def _evidence_error_strings(binding: dict[str, Any]) -> list[str]:
    rows = [
        *(binding.get("invalid") or []),
        *(binding.get("missing") or []),
        *(binding.get("pending") or []),
    ]
    return [
        "finance_spec.v3 证据绑定未通过: "
        f"{row.get('source_path') or '$'} [{row.get('code') or 'EVIDENCE_BINDING_FAILED'}] "
        f"{row.get('message') or ''}".strip()
        for row in rows
    ]


def _formal_assessment(
    workspace_id: str,
    spec: dict[str, Any],
    *,
    evidence_binding: dict[str, Any] | None = None,
) -> tuple[bool, list[str], dict[str, Any]]:
    schema_ok, schema_errors = validate_for_formal(spec)
    binding = copy.deepcopy(evidence_binding) if evidence_binding is not None else _bind_spec_evidence(
        workspace_id,
        spec,
    )
    errors = [*schema_errors, *_evidence_error_strings(binding)]
    return bool(schema_ok and binding.get("formal_ok") and not errors), errors, binding


def _evidence_blocking_issues(
    binding: dict[str, Any],
    *,
    created_at: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for category in ("invalid", "missing", "pending"):
        for item in binding.get(category) or []:
            issues.append({
                "code": str(item.get("code") or "EVIDENCE_BINDING_FAILED"),
                "blocking": True,
                "status": "open",
                "detail": json.dumps(item, ensure_ascii=False, sort_keys=True),
                "source_path": item.get("source_path") or "$",
                "created_at": created_at,
            })
    if not issues and not binding.get("formal_ok"):
        issues.append({
            "code": "EVIDENCE_BINDING_FAILED",
            "blocking": True,
            "status": "open",
            "detail": "FinanceSpec 未形成可发布的服务端证据绑定",
            "created_at": created_at,
        })
    return issues


def _current_evidence_matches_run(
    workspace_id: str,
    run: dict[str, Any],
    spec: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    current = _bind_spec_evidence(
        workspace_id,
        spec,
    )
    return bool(
        current.get("formal_ok")
        and current.get("binding_hash") == run.get("evidence_binding_hash")
        and current.get("binding_version") == run.get("evidence_binding_version")
    ), current


def sanitize_spec_input(spec: dict[str, Any]) -> dict[str, Any]:
    """Public contract helper returning the server-trusted spec assertion body."""

    cleaned = _sanitize_client_evidence_claims(spec)
    return cleaned if isinstance(cleaned, dict) else {}


def assess_spec_evidence(
    workspace_id: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Resolve a spec's evidence using only workspace-owned server state."""

    return _bind_spec_evidence(
        workspace_id,
        sanitize_spec_input(spec),
    )
