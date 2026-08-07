"""Agent 提案生命周期：创建、比对、应用与财务一致性检查。"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Optional



from .consistency import (
    consistency_check,
)

from .outline import (
    resolve_report_type,
)

from .paths import (
    DocServiceError,
    _new_id,
    _now_iso,
    _proposal_dir,
    _read_json,
    _write_json,
    _write_text,
)

from .structure import (
    merge_single_chapter_proposal,
    validate_report_structure,
)

from .workspace import (
    _current_revision_content,
    _save_revision,
    _write_meta,
    ensure_workspace,
    finance_summary,
    list_revisions,
    revision_content,
)


def _read_proposal(workspace_id: str, proposal_id: str) -> dict[str, Any]:
    meta = _read_json(_proposal_dir(workspace_id, proposal_id) / "meta.json", None)
    if not isinstance(meta, dict):
        raise DocServiceError("proposal_not_found", f"提案不存在：{proposal_id}")
    return meta


def create_agent_proposal(
    workspace_id: str,
    *,
    session_id: str = "",
    summary: str,
    proposed_content: str,
    target_sections: Optional[list[str]] = None,
    basis: str = "",
    expected_outline: Optional[list[str]] = None,
) -> dict[str, Any]:
    """创建文档修改提案(参考可研 create_agent_proposal:写 proposed_report.md + diff.html + meta)。"""
    meta = ensure_workspace(workspace_id)
    base_rev = str(meta.get("current_revision_id") or "")
    base_content = revision_content(workspace_id, base_rev) or ""
    proposed_content = proposed_content if proposed_content is not None else ""
    if not proposed_content.strip():
        raise DocServiceError("empty_proposal", "提案内容为空。")
    rt = resolve_report_type(meta)

    proposal_id = _new_id("prop")
    pdir = _proposal_dir(workspace_id, proposal_id)

    # 单章/少章提案自动合并进完整文档（幂等：agent 若已传完整文档则不进此分支）。
    merged_from_single_chapter = False
    expected_outline = [str(item) for item in (expected_outline or []) if str(item).strip()]
    pre_struct = validate_report_structure(
        proposed_content, rt, expected_chapters=expected_outline,
    )
    if pre_struct["missing_chapters"] and target_sections and len(target_sections) == 1:
        merged = merge_single_chapter_proposal(
            base_content, str(target_sections[0]), proposed_content
        )
        if merged is not None:
            vmerged = validate_report_structure(
                merged, rt, expected_chapters=expected_outline,
            )
            if not vmerged["missing_chapters"]:
                proposed_content = merged
                merged_from_single_chapter = True

    _write_text(pdir / "proposed_report.md", proposed_content)
    html = _html_diff(base_content, proposed_content, base_rev, "proposed")
    _write_text(pdir / "diff.html", html)

    structure = validate_report_structure(
        proposed_content, rt, expected_chapters=expected_outline,
    )
    now = _now_iso()
    record = {
        "id": proposal_id,
        "workspace_id": workspace_id,
        "session_id": session_id,
        "base_revision_id": base_rev,
        "target_sections": list(target_sections or []),
        "edit_summary": summary,
        "basis": basis,
        "expected_outline": expected_outline,
        "proposed_report_path": str(pdir / "proposed_report.md"),
        "diff_path": str(pdir / "diff.html"),
        "structure_ok": structure["ok"],
        "structure_issues": structure["issues"],
        "merged_from_single_chapter": merged_from_single_chapter,
        "status": "proposed",
        "applied_revision_id": "",
        "created_at": now,
        "updated_at": now,
    }
    _write_json(pdir / "meta.json", record)
    return record


def diff_agent_proposal(workspace_id: str, *, proposal_id: str = "", from_revision: str = "", to_revision: str = "") -> dict[str, Any]:
    """生成 diff(参考可研 diff_agent_proposal,difflib.HtmlDiff 章节级)。"""
    if proposal_id:
        proposal = _read_proposal(workspace_id, proposal_id)
        base_rev = str(proposal.get("base_revision_id") or "")
        base_content = revision_content(workspace_id, base_rev) or ""
        proposed_path = Path(proposal["proposed_report_path"])
        proposed_content = proposed_path.read_text(encoding="utf-8") if proposed_path.exists() else ""
        html = _html_diff(
            base_content, proposed_content,
            _revision_label(workspace_id, base_rev) or "当前版本", "拟应用提案",
        )
        return {"workspace_id": workspace_id, "proposal_id": proposal_id, "html_diff": html}
    # revision-to-revision
    left = revision_content(workspace_id, from_revision) if from_revision else _current_revision_content(workspace_id)[1]
    right = revision_content(workspace_id, to_revision) if to_revision else _current_revision_content(workspace_id)[1]
    html = _html_diff(
        left or "", right or "",
        _revision_label(workspace_id, from_revision) or "基准版本",
        _revision_label(workspace_id, to_revision) or "对照版本",
    )
    return {"workspace_id": workspace_id, "html_diff": html}


_REVISION_SOURCE_CN = {
    "bootstrap": "初始化", "fast_draft": "首版生成", "enhance_pass": "专业化增强",
    "apply": "应用提案", "agent": "AI 修改", "manual": "手动编辑",
}


def _revision_label(workspace_id: str, revision_id: str) -> str:
    """把修订编码映射为友好列头「第 N 版 · YYYY-MM-DD HH:mm · 来源」（D-2）。"""
    if not revision_id:
        return ""
    try:
        revs = list_revisions(workspace_id)  # 最新在前
    except Exception:  # noqa: BLE001
        return revision_id[:10]
    total = len(revs)
    for idx, r in enumerate(revs):
        if r.get("revision_id") == revision_id:
            no = total - idx
            tag = "最新版" if r.get("is_current") else ("初版" if no == 1 else f"第 {no} 版")
            when = str(r.get("created_at") or "")[:16].replace("T", " ")
            src = _REVISION_SOURCE_CN.get(str(r.get("source") or ""), "")
            return f"{tag} · {when}{(' · ' + src) if src else ''}"
    return revision_id[:10]


def _html_diff(left: str, right: str, left_label: str, right_label: str) -> str:
    differ = difflib.HtmlDiff(wrapcolumn=80)
    return differ.make_file(
        left.splitlines(),
        right.splitlines(),
        fromdesc=left_label,
        todesc=right_label,
        context=True,
        numlines=3,
    )


def apply_agent_proposal(
    workspace_id: str,
    proposal_id: str,
    *,
    readonly: bool = False,
    enforce_structure: bool = True,
) -> dict[str, Any]:
    """应用提案,多步校验后落新修订(参考可研 apply_agent_proposal 校验顺序)。

    校验顺序:① 只读锁 → ② 提案存在 → ③ status==proposed → ④ 版本新鲜度
    (base_revision_id == 当前修订) → ⑤ 结构校验 → ⑥ 财务一致性(轻量) → ⑦ 落版本。
    任一步失败抛 :class:`DocServiceError` 并阻断。
    """
    # ① 只读锁
    if readonly:
        raise DocServiceError("readonly", "工作区处于只读模式（被其他会话占用），无法应用提案。")
    # ② 提案存在
    proposal = _read_proposal(workspace_id, proposal_id)
    # ②.5 归属:提案必须属于本工作区。
    if str(proposal.get("workspace_id") or workspace_id) != workspace_id:
        raise DocServiceError("proposal_ownership", "提案不属于当前工作区。")
    # ③ 状态
    if proposal.get("status") != "proposed":
        raise DocServiceError(
            "invalid_proposal_status",
            f"提案状态为 {proposal.get('status')}，只能应用 proposed 状态的提案。",
        )
    # ④ 版本新鲜度
    meta = ensure_workspace(workspace_id)
    current_rev = str(meta.get("current_revision_id") or "")
    if str(proposal.get("base_revision_id") or "") != current_rev:
        raise DocServiceError(
            "stale_proposal",
            "提案基于的版本已过期（文档已被更新），请基于最新版本重新提案。",
        )
    proposed_path = Path(proposal["proposed_report_path"])
    if not proposed_path.exists():
        raise DocServiceError("proposal_payload_missing", "提案正文文件缺失。")
    proposed_content = proposed_path.read_text(encoding="utf-8")
    # ⑤ 结构校验
    if enforce_structure:
        structure = validate_report_structure(
            proposed_content,
            resolve_report_type(meta),
            expected_chapters=list(proposal.get("expected_outline") or []),
        )
        if not structure["ok"]:
            raise DocServiceError(
                "structure_invalid",
                "结构校验未通过：" + "；".join(structure["issues"]),
            )
    # ⑥ 财务一致性(轻量:占位通过,有 finance.json 时校验金额标记存在)
    _check_finance_consistency(workspace_id, proposed_content)
    # ⑦ 落版本
    rev = _save_revision(
        workspace_id,
        content=proposed_content,
        parent_id=current_rev,
        summary=str(proposal.get("edit_summary") or "应用 agent 提案"),
        source="agent_proposal",
    )
    meta["current_revision_id"] = rev["revision_id"]
    meta["updated_at"] = _now_iso()
    _write_meta(workspace_id, meta)

    proposal["status"] = "applied"
    proposal["applied_revision_id"] = rev["revision_id"]
    proposal["updated_at"] = _now_iso()
    _write_json(_proposal_dir(workspace_id, proposal_id) / "meta.json", proposal)
    return {
        "workspace_id": workspace_id,
        "proposal_id": proposal_id,
        "applied_revision_id": rev["revision_id"],
        "status": "applied",
    }


def _check_finance_consistency(workspace_id: str, proposed_content: str) -> None:
    """财务一致性校验(委托具名的 ``consistency_check`` 的财务部分)。

    有 finance.json 且声明了 ``required_markers`` 时,要求这些标记词出现在正文,
    否则阻断 apply;占位章节(``（待补充）``)只进入待补充清单,不阻断 apply。
    """
    finance = finance_summary(workspace_id)
    markers = finance.get("required_markers") if isinstance(finance, dict) else None
    if not markers:
        return
    result = consistency_check(workspace_id, report_text=proposed_content, finance=finance)
    missing_markers = [
        item.split("：", 1)[-1]
        for item in result["missing_items"]
        if item.startswith("正文缺少关键财务指标")
    ]
    if missing_markers:
        raise DocServiceError(
            "finance_inconsistent",
            "财务一致性校验未通过，正文缺少关键财务指标：" + "、".join(missing_markers),
        )
