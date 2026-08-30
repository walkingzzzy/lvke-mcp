"""Candidate/validation/confirmation lifecycles for planning objects.

门面模块：实现已按业务对象拆分到 ``_lifecycle/`` 子包，这里统一 re-export，
保持 ``lvke_mcp.servers.lvke_project_planning.lifecycle`` 路径与符号稳定。
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import yaml

from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.domains.project_planning import application as service

from ._lifecycle.base import _candidate, _payload, _put_candidate, _selection
from ._lifecycle.build_scale import (
    confirm_build_scale,
    get_industry_constraints,
    solve_build_scale,
    validate_build_scale,
)
from ._lifecycle.cost import (
    _calculated_cost_items,
    calculate_cost_drivers,
    confirm_cost_drivers,
    get_environmental_scheme_templates,
    prepare_cost_drivers,
    validate_cost_drivers,
)
from ._lifecycle.labor import confirm_labor_plan, infer_labor_plan, validate_labor_plan
from ._lifecycle.policy_option import (
    confirm_policy_basis,
    prepare_policy_basis,
    score_option_comparison,
    validate_option_comparison,
    validate_policy_basis,
)
from ._lifecycle.revenue import (
    compare_revenue_candidates,
    confirm_revenue_drivers,
    prepare_revenue_drivers,
    validate_revenue_drivers,
)

__all__ = [
    "Any",
    "Decimal",
    "Path",
    "ROUND_HALF_UP",
    "_calculated_cost_items",
    "_candidate",
    "_payload",
    "_put_candidate",
    "_selection",
    "calculate_cost_drivers",
    "compare_revenue_candidates",
    "confirm_build_scale",
    "confirm_cost_drivers",
    "confirm_labor_plan",
    "confirm_policy_basis",
    "confirm_revenue_drivers",
    "get_environmental_scheme_templates",
    "get_industry_constraints",
    "infer_labor_plan",
    "math",
    "prepare_cost_drivers",
    "prepare_policy_basis",
    "prepare_revenue_drivers",
    "score_option_comparison",
    "service",
    "sha256_json",
    "solve_build_scale",
    "validate_build_scale",
    "validate_cost_drivers",
    "validate_labor_plan",
    "validate_option_comparison",
    "validate_policy_basis",
    "validate_revenue_drivers",
    "yaml",
]
