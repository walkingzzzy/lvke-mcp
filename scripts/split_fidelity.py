#!/usr/bin/env python3
"""纯搬移保真度校验：搬出去的定义必须与原文件 AST 等价。

对应 MODULARIZATION_PLAN.md §5.2「纯搬移 PR 不合并重复函数，不改变语义」。

Wave 0 的三道门禁（契约 / API / 依赖边界）只能发现**接口**层面的破坏。
它们查不出下面这两类「拆分事故」——两者都能让全部测试继续变绿：

  1. **语义等价改写**：把 ``r"[\\w\\u4e00-\\u9fff]+"`` 写成 ``r"[\\w一-鿿]+"``。
     对 ``re`` 完全等价，测试全过，但这是重写而不是搬移，diff 从此不可复核。
  2. **helper 复制而非搬移**：同一个 ``_locator_text`` 被复制进两个子模块。
     两份都能用，没有任何门禁失败，但从此存在两份会各自漂移的实现。

本脚本按 AST 比较（``ast.unparse`` 归一化，忽略格式与注释差异），因此
「只调整缩进/import 顺序」不会误报，而「改了一个字符的正则」会被抓出来。

用法::

    python scripts/split_fidelity.py <git-ref> <facade-path> <impl-dir>

例::

    python scripts/split_fidelity.py HEAD \\
        src/lvke_mcp/servers/lvke_data_analysis/service.py \\
        src/lvke_mcp/servers/lvke_data_analysis/_service

判定：

  * 搬移后函数体 AST 不一致  = 失败（语义等价的改写也算失败）
  * 定义凭空消失（两边都没）  = 失败
  * 同一定义出现在多个实现文件 = 失败（复制而非搬移）
  * 门面仍保留同名定义        = 通过（re-export 门面的预期形态）
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import subprocess
import sys
from collections import defaultdict

_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _top_level_defs(source: str) -> dict[str, str]:
    """顶层函数/类名 -> 归一化 AST dump。

    经过 ``unparse`` -> ``parse`` 一轮，消除注释、空行、引号风格与换行位置差异，
    只保留真正的语法结构。
    """
    result: dict[str, str] = {}
    for node in ast.parse(source).body:
        if isinstance(node, _DEF_NODES):
            result[node.name] = ast.dump(ast.parse(ast.unparse(node)))
    return result


def _git_show(ref: str, path: str) -> str:
    completed = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def check(ref: str, facade_path: str, impl_dir: str) -> tuple[list[str], dict[str, int]]:
    baseline = _top_level_defs(_git_show(ref, facade_path))

    # name -> [文件名]，用于发现「复制而非搬移」。
    locations: dict[str, list[str]] = defaultdict(list)
    mismatched: list[str] = []
    novel: list[str] = []

    impl_root = pathlib.Path(impl_dir)
    for path in sorted(impl_root.rglob("*.py")):
        for name, dump in _top_level_defs(path.read_text(encoding="utf-8")).items():
            if name in baseline:
                locations[name].append(path.name)
                if baseline[name] != dump:
                    mismatched.append(f"{path.name}::{name}")
            else:
                novel.append(f"{path.name}::{name}")

    facade_now = _top_level_defs(pathlib.Path(facade_path).read_text(encoding="utf-8"))

    violations: list[str] = []
    for entry in mismatched:
        violations.append(f"body changed during move (must be a pure move): {entry}")
    for name, files in sorted(locations.items()):
        if len(files) > 1:
            violations.append(
                f"definition copied into {len(files)} modules instead of moved once: "
                f"{name} -> {', '.join(files)}"
            )
    for name in sorted(baseline):
        if name not in locations and name not in facade_now:
            violations.append(f"definition lost (neither in facade nor impl): {name}")

    summary = {
        "baseline_defs": len(baseline),
        "moved": len(locations),
        "still_in_facade": len(set(baseline) & set(facade_now)),
        "identical": len(locations) - len({m.split("::")[-1] for m in mismatched}),
        "new_in_impl": len(novel),
    }
    return violations, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("ref", help="拆分前的 git ref（例：HEAD 或基线 commit）")
    parser.add_argument("facade", help="原文件 / 门面路径")
    parser.add_argument("impl_dir", help="实现包目录（例：..._service）")
    args = parser.parse_args()

    violations, summary = check(args.ref, args.facade, args.impl_dir)

    print(f"baseline defs     : {summary['baseline_defs']}")
    print(f"moved to impl     : {summary['moved']}")
    print(f"still in facade   : {summary['still_in_facade']}")
    print(f"AST-identical     : {summary['identical']}/{summary['moved']}")
    print(f"new in impl       : {summary['new_in_impl']}")

    if violations:
        print()
        for violation in violations:
            print(f"[FAIL] {violation}", file=sys.stderr)
        print(f"\n{len(violations)} fidelity violation(s)", file=sys.stderr)
        return 1

    print("\nsplit fidelity: pure move confirmed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
