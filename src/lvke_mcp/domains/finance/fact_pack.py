"""Authoritative finance_fact_pack.v1 normalization, sealing and projection.

The client may edit candidate facts, but it cannot self-assert evidence grade,
review status or delivery grade.  Confirmation replays every source/evidence
binding against the workspace source store and seals the resulting snapshot.
Only a valid confirmed seal may project facts into the deterministic engine.

Seal v2 is server-only: content hash + HMAC (workspace-local secret) + durable
ledger entry.  Public SHA-256 alone is not enough to forge a formal pack.

Wave 3.3 facade: implementation moved to ``_fact_pack/`` sub-modules — ``base``
(version constants, domain keys, hash primitives and workspace roots), ``seal``
(secret, durable ledger and MAC compute/verify), ``completeness`` (row-level
completeness primitives), ``depth`` (domain depth assessment and fact leaves),
``evidence`` (per-domain evidence support) and ``snapshot`` (snapshot build, v0
migration and confirmed projection).

``_SEAL_LOCK`` lives only in ``seal``; the seal ledger therefore stays guarded by
a single lock instance regardless of which module triggers a write.
"""

from __future__ import annotations

import copy  # noqa: F401
import hashlib  # noqa: F401
import hmac  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import secrets  # noqa: F401
import threading  # noqa: F401
from datetime import datetime  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Callable  # noqa: F401

from ._fact_pack.base import (  # noqa: F401
    CORE_DOMAIN_KEYS,
    DOMAIN_KEYS,
    LEGACY_SEAL_VERSION,
    LEGACY_VERSION,
    POLICY_DOMAIN_KEYS,
    SEAL_VERSION,
    VERSION,
    EvidenceResolver,
    _canonical,
    _hash_material,
    _mcp_data_root,
    _now_iso,
    _record,
    _rows,
    _workspace_root,
    compute_fact_pack_hash,
)
from ._fact_pack.completeness import (  # noqa: F401
    _inventory_complete,
    _nonnegative_present,
    _positive,
    _rows_with_item_ids,
    _stable_item_id,
    _turnover_component_complete,
    _year_sequence_issues,
)
from ._fact_pack.depth import (  # noqa: F401
    _domain_fact_leaves,
    _domain_numeric_anchors,
    assess_domain_depth,
)
from ._fact_pack.evidence import (  # noqa: F401
    _evidence_supports_domain,
    _values_close,
)
from ._fact_pack.seal import (  # noqa: F401
    _SEAL_LOCK,
    _ledger_has_seal,
    _record_seal_ledger,
    _seal_ledger_path,
    _seal_secret,
    compute_fact_pack_mac,
    verify_fact_pack_seal,
)
from ._fact_pack.snapshot import (  # noqa: F401
    _migrate_v0_pack,
    _sort_year_rows,
    build_fact_pack_snapshot,
    project_confirmed_fact_pack,
)
