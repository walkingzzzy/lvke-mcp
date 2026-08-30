"""报告配置化：同输入同 hash、跨行业不同章节、无匹配 fail-closed、固定正文已移除。

这组测试守的是"报告内容不再由 Python 固定正文决定"这一条。它有四个可被独立
破坏的部分，因此分四组断言：

1. 确定性：同一输入必须得到同一份配置、同一组章节、同一个 content_hash。
2. 差异性：换行业/项目类型必须真的换章节——否则"配置化"只是把同一份模板挪了个
   地方。
3. fail-closed：零命中、同优先级冲突、显式指定不存在都必须阻断，不静默套用通用
   模板。
4. 固定正文已移除：业务代码里不能再有写死的章节列表或正文 f-string。
"""

from __future__ import annotations

import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path

from lvke_mcp.runtime.package_config import (
    PackageConfigError,
    load_versioned_config,
    package_config_path,
)
from lvke_mcp.servers.lvke_zero_material_delivery._service import (
    report_profiles,
    report_render,
)


class PackageConfigLoaderTest(unittest.TestCase):
    def test_content_hash_is_recomputable_from_the_body(self) -> None:
        from lvke_mcp.runtime.storage import sha256_json

        document = load_versioned_config(
            "report_profiles",
            "generic-gov10.v1.json",
            expected_schema_version="lvke-report-profile.v1",
        )
        body = {key: value for key, value in document.items() if key != "content_hash"}
        self.assertEqual(document["content_hash"], sha256_json(body))

    def test_schema_version_mismatch_fails_closed(self) -> None:
        with self.assertRaises(PackageConfigError) as caught:
            load_versioned_config(
                "report_profiles",
                "generic-gov10.v1.json",
                expected_schema_version="lvke-report-profile.v999",
            )
        self.assertEqual(caught.exception.code, "package_config_schema_version_mismatch")

    def test_every_shipped_profile_consumes_its_required_fields(self) -> None:
        """必填字段必须被某个章节槽位引用，否则追问了也不影响正文。"""

        manifest = load_versioned_config(
            "report_profiles",
            "manifest.v1.json",
            expected_schema_version="lvke-report-profiles.v1",
        )
        for row in manifest["profiles"]:
            with self.subTest(profile_id=row["profile_id"]):
                # load_profile_document 自带这道一致性门禁，能加载即为通过。
                document = report_profiles.load_profile_document(row["document"])
                consumed = {
                    slot
                    for chapter in document["chapters"]
                    for sub in chapter.get("subs") or []
                    for slot in sub.get("slots") or []
                }
                self.assertEqual(
                    [f for f in document["required_fields"] if f not in consumed], []
                )

    def test_every_shipped_profile_covers_the_argument_chain(self) -> None:
        """章节标题必须命中审查侧的论证链词组。

        审查的 FEASIBILITY.STRUCTURE.COVERAGE 按词组扫正文，"客流预测"这种
        语义对但用词不匹配的标题会让该维度恒判 P1，而根因隔了三层才看得到。
        配置加载期就该挡住。
        """

        manifest = load_versioned_config(
            "report_profiles",
            "manifest.v1.json",
            expected_schema_version="lvke-report-profiles.v1",
        )
        for row in manifest["profiles"]:
            with self.subTest(profile_id=row["profile_id"]):
                document = report_profiles.load_profile_document(row["document"])
                self.assertEqual(
                    report_profiles._missing_argument_groups(document), []
                )

    def test_argument_chain_groups_mirror_the_review_domain(self) -> None:
        """本地镜像表必须与审查侧 required_groups 完全一致，否则静默偏差。"""

        from lvke_mcp.servers.lvke_deliverable_review._service import suite_review

        source = inspect.getsource(suite_review)
        start = source.index("required_groups = {")
        block = source[start : source.index("}", start)]
        for name, terms in report_profiles.ARGUMENT_CHAIN_GROUPS.items():
            with self.subTest(group=name):
                self.assertIn(f'"{name}"', block)
                for term in terms:
                    self.assertIn(f'"{term}"', block)

    def test_missing_config_reports_not_found_not_a_default(self) -> None:
        with self.assertRaises(PackageConfigError) as caught:
            load_versioned_config("report_profiles", "no-such-profile.v1.json")
        self.assertEqual(caught.exception.code, "package_config_not_found")

    def test_path_traversal_is_refused(self) -> None:
        for parts in ((".."), ("..", "manifest.v1.json"), ("a/b",)):
            with self.subTest(parts=parts):
                with self.assertRaises(PackageConfigError):
                    package_config_path(*([parts] if isinstance(parts, str) else parts))


