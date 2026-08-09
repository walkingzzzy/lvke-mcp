"""城市轨道交通必须有专用规划参数模板与专用行业 Skill。

此前两处都落到通用分支：
- ``planning_get_industry_constraints`` 直接返回 ``industry_constraints_not_available``，
  调用方拿不到任何字段清单，只能自己猜要填什么。
- ``planning_resolve_industry_skill`` 把轨道并进 ``public-service``，主 Skill 是通用
  ``lvke-industry-context``，于是收入结构、成本口径、规模口径全按通用公共服务办。

修复后：轨道返回**只含字段与校验规则、零默认取值**的模板（``field_template_only``，
不取得正式证据资格），并解析到专用 ``lvke-urban-rail-transit``。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.domains.project_planning import application as service
from lvke_mcp.servers.lvke_project_planning._lifecycle import build_scale

_ROUTES = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "lvke_mcp"
    / "config"
    / "industry_skill_routes.json"
)


class _PlanningCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-rail-planning-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "rail-planning-test"
        self._sequence = 0

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _context(self, industry_code: str) -> str:
        self._sequence += 1
        created = service.create_project_context(
            self.workspace,
            {
                "project_name": f"轨道测试-{industry_code}",
                "industry_code": industry_code,
                "project_type": "new_build",
                "region": {"province": "湖北省", "city": "武汉市"},
                "objective": "评估线路可行性",
            },
            idempotency_key=f"rail-ctx-{self._sequence}",
        )
        return created["project_context_id"]


class RailFieldTemplateTest(_PlanningCase):
    def test_rail_returns_a_field_template_not_a_bare_block(self) -> None:
        result = build_scale.get_industry_constraints(
            self.workspace, self._context("urban_rail_transit")
        )
        self.assertEqual(result["code"], "industry_field_template_only")
        self.assertEqual(result["matched_field_template_key"], "轨道交通")

    def test_template_declares_the_rail_scale_fields(self) -> None:
        result = build_scale.get_industry_constraints(
            self.workspace, self._context("urban_rail_transit")
        )
        fields = (result["field_template"] or {}).get("fields") or {}
        for name in (
            "line_length_km",
            "station_count",
            "average_station_spacing_km",
            "underground_ratio",
            "elevated_ratio",
            "depot_count",
            "design_speed_kph",
            "train_formation",
            "headway_seconds",
        ):
            with self.subTest(field=name):
                self.assertIn(name, fields)
                self.assertIn("unit", fields[name])
                self.assertIn("type", fields[name])

    def test_template_carries_validation_rules(self) -> None:
        result = build_scale.get_industry_constraints(
            self.workspace, self._context("urban_rail_transit")
        )
        rule_ids = {rule["id"] for rule in result["validation_rules"]}
        self.assertIn("grade_separation_ratio_sum", rule_ids)
        self.assertIn("station_spacing_consistency", rule_ids)

    def test_template_grants_no_evidence_qualification(self) -> None:
        result = build_scale.get_industry_constraints(
            self.workspace, self._context("urban_rail_transit")
        )
        self.assertEqual(result["evidence_eligibility"], "field_template_only")
        self.assertEqual(result["status"], "missing_inputs")
        self.assertIn("industry_specific_constraints_required", result["blockers"])

    def test_template_contains_no_default_values(self) -> None:
        result = build_scale.get_industry_constraints(
            self.workspace, self._context("urban_rail_transit")
        )
        # 参数取值必须为空：模板只定义字段与规则，绝不预设线路长度或站数。
        self.assertEqual(result["parameters"], {})
        fields = (result["field_template"] or {}).get("fields") or {}
        for name, spec in fields.items():
            with self.subTest(field=name):
                self.assertNotIn("value", spec)
                self.assertNotIn("default", spec)

    def test_required_fields_are_reported(self) -> None:
        result = build_scale.get_industry_constraints(
            self.workspace, self._context("urban_rail_transit")
        )
        self.assertIn("line_length_km", result["required_fields"])
        self.assertIn("station_count", result["required_fields"])

    def test_land_use_industries_still_get_real_parameters(self) -> None:
        result = build_scale.get_industry_constraints(
            self.workspace, self._context("manufacturing")
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["evidence_eligibility"], "technical_fixture")
        self.assertIn("plot_ratio", result["parameters"])

    def test_rail_never_receives_land_use_parameters(self) -> None:
        result = build_scale.get_industry_constraints(
            self.workspace, self._context("urban_rail_transit")
        )
        for land_use_key in ("plot_ratio", "building_coverage", "workshop_share"):
            with self.subTest(key=land_use_key):
                self.assertNotIn(land_use_key, result["parameters"])


class RailSkillRouteTest(_PlanningCase):
    def test_rail_resolves_to_the_dedicated_skill(self) -> None:
        for industry in ("urban_rail_transit", "rail_transit", "urban_rail", "metro"):
            with self.subTest(industry=industry):
                resolved = service.resolve_industry_skill(
                    self.workspace, self._context(industry)
                )
                self.assertTrue(resolved["success"], resolved)
                self.assertEqual(resolved["route_id"], "urban-rail-transit")
                self.assertEqual(
                    resolved["primary_skill"], "lvke-urban-rail-transit"
                )

    def test_rail_no_longer_falls_into_public_service(self) -> None:
        resolved = service.resolve_industry_skill(
            self.workspace, self._context("urban_rail_transit")
        )
        self.assertNotEqual(resolved["route_id"], "public-service")
        self.assertNotEqual(resolved["primary_skill"], "lvke-industry-context")

    def test_generic_public_service_still_routes_to_public_service(self) -> None:
        resolved = service.resolve_industry_skill(
            self.workspace, self._context("government_public_service")
        )
        self.assertEqual(resolved["route_id"], "public-service")

    def test_route_binds_the_six_rail_capabilities(self) -> None:
        manifest = json.loads(_ROUTES.read_text(encoding="utf-8"))
        route = next(
            item
            for item in manifest["routes"]
            if item["route_id"] == "urban-rail-transit"
        )
        auxiliary = route["auxiliary_skills"]
        self.assertEqual(len(auxiliary), 6)
        for expected in (
            "lvke-rail-ridership-forecast",
            "lvke-rail-alignment-and-stations",
            "lvke-rail-investment-estimate",
            "lvke-rail-car-km-cost",
            "lvke-rail-rolling-stock-renewal",
            "lvke-rail-operating-staffing",
        ):
            with self.subTest(skill=expected):
                self.assertIn(expected, auxiliary)

    def test_rail_route_outranks_public_service(self) -> None:
        manifest = json.loads(_ROUTES.read_text(encoding="utf-8"))
        by_id = {item["route_id"]: item for item in manifest["routes"]}
        self.assertGreater(
            int(by_id["urban-rail-transit"]["priority"]),
            int(by_id["public-service"]["priority"]),
        )

    def test_dedicated_skill_directory_exists(self) -> None:
        skill = (
            Path(__file__).resolve().parents[2]
            / "skills"
            / "lvke-urban-rail-transit"
            / "SKILL.md"
        )
        self.assertTrue(skill.is_file(), skill)
        text = skill.read_text(encoding="utf-8")
        self.assertIn("name: lvke-urban-rail-transit", text)
        self.assertIn("rail_transit", text)


if __name__ == "__main__":
    unittest.main()
