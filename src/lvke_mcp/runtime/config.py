"""MCP-owned runtime configuration (§6.1).

Reads only ``LVKE_MCP_*`` environment variables (plus ``TAVILY_API_KEY`` for
the optional Tavily web provider).  It deliberately never reads host-application
environment variables: the MCP distribution owns its own configuration and its
own data root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from lvke_mcp.runtime.workspace import data_root

PROFILES = ("core", "formal")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    config_dir: Path
    temp_dir: Path
    profile: str
    tavily_api_key: str

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = data_root()
        configured_config = str(os.getenv("LVKE_MCP_CONFIG_DIR") or "").strip()
        config_dir = (
            Path(configured_config).expanduser()
            if configured_config
            else data_dir / "config"
        )
        configured_temp = str(os.getenv("LVKE_MCP_TEMP_DIR") or "").strip()
        temp_dir = Path(configured_temp).expanduser() if configured_temp else data_dir / "tmp"
        profile = str(os.getenv("LVKE_MCP_PROFILE") or "core").strip().lower()
        if profile not in PROFILES:
            profile = "core"
        tavily_api_key = str(os.getenv("TAVILY_API_KEY") or "").strip()
        return cls(
            data_dir=data_dir,
            config_dir=config_dir,
            temp_dir=temp_dir,
            profile=profile,
            tavily_api_key=tavily_api_key,
        )


# ── 受信提取 receipt 的 HMAC 密钥 ──
#
# 住在 runtime 而不是 servers/ 或 domains/：签发方（data-acquisition service）
# 与探测方（research 的 tavily provider）分属两层，而 domains -> servers 是禁止
# 的层边（scripts/module_metrics.py）。两侧必须读同一个实现，否则用 *_FILE
# 间接持有密钥的部署会被 provider_status 误报成「未配置」，而 data_fetch 其实
# 能正常签发 receipt —— 前置检查与真实能力矛盾，比不检查更糟。
EXTERNAL_RECEIPT_SECRET_ENV = "LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET"
EXTERNAL_RECEIPT_SECRET_FILE_ENV = "LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET_FILE"


def external_receipt_secret() -> bytes:
    """Read the receipt HMAC secret from the env var, else from a secret file.

    Returns empty bytes when neither is configured; callers must treat that as a
    local configuration gap, never as an upstream provider failure.  The
    ``*_FILE`` form mirrors ``TAVILY_MCP_BEARER_TOKEN_FILE`` so a distributable
    ``.mcp.json`` can reference a path instead of embedding the secret.
    """

    value = str(os.getenv(EXTERNAL_RECEIPT_SECRET_ENV) or "").strip()
    if not value:
        secret_file = str(os.getenv(EXTERNAL_RECEIPT_SECRET_FILE_ENV) or "").strip()
        if secret_file:
            try:
                value = (
                    Path(secret_file)
                    .expanduser()
                    .read_text(encoding="utf-8")[:16384]
                    .strip()
                )
            except OSError:
                value = ""
    return value.encode("utf-8")
