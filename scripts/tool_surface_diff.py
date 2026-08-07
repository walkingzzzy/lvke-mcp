#!/usr/bin/env python3
"""工具面契约比对：拆分前后 MCP 工具的注册面必须逐字段相同。

对应 MODULARIZATION_PLAN.md §10「允许切函数体的 4 处例外」。

``split_fidelity.py`` 只比较**顶层**同名定义的 AST。一旦把巨型单函数的函数体
切成内部辅助函数（``build_server`` 814 行切成 9 个 ``_register_*``），它对该
函数就失效了——顶层定义还在，但函数体必然不同。

本脚本是那 4 处例外的补偿手段中「纯注册代码」那一类的验证方式：在两个独立
进程里分别构建服务器，逐项比对工具面。它能抓出下面这些 ``split_fidelity``
和 ``api_snapshot`` 都查不出、且测试仍会全绿的拆分事故：

  1. **注册顺序漂移**：把 ``create_*`` 那批从 build_scale 与 cost 之间挪到
     按业务分组的位置。``tools/list`` 的输出顺序随注册序变化。
  2. **schema 片段串味**：两个工具共享同一个 dict 字面量，搬移时其中一个被
     指到了另一个片段。两边都是合法 schema，只有逐字段比对能发现。
  3. **round2 聚合面变化**：``_install_round2_aggregates`` 从
     ``server._tools`` 取出 legacy spec 后会 ``pop`` 掉它们的公开名。若注册
     顺序变了导致某个 legacy 工具尚未注册，聚合分支会静默少一个 enum 值。
  4. **annotations 退化**：read/write 的 ``ToolAnnotations`` 被搬错，只读工具
     变成可写，或反之。

用法::

    python scripts/tool_surface_diff.py <git-ref> <server-module>

例::

    python scripts/tool_surface_diff.py HEAD \\
        lvke_mcp.servers.lvke_project_planning.server

判定：

  * 注册顺序、工具名集合、description、input/output schema、annotations
    任一不同 = 失败
  * schema 资源 URI 集合、round2 legacy spec 名单不同 = 失败
  * 模块级 operation map（幂等命名空间）不同 = 失败
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_PROBE = '''
import json
import importlib

module = importlib.import_module({module!r})
server = module.SERVER

tools = {{}}
for name, spec in server._tools.items():  # noqa: SLF001
    tools[name] = {{
        "description": spec.description,
        "input_schema": spec.input_schema,
        "output_schema": spec.output_schema,
        "annotations": repr(spec.annotations),
    }}

# Module-level operation maps pin historical idempotency namespaces; a rename
# there silently reroutes replay lookups.
operation_maps = {{
    name: getattr(module, name)
    for name in sorted(dir(module))
    if name.endswith("_OPERATION_BY_KIND")
}}

document = {{
    "order": list(server._tools),  # noqa: SLF001
    "tools": tools,
    "schema_resources": sorted(getattr(server, "_schema_resources", {{}}) or {{}}),
    "round2_legacy": sorted(getattr(server, "_round2_legacy_specs", {{}}) or {{}}),
    "operation_maps": operation_maps,
}}
print("@@SURFACE@@" + json.dumps(document, sort_keys=True, ensure_ascii=False))
'''


def _capture(checkout: Path, module: str) -> dict:
    """Build the server in a fresh interpreter rooted at ``checkout``."""

    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(module=module)],
        cwd=checkout,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(checkout / "src"), "PATH": "/usr/bin:/bin"},
    )
    if "@@SURFACE@@" not in result.stdout:
        raise SystemExit(
            f"probe failed in {checkout}:\n{result.stdout}\n{result.stderr}"
        )
    return json.loads(result.stdout.split("@@SURFACE@@", 1)[1].strip())


def _compare(baseline: dict, current: dict) -> list[str]:
    problems: list[str] = []

    if baseline["order"] != current["order"]:
        problems.append(
            "registration order changed\n"
            f"    baseline: {baseline['order']}\n"
            f"    current : {current['order']}"
        )

    for key in ("schema_resources", "round2_legacy", "operation_maps"):
        if baseline[key] != current[key]:
            problems.append(
                f"{key} changed\n"
                f"    baseline: {baseline[key]}\n"
                f"    current : {current[key]}"
            )

    for name, base_spec in baseline["tools"].items():
        current_spec = current["tools"].get(name)
        if current_spec is None:
            problems.append(f"tool disappeared: {name}")
            continue
        for field in ("description", "input_schema", "output_schema", "annotations"):
            if base_spec[field] != current_spec[field]:
                problems.append(f"{name}.{field} differs")

    for name in current["tools"]:
        if name not in baseline["tools"]:
            problems.append(f"tool appeared: {name}")

    return problems


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    ref, module = sys.argv[1], sys.argv[2]
    root = Path(__file__).resolve().parent.parent

    current = _capture(root, module)

    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "baseline"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), ref],
            cwd=root, check=True, capture_output=True,
        )
        try:
            baseline = _capture(worktree, module)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=root, check=True, capture_output=True,
            )

    problems = _compare(baseline, current)

    print(
        f"tools {len(baseline['tools'])} -> {len(current['tools'])}"
        f" | schema resources {len(baseline['schema_resources'])}"
        f" -> {len(current['schema_resources'])}"
    )
    if problems:
        print()
        for problem in problems:
            print(f"[FAIL] {problem}")
        print(f"\n{len(problems)} tool surface violation(s)")
        return 1

    print("tool surface: identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())