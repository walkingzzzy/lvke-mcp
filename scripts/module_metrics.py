#!/usr/bin/env python3
"""模块化重构护栏：行数、消费者清单、导入图与分层边扫描。

对应 `dev-docs/plans/MODULARIZATION_PLAN.md` §2/§7/§8。本脚本只做**观测与门禁**，不改代码：

  1. 行数统计     —— 全部 ``src/lvke_mcp`` Python 文件，标出 ``--long-threshold`` 之上的文件。
  2. 消费者清单   —— 对每个模块统计 ``src``/``tests``/``scripts`` 里引用它的文件，
                     同时收集 AST import 与 ``import_module("...")`` 字符串懒加载。
  3. 导入图       —— 模块级有向边，用于循环依赖检测。
  4. 分层边       —— 按 package/layer 聚合（不是文件级 import 计数），
                     冻结历史反向依赖，门禁只判定**新增**的跨层边。

用法::

    python scripts/module_metrics.py --output quality/module_metrics.json
    python scripts/module_metrics.py --check quality/module_metrics.json   # 与基线比较

``--check`` 的判定规则（与方案 §8 一致）：

  * 新增跨层边（``runtime -> servers|domains``、``adapters -> domains``、
    ``domains -> servers``）= 失败；
  * 新增循环依赖 = 失败；
  * 基线里已存在的历史边保持存在 = 通过（记为 preserved）；
  * 历史边消失 = 通过并记为 improved；
  * 同包内新增文件边 = 通过（搬移的预期结果）。
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "lvke_mcp"

# 基线必须随仓库留存：``quality/`` 在 .gitignore 里，放那里会让门禁在干净 clone
# 上静默跳过。与 tools/resources 契约基线同一棵树。
DEFAULT_OUT = REPO_ROOT / "tests" / "fixtures" / "baseline" / "refactor" / "module_metrics.json"

SCAN_ROOTS = ("src", "tests", "scripts")
_EXCLUDED_DIRS = frozenset({
    "__pycache__", ".git", ".venv", "venv", "build", "dist", "node_modules",
})

# 层的解析顺序很重要：servers/domains/adapters/runtime/testing 是 lvke_mcp 的直接子包。
_LAYERS = ("runtime", "adapters", "domains", "servers", "testing")

# 禁止**新增**的跨层方向（方案 §2.3 冻结现状、禁止新增）。
_FORBIDDEN_LAYER_EDGES = (
    ("runtime", "servers"),
    ("runtime", "domains"),
    ("adapters", "domains"),
    ("domains", "servers"),
)


def _iter_py_files(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*.py")):
        if _EXCLUDED_DIRS & set(path.parts):
            continue
        yield path


def _module_name(path: Path) -> str:
    """src/lvke_mcp/domains/finance/spec.py -> lvke_mcp.domains.finance.spec"""
    try:
        relative = path.relative_to(SRC_ROOT)
    except ValueError:
        return ""
    parts = relative.with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _layer_of(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "lvke_mcp":
        return None
    return parts[1] if parts[1] in _LAYERS else None


def _package_of(module: str) -> str:
    """归到「拆分单元」粒度：servers/domains 取到第三段，其余取层。"""
    parts = module.split(".")
    if len(parts) >= 3 and parts[0] == "lvke_mcp" and parts[1] in ("servers", "domains", "adapters"):
        return ".".join(parts[:3])
    if len(parts) >= 2 and parts[0] == "lvke_mcp":
        return ".".join(parts[:2])
    return module


def _resolve_relative(module: str | None, level: int, source_module: str) -> str:
    """把 ``from . import x`` / ``from ..y import z`` 解析成绝对模块名。"""
    if level <= 0:
        return module or ""
    base = source_module.split(".")
    # level=1 表示当前包，需要去掉模块自身那一段。
    anchor = base[: len(base) - level] if len(base) >= level else []
    if module:
        anchor = anchor + module.split(".")
    return ".".join(anchor)


def _import_wrapper_names(tree: ast.AST) -> set[str]:
    """找出本模块内「把参数直接交给 import_module 的薄封装」函数名。

    ``runtime/resource_registry.py`` 用 ``def _module(name): return import_module(name)``
    做懒加载，调用点只出现 ``_module("lvke_mcp...")``。只匹配 import_module 字面参数
    会系统性漏掉这类边，因此把这种一层间接也认成动态导入点。
    """
    wrappers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [arg.arg for arg in node.args.args]
        if not params:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call) or not inner.args:
                continue
            func = inner.func
            calls_import = (
                (isinstance(func, ast.Attribute) and func.attr in ("import_module", "__import__"))
                or (isinstance(func, ast.Name) and func.id in ("import_module", "__import__"))
            )
            first = inner.args[0]
            if calls_import and isinstance(first, ast.Name) and first.id in params:
                wrappers.add(node.name)
                break
    return wrappers


def _string_module_targets(tree: ast.AST) -> list[tuple[str, int]]:
    """收集 import_module("...")、__import__("...") 与本地懒加载封装的字符串目标。"""
    dynamic_names = {"import_module", "__import__"} | _import_wrapper_names(tree)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_dynamic = (
            (isinstance(func, ast.Attribute) and func.attr in dynamic_names)
            or (isinstance(func, ast.Name) and func.id in dynamic_names)
        )
        if not is_dynamic or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append((first.value, int(getattr(node, "lineno", 0))))
    return found


def _scan_imports(path: Path, source_module: str) -> tuple[set[str], list[dict]]:
    """返回 (静态+动态目标模块集合, 动态加载明细)。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return set(), []

    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("lvke_mcp"):
                    targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_relative(node.module, node.level or 0, source_module)
            if resolved.startswith("lvke_mcp"):
                targets.add(resolved)
                # from lvke_mcp.domains.finance import spec -> 也记 .spec 子模块边
                for alias in node.names:
                    targets.add(f"{resolved}.{alias.name}")

    dynamic: list[dict] = []
    for value, lineno in _string_module_targets(tree):
        if value.startswith("lvke_mcp"):
            targets.add(value)
            dynamic.append({"target": value, "line": lineno})
    return targets, dynamic


