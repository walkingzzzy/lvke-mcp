"""句中写明的参数必须压过行业种子，且不得留下矛盾残留。

回归此前三处缺陷：
1. ``pop("low"/"high")`` 打错层级，导致 value=60 与 range.base=24 并存；
2. 明确输入仍带 ``deterministic_industry_scenario_seed`` 方法与非零确认分值，
   于是继续出现在 confirmation_items 里要求用户确认已写明的数字；
3. 候选量纲白名单过窄，客流/票价/行车间隔等数字既不进 fields 也不进
   unmapped，等于静默丢弃。
"""

from __future__ import annotations

import unittest

from lvke_mcp.servers.lvke_zero_material_delivery._service import (
    base,
    intake,
    lifecycle,
)
from lvke_mcp.servers.lvke_zero_material_delivery._service.explicit_inputs import (
    SOURCE_SENTENCE,
    extract_explicit_inputs,
)

_RAIL_SENTENCE = (
    "拟建设城市轨道交通1号线，线路全长50公里，设10座车站，"
    "设计速度120km/h，建设期2028年至2032年"
)
_EXPLICIT_NAMES = (
    "route_length_km",
    "station_count",
    "design_speed_kmh",
    "build_period_months",
    "construction_start_year",
    "construction_end_year",
)


class ExplicitInputExtractionTest(unittest.TestCase):
    def test_year_span_becomes_sixty_month_build_period(self) -> None:
        fields = extract_explicit_inputs(_RAIL_SENTENCE)["fields"]
        self.assertEqual(fields["build_period_months"]["value"], 60)
        self.assertEqual(fields["construction_start_year"]["value"], 2028)
        self.assertEqual(fields["construction_end_year"]["value"], 2032)
        self.assertIn("含首尾年", fields["build_period_months"]["derivation"])

    def test_scale_parameters_are_extracted(self) -> None:
        fields = extract_explicit_inputs(_RAIL_SENTENCE)["fields"]
        self.assertEqual(fields["route_length_km"]["value"], 50.0)
        self.assertEqual(fields["station_count"]["value"], 10)
        self.assertEqual(fields["design_speed_kmh"]["value"], 120.0)

    def test_unrecognised_dimensions_are_reported_not_dropped(self) -> None:
        sentence = "线路全长50公里，初期客流45万人次/年，平均票价3.2元/人次，行车间隔5分钟"
        extracted = extract_explicit_inputs(sentence)
        raws = [item["raw"] for item in extracted["unmapped"]]
        self.assertIn("45万人次/年", raws)
        self.assertIn("3.2元/人次", raws)
        self.assertIn("5分钟", raws)


class ExplicitInputPrecedenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = "test-explicit-precedence"
        created = intake.create_from_sentence(
            {
                "workspace_id": cls.workspace,
                "sentence": _RAIL_SENTENCE,
                "idempotency_key": "explicit-precedence-1",
            }
        )
        started = lifecycle.start(
            {
                "workspace_id": cls.workspace,
                "delivery_run_id": created["delivery_run"]["delivery_run_id"],
                "idempotency_key": "explicit-precedence-start-1",
            }
        )
        cls.package_id = started["delivery_run"]["assumption_package_id"]
        record = base.ASSUMPTION_STORE.get(cls.workspace, cls.package_id)
        cls.fields = {
            str(item["name"]): item for item in (record or {})["payload"]["fields"]
        }

    def test_explicit_values_override_industry_seed(self) -> None:
        self.assertEqual(self.fields["build_period_months"]["value"], 60)
        self.assertEqual(self.fields["route_length_km"]["value"], 50.0)
        self.assertEqual(self.fields["station_count"]["value"], 10)

    def test_no_seed_range_residue_contradicts_the_value(self) -> None:
        for name in _EXPLICIT_NAMES:
            with self.subTest(field=name):
                field = self.fields[name]
                value = field["value"]
                self.assertEqual(
                    field["range"],
                    {"low": value, "base": value, "high": value},
                    f"{name} 的 range 仍残留行业种子区间",
                )

    def test_explicit_fields_carry_sentence_provenance(self) -> None:
        for name in _EXPLICIT_NAMES:
            with self.subTest(field=name):
                field = self.fields[name]
                self.assertEqual(field["source_ref"], SOURCE_SENTENCE)
                self.assertEqual(field["method"], SOURCE_SENTENCE)
                self.assertEqual(field["confirmation_priority_score"], 0)

    def test_explicit_fields_are_not_queued_for_confirmation(self) -> None:
        listed = lifecycle.list_assumptions(
            {"workspace_id": self.workspace, "assumption_package_id": self.package_id}
        )
        queued = {str(item["name"]) for item in listed["confirmation_items"]}
        self.assertEqual(queued & set(_EXPLICIT_NAMES), set())
        self.assertEqual(
            sorted(listed["explicit_input_fields"]), sorted(_EXPLICIT_NAMES)
        )

    def test_seed_fields_still_require_confirmation(self) -> None:
        listed = lifecycle.list_assumptions(
            {"workspace_id": self.workspace, "assumption_package_id": self.package_id}
        )
        queued = {str(item["name"]) for item in listed["confirmation_items"]}
        # 投资额与收入没有写在句子里，必须继续要求确认。
        self.assertIn("total_investment_wan", queued)


if __name__ == "__main__":
    unittest.main()
