"""Versioned finance model manifest.

The manifest freezes every governed component that can change a finance run.
It is deliberately independent from workspace IO and model arithmetic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from typing import Any

from lvke_mcp.standards.ndrc_feasibility_2023 import source_fingerprint


MANIFEST_VERSION = "finance_manifest.v1"
# Single source of truth for the active finance model/template versions.
# run_service re-exports these so the engine, the frozen manifest and the
# public /api/finance-model-versions endpoint cannot report divergent versions.
DEFAULT_MODEL_VERSION = "finance_model.v2.4"
DEFAULT_SPEC_SCHEMA_VERSION = "finance_spec.v2"
DEFAULT_POLICY_VERSION = "cn_tax_policy.2026-01"
DEFAULT_TEMPLATE_VERSION = "finance_tables.v3"
DEFAULT_GATE_VERSION = "finance_gate.v2"
DEFAULT_GENERATION_STANDARD = "ndrc-feasibility-outline-2023"
DEFAULT_GENERATION_STANDARD_VERSION = "2023-05-01"
DEFAULT_GENERATION_STANDARD_SOURCE_HASH = source_fingerprint()


def _stable_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )


@dataclass(frozen=True)
class ModelManifest:
    manifest_version: str = MANIFEST_VERSION
    model_version: str = DEFAULT_MODEL_VERSION
    spec_schema_version: str = DEFAULT_SPEC_SCHEMA_VERSION
    policy_version: str = DEFAULT_POLICY_VERSION
    industry_profile_version: str = "general.v1"
    template_version: str = DEFAULT_TEMPLATE_VERSION
    gate_version: str = DEFAULT_GATE_VERSION
    generation_standard: str = DEFAULT_GENERATION_STANDARD
    generation_standard_version: str = DEFAULT_GENERATION_STANDARD_VERSION
    generation_standard_source_hash: str = DEFAULT_GENERATION_STANDARD_SOURCE_HASH
    effective_from: str = "2026-01-01"
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def hash(self) -> str:
        raw = _stable_json(self.to_dict()).encode("utf-8")
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    def validate(self, *, as_of: str | date | None = None) -> list[str]:
        errors: list[str] = []
        if self.status != "active":
            errors.append(f"manifest status is {self.status}, expected active")
        for field_name, value in self.to_dict().items():
            if field_name != "status" and not str(value or "").strip():
                errors.append(f"manifest field is empty: {field_name}")
        target = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
        if target and date.fromisoformat(self.effective_from) > target:
            errors.append(
                f"manifest effective_from={self.effective_from} is after valuation date={target.isoformat()}"
            )
        return errors


def build_manifest(
    *,
    industry_profile_version: str = "general.v1",
    policy_version: str = DEFAULT_POLICY_VERSION,
    model_version: str = DEFAULT_MODEL_VERSION,
    spec_schema_version: str = DEFAULT_SPEC_SCHEMA_VERSION,
    template_version: str = DEFAULT_TEMPLATE_VERSION,
    gate_version: str = DEFAULT_GATE_VERSION,
    generation_standard: str = DEFAULT_GENERATION_STANDARD,
    generation_standard_version: str = DEFAULT_GENERATION_STANDARD_VERSION,
    generation_standard_source_hash: str = DEFAULT_GENERATION_STANDARD_SOURCE_HASH,
    effective_from: str = "2026-01-01",
) -> ModelManifest:
    return ModelManifest(
        model_version=model_version,
        spec_schema_version=spec_schema_version,
        policy_version=policy_version,
        industry_profile_version=industry_profile_version,
        template_version=template_version,
        gate_version=gate_version,
        generation_standard=generation_standard,
        generation_standard_version=generation_standard_version,
        generation_standard_source_hash=generation_standard_source_hash,
        effective_from=effective_from,
    )


def manifest_from_dict(value: dict[str, Any] | None) -> ModelManifest:
    data = dict(value or {})
    allowed = set(ModelManifest.__dataclass_fields__)
    return ModelManifest(**{key: val for key, val in data.items() if key in allowed})
