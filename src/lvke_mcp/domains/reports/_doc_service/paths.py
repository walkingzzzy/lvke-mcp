"""错误类型、ID/时间原语、工作区路径布局与 JSON/文本读写。"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any


from lvke_mcp.runtime import workspace as runtime_workspace


# 待补充占位标记(参考可研 MISSING="【待补充】";域内内置模板用全角括号占位)。
MISSING_MARKER = "（待补充）"


ISSUE_SOURCES = {"preview_gate", "check_issues", "missing_items", "review_comment"}


ISSUE_SEVERITIES = {"info", "low", "medium", "high", "critical"}


ISSUE_STATUSES = {"open", "in_progress", "resolved", "ignored"}


PROPOSAL_STATUSES = {"proposed", "applied", "rejected"}


class DocServiceError(RuntimeError):
    """文档服务统一异常,携带机器可读 code。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _workspace_root(workspace_id: str) -> Path:
    return runtime_workspace.workspace_root(workspace_id)


def _meta_path(workspace_id: str) -> Path:
    return _workspace_root(workspace_id) / "workspace_meta.json"


def _revisions_dir(workspace_id: str) -> Path:
    """报告修订正文（Markdown + meta）落盘目录。

    正文草稿是交付物，随仓库留存；``workspace_meta / issues / finance`` 是
    运行时元数据，仍走 :func:`_workspace_root`。
    """
    return runtime_workspace.deliverable_dir(workspace_id, "report", "revisions")


def _revision_dir(workspace_id: str, revision_id: str) -> Path:
    return _revisions_dir(workspace_id) / revision_id


def _proposals_dir(workspace_id: str) -> Path:
    """Agent 提案（proposed_report.md / diff.html）落盘目录。"""
    return runtime_workspace.deliverable_dir(workspace_id, "report", "proposals")


def _proposal_dir(workspace_id: str, proposal_id: str) -> Path:
    return _proposals_dir(workspace_id) / proposal_id


def _issues_path(workspace_id: str) -> Path:
    return _workspace_root(workspace_id) / "issues" / "issues.json"


def _finance_path(workspace_id: str) -> Path:
    return _workspace_root(workspace_id) / "finance.json"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
