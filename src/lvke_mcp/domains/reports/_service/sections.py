"""章节读取、校验与章节级提案。"""

from __future__ import annotations

import hashlib
from typing import Any


from lvke_mcp.domains.reports import read_model as report_read_model

from .base import (
    _failure,
    _merge_section_patch,
    _resolve_revision_record,
    _revision_sections,
)

from .revisions import (
    propose,
)


def validate(
    workspace_id: str,
    revision_id: str,
) -> dict[str, Any]:
    from lvke_mcp.domains.reports.validation import validate_report

    return validate_report(workspace_id, revision_id)


def list_sections(
    workspace_id: str,
    revision_id: str,
) -> dict[str, Any]:
    return report_read_model.list_sections(workspace_id, revision_id)


def get_section(
    workspace_id: str,
    revision_id: str,
    section_id: str,
) -> dict[str, Any]:
    return report_read_model.get_section(workspace_id, revision_id, section_id)


def propose_section(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    basis = dict(args.get("basis")) if isinstance(args.get("basis"), dict) else {}
    revision_id = str(args.get("report_revision_id") or "")
    nested_revision_id = str(basis.get("report_revision_id") or "")
    if nested_revision_id and nested_revision_id != revision_id:
        return _failure("proposal_revision_ambiguous", "顶层 revision 与 basis revision 不一致")
    basis["report_revision_id"] = revision_id
    current = get_section(
        workspace_id,
        revision_id,
        str(args.get("section_id") or ""),
    )
    if current.get("status") != "ok":
        return current
    descriptor = current["section"]
    # 正文里还没有这个标题时，提案注定在 apply 阶段失败（章节 span 找不到）。
    # 此前 propose 照样成功创建，让调用方以为可以继续，直到 apply 才报
    # section_patch_stale —— 而那个码说的是"内容已变化"，方向完全是错的。
    # propose 阶段就能判定，就在这里拒绝并指出首次落章该走哪条路。
    if current.get("found_in_document") is False:
        return _failure(
            "section_absent_from_document",
            f"章节《{descriptor.get('title')}》已在 outline 固化，但当前正文尚无对应标题；"
            "report_propose_section 只能改写正文里已存在的章节。"
            "首次落章请调用 report_propose 提交含该标题的整篇正文，再用本工具做后续改写。",
        )
    revision, _native_alias = _resolve_revision_record(
        workspace_id,
        revision_id,
    )
    snapshot = ((revision or {}).get("payload") or {}).get("document_snapshot") or {}
    document = str(snapshot.get("content") or "")
    merged = _merge_section_patch(
        document,
        descriptor,
        str(args.get("proposed_content") or ""),
        descriptors=_revision_sections(revision or {}),
    )
    if merged is None:
        return _failure("section_patch_invalid", "目标章节不存在或 proposed_content 为空")
    merged_document, base_section = merged
    basis.update({
        "patch_scope": "section",
        "section_id": str(descriptor["section_id"]),
        "base_section_content_hash": "sha256:" + hashlib.sha256(
            base_section.encode("utf-8")
        ).hexdigest(),
        "merged_document_hash": "sha256:" + hashlib.sha256(
            merged_document.encode("utf-8")
        ).hexdigest(),
        "upstream_refs": list(basis.get("upstream_refs") or []),
        "citation_locators": list(basis.get("citation_locators") or []),
        "upstream_basis_hashes": dict(basis.get("upstream_basis_hashes") or {}),
    })
    return propose(
        {
            "workspace_id": workspace_id,
            "summary": args["summary"],
            "proposed_content": merged_document,
            "target_sections": [descriptor["title"]],
            "basis": basis,
        }
    )


def validate_section(
    workspace_id: str,
    revision_id: str,
    section_id: str,
) -> dict[str, Any]:
    return report_read_model.validate_section(workspace_id, revision_id, section_id)
