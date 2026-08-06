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
- Destination: ``<mirror_root>/<workspace_id>/<category>/<filename>``
  where ``mirror_root`` = env ``LVKE_PROJECT_ARTIFACT_DIR`` or
  ``deliverable_root()``（默认仓库 ``lvke产出/``，测试隔离时随
  ``LVKE_MCP_DATA_DIR`` 走）。与其他域产出（finance-tables/report/…）
  同根，避免出现第二套 ``{title}_{workspace_id}`` 布局。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_MIRROR_DIRNAME = "lvke产出"
_INVALID = re.compile(r'[/\\:*?"<>|\r\n\t]')


def _project_mirror_root() -> Path:
    """Root under which every project subfolder is created.

    统一用 :func:`~lvke_mcp.runtime.workspace.deliverable_root`，
    而不是 ``cwd()/lvke产出``：两者在生产环境等价，但测试时
    ``LVKE_MCP_DATA_DIR`` 已对 deliverable_root 生效，cwd 版本无法被测试
    隔离并会污染仓库（``lvke产出/ws-mirror_ws-mirror`` 即源于此）。
    此外统一根路径后 ``mirror_file / mirror_dir`` 产出的文件自然落到
    ``lvke产出/{workspace_id}/…`` 中，与其他域产出口径一致。
    """
    env = os.environ.get("LVKE_PROJECT_ARTIFACT_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    from lvke_mcp.runtime.workspace import deliverable_root
    return deliverable_root()


def _sanitize(name: str) -> str:
    cleaned = _INVALID.sub("_", (name or "").strip())
    cleaned = cleaned.strip(". ")
    return cleaned[:60] or "未命名项目"


def project_dir_for(workspace_id: str) -> Path:
    """Return ``<deliverable_root>/<workspace_id>`` (not created here).

    原设计为 ``<root>/<title>_<workspace_id>``，改成纯 workspace_id 以与
    ``deliverable_dir(workspace_id, …)`` 的目录结构对齐，并去掉对
    workspace_meta.json 的额外读取（非阻塞但在高频导出时无谓 IO）。
    """
    return _project_mirror_root() / str(workspace_id)


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
