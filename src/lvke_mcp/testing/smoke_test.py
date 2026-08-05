"""通过真实 stdio 子进程执行 MCP Server 核心 smoke。"""

from __future__ import annotations

import argparse
import json
import sys

from lvke_mcp.testing.protocol_testkit import (
    initialize_message,
    initialized_notification,
    run_raw,
    tool_call,
)

from lvke_mcp.testing.server_manifest import SERVER_SPECS

PROBES = {
    spec.name: (spec.probe_tool, dict(spec.probe_arguments))
    for spec in SERVER_SPECS
}


def modules() -> dict[str, str]:
    return {spec.name: spec.module for spec in SERVER_SPECS}


SERVERS = {spec.name: {"module": spec.module} for spec in SERVER_SPECS}


def smoke_server(server: str) -> dict[str, object]:
    tool, arguments = PROBES[server]
    responses, stderr = run_raw(
        modules()[server],
        [
            initialize_message(1, "2025-11-25"),
            initialized_notification(),
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
            tool_call(4, tool, arguments),
        ],
    )
    init_response, list_response, resources_response, call_response = responses[:4]
    assert init_response["result"]["serverInfo"]["name"] == server
    listed = list_response["result"]["tools"]
    assert "resources" in resources_response["result"]
    assert any(item["name"] == tool for item in listed)
    assert "result" in call_response, call_response
    return {
        "server": server,
        "ok": True,
        "tools": len(listed),
        "probe": tool,
        "response": call_response,
        "stderr": stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("server", nargs="?", choices=sorted(PROBES))
    args = parser.parse_args()
    targets = [args.server] if args.server else [spec.name for spec in SERVER_SPECS]
    results: list[dict[str, object]] = []
    for server in targets:
        try:
            result = smoke_server(server)
            print(f"[OK] {server}: tools={result['tools']} probe={result['probe']}")
        except Exception as exc:  # noqa: BLE001
            result = {"server": server, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            print(f"[FAIL] {server}: {result['error']}", file=sys.stderr)
        results.append(result)

    if args.server and results[0]["ok"]:
        print(json.dumps(results[0], ensure_ascii=False, indent=2))
    ok_count = sum(result["ok"] is True for result in results)
    print(f"smoke: {ok_count}/{len(results)} passed")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
