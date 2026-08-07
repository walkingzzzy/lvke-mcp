"""临时脚本：AST 剪除目录内各模块顶层未被引用的 import 名字（不动函数体）。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def used_names(tree: ast.Module, import_lines: set[int]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            base = node
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                names.add(base.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # 字符串注解里的名字按整词计入，避免误剪 typing 符号。
            names.update(part for part in node.value.replace("[", " ").replace("]", " ")
                         .replace(",", " ").replace("|", " ").split())
    return names


def prune(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)

    import_nodes = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    import_lines = {n.lineno for n in import_nodes}
    live = used_names(tree, import_lines)

    drop_spans: list[tuple[int, int]] = []
    rewrites: dict[tuple[int, int], str] = {}
    removed = 0

    for node in import_nodes:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        keep = []
        for alias in node.names:
            bound = (alias.asname or alias.name).split(".")[0]
            if bound in live:
                keep.append(alias)
            else:
                removed += 1
        if len(keep) == len(node.names):
            continue
        span = (node.lineno, node.end_lineno or node.lineno)
        if not keep:
            drop_spans.append(span)
            continue
        rendered = ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in keep)
        if isinstance(node, ast.ImportFrom):
            dots = "." * (node.level or 0)
            rewrites[span] = f"from {dots}{node.module or ''} import {rendered}\n"
        else:
            rewrites[span] = f"import {rendered}\n"

    if not drop_spans and not rewrites:
        return 0

    out: list[str] = []
    idx = 1
    while idx <= len(lines):
        span = next((s for s in drop_spans if s[0] == idx), None)
        if span:
            idx = span[1] + 1
            continue
        span = next((s for s in rewrites if s[0] == idx), None)
        if span:
            out.append(rewrites[span])
            idx = span[1] + 1
            continue
        out.append(lines[idx - 1])
        idx += 1

    path.write_text("".join(out), encoding="utf-8")
    return removed


target = Path(sys.argv[1])
files = sorted(target.glob("*.py")) if target.is_dir() else [target]
for f in files:
    n = prune(f)
    print(f"{f.name}: removed {n} import name(s)")