"""Deterministic evidence assembly over acquisition snapshots and source files.

Wave 1.1 facade: Implementation moved to _service/ sub-modules. This module
preserves all public symbols via explicit re-export for backward compatibility.
"""

from __future__ import annotations

# Preserve incidental imports that became public API surface
import re  # noqa: F401
from datetime import datetime  # noqa: F401
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP  # noqa: F401
from typing import Any  # noqa: F401

# Preserve store constants
from lvke_mcp.adapters.data_acquisition_repository import SOURCE_STORE  # noqa: F401
from lvke_mcp.adapters.data_analysis_repository import (
    BENCHMARK_COMPARISON_STORE,  # noqa: F401
    CANDIDATE_STORE,  # noqa: F401
    EVIDENCE_STORE,  # noqa: F401
    FINANCIAL_TREND_STORE,  # noqa: F401
    INGEST_STORE,  # noqa: F401
    NORMALIZED_COMPARE_STORE,  # noqa: F401
    PROFILE_STORE,  # noqa: F401
    RESOURCE_STORES,  # noqa: F401
)
from lvke_mcp.runtime.source_reconstruction import (
    SOURCE_RECONSTRUCTED,  # noqa: F401
    normalize_reconstruction,  # noqa: F401
    validate_reconstruction_records,  # noqa: F401
)
from lvke_mcp.runtime.storage import (
    paginate_resource_entries,  # noqa: F401
    sha256_json,  # noqa: F401
)

# Re-export implementation from sub-modules
from ._service.benchmark import compare_benchmark
from ._service.candidate_extract import extract_candidates
from ._service.compare import compare, normalize_compare
from ._service.evidence_pack import (
    EVIDENCE_TRACKS,
    build_evidence_pack,
    _validate_fixture_manifest,
)
from ._service.ingest import ingest, query, status
from ._service.period_norm import normalize_financial_period
from ._service.profile import profile_tabular
from ._service.resources import list_resources, resolve_resource
from ._service.trends import financial_trends
from ._service.unit_rules import CONTROLLED_UNIT_RULES, controlled_unit_rules

# Preserve alias for repository function
from lvke_mcp.adapters.data_analysis_repository import (
    resolve_resource as resolve_repository_resource,  # noqa: F401
)

__all__ = [
    # Public functions
    "build_evidence_pack",
    "compare",
    "compare_benchmark",
    "controlled_unit_rules",
    "extract_candidates",
    "financial_trends",
    "ingest",
    "list_resources",
    "normalize_compare",
    "normalize_financial_period",
    "profile_tabular",
    "query",
    "resolve_resource",
    "status",
    # Constants
    "CONTROLLED_UNIT_RULES",
    "EVIDENCE_TRACKS",
    "SOURCE_RECONSTRUCTED",
    # Store objects
    "BENCHMARK_COMPARISON_STORE",
    "CANDIDATE_STORE",
    "EVIDENCE_STORE",
    "FINANCIAL_TREND_STORE",
    "INGEST_STORE",
    "NORMALIZED_COMPARE_STORE",
    "PROFILE_STORE",
    "RESOURCE_STORES",
    "SOURCE_STORE",
    # Incidental imports that became public
    "Any",
    "Decimal",
    "InvalidOperation",
    "ROUND_HALF_UP",
    "datetime",
    "normalize_reconstruction",
    "paginate_resource_entries",
    "re",
    "resolve_repository_resource",
    "sha256_json",
    "validate_reconstruction_records",
    # Private helper kept for _validate_fixture_manifest sole internal caller
    "_validate_fixture_manifest",
]
