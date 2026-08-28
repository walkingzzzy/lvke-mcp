#!/usr/bin/env python3
"""Validate Skill-to-Tool mapping against inventory and live tool contracts."""

from __future__ import annotations

import json
import re
import sys
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvke_mcp.runtime.skill_inventory import resolve_skill_inventory  # noqa: E402
from lvke_mcp.testing.server_manifest import SERVER_BY_NAME, SERVER_SPECS  # noqa: E402

MAPPING_PATH = ROOT / "src" / "lvke_mcp" / "runtime" / "skill_tool_mapping.json"
BASELINE = ROOT / "tests" / "fixtures" / "baseline" / "contracts"
PLUGIN_SKILLS = ROOT / "plugins" / "lvke-mcp" / "skills"
SOURCE_SKILLS = ROOT / "skills"


def _live_tool_index() -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for spec in SERVER_SPECS:
        module = import_module(spec.module)
        server = getattr(module, "SERVER", None) or module.build_server()
        index[spec.name] = {tool.name for tool in server.tool_specs}
    return index


def _baseline_tool_index() -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for spec in SERVER_SPECS:
        path = BASELINE / f"{spec.name}.json"
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        index[spec.name] = {item["name"] for item in payload if isinstance(item, dict)}
    return index


#: SKILL.md 正文里真正的"工具调用"写法：``name(``。
#:
#: 刻意只认带左括号的调用形态。反引号形态（`` `name` ``）不能用：
#: 正文里大量反引号包的是枚举值（``source_reconstructed``、``review_candidate``）
#: 与字段名（``review_purpose``），把它们当工具会淹没真实问题。退役工具另有
#: ``_RETIRED_NAMES`` 精确名单兜底，不依赖这条正则。
_CALL_SHAPE = re.compile(
    r"(?:^|[^0-9A-Za-z_])(?:mcp_lvke_)?"
    r"((?:acquisition|analysis|archive|data|delivery|dr|feasibility|finance|geo"
    r"|knowledge|planning|project_context|reference|report|review|source|tables"
    r"|template|doc|issue|context|lock)_[a-z0-9_]{2,60})"
    r"\s*\("
)

#: 已退役但历史 Skill 仍在教的名字。这些码即便不符合 _CALL_SHAPE 也要报，
#: 因为它们出现在散文里同样会把 agent 引向不存在的工具。
_RETIRED_NAMES = (
    "doc_read", "doc_review", "doc_propose", "doc_apply", "doc_reject",
    "doc_diff", "issue_list", "issue_update", "finance_view", "context_view",
    "lock_heartbeat", "source_upload_status", "source_parse_status",
    "archive_get_chapter", "archive_search_archive",
)

#: 允许出现失效名的文件：迁移对照表必须写出旧名才能说明"旧名→新名"。
_LEGACY_TABLE_ALLOWLIST = (
    "workspace-navigation/SKILL.md",
    "workspace-navigation/REFERENCE.md",
)


def _skill_prose_problems(live_tools: set[str]) -> list[str]:
    """Report tool names taught in SKILL.md prose that do not exist at runtime.

    映射 JSON 校验管不到这一层：mapping 里每个工具都真实存在，SKILL.md 正文
    却可以照着一套已退役的接口写整篇操作流程，而门禁全绿。这正是 11 个
    ``doc_*`` 工具与 ``mcp_lvke_*`` 前缀能长期潜伏的原因，所以这里直接读正文。
    """

    problems: list[str] = []
    for root in (SOURCE_SKILLS, PLUGIN_SKILLS):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            relative = path.relative_to(ROOT)
            allowed = any(str(path).endswith(item) for item in _LEGACY_TABLE_ALLOWLIST)
            text = path.read_text(encoding="utf-8", errors="replace")
            if not allowed:
                for retired in _RETIRED_NAMES:
                    for line in text.splitlines():
                        if not re.search(r"\b" + re.escape(retired) + r"\b", line):
                            continue
                        # 明确说明"该工具已不存在"的行是正确的迁移提示，不是错误指引。
                        if any(mark in line for mark in ("已不存在", "已退役", "不再需要", "已移除")):
                            continue
                        problems.append(
                            f"{relative}: teaches retired tool {retired}"
                        )
                        break
            for match in _CALL_SHAPE.finditer(text):
                name = match.group(1)
                if name in live_tools:
                    continue
                if allowed and name in _RETIRED_NAMES:
                    continue
                # 字段名常以这些后缀结尾，不是工具。
                if name.endswith(("_id", "_ids", "_hash", "_hashes", "_key", "_keys")):
                    continue
                problems.append(f"{relative}: unknown tool referenced {name}")
    return sorted(set(problems))


def _uncovered_live_tools(live: dict[str, set[str]]) -> list[str]:
    """Report live tools that no shipped Skill documents.

    这是与其它检查相反方向的不变量。其余检查都在问"Skill 提到的工具存不存在"，
    这条问"存在的工具有没有人教"——两者都过才算 Skill 层真正覆盖了工具面。
    少了这条，删掉半个服务器的文档也能拿到 OK：mapping 里剩下的条目依然合法。

    覆盖的判定放得比较宽：mapping JSON 声明过，或任一 Skill 正文里出现过完整
    工具名，都算已覆盖。目的是守住"整块服务没人教"这条底线，而不是强制每个
    工具都写一段说明。
    """

    mapping_doc = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    covered = {
        str(tool)
        for entry in mapping_doc.get("mappings", [])
        for tool in (entry.get("tools") or [])
    }
    # 只扫源树。插件树是 build 脚本的 copytree 快照，两棵都扫的话，
    # 源树删掉一份文档后插件树的旧副本仍会顶上，这条不变量就永远不会触发。
    # 双树是否同步由 --check-plugin-sync 单独负责。
    prose = ""
    if SOURCE_SKILLS.is_dir():
        for path in SOURCE_SKILLS.rglob("*.md"):
            prose += path.read_text(encoding="utf-8", errors="replace")

    problems: list[str] = []
    for server in sorted(live):
        missing = sorted(
            tool
            for tool in live[server]
            if tool not in covered
            and not re.search(r"\b" + re.escape(tool) + r"\b", prose)
        )
        if missing:
            problems.append(
                f"{server}: {len(missing)} live tools documented by no Skill: "
                + ", ".join(missing[:8])
                + (" ..." if len(missing) > 8 else "")
            )
    return problems


def validate_skill_tool_mapping(*, strict: bool = False, check_plugin_sync: bool = False) -> list[str]:
    problems: list[str] = []
    if not MAPPING_PATH.is_file():
        return [f"missing mapping file: {MAPPING_PATH}"]

    mapping_doc = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    inventory = resolve_skill_inventory()
    published = set(inventory.get("names", []))
    dev_only = set(mapping_doc.get("dev_only_skills", []))
    mapped_skills = {str(entry.get("skill", "")) for entry in mapping_doc.get("mappings", [])}
    live = _live_tool_index()
    baseline = _baseline_tool_index()

    product_skills = published - dev_only
    for skill in sorted(product_skills):
        if skill not in mapped_skills:
            problems.append(f"product skill missing mapping entry: {skill}")

    if check_plugin_sync and PLUGIN_SKILLS.is_dir() and SOURCE_SKILLS.is_dir():
        for skill in sorted(product_skills):
            source = SOURCE_SKILLS / skill / "SKILL.md"
            plugin = PLUGIN_SKILLS / skill / "SKILL.md"
            if not source.is_file():
                problems.append(f"missing source skill: {source}")
                continue
            if not plugin.is_file():
                problems.append(f"plugin skill not built: {plugin}")
                continue
            if source.read_text(encoding="utf-8") != plugin.read_text(encoding="utf-8"):
                problems.append(f"plugin skill drift: {skill} (run scripts/build_codex_plugin.py)")

    all_live_tools: dict[str, str] = {}
    for server, tools in live.items():
        for tool in tools:
            all_live_tools[tool] = server

    for entry in mapping_doc.get("mappings", []):
        skill = str(entry.get("skill", ""))
        if not skill:
            problems.append("mapping entry missing skill name")
            continue
        if skill in dev_only:
            continue
        if skill not in published:
            problems.append(f"skill not in inventory: {skill}")
            continue

        for tool in entry.get("tools", []):
            server = all_live_tools.get(tool)
            if server is None:
                problems.append(f"{skill}: unknown tool {tool}")
                continue
            declared_servers = set(entry.get("servers", []))
            if declared_servers and server not in declared_servers:
                problems.append(
                    f"{skill}: tool {tool} lives on {server}, "
                    f"not in declared servers {sorted(declared_servers)}"
                )
            for baseline_server, baseline_tools in baseline.items():
                if tool in baseline_tools and baseline_server != server:
                    problems.append(
                        f"{skill}: tool {tool} baseline server mismatch "
                        f"({baseline_server} vs {server})"
                    )

        for server_name in entry.get("servers", []):
            if server_name not in SERVER_BY_NAME:
                problems.append(f"{skill}: unknown server {server_name}")

    # Every shipped product Skill must have an explicit mapping entry.  An empty
    # tool list is valid for cross-cutting Skills (contract/backend/local verify)
    # only when the entry documents that it is an orchestrator or gate.
    mapped = {
        str(entry.get("skill", ""))
        for entry in mapping_doc.get("mappings", [])
        if str(entry.get("skill", ""))
    }
    required_skills = published - dev_only
    for skill in sorted(required_skills - mapped):
        problems.append(f"missing mapping entry for published Skill: {skill}")

    problems.extend(_skill_prose_problems(set(all_live_tools)))
    problems.extend(_uncovered_live_tools(live))

    if strict:
        for spec in SERVER_SPECS:
            contract = BASELINE / f"{spec.name}.json"
            if not contract.is_file():
                problems.append(f"missing contract baseline: {contract}")
                continue
            frozen = {item["name"] for item in json.loads(contract.read_text())}
            current = live.get(spec.name, set())
            if frozen != current:
                problems.append(
                    f"{spec.name}: live tools differ from baseline "
                    f"(frozen={len(frozen)} live={len(current)})"
                )

    return problems


def main() -> int:
    strict = "--strict" in sys.argv
    check_plugin = "--check-plugin-sync" in sys.argv
    problems = validate_skill_tool_mapping(strict=strict, check_plugin_sync=check_plugin)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"skill-tool mapping OK ({MAPPING_PATH.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
