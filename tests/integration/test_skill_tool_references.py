"""Skill 文档里的工具式引用必须对应真实注册的 MCP 工具。

第二轮压缩把 9 个规划工具收口为 4 个聚合入口（`planning_validate` / `planning_confirm` /
`planning_prepare` / `planning_create`），并把逐类型 getter 收口为 `planning_get_object`。
文档没有同步迁移时，调用方会按文档去调 `planning_validate_build_scale` 这类已不存在的
名字，拿到 unknown tool；更隐蔽的是只补 `object_kind` 却漏掉判别式 `payload`，
schema 校验仍然失败。

本测试同时守两件事：
1. 文档里带调用括号或"调用/→"句式的工具名，必须是运行时真的注册了的名字；
2. 聚合入口的必填字段以运行时 schema 为准，文档必须给出完整调用形状。
"""

from __future__ import annotations

import re
import unittest
from importlib import import_module
from pathlib import Path

from lvke_mcp.testing.server_manifest import SERVER_SPECS


ROOT = Path(__file__).resolve().parents[2]

# 只检查这些前缀：它们是 Lvke MCP 的工具命名空间。散文里出现的同形字段名不在此列，
# 因为字段引用不会被当成工具调用。
_TOOL_PREFIXES = (
    "planning_",
    "project_context_",
    "finance_",
    "tables_",
    "report_",
    "review_",
    "source_",
    "data_",
    "analysis_",
    "dr_",
    "knowledge_",
    "delivery_",
    "feasibility_",
    "acquisition_",
    # lvke-reference 的命名空间此前整段漏掉，于是 mcp_lvke_archive_* 这类
    # 错前缀写法长期无人发现。
    "archive_",
    "reference_",
    "geo_",
    "template_",
    # 已退役的 HTTP 工作台命名空间。它们不在当前工具面里，但历史 Skill 仍
    # 成篇地教，必须能被查出来——漏掉这四个前缀正是 11 个 doc_* 工具能潜伏
    # 至今的原因。
    "doc_",
    "issue_",
    "context_",
    "lock_",
)
# 只认真正的调用形状：``name(``。
#
# 刻意不认 ``→ name`` / ``调用 name``：文档里 ``→ project_context_id`` 表示"返回该 ID"，
# ``finance_spec -> finance_run`` 是交付阶段名，两者都不是工具调用。把它们算进来会
# 逼着测试维护一份越来越长的豁免名单，而豁免名单本身就是漏检入口。
# 真正需要守的是"照抄文档就调不通"，那只发生在带参数括号的调用形状上。
_CALL = re.compile(r"([a-z][a-z0-9_]{3,})\s*\(")

# 不是 MCP 工具、但允许以调用形状出现的名字。
#
# 这份名单要保持为空或极短：每加一项就等于放弃一处检查。此前 finance_view
# 被豁免，实际它属于一整套已退役的 doc_*/issue_*/context_* 工作台接口，
# 豁免它掩盖了那批指引全部失效的事实——现在它们已改写成真实工具，豁免撤销。
_KNOWN_NON_TOOLS: set[str] = set()


def _registered_tool_names() -> set[str]:
    names: set[str] = set()
    for spec in SERVER_SPECS:
        server = import_module(spec.module).build_server()
        names.update(tool.name for tool in server.tool_specs)
    return names


def _tool_like_references(root: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for markdown in sorted(root.rglob("*.md")):
        text = markdown.read_text(encoding="utf-8")
        for name in set(_CALL.findall(text)):
            if not name.startswith(_TOOL_PREFIXES) or name in _KNOWN_NON_TOOLS:
                continue
            found.setdefault(name, set()).add(str(markdown.relative_to(root)))
    return found


class SkillToolReferenceTest(unittest.TestCase):
    def test_no_phantom_tool_names_in_skill_sources(self) -> None:
        registered = _registered_tool_names()
        phantom = {
            name: sorted(paths)
            for name, paths in _tool_like_references(ROOT / "skills").items()
            if name not in registered
        }
        self.assertEqual(
            {},
            phantom,
            "Skill 文档引用了未注册的工具名：\n"
            + "\n".join(f"  {name} <- {paths}" for name, paths in sorted(phantom.items())),
        )

    def test_no_phantom_tool_names_in_published_plugin(self) -> None:
        plugin_skills = ROOT / "plugins" / "lvke-mcp" / "skills"
        if not plugin_skills.is_dir():
            self.skipTest("plugin tree not built")
        registered = _registered_tool_names()
        phantom = {
            name: sorted(paths)
            for name, paths in _tool_like_references(plugin_skills).items()
            if name not in registered
        }
        self.assertEqual({}, phantom, f"插件树引用了未注册的工具名：{phantom}")

    def test_planning_aggregate_required_fields_match_runtime_schema(self) -> None:
        """聚合入口的必填字段以运行时 schema 为准，不以文档口述为准。"""

        server = import_module("lvke_mcp.servers.lvke_project_planning.server").build_server()
        required = {
            name: set(server._tools[name].input_schema["required"])  # noqa: SLF001
            for name in (
                "planning_validate",
                "planning_compare",
                "planning_confirm",
                "planning_prepare",
                "planning_create",
            )
        }
        self.assertEqual(required["planning_validate"], {"workspace_id", "object_kind", "target_id"})
        self.assertEqual(required["planning_compare"], {"workspace_id", "object_kind", "target_id"})
        # confirm/prepare/create 都要求判别式 payload：只补 object_kind 仍会 schema 失败。
        self.assertEqual(
            required["planning_confirm"],
            {"workspace_id", "object_kind", "target_id", "idempotency_key", "payload"},
        )
        for name in ("planning_prepare", "planning_create"):
            with self.subTest(tool=name):
                self.assertEqual(
                    required[name],
                    {
                        "workspace_id",
                        "object_kind",
                        "project_context_id",
                        "idempotency_key",
                        "payload",
                    },
                )

    def test_migrated_planning_docs_state_the_full_call_shape(self) -> None:
        """迁移过的规划文档必须写出判别式 payload，而不是只提 object_kind。"""

        preserved = ROOT / "skills" / "lvke-project-planning" / "references" / "preserved"
        for name in ("lvke-build-scale", "lvke-labor-planning", "lvke-option-comparison"):
            text = (preserved / name / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=name):
                self.assertIn("planning_validate(object_kind=", text)
                self.assertIn("planning_confirm(object_kind=", text)
                self.assertIn("payload", text)
                self.assertIn("planning_get_object", text)


class SkillToolMappingValidatorTest(unittest.TestCase):
    """把 scripts/validate_skill_tool_mapping.py 接进 pytest。

    该校验器此前是孤儿脚本：没有测试引用、仓库也没有 CI 或 Makefile 入口，
    唯一调用方是 scripts/build_codex_plugin.py。于是只跑 pytest 的人不会执行它，
    而它守的恰好是"Skill 指引与真实工具面是否一致"——那正是失效指引能长期
    潜伏的原因。这里直接调它的函数入口，让 `pytest` 就能兜住。
    """

    def test_validator_reports_no_problems(self) -> None:
        import sys

        scripts_dir = ROOT / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from validate_skill_tool_mapping import validate_skill_tool_mapping

        problems = validate_skill_tool_mapping(strict=True, check_plugin_sync=True)
        self.assertEqual([], problems, "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
