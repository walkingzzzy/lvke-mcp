"""Contracts for evidence reconstructed from supplied project materials.

``source_reconstructed`` is deliberately separate from ``real``.  It records
that a value was replayed or mapped from a supplied report/template/history
file, without claiming that the unavailable original BoE was supplied.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

SOURCE_RECONSTRUCTED = "source_reconstructed"
RECONSTRUCTION_SOURCE_KINDS = {
    "client_report",
    "finance_template",
    "historical_statement",
    "scenario_note",
}
RECONSTRUCTION_METHODS = {"table_extract", "formula_replay", "explicit_mapping"}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def reconstruction_errors(value: Mapping[str, Any] | None) -> list[str]:
    """Return deterministic missing/invalid fields for one reconstruction row."""

    row = value if isinstance(value, Mapping) else {}
    required = (
        "reconstruction_id",
        "source_uri",
        "content_hash",
        "locator",
        "source_kind",
        "method",
        "original_formula_available",
        "limitations",
    )
    errors = [
        f"{field}_required"
        for field in required
        if row.get(field) in (None, "")
    ]
    if row.get("content_hash") not in (None, "") and not _HASH_RE.fullmatch(str(row.get("content_hash"))):
        errors.append("content_hash_invalid")
    if row.get("source_kind") not in (None, "") and str(row.get("source_kind")) not in RECONSTRUCTION_SOURCE_KINDS:
        errors.append("source_kind_invalid")
    if row.get("method") not in (None, "") and str(row.get("method")) not in RECONSTRUCTION_METHODS:
        errors.append("reconstruction_method_invalid")
    if row.get("original_formula_available") not in (True, False):
        errors.append("original_formula_available_required")
    if row.get("limitations") not in (None, "") and not isinstance(row.get("limitations"), list):
        errors.append("limitations_must_be_array")
    return sorted(set(errors))


def normalize_reconstruction(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a reconstruction record without inventing omitted values."""

    row = dict(value)
    if row.get("content_hash") and not str(row["content_hash"]).startswith("sha256:"):
        row["content_hash"] = f"sha256:{row['content_hash']}"
    if "limitations" in row and isinstance(row["limitations"], list):
        row["limitations"] = [str(item) for item in row["limitations"]]
    return row


def validate_reconstruction_records(records: Any) -> list[dict[str, Any]]:
    """Validate and return structured errors for an array of reconstruction rows."""

    if not isinstance(records, list) or not records:
        return [{"index": None, "code": "reconstruction_records_required"}]
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(records):
        for code in reconstruction_errors(row if isinstance(row, Mapping) else None):
            errors.append({"index": index, "code": code})
    return errors


def is_reconstructed_source(value: Any) -> bool:
    return isinstance(value, Mapping) and (
        str(value.get("evidence_track") or value.get("evidence_eligibility") or value.get("source_type") or "")
        == SOURCE_RECONSTRUCTED
    )
