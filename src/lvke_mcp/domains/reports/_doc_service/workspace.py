"""工作区与修订存储：元信息、修订保存与读取、快照与财务摘要。"""

from __future__ import annotations

import re
from typing import Any, Optional

from filelock import FileLock

from lvke_mcp.runtime import workspace as runtime_workspace

from .outline import (
    DEFAULT_DOC_KIND,
    DEFAULT_REPORT_TYPE,
    DOC_KINDS,
    REPORT_STRUCTURES,
    report_structure,
    resolve_doc_kind,
    resolve_report_type,
)

from .paths import (
    DocServiceError,
    _finance_path,
    _issues_path,
    _meta_path,
    _new_id,
    _now_iso,
    _read_json,
    _revision_dir,
    _revisions_dir,
    _write_json,
    _write_text,
)

from .structure import (
    default_report_markdown,
    parse_revision_sections,
)


def workspace_report_type(workspace_id: str) -> str:
    return resolve_report_type(_read_meta(workspace_id))


def _default_meta(workspace_id: str) -> dict[str, Any]:
    return {
        "schema_version": "keyui_workspace.v1",
        "workspace_id": workspace_id,
        "title": "",
        "current_revision_id": "",
        "created_at": "",
        "updated_at": "",
    }


def _read_meta(workspace_id: str) -> dict[str, Any]:
    # MCP 域内无 hermes WAL 控制面：直接读 JSON，损坏时回退默认 meta。
    value = _read_json(_meta_path(workspace_id), None)
    if isinstance(value, dict):
        return value
    return _default_meta(workspace_id)


def _write_meta(workspace_id: str, metadata: dict[str, Any]) -> None:
    value = dict(metadata)
    value.pop("_metadata_source", None)
    value.pop("_read_only_recovery", None)
    value["schema_version"] = "keyui_workspace.v1"
    _write_json(_meta_path(workspace_id), value)


def _save_revision(workspace_id: str, *, content: str, parent_id: str, summary: str, source: str) -> dict[str, Any]:
    revision_id = _new_id("rev")
    rev_dir = _revision_dir(workspace_id, revision_id)
    _write_text(rev_dir / "report.md", content)
    meta = {
        "schema_version": "keyui_revision.v1",
        "revision_id": revision_id,
        "parent_id": parent_id,
        "summary": summary,
        "source": source,
        "created_at": _now_iso(),
    }
    _write_json(rev_dir / "meta.json", meta)
    return meta


def ensure_workspace(
    workspace_id: str,
    *,
    title: str = "可行性研究报告",
    report_type: str = "",
    doc_kind: str = "",
    requirement: Optional[dict[str, Any]] = None,
    cover: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """确保工作区存在;不存在则按结构类型创建初始修订(默认发改委新版 9 章)。"""
    if not workspace_id or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", workspace_id):
        raise DocServiceError("invalid_workspace_id", "工作区 id 不合法。")
    lock_dir = runtime_workspace.data_root() / "workspace_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_dir / f"{workspace_id}.init.lock"), timeout=30):
        meta = _read_meta(workspace_id)
        if meta.get("current_revision_id"):
            return meta
        rt = report_type if report_type in REPORT_STRUCTURES else DEFAULT_REPORT_TYPE
        dk = doc_kind if doc_kind in DOC_KINDS else DEFAULT_DOC_KIND
        now = _now_iso()
        rev = _save_revision(
            workspace_id,
            content=default_report_markdown(title, rt),
            parent_id="",
            summary="初始化报告大纲",
            source="bootstrap",
        )
        meta = {
            "schema_version": "keyui_workspace.v1",
            "workspace_id": workspace_id,
            "title": title,
            "report_type": rt,
            "doc_kind": dk,
            "requirement": dict(requirement) if isinstance(requirement, dict) else {},
            "cover": dict(cover) if isinstance(cover, dict) else {},
            "current_revision_id": rev["revision_id"],
            "created_at": now,
            "updated_at": now,
        }
        _write_meta(workspace_id, meta)
        return meta


def _current_revision_content(workspace_id: str) -> tuple[str, str]:
    """返回 ``(current_revision_id, markdown)``;工作区未初始化则自动初始化。"""
    meta = ensure_workspace(workspace_id)
    rev_id = str(meta.get("current_revision_id") or "")
    content = ""
    if rev_id:
        path = _revision_dir(workspace_id, rev_id) / "report.md"
        content = path.read_text(encoding="utf-8") if path.exists() else ""
    return rev_id, content


def revision_content(workspace_id: str, revision_id: str) -> Optional[str]:
    path = _revision_dir(workspace_id, revision_id) / "report.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def load_workspace_snapshot(workspace_id: str) -> dict[str, Any]:
    """工作区快照(参考可研 load_workspace_snapshot)。"""
    meta = ensure_workspace(workspace_id)
    rev_id, content = _current_revision_content(workspace_id)
    rt = resolve_report_type(meta)
    struct = report_structure(rt)
    dk = resolve_doc_kind(meta)
    return {
        "workspace_id": workspace_id,
        "title": meta.get("title") or "",
        "report_type": rt,
        "report_type_label": struct.get("label", ""),
        "doc_kind": dk,
        "doc_kind_label": DOC_KINDS.get(dk, {}).get("label", ""),
        "requirement": meta.get("requirement") or {},
        "cover": meta.get("cover") or {},
        "report_outline": [c["title"] for c in struct["chapters"]],
        "current_revision_id": rev_id,
        "updated_at": meta.get("updated_at") or "",
        "sections": parse_revision_sections(content),
        "issue_center": list_issues(workspace_id),
        "finance_summary": finance_summary(workspace_id),
    }


def read_document(workspace_id: str, *, section: str = "", revision_id: str = "") -> dict[str, Any]:
    """读取文档全文或某章节(参考可研 doc_read)。"""
    if revision_id:
        content = revision_content(workspace_id, revision_id)
        if content is None:
            raise DocServiceError("revision_not_found", f"修订不存在：{revision_id}")
    else:
        revision_id, content = _current_revision_content(workspace_id)
    if section:
        for sec in parse_revision_sections(content):
            if section in sec["title"] or section == sec["anchor"]:
                return {
                    "workspace_id": workspace_id,
                    "revision_id": revision_id,
                    "section": sec["title"],
                    "content": sec["body"],
                }
        raise DocServiceError("section_not_found", f"章节不存在：{section}")
    return {
        "workspace_id": workspace_id,
        "revision_id": revision_id,
        "content": content,
        "sections": [s["title"] for s in parse_revision_sections(content)],
    }


def finance_summary(workspace_id: str) -> dict[str, Any]:
    """只读财务摘要(读 finance.json,缺省返回空结构)。"""
    data = _read_json(_finance_path(workspace_id), {})
    if not isinstance(data, dict):
        return {}
    return data


def workspace_finance_model(workspace_id: str, *, force_flat: bool = False) -> dict[str, Any]:
    """基于工作区 requirement.finance 运行完整财务模型并返回 13 表结果。

    兼容入口：内部委托 ``domains.finance.run_service``。
    """
    try:
        from lvke_mcp.domains.finance import run_service

        return run_service.run_workspace_finance_model(
            workspace_id,
            force_flat=force_flat,
            allow_prepare_llm=not force_flat,
            record_audit=False,  # 审计由 API/报告链路显式登记，避免隐式写副作用
            mode="estimate_preview",
        )
    except Exception as exc:  # noqa: BLE001 - 财务模型失败不阻断生成
        try:
            meta = _read_meta(workspace_id)
            req = meta.get("requirement") or {}
            fin = dict((req.get("finance") if isinstance(req, dict) else {}) or {})
        except Exception:  # noqa: BLE001
            fin = {}
        return {
            "available": False,
            "ok": False,
            "reason": str(exc)[:200],
            "finance_inputs": fin,
        }


def list_issues(workspace_id: str, *, status: str = "", source: str = "") -> list[dict[str, Any]]:
    issues = _read_json(_issues_path(workspace_id), [])
    if not isinstance(issues, list):
        issues = []
    result = []
    for issue in issues:
        if status and issue.get("status") != status:
            continue
        if source and issue.get("source") != source:
            continue
        result.append(issue)
    return result


def list_revisions(workspace_id: str) -> list[dict[str, Any]]:
    """列出工作区所有修订(按创建时间倒序,标注当前修订)。"""
    meta = _read_meta(workspace_id)
    current = str(meta.get("current_revision_id") or "")
    rdir = _revisions_dir(workspace_id)
    if not rdir.is_dir():
        return []
    revs: list[dict[str, Any]] = []
    for child in rdir.iterdir():
        if not child.is_dir():
            continue
        rmeta = _read_json(child / "meta.json", None)
        if not isinstance(rmeta, dict):
            rmeta = _read_json(child / "revision.json", None)
        if isinstance(rmeta, dict) and rmeta.get("revision_id"):
            revs.append(
                {
                    "revision_id": rmeta.get("revision_id"),
                    "parent_id": rmeta.get("parent_id") or "",
                    "summary": rmeta.get("summary") or "",
                    "source": rmeta.get("source") or "",
                    "created_at": rmeta.get("created_at") or "",
                    "is_current": rmeta.get("revision_id") == current,
                }
            )
    revs.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return revs
