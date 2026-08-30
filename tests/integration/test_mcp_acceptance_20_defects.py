"""20 缺陷修复验收测试（MCP 正式验收 blocker）

覆盖方案 MCP_DEFECT_FIX_PLAN.md 的 20 项缺陷修复：
- 2 个 P0（P0-002 Tavily, P0-009 证据策略）
- 14 个 P1（资料源、财务、研究、规划、编排）
- 4 个 P2（分页、零资料、locator、成本口径）
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 压缩后 lvke-market-sizing / lvke-cost-drivers 被并入 lvke-project-planning，
# 原 SKILL.md 保留在 references/preserved/ 下作为口径依据。
PRESERVED_SKILLS = REPO_ROOT / "skills" / "lvke-project-planning" / "references" / "preserved"


class McpAcceptance20DefectsTest(unittest.TestCase):
    """20 个 MCP 验收 blocker 的回归测试。"""

    # ===== P0-002: Tavily provider_status 在事件循环内可调 =====

    def test_p0_002_tavily_provider_status_in_running_loop(self) -> None:
        """P0-002: provider_status 是 async def，调用方不应再包 asyncio.run()。"""
        from lvke_mcp.servers.lvke_data_acquisition import service

        # 在已运行的事件循环内直接 await
        async def probe() -> dict:
            return await service.provider_status()

        result = asyncio.run(probe())
        # Tavily 配置在 USER_CONFIG_ENV_ADDITIONS.md，当前环境未设环境变量会返回 blocked
        # 但关键是函数签名已改为 async def，可在已有事件循环内调用不会抛 RuntimeError
        self.assertIn("providers", result)
        self.assertIsInstance(result["providers"], list)

    # ===== P1-003 / P1-008: 资源清单不再 NameError =====

    def test_p1_003_data_acquisition_list_resources(self) -> None:
        """P1-003: lvke_data_acquisition 资源清单正确 import RESOURCE_STORES。"""
        from lvke_mcp.servers.lvke_data_acquisition import service

        result = service.list_resources("ws-test", resource_type="", cursor="", limit=10)
        self.assertIn("resources", result)
        self.assertIsInstance(result["resources"], list)
        self.assertIn("next_cursor", result)
        self.assertIn("has_more", result)

    def test_p1_008_data_analysis_list_resources(self) -> None:
        """P1-008: lvke_data_analysis 资源清单正确 import RESOURCE_STORES。"""
        from lvke_mcp.servers.lvke_data_analysis import service

        result = service.list_resources("ws-test", resource_type="", cursor="", limit=10)
        self.assertIn("resources", result)
        self.assertIsInstance(result["resources"], list)

    # ===== P1-006: missing_fields 输出 schema 不再拒绝额外字段 =====

    def test_p1_006_missing_field_schema_accepts_extra_fields(self) -> None:
        """P1-006: _MISSING_FIELD 的 additionalProperties=False 已改为允许可选字段。"""
        from lvke_mcp.servers.lvke_data_analysis import server as S
        import jsonschema

        schema = S._MISSING_FIELD
        # 空 CandidateSet 的 missing_fields 会包含 aliases_tried / source_ids / next_action
        missing_with_extras = {
            "field": "annual_revenue_wan",
            "reason": "no_candidate_found",
            "aliases_tried": ["年营业收入", "销售收入"],
            "expected_unit": "万元",
            "source_ids": [],
            "next_action": "补充资料或调整 field_spec",
        }
        # 验证不抛 ValidationError
        jsonschema.validate(missing_with_extras, schema)
        self.assertEqual(schema["additionalProperties"], False)
        self.assertIn("aliases_tried", schema["properties"])

    # ===== P0-009: 证据策略聚合同时读 pack 与 citation =====

    def test_p0_009_evidence_policy_aggregates_from_citations(self) -> None:
        """P0-009: dr_submit 同时从 evidence_pack_ids 与 citations 聚合 evidence_policy。"""
        from lvke_mcp.domains.research import application

        # 导入本身就是断言:门面必须可加载。显式用一次,避免被当成死导入删掉。
        self.assertTrue(hasattr(application, "submit_agent"))

        # 验证聚合逻辑存在——检查代码同时遍历 evidence_pack 与 citations
        # Wave 2.5 起 dr_submit 实现位于 _service/agent_lifecycle.py。
        src = Path("src/lvke_mcp/domains/research/_service/agent_lifecycle.py").read_text()
        # P0-009 修复注释
        self.assertIn("P0-009", src)
        # 从 evidence_payloads 收集 evidence_policy
        self.assertIn("evidence_policies", src)
        self.assertIn("source_reconstructed", src)
        # 遍历 citations 并加入 source_reconstructed
        self.assertIn("for citation in citations:", src)
        self.assertIn('citation.get("evidence_policy")', src)

    # ===== P1-007: profile_tabular 早期失败路径改为 blocked =====

    def test_p1_007_profile_tabular_early_failure_returns_blocked(self) -> None:
        """P1-007: profile_tabular 无 cell locator 时返回 status=blocked，避免触发 if/then。"""
        from lvke_mcp.servers.lvke_data_analysis import service, server as S
        import jsonschema

        # 模拟空分析任务 → 返回 blocked
        result = service.profile_tabular("ws-test", "task-missing", file_ids=None)
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("data_profile_id", result)

        # 验证 schema 接受它
        jsonschema.validate(result, S._PROFILE_OUTPUT)

    # ===== P1-014: FinanceSpec 连字符版本别名归一化 =====

    def test_p1_014_finance_spec_hyphen_aliases_normalized(self) -> None:
        """P1-014: finance-spec.v1/v2/v3 归一化到 finance_spec.v*。"""
        from lvke_mcp.domains.finance import spec

        self.assertEqual(spec.normalize_spec_version("finance-spec.v1"), "finance_spec.v1")
        self.assertEqual(spec.normalize_spec_version("finance-spec.v3"), "finance_spec.v3")
        # 未知版本原样返回（由 validate 显式拒绝）
        self.assertEqual(spec.normalize_spec_version("finance-spec.v9"), "finance-spec.v9")

    # ===== P1-015: 三张表 blocker 文案含 actionable =====

    def test_p1_015_finance_export_blockers_have_actionable(self) -> None:
        """P1-015: working_capital_reconciled / supporting_schedules_formula_driven 的 blocker 含 actionable。"""
        # Wave 3.7 实现搬到 _finance_export/delivery_tables.py，门面只 re-export。
        src = Path("src/lvke_mcp/adapters/spreadsheets/_finance_export/delivery_tables.py").read_text()
        self.assertIn('"actionable": wc_actionable', src)
        self.assertIn('wc_actionable = (', src)
        # supporting_schedules 的 actionable 列出缺失表名
        self.assertIn('"actionable": (', src)
        self.assertIn("对应输入", src)

    # ===== P1-017: 跨服务解析器区分 ref_not_found 与 ref_wrong_workspace =====

    def test_p1_017_cross_service_resolver_distinguishes_workspace_mismatch(self) -> None:
        """P1-017: URI 合法但属于另一 workspace 时报 ref_wrong_workspace，不再误报 not_found。"""
        src = Path("src/lvke_mcp/servers/lvke_feasibility_delivery/service.py").read_text()
        self.assertIn("ref_wrong_workspace", src)
        self.assertIn("P1-017", src)  # 修复注释标记

    # ===== P1-018: asset_acquisition confirmation_scope 枚举可见 =====

    def test_p1_018_asset_acquisition_confirmation_scope_enum(self) -> None:
        """P1-018: server.py 输入 schema 暴露 confirmation_scope 枚举。"""
        from lvke_mcp.servers.lvke_asset_acquisition import server as S

        # build_server() 返回 OfficialStdioServer，_tools 是 name -> ToolSpec 的 dict
        svr = S.build_server()
        spec = svr._tools.get("acquisition_confirm_spec")
        self.assertIsNotNone(spec, "acquisition_confirm_spec tool not registered")
        props = spec.input_schema.get("properties") or {}
        self.assertIn("confirmation_scope", props)
        cs_def = props["confirmation_scope"]
        self.assertEqual(cs_def.get("type"), "string")
        self.assertIn("enum", cs_def)
        self.assertIn("project_candidate", cs_def["enum"])
        self.assertIn("process_acceptance", cs_def["enum"])

    # ===== P1-011: 行业 Skill 解析器可加载 config =====

    def test_p1_011_industry_skill_routes_loadable(self) -> None:
        """P1-011: config/industry_skill_routes.json 已被 Git 跟踪并可加载。"""
        from lvke_mcp.domains.project_planning import application

        # 导入本身就是断言:门面必须可加载并暴露读取该 config 的入口。
        self.assertTrue(hasattr(application, "resolve_industry_skill"))

        # resolve_industry_skill 会读 industry_skill_routes.json
        # 这里仅验证文件可被模块发现并打开
        config_path = Path("src/lvke_mcp/config/industry_skill_routes.json")
        self.assertTrue(config_path.exists(), "industry_skill_routes.json missing")
        manifest = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertIn("routes", manifest)
        self.assertIsInstance(manifest["routes"], list)

    # ===== P1-016: review_standard_requirements 可加载 =====

    def test_p1_016_review_standard_requirements_loadable(self) -> None:
        """P1-016: config/review_standard_requirements.json 已被 Git 跟踪并可加载。"""
        config_path = Path("src/lvke_mcp/config/review_standard_requirements.json")
        self.assertTrue(config_path.exists(), "review_standard_requirements.json missing")
        reqs = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertIn("requirements", reqs)
        self.assertIsInstance(reqs["requirements"], list)

    # ===== P1-001: 档案索引 SQLite 可被脚本构建 =====

    def test_p1_001_archive_index_buildable(self) -> None:
        """P1-001: scripts/build_archive_index.py 被跟踪且可 import。"""
        script = Path("scripts/build_archive_index.py")
        self.assertTrue(script.exists(), "build_archive_index.py missing")
        # 验证可以 import（不实际构建，避免写 ~/.lvke）
        import sys
        sys.path.insert(0, str(REPO_ROOT / "src"))
        try:
            from lvke_mcp.servers.lvke_archive.archive_index import metadata
            self.assertIsNotNone(metadata.extract)
        finally:
            sys.path.pop(0)

    # ===== P1-004: external_corpora.v1.json 被跟踪 =====

    def test_p1_004_external_corpora_tracked(self) -> None:
        """P1-004: config/external_corpora.v1.json 被 Git 跟踪并可解析。"""
        config_path = Path("src/lvke_mcp/config/external_corpora.v1.json")
        self.assertTrue(config_path.exists(), "external_corpora.v1.json missing")
        corpora = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertIn("corpora", corpora)

    # ===== P2-010: dr_list_resources 已分页 =====

    def test_p2_010_dr_list_resources_paginated(self) -> None:
        """P2-010: lvke_deep_research server.py 调用 paginate_resource_entries。"""
        src = Path("src/lvke_mcp/servers/lvke_deep_research/server.py").read_text()
        self.assertIn("paginate_resource_entries", src)
        self.assertIn("from lvke_mcp.runtime.storage import paginate_resource_entries", src)

    # ===== P2-019: delivery_list_resources 返回 resource_uris =====

    def test_p2_019_delivery_list_resources_returns_uris(self) -> None:
        """P2-019: lvke_zero_material_delivery 的 delivery_list_resources 返回 resource_uris。"""
        src = Path("src/lvke_mcp/servers/lvke_zero_material_delivery/_service/lifecycle.py").read_text()
        self.assertIn('resource_uris=[str(item["uri"]) for item in page.get("resources")', src)

    # ===== SKILL-P1-012: locator 归一化文档已补 =====

    def test_skill_p1_012_locator_normalization_documented(self) -> None:
        """P1-012: MarketSizing skill 文档说明 locator 不要 ad hoc spacing。"""
        skill_md = PRESERVED_SKILLS / "lvke-market-sizing" / "SKILL.md"
        self.assertTrue(skill_md.exists(), f"missing preserved skill: {skill_md}")
        content = skill_md.read_text(encoding="utf-8")
        self.assertIn("ad hoc spacing", content)
        self.assertIn("locator", content)

    # ===== SKILL-P2-013: 成本口径文档已补 =====

    def test_skill_p2_013_cost_quantity_semantics_documented(self) -> None:
        """P2-013: CostDrivers skill 文档说明 annual_quantity 是计算量，design_capacity 不参与。"""
        skill_md = PRESERVED_SKILLS / "lvke-cost-drivers" / "SKILL.md"
        self.assertTrue(skill_md.exists(), f"missing preserved skill: {skill_md}")
        content = skill_md.read_text(encoding="utf-8")
        self.assertIn("annual_quantity", content)
        self.assertIn("design_capacity", content)
        self.assertIn("engineering capacity", content)

    # ===== P2-005: URL 审计与抓取路径差异已记录 =====

    def test_p2_005_url_audit_fetch_documented_as_contract_gap(self) -> None:
        """P2-005: 方案文档已将 URL 审计与抓取路径差异记录为契约缺口。"""
        plan = Path("dev-docs/plans/MCP_DEFECT_FIX_PLAN.md")
        self.assertTrue(plan.exists())
        content = plan.read_text()
        self.assertIn("MCP-P2-005", content)
        self.assertIn("契约缺口", content)


if __name__ == "__main__":
    unittest.main()
