"""Deterministic report and finance/report consistency checks.

Wave 2.9 facade: implementation moved to ``_report_checks/`` sub-modules —
``patterns`` (rule registry and static pattern tables), ``normalize`` (number,
unit and semantic comparison primitives), ``claims`` (semantic finance index and
claim graph), ``evidence`` (evidence catalog and claim/evidence matching per
evidence track), ``structure`` (section, reference and internal consistency
groups), ``hotel`` (hotel lease rules), ``mineral`` (mineral processing contract
rules) and ``entry`` (``review_report`` / ``review_combined``).

The grouping follows the existing call order inside the two entry points, so
finding IDs, severity and blocker aggregation order are unchanged.
"""

from __future__ import annotations

import re  # noqa: F401
from calendar import monthrange  # noqa: F401
from copy import deepcopy  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from typing import Any, Iterable  # noqa: F401

from lvke_mcp.runtime.storage import sha256_json  # noqa: F401
from lvke_mcp.servers.lvke_deliverable_review import rules  # noqa: F401

from ._report_checks.claims import (  # noqa: F401
    _claim_run_matches,
    build_claim_graph,
    semantic_finance_index,
)
from ._report_checks.entry import (  # noqa: F401
    review_combined,
    review_report,
)
from ._report_checks.evidence import (  # noqa: F401
    _candidate_location,
    _candidate_matches_claim,
    _candidate_metric,
    _candidate_scope,
    _claim_evidence,
    _evidence_catalog,
    _evidence_claims,
    _formal_evidence_candidate,
    _formal_evidence_source,
    _parse_datetime,
    _source_reconstructed_candidate,
    _source_timestamp,
    _technical_fixture_candidate,
)
from ._report_checks.hotel import (  # noqa: F401
    _LEASE_DATE_PATTERN,
    _LEASE_DATE_TEXT,
    _LEASE_ENTITY_PATTERN,
    _MONEY_PATTERN,
    _candidate_context,
    _candidate_text,
    _distinct_money_values,
    _evidence_has_term,
    _hotel_findings,
    _lease_date,
    _lease_end_dates,
    _lease_scoped_texts,
    _lease_term_flags,
    _money_values,
)
from ._report_checks.mineral import (  # noqa: F401
    _CONTRACT_MENTION_PATTERN,
    _CONTRACT_QUANTITY_PATTERN,
    _contract_candidate_field,
    _contract_evidence_value,
    _contract_party_role,
    _contract_reference,
    _contract_value_matches,
    _formal_contract_evidence,
    _mineral_findings,
    _normalized_company,
    _report_contract_values,
)
from ._report_checks.normalize import (  # noqa: F401
    _canonical_unit,
    _canonical_value,
    _flatten_numbers,
    _metric_unit_compatible,
    _number,
    _period_near,
    _semantic,
    _semantic_near,
    _within_tolerance,
)
from ._report_checks.patterns import (  # noqa: F401
    COMBINED_RULES,
    REPORT_RULES,
    _COMPANY_PATTERN,
    _DEFAULT_SECTION_GROUPS,
    _FINANCIAL_METRICS,
    _METRIC_PATTERNS,
    _NUMBER_PATTERN,
    _PATH_PATTERNS,
    _PERIOD_PATTERN,
)
from ._report_checks.structure import (  # noqa: F401
    _headings,
    _internal_consistency_findings,
    _normalize_heading,
    _reference_findings,
    _required_section_findings,
)
