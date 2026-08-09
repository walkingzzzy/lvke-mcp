"""Round-two branch tables mapping object_kind to legacy tool names.

Insertion order feeds the ``enum`` of each aggregate schema, so these dict
literals are order-sensitive and must not be reordered or derived.
"""

from __future__ import annotations

_VALIDATE_BRANCHES = {
    "market_case": ("planning_validate_market_case", "market_case_id"),
    "revenue_drivers": ("planning_validate_revenue_drivers", "revenue_driver_set_id"),
    "build_scale": ("planning_validate_build_scale", "build_scale_case_id"),
    "cost_drivers": ("planning_validate_cost_drivers", "cost_driver_set_id"),
    "labor_plan": ("planning_validate_labor_plan", "labor_plan_id"),
    "option_comparison": ("planning_validate_option_comparison", "option_comparison_id"),
    "policy_basis": ("planning_validate_policy_basis", "policy_basis_id"),
}
_COMPARE_BRANCHES = {
    "market_case": ("planning_compare_market_cases", "market_case_id"),
    "revenue_drivers": ("planning_compare_revenue_candidates", "revenue_driver_set_id"),
}
_CONFIRM_BRANCHES = {
    "market_case": ("planning_confirm_market_case", "market_case_id"),
    "revenue_drivers": ("planning_confirm_revenue_drivers", "revenue_driver_set_id"),
    "build_scale": ("planning_confirm_build_scale", "build_scale_case_id"),
    "cost_drivers": ("planning_confirm_cost_drivers", "cost_driver_set_id"),
    "labor_plan": ("planning_confirm_labor_plan", "labor_plan_id"),
    "policy_basis": ("planning_confirm_policy_basis", "policy_basis_id"),
    "option_comparison": ("planning_confirm_option_comparison", "option_comparison_id"),
}
# These are deliberately explicit.  In particular, option_comparison has a
# historical operation/producer name that cannot be reconstructed from kind.
CONFIRM_OPERATION_BY_KIND = {
    "market_case": "planning_confirm_market_case",
    "revenue_drivers": "planning_confirm_revenue_drivers",
    "build_scale": "planning_confirm_build_scale",
    "cost_drivers": "planning_confirm_cost_drivers",
    "labor_plan": "planning_confirm_labor_plan",
    "policy_basis": "planning_confirm_policy_basis",
    "option_comparison": "planning_confirm_option_selection",
}
_PREPARE_BRANCHES = {
    "market_case": "planning_prepare_market_case",
    "revenue_drivers": "planning_prepare_revenue_drivers",
    "cost_drivers": "planning_prepare_cost_drivers",
    "policy_basis": "planning_prepare_policy_basis",
    "option_comparison": "planning_prepare_option_comparison",
}
PREPARE_OPERATION_BY_KIND = dict(_PREPARE_BRANCHES)
_CREATE_BRANCHES = {
    "revenue_drivers": "planning_create_revenue_drivers",
    "build_scale": "planning_create_build_scale",
    "cost_drivers": "planning_create_cost_drivers",
    "labor_plan": "planning_create_labor_plan",
}
CREATE_OPERATION_BY_KIND = dict(_CREATE_BRANCHES)

