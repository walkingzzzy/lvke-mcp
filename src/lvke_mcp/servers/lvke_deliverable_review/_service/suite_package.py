"""Immutable component and lineage validation for research review packages."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    validate_finance_run,
    validate_finance_tables_package,
    validate_formal_record,
    validate_promoted_source_file,
    validate_research_package,
)
from lvke_mcp.runtime.storage import require_safe_id, sha256_json
from lvke_mcp.servers.lvke_deliverable_review.contracts import normalize_target

from .base import REVIEW_PACKAGE_STORE


TargetResolver = Callable[
    [str, dict[str, Any]],
    tuple[dict[str, Any] | None, list[str]],
]


def source_records(workspace_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from lvke_mcp.adapters import source_files_repository as source_files

    state = source_files._load_state(workspace_id)  # noqa: SLF001 - local repository adapter
    return dict(state.get("files") or {}), dict(state.get("analyses") or {})


def verified_source_component(
    workspace_id: str,
    file_id: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    files, analyses = source_records(workspace_id)
    record = files.get(file_id)
    if not isinstance(record, dict):
        return None, ["source_file_not_found"]
    path = Path(str(record.get("path") or ""))
    if not path.is_file():
        return None, ["source_file_content_missing"]
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    recorded_hash = str(record.get("sha256") or "").lower().strip()
    if recorded_hash and not recorded_hash.startswith("sha256:"):
        recorded_hash = f"sha256:{recorded_hash}"
    if digest != recorded_hash:
        return None, ["source_file_hash_mismatch"]
    analysis = analyses.get(file_id) if isinstance(analyses.get(file_id), dict) else {}
    analysis_hash = sha256_json(analysis) if analysis else ""
    return {
        "component_id": file_id,
        "component_type": "source_file",
        "file_id": file_id,
        "filename": str(record.get("original_filename") or path.name),
        "mime_type": str(record.get("declared_mime") or record.get("mime_type") or ""),
        "content_hash": digest,
        "size_bytes": int(record.get("size_bytes") or path.stat().st_size),
        "parse_status": str(record.get("extract_status") or record.get("status") or ""),
        "ocr_status": str(record.get("ocr_status") or (analysis or {}).get("ocr_status") or ""),
        "analysis_hash": analysis_hash,
        "analysis_summary": {
            "parser": str((analysis or {}).get("parser") or ""),
            "locator_count": len((analysis or {}).get("locators") or []),
            "page_count": int((analysis or {}).get("page_count") or 0),
            "degraded_reason": str((analysis or {}).get("degraded_reason") or ""),
        },
    }, []


def internal_component(
    workspace_id: str,
    target: dict[str, Any],
    *,
    resolve_target: TargetResolver,
) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        resolved, blockers = resolve_target(workspace_id, normalize_target(target))
    except (OSError, ValueError) as exc:
        return None, [str(exc)]
    if blockers or not resolved:
        return None, list(blockers or ["internal_target_unavailable"])
    target_type = str(resolved.get("target_type") or "")
    role = {
        "report_revision": "report",
        "report_artifact": "report",
        "research_package": "source_evidence",
        "evidence_pack": "source_evidence",
        "finance_spec": "finance_model",
        "basis_of_estimate": "finance_model",
        "finance_run": "finance_model",
        "acquisition_run": "finance_model",
        "finance_tables_package": "finance_tables",
        "acquisition_tables_package": "finance_tables",
    }.get(target_type, "attachment")
    return {
        "component_id": str(resolved.get("target_id") or ""),
        "component_type": target_type,
        "target_spec": deepcopy(resolved.get("target_spec") or {}),
        "content_hash": str(resolved.get("target_sha256") or ""),
        "suggested_role": role,
        "classification_confidence": 1.0,
        "classification_reasons": ["immutable_object_type"],
    }, []


def _component_formal_lineage(
    workspace_id: str,
    component: dict[str, Any],
    *,
    resolve_target: TargetResolver,
) -> dict[str, Any]:
    component_type = str(component.get("component_type") or "")
    component_id = str(component.get("component_id") or "")
    if component_type == "source_file":
        return validate_promoted_source_file(workspace_id, component_id)

    target_spec = component.get("target_spec")
    if not isinstance(target_spec, dict):
        raise FormalLineageError(
            "formal_lineage_parent_required",
            f"内部套件组件缺少不可变 target_spec: {component_id}",
        )
    resolved, blockers = resolve_target(workspace_id, normalize_target(target_spec))
    if blockers or not resolved:
        raise FormalLineageError(
            blockers[0] if blockers else "formal_lineage_object_not_found",
            f"内部套件组件无法重新解析: {component_id}",
        )
    if (
        str(resolved.get("target_id") or "") != component_id
        or str(resolved.get("target_sha256") or "") != str(component.get("content_hash") or "")
    ):
        raise FormalLineageError(
            "formal_lineage_content_hash_mismatch",
            f"内部套件组件已变化: {component_id}",
        )
    snapshot = resolved.get("snapshot")
    if component_type == "report_revision":
        from lvke_mcp.domains.reports.formal_lineage import validate_report_revision_lineage

        record = (snapshot or {}).get("revision_record") if isinstance(snapshot, dict) else None
        if not isinstance(record, dict):
            raise FormalLineageError("formal_report_revision_invalid", "报告修订记录无效")
        return validate_report_revision_lineage(workspace_id, record)
    if component_type == "research_package":
        return validate_research_package(workspace_id, snapshot or {})
    if component_type in {"evidence_pack", "finance_spec", "basis_of_estimate"}:
        return validate_formal_record(workspace_id, snapshot or {})
    if component_type == "finance_run":
        return validate_finance_run(workspace_id, component_id)
    if component_type == "finance_tables_package":
        return validate_finance_tables_package(workspace_id, snapshot or {})
    raise FormalLineageError(
        "formal_lineage_component_unsupported",
        f"内部正式套件暂不支持该组件类型: {component_type}",
    )


def internal_package_lineage(
    workspace_id: str,
    components: list[dict[str, Any]],
    *,
    resolve_target: TargetResolver,
) -> dict[str, Any]:
    if not components:
        raise FormalLineageError("formal_lineage_parent_required", "内部套件没有正式父对象")
    rows = [
        _component_formal_lineage(
            workspace_id,
            component,
            resolve_target=resolve_target,
        )
        for component in components
    ]
    canonical = rows[0]
    if any(row != canonical for row in rows[1:]):
        raise FormalLineageError(
            "formal_lineage_mixed_promotions",
            "内部套件组件来自不同 FormalPromotion",
        )
    return canonical


def get_package(workspace_id: str, package_id: str) -> dict[str, Any] | None:
    try:
        record = REVIEW_PACKAGE_STORE.get(
            workspace_id,
            require_safe_id(package_id, "review_package_id"),
        )
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
        return None
    if record.get("content_hash") != sha256_json(record["payload"]):
        return None
    if record.get("basis_hash") != sha256_json(record.get("basis")):
        return None
    return record


def package_integrity_reasons(
    workspace_id: str,
    package_record: dict[str, Any],
    *,
    resolve_target: TargetResolver,
) -> list[str]:
    reasons: list[str] = []
    payload = package_record.get("payload") or {}
    components = list(payload.get("components") or [])
    for component in components:
        if component.get("component_type") == "source_file":
            current, current_reasons = verified_source_component(
                workspace_id,
                str(component.get("component_id") or ""),
            )
            reasons.extend(current_reasons)
            if current is not None:
                for field in ("content_hash", "analysis_hash"):
                    if str(current.get(field) or "") != str(component.get(field) or ""):
                        reasons.append(
                            f"review_package_component_changed:"
                            f"{component.get('component_id')}:{field}"
                        )
            continue
        current, current_reasons = internal_component(
            workspace_id,
            component.get("target_spec") if isinstance(component.get("target_spec"), dict) else {},
            resolve_target=resolve_target,
        )
        reasons.extend(current_reasons)
        if current is not None and str(current.get("content_hash") or "") != str(
            component.get("content_hash") or ""
        ):
            reasons.append(
                f"review_package_component_changed:"
                f"{component.get('component_id')}:content_hash"
            )
    if payload.get("review_mode") == "internal":
        try:
            canonical = internal_package_lineage(
                workspace_id,
                components,
                resolve_target=resolve_target,
            )
            stored = {key: payload.get(key) for key in canonical}
            if stored != canonical:
                reasons.append("formal_lineage_metadata_mismatch")
        except FormalLineageError as exc:
            reasons.append(exc.code)
    return sorted(set(reasons))


__all__ = [
    "get_package",
    "internal_component",
    "internal_package_lineage",
    "package_integrity_reasons",
    "source_records",
    "verified_source_component",
]
