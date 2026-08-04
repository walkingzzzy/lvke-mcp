"""Industry finance profile registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_ROOT = Path(__file__).resolve().parent / "config"
DEFAULT_INDUSTRY_DIR = _ROOT / "finance_industries"


class IndustryRegistryError(ValueError):
    pass


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise IndustryRegistryError(f"cannot read industry profile {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise IndustryRegistryError(f"industry profile must be an object: {path}")
    return value


def list_industry_profiles(directory: Path | None = None) -> list[dict[str, Any]]:
    base = directory or DEFAULT_INDUSTRY_DIR
    if not base.is_dir():
        return []
    return [_read_yaml(path) for path in sorted(base.glob("*.yaml"))]


def load_industry_profile(version: str, directory: Path | None = None) -> dict[str, Any]:
    for profile in list_industry_profiles(directory):
        if profile.get("version") == version:
            return profile
    raise IndustryRegistryError(f"unknown finance industry profile: {version}")


def select_industry_profile(industry: str, directory: Path | None = None) -> dict[str, Any]:
    text = str(industry or "").strip().lower()
    profiles = list_industry_profiles(directory)
    exact = [p for p in profiles if str(p.get("industry") or "").lower() == text]
    if len(exact) == 1:
        return exact[0]
    matches: list[tuple[int, dict[str, Any]]] = []
    for profile in profiles:
        aliases = [str(x).lower() for x in (profile.get("aliases") or [])]
        matched_aliases = [alias for alias in aliases if text and alias and alias in text]
        if matched_aliases:
            # Prefer the most specific alias.  For example, ``工业互联网`` must
            # select the software profile instead of becoming ambiguous with
            # the manufacturing alias ``工业``.
            matches.append((max(len(alias) for alias in matched_aliases), profile))
    if matches:
        best_length = max(length for length, _profile in matches)
        best = [profile for length, profile in matches if length == best_length]
        if len(best) == 1:
            return best[0]
        raise IndustryRegistryError(
            f"ambiguous finance industry profile for {industry}: "
            f"{[p.get('version') for p in best]}"
        )
    general = [p for p in profiles if p.get("version") == "general.v1"]
    if general:
        return general[0]
    raise IndustryRegistryError(f"no finance industry profile for: {industry}")
