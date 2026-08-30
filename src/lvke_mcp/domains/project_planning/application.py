"""Immutable ProjectContext and InputApplicability lifecycle.

Wave 2.2 facade: Implementation moved to _service/ sub-modules grouped by
business object. This module preserves all public symbols (including the
incidental import names and store constants) via explicit re-export so that
``lvke_mcp.servers.lvke_project_planning.service`` star-import and lifecycle
``service._xxx`` attribute access keep working unchanged.
"""

from __future__ import annotations

# Preserve incidental imports that became public API surface
import hashlib  # noqa: F401
import json  # noqa: F401
from pathlib import Path  # noqa: F401
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP  # noqa: F401
from typing import Any, Callable  # noqa: F401

from filelock import FileLock  # noqa: F401
from lvke_mcp.runtime.workspace import workspace_root  # noqa: F401

from lvke_mcp.runtime.storage import (  # noqa: F401
    canonical_json,
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
)
from lvke_mcp.adapters.project_planning_repository import (  # noqa: F401
    BUILD_SCALE_STORE,
    COST_DRIVER_STORE,
    IDEMPOTENCY_STORE,
    INPUT_APPLICABILITY_STORE,
    LABOR_PLAN_STORE,
    MARKET_CASE_STORE,
    OPTION_COMPARISON_STORE,
    POLICY_BASIS_STORE,
    PROJECT_CONTEXT_STORE,
    RESOURCE_STORES as _RESOURCE_STORES,
    REVENUE_DRIVER_STORE,
)

# Re-export implementation from sub-modules
from ._service.base import (
    _applicability_view,
    _blocked,
    _contains_object_id,
    _context_view,
    _decimal,
    _downstream_stale,
    _envelope,
    _idempotency_lock,
    _idempotent_mutation,
    _market_view,
    _planning_view,
    _planning_evidence_qualification,
)
from ._service.context import (
    create_project_context,
    get_project_context,
    list_project_contexts,
    resolve_industry_skill,
    revise_project_context,
    validate_project_context,
)
from ._service.market import (
    _confirmed_market_basis,
    compare_market_cases,
    confirm_market_case,
    get_market_case,
    prepare_market_case,
    validate_market_case,
)
from ._service.factories import (
    create_build_scale_case,
    create_cost_driver_set,
    create_labor_plan,
    create_revenue_driver_set,
)
from ._service.options import (
    confirm_option_selection,
    prepare_option_comparison,
)
from ._service.query import (
    get_planning_object,
    list_resources,
    read_resource,
    resolve_resource,
)

__all__ = [
    "Any",
    "BUILD_SCALE_STORE",
    "COST_DRIVER_STORE",
    "Callable",
    "Decimal",
    "FileLock",
    "IDEMPOTENCY_STORE",
    "INPUT_APPLICABILITY_STORE",
    "InvalidOperation",
    "LABOR_PLAN_STORE",
    "MARKET_CASE_STORE",
    "OPTION_COMPARISON_STORE",
    "POLICY_BASIS_STORE",
    "PROJECT_CONTEXT_STORE",
    "Path",
    "REVENUE_DRIVER_STORE",
    "ROUND_HALF_UP",
    "_RESOURCE_STORES",
    "_applicability_view",
    "_blocked",
    "_confirmed_market_basis",
    "_contains_object_id",
    "_context_view",
    "_decimal",
    "_downstream_stale",
    "_envelope",
    "_idempotency_lock",
    "_idempotent_mutation",
    "_market_view",
    "_planning_evidence_qualification",
    "_planning_view",
    "annotations",
    "canonical_json",
    "compare_market_cases",
    "confirm_market_case",
    "confirm_option_selection",
    "create_build_scale_case",
    "create_cost_driver_set",
    "create_labor_plan",
    "create_project_context",
    "create_revenue_driver_set",
    "get_market_case",
    "get_planning_object",
    "get_project_context",
    "hashlib",
    "json",
    "list_project_contexts",
    "list_resources",
    "paginate_resource_entries",
    "prepare_market_case",
    "prepare_option_comparison",
    "read_resource",
    "require_safe_id",
    "resolve_industry_skill",
    "resolve_resource",
    "revise_project_context",
    "sha256_json",
    "validate_market_case",
    "validate_project_context",
    "workspace_root",
]
