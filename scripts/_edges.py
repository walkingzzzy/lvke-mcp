"""临时脚本：打印指定配置下的组间边与具体符号，用于定位成环原因。"""
from __future__ import annotations

import ast
import builtins
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_split import outline, read_rev, referenced, bound_names  # noqa: E402

cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
text = read_rev(cfg.get("rev", "HEAD"), cfg["source"])
lines, items = outline(text)

header_end = None
for _, _, end, node in items:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        header_end = end
    else:
        break
payload = [it for it in items if it[2] > header_end]
header_bound: set[str] = set()
for _, _, end, node in items:
    if end <= header_end:
        header_bound |= bound_names(node)

owner = {s: g for g, spec in cfg["groups"].items() for s in spec["symbols"]}
sym_group: dict[str, str] = {}
for name, start, end, node in payload:
    for b in bound_names(node):
        sym_group[b] = owner[name]

BI = set(dir(builtins))
for name, start, end, node in payload:
    grp = owner[name]
    body = "\n".join(lines[start - 1:end])
    own = {s for s, g in sym_group.items() if g == grp}
    for ref in sorted(referenced(body) - own - header_bound - BI):
        provider = sym_group.get(ref)
        if provider and provider != grp:
            print(f"{grp:14s} -> {provider:14s}  {name} uses {ref}")