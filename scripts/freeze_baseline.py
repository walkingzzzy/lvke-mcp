#!/usr/bin/env python3
"""阶段0 基线冻结工具：抓取全部正式 MCP Server 的外部契约。

对每个 server 走 stdio MCP：initialize -> notifications/initialized -> tools/list -> resources/list，
把完整响应 JSON 固化到 tests/fixtures/baseline/ 下，作为独立化版本的「外部行为」对照基准。

用法：
    python scripts/freeze_baseline.py [server...]
    # 不带参数 = 冻结统一 manifest 中的全部 23 个 server
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from lvke_mcp.testing.server_manifest import SERVER_BY_NAME, SERVER_SPECS

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = REPO_ROOT
PYTHON = sys.executable

PROTOCOL_VERSION = "2025-11-25"

# 唯一分母来自可随 wheel 安装的包内 manifest。
SERVERS = [spec.name for spec in SERVER_SPECS]

BASELINE = MCP_ROOT / "tests" / "fixtures" / "baseline"
BASELINE_COLLECTIONS = ("tools-list", "resources-list", "contracts")


def prune_stale_baselines() -> None:
    expected = {f"{spec.name}.json" for spec in SERVER_SPECS}
    for collection in BASELINE_COLLECTIONS:
        directory = BASELINE / collection
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.glob("*.json"):
            if path.name not in expected:
                path.unlink()


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
    spec = SERVER_BY_NAME[server]
    out_dir = BASELINE / "tools-list"
    res_dir = BASELINE / "resources-list"
    ctr_dir = BASELINE / "contracts"
    for d in (out_dir, res_dir, ctr_dir):
        d.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    temporary_data = tempfile.TemporaryDirectory(prefix="lvke-freeze-")
    env["LVKE_MCP_DATA_DIR"] = temporary_data.name
    proc = subprocess.Popen(
        [PYTHON, "-m", spec.module],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
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
        proc.wait()
        temporary_data.cleanup()


def main() -> None:
    targets = sys.argv[1:] or SERVERS
    unknown = sorted(set(targets) - set(SERVERS))
    if unknown:
        print(f"unknown servers: {unknown}", file=sys.stderr)
        sys.exit(2)
    if targets == SERVERS:
        prune_stale_baselines()
        existing_servers: list[dict] = []
    else:
        try:
            existing = json.loads((BASELINE / "manifest.json").read_text(encoding="utf-8"))
            existing_servers = [
                row for row in (existing.get("servers") or [])
                if isinstance(row, dict) and row.get("server")
            ]
        except (OSError, json.JSONDecodeError):
            existing_servers = []
    results = []
    for server in targets:
        try:
            r = freeze_server(server)
        except Exception as exc:  # noqa: BLE001
            r = {"server": server, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        results.append(r)
        status = "OK" if r["ok"] else "FAIL"
        print(f"[{status}] {r['server']}: tools={r.get('tools', '?')} resources={r.get('resources', '?')}")

    by_name = {str(row.get("server")): row for row in existing_servers}
    for row in results:
        by_name[str(row.get("server"))] = row
    merged = [by_name[spec] for spec in SERVERS if spec in by_name]
    extra = [row for name, row in by_name.items() if name not in set(SERVERS)]
    manifest_servers = merged + extra
    manifest = {
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "protocol_version": PROTOCOL_VERSION,
        "python": "installed-environment",
        "servers": manifest_servers,
        "ok_count": sum(1 for r in manifest_servers if r.get("ok")),
        "total": len(manifest_servers),
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
