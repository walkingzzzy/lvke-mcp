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
