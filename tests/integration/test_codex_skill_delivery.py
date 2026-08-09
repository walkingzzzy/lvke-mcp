from __future__ import annotations

import json
import re
import unittest
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lvke_mcp.domains.finance.env_templates import env_profile
from lvke_mcp.domains.finance.industry_aliases import normalize_industry
from lvke_mcp.domains.finance.scale_infer import _resolve
from lvke_mcp.domains.research.providers import tavily
from lvke_mcp.domains.reports._doc_service.outline import REPORT_STRUCTURES
from lvke_mcp.testing.server_manifest import SERVER_SPECS


ROOT = Path(__file__).resolve().parents[2]
PUBLISHED_SKILLS = {
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
}


class CodexSkillDeliveryTest(unittest.TestCase):
    def test_catalogs_are_relative_and_resolve_inside_each_skill(self) -> None:
        for catalog in sorted((ROOT / "skills").glob("*/references/catalog.md")):
            text = catalog.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"/(?:Users|home|root)/")
            links = re.findall(r"\]\(([^)]+/SKILL\.md)\)", text)
            self.assertTrue(links, catalog)
            for link in links:
                with self.subTest(catalog=catalog, link=link):
                    self.assertFalse(Path(link).is_absolute())
                    self.assertTrue((catalog.parent / link).is_file())

    def test_plugin_publishes_only_non_frontend_codex_skills(self) -> None:
        plugin_root = ROOT / "plugins" / "lvke-mcp"
        manifest = json.loads(
            (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "lvke-mcp")
        published = {
            path.name
            for path in (plugin_root / "skills").iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        }
        self.assertEqual(published, PUBLISHED_SKILLS)
        for name in published:
            self.assertTrue((plugin_root / "skills" / name / "agents" / "openai.yaml").is_file())

        mcp_config = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(mcp_config["mcpServers"]),
            {spec.name for spec in SERVER_SPECS},
        )
        acquisition = mcp_config["mcpServers"]["lvke-data-acquisition"]
        self.assertEqual(
            acquisition["env"]["TAVILY_MCP_URL"],
            "https://tavily.ivanli.cc/mcp",
        )
        self.assertIn("TAVILY_MCP_BEARER_TOKEN_FILE", acquisition["env"])
        self.assertIn("TAVILY_MCP_BEARER_TOKEN", acquisition["env_vars"])
        self.assertNotRegex(json.dumps(mcp_config), r"Bearer\s+\S+")

    def test_plugin_skill_copies_match_publishable_sources(self) -> None:
        plugin_skills = ROOT / "plugins" / "lvke-mcp" / "skills"
        all_plugin_skill_files = set(plugin_skills.rglob("SKILL.md"))
        self.assertEqual(
            all_plugin_skill_files,
            {plugin_skills / name / "SKILL.md" for name in PUBLISHED_SKILLS},
        )
        for name in PUBLISHED_SKILLS:
            source_root = ROOT / "skills" / name
            plugin_root = plugin_skills / name
            source_files = {
                (
                    path.relative_to(source_root).with_name("REFERENCE.md")
                    if path.name == "SKILL.md" and path != source_root / "SKILL.md"
                    else path.relative_to(source_root)
                ): path
                for path in source_root.rglob("*")
                if path.is_file() and "self-improvement" not in path.parts
            }
            plugin_files = {
                path.relative_to(plugin_root)
                for path in plugin_root.rglob("*")
                if path.is_file()
            }
            with self.subTest(skill=name):
                self.assertEqual(plugin_files, set(source_files))
                for relative, source_path in source_files.items():
                    plugin_path = plugin_root / relative
                    if source_path.suffix == ".md":
                        expected = source_path.read_text(encoding="utf-8").replace(
                            "SKILL.md", "REFERENCE.md"
                        )
                        self.assertEqual(plugin_path.read_text(encoding="utf-8"), expected)
                    else:
                        self.assertEqual(plugin_path.read_bytes(), source_path.read_bytes())

    def test_published_skills_use_codex_and_current_review_tools(self) -> None:
        skill_root = ROOT / "plugins" / "lvke-mcp" / "skills"
        content = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in skill_root.rglob("*.md")
        )
        self.assertNotIn("Claude Code", content)
        self.assertNotIn(".claude/", content)
        self.assertNotRegex(content, r"\breview_attest\s*\(")
        self.assertNotRegex(content, r"\breview_release\s*\(")
        self.assertNotRegex(content, r"\b(?:Exa|Firecrawl|ddgs)\b", re.I)
        self.assertNotRegex(content, r"(?:≥2 通道|独立 provider|同步前端|web/src)")
        self.assertNotRegex(content, r"/(?:Users|home|root)/")

        registered: set[str] = set()
        for spec in SERVER_SPECS:
            server = import_module(spec.module).build_server()
            registered.update(tool.name for tool in server.tool_specs)
        expected_calls = {
            "project_context_create",
            "project_context_validate",
            "project_context_revise",
            "planning_get_object",
            "review_list_rubrics",
            "review_score_section",
            "review_compare_assessments",
            "report_propose_section",
            "report_diff",
            "report_apply",
            "source_import_content",
            "data_capture_source_view",
        }
        self.assertLessEqual(expected_calls, registered)

    def test_product_runtime_has_no_identity_or_permission_layer(self) -> None:
        forbidden = re.compile(r"\b(actor|tenant|rbac|permission|authentication)\b", re.I)
        for path in (ROOT / "src" / "lvke_mcp").rglob("*"):
            if path.suffix not in {".py", ".yaml", ".yml"}:
                continue
            with self.subTest(path=path):
                self.assertIsNone(
                    forbidden.search(path.read_text(encoding="utf-8", errors="replace"))
                )

    def test_url_skill_matches_source_import_and_audit_contracts(self) -> None:
        skill = (
            ROOT
            / "skills/lvke-source-evidence/references/preserved"
            / "lvke-url-audit-fetch-visual-chain/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn('declared_mime="image/png"', skill)
        self.assertIn("content_base64=", skill)
        self.assertNotIn("source_import_content(content_type=", skill)
        self.assertIn("只做本地 URL/公网目标检查，不联网", skill)

        server = import_module("lvke_mcp.servers.lvke_source_files.server").build_server()
        required = set(server._tools["source_import_content"].input_schema["required"])
        self.assertEqual(
            required,
            {
                "workspace_id",
                "original_filename",
                "declared_mime",
                "content_base64",
                "idempotency_key",
            },
        )

    def test_market_contract_contains_all_customer_dimensions(self) -> None:
        skill = (
            ROOT
            / "skills/lvke-project-planning/references/preserved"
            / "lvke-market-analysis-output-contract/SKILL.md"
        ).read_text(encoding="utf-8")
        for required in (
            "Industry Form",
            "Target Market Environment",
            "Industry and Supply Chains",
            "Product/Service Competitiveness",
            "Marketing Strategy",
        ):
            self.assertIn(required, skill)

    def test_industry_aliases_prefer_specific_codes_and_cover_solar(self) -> None:
        self.assertEqual(normalize_industry("warehouse_storage"), "仓储物流")
        for value in ("光伏", "photovoltaic", "solar_power", "pv_power"):
            with self.subTest(value=value):
                self.assertEqual(normalize_industry(value), "能源")
                self.assertEqual(_resolve(value).get("_matched"), "能源")
                self.assertEqual(env_profile(value).get("matched"), "能源")

    def test_modern_feasibility_outlines_require_energy_use_section(self) -> None:
        for report_type in ("gov10", "gov9", "ent9", "ent14"):
            with self.subTest(report_type=report_type):
                subs = {
                    sub
                    for chapter in REPORT_STRUCTURES[report_type]["chapters"]
                    for sub in chapter.get("subs", [])
                }
                self.assertIn("项目用能情况", subs)

    def test_tavily_token_supports_env_or_user_secret_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "token"
            path.write_text("Bearer file-token\n", encoding="utf-8")
            with mock.patch.dict(
                "os.environ",
                {
                    "TAVILY_MCP_BEARER_TOKEN": "",
                    "TAVILY_MCP_BEARER_TOKEN_FILE": str(path),
                },
                clear=False,
            ):
                self.assertEqual(
                    tavily._authorization_headers(),
                    {"Authorization": "Bearer file-token"},
                )

        with mock.patch.dict(
            "os.environ",
            {"TAVILY_MCP_BEARER_TOKEN": "Bearer env-token"},
            clear=False,
        ):
            self.assertEqual(
                tavily._authorization_headers(),
                {"Authorization": "Bearer env-token"},
            )


if __name__ == "__main__":
    unittest.main()
