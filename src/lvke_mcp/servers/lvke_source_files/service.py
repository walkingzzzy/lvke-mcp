"""MCP adapter for governed source-file staging, parsing and resources.

Wave 1.2 facade: implementation moved to ``_service/`` sub-modules. Every name
that was previously reachable as ``service.X`` — public functions, constants and
the incidental module/class imports — is re-exported here unchanged, because
consumers use ``from lvke_mcp.servers.lvke_source_files import service`` and then
attribute access.
"""

from __future__ import annotations

# Incidental imports that became part of the ``service.X`` attribute surface.
import base64  # noqa: F401
import binascii  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import stat  # noqa: F401
import uuid  # noqa: F401
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from filelock import FileLock  # noqa: F401

from lvke_mcp.runtime.storage import (
    canonical_json,  # noqa: F401
    paginate_resource_entries,  # noqa: F401
    require_safe_id,  # noqa: F401
    sha256_json,  # noqa: F401
)
from lvke_mcp.adapters import source_files_repository as source_api  # noqa: F401
from lvke_mcp.adapters.workbook_inspection import inspect_path  # noqa: F401
from lvke_mcp.runtime.workspace import workspace_root  # noqa: F401
from lvke_mcp.runtime.coordination import build_coordination  # noqa: F401
from lvke_mcp.servers.lvke_source_files.external_corpora import (
    ExternalCorpusError,  # noqa: F401
    configured_import_root_diagnostics,  # noqa: F401
    configured_import_roots,  # noqa: F401
    resolve_project_corpora,  # noqa: F401
)

# Constants keep their original import path.
from ._service.constants import (
    CHUNK_LIMIT,
    DIRECT_CONTENT_LIMIT,
    DOMAIN,
    SESSION_TTL,
)

# Implementation re-export.
from ._service.envelope import (
    _blocked,
    _envelope,
    _from_source_exception,
    _now,
)
from ._service.imports import (
    _commit_and_parse,
    _configured_import_roots,
    _decode_content,
    _public_record,
    _resolve_local_source,
    _stage_bytes,
    import_content,
    import_promoted_content,
    import_local_path,
    resolve_external_corpus,
)
from ._service.listing import get_source_file, list_source_files
from ._service.manifest import (
    _load_manifest,
    _owned_manifest,
    _validate_chunk_continuity,
)
from ._service.parse_jobs import parse_cancel, parse_retry, parse_status
from ._service.paths import (
    _analysis_uri,
    _file_uri,
    _job_uri,
    _manifest_path,
    _session_dir,
    _session_lock,
    _sessions_root,
    _source_root,
    _write_json_atomic,
)
from ._service.resources import list_resources, read_resource
from ._service.upload import (
    upload_abort,
    upload_begin,
    upload_chunk,
    upload_commit,
    upload_status,
)
from ._service.workbook import (
    _WORKBOOK_OPERATION_TO_HANDLER,
    _WORKBOOK_RANGE_RE,
    _column_index,
    _parse_workbook_range,
    _slice_workbook_result,
    inspect_workbook,
)

__all__ = [
    "Any",
    "CHUNK_LIMIT",
    "DIRECT_CONTENT_LIMIT",
    "DOMAIN",
    "ExternalCorpusError",
    "FileLock",
    "Path",
    "SESSION_TTL",
    "_WORKBOOK_OPERATION_TO_HANDLER",
    "_WORKBOOK_RANGE_RE",
    "_analysis_uri",
    "_blocked",
    "_column_index",
    "_commit_and_parse",
    "_configured_import_roots",
    "_decode_content",
    "_envelope",
    "_file_uri",
    "_from_source_exception",
    "_job_uri",
    "_load_manifest",
    "_manifest_path",
    "_now",
    "_owned_manifest",
    "_parse_workbook_range",
    "_public_record",
    "_resolve_local_source",
    "_session_dir",
    "_session_lock",
    "_sessions_root",
    "_slice_workbook_result",
    "_source_root",
    "_stage_bytes",
    "_validate_chunk_continuity",
    "_write_json_atomic",
    "base64",
    "binascii",
    "build_coordination",
    "canonical_json",
    "configured_import_root_diagnostics",
    "configured_import_roots",
    "datetime",
    "get_source_file",
    "hashlib",
    "import_content",
    "import_local_path",
    "import_promoted_content",
    "inspect_path",
    "inspect_workbook",
    "json",
    "list_resources",
    "list_source_files",
    "os",
    "paginate_resource_entries",
    "parse_cancel",
    "parse_retry",
    "parse_status",
    "re",
    "read_resource",
    "require_safe_id",
    "resolve_external_corpus",
    "resolve_project_corpora",
    "sha256_json",
    "shutil",
    "source_api",
    "stat",
    "timedelta",
    "timezone",
    "upload_abort",
    "upload_begin",
    "upload_chunk",
    "upload_commit",
    "upload_status",
    "uuid",
    "workspace_root",
]
