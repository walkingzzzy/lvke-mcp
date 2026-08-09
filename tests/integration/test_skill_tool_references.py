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
)
# 只认真正的调用形状：``name(``。
#
# 刻意不认 ``→ name`` / ``调用 name``：文档里 ``→ project_context_id`` 表示"返回该 ID"，
# ``finance_spec -> finance_run`` 是交付阶段名，两者都不是工具调用。把它们算进来会
# 逼着测试维护一份越来越长的豁免名单，而豁免名单本身就是漏检入口。
# 真正需要守的是"照抄文档就调不通"，那只发生在带参数括号的调用形状上。
_CALL = re.compile(r"([a-z][a-z0-9_]{3,})\s*\(")

# 这些不是 MCP 工具，而是早期文档留下的示意用例名（doc_read/context_view 同族）。
_KNOWN_NON_TOOLS = {
    # propose-apply-flow 里的 python 伪代码示例，示意旧文档工具，不是 MCP 工具面。
    "finance_view",
}


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


if __name__ == "__main__":
    unittest.main()
