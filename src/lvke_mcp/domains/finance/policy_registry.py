"""Finance policy profile registry with effective-date selection."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml


_ROOT = Path(__file__).resolve().parent / "config"
DEFAULT_POLICY_DIR = _ROOT / "finance_policies"


class PolicyRegistryError(ValueError):
    pass


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise PolicyRegistryError(f"cannot read policy profile {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PolicyRegistryError(f"policy profile must be an object: {path}")
    return value


def list_policy_profiles(directory: Path | None = None) -> list[dict[str, Any]]:
    base = directory or DEFAULT_POLICY_DIR
    if not base.is_dir():
        return []
    profiles = [_read_yaml(path) for path in sorted(base.glob("*.yaml"))]
    return sorted(profiles, key=lambda item: str(item.get("effective_from") or ""))


def load_policy_profile(version: str, directory: Path | None = None) -> dict[str, Any]:
    for profile in list_policy_profiles(directory):
        if profile.get("version") == version:
            return profile
    raise PolicyRegistryError(f"unknown finance policy version: {version}")


def select_policy_profile(
    *,
    as_of: str | date,
    jurisdiction: str = "CN",
    directory: Path | None = None,
) -> dict[str, Any]:
    target = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    candidates: list[dict[str, Any]] = []
    for profile in list_policy_profiles(directory):
        if str(profile.get("jurisdiction") or "") != jurisdiction:
            continue
        start = date.fromisoformat(str(profile.get("effective_from")))
        end_raw = profile.get("effective_to")
        end = date.fromisoformat(str(end_raw)) if end_raw else None
        if start <= target and (end is None or target <= end):
            candidates.append(profile)
    if not candidates:
        raise PolicyRegistryError(
            f"no active finance policy for jurisdiction={jurisdiction}, as_of={target.isoformat()}"
        )
    if len(candidates) > 1:
        versions = [str(item.get("version")) for item in candidates]
        raise PolicyRegistryError(f"overlapping active finance policies: {versions}")
    return candidates[0]


def policy_parameter(
    profile: dict[str, Any], section: str, name: str,
) -> dict[str, Any] | None:
    value = ((profile.get(section) or {}).get(name))
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    return {"value": value, "source": "policy_profile"}

