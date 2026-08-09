"""Facade over the shared project-scale reconciliation.

判定逻辑已提升为 ``lvke_mcp.domains.finance.scale_reconciliation`` 的共享门禁，
正式 ``finance_run_model`` 与零材料交付链共用同一实现 —— 此前它只存在于零材料链，
同一个尺度错误在正式链完全不被拦截。

本模块保留原有导入路径与签名，不复制规则。
"""

from __future__ import annotations

# ``Any`` 在公共 API 基线里被记录为本模块的符号；搬走实现后不重新导出会被判为
# "symbol disappeared"。它只是类型别名，重新导出无副作用。
from typing import Any  # noqa: F401

from lvke_mcp.domains.finance.scale_reconciliation import (  # noqa: F401
    URBAN_RAIL_INVEST_INTENSITY,
    check_project_scale,
)

__all__ = ["URBAN_RAIL_INVEST_INTENSITY", "check_project_scale"]
