"""Mirror lvke deliverable artifacts into a human-navigable project folder.

lvke's authoritative store stays under the MCP-owned workspace root
(``{LVKE_MCP_DATA_DIR}/workspaces/<id>/mcp_objects``)
— that is where resource URIs, content hashes and cell lineage all point, and it
must not move.  This module *additionally* copies finished deliverable files into
a project folder so a person can open them directly (Excel/Word) without knowing
the internal store layout.

Contract:
- Mirroring is **best-effort**: any failure is swallowed and never breaks the
  authoritative write.  A mirror copy is a convenience, not a source of truth.
- Destination: ``<mirror_root>/<title>_<workspace_id>/<category>/<filename>``
  where ``mirror_root`` = env ``LVKE_PROJECT_ARTIFACT_DIR`` or ``<cwd>/lvke产出``.
- ``title`` is the human project name read from ``workspace_meta.json``; falls
  back to the workspace id when unavailable.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

_MIRROR_DIRNAME = "lvke产出"
_INVALID = re.compile(r'[/\\:*?"<>|\r\n\t]')


def _project_mirror_root() -> Path:
    """Root under which every project subfolder is created."""
    env = os.environ.get("LVKE_PROJECT_ARTIFACT_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    # MCP servers are launched with ``uv run --directory <project>`` so the
    # process cwd is the project root.
    return Path.cwd() / _MIRROR_DIRNAME


def _sanitize(name: str) -> str:
    cleaned = _INVALID.sub("_", (name or "").strip())
    cleaned = cleaned.strip(". ")
    return cleaned[:60] or "未命名项目"


def _workspace_title(workspace_id: str) -> str:
    """Read the human project title from the workspace meta, id as fallback."""
    try:
        from lvke_mcp.runtime.workspace import workspace_root

        meta_path = workspace_root(workspace_id) / "workspace_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        title = str(meta.get("title") or "").strip()
        return title or workspace_id
    except Exception:  # noqa: BLE001 - best effort, never break the caller
        return workspace_id


def project_dir_for(workspace_id: str) -> Path:
    """Return ``<mirror_root>/<title>_<workspace_id>`` (not created here)."""
    title = _sanitize(_workspace_title(workspace_id))
    return _project_mirror_root() / f"{title}_{workspace_id}"


def mirror_file(workspace_id: str, src: str | Path, *, category: str = "") -> Path | None:
    """Copy a finished artifact into the project folder. Best-effort.

    Returns the mirrored path on success, ``None`` on any failure (the
    authoritative store already holds the real file, so a miss is non-fatal).
    """
    try:
        src_path = Path(src)
        if not src_path.is_file():
            return None
        base = project_dir_for(workspace_id)
        dest_dir = base / category if category else base
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src_path.name
        shutil.copy2(src_path, dest)
        return dest
    except Exception:  # noqa: BLE001 - mirroring must never break delivery
        return None


def mirror_dir(
    workspace_id: str, src_dir: str | Path, *, category: str = "", subdir: str = ""
) -> Path | None:
    """Copy a finished artifact *directory* into the project folder. Best-effort.

    Used for packaged deliverables (e.g. a DOCX artifact bundle carrying the
    document plus manifest/index).  Mirrors the whole tree under
    ``<project>/<category>/<subdir>/``.  Returns the mirrored dir on success,
    ``None`` on any failure.
    """
    try:
        src_path = Path(src_dir)
        if not src_path.is_dir():
            return None
        base = project_dir_for(workspace_id)
        dest_dir = base / category if category else base
        if subdir:
            dest_dir = dest_dir / subdir
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_path, dest_dir)
        return dest_dir
    except Exception:  # noqa: BLE001 - mirroring must never break delivery
        return None
