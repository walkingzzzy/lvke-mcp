"""报告域确定性交付工件实现。

提供 ``create_draft_export`` / ``create_deliverable_artifact`` /
``_artifact_root`` / ``list_artifacts`` / ``get_artifact`` /
``read_artifact_download`` 及其闭包：

- doc_service → ``lvke_mcp.domains.reports.doc_service``（MCP 自有）
- report_artifacts._path → 读工作区根 ``{name}.json``；governed 快照裁剪为
  MCP 域内实际存在的 ``evidence_pack``（读公共 data-analysis repository）
- source_files_api.source_basis_snapshot → 读 ``lvke_data_acquisition.SOURCE_STORE``
  构造同 schema 快照（存储不可用 fail-closed 进入 basis 指纹）
- finance 审计/门禁 → ``lvke_mcp.domains.finance.run_store`` /
  ``lvke_mcp.domains.finance.gate``；MCP 边界无持久化 finance_binding，
  绑定退化为最新 run（与 MCP gate 语义一致），门禁显式传 expected_run_id
- docx_fonts.normalize_docx_fonts → ``lvke_mcp.domains.reports.docx_fonts``

Wave 2.8 门面：实现搬到 ``_artifacts/`` 子模块 —— ``base``（版本常量、ID 白名单
正则与 hash 原语）、``storage``（路径布局、state 读写与原子落盘）、
``snapshots``（上游依据快照）、``formal_gate``（正式资格门禁与 basis 捕获）、
``support_files``（附件收集与路径安全）、``directory``（工件目录构建与文件校验）、
``lifecycle``（创建、失效与刷新）与 ``query``（读取、列举与受控下载）。

``_LOCK`` 与 ``_GOVERNED_SNAPSHOTS`` 等模块级状态只在 ``base`` 里有一份实例，
从这里 re-export 后仍是同一对象，因此跨模块的锁语义不变。
"""

from __future__ import annotations

import copy  # noqa: F401
import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import mimetypes  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import threading  # noqa: F401
import uuid  # noqa: F401
from collections.abc import Mapping, Sequence  # noqa: F401
from contextlib import contextmanager  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from pathlib import Path, PurePosixPath  # noqa: F401
from typing import Any  # noqa: F401

from filelock import FileLock  # noqa: F401

from lvke_mcp.domains.reports import doc_service  # noqa: F401
from lvke_mcp.runtime import workspace  # noqa: F401

from ._artifacts.base import (  # noqa: F401
    BASIS_SCHEMA_VERSION,
    DEFAULT_TEMPLATE_VERSION,
    DRAFT_MARKER,
    MANIFEST_SCHEMA_VERSION,
    SCHEMA_VERSION,
    DeliverableArtifactError,
    _GOVERNED_SNAPSHOTS,
    _LOCK,
    _SAFE_ARTIFACT_ID,
    _SAFE_OPERATION_ID,
    _SAFE_REVISION_ID,
    _SAFE_TEMPLATE_VERSION,
    _SAFE_WORKSPACE_ID,
    _SHA256_EVIDENCE_HASH,
    _SUPPORT_SUFFIXES,
    _VERIFIED_APPENDIX_STATES,
    _bytes_hash,
    _canonical_hash,
    _file_hash,
    _now,
    _validate_artifact_id,
    _validate_template_version,
    _validate_workspace_id,
    _without_volatile_timestamps,
)
from ._artifacts.directory import (  # noqa: F401
    _build_artifact_directory,
    _file_entry,
    _safe_relative_name,
    _set_docx_metadata,
    _verify_files,
)
from ._artifacts.formal_gate import (  # noqa: F401
    _assert_formal_basis,
    _basis_problem,
    _capture_basis,
    _draft_basis_blockers,
    _marker_markdown,
    _readiness_blockers,
    _strict_finance_gate,
)
from ._artifacts.lifecycle import (  # noqa: F401
    _append_event,
    _basis_change_reasons,
    _create,
    _invalidate_locked,
    _persist_new_record,
    _refresh_record_locked,
    _create_revision_bound_deliverable_artifact,
    _create_revision_bound_draft_export,
    create_deliverable_artifact,
    create_draft_export,
)
from ._artifacts.query import (  # noqa: F401
    _resolve_artifact_download,
    get_artifact,
    list_artifacts,
    read_artifact_download,
)
from ._artifacts.snapshots import (  # noqa: F401
    _document_snapshot,
    _evidence_pack_snapshot,
    _fresh_readiness,
    _json_snapshot,
    _load_finance_run,
    _source_basis_snapshot,
)
from ._artifacts.storage import (  # noqa: F401
    _artifact_root,
    _artifacts_root,
    _empty_state,
    _finance_artifact_root,
    _read_state,
    _require_workspace,
    _service_root,
    _state_guard,
    _state_path,
    _strict_read_json,
    _workspace_root,
    _write_bytes,
    _write_json_atomic,
    _write_json_file,
    bind_finance_run,
    load,
    save,
)
from ._artifacts.support_files import (  # noqa: F401
    _appendix_files_snapshot,
    _appendix_path_rows,
    _collect_support_files,
    _copy_support_file,
    _is_relative_to,
    _normalize_declared_hash,
    _path_has_symlink,
    _safe_filename,
    _safe_support_source,
    _verify_finance_workbook,
)
