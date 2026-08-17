"""模块化重构护栏门禁（dev-docs/plans/MODULARIZATION_PLAN.md §8）。

这组测试是「纯拆分」PR 的自动化验收条件，覆盖方案 §8 里原先只有人工检查的三项：

  1. MCP 契约不变     —— tools/resources 的名称、数量、schema、URI 与冻结基线一致；
  2. Python API 不变  —— 原 import 路径与稳定符号仍可用，签名未变；
  3. 依赖边界不退化   —— 不新增禁止方向的跨层边，不新增循环 import。

设计取舍：

  * 全部基线都放在 ``tests/fixtures/baseline/`` 下随仓库留存。
    ``quality/`` 在 .gitignore 里，把基线放那里会让门禁在干净 clone 上静默跳过。
  * 基线缺失时**硬失败**而不是 skipTest，避免「门禁存在但从未真正比较」。
  * 只判定「消失/变化」，不判定「新增」。拆分本身必然新增模块与文件边。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "tests" / "fixtures" / "baseline"
# 重构基线随仓库留存（不能放 gitignore 的 quality/，否则干净 clone 上门禁会静默跳过）。
REFACTOR_BASELINE = BASELINE / "refactor"
SCRIPTS = REPO_ROOT / "scripts"


def _load_script(name: str) -> Any:
    """按路径加载 scripts/ 下的工具模块（scripts 不是可安装包）。"""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_refactor_guard_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 环境异常
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class McpContractBaselineTest(unittest.TestCase):
    """冻结的 tools/list 与 resources/list 契约不得被拆分改变。"""

    @classmethod
    def setUpClass(cls) -> None:
        from lvke_mcp.testing.server_manifest import SERVER_SPECS

        cls.specs = SERVER_SPECS

    def _current_tools(self, module: str) -> list[dict]:
        from lvke_mcp.testing.protocol_testkit import (
            initialize_message,
            initialized_notification,
            run_raw,
        )

        responses, _stderr = run_raw(
            module,
            [
                initialize_message(1, "2025-11-25"),
                initialized_notification(),
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ],
        )
        return responses[1]["result"]["tools"]

    def test_manifest_matches_frozen_baseline(self) -> None:
        """14 个 server 的名称集合与冻结 manifest 一致。"""
        manifest_path = BASELINE / "manifest.json"
        self.assertTrue(manifest_path.exists(), f"missing frozen manifest: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frozen = sorted(entry["server"] for entry in manifest["servers"])
        current = sorted(spec.name for spec in self.specs)
        self.assertEqual(frozen, current)

    def test_tool_contracts_unchanged(self) -> None:
        """每个 server 的工具名、数量、inputSchema/outputSchema 与基线逐项一致。"""
        for spec in self.specs:
            with self.subTest(server=spec.name):
                contract_path = BASELINE / "contracts" / f"{spec.name}.json"
                self.assertTrue(contract_path.exists(), f"missing contract baseline: {contract_path}")
                frozen = json.loads(contract_path.read_text(encoding="utf-8"))
                current = self._current_tools(spec.module)

                frozen_by_name = {item["name"]: item for item in frozen}
                current_by_name = {item["name"]: item for item in current}
                self.assertEqual(
                    sorted(frozen_by_name),
                    sorted(current_by_name),
                    f"{spec.name}: tool name set changed",
                )

                for name, frozen_tool in frozen_by_name.items():
                    current_tool = current_by_name[name]
                    self.assertEqual(
                        frozen_tool["inputSchema"],
                        current_tool.get("inputSchema"),
                        f"{spec.name}.{name}: inputSchema changed",
                    )
                    self.assertEqual(
                        frozen_tool["outputSchema"],
                        current_tool.get("outputSchema"),
                        f"{spec.name}.{name}: outputSchema changed",
                    )
                    self.assertEqual(
                        frozen_tool.get("annotations"),
                        current_tool.get("annotations"),
                        f"{spec.name}.{name}: annotations changed",
                    )

    def test_resource_uri_templates_unchanged(self) -> None:
        """resources/list 的 URI 集合不得因拆分而变化。"""
        from lvke_mcp.testing.protocol_testkit import (
            initialize_message,
            initialized_notification,
            run_raw,
        )

        for spec in self.specs:
            with self.subTest(server=spec.name):
                path = BASELINE / "resources-list" / f"{spec.name}.json"
                self.assertTrue(path.exists(), f"missing resources baseline: {path}")
                frozen = json.loads(path.read_text(encoding="utf-8"))
                responses, _stderr = run_raw(
                    spec.module,
                    [
                        initialize_message(1, "2025-11-25"),
                        initialized_notification(),
                        {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
                    ],
                )
                current = responses[1]["result"].get("resources", [])
                self.assertEqual(
                    sorted(item.get("uri", "") for item in frozen),
                    sorted(item.get("uri", "") for item in current),
                    f"{spec.name}: resource URI set changed",
                )


class PythonApiBaselineTest(unittest.TestCase):
    """原 import 路径与稳定符号必须继续可用（门面兼容）。"""

    def test_public_api_compatible_with_snapshot(self) -> None:
        snapshot = REFACTOR_BASELINE / "api_snapshot.json"
        self.assertTrue(
            snapshot.exists(),
            f"missing API baseline {snapshot}; run: python scripts/api_snapshot.py",
        )
        api_snapshot = _load_script("api_snapshot")
        baseline = json.loads(snapshot.read_text(encoding="utf-8"))
        current = api_snapshot.build_document()
        violations, _notes = api_snapshot.check_against(baseline, current)
        self.assertEqual([], violations, "public API regressions:\n" + "\n".join(violations))


class DependencyBoundaryTest(unittest.TestCase):
    """冻结现状、禁止新增：不新增禁止方向跨层边，不新增循环 import。"""

    def test_no_new_forbidden_edges_or_cycles(self) -> None:
        snapshot = REFACTOR_BASELINE / "module_metrics.json"
        self.assertTrue(
            snapshot.exists(),
            f"missing module metrics baseline {snapshot}; run: python scripts/module_metrics.py",
        )
        module_metrics = _load_script("module_metrics")
        baseline = json.loads(snapshot.read_text(encoding="utf-8"))
        current = module_metrics.build_document(baseline.get("long_threshold", 800))
        violations, _notes = module_metrics.check_against(baseline, current)
        self.assertEqual([], violations, "dependency boundary regressions:\n" + "\n".join(violations))

    def test_dynamic_module_targets_all_resolve(self) -> None:
        """字符串懒加载的目标模块必须真实存在（拆分后最容易断的一类边）。"""
        module_metrics = _load_script("module_metrics")
        current = module_metrics.build_document(800)
        unresolved: list[str] = []
        for source, items in current["dynamic_loads"].items():
            for item in items:
                if item["resolved"] is None:
                    unresolved.append(f"{source}: {item['target']} (line {item['line']})")
        self.assertEqual([], unresolved, "unresolved dynamic imports:\n" + "\n".join(unresolved))


if __name__ == "__main__":
    unittest.main()
