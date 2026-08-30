"""动态追问：缺口按所选配置算、跳过被登记、关键字段未答不得取得正式资格。

判据集合必须随报告配置变化——这正是"追问也要配置化"的部分。用一张写死的字段表
会让换配置后仍问同一批问题，而配置真正需要的字段无人问起。
"""

from __future__ import annotations

import unittest

from lvke_mcp.servers.lvke_zero_material_delivery._service import (
    questions,
    report_profiles,
)
from lvke_mcp.servers.lvke_zero_material_delivery._service.explicit_inputs import (
    SOURCE_SENTENCE,
)


def _profile(profile_id: str) -> dict:
    return report_profiles.load_profile_document(f"{profile_id}.v1.json")


class GapComputationTest(unittest.TestCase):
    def test_gaps_follow_the_selected_profile_not_a_fixed_table(self) -> None:
        generic = questions.compute_missing_inputs(
            profile=_profile("generic-gov10"), intent={}
        )
        rail = questions.compute_missing_inputs(
            profile=_profile("urban-rail-gov10"), intent={}
        )
        generic_fields = {row["field"] for row in generic}
        rail_fields = {row["field"] for row in rail}
        self.assertNotEqual(generic_fields, rail_fields)
        # 轨道配置独有的必填字段必须被问到。
        self.assertIn("route_length_km", rail_fields)
        self.assertIn("station_count", rail_fields)
        self.assertNotIn("route_length_km", generic_fields)

    def test_every_gap_carries_the_full_question_contract(self) -> None:
        rows = questions.compute_missing_inputs(
            profile=_profile("urban-rail-gov10"), intent={}
        )
        for row in rows:
            with self.subTest(field=row["field"]):
                # 字段名、章节、是否关键、受控假设来源、影响说明缺一不可。
                self.assertTrue(row["field"])
                self.assertTrue(row["section"])
                self.assertIn("critical", row)
                self.assertTrue(row["controlled_assumption_source"])
                self.assertTrue(row["impact"])
                self.assertIn(row["status"], {"pending", "skipped"})

    def test_critical_fields_sort_first(self) -> None:
        rows = questions.compute_missing_inputs(
            profile=_profile("urban-rail-gov10"), intent={}
        )
        priorities = [row["priority"] for row in rows]
        self.assertEqual(priorities, sorted(priorities))

    def test_units_and_ranges_are_declared_where_checkable(self) -> None:
        rows = {
            row["field"]: row
            for row in questions.compute_missing_inputs(
                profile=_profile("urban-rail-gov10"), intent={}
            )
        }
        self.assertEqual(rows["route_length_km"]["unit"], "公里")
        self.assertEqual(rows["station_count"]["minimum"], 1)
        self.assertEqual(rows["build_period_months"]["maximum"], 240)

    def test_sentence_explicit_and_confirmed_fields_are_not_asked_again(self) -> None:
        profile = _profile("generic-gov10")
        package = {
            "fields": [
                {
                    "name": "total_investment_wan",
                    "value": 50000,
                    "source_ref": SOURCE_SENTENCE,
                },
                {"name": "annual_revenue_wan", "value": 9000, "confirmed": True},
                # 仅由行业种子填出的值**不算**已回答。
                {"name": "build_period_months", "value": 24},
            ]
        }
        fields = {
            row["field"]
            for row in questions.compute_missing_inputs(
                profile=profile,
                intent={"project_name": "X", "region": "湖北省"},
                assumption_package=package,
            )
        }
        self.assertNotIn("total_investment_wan", fields)
        self.assertNotIn("annual_revenue_wan", fields)
        self.assertIn("build_period_months", fields)

    def test_intent_placeholder_does_not_count_as_answered(self) -> None:
        fields = {
            row["field"]
            for row in questions.compute_missing_inputs(
                profile=_profile("generic-gov10"),
                intent={"project_name": "X", "region": "待确认"},
            )
        }
        self.assertIn("region", fields)
        self.assertNotIn("project_name", fields)


class SkipRegistrationTest(unittest.TestCase):
    def test_skipped_field_is_recorded_and_becomes_a_limitation(self) -> None:
        rows = questions.compute_missing_inputs(
            profile=_profile("generic-gov10"),
            intent={},
            skipped=[{"field": "build_period_months", "reason": "user_skipped"}],
        )
        by_field = {row["field"]: row for row in rows}
        self.assertEqual(by_field["build_period_months"]["status"], "skipped")
        summary = questions.summarize_gaps(rows)
        self.assertEqual(summary["skipped_count"], 1)
        self.assertTrue(
            any("build_period_months" in item for item in summary["release_limitations"])
        )

    def test_unanswered_critical_fields_are_surfaced_for_the_formal_gate(self) -> None:
        rows = questions.compute_missing_inputs(
            profile=_profile("urban-rail-gov10"), intent={}
        )
        summary = questions.summarize_gaps(rows)
        self.assertIn("route_length_km", summary["critical_unanswered_fields"])
        # 关键字段未答必须留下限制项，供正式候选门禁读取；技术预览仍可生成。
        self.assertTrue(
            any(
                item.startswith("required_field_unanswered:")
                for item in summary["release_limitations"]
            )
        )

    def test_answering_everything_clears_the_gap_summary(self) -> None:
        profile = _profile("generic-gov10")
        package = {
            "fields": [
                {"name": name, "value": 1, "confirmed": True}
                for name in profile["required_fields"]
            ]
        }
        rows = questions.compute_missing_inputs(
            profile=profile,
            intent={
                "project_name": "X",
                "region": "湖北省",
                "industry": {"industry_label": "文旅"},
            },
            assumption_package=package,
        )
        summary = questions.summarize_gaps(rows)
        self.assertEqual(rows, [])
        self.assertEqual(summary["pending_count"], 0)
        self.assertEqual(summary["release_limitations"], [])


if __name__ == "__main__":
    unittest.main()
