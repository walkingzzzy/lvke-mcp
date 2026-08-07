#!/usr/bin/env python3
"""模块化重构护栏：Python 公开 API 快照（导入路径 + 符号 + 签名）。

对应 MODULARIZATION_PLAN.md §7.3/§8「原 import 路径和稳定符号仍可用」。

拆分 PR 的核心风险是「文件搬走了，但门面没把符号 re-export 回来」。本工具在
基线 commit 上把每个模块的公开符号与签名固化下来，拆分后重新抓取并比较：

  * 模块消失 / 无法导入      = 失败
  * 公开符号消失            = 失败
  * 函数签名变化            = 失败
  * 新增模块或新增符号      = 通过（拆分的预期结果）
  * 符号所属实现模块变化    = 通过并记录（门面转发的预期结果）

抓取方式是**真实 import**，而不是静态解析，因此能覆盖门面 re-export、
``__getattr__`` 代理与运行时注入。

用法::

    python scripts/api_snapshot.py --output quality/api_snapshot.json
    python scripts/api_snapshot.py --check quality/api_snapshot.json
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import pkgutil
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 基线必须随仓库留存：``quality/`` 在 .gitignore 里，放那里会让门禁在干净 clone
# 上静默跳过。与 tools/resources 契约基线同一棵树。
DEFAULT_OUT = REPO_ROOT / "tests" / "fixtures" / "baseline" / "refactor" / "api_snapshot.json"

PACKAGE = "lvke_mcp"

# 这些子包在导入期就会启动 stdio server 或需要外部副作用，不纳入 import 快照。
_SKIP_SUFFIXES = (".__main__",)


def _iter_modules() -> list[str]:
    package = importlib.import_module(PACKAGE)
    names = [PACKAGE]
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{PACKAGE}."):
        if info.name.endswith(_SKIP_SUFFIXES):
            continue
        names.append(info.name)
    return sorted(names)


# ``repr`` 里的对象地址（``<... at 0x105820440>``）每个进程都不同。签名里出现
# 哨兵默认值（dataclasses.MISSING、函数默认参数）时会把地址带进快照，导致
# 每次运行都「签名变化」。抹掉地址，只保留类型与限定名。
_ADDRESS_RE = re.compile(r" at 0x[0-9a-fA-F]+")


def _stable_signature(text: str) -> str:
    return _ADDRESS_RE.sub("", text)


def _signature_of(value: object) -> str | None:
    try:
        return _stable_signature(str(inspect.signature(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _public_names(module: object) -> list[str]:
    declared = getattr(module, "__all__", None)
    if isinstance(declared, (list, tuple)):
        return sorted(str(name) for name in declared)
    return sorted(name for name in dir(module) if not name.startswith("_"))


def _describe(module_name: str, module: object) -> dict:
    symbols: dict[str, dict] = {}
    for name in _public_names(module):
        try:
            value = getattr(module, name)
        except Exception as exc:  # noqa: BLE001
            symbols[name] = {"kind": "error", "error": f"{type(exc).__name__}: {exc}"}
            continue

        kind = type(value).__name__
        if inspect.ismodule(value):
            # 只记录「这里可以拿到一个模块」，不递归展开，避免快照爆炸。
            symbols[name] = {"kind": "module", "target": getattr(value, "__name__", None)}
            continue

        entry: dict = {"kind": kind}
        if inspect.isclass(value) or inspect.isroutine(value):
            entry["kind"] = "class" if inspect.isclass(value) else "callable"
            entry["signature"] = _signature_of(value)
            # 实现所在模块：门面转发时会与 module_name 不同，这是允许的。
            entry["defined_in"] = getattr(value, "__module__", None)
            if inspect.isclass(value):
                entry["public_members"] = sorted(
                    member for member in dir(value) if not member.startswith("_")
                )
        elif isinstance(value, (str, int, float, bool, type(None))):
            entry["kind"] = "constant"
            entry["value_type"] = type(value).__name__
        elif isinstance(value, (list, tuple, set, frozenset, dict)):
            entry["kind"] = "container"
            entry["value_type"] = type(value).__name__
            entry["length"] = len(value)
        symbols[name] = entry
    return {"importable": True, "symbols": symbols}


def collect() -> dict:
    modules: dict[str, dict] = {}
    for name in _iter_modules():
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            modules[name] = {
                "importable": False,
                "error": f"{type(exc).__name__}: {exc}",
                "symbols": {},
            }
            continue
        try:
            modules[name] = _describe(name, module)
        except Exception:  # noqa: BLE001
            modules[name] = {
                "importable": True,
                "error": traceback.format_exc(limit=3),
                "symbols": {},
            }
    return modules


def build_document() -> dict:
    modules = collect()
    unimportable = sorted(name for name, data in modules.items() if not data.get("importable"))
    return {
        "schema": "api_snapshot.v1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "plan_ref": "MODULARIZATION_PLAN.md §7.3/§8",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "summary": {
            "module_count": len(modules),
            "symbol_count": sum(len(data.get("symbols", {})) for data in modules.values()),
            "unimportable_count": len(unimportable),
        },
        "unimportable": unimportable,
        "modules": modules,
    }


def check_against(baseline: dict, current: dict) -> tuple[list[str], list[str]]:
    violations: list[str] = []
    notes: list[str] = []

    base_modules: dict[str, dict] = baseline.get("modules", {})
    cur_modules: dict[str, dict] = current.get("modules", {})

    for name in sorted(base_modules):
        base = base_modules[name]
        if name not in cur_modules:
            violations.append(f"module disappeared: {name}")
            continue
        cur = cur_modules[name]
        if base.get("importable") and not cur.get("importable"):
            violations.append(f"module no longer importable: {name} ({cur.get('error')})")
            continue

        base_symbols: dict[str, dict] = base.get("symbols", {})
        cur_symbols: dict[str, dict] = cur.get("symbols", {})
        for symbol in sorted(base_symbols):
            if symbol not in cur_symbols:
                violations.append(f"symbol disappeared: {name}.{symbol}")
                continue
            base_entry = base_symbols[symbol]
            cur_entry = cur_symbols[symbol]
            base_sig = base_entry.get("signature")
            cur_sig = cur_entry.get("signature")
            if base_sig is not None and cur_sig is not None and base_sig != cur_sig:
                violations.append(
                    f"signature changed: {name}.{symbol}: {base_sig} -> {cur_sig}"
                )
            base_kind = base_entry.get("kind")
            cur_kind = cur_entry.get("kind")
            if base_kind != cur_kind:
                violations.append(f"kind changed: {name}.{symbol}: {base_kind} -> {cur_kind}")
            base_where = base_entry.get("defined_in")
            cur_where = cur_entry.get("defined_in")
            if base_where and cur_where and base_where != cur_where:
                notes.append(
                    f"implementation moved (facade ok): {name}.{symbol}: {base_where} -> {cur_where}"
                )
            base_members = base_entry.get("public_members")
            cur_members = cur_entry.get("public_members")
            if isinstance(base_members, list) and isinstance(cur_members, list):
                for member in sorted(set(base_members) - set(cur_members)):
                    violations.append(f"class member disappeared: {name}.{symbol}.{member}")

        for symbol in sorted(set(cur_symbols) - set(base_symbols)):
            notes.append(f"new symbol: {name}.{symbol}")

    for name in sorted(set(cur_modules) - set(base_modules)):
        notes.append(f"new module: {name}")

    return violations, notes


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", type=Path, default=None)
    parser.add_argument("--max-notes", type=int, default=40)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    document = build_document()

    if args.check is not None:
        baseline = json.loads(args.check.read_text(encoding="utf-8"))
        violations, notes = check_against(baseline, document)
        for note in notes[: args.max_notes]:
            print(f"[note] {note}")
        if len(notes) > args.max_notes:
            print(f"[note] ... {len(notes) - args.max_notes} more")
        for violation in violations:
            print(f"[FAIL] {violation}", file=sys.stderr)
        print(
            "modules {} -> {} | symbols {} -> {}".format(
                baseline.get("summary", {}).get("module_count"),
                document["summary"]["module_count"],
                baseline.get("summary", {}).get("symbol_count"),
                document["summary"]["symbol_count"],
            )
        )
        if violations:
            print(f"\n{len(violations)} API compatibility violation(s)", file=sys.stderr)
            return 1
        print("api check: compatible")
        return 0

    out = args.output.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = document["summary"]
    print(f"modules: {summary['module_count']}  symbols: {summary['symbol_count']}")
    if document["unimportable"]:
        print(f"unimportable ({summary['unimportable_count']}):")
        for name in document["unimportable"]:
            print(f"  {name}: {document['modules'][name].get('error')}")
    print(f"written -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
