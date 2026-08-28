"""Application use cases for immutable finance specifications and runs.

Wave 3.3 facade: implementation moved to ``_model_application/`` sub-modules —
``base`` (service name, logger, workspace/URI primitives, idempotency records and
the ok/err/exception envelopes), ``fact_pack_cases`` (prepare/confirm/get fact
pack), ``spec_cases`` (prepare/confirm/validate spec plus candidate-input
canonicalization) and ``run_cases`` (``run_model`` and ``get_run``).

The grouping follows transaction boundaries: each use case keeps its own
idempotency key handling and envelope shape unchanged.
"""

from __future__ import annotations

from typing import Any  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import time  # noqa: F401
import uuid  # noqa: F401
from datetime import datetime, timedelta, timezone  # noqa: F401

from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE  # noqa: F401
from lvke_mcp.adapters.finance_model_repository import (  # noqa: F401
    BASIS_OF_ESTIMATE_STORE,
    FACT_PACK_STORE,
    IDEMPOTENCY_STORE,
    SPEC_STORE,
)
from lvke_mcp.domains.finance.parameter_resolver import (  # noqa: F401
    canonicalize_finance_inputs,
    finance_input_schema,
)
from lvke_mcp.runtime.logging import get_logger  # noqa: F401
from lvke_mcp.runtime.responses import err, ok  # noqa: F401
from lvke_mcp.runtime.storage import sha256_json  # noqa: F401

from ._model_application.base import (  # noqa: F401
    SERVER_NAME,
    _active_idempotency_record,
    _blocked_run,
    _blocking_rules,
    _err_env,
    _exception_env,
    _expires_at,
    _finalize,
    _latest_formal_boe,
    _missing_run,
    _ok_env,
    _run_uri,
    _str_list,
    _unique_strings,
    _workspace_id,
    logger,
)
from ._model_application.fact_pack_cases import (  # noqa: F401
    _fact_pack_result,
    confirm_fact_pack,
    get_fact_pack,
    prepare_fact_pack,
)
from ._model_application.run_cases import (  # noqa: F401
    get_run,
    run_model,
)
from ._model_application.spec_cases import (  # noqa: F401
    _canonical_candidate_inputs,
    _revenue_input_complete,
    confirm_spec,
    prepare_spec,
    validate_spec,
)
from .post_generation_validation import validate_post_generation  # noqa: F401
from .formal_upgrade import promote_to_formal  # noqa: F401
