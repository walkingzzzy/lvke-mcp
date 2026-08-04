"""MCP-owned workspace paths.

The MCP distribution owns its data root.  It deliberately does not consult
the host application's configuration or filesystem.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_root() -> Path:
    configured = str(os.getenv("LVKE_MCP_DATA_DIR") or "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".lvke"


def workspace_root(workspace_id: str) -> Path:
    return data_root() / "workspaces" / str(workspace_id)
