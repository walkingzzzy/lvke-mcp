"""Thin orchestration over existing report, document and artifact services.

Wave 2.6 facade: implementation moved to ``_service/`` sub-modules —
``base`` (terminal states, outline/section primitives, snapshot capture and the
``_ok``/``_failure`` envelopes), ``generation`` (``prepare``/``start``/
``status``/``readiness``), ``sections`` (section read, validate and
section-scoped proposals), ``revisions`` (whole-document propose/diff/apply),
``export`` (docx export) and ``resources`` (resource listing and resolution).

``servers/lvke_report_generation/service.py`` re-exports this module with
``import *``, and ``runtime/resource_registry`` loads it by string path, so the
full public surface must stay importable from here. Consumers that hold the
module object (``from ... import application as service``) reach incidental
imports as attributes, so the original header imports are re-exported too.
"""

from __future__ import annotations

import hmac  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
from typing import Any  # noqa: F401
from urllib.parse import quote, unquote  # noqa: F401

from lvke_mcp.adapters.report_repository import (  # noqa: F401
    BINDING_STORE,
    PREPARATION_STORE,
    REVISION_STORE,
)
from lvke_mcp.runtime.storage import paginate_resource_entries  # noqa: F401
from lvke_mcp.domains.asset_acquisition.tables import get_package_record  # noqa: F401
from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE  # noqa: F401
from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_STORE  # noqa: F401
from lvke_mcp.adapters.finance_tables_repository import PACKAGE_STORE as TABLE_STORE  # noqa: F401

from lvke_mcp.domains.reports import read_model as report_read_model  # noqa: F401

from ._service.base import (  # noqa: F401
    _HEADING_RE,
    _TASK_TERMINAL,
    _capture_document_snapshot,
    _failure,
    _materialize_local_document_snapshot,
    _merge_section_patch,
    _normalize_finance_binding,
    _normalize_outline,
    _ok,
    _resolve_revision_record,
    _resolve_target_sections,
    _revision_sections,
    _section_content,
    _section_span,
    _supplied_document_snapshot,
)
from ._service.export import export_docx  # noqa: F401
from ._service.generation import (  # noqa: F401
    prepare,
    readiness,
    start,
    status,
)
from ._service.resources import (  # noqa: F401
    list_resources,
    resolve_resource,
)
from ._service.revisions import (  # noqa: F401
    apply,
    diff,
    propose,
)
from ._service.sections import (  # noqa: F401
    get_section,
    list_sections,
    propose_section,
    validate,
    validate_section,
)
