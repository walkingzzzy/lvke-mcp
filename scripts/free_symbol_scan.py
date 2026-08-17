#!/usr/bin/env python3
"""自由符号解析校验：搬移后的模块不能引用任何解析不到的名字。

对应 `dev-docs/plans/MODULARIZATION_PLAN.md` §5.2「纯搬移 PR」。

``split_fidelity.py`` 比较的是**搬移后定义体的 AST**。它能证明「函数体没被
改写」，但证明不了「函数体里的名字还能解析」——模块级常量留在 base 模块、
消费者的 import 清单漏列它，是拆分中最常见的一类事故：

    # _service/base.py       ← 常量留在这里
    _ROUTE_RULES = (...)

    # _service/routing.py    ← import 清单漏了它
    from .base import RUN_STORE, SERVICE_VERSION

    def _resolve_route(...):
        for route in _ROUTE_RULES:   # NameError，但只在调用到才炸

这种缺陷躲得过全部现有门禁：

  * ``split_fidelity``：函数体逐字未变，报「纯搬移确认」
  * ``compileall``：语法合法，字节码正常生成
  * ``api_snapshot``：符号在门面上可见（门面从 base 直接导入了它）
  * ``smoke_test`` / 导入探测：模块级不引用该名字，import 阶段不求值
  * ``pytest``：只有恰好覆盖到那条分支的用例才会失败

Wave 2.4 实测拦下三处（``routing`` 漏 ``SERVICE_NAME``/``_ACTIVE_STAGES``/
``_ROUTE_RULES``，``intake`` 漏 ``SERVICE_NAME``/``_view``）。

用法::

    python scripts/free_symbol_scan.py <impl-dir> [<impl-dir> ...]

例::

    python scripts/free_symbol_scan.py \\
        src/lvke_mcp/servers/lvke_zero_material_delivery/_service

判定：模块引用了既未定义、也未导入、且不是内建的名字 = 失败。
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

_BUILTINS = set(dir(builtins)) | {
    "__annotations__",
    "__builtins__",
    "__doc__",
    "__file__",
    "__loader__",
    "__name__",
    "__package__",
    "__path__",
    "__spec__",
    "WindowsError",
}


def _bound_names(tree: ast.Module) -> set[str]:
    """模块里所有会绑定名字的位置：import / def / class / 赋值 / 参数 / except。"""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            arguments = node.args
            for arg in (
                arguments.posonlyargs + arguments.args + arguments.kwonlyargs
            ):
                names.add(arg.arg)
            if arguments.vararg:
                names.add(arguments.vararg.arg)
            if arguments.kwarg:
                names.add(arguments.kwarg.arg)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Lambda):
            arguments = node.args
            for arg in (
                arguments.posonlyargs + arguments.args + arguments.kwonlyargs
            ):
                names.add(arg.arg)
            if arguments.vararg:
                names.add(arguments.vararg.arg)
            if arguments.kwarg:
                names.add(arguments.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Global):
            names.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            names.update(node.names)
    return names


def _loaded_names(tree: ast.Module) -> set[str]:
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def scan(directory: Path) -> list[tuple[Path, list[str]]]:
    findings: list[tuple[Path, list[str]]] = []
    for path in sorted(directory.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        missing = sorted(_loaded_names(tree) - _bound_names(tree) - _BUILTINS)
        if missing:
            findings.append((path, missing))
    return findings


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    root = Path.cwd()
    total_modules = 0
    findings: list[tuple[Path, list[str]]] = []

    for argument in sys.argv[1:]:
        directory = Path(argument)
        if not directory.is_dir():
            print(f"not a directory: {directory}")
            return 2
        total_modules += len(list(directory.rglob("*.py")))
        findings.extend(scan(directory))

    print(f"modules scanned: {total_modules}")
    if findings:
        print()
        for path, missing in findings:
            try:
                shown = path.relative_to(root)
            except ValueError:
                shown = path
            print(f"[FAIL] unresolved names in {shown}: {', '.join(missing)}")
        print(f"\n{len(findings)} module(s) with unresolved names")
        return 1

    print("free symbols: all resolvable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())