"""Persistence and artifact orchestration for FinanceSpec v3 acquisition runs.

Wave 3.6 facade: implementation moved to ``_backend/`` sub-modules — ``base``
(global lock, logger, recovery pool, failure messages and number formatting),
``store`` (workspace paths, state IO, idempotency/history/issue records),
``evidence`` (evidence-track detection, reconstruction record validation and
blocking issues), ``specs`` (save/confirm/read spec, decision thresholds),
``runs`` (create/enqueue/execute/read runs), ``scenarios`` (scenario matrix),
``max_price`` (highest acceptable price solving), ``report_data`` (report data and
markdown), ``xlsx`` (minimal workbook writer), ``artifacts`` (artifact generation
and consistency checks), ``downloads`` (artifact read and controlled download) and
``recovery`` (interrupted task recovery).

``_LOCK`` and ``_RECOVERY_POOL`` live only in ``base``, so the state guard and the
recovery thread pool each stay a single instance regardless of caller module.
"""

from __future__ import annotations

import copy  # noqa: F401
import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import math  # noqa: F401
import mimetypes  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import threading  # noqa: F401
import uuid  # noqa: F401
import zipfile  # noqa: F401
from concurrent.futures import ThreadPoolExecutor  # noqa: F401
from contextlib import contextmanager  # noqa: F401
from collections.abc import Mapping  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from itertools import product  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401
from xml.etree import ElementTree as ET  # noqa: F401

from filelock import FileLock  # noqa: F401

from lvke_mcp.runtime.workspace import data_root, deliverable_dir, workspace_root  # noqa: F401
from lvke_mcp.domains.asset_acquisition.model import (  # noqa: F401
    INDEPENDENT_SCENARIO_FIELDS,
    AcquisitionModelError,
    apply_scenario,
    run_acquisition_model,
    solve_max_acquisition_price,
)
from lvke_mcp.domains.finance.spec import (  # noqa: F401
    LATEST_SPEC_VERSION,
    mark_spec_confirmed,
    validate,
    validate_for_formal,
)

from ._backend.artifacts import (  # noqa: F401
    _bind_succeeded_artifact,
    _check_artifact_consistency,
    enqueue_artifact,
    execute_queued_artifact,
    generate_artifacts,
)
from ._backend.base import (  # noqa: F401
    _ARTIFACT_GENERATION_FAILURE_MESSAGE,
    _DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    _LOCK,
    _LOG,
    _RECOVERY_POOL,
    _RUN_EXECUTION_FAILURE_MESSAGE,
    _RUN_VALIDATION_FAILURE_MESSAGE,
    _SOURCE_EVIDENCE_FAILURE_MESSAGE,
    _UNTRUSTED_EVIDENCE_ASSERTION_KEYS,
    _hash,
    _idempotency_ttl_seconds,
    _now,
    _num,
    _pct,
    _pct_ratio,
    _same_number,
    _same_optional_number,
)
from ._backend.downloads import (  # noqa: F401
    _artifact_filename_is_safe,
    _artifact_media_type,
    _read_resolved_artifact,
    _resolve_artifact_download,
    get_artifact,
    list_artifacts,
    read_artifact_candidate_download,
    read_artifact_download,
    resolve_artifact_candidate_download,
    resolve_artifact_download,
)
from ._backend.evidence import (  # noqa: F401
    PROCESS_ACCEPTANCE_BASIS_FIELDS,
    RECONSTRUCTION_RECORD_FIELDS,
    _bind_spec_evidence,
    _current_evidence_matches_run,
    _evidence_blocking_issues,
    _evidence_error_strings,
    _formal_assessment,
    _is_estimate_preview_spec,
    _is_process_acceptance_spec,
    _record_gaps,
    _sanitize_client_evidence_claims,
    _valid_process_acceptance_basis,
    _valid_reconstruction_records,
    assess_spec_evidence,
    process_acceptance_gaps,
    sanitize_spec_input,
)
from ._backend.max_price import (  # noqa: F401
    _diff_is_blocking,
    max_price,
)
from ._backend.recovery import (  # noqa: F401
    _recover_published_artifact,
    recover_incomplete_acquisition_tasks,
)
from ._backend.report_data import (  # noqa: F401
    build_acquisition_report_data,
    render_markdown,
)
from ._backend.runs import (  # noqa: F401
    _is_selected_scenario,
    create_run,
    enqueue_run,
    execute_queued_run,
    get_run,
    list_runs,
)
from ._backend.scenarios import (  # noqa: F401
    MAX_SCENARIO_MATRIX_COMBINATIONS,
    create_scenario_matrix,
    get_scenario_matrix,
    list_scenario_matrices,
)
from ._backend.specs import (  # noqa: F401
    _decision_thresholds,
    _max_price_validation,
    confirm_saved_spec,
    get_spec,
    list_specs,
    save_spec,
)
from ._backend.store import (  # noqa: F401
    _active_idempotency_record,
    _artifacts_root,
    _close_issue,
    _history_event,
    _idempotency_record,
    _load,
    _migration_binding,
    _open_issue,
    _root,
    _save,
    _state_guard,
    _state_path,
    _workspace_ids,
)
from ._backend.xlsx import (  # noqa: F401
    _file_hash,
    _sheet_xml,
    _write_minimal_xlsx,
    _xlsx_col,
    _xlsx_summary_values,
    _xml_cell,
)
