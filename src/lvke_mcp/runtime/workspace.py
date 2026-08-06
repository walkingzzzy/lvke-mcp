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


def deliverable_root() -> Path:
    """交付物导出根目录，默认落到仓库内的 ``lvke产出/``。

    与 :func:`data_root` 刻意分开：``data_root`` 存运行时状态（控制面
    sqlite、workspace 锁、解析缓存），不适合入库；而十三表、研报、证据包
    这些**交付物**需要随仓库留存和复核，因此单独给一个根目录。
    """

    configured = str(os.getenv("LVKE_DELIVERABLE_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    # workspace.py -> runtime -> lvke_mcp -> src -> <repo root>
    return Path(__file__).resolve().parents[3] / "lvke产出"


def deliverable_dir(workspace_id: str, domain: str, kind: str) -> Path:
    """``lvke产出/{workspace_id}/{domain}/{kind}``"""

    return deliverable_root() / str(workspace_id) / str(domain) / str(kind)
