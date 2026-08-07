"""通用模块拆分驱动器：把巨型模块按符号归属机械搬移为子模块包。

配置只描述「哪个符号归哪个组」，其余全部由本脚本推导：
组间 import 清单、未用 import 剪枝、组间成环检测、紧邻前置注释随代码迁移。
手写 import 清单是 Wave 2.4/2.5 两次 NameError 事故的根因，故一律不手写。

用法：
  python scripts/module_split.py --outline <rev> <path>   打印顶层符号大纲
  python scripts/module_split.py <config.json>            执行搬移
"""

from __future__ import annotations

import ast
import builtins
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _prune_imports import prune  # noqa: E402

BUILTIN_NAMES = set(dir(builtins))


def read_rev(rev: str, rel: str) -> str:
    if rev in ("", "WORKTREE"):
        return Path(rel).read_text(encoding="utf-8")
    out = subprocess.run(["git", "show", f"{rev}:{rel}"], check=True,
                         capture_output=True, text=True).stdout
    if not out:
        raise SystemExit(f"empty blob: {rev}:{rel}")
    return out


def node_name(node: ast.stmt) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign):
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        return names[0] if names else f"<assign@{node.lineno}>"
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return f"<import@{node.lineno}>"
    return f"<{type(node).__name__.lower()}@{node.lineno}>"


def bound_names(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign):
        return {node.target.id} if isinstance(node.target, ast.Name) else set()
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {(a.asname or a.name).split(".")[0] for a in node.names}
    # 顶层 try/if 块也会绑定名字（`try: from x import y / except: def y(): ...`
    # 这类可选依赖兜底）。不递归进去的话这些名字查不到 provider，跨组引用时
    # 既不报错也不生成组间 import —— 直接 NameError。这与脚本头记录的
    # Wave 2.4/2.5 事故同源，所以这里必须递归。
    if isinstance(node, (ast.Try, ast.If, ast.With)):
        names: set[str] = set()
        for field in ("body", "orelse", "finalbody"):
            for child in getattr(node, field, []) or []:
                names |= bound_names(child)
        for handler in getattr(node, "handlers", []) or []:
            for child in handler.body:
                names |= bound_names(child)
        return names
    return set()


def outline(text: str) -> tuple[list[str], list[tuple[str, int, int, ast.stmt]]]:
    """顶层节点大纲；start 已并入紧邻前置注释行，保证注释随代码迁移。"""
    tree = ast.parse(text)
    lines = text.splitlines()
    body = list(tree.body)
    idx = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        idx = 1
    items: list[tuple[str, int, int, ast.stmt]] = []
    for node in body[idx:]:
        start = node.lineno
        decorators = getattr(node, "decorator_list", [])
        if decorators:
            start = min([start] + [d.lineno for d in decorators])
        while start > 1 and lines[start - 2].lstrip().startswith("#"):
            start -= 1
        items.append((node_name(node), start, node.end_lineno or start, node))
    return lines, items


def _annotation_names(node: ast.expr | None) -> set[str]:
    """注解表达式里的名字；字符串注解（PEP 563 引号形式）按整词解析。

    只在**注解位置**解析字符串，不扫描普通字符串常量：``{"status": ...}``
    的 dict 键、``data.get("start")`` 的键名与错误码字面量都不是符号引用。
    把它们计入会造出假的组间依赖边，进而造出假环挡住正确分组。
    """
    if node is None:
        return set()
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            base: ast.expr = sub
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                out.add(base.id)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            cleaned = sub.value
            for ch in "[],|()":
                cleaned = cleaned.replace(ch, " ")
            out.update(cleaned.split())
    return out


