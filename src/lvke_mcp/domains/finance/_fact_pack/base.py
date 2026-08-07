"""版本常量、域键、hash 原语与工作区路径。"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


VERSION = "finance_fact_pack.v1"


LEGACY_VERSION = "finance_fact_pack.v0"


SEAL_VERSION = "finance_fact_pack_seal.v2"


LEGACY_SEAL_VERSION = "finance_fact_pack_seal.v1"


DOMAIN_KEYS = (
    "construction_items",
    "products",
    "cost_items",
    "staff_detail",
    "asset_classes",
    "wc_turnover",
    "funding_plan",
    "debt_schedule",
    "amort_bases",
    "distribution_policy",
    "cost_behavior",
    "tax_component_policy",
)


CORE_DOMAIN_KEYS = DOMAIN_KEYS[:9]


POLICY_DOMAIN_KEYS = DOMAIN_KEYS[9:]


EvidenceResolver = Callable[..., dict[str, Any]]


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash_material(pack: dict[str, Any]) -> dict[str, Any]:
    material = copy.deepcopy(pack)
    # Signatures are never part of the content hash material.
    material.pop("fact_pack_hash", None)
    material.pop("seal_mac", None)
    material.pop("seal_id", None)
    return material


def compute_fact_pack_hash(pack: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical(_hash_material(pack)).encode("utf-8")
    ).hexdigest()


def _mcp_data_root() -> Path:
    from lvke_mcp.runtime.workspace import data_root

    return data_root()


def _workspace_root(workspace_id: str) -> Path:
    root = _mcp_data_root() / "workspaces" / str(workspace_id or "local")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []
