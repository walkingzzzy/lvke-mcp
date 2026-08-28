"""Immutable state and intent parsing for zero-material delivery orchestration.

Wave 2.4 facade: implementation moved to ``_service/`` sub-modules —
``base`` (artifact stores, stage table, envelope/idempotency foundation),
``routing`` (sentence to industry route, run records), ``intake``
(``create_from_sentence``), ``assumptions`` (assumption package),
``finance_align`` (finance-basis reconciliation), ``orchestration``
(cross-domain execution) and ``lifecycle`` (delivery routes and resources).

All nine ``JSONArtifactStore`` objects live in ``base`` and are re-exported from
here, so there is exactly one instance of each backing the same on-disk state.
The fourteen in-function cross-domain imports stay inside their function bodies;
hoisting any of them to module level would create an import cycle with the
finance, research, planning and reports domains.
"""

from __future__ import annotations

import base64  # noqa: F401
import hashlib  # noqa: F401
import hmac  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
from copy import deepcopy  # noqa: F401
from datetime import date  # noqa: F401
from typing import Any, Callable  # noqa: F401

from filelock import FileLock  # noqa: F401
from lvke_mcp.runtime.workspace import workspace_root  # noqa: F401

from lvke_mcp.runtime.storage import (  # noqa: F401
    JSONArtifactStore,
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
)

from ._service.base import (  # noqa: F401
    ASSUMPTION_PROFILE_VERSION,
    ASSUMPTION_REGISTER_STORE,
    ASSUMPTION_STORE,
    EVIDENCE_MANIFEST_STORE,
    GAP_REGISTER_STORE,
    IDEMPOTENCY_STORE,
    INTENT_STORE,
    MANIFEST_STORE,
    PROMOTION_STORE,
    REPORT_STORE,
    RUN_STORE,
    TEMPLATE_PACK_STORE,
    SERVICE_NAME,
    SERVICE_VERSION,
    _ACTIVE_STAGES,
    _RESOURCE_STORES,
    _blocked,
    _envelope,
    _idempotency_lock,
    _idempotent_mutation,
    _view,
)
from ._service.assumptions import (  # noqa: F401
    _assumption_field,
    _build_assumption_package,
    _field_values,
)
from ._service.finance_align import (  # noqa: F401
    _apply_revenue_target,
    _effective_revenue_target,
    _reconcile_funding,
    _scale_investment_breakdown,
    _scenario_inputs,
    _sync_working_capital,
)
from ._service.intake import create_from_sentence  # noqa: F401
from ._service.promotion import (  # noqa: F401
    confirm_formal_promotion,
    generate_template_pack,
)
from ._service.lifecycle import (  # noqa: F401
    _stage_progress,
    _transition_control,
    cancel,
    confirm_assumptions,
    get_artifacts,
    get_delivery,
    list_assumptions,
    list_resources,
    read_resource,
    resolve_resource,
    resume,
    start,
    status,
)
from ._service.orchestration import (  # noqa: F401
    _create_project_context,
    _start_research,
    execute,
)
from ._service.routing import (  # noqa: F401
    _new_run,
    _planned_run_id,
    _project_name,
    _resolve_route,
)