def _known_modules() -> set[str]:
    return {_module_name(p) for p in _iter_py_files(PACKAGE_ROOT) if _module_name(p)}


def _normalize_target(target: str, known: set[str]) -> str | None:
    """把 ``pkg.mod.symbol`` 收敛到真实存在的模块名。"""
    parts = target.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in known:
            return candidate
        parts.pop()
    return None


def collect() -> dict:
    known = _known_modules()

    line_counts: dict[str, int] = {}
    total_lines = 0
    for path in _iter_py_files(PACKAGE_ROOT):
        rel = path.relative_to(REPO_ROOT).as_posix()
        count = len(path.read_text(encoding="utf-8").splitlines())
        line_counts[rel] = count
        total_lines += count

    consumers: dict[str, set[str]] = defaultdict(set)
    dynamic_loads: dict[str, list[dict]] = defaultdict(list)
    import_graph: dict[str, set[str]] = defaultdict(set)

    for root_name in SCAN_ROOTS:
        for path in _iter_py_files(REPO_ROOT / root_name):
            rel = path.relative_to(REPO_ROOT).as_posix()
            source_module = _module_name(path)
            targets, dynamic = _scan_imports(path, source_module)
            for target in targets:
                resolved = _normalize_target(target, known)
                if not resolved or resolved == source_module:
                    continue
                consumers[resolved].add(rel)
                if source_module:
                    import_graph[source_module].add(resolved)
            for item in dynamic:
                resolved = _normalize_target(item["target"], known)
                dynamic_loads[rel].append({
                    "target": item["target"],
                    "resolved": resolved,
                    "line": item["line"],
                })

    # 分层边（package 粒度，含来源文件，便于定位新增边）。
    layer_edges: dict[str, dict] = {}
    for source, targets in import_graph.items():
        source_layer = _layer_of(source)
        if source_layer is None:
            continue
        for target in targets:
            target_layer = _layer_of(target)
            if target_layer is None or target_layer == source_layer:
                continue
            key = f"{source_layer} -> {target_layer}"
            entry = layer_edges.setdefault(key, {
                "source_layer": source_layer,
                "target_layer": target_layer,
                "forbidden_direction": (source_layer, target_layer) in _FORBIDDEN_LAYER_EDGES,
                "package_edges": {},
            })
            pkg_key = f"{_package_of(source)} -> {_package_of(target)}"
            entry["package_edges"].setdefault(pkg_key, []).append(source)

    for entry in layer_edges.values():
        entry["package_edges"] = {
            key: sorted(set(value)) for key, value in sorted(entry["package_edges"].items())
        }

    cycles = _find_cycles(import_graph)

    return {
        "line_counts": line_counts,
        "total_lines": total_lines,
        "file_count": len(line_counts),
        "consumers": {key: sorted(value) for key, value in consumers.items()},
        "dynamic_loads": {key: value for key, value in sorted(dynamic_loads.items()) if value},
        "import_graph": {key: sorted(value) for key, value in sorted(import_graph.items())},
        "layer_edges": dict(sorted(layer_edges.items())),
        "cycles": cycles,
    }


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan 强连通分量：只报大小 > 1 的模块级循环。"""
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    result: list[list[str]] = []

    def strongconnect(node: str) -> None:
        indices[node] = low[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for successor in sorted(graph.get(node, ())):
            if successor not in indices:
                strongconnect(successor)
                low[node] = min(low[node], low[successor])
            elif successor in on_stack:
                low[node] = min(low[node], indices[successor])
        if low[node] == indices[node]:
            component = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1:
                result.append(sorted(component))

    previous_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(previous_limit, 10000))
    try:
        for node in sorted(graph):
            if node not in indices:
                strongconnect(node)
    finally:
        sys.setrecursionlimit(previous_limit)
    return sorted(result)


def build_document(long_threshold: int) -> dict:
    data = collect()
    long_files = {
        path: count
        for path, count in sorted(data["line_counts"].items(), key=lambda kv: -kv[1])
        if count >= long_threshold
    }
    return {
        "schema": "module_metrics.v1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "plan_ref": "dev-docs/plans/MODULARIZATION_PLAN.md §2/§7/§8",
        "long_threshold": long_threshold,
        "summary": {
            "file_count": data["file_count"],
            "total_lines": data["total_lines"],
            "long_file_count": len(long_files),
            "long_file_lines": sum(long_files.values()),
            "cycle_count": len(data["cycles"]),
            "forbidden_layer_edge_count": sum(
                1 for entry in data["layer_edges"].values() if entry["forbidden_direction"]
            ),
        },
        "long_files": long_files,
        "line_counts": data["line_counts"],
        "consumers": data["consumers"],
        "dynamic_loads": data["dynamic_loads"],
        "layer_edges": data["layer_edges"],
        "cycles": data["cycles"],
        "import_graph": data["import_graph"],
    }


def _layer_edge_package_set(document: dict) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for key, entry in document.get("layer_edges", {}).items():
        result[key] = set(entry.get("package_edges", {}))
    return result


def check_against(baseline: dict, current: dict) -> tuple[list[str], list[str]]:
    """返回 (violations, notes)。只判定新增，不判定历史存量。"""
    violations: list[str] = []
    notes: list[str] = []

    base_edges = _layer_edge_package_set(baseline)
    cur_edges = _layer_edge_package_set(current)
    forbidden = {f"{a} -> {b}" for a, b in _FORBIDDEN_LAYER_EDGES}

    for key, packages in sorted(cur_edges.items()):
        added = packages - base_edges.get(key, set())
        if not added:
            continue
        if key in forbidden:
            for pkg in sorted(added):
                violations.append(f"new forbidden layer edge [{key}]: {pkg}")
        else:
            for pkg in sorted(added):
                notes.append(f"new allowed cross-layer package edge [{key}]: {pkg}")

    for key, packages in sorted(base_edges.items()):
        removed = packages - cur_edges.get(key, set())
        for pkg in sorted(removed):
            notes.append(f"removed cross-layer package edge [{key}]: {pkg}")

    base_cycles = {tuple(c) for c in baseline.get("cycles", [])}
    cur_cycles = {tuple(c) for c in current.get("cycles", [])}
    # 环按「参与其中的模块集合」归一化后比较，而不是按节点序列精确匹配。
    #
    # 拆分把实现搬进 ``_impl/`` 子模块后，同一个历史环的路径必然多出子模块节点
    # （``a -> b -> facade`` 变成 ``a -> b -> _impl.x -> _impl.y -> facade``）。
    # 按序列比较会把它同时报成「新增环」和「已解决环」——环总数不变却门禁失败，
    # 这是假阳性。归一化时把实现子模块折叠回其门面模块：判据变成「有没有新的
    # **模块组**成环」，而不是「环的路径写法有没有变」。
    def _fold(node: str) -> str:
        # ``pkg.reports._artifacts.query`` -> ``pkg.reports.artifacts``：实现包
        # 按约定命名为门面名加下划线前缀，因此去掉前缀就得到同级门面模块，
        # 子模块段整段丢弃。
        #
        # 不能改成「截断到实现包的父包」（得到 ``pkg.reports``），那会把
        # ``_artifacts`` 与 ``_doc_service`` 两个不同门面的环折叠成同一个 key，
        # 真的新环就会被吞掉。
        parts = node.split(".")
        for index, part in enumerate(parts):
            if part.startswith("_"):
                return ".".join([*parts[:index], part[1:]])
        return node

    def _key(cycle: tuple[str, ...]) -> frozenset[str]:
        return frozenset(_fold(n) for n in cycle)

    base_keys = {_key(c) for c in base_cycles}
    cur_keys = {_key(c) for c in cur_cycles}
    reported: set[frozenset[str]] = set()
    for cycle in sorted(cur_cycles):
        key = _key(cycle)
        if key not in base_keys and key not in reported:
            reported.add(key)
            violations.append(f"new import cycle: {' -> '.join(cycle)}")
    for cycle in sorted(base_cycles):
        if _key(cycle) not in cur_keys:
            notes.append(f"resolved import cycle: {' -> '.join(cycle)}")

    return violations, notes


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--long-threshold", type=int, default=800)
    parser.add_argument(
        "--check",
        type=Path,
        default=None,
        help="与该基线 JSON 比较，只在出现新增禁止边或新增循环时退出非零",
    )
    parser.add_argument("--top", type=int, default=15, help="打印最长的 N 个文件")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    document = build_document(args.long_threshold)

    if args.check is not None:
        baseline = json.loads(args.check.read_text(encoding="utf-8"))
        violations, notes = check_against(baseline, document)
        for note in notes:
            print(f"[note] {note}")
        for violation in violations:
            print(f"[FAIL] {violation}", file=sys.stderr)
        base_summary = baseline.get("summary", {})
        print(
            "files {} -> {} | lines {} -> {} | long {} -> {} | cycles {} -> {}".format(
                base_summary.get("file_count"), document["summary"]["file_count"],
                base_summary.get("total_lines"), document["summary"]["total_lines"],
                base_summary.get("long_file_count"), document["summary"]["long_file_count"],
                base_summary.get("cycle_count"), document["summary"]["cycle_count"],
            )
        )
        if violations:
            print(f"\n{len(violations)} boundary violation(s)", file=sys.stderr)
            return 1
        print("boundary check: conforming")
        return 0

    out = args.output.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = document["summary"]
    print(f"files: {summary['file_count']}  lines: {summary['total_lines']}")
    print(
        f"long files (>= {args.long_threshold}): {summary['long_file_count']}"
        f"  lines: {summary['long_file_lines']}"
    )
    print(f"cycles: {summary['cycle_count']}  forbidden-direction layer edges: {summary['forbidden_layer_edge_count']}")
    for path, count in list(document["long_files"].items())[: args.top]:
        consumer_count = len(document["consumers"].get(_module_name(REPO_ROOT / path), []))
        print(f"  {count:6d}  {path}  (consumers={consumer_count})")
    print(f"written -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