def referenced(text: str) -> set[str]:
    """块内引用到的名字：Name、Attribute 根、以及注解位置的字符串整词。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            base: ast.expr = node
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                out.add(base.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs,
                        args.vararg, args.kwarg]:
                if arg is not None:
                    out |= _annotation_names(arg.annotation)
            out |= _annotation_names(node.returns)
        elif isinstance(node, ast.AnnAssign):
            out |= _annotation_names(node.annotation)
        elif isinstance(node, ast.arg):
            out |= _annotation_names(node.annotation)
    return out


def detect_cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    found: list[list[str]] = []

    def walk(node: str, stack: list[str]) -> None:
        for nxt in sorted(edges.get(node, ())):
            if nxt in stack:
                found.append(stack[stack.index(nxt):] + [nxt])
            elif len(stack) < 12:
                walk(nxt, stack + [nxt])

    for start in sorted(edges):
        walk(start, [start])
    return found


def run(config: dict) -> None:
    rev, rel = config.get("rev", "HEAD"), config["source"]
    text = read_rev(rev, rel)
    lines, items = outline(text)

    def span(lo: int, hi: int) -> str:
        return "\n".join(lines[lo - 1:hi]).rstrip("\n")

    header_end = None
    for _, _, end, node in items:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            header_end = end
        else:
            break
    if header_end is None:
        raise SystemExit("no leading import block found")
    header = span(items[0][1], header_end)
    payload = [it for it in items if it[2] > header_end]

    groups: dict[str, dict] = config["groups"]
    owner: dict[str, str] = {}
    for group, spec in groups.items():
        for sym in spec["symbols"]:
            if sym in owner:
                raise SystemExit(f"symbol assigned twice: {sym}")
            owner[sym] = group

    # ``keep_in_facade`` 显式列出**不搬移**的顶层节点，典型是
    # ``if __name__ == "__main__": main()`` 这类入口样板：门面模块本身就是
    # ``python -m`` 的启动路径，这个块搬进实现包后永不触发。必须显式声明，
    # 不能靠「漏掉就默默不搬」——全覆盖检查是防漏搬的主要护栏。
    keep: set[str] = set(config.get("keep_in_facade", []))
    if unknown_keep := sorted(keep - {name for name, _, _, _ in payload}):
        raise SystemExit(f"keep_in_facade lists unknown symbols: {unknown_keep}")
    payload = [it for it in payload if it[0] not in keep]

    known = {name for name, _, _, _ in payload}
    if stray := sorted(set(owner) - known):
        raise SystemExit(f"config lists unknown symbols: {stray}")
    if missing := sorted(known - set(owner)):
        raise SystemExit(f"unassigned symbols: {missing}")

    sym_group: dict[str, str] = {}
    blocks: dict[str, list[tuple[int, int]]] = {g: [] for g in groups}
    for name, start, end, node in payload:
        group = owner[name]
        blocks[group].append((start, end))
        for bound in bound_names(node):
            sym_group[bound] = group

    header_bound = set()
    for _, _, end, node in items:
        if end <= header_end:
            header_bound |= bound_names(node)

    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    edges: dict[str, set[str]] = {}
    rendered: dict[str, str] = {}

    for group, spec in groups.items():
        spans = sorted(blocks[group])
        body = "\n\n\n".join(span(lo, hi) for lo, hi in spans)
        own = {s for s, g in sym_group.items() if g == group}
        needs = referenced(body) - own - header_bound - BUILTIN_NAMES
        deps: dict[str, list[str]] = {}
        for name in sorted(needs):
            provider = sym_group.get(name)
            if provider and provider != group:
                deps.setdefault(provider, []).append(name)
        edges[group] = set(deps)
        parts = [f'"""{spec["doc"]}"""\n\n', header, "\n"]
        for dep in sorted(deps):
            joined = ",\n    ".join(deps[dep])
            parts.append(f"\nfrom .{dep} import (\n    {joined},\n)\n")
        parts.append("\n\n" + body + "\n")
        rendered[group] = "".join(parts)

    if cycles := detect_cycles(edges):
        for cycle in sorted({tuple(c) for c in cycles}):
            print("  cycle: " + " -> ".join(cycle))
        raise SystemExit("group dependency graph has cycles; regroup required")

    for group, content in rendered.items():
        path = out_dir / f"{group}.py"
        path.write_text(content, encoding="utf-8")
        prune(path)
        print(f"  {group}.py: {len(content.splitlines())} lines, deps={sorted(edges[group])}")

    init = out_dir / "__init__.py"
    if not init.exists():
        init.write_text(config.get("package_doc", '"""实现子模块包；对外入口是同级门面模块。"""\n'),
                        encoding="utf-8")
    print("split done")


if __name__ == "__main__":
    if sys.argv[1] == "--outline":
        rev_arg, path_arg = sys.argv[2], sys.argv[3]
        src = read_rev(rev_arg, path_arg)
        _, entries = outline(src)
        for name, start, end, _ in entries:
            print(f"{start:6d}-{end:6d} ({end - start + 1:5d})  {name}")
    else:
        run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))