class ProfileResolutionTest(unittest.TestCase):
    def test_same_input_resolves_to_the_same_frozen_configuration(self) -> None:
        selector = {
            "industry_code": "tourism_catering",
            "project_type": "generic_feasibility",
            "transaction_structure": "new_build",
            "asset_type": "general",
            "report_type": "可行性研究报告",
        }
        first = report_profiles.resolve_profile(**selector)
        second = report_profiles.resolve_profile(**selector)
        self.assertEqual(first["selection"], second["selection"])
        self.assertEqual(
            report_profiles.chapter_titles(first["profile"]),
            report_profiles.chapter_titles(second["profile"]),
        )
        self.assertTrue(first["selection"]["profile_content_hash"].startswith("sha256:"))

    def test_different_industries_get_different_chapters(self) -> None:
        rail = report_profiles.resolve_profile(
            industry_code="urban_rail_transit",
            project_type="generic_feasibility",
            transaction_structure="new_build",
        )
        tourism = report_profiles.resolve_profile(
            industry_code="tourism_catering",
            project_type="generic_feasibility",
            transaction_structure="new_build",
        )
        acquisition = report_profiles.resolve_profile(
            industry_code="energy_utilities",
            project_type="asset_acquisition",
            transaction_structure="asset_acquisition",
        )
        rail_titles = report_profiles.chapter_titles(rail["profile"])
        tourism_titles = report_profiles.chapter_titles(tourism["profile"])
        acquisition_titles = report_profiles.chapter_titles(acquisition["profile"])
        self.assertNotEqual(rail_titles, tourism_titles)
        self.assertNotEqual(acquisition_titles, tourism_titles)
        # 判"该行业独有的论证对象出现在章节里"，不钉死具体措辞：
        # 标题要同时满足审查侧的论证链词组，用词会随之调整。
        self.assertTrue(any("客流" in title for title in rail_titles), rail_titles)
        self.assertTrue(
            any("权属" in title for title in acquisition_titles), acquisition_titles
        )
        # 三份配置的 template_set_id 与 hash 必须互不相同。
        ids = {
            item["selection"]["template_set_id"] for item in (rail, tourism, acquisition)
        }
        hashes = {
            item["selection"]["profile_content_hash"]
            for item in (rail, tourism, acquisition)
        }
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(hashes), 3)

    def test_unmatched_selector_blocks_instead_of_using_generic(self) -> None:
        with self.assertRaises(report_profiles.ReportProfileError) as caught:
            report_profiles.resolve_profile(
                industry_code="no_such_industry",
                project_type="generic_feasibility",
                transaction_structure="new_build",
            )
        self.assertEqual(caught.exception.code, "report_profile_not_matched")
        # 诊断必须说清看到了哪些候选与下一步，而不是只给一个码。
        self.assertTrue(caught.exception.detail.get("available_profile_ids"))
        self.assertTrue(caught.exception.detail.get("next_actions"))

    def test_explicit_request_wins_and_unknown_request_blocks(self) -> None:
        chosen = report_profiles.resolve_profile(
            industry_code="tourism_catering",
            project_type="generic_feasibility",
            transaction_structure="new_build",
            requested_profile_id="urban-rail-gov10",
        )
        self.assertEqual(chosen["selection"]["profile_id"], "urban-rail-gov10")
        self.assertEqual(chosen["selection"]["selection_method"], "explicit_request")
        with self.assertRaises(report_profiles.ReportProfileError) as caught:
            report_profiles.resolve_profile(
                industry_code="tourism_catering",
                project_type="generic_feasibility",
                requested_profile_id="not-a-profile",
            )
        self.assertEqual(caught.exception.code, "report_profile_not_found")

    def test_conflicting_profile_and_template_set_request_blocks(self) -> None:
        with self.assertRaises(report_profiles.ReportProfileError) as caught:
            report_profiles.resolve_profile(
                industry_code="tourism_catering",
                project_type="generic_feasibility",
                requested_profile_id="generic-gov10",
                requested_template_set_id="lvke-report.urban-rail-gov10.v1",
            )
        self.assertEqual(caught.exception.code, "report_profile_request_ambiguous")

    def test_tied_priority_is_ambiguous_rather_than_arbitrary(self) -> None:
        """同优先级多命中必须阻断，不能按任意顺序挑一个。"""

        with tempfile.TemporaryDirectory(prefix="lvke-profile-conflict-") as root:
            target = Path(root) / "report_profiles"
            target.mkdir(parents=True)
            source = package_config_path("report_profiles")
            for name in ("generic-gov10.v1.json",):
                (target / name).write_text(
                    (source / name).read_text(encoding="utf-8"), encoding="utf-8"
                )
            document = json.loads((target / "generic-gov10.v1.json").read_text(encoding="utf-8"))
            twin = {
                **document,
                "profile_id": "generic-gov10-twin",
                "template_set_id": "lvke-report.generic-gov10-twin.v1",
            }
            (target / "generic-gov10-twin.v1.json").write_text(
                json.dumps(twin, ensure_ascii=False), encoding="utf-8"
            )
            manifest = json.loads(
                (source / "manifest.v1.json").read_text(encoding="utf-8")
            )
            base = next(
                item for item in manifest["profiles"] if item["profile_id"] == "generic-gov10"
            )
            manifest["profiles"] = [
                base,
                {
                    **base,
                    "profile_id": "generic-gov10-twin",
                    "template_set_id": "lvke-report.generic-gov10-twin.v1",
                    "document": "generic-gov10-twin.v1.json",
                },
            ]
            (target / "manifest.v1.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            previous = os.environ.get("LVKE_MCP_PACKAGE_CONFIG_DIR")
            os.environ["LVKE_MCP_PACKAGE_CONFIG_DIR"] = root
            try:
                with self.assertRaises(report_profiles.ReportProfileError) as caught:
                    report_profiles.resolve_profile(
                        industry_code="tourism_catering",
                        project_type="generic_feasibility",
                        transaction_structure="new_build",
                    )
            finally:
                if previous is None:
                    os.environ.pop("LVKE_MCP_PACKAGE_CONFIG_DIR", None)
                else:
                    os.environ["LVKE_MCP_PACKAGE_CONFIG_DIR"] = previous
        self.assertEqual(caught.exception.code, "report_profile_ambiguous")
        self.assertEqual(
            sorted(caught.exception.detail["matched_profile_ids"]),
            ["generic-gov10", "generic-gov10-twin"],
        )


class RendererTest(unittest.TestCase):
    def _slots(self) -> dict:
        return report_render.build_slot_values(
            intent={
                "project_name": "样例项目",
                "region": "湖北省",
                "industry": {"industry_label": "文旅与休闲服务"},
                "project_nature": "新建",
                "report_type": "可行性研究报告",
                "material_state": "client_materials_absent",
                "assurance_level": "estimate_preview",
            },
            assumption_package={
                "industry_profile": {"revenue_model": "tourism"},
                "fields": [
                    {
                        "name": "total_investment_wan",
                        "value": 12000,
                        "unit": "万元",
                        "source_type": "controlled_assumption",
                        "confidence": 0.42,
                        "validation_condition": "须以合同替换",
                    }
                ],
            },
            finance={
                "run_id": "run_x",
                "consistency_ok": True,
                "total_investment_wan": 12000.0,
                "project_irr": 0.083,
            },
            blockers=[],
            quality_issues=["research_evidence_pending"],
            public_research={"status": "ok", "source_summaries": []},
            skipped_fields=[{"field": "loan_rate", "reason": "user_skipped"}],
        )

    def test_render_is_deterministic_and_follows_the_configured_tree(self) -> None:
        resolved = report_profiles.resolve_profile(
            industry_code="tourism_catering",
            project_type="generic_feasibility",
            transaction_structure="new_build",
        )
        slots = self._slots()
        first, unresolved_a = report_render.render_report_markdown(
            profile=resolved["profile"], selection=resolved["selection"], slots=slots
        )
        second, unresolved_b = report_render.render_report_markdown(
            profile=resolved["profile"], selection=resolved["selection"], slots=slots
        )
        self.assertEqual(first, second)
        self.assertEqual(unresolved_a, unresolved_b)
        for title in report_profiles.chapter_titles(resolved["profile"]):
            self.assertIn(title, first)
        # 配置 hash 必须出现在正文里，便于人工核对这份正文出自哪份配置。
        self.assertIn(resolved["selection"]["profile_content_hash"], first)

    def test_skipped_fields_and_limitations_are_disclosed(self) -> None:
        resolved = report_profiles.resolve_profile(
            industry_code="tourism_catering",
            project_type="generic_feasibility",
            transaction_structure="new_build",
        )
        markdown, _unresolved = report_render.render_report_markdown(
            profile=resolved["profile"], selection=resolved["selection"], slots=self._slots()
        )
        self.assertIn("loan_rate", markdown)
        self.assertIn("research_evidence_pending", markdown)
        self.assertIn(
            resolved["profile"]["disclosure"]["assumption_notice"], markdown
        )

    def test_unresolved_slots_are_reported_not_silently_filled(self) -> None:
        resolved = report_profiles.resolve_profile(
            industry_code="urban_rail_transit",
            project_type="generic_feasibility",
            transaction_structure="new_build",
        )
        _markdown, unresolved = report_render.render_report_markdown(
            profile=resolved["profile"], selection=resolved["selection"], slots=self._slots()
        )
        # 轨道配置引用了 route_length_km / station_count，示例槽位里没有，
        # 必须如实登记为未解析，而不是拿别的字段凑一个数。
        self.assertIn("route_length_km", unresolved)
        self.assertIn("station_count", unresolved)

    def test_promoted_banner_replaces_preview_banner(self) -> None:
        resolved = report_profiles.resolve_profile(
            industry_code="tourism_catering",
            project_type="generic_feasibility",
            transaction_structure="new_build",
        )
        disclosure = resolved["profile"]["disclosure"]
        promoted, _ = report_render.render_report_markdown(
            profile=resolved["profile"],
            selection=resolved["selection"],
            slots=self._slots(),
            promoted=True,
        )
        self.assertIn(disclosure["promoted_banner"], promoted)
        self.assertNotIn(disclosure["preview_banner"], promoted)
        # 晋升后仍必须保留模拟来源说明。
        self.assertIn("sim_a_formal", disclosure["promoted_banner"])


class IntakeSelectionTest(unittest.TestCase):
    """创建期的 project_type 判定必须与编排侧同源。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-intake-profile-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_acquisition_without_special_asset_type_still_picks_acquisition(self) -> None:
        """"收购一家酒店" 的 asset_type 仍是 general，但必须选收购配置。

        此前创建期额外要求 asset_type != general，于是酒店收购落到通用可研配置，
        而编排侧按 _ACQUISITION_KEYWORDS 走收购模型——正文与财务口径分叉。
        """

        from lvke_mcp.servers.lvke_zero_material_delivery import service as zmd

        cases = {
            "收购一家酒店并编制可行性研究报告": "lvke-report.asset-acquisition-9.v1",
            "收购江夏光伏电站资产": "lvke-report.asset-acquisition-9.v1",
            "在湖北新建一座儿童游乐园": "lvke-report.generic-gov10.v1",
        }
        for index, (sentence, expected) in enumerate(cases.items()):
            with self.subTest(sentence=sentence):
                created = zmd.create_from_sentence(
                    {
                        "workspace_id": "intake-profile",
                        "sentence": sentence,
                        "region": "湖北省",
                        "idempotency_key": f"intake-{index}",
                    }
                )
                self.assertEqual(
                    (created.get("report_profile") or {}).get("template_set_id"),
                    expected,
                    created,
                )

    def test_selection_is_frozen_on_both_intent_and_run(self) -> None:
        from lvke_mcp.servers.lvke_zero_material_delivery import service as zmd

        created = zmd.create_from_sentence(
            {
                "workspace_id": "intake-freeze",
                "sentence": "在湖北新建一座儿童游乐园",
                "region": "湖北省",
                "idempotency_key": "freeze-1",
            }
        )
        intent_profile = (created.get("delivery_intent") or {}).get("report_profile") or {}
        run_profile = (created.get("delivery_run") or {}).get("report_profile") or {}
        self.assertEqual(intent_profile, run_profile)
        self.assertTrue(intent_profile.get("profile_content_hash"))
        self.assertTrue(intent_profile.get("profile_manifest_hash"))


class LegacyRecordCompatibilityTest(unittest.TestCase):
    """配置升级不得改写历史运行：v1 老记录原样可读、hash 不变、不重渲染。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-legacy-zmd-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "legacy-zmd"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _legacy_record(self) -> dict:
        from lvke_mcp.adapters.zero_material_repository import REPORT_STORE

        return REPORT_STORE.put(
            self.workspace,
            {
                "object_type": "TechnicalReport",
                "title": "老项目技术预估报告",
                # v1：既没有 report_profile，也没有 unresolved_slots。
                "format_version": "zero-material-technical-report.v1",
                "assurance_level": "estimate_preview",
                "content_markdown": "# 老正文\n\n## 一、项目识别\n\n- 地区：湖北省\n",
                "finance_run_id": "run_old",
                "validation_complete": False,
                "input_evidence_complete": False,
            },
            producer="legacy-compat-test",
            status="partial",
            basis={"legacy": True},
        )

    def test_legacy_record_keeps_its_body_and_hash(self) -> None:
        from lvke_mcp.adapters.zero_material_repository import REPORT_STORE

        created = self._legacy_record()
        again = REPORT_STORE.get(self.workspace, created["object_id"])
        self.assertEqual(again["content_hash"], created["content_hash"])
        self.assertTrue(again["payload"]["content_markdown"].startswith("# 老正文"))
        self.assertEqual(
            again["payload"]["format_version"], "zero-material-technical-report.v1"
        )

    def test_review_can_still_resolve_a_legacy_preview_artifact(self) -> None:
        from lvke_mcp.servers.lvke_deliverable_review._service.target_resolve import (
            _resolve_report_artifact,
        )

        created = self._legacy_record()
        snapshot, _bindings, blockers = _resolve_report_artifact(
            self.workspace,
            created["object_id"],
            artifact_domain="zero_material_preview",
        )
        self.assertEqual(blockers, [])
        self.assertIsNotNone(snapshot)

    def test_frozen_snapshot_replays_after_config_root_changes(self) -> None:
        """配置被移除或部署根变化后，旧运行仍按原配置重放。

        只存 ID+版本+hash 不够：重新加载会 not_found 或 hash 漂移，历史运行
        就再也按不了原配置重放。
        """

        from lvke_mcp.servers.lvke_zero_material_delivery.artifact_delivery import (
            _resolve_report_profile,
        )

        resolved = report_profiles.resolve_profile(
            industry_code="tourism_catering",
            project_type="generic_feasibility",
            transaction_structure="new_build",
        )
        selection = resolved["selection"]
        self.assertTrue(selection.get("profile_snapshot", {}).get("chapters"))
        expected_chapters = report_profiles.chapter_titles(resolved["profile"])

        with tempfile.TemporaryDirectory(prefix="lvke-empty-config-") as empty:
            previous = os.environ.get("LVKE_MCP_PACKAGE_CONFIG_DIR")
            os.environ["LVKE_MCP_PACKAGE_CONFIG_DIR"] = empty
            try:
                profile, echoed, error = _resolve_report_profile(
                    {"report_profile": selection},
                    {"route": {"industry_code": "tourism_catering"}},
                )
            finally:
                if previous is None:
                    os.environ.pop("LVKE_MCP_PACKAGE_CONFIG_DIR", None)
                else:
                    os.environ["LVKE_MCP_PACKAGE_CONFIG_DIR"] = previous
        self.assertEqual(error, "")
        self.assertEqual(report_profiles.chapter_titles(profile), expected_chapters)
        self.assertEqual(
            echoed["profile_content_hash"], selection["profile_content_hash"]
        )

    def test_tampered_snapshot_is_refused(self) -> None:
        from lvke_mcp.servers.lvke_zero_material_delivery.artifact_delivery import (
            _resolve_report_profile,
        )

        resolved = report_profiles.resolve_profile(
            industry_code="tourism_catering",
            project_type="generic_feasibility",
            transaction_structure="new_build",
        )
        selection = dict(resolved["selection"])
        tampered = dict(selection["profile_snapshot"])
        tampered["content_hash"] = "sha256:" + "0" * 64
        selection["profile_snapshot"] = tampered
        _profile, _echoed, error = _resolve_report_profile(
            {"report_profile": selection}, {"route": {}}
        )
        self.assertEqual(error, "report_profile_snapshot_hash_mismatch")

    def test_absent_frozen_profile_falls_back_to_re_resolution(self) -> None:
        """老记录没有冻结配置时按路由重解析，而不是崩掉或留空正文。"""

        from lvke_mcp.servers.lvke_zero_material_delivery.artifact_delivery import (
            _resolve_report_profile,
        )

        created = self._legacy_record()
        profile, selection, error = _resolve_report_profile(
            created["payload"],
            {"route": {"industry_code": "tourism_catering"}},
        )
        self.assertEqual(error, "")
        self.assertTrue(profile.get("chapters"))
        self.assertTrue(selection.get("template_set_id"))


class ConfirmedAnswerReferenceTest(unittest.TestCase):
    """显式答案引用是乐观并发断言：过期即阻断，正确即通过，省略即沿用。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-ansref-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "ansref"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_start_recomputes_gaps_on_the_snapshot_path(self) -> None:
        """快照路径必须照样算缺口。

        缺口计算曾落在 elif 分支内，而新运行都带 snapshot，于是那段代码永远不
        执行：未回答的关键字段被清空、不产生 required_field_unanswered:*，
        正式资格门禁整体失效。
        """

        from lvke_mcp.servers.lvke_zero_material_delivery import service as zmd

        created = zmd.create_from_sentence(
            {
                "workspace_id": self.workspace,
                # 轨道项目的 route_length_km / station_count 等关键字段全不回答。
                "sentence": "在湖北建设城市轨道交通线路",
                "region": "湖北省",
                "idempotency_key": "gap-1",
            }
        )
        self.assertTrue(created.get("missing_inputs"), created)
        started = zmd.start(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": created["delivery_run"]["delivery_run_id"],
                "idempotency_key": "gap-2",
            }
        )
        # 走的确实是快照路径。
        self.assertTrue(
            (started.get("report_profile") or {}).get("profile_snapshot"), started
        )
        self.assertTrue(started.get("missing_inputs"), started)
        limitations = (started.get("gap_summary") or {}).get("release_limitations") or []
        self.assertTrue(
            any(item.startswith("required_field_unanswered:") for item in limitations),
            limitations,
        )
        # 关键字段未答必须真的阻断正式资格。
        formal = (started.get("acceptance") or {}).get("formal") or {}
        self.assertTrue(
            any(
                str(item).startswith("required_field_unanswered:")
                for item in formal.get("blockers") or []
            ),
            formal,
        )

    def test_profile_override_conflicting_with_the_run_is_refused(self) -> None:
        """已按配置 A 验收的运行，不得生成配置 B 的模板包。

        否则配置 B 的 pack 会继承配置 A 的验收结论并据此晋升——验收对象与
        晋升对象不是同一件东西。
        """

        from lvke_mcp.servers.lvke_zero_material_delivery import service as zmd

        created = zmd.create_from_sentence(
            {
                "workspace_id": self.workspace,
                "sentence": "在湖北新建一座儿童游乐园",
                "region": "湖北省",
                "idempotency_key": "ov-1",
            }
        )
        started = zmd.start(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": created["delivery_run"]["delivery_run_id"],
                "idempotency_key": "ov-2",
            }
        )
        package_id = started["assumption_package"]["assumption_package_id"]
        run_id = started["delivery_run"]["delivery_run_id"]
        for index in range(8):
            listed = zmd.list_assumptions(
                {"workspace_id": self.workspace, "assumption_package_id": package_id}
            )
            items = list(listed.get("confirmation_items") or [])
            if not items:
                break
            revised = zmd.confirm_assumptions(
                {
                    "workspace_id": self.workspace,
                    "assumption_package_id": package_id,
                    "confirmations": [
                        {"name": item["name"], "value": item.get("value")}
                        for item in items
                    ],
                    "idempotency_key": f"ov-c{index}",
                }
            )
            package_id = revised["assumption_package"]["assumption_package_id"]
            run_id = revised["delivery_run"]["delivery_run_id"]

        conflicting = zmd.generate_template_pack(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": run_id,
                "report_profile_id": "urban-rail-gov10",
                "idempotency_key": "ov-b",
            }
        )
        self.assertFalse(conflicting.get("success"), conflicting)
        self.assertEqual(
            conflicting.get("code"), "report_profile_override_conflicts_with_run"
        )
        # 沿用运行冻结的配置仍必须可用（不能把正常路径一起堵死）。
        inherited = zmd.generate_template_pack(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": run_id,
                "idempotency_key": "ov-a",
            }
        )
        self.assertTrue(inherited.get("success"), inherited)
        # 显式声明与运行相同的配置也应通过。
        same = zmd.generate_template_pack(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": run_id,
                "report_profile_id": "generic-gov10",
                "idempotency_key": "ov-same",
            }
        )
        self.assertTrue(same.get("success"), same)

    def test_stale_reference_blocks_and_current_one_passes(self) -> None:
        from lvke_mcp.servers.lvke_zero_material_delivery import service as zmd

        created = zmd.create_from_sentence(
            {
                "workspace_id": self.workspace,
                "sentence": "在湖北新建一座儿童游乐园",
                "region": "湖北省",
                "idempotency_key": "ar-1",
            }
        )
        started = zmd.start(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": created["delivery_run"]["delivery_run_id"],
                "idempotency_key": "ar-2",
            }
        )
        package_id = started["assumption_package"]["assumption_package_id"]
        run_id = started["delivery_run"]["delivery_run_id"]
        previous = package_id
        for index in range(8):
            listed = zmd.list_assumptions(
                {"workspace_id": self.workspace, "assumption_package_id": package_id}
            )
            items = list(listed.get("confirmation_items") or [])
            if not items:
                break
            revised = zmd.confirm_assumptions(
                {
                    "workspace_id": self.workspace,
                    "assumption_package_id": package_id,
                    "confirmations": [
                        {"name": item["name"], "value": item.get("value")}
                        for item in items
                    ],
                    "idempotency_key": f"ar-c{index}",
                }
            )
            previous = package_id
            package_id = revised["assumption_package"]["assumption_package_id"]
            run_id = revised["delivery_run"]["delivery_run_id"]
        self.assertNotEqual(previous, package_id)

        stale = zmd.generate_template_pack(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": run_id,
                "confirmed_assumption_package_id": previous,
                "idempotency_key": "ar-stale",
            }
        )
        self.assertFalse(stale.get("success"), stale)
        self.assertEqual(stale.get("code"), "confirmed_assumption_package_stale")
        # 诊断必须同时给出声明值与当前值，否则调用方不知道该改成什么。
        self.assertEqual(stale.get("declared_assumption_package_id"), previous)
        self.assertEqual(stale.get("current_assumption_package_id"), package_id)

        current = zmd.generate_template_pack(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": run_id,
                "confirmed_assumption_package_id": package_id,
                "idempotency_key": "ar-ok",
            }
        )
        self.assertTrue(current.get("success"), current)

        omitted = zmd.generate_template_pack(
            {
                "workspace_id": self.workspace,
                "delivery_run_id": run_id,
                "idempotency_key": "ar-omit",
            }
        )
        self.assertTrue(omitted.get("success"), omitted)


class ProfileIdentityMismatchTest(unittest.TestCase):
    """晋升前的配置一致性比对必须处理"历史 run 没有配置"这个边界。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-pid-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "pid"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _run(self, profile: dict) -> str:
        from lvke_mcp.servers.lvke_zero_material_delivery._service.base import RUN_STORE

        payload = {
            "object_type": "DeliveryRun",
            "stage": "preview_ready",
            "intent_id": "i",
            "assumption_package_id": "a",
            "blockers": [],
        }
        if profile:
            payload["report_profile"] = profile
        return RUN_STORE.put(
            self.workspace, payload, producer="pid-test", status="ok", basis=payload
        )["object_id"]

    def test_legacy_run_without_profile_is_not_flagged(self) -> None:
        """没有可比对的基准时不能凭空判不一致。"""

        from lvke_mcp.servers.lvke_zero_material_delivery._service.promotion import (
            _profile_identity_mismatch,
        )

        run_id = self._run({})
        self.assertEqual(
            _profile_identity_mismatch(
                self.workspace, {"delivery_run_id": run_id, "report_profile": {}}
            ),
            [],
        )

    def test_run_with_profile_but_pack_without_is_flagged(self) -> None:
        """晋升对象必须能指名它用的配置。"""

        from lvke_mcp.servers.lvke_zero_material_delivery._service.promotion import (
            _profile_identity_mismatch,
        )

        run_id = self._run(
            {
                "profile_id": "generic-gov10",
                "template_set_id": "lvke-report.generic-gov10.v1",
                "profile_version": "1.0.0",
                "profile_content_hash": "sha256:" + "a" * 64,
            }
        )
        mismatch = _profile_identity_mismatch(
            self.workspace, {"delivery_run_id": run_id, "report_profile": {}}
        )
        self.assertEqual(len(mismatch), 4)
        self.assertEqual(
            sorted(row["field"] for row in mismatch),
            ["profile_content_hash", "profile_id", "profile_version", "template_set_id"],
        )

    def test_promotion_identity_excludes_the_bulky_snapshot(self) -> None:
        """promotion 只留身份字段：整份配置属于 TemplatePack，不重复进正式对象。"""

        from lvke_mcp.runtime.formal_promotion import _report_profile_identity

        selection = report_profiles.resolve_profile(
            industry_code="tourism_catering",
            project_type="generic_feasibility",
            transaction_structure="new_build",
        )["selection"]
        identity = _report_profile_identity({"report_profile": selection})
        self.assertNotIn("profile_snapshot", identity)
        self.assertIn("profile_content_hash", identity)


class SnapshotIntegrityTest(unittest.TestCase):
    """快照采信必须从内容复算 hash，不能只比对两个字面量。"""

    def _selection(self) -> dict:
        return dict(
            report_profiles.resolve_profile(
                industry_code="tourism_catering",
                project_type="generic_feasibility",
                transaction_structure="new_build",
            )["selection"]
        )

    def test_intact_snapshot_is_trusted(self) -> None:
        selection = self._selection()
        verified = report_profiles.verified_snapshot(selection)
        self.assertIsNotNone(verified)
        self.assertTrue(verified.get("chapters"))

    def test_tampered_body_with_stale_hash_is_refused(self) -> None:
        """改正文但保留原 content_hash：字面量仍相等，必须被复算拦下。"""

        selection = self._selection()
        snapshot = dict(selection["profile_snapshot"])
        chapters = [dict(item) for item in snapshot["chapters"]]
        chapters[0]["title"] = "TAMPERED"
        snapshot["chapters"] = chapters  # content_hash 刻意保持原值
        selection["profile_snapshot"] = snapshot
        self.assertIsNone(report_profiles.verified_snapshot(selection))

    def test_self_contradicting_snapshot_is_refused(self) -> None:
        selection = self._selection()
        snapshot = dict(selection["profile_snapshot"])
        snapshot["content_hash"] = "sha256:" + "0" * 64
        selection["profile_snapshot"] = snapshot
        self.assertIsNone(report_profiles.verified_snapshot(selection))

    def test_all_three_consumers_go_through_the_shared_gate(self) -> None:
        """三处消费方都必须走 verified_snapshot：漏一处就是篡改入口。"""

        import inspect

        from lvke_mcp.servers.lvke_zero_material_delivery import artifact_delivery
        from lvke_mcp.servers.lvke_zero_material_delivery._service import (
            lifecycle,
            orchestration,
        )

        for module in (artifact_delivery, orchestration, lifecycle):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertIn("verified_snapshot(", source)
                # 不得再出现"只判结构完整就采信"的旧写法。
                self.assertNotIn('snapshot.get("chapters")', source)


class ProseConfigurationTest(unittest.TestCase):
    """正文说明句、缺失占位、表头、血缘片段都必须由配置驱动。"""

    def _profile(self) -> dict:
        return report_profiles.load_profile_document("generic-gov10.v1.json")

    def _args(self) -> dict:
        return dict(
            intent={
                "project_name": "X",
                "region": "湖北省",
                "industry": {"industry_label": "文旅"},
            },
            assumption_package={
                "industry_profile": {},
                "fields": [
                    {
                        "name": "a",
                        "value": 1,
                        "unit": "万元",
                        "source_type": "controlled_assumption",
                        "confidence": 0.4,
                        "validation_condition": "c",
                    }
                ],
            },
            finance={"run_id": "r1", "consistency_ok": True},
            blockers=[],
            quality_issues=[],
            public_research={"status": "ok", "source_summaries": []},
            skipped_fields=[],
        )

    def test_every_shipped_profile_declares_prose_tables_and_fragments(self) -> None:
        manifest = load_versioned_config(
            "report_profiles",
            "manifest.v1.json",
            expected_schema_version="lvke-report-profiles.v1",
        )
        for row in manifest["profiles"]:
            with self.subTest(profile_id=row["profile_id"]):
                document = report_profiles.load_profile_document(row["document"])
                self.assertTrue(document.get("prose"))
                self.assertTrue(
                    document.get("tables", {}).get("assumption_table", {}).get("columns")
                )
                self.assertTrue(document.get("fragments", {}).get("finance_lineage"))

    def test_changing_prose_changes_the_body(self) -> None:
        profile = self._profile()
        altered = {
            **profile,
            "prose": {
                **profile["prose"],
                "no_blockers": "★配置化生效★",
                "missing_value": "【无】",
            },
        }
        baseline = report_render.build_slot_values(
            **self._args(), report_profile=profile
        )
        changed = report_render.build_slot_values(
            **self._args(), report_profile=altered
        )
        self.assertNotEqual(baseline["blockers"], changed["blockers"])
        self.assertEqual(changed["blockers"], "★配置化生效★")
        markdown, _unresolved = report_render.render_report_markdown(
            profile=altered,
            selection={"template_set_id": "t", "profile_version": "1", "profile_content_hash": "h"},
            slots=changed,
        )
        self.assertIn("★配置化生效★", markdown)

    def test_assumption_table_columns_come_from_config(self) -> None:
        profile = self._profile()
        altered = {
            **profile,
            "tables": {
                "assumption_table": {
                    "columns": [
                        {"header": "字段名", "field": "name", "align": "left"},
                        {"header": "取值", "field": "value", "align": "right"},
                    ]
                }
            },
        }
        slots = report_render.build_slot_values(**self._args(), report_profile=altered)
        header = slots["assumption_table"].splitlines()[0]
        self.assertEqual(header, "| 字段名 | 取值 |")

    def test_finance_lineage_fragment_comes_from_config(self) -> None:
        profile = self._profile()
        altered = {
            **profile,
            "fragments": {"finance_lineage": [{"label": "运行", "field": "run_id"}]},
        }
        slots = report_render.build_slot_values(**self._args(), report_profile=altered)
        self.assertEqual(slots["finance_lineage"], "- 运行：`r1`")

    def test_legacy_profile_without_prose_still_renders(self) -> None:
        """v1 老配置没有 prose 段，必须仍能渲染（兜底默认值）。"""

        profile = {
            key: value
            for key, value in self._profile().items()
            if key not in ("prose", "tables", "fragments")
        }
        slots = report_render.build_slot_values(**self._args(), report_profile=profile)
        self.assertTrue(slots["blockers"])
        self.assertTrue(slots["assumption_table"])
        markdown, _unresolved = report_render.render_report_markdown(
            profile=profile,
            selection={"template_set_id": "t", "profile_version": "1", "profile_content_hash": "h"},
            slots=slots,
        )
        self.assertIn("## 1、", markdown)


class FixedBodyRemovedTest(unittest.TestCase):
    def test_artifact_delivery_no_longer_hardcodes_report_body(self) -> None:
        from lvke_mcp.servers.lvke_zero_material_delivery import artifact_delivery

        source = inspect.getsource(artifact_delivery)
        # 断言那个**函数**没了，而不是断言字符串不出现：
        # ``render_report_markdown`` 含同样的子串，宽泛匹配会永远失败。
        self.assertFalse(hasattr(artifact_delivery, "_report_markdown"))
        self.assertNotIn("def _report_markdown", source)
        # 旧固定正文里的章节标题不得再出现在业务代码中。
        for marker in ("一、项目识别", "二、依据与边界", "四、受控假设登记"):
            self.assertNotIn(marker, source)

    def test_orchestration_no_longer_hardcodes_outline(self) -> None:
        from lvke_mcp.servers.lvke_zero_material_delivery._service import orchestration

        # 只看**代码行**：注释与 docstring 里提到旧符号是为了解释为什么改掉它，
        # 那不是残留。整文件匹配会把这类说明判成违规。
        code_lines = [
            line
            for line in inspect.getsource(orchestration).splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        code = "\n".join(code_lines)
        self.assertNotIn("list(REPORT_CHAPTERS)", code)
        self.assertNotIn("项目识别与交付边界", code)
        self.assertNotIn(
            "from lvke_mcp.domains.reports._doc_service.outline import", code
        )


if __name__ == "__main__":
    unittest.main()
