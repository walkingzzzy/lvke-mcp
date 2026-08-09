"""FinanceRun 前的四方尺度对账。

50 公里轨道线套用通用单体种子（约 11.6 亿元）时算术全部自洽、十三表也能
勾稽，因此 finance_status 会显示 ok——但业务尺度明显错误。对账覆盖
DeliveryIntent 的明确输入、ProjectContext 的行业口径、AssumptionPackage 的
字段取值，以及送进 FinanceRun 的 InputRevision。

设计约束：只报不改，区间只用于提示，绝不用区间自动改写给定值。
"""

from __future__ import annotations

import unittest

from lvke_mcp.servers.lvke_zero_material_delivery._service import intake, lifecycle
from lvke_mcp.servers.lvke_zero_material_delivery._service.scale_guard import (
    check_project_scale,
)

_EXPLICIT = {
    "route_length_km": {"value": 50.0},
    "station_count": {"value": 10},
    "build_period_months": {"value": 60},
    "construction_start_year": {"value": 2028},
    "construction_end_year": {"value": 2032},
}
# 50 公里 × 6 亿元/公里 = 300 亿元，落在参考强度区间内。
_CONSISTENT_FIELDS = {
    "total_investment_wan": 3_000_000.0,
    "build_period_months": 60,
    "operating_period_years": 30,
    "loan_ratio": 0.6,
}


def _check(**overrides: object) -> dict:
    args: dict = {
        "industry_code": "urban_rail_transit",
        "explicit_inputs": _EXPLICIT,
        "field_values": dict(_CONSISTENT_FIELDS),
    }
    args.update(overrides)
    return check_project_scale(**args)  # type: ignore[arg-type]


def _codes(result: dict) -> set[str]:
    return {str(item["code"]) for item in result["issues"]}


def _fields(result: dict) -> set[str]:
    return {str(item["field"]) for item in result["issues"]}


class ScaleGuardConsistentCaseTest(unittest.TestCase):
    def test_scale_consistent_project_passes(self) -> None:
        result = _check()
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["issues"], [])

    def test_reconciled_dimensions_are_reported(self) -> None:
        result = _check()
        self.assertEqual(result["reconciled"]["average_station_spacing_km"], 5.0)
        self.assertIn("loan_ratio", result["dimensions_checked"])
        self.assertIn("station_count", result["dimensions_checked"])


class ScaleGuardMismatchTest(unittest.TestCase):
    def test_generic_seed_investment_is_blocked(self) -> None:
        # 11.64 亿元通用单体种子配 50 公里轨道线。
        result = _check(field_values={**_CONSISTENT_FIELDS, "total_investment_wan": 116_430.12})
        self.assertFalse(result["ok"])
        self.assertIn("project_scale_inconsistent", _codes(result))
        self.assertIn("total_investment_wan", _fields(result))

    def test_project_context_industry_must_match_intent(self) -> None:
        result = _check(project_context={"industry_code": "real_estate"})
        self.assertIn("industry_code", _fields(result))

    def test_absurd_station_spacing_is_blocked(self) -> None:
        result = _check(explicit_inputs={**_EXPLICIT, "station_count": {"value": 3}})
        self.assertIn("station_count", _fields(result))

    def test_build_period_outside_range_is_blocked(self) -> None:
        result = _check(field_values={**_CONSISTENT_FIELDS, "build_period_months": 240})
        self.assertIn("build_period_months", _fields(result))

    def test_financing_ratio_must_be_a_fraction(self) -> None:
        result = _check(field_values={**_CONSISTENT_FIELDS, "loan_ratio": 60})
        self.assertIn("loan_ratio", _fields(result))

    def test_equity_and_debt_ratio_must_complement(self) -> None:
        result = _check(
            field_values={**_CONSISTENT_FIELDS, "loan_ratio": 0.6, "equity_ratio": 0.3}
        )
        self.assertIn("loan_ratio", _fields(result))

    def test_explicit_input_may_not_be_overridden_by_seed(self) -> None:
        result = _check(field_values={**_CONSISTENT_FIELDS, "build_period_months": 24})
        self.assertIn("explicit_input_overridden", _codes(result))

    def test_input_revision_drift_is_blocked(self) -> None:
        result = _check(input_revision={"input_revision": {"loan_ratio": 0.2}})
        self.assertIn("input_revision_scale_drift", _codes(result))

    def test_guard_never_rewrites_the_given_value(self) -> None:
        fields = {**_CONSISTENT_FIELDS, "total_investment_wan": 116_430.12}
        _check(field_values=fields)
        # 只报不改：入参不得被对账逻辑改写。
        self.assertEqual(fields["total_investment_wan"], 116_430.12)


class ScaleGuardDeliveryChainTest(unittest.TestCase):
    def test_rail_sentence_blocks_finance_run_on_seed_investment(self) -> None:
        workspace = "test-scale-guard-chain"
        sentence = (
            "拟建设城市轨道交通1号线，线路全长50公里，设10座车站，"
            "设计速度120km/h，建设期2028年至2032年"
        )
        created = intake.create_from_sentence(
            {
                "workspace_id": workspace,
                "sentence": sentence,
                "idempotency_key": "scale-guard-chain-1",
            }
        )
        started = lifecycle.start(
            {
                "workspace_id": workspace,
                "delivery_run_id": created["delivery_run"]["delivery_run_id"],
                "idempotency_key": "scale-guard-chain-start-1",
            }
        )
        blockers = list(started.get("blockers") or [])
        self.assertIn("project_scale_inconsistent", blockers)
        # 口径差不得报成漂移：假设包投资额不含建设期利息，InputRevision 含。
        self.assertNotIn("input_revision_scale_drift", blockers)


if __name__ == "__main__":
    unittest.main()
