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


_ALL_PUBLISHED_SKILLS = (
    "lvke-api-contract",
    "lvke-backend",
    "lvke-delivery-guardrails",
    "lvke-error-recovery",
    "lvke-feasibility-study",
    "lvke-finance",
    "lvke-local-verify",
    "lvke-mcp-acceptance",
    "lvke-project-planning",
    "lvke-report",
    "lvke-research",
    "lvke-review-release",
    "lvke-source-evidence",
    "lvke-tool-coordination",
    "lvke-urban-rail-transit",
)


class _HostInventoryCase(_PlanningCase):
    """Declare a host Skill inventory explicitly, never reusing the implementation.

    此前测试直接 import 实现里的 ``_installed_skill_names()`` 来算期望值，于是实现
    和测试用的是同一个函数：实现错了，期望值跟着一起错，测试照样全绿。现在测试自己
    声明宿主 inventory，实现只负责消费它。
    """

    def setUp(self) -> None:
        super().setUp()
        self.previous_inventory = os.environ.get("LVKE_MCP_SKILL_INVENTORY")
        self._declare(_ALL_PUBLISHED_SKILLS)

    def tearDown(self) -> None:
        if self.previous_inventory is None:
            os.environ.pop("LVKE_MCP_SKILL_INVENTORY", None)
        else:
            os.environ["LVKE_MCP_SKILL_INVENTORY"] = self.previous_inventory
        super().tearDown()

    def _declare(self, names: tuple[str, ...] | list[str]) -> None:
        os.environ["LVKE_MCP_SKILL_INVENTORY"] = json.dumps(list(names))


