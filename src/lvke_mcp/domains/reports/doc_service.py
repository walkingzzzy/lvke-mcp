"""报告域文档服务 —— MCP 自有实现（零外部依赖）。

既有报告文档服务的域内复刻（原样保留被引符号，仅改 import 路径与
存储根，不重写业务逻辑）：

- 存储根：MCP 自有 ``runtime.workspace.workspace_root``
- 内部依赖改写：``workspace_migration``(WAL 控制面) 删除、审计
  调用删除（MCP 域内无对应设施，best-effort 块直接移除）、``docx_fonts`` /
  ``env_templates`` / ``finance_model`` / ``run_service`` 指向 lvke_mcp 域内实现

存储落点（MCP 自管，每个工作区一个报告）：
``{LVKE_MCP_DATA_DIR}/workspaces/{workspace_id}/``
  ├─ ``workspace_meta.json``        工作区元信息 + 当前修订指针
  ├─ ``revisions/{rev_id}/report.md`` 各修订正文
  ├─ ``revisions/{rev_id}/meta.json`` 各修订元信息
  ├─ ``agent_proposals/{pid}/proposed_report.md`` / ``diff.html`` / ``meta.json``
  ├─ ``issues/issues.json``         issue_center
  └─ ``finance.json``               (可选)财务摘要,只读

Wave 2.7 门面：实现搬到 ``_doc_service/`` 子模块 —— ``outline``（九章大纲与
报告类型静态目录）、``paths``（错误类型、ID/时间原语与工作区路径布局）、
``structure``（正文解析与结构校验）、``workspace``（工作区与修订存储）、
``consistency``（跨工作区一致性检查）、``proposals``（Agent 提案生命周期）、
``gen_tasks``（生成任务快照）与 ``docx``（docx 导出适配）。

``artifacts``、``readiness``、``validation``、``read_model`` 与 review service
都按 ``doc_service._workspace_root`` / ``._revision_dir`` 这类**私有**属性访问本
模块，因此门面必须连私有符号一并 re-export。
"""

from __future__ import annotations

import difflib  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import re  # noqa: F401
import secrets  # noqa: F401
from datetime import datetime  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Optional  # noqa: F401

from filelock import FileLock  # noqa: F401

from lvke_mcp.runtime import workspace as runtime_workspace  # noqa: F401

from ._doc_service.consistency import consistency_check  # noqa: F401
from ._doc_service.docx import (  # noqa: F401
    _DOCX_APPENDIX_HEADING_RE,
    _DOCX_DATA_IMAGE_TYPES,
    _DOCX_IMAGE_LINE_RE,
    _DOCX_MAX_IMAGE_BYTES,
    _DOCX_PAGE_BREAK_MARKERS,
    _docx_image_bytes,
    _export_docx_via_pandoc,
    _export_docx_via_python_docx,
    markdown_to_docx,
)
from ._doc_service.gen_tasks import (  # noqa: F401
    _gen_task_is_latest,
    _gen_task_path,
    _gen_task_snapshot_path,
    list_gen_tasks,
    load_gen_task,
    save_gen_task,
)
from ._doc_service.outline import (  # noqa: F401
    DEFAULT_DOC_KIND,
    DEFAULT_REPORT_TYPE,
    DOC_KINDS,
    ENABLED_DOC_KINDS,
    REPORT_CHAPTERS,
    REPORT_STRUCTURES,
    logger,
    report_chapter_titles,
    report_structure,
    resolve_doc_kind,
    resolve_report_type,
)
from ._doc_service.paths import (  # noqa: F401
    ISSUE_SEVERITIES,
    ISSUE_SOURCES,
    ISSUE_STATUSES,
    MISSING_MARKER,
    PROPOSAL_STATUSES,
    DocServiceError,
    _finance_path,
    _issues_path,
    _meta_path,
    _new_id,
    _now_iso,
    _proposal_dir,
    _proposals_dir,
    _read_json,
    _revision_dir,
    _revisions_dir,
    _workspace_root,
    _write_json,
    _write_text,
)
from ._doc_service.proposals import (  # noqa: F401
    _REVISION_SOURCE_CN,
    _check_finance_consistency,
    _html_diff,
    _read_proposal,
    _revision_label,
    apply_agent_proposal,
    create_agent_proposal,
    diff_agent_proposal,
)
from ._doc_service.structure import (  # noqa: F401
    _HEADING_RE,
    _anchor_for,
    _strip_leading_chapter_title,
    default_report_markdown,
    merge_single_chapter_proposal,
    parse_revision_sections,
    validate_report_structure,
)
from ._doc_service.workspace import (  # noqa: F401
    _current_revision_content,
    _default_meta,
    _read_meta,
    _save_revision,
    _write_meta,
    ensure_workspace,
    finance_summary,
    list_issues,
    list_revisions,
    read_document,
    revision_content,
    load_workspace_snapshot,
    workspace_finance_model,
    workspace_report_type,
)
