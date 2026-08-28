from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


class StandardIntegrityError(ValueError):
    pass


def _load_json(name: str) -> dict[str, Any]:
    path = ROOT / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StandardIntegrityError(f"cannot load NDRC 2023 artifact {name}: {exc}") from exc
    if not isinstance(value, dict):
        raise StandardIntegrityError(f"NDRC 2023 artifact must be an object: {name}")
    return value


def load_standard_manifest() -> dict[str, Any]:
    return _load_json("standard_manifest.json")


def load_clause_tree() -> dict[str, Any]:
    return _load_json("parsed/clauses.json")


def load_generation_mapping() -> dict[str, Any]:
    return _load_json("generation_mapping.json")


def source_fingerprint() -> str:
    value = str(load_clause_tree().get("source_fingerprint") or "")
    if not value.startswith("sha256:") or len(value) != 71:
        raise StandardIntegrityError("NDRC 2023 source fingerprint is missing or invalid")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_source_integrity() -> dict[str, Any]:
    manifest = load_standard_manifest()
    issues: list[str] = []
    checked: list[dict[str, Any]] = []
    records = [
        {"id": "notice", **dict(manifest.get("notice") or {})},
        *[dict(item) for item in manifest.get("documents") or [] if isinstance(item, dict)],
    ]
    for record in records:
        source_id = str(record.get("id") or "")
        path = ROOT / str(record.get("path") or "")
        expected = str(record.get("sha256") or "")
        actual = _sha256(path) if path.is_file() else ""
        item_issues: list[str] = []
        if not path.is_file():
            item_issues.append("source_missing")
        elif actual != expected:
            item_issues.append("source_hash_mismatch")
        issues.extend(f"{source_id}:{issue}" for issue in item_issues)
        checked.append({
            "source_id": source_id,
            "path": str(record.get("path") or ""),
            "expected_sha256": expected,
            "actual_sha256": actual,
            "valid": not item_issues,
            "issues": item_issues,
        })

    clauses = load_clause_tree()
    mapping = load_generation_mapping()
    fingerprint = source_fingerprint()
    if clauses.get("source_valid") is not True:
        issues.append("parsed_clause_sources_invalid")
    if mapping.get("source_fingerprint") != fingerprint:
        issues.append("generation_mapping_source_fingerprint_mismatch")
    return {
        "valid": not issues,
        "standard_id": manifest.get("standard_id"),
        "source_fingerprint": fingerprint,
        "issues": issues,
        "sources": checked,
    }


def generation_basis(project_type: str = "enterprise_investment") -> dict[str, Any]:
    manifest = load_standard_manifest()
    mapping = load_generation_mapping()
    profiles = mapping.get("project_type_profiles") or {}
    profile = profiles.get(project_type)
    selected_type = project_type
    assumptions: list[str] = []
    if not isinstance(profile, dict):
        selected_type = "enterprise_investment"
        profile = profiles.get(selected_type) or {}
        assumptions.append(
            f"unknown project_type={project_type}; enterprise_investment outline used as provisional basis"
        )
    return {
        "generation_standard": manifest.get("standard_id"),
        "generation_standard_version": manifest.get("effective_from"),
        "generation_standard_source_hash": source_fingerprint(),
        "document_no": manifest.get("document_no"),
        "issuer": manifest.get("issuer"),
        "project_type": selected_type,
        "outline_document": profile.get("outline_document"),
        "report_chapters": list(profile.get("report_chapters") or []),
        "finance_clause": profile.get("finance_clause"),
        "finance_requirements": list(mapping.get("finance_requirements") or []),
        "generation_policy": mapping.get("generation_policy"),
        "standard_conformance": "unverified",
        "assumptions": assumptions,
        "quality_issues": [],
    }