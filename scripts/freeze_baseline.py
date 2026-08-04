#!/usr/bin/env python3
"""阶段0 基线冻结工具：抓取全部正式 MCP Server 的外部契约。

对每个 server 走 stdio MCP：initialize -> notifications/initialized -> tools/list -> resources/list，
把完整响应 JSON 固化到 tests/fixtures/baseline/ 下，作为独立化版本的「外部行为」对照基准。

用法：
    .venv/bin/python mcp_servers/scripts/freeze_baseline.py [server...]
    # 不带参数 = 冻结全部 24 个 server
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = REPO_ROOT
PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python")

PROTOCOL_VERSION = "2025-11-25"

# 冻结的 server 集（与 mcp_servers/ 下 server.py 一一对应，_scaffold 为模板不计入）。
SERVERS = [
    "environmental_data",
    "excel_bridge",
    "finance_calc",
    "industry_research",
    "lvke_archive",
    "lvke_asset_acquisition",
    "lvke_clients",
    "lvke_data_acquisition",
    "lvke_data_analysis",
    "lvke_deep_research",
    "lvke_deliverable_review",
    "lvke_experts",
    "lvke_finance_model",
    "lvke_finance_tables",
    "lvke_knowledge_governance",
    "lvke_project_planning",
    "lvke_report_generation",
    "lvke_source_files",
    "lvke_templates",
    "lvke_zero_material_delivery",
    "map_geo",
    "policy_search",
    "statistics_cn",
]

BASELINE = MCP_ROOT / "tests" / "fixtures" / "baseline"


def call(proc, payload, timeout: float = 60.0):
    """写入一个请求并等待同 id 的响应；返回响应 dict 或 None。"""
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == payload.get("id"):
            return msg
    return None


def freeze_server(server: str) -> dict:
    out_dir = BASELINE / "tools-list"
    res_dir = BASELINE / "resources-list"
    ctr_dir = BASELINE / "contracts"
    for d in (out_dir, res_dir, ctr_dir):
        d.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [PYTHON, "-m", f"lvke_mcp.servers.{server}.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
    )
    try:
        init = call(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "freeze-baseline", "version": "1.0.0"},
            },
        })
        if init is None or "result" not in init:
            return {"server": server, "ok": False, "error": init or "no-initialize-response"}

        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        proc.stdin.flush()

        tools = call(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resources = call(proc, {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}})

        if tools is None or "result" not in tools:
            return {"server": server, "ok": False, "error": tools or "no-tools-response"}

        tool_list = tools["result"].get("tools", [])
        resource_list = resources["result"].get("resources", []) if resources and "result" in resources else []

        # 固化完整 tools/list（含 schemas 与 annotations），这是契约对照的原始基准。
        (out_dir / f"{server}.json").write_text(
            json.dumps(tool_list, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (res_dir / f"{server}.json").write_text(
            json.dumps(resource_list, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # 规范化契约：每个 tool 的 name/description/inputSchema/outputSchema/annotations。
        contracts = []
        for t in tool_list:
            contracts.append({
                "name": t.get("name"),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema"),
                "outputSchema": t.get("outputSchema"),
                "annotations": t.get("annotations"),
                "taskSupport": (t.get("execution") or {}).get("taskSupport"),
            })
        (BASELINE / "contracts" / f"{server}.json").write_text(
            json.dumps(contracts, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        return {
            "server": server,
            "ok": True,
            "tools": len(tool_list),
            "tool_names": [t.get("name") for t in tool_list],
            "resources": len(resource_list),
            "protocol_version": PROTOCOL_VERSION,
        }
    finally:
        proc.kill()


def main() -> None:
    targets = sys.argv[1:] or SERVERS
    results = []
    for server in targets:
        try:
            r = freeze_server(server)
        except Exception as exc:  # noqa: BLE001
            r = {"server": server, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        results.append(r)
        status = "OK" if r["ok"] else "FAIL"
        print(f"[{status}] {r['server']}: tools={r.get('tools', '?')} resources={r.get('resources', '?')}")

    manifest = {
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "protocol_version": PROTOCOL_VERSION,
        "python": PYTHON,
        "servers": results,
        "ok_count": sum(1 for r in results if r["ok"]),
        "total": len(results),
    }
    (BASELINE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    failed = [r for r in results if not r["ok"]]
    if failed:
        print(f"\n{failed} servers failed to freeze", file=sys.stderr)
        sys.exit(1)
    print(f"\nFrozen {len(results)} servers -> {BASELINE}")


if __name__ == "__main__":
    main()
