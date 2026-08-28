"""国家发改委 2023 可研大纲的生成基线与后置覆盖投影。"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from lvke_mcp.standards.ndrc_feasibility_2023 import (
    generation_basis as official_generation_basis,
    load_generation_mapping,
    load_standard_manifest,
    source_fingerprint,
)


_MANIFEST = load_standard_manifest()
STANDARD_ID = str(_MANIFEST["standard_id"])
DOCUMENT_NO = str(_MANIFEST["document_no"])
STANDARD_VERSION = str(_MANIFEST["effective_from"])
SOURCE_URL = str((_MANIFEST.get("notice") or {}).get("url") or "")


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _project_type(invest_type: str) -> str:
    value = str(invest_type or "").lower()
    if "capital" in value or "资本金" in value:
        return "government_capital_injection"
    if any(token in value for token in ("政府", "government")):
        return "government_direct_investment"
    return "enterprise_investment"


def select_profile(invest_type: str = "") -> str:
    return "government" if _project_type(invest_type).startswith("government_") else "enterprise"


def generation_baseline(*, invest_type: str = "") -> dict[str, Any]:
    project_type = _project_type(invest_type)
    basis = official_generation_basis(project_type)
    mapping = load_generation_mapping()
    return {
        **basis,
        "standard_id": basis["generation_standard"],
        "standard_version": basis["generation_standard_version"],
        "source_hash": basis["generation_standard_source_hash"],
        "mapping_hash": _stable_hash(mapping),
        "source_url": SOURCE_URL,
        "profile": select_profile(invest_type),
        "role": "generation_baseline",
        "requirements": basis["finance_requirements"],
    }


def standard_stamp(*, invest_type: str = "") -> dict[str, Any]:
    """Compact immutable identity carried by FinanceSpec and FinanceRun."""

    baseline = generation_baseline(invest_type=invest_type)
    return {
        "standard_id": baseline["standard_id"],
        "standard_version": baseline["standard_version"],
        "source_url": baseline["source_url"],
        "source_hash": baseline["source_hash"],
        "mapping_hash": baseline["mapping_hash"],
        "project_type": baseline["project_type"],
        "profile": baseline["profile"],
        "generation_policy": baseline["generation_policy"],
        "standard_conformance": "unverified",
        "finance_requirement_ids": [
            item["requirement_id"] for item in baseline["requirements"]
        ],
    }


def stamp_finance_spec(
    spec: dict[str, Any] | None,
    *,
    invest_type: str = "",
) -> dict[str, Any] | None:
    """Return a stamped copy without overwriting caller-owned source data."""

    if not isinstance(spec, dict):
        return None
    stamped = copy.deepcopy(spec)
    stamped["generation_standard"] = standard_stamp(invest_type=invest_type)
    return stamped


def _path_value(inputs: dict[str, Any], path: str) -> Any:
    parts = path.split(".")
    if parts and parts[0] == "finance":
        parts = parts[1:]
    value: Any = inputs
    for part in parts:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def coverage_snapshot(
    *,
    finance_inputs: dict[str, Any] | None = None,
    table_manifest: list[dict[str, Any]] | None = None,
    report_sections: list[str] | None = None,
    invest_type: str = "",
) -> dict[str, Any]:
    """Project coverage after generation; gaps never prevent generating artifacts."""

    baseline = generation_baseline(invest_type=invest_type)
    inputs = finance_inputs or {}
    table_codes = {
        str(item.get("table_code") or item.get("table_id") or "")
        for item in table_manifest or []
        if isinstance(item, dict)
    }
    sections = {str(item) for item in report_sections or [] if str(item)}
    rows: list[dict[str, Any]] = []
    for requirement in baseline["requirements"]:
        expected_paths = tuple(requirement.get("finance_spec_paths") or ())
        expected_tables = tuple(requirement.get("delivery_tables") or ())
        expected_sections = tuple(requirement.get("report_sections") or ())
        present_inputs = [
            path for path in expected_paths
            if _path_value(inputs, path) not in (None, "", [], {})
        ]
        present_tables = [code for code in expected_tables if code in table_codes]
        present_sections = [title for title in expected_sections if title in sections]
        has_output = bool(present_inputs or present_tables or present_sections)
        complete = (
            (not expected_paths or len(present_inputs) == len(expected_paths))
            and (not expected_tables or len(present_tables) == len(expected_tables))
            and (not sections or len(present_sections) == len(expected_sections))
            and not requirement.get("known_gap")
        )
        status = "conformant" if complete and has_output else (
            "partial" if has_output else "unverified"
        )
        rows.append({
            "requirement_id": requirement["requirement_id"],
            "title": requirement["title"],
            "status": status,
            "source_clauses": list(requirement.get("source_clauses") or []),
            "finance_paths_present": present_inputs,
            "finance_paths_missing": [path for path in expected_paths if path not in present_inputs],
            "table_codes_present": present_tables,
            "table_codes_missing": [code for code in expected_tables if code not in present_tables],
            "report_sections_present": present_sections,
            "report_sections_missing": [title for title in expected_sections if title not in present_sections],
            "known_gap": requirement.get("known_gap"),
        })
    statuses = {row["status"] for row in rows}
    overall = "conformant" if statuses == {"conformant"} else (
        "partial" if statuses - {"unverified"} else "unverified"
    )
    return {
        "standard_id": STANDARD_ID,
        "standard_version": STANDARD_VERSION,
        "source_hash": source_fingerprint(),
        "mapping_hash": baseline["mapping_hash"],
        "profile": baseline["profile"],
        "project_type": baseline["project_type"],
        "status": overall,
        "generated_against_standard": True,
        "validation_stage": "post_generation",
        "requirements": rows,
    }