class RailSkillRouteTest(_HostInventoryCase):
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

    def test_rail_capabilities_resolve_to_real_reference_files(self) -> None:
        """城轨六项能力必须各自有真实存在的口径参考文件。

        manifest 原先声明 6 个 lvke-rail-* 专用 Skill，磁盘上都不存在；后来改成三个
        父 Skill，但那三个 Skill 里对"轨道/清分/车公里"并无承载内容，等于仍然静默
        丢掉这六处最容易错的口径。现在每项能力绑定 carrier_skill + reference_path，
        并在此断言文件真的存在且提到本能力的关键口径词。
        """

        resolved = service.resolve_industry_skill(
            self.workspace, self._context("urban_rail_transit")
        )
        references = resolved["industry_references"]
        self.assertEqual(
            [item["capability"] for item in references],
            [
                "轨道客流预测与清分票价",
                "线站位与敷设方案",
                "投资估算与单位里程强度",
                "车公里运营成本",
                "车辆更新与大修周期",
                "运营定员编制",
            ],
        )
        self.assertEqual([], resolved["unresolved_industry_references"])
        skills_root = Path(__file__).resolve().parents[2] / "skills"
        expected_terms = {
            "轨道客流预测与清分票价": "清分",
            "线站位与敷设方案": "敷设",
            "投资估算与单位里程强度": "亿元/公里",
            "车公里运营成本": "车公里",
            "车辆更新与大修周期": "大修",
            "运营定员编制": "班制",
        }
        for item in references:
            with self.subTest(capability=item["capability"]):
                self.assertTrue(item["reference_resolved"])
                path = skills_root / item["carrier_skill"] / item["reference_path"]
                self.assertTrue(path.is_file(), path)
                self.assertIn(
                    expected_terms[item["capability"]],
                    path.read_text(encoding="utf-8"),
                )

    def test_every_route_declares_resolvable_skills_and_references(self) -> None:
        """任何路由的主 Skill、辅助 Skill 与行业参考都必须真实存在。

        修复前 8 条路由里 7 条的主 Skill 指向不存在的 lvke-industry-context，
        14 个辅助 Skill 无一存在，而守门测试只读 JSON 不查磁盘，因此长期全绿。
        """

        manifest = json.loads(_ROUTES.read_text(encoding="utf-8"))
        skills_root = Path(__file__).resolve().parents[2] / "skills"
        published = set(_ALL_PUBLISHED_SKILLS)
        for route in manifest["routes"]:
            with self.subTest(route=route["route_id"]):
                self.assertIn(route["primary_skill"], published)
                for name in route.get("auxiliary_skills") or []:
                    self.assertIn(name, published)
                # 通用编排 Skill 不含行业口径，因此每条路由都必须另外给出行业参考。
                references = route.get("industry_references") or []
                self.assertTrue(references, route["route_id"])
                for item in references:
                    self.assertIn(item["carrier_skill"], published)
                    path = skills_root / item["carrier_skill"] / item["reference_path"]
                    self.assertTrue(path.is_file(), path)

    def test_absent_auxiliary_skills_are_reported_not_hidden(self) -> None:
        """辅助 Skill 缺失必须逐个如实报出，且不得再自称完全成功。"""

        self._declare(["lvke-urban-rail-transit", "lvke-project-planning", "lvke-finance"])
        resolved = service.resolve_industry_skill(
            self.workspace, self._context("urban_rail_transit")
        )
        self.assertEqual(["lvke-research"], resolved["missing_skills"])
        self.assertEqual("partial", resolved["status"])
        self.assertFalse(resolved["success"])
        self.assertIn(
            "auxiliary_skill_not_installed:lvke-research", resolved["warnings"]
        )

    def test_missing_primary_skill_blocks_the_route(self) -> None:
        """主 Skill 未加载时必须阻断，不能返回一个无法加载的名字。"""

        self._declare(["lvke-research"])
        resolved = service.resolve_industry_skill(
            self.workspace, self._context("urban_rail_transit")
        )
        self.assertFalse(resolved["success"])
        self.assertEqual("blocked", resolved["status"])
        self.assertEqual("industry_skill_not_installed", resolved["code"])
        self.assertIn("lvke-urban-rail-transit", resolved["missing_skills"])
        # 阻断也必须带路由 lineage，否则 manifest 变更后无法审计是哪一版阻断的。
        self.assertEqual("urban-rail-transit", resolved["lineage"]["route_id"])
        self.assertTrue(resolved["lineage"]["route_manifest_version"])
        self.assertTrue(resolved["lineage"]["route_manifest_hash"].startswith("sha256:"))

    def test_route_not_found_still_carries_manifest_lineage(self) -> None:
        resolved = service.resolve_industry_skill(
            self.workspace, self._context("no_such_industry_at_all")
        )
        self.assertEqual("industry_skill_route_not_found", resolved["code"])
        self.assertTrue(resolved["lineage"]["route_manifest_version"])
        self.assertTrue(resolved["lineage"]["route_manifest_hash"].startswith("sha256:"))
        self.assertEqual(
            resolved["lineage"]["project_context_id"], resolved["project_context_id"]
        )

    def test_disk_presence_alone_never_certifies_loadability(self) -> None:
        """宿主没声明清单时不得报 ok：磁盘上有只说明仓库里写了它。

        这正是原实现的假阳性来源 —— 源码 checkout 存在即返回 installed，而 Codex
        实际加载的是 ~/.codex/plugins/cache/.../skills。
        """

        os.environ.pop("LVKE_MCP_SKILL_INVENTORY", None)
        resolved = service.resolve_industry_skill(
            self.workspace, self._context("urban_rail_transit")
        )
        self.assertEqual("disk_offline", resolved["skill_inventory_source"])
        self.assertEqual("partial", resolved["status"])
        self.assertFalse(resolved["success"])
        self.assertEqual("skill_loadability_unverified", resolved["code"])
        self.assertIn("skill_loadability_unverified", resolved["warnings"])

    def test_host_declared_inventory_takes_precedence_over_disk(self) -> None:
        """宿主声明缺失时以宿主为准，即使磁盘上存在该 Skill。"""

        self._declare(["lvke-urban-rail-transit", "lvke-research", "lvke-finance"])
        resolved = service.resolve_industry_skill(
            self.workspace, self._context("urban_rail_transit")
        )
        self.assertEqual("host_declared", resolved["skill_inventory_source"])
        # lvke-project-planning 在磁盘上确实存在，但宿主没声明加载它。
        self.assertTrue(
            (Path(__file__).resolve().parents[2] / "skills" / "lvke-project-planning" / "SKILL.md").is_file()
        )
        self.assertIn("lvke-project-planning", resolved["missing_skills"])

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
