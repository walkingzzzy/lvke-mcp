"""MCP-owned workspace paths.

The MCP distribution owns its data root.  It deliberately does not consult
the host application's configuration or filesystem.
"""

from __future__ import annotations

import os
from pathlib import Path


import re

_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_.一-鿿-]{1,160}$")


def _require_safe_segment(value: str, field: str) -> str:
    """校验单个路径段：无 ``/``、无 ``..``、无空段，防止路径穿越。

    ``deliverable_dir`` 拼接的每一段都来自调用方（workspace_id/domain/kind），
    任何一段若能塞入 ``../`` 就能逃出交付物根目录写到仓库外，因此逐段校验，
    而不是只校验拼好的最终路径。
    """

    text = str(value or "").strip()
    if not _SAFE_PATH_SEGMENT.fullmatch(text):
        raise ValueError(f"{field} 不合法: {value!r}")
    # 明确拒绝 "." 和 ".."：正则允许点号，但仅点组成的段是路径穿越
    if set(text) <= {"."}:
        raise ValueError(f"{field} 不合法: {value!r}")
    return text


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

    测试隔离：优先读 ``LVKE_DELIVERABLE_DIR``；未设置时若
    ``LVKE_MCP_DATA_DIR`` 已指向非默认目录（说明调用方在做隔离测试），
    交付物根随之挂到该目录下的 ``lvke产出/``，避免测试把假数据写进仓库。
    只有两者都未设置时才落到仓库根 ``lvke产出/``（真实生产/交互场景）。
    """

    configured = str(os.getenv("LVKE_DELIVERABLE_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    data_dir = str(os.getenv("LVKE_MCP_DATA_DIR") or "").strip()
    if data_dir:
        return Path(data_dir).expanduser() / "lvke产出"
    # workspace.py -> runtime -> lvke_mcp -> src -> <repo root>
    return Path(__file__).resolve().parents[3] / "lvke产出"


def deliverable_dir(workspace_id: str, domain: str, kind: str) -> Path:
    """``lvke产出/{workspace_id}/{domain}/{kind}``，逐段校验防路径穿越。"""

    root = deliverable_root()
    return (
        root
        / _require_safe_segment(workspace_id, "workspace_id")
        / _require_safe_segment(domain, "domain")
        / _require_safe_segment(kind, "kind")
    )
