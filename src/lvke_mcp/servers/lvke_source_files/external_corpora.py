"""Resolve Git-external Lvke corpora through a fail-closed logical catalog.

Path base note (MCP independence, §29.4): this module no longer anchors
relative ``LVKE_EXTERNAL_CORPUS_*`` values at the host repository root.
It anchors them at the MCP-owned configuration directory
(``LVKE_MCP_CONFIG_DIR``, else ``<data_dir>/config``), and the default
manifest now lives at ``<config_dir>/external_corpora.v1.json``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lvke_mcp.runtime.config import Config

CONFIG_DIR = Config.from_env().config_dir
PACKAGED_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
DEFAULT_MANIFEST = PACKAGED_CONFIG_DIR / "external_corpora.v1.json"
_ROUTE_VALUES = {"generic_feasibility", "asset_acquisition"}
_REPORT_VALUES = {"feasibility_study", "investment_decision"}


class ExternalCorpusError(RuntimeError):
    """Raised when the external corpus catalog cannot be trusted."""


@dataclass(frozen=True)
class ResolvedCorpus:
    corpus_id: str
    path: Path
    relative_path: str
    marker_files: tuple[str, ...]
    allowed_evidence_roles: tuple[str, ...]
    preferred_extensions: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "import_root": str(self.path),
            "relative_path": self.relative_path,
            "marker_files": list(self.marker_files),
            "allowed_evidence_roles": list(self.allowed_evidence_roles),
            "preferred_extensions": list(self.preferred_extensions),
        }


def _manifest_path() -> Path:
    configured = str(os.getenv("LVKE_EXTERNAL_CORPUS_MANIFEST") or "").strip()
    if not configured:
        return DEFAULT_MANIFEST
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = CONFIG_DIR / path
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ExternalCorpusError("external corpus manifest does not exist") from exc


def _load_manifest() -> dict[str, Any]:
    path = _manifest_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExternalCorpusError("external corpus manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "external-corpora.v1":
        raise ExternalCorpusError("external corpus manifest schema_version is invalid")
    corpora = payload.get("corpora")
    projects = payload.get("projects")
    if not isinstance(corpora, list) or not corpora or not isinstance(projects, list) or not projects:
        raise ExternalCorpusError("external corpus manifest requires corpora and projects")
    return payload


def _external_root() -> Path:
    configured = str(os.getenv("LVKE_EXTERNAL_CORPUS_ROOT") or "").strip()
    if not configured:
        raise ExternalCorpusError("LVKE_EXTERNAL_CORPUS_ROOT is not configured")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = CONFIG_DIR / path
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ExternalCorpusError("LVKE_EXTERNAL_CORPUS_ROOT does not exist") from exc
    if not resolved.is_dir():
        raise ExternalCorpusError("LVKE_EXTERNAL_CORPUS_ROOT is not a directory")
    return resolved


def _safe_relative(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ExternalCorpusError(f"{field} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ExternalCorpusError(f"{field} must stay below the external corpus root")
    return path


def _string_list(value: Any, field: str, *, required: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or (required and not value):
        raise ExternalCorpusError(f"{field} must be a non-empty string array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ExternalCorpusError(f"{field} contains an invalid value")
    return tuple(item.strip() for item in value)


def resolve_all_corpora() -> tuple[ResolvedCorpus, ...]:
    root = _external_root()
    payload = _load_manifest()
    seen: set[str] = set()
    resolved: list[ResolvedCorpus] = []
    for item in payload["corpora"]:
        if not isinstance(item, dict):
            raise ExternalCorpusError("corpus entry must be an object")
        corpus_id = str(item.get("corpus_id") or "").strip()
        if not corpus_id or corpus_id in seen:
            raise ExternalCorpusError("corpus_id must be non-empty and unique")
        seen.add(corpus_id)
        relative = _safe_relative(item.get("relative_path"), f"{corpus_id}.relative_path")
        try:
            corpus_path = (root / relative).resolve(strict=True)
        except OSError as exc:
            raise ExternalCorpusError(f"corpus directory is missing: {corpus_id}") from exc
        if not corpus_path.is_dir() or (corpus_path != root and root not in corpus_path.parents):
            raise ExternalCorpusError(f"corpus directory escapes root: {corpus_id}")
        markers = _string_list(item.get("marker_files"), f"{corpus_id}.marker_files")
        for marker in markers:
            marker_relative = _safe_relative(marker, f"{corpus_id}.marker_files")
            try:
                marker_path = (corpus_path / marker_relative).resolve(strict=True)
            except OSError as exc:
                raise ExternalCorpusError(f"corpus marker is missing: {corpus_id}/{marker}") from exc
            if not marker_path.is_file() or corpus_path not in marker_path.parents:
                raise ExternalCorpusError(f"corpus marker is unsafe: {corpus_id}/{marker}")
        resolved.append(
            ResolvedCorpus(
                corpus_id=corpus_id,
                path=corpus_path,
                relative_path=relative.as_posix(),
                marker_files=markers,
                allowed_evidence_roles=_string_list(
                    item.get("allowed_evidence_roles"),
                    f"{corpus_id}.allowed_evidence_roles",
                ),
                preferred_extensions=_string_list(
                    item.get("preferred_extensions"),
                    f"{corpus_id}.preferred_extensions",
                ),
            )
        )
    return tuple(resolved)


def configured_import_roots() -> tuple[Path, ...]:
    explicit = str(os.getenv("LVKE_SOURCE_IMPORT_ROOTS") or "")
    if explicit.strip():
        roots: list[Path] = []
        for value in explicit.split(os.pathsep):
            if not value.strip():
                continue
            try:
                path = Path(value).expanduser().resolve(strict=True)
            except OSError as exc:
                raise ExternalCorpusError("LVKE_SOURCE_IMPORT_ROOTS contains a missing path") from exc
            if not path.is_dir():
                raise ExternalCorpusError("LVKE_SOURCE_IMPORT_ROOTS must contain directories")
            if path == CONFIG_DIR:
                raise ExternalCorpusError(
                    "MCP configuration directory cannot be used as a source import root"
                )
            roots.append(path)
        if not roots:
            raise ExternalCorpusError("LVKE_SOURCE_IMPORT_ROOTS contains no usable directory")
        return tuple(dict.fromkeys(roots))
    return tuple(item.path for item in resolve_all_corpora())


def _normalized_project_name(value: str) -> str:
    return re.sub(r"[\s\-_()（）]+", "", value).casefold()


def resolve_project_corpora(project_name: str) -> dict[str, Any]:
    if not isinstance(project_name, str) or not project_name.strip():
        raise ExternalCorpusError("project_name is required")
    payload = _load_manifest()
    available = {item.corpus_id: item for item in resolve_all_corpora()}
    needle = _normalized_project_name(project_name)
    matches: list[dict[str, Any]] = []
    for project in payload["projects"]:
        if not isinstance(project, dict):
            raise ExternalCorpusError("project entry must be an object")
        aliases = _string_list(project.get("aliases"), "project.aliases")
        if any(_normalized_project_name(alias) in needle or needle in _normalized_project_name(alias) for alias in aliases):
            matches.append(project)
    if not matches:
        raise ExternalCorpusError("project materials are not registered in external corpus manifest")
    if len(matches) != 1:
        raise ExternalCorpusError("project name matches multiple corpus routes")
    project = matches[0]
    route = str(project.get("finance_route") or "")
    report_type = str(project.get("report_type") or "")
    if route not in _ROUTE_VALUES or report_type not in _REPORT_VALUES:
        raise ExternalCorpusError("project route or report type is invalid")
    corpus_ids = _string_list(project.get("corpus_ids"), "project.corpus_ids")
    if any(corpus_id not in available for corpus_id in corpus_ids):
        raise ExternalCorpusError("project references an unknown corpus_id")
    selected = [available[corpus_id].public_dict() for corpus_id in corpus_ids]
    return {
        "project_id": str(project.get("project_id") or ""),
        "project_name": project_name.strip(),
        "finance_route": route,
        "report_type": report_type,
        "route_markers": list(_string_list(project.get("route_markers"), "project.route_markers")),
        "corpora": selected,
        "import_roots": [item["import_root"] for item in selected],
    }
