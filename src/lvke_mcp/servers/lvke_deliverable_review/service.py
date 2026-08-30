"""Unified deliverable-review application service.

All decisions are projected from immutable preparation objects and append-only
review events. Existing finance/report validators are evidence inputs, never a
substitute for the unified review verdict.

Wave 4 facade: implementation moved to ``_service/`` sub-modules — ``base``
(stores, async-review state, envelopes, URI builders and the retest-classification
primitives), ``legacy_gate``, ``target_resolve`` (cross-domain target and upstream
binding projection), ``preparation``, ``finding_rules`` (per-group finding
generation), ``executor`` (rule executor and review run), ``lifecycle``
(``start``, async scheduling/resume and ``get_review``), ``events`` (project
events, freshness and review projection), ``metrics`` (workspace metrics),
``findings_query``, ``disposition`` (finding state machine), ``retest``,
``export``, ``standards`` and ``resources``.

Three grouping decisions worth recording, all forced by real cycles in the review
state machine rather than by line counts:

* the retest classification primitives (``_classify_retest_operations``,
  ``_shadow_comparison``, ``_finding_match_key``, ``_finding_coverage_rule_id``,
  ``_gate_difference``) live in ``base`` because ``events._project_events`` needs
  them just as much as ``retest`` does;
* ``get_review`` lives in ``lifecycle``, not ``events``: reading a review can
  trigger ``_resume_async_review_if_needed``, so it is a lifecycle operation
  rather than a pure projection;
* ``_project`` stays with ``events`` since it orchestrates ``_project_events`` and
  ``_freshness_reasons``.

``_ASYNC_THREADS`` and ``_ASYNC_LOCK`` live only in ``base``, so async review
bookkeeping stays a single shared instance. Rule execution order, finding IDs,
severity and blocker aggregation are unchanged.
"""

from __future__ import annotations

import base64  # noqa: F401
import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import tempfile  # noqa: F401
import threading  # noqa: F401
from copy import deepcopy  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Callable  # noqa: F401
from urllib.parse import quote, unquote  # noqa: F401

from lvke_mcp.runtime.storage import (  # noqa: F401
    JSONArtifactStore, canonical_json, paginate_resource_entries, require_safe_id,
    sha256_json, utc_now,
)
from lvke_mcp.runtime.workspace import deliverable_dir, workspace_root  # noqa: F401
from lvke_mcp.servers.lvke_deliverable_review import financial_checks, report_checks, rules  # noqa: F401
from lvke_mcp.servers.lvke_deliverable_review.contracts import (  # noqa: F401
    DEPLOYMENT_MODES, FINDING_STATUSES, SEVERITIES, SEVERITY_ORDER,
    finding_blocks, normalize_project_context, normalize_target,
    require_write_context, verdict_for,
)
from lvke_mcp.servers.lvke_deliverable_review.store import STORE  # noqa: F401

from ._service.base import (  # noqa: F401
    DIMENSION_RESULT_STORE,
    EXPORT_STORE,
    PACKAGE_CONFIG_DIR,
    PREPARATION_STORE,
    REPO_ROOT,
    STANDARD_APPLICABILITY_STORE,
    STANDARD_EVIDENCE_STORE,
    _ASYNC_LOCK,
    _ASYNC_THREADS,
    _REPORT_ARTIFACT_DOMAINS,
    _blocked,
    _classify_retest_operations,
    _finding_coverage_rule_id,
    _finding_match_key,
    _finding_uri,
    _flatten_numbers,
    _gate_difference,
    _message,
    _metrics_uri,
    _next_actions,
    _number,
    _ok,
    _parse_timestamp,
    _review_envelope_status,
    _review_uri,
    _safe_file,
    _severity,
    _shadow_comparison,
    _write,
)
from ._service.disposition import (  # noqa: F401
    _evidence_is_precise,
    _require_open_review,
    _retest_target_scope_matches,
    _successful_retest_closes_finding,
    _target_version_scope,
    disposition_finding,
)
from ._service.events import (  # noqa: F401
    _freshness_reasons,
    _project,
    _project_events,
)
from ._service.executor import (  # noqa: F401
    _execute_rules,
    _preparation_execution_integrity_reasons,
    _run_review,
)
from ._service.export import (  # noqa: F401
    _export_file_uri,
    _export_integrity_reasons,
    _export_record_integrity_reasons,
    _export_resource_uri,
    _export_review_locked,
    _export_root,
    _findings_xlsx,
    _release_export_integrity_reasons,
    _review_docx,
    _review_markdown,
    _write_once_bytes,
    export_review,
    latest_review_for_target,
)
from ._service.finding_rules import (  # noqa: F401
    _CLAIM_PATTERN,
    _EXTERNAL_GAP_CATEGORIES,
    _EXTERNAL_GAP_REASON_MARKERS,
    _FINANCE_WORDS,
    _LOCAL_IMPLEMENTATION_CATEGORIES,
    _LOCAL_IMPLEMENTATION_REASON_MARKERS,
    _acquisition_input_findings,
    _claim_value,
    _document_from_snapshot,
    _existing_issue_findings,
    _expected_report_sections,
    _finance_recalculation_findings,
    _hotel_acquisition_run_findings,
    _professional_rule_finding,
    _project_metadata_findings,
    _report_artifact_text,
    _report_content,
    _report_evidence_packs,
    _report_findings,
    _required_finding_rows,
    _summarize_track_coverage,
)
from ._service.findings_query import (  # noqa: F401
    get_finding,
    list_findings,
)
from ._service.legacy_gate import (  # noqa: F401
    _legacy_blockers,
    _legacy_gate_result,
    _legacy_gate_snapshot,
)
from ._service.lifecycle import (  # noqa: F401
    _resume_async_review_if_needed,
    _run_async_review,
    _schedule_async_review,
    get_review,
    start,
)
from ._service.metrics import (  # noqa: F401
    _metric_percentile,
    _metric_rate,
    _workspace_metrics_payload,
    workspace_metrics,
)
from ._service.preparation import (  # noqa: F401
    _PREPARATION_BASIS_FIELDS,
    _component_preparation,
    _mandatory_findings,
    _preparation_basis,
    _run_from_preparation,
    _standard_basis,
    _verified_preparation_record,
    prepare,
)
from ._service.resources import (  # noqa: F401
    _resource_entry,
    list_resources,
    read_resource,
    resolve_resource,
)
from ._service.retest import (  # noqa: F401
    _append_retest_event_once,
    _find_retest_intent,
    _retest_failure,
    _retest_operation_identity,
    retest,
)
from ._service.standards import (  # noqa: F401
    _resolve_standard_evidence_resource,
    _standard_applicability_record,
    _standard_catalog,
    _standard_evidence_rows,
    _standard_requirement_applicability,
    attach_requirement_evidence,
    list_standard_requirements,
    resolve_standards,
    validate_standards,
)
from ._service.suite_review import (  # noqa: F401
    CHECK_CATALOG,
    confirm_dimension,
    confirm_extraction,
    confirm_package,
    finalize,
    get_dimension,
    prepare_package,
    submit_assessment,
)
from ._service.target_resolve import (  # noqa: F401
    _acquisition_artifact_snapshot,
    _acquisition_run_snapshot,
    _artifact_upstream_bindings,
    _binding_snapshot,
    _combined_bindings_manifest,
    _generic_artifact_snapshot,
    _immutable_artifact_files,
    _linked_generic_report_revision,
    _resolve_report_artifact,
    _resolve_target,
    _string_ids,
)
