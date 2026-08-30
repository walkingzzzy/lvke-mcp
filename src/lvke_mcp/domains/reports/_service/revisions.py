"""全文修订提案：提出、比对与应用。"""

from __future__ import annotations

import hmac
import hashlib
import json
from typing import Any

from lvke_mcp.adapters.report_repository import PREPARATION_STORE, REVISION_STORE
from lvke_mcp.runtime.formal_promotion import FormalLineageError, SIM_A_FORMAL


from .base import (
    _capture_document_snapshot,
    _failure,
    _ok,
    _resolve_revision_record,
    _resolve_target_sections,
    _revision_sections,
    _section_span,
)


def propose(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.domains.reports.doc_service import create_agent_proposal

    workspace_id = str(args["workspace_id"])
    basis = args.get("basis") if isinstance(args.get("basis"), dict) else {}
    preparation_id = str(basis.get("report_preparation_id") or "")
    basis_hash = str(basis.get("basis_hash") or "")
    revision_id = str(basis.get("report_revision_id") or "")
    try:
        preparation = PREPARATION_STORE.get(
            workspace_id,
            preparation_id,
        )
    except ValueError:
        preparation = None
    if preparation is None:
        return _failure("proposal_basis_not_found", "提案依据不存在或不属于当前工作区")
    expected_hash = str(preparation.get("basis_hash") or "")
    if not basis_hash or not hmac.compare_digest(expected_hash, basis_hash):
        return _failure("proposal_basis_hash_mismatch", "提案依据哈希与已固化准备对象不一致")
    try:
        revision, _native_alias = _resolve_revision_record(
            workspace_id,
            revision_id,
        )
    except ValueError:
        revision = None
    revision_payload = (revision or {}).get("payload") if isinstance(revision, dict) else {}
    if (
        not isinstance(revision_payload, dict)
        or str(revision_payload.get("report_preparation_id") or "") != preparation_id
        or not hmac.compare_digest(str(revision_payload.get("basis_hash") or ""), basis_hash)
    ):
        return _failure("proposal_revision_basis_mismatch", "报告修订未绑定到指定准备对象及 basis_hash")
    preparation_payload = (
        (preparation.get("payload") or {})
        if isinstance(preparation.get("payload"), dict)
        else {}
    )
    if str(preparation_payload.get("evidence_policy") or "") == SIM_A_FORMAL:
        from lvke_mcp.domains.reports.formal_lineage import (
            validate_report_preparation_lineage,
            validate_report_revision_lineage,
        )

        try:
            validate_report_preparation_lineage(workspace_id, preparation)
            validate_report_revision_lineage(workspace_id, revision)
        except FormalLineageError as exc:
            return _failure(exc.code, f"报告提案前正式 promotion 谱系无效：{exc.message}")
    verified_basis = {
        "report_preparation_id": preparation_id,
        "basis_hash": basis_hash,
        "report_revision_id": revision_id,
        "outline": list(preparation_payload.get("outline") or []),
    }
    if basis.get("patch_scope") == "section":
        for key in (
            "patch_scope", "section_id", "base_section_content_hash",
            "merged_document_hash", "upstream_refs", "citation_locators",
            "upstream_basis_hashes",
        ):
            value = basis.get(key)
            verified_basis[key] = (
                list(value or []) if key in {"upstream_refs", "citation_locators"}
                else dict(value or {}) if key == "upstream_basis_hashes"
                else str(value or "")
            )
        if not all(verified_basis[key] for key in (
            "section_id", "base_section_content_hash", "merged_document_hash",
        )):
            return _failure("section_patch_basis_invalid", "单章提案缺少稳定章节绑定")
        proposed_hash = "sha256:" + hashlib.sha256(
            str(args["proposed_content"]).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(verified_basis["merged_document_hash"], proposed_hash):
            return _failure("section_patch_hash_mismatch", "合并后全文哈希与单章提案 basis 不一致")
    requested_sections = list(args.get("target_sections") or [])
    outline = verified_basis["outline"]
    canonical_sections, section_mappings, target_error = _resolve_target_sections(
        preparation_payload,
        requested_sections,
    )
    if outline and target_error:
        return _failure(
            target_error,
            (
                "提案目标章节重复"
                if target_error == "proposal_target_duplicate"
                else "提案目标章节不属于 preparation 固化的自定义 outline"
            ),
        )
    if outline:
        requested_sections = canonical_sections
        verified_basis["target_sections"] = section_mappings
    result = create_agent_proposal(
        workspace_id,
        session_id="mcp-local-agent",
        summary=str(args["summary"]),
        proposed_content=str(args["proposed_content"]),
        target_sections=requested_sections,
        basis=json.dumps(verified_basis, ensure_ascii=False, sort_keys=True),
        expected_outline=outline,
    )
    return _ok({"proposal_id": result.get("id"), "structure_ok": result.get("structure_ok"), "structure_issues": result.get("structure_issues")}, "提案已创建，必须先 diff 后 apply")


def diff(
    workspace_id: str,
    proposal_id: str,
) -> dict[str, Any]:
    from lvke_mcp.domains.reports import doc_service as doc

    try:
        result = doc.diff_agent_proposal(workspace_id, proposal_id=proposal_id)
    except doc.DocServiceError as exc:
        return _failure(exc.code, exc.message)
    return _ok(result, "核对差异后才可 apply")


def apply(
    workspace_id: str,
    proposal_id: str,
    *,
    enforce_structure: bool = True,
) -> dict[str, Any]:
    from lvke_mcp.domains.reports import doc_service as doc

    try:
        proposal = doc._read_proposal(workspace_id, proposal_id)  # noqa: SLF001
    except doc.DocServiceError as exc:
        return _failure(exc.code, exc.message)
    try:
        basis = json.loads(str(proposal.get("basis") or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("proposal_basis_invalid", "提案缺少可验证的结构化 basis")
    if not isinstance(basis, dict):
        return _failure("proposal_basis_invalid", "提案 basis 必须是结构化对象")
    preparation_id = str(basis.get("report_preparation_id") or "")
    revision_id = str(basis.get("report_revision_id") or "")
    basis_hash = str(basis.get("basis_hash") or "")
    try:
        preparation = PREPARATION_STORE.get(
            workspace_id,
            preparation_id,
        )
    except ValueError:
        preparation = None
    revision, native_alias = _resolve_revision_record(
        workspace_id,
        revision_id,
    )
    revision_payload = (revision or {}).get("payload") if isinstance(revision, dict) else {}
    preparation_payload = (preparation or {}).get("payload")
    expected_outline = list(
        (preparation_payload if isinstance(preparation_payload, dict) else {}).get("outline")
        or []
    )
    if (
        preparation is None
        or not hmac.compare_digest(str(preparation.get("basis_hash") or ""), basis_hash)
        or not isinstance(revision_payload, dict)
        or str(revision_payload.get("report_preparation_id") or "") != preparation_id
        or not hmac.compare_digest(str(revision_payload.get("basis_hash") or ""), basis_hash)
        or str(revision_payload.get("native_revision_id") or "")
        != str(proposal.get("base_revision_id") or "")
        or list(proposal.get("expected_outline") or []) != expected_outline
    ):
        return _failure(
            "proposal_basis_stale_or_mismatch",
            "提案的 preparation、basis_hash、revision 或 outline 绑定已失效",
        )
    if str((preparation_payload or {}).get("evidence_policy") or "") == SIM_A_FORMAL:
        from lvke_mcp.domains.reports.formal_lineage import (
            validate_report_preparation_lineage,
            validate_report_revision_lineage,
        )

        try:
            validate_report_preparation_lineage(workspace_id, preparation)
            validate_report_revision_lineage(workspace_id, revision)
        except FormalLineageError as exc:
            return _failure(exc.code, f"报告应用前正式 promotion 谱系无效：{exc.message}")

    if basis.get("patch_scope") == "section":
        section_id = str(basis.get("section_id") or "")
        descriptor = next(
            (item for item in _revision_sections(revision or {}) if item.get("section_id") == section_id),
            None,
        )
        snapshot = (revision_payload.get("document_snapshot") or {}) if isinstance(revision_payload, dict) else {}
        span = _section_span(str(snapshot.get("content") or ""), str((descriptor or {}).get("title") or ""))
        current_hash = "sha256:" + hashlib.sha256(
            str((span or {}).get("content") or "").strip().encode("utf-8")
        ).hexdigest()
        if descriptor is None or span is None or not hmac.compare_digest(
            str(basis.get("base_section_content_hash") or ""), current_hash
        ):
            return _failure(
                "section_patch_stale",
                "目标章节内容已变化或章节绑定失效，请基于最新 revision 重新提案",
            )

    try:
        result = doc.apply_agent_proposal(
            workspace_id, proposal_id, readonly=False,
            enforce_structure=enforce_structure,
        )
    except doc.DocServiceError as exc:
        return _failure(exc.code, exc.message)
    native_revision_id = str(result.get("applied_revision_id") or "")
    document_snapshot = _capture_document_snapshot(workspace_id)
    public_revision = REVISION_STORE.put(
        workspace_id,
        {
            **revision_payload,
            "native_revision_id": native_revision_id,
            "parent_report_revision_id": str((revision or {}).get("object_id") or ""),
            "proposal_id": proposal_id,
            "task_status": "agent_drafted",
            "document_snapshot": document_snapshot,
            "section_lineage": {
                **dict(revision_payload.get("section_lineage") or {}),
                **({
                    str(basis.get("section_id") or ""): {
                        "upstream_refs": list(basis.get("upstream_refs") or []),
                        "citation_locators": list(basis.get("citation_locators") or []),
                        "upstream_basis_hashes": dict(basis.get("upstream_basis_hashes") or {}),
                    }
                } if basis.get("patch_scope") == "section" else {}),
            },
        },
        producer="lvke-report-generation.report_apply",
        status="partial",
        source_ids=[preparation_id, str((revision or {}).get("object_id") or ""), proposal_id],
        basis={
            "native_revision_id": native_revision_id,
            "parent_report_revision_id": str((revision or {}).get("object_id") or ""),
            "upstream_basis_hash": basis_hash,
        },
    )
    warnings = ["已生成新修订；须重新 report_validate"]
    if not enforce_structure:
        warnings.append("结构校验已跳过（enforce_structure=false），不具备正式交付资格")
    if native_alias:
        warnings.append("native_revision_id 输入已弃用；请改用 report_revision_id")
    return _ok(
        {
            **result,
            "report_revision_id": public_revision["object_id"],
            "native_revision_id": native_revision_id,
            "parent_report_revision_id": str((revision or {}).get("object_id") or ""),
            "resource_uris": [public_revision["resource_uri"]],
        },
        warnings,
    )
