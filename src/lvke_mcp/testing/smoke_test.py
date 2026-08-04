"""通过真实 stdio 子进程执行单个 MCP 服务的核心 smoke。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lvke_mcp.testing.protocol_testkit import (
    initialize_message,
    initialized_notification,
    run_raw,
    tool_call,
)

ROOT = Path(__file__).resolve().parents[4]  # src/lvke_mcp/testing -> 仓库根
WORKSPACE = "mcp-smoke-nonexistent"
PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    "lvke-data-acquisition": ("data_provider_status", {}),
    "lvke-data-analysis": ("analysis_status", {"workspace_id": WORKSPACE, "analysis_task_id": "analysis_missing"}),
    "lvke-project-planning": ("project_context_list", {"workspace_id": WORKSPACE}),
    "lvke-source-files": ("source_file_list", {"workspace_id": WORKSPACE}),
    "lvke-finance-model": ("finance_get_run", {"workspace_id": WORKSPACE}),
    "lvke-deep-research": ("dr_status", {"workspace_id": WORKSPACE}),
    "lvke-finance-tables": ("tables_validate", {"workspace_id": WORKSPACE, "run_id": "run_missing"}),
    "lvke-report-generation": ("report_get_readiness", {"workspace_id": WORKSPACE}),
    "lvke-asset-acquisition": ("acquisition_get_run", {"workspace_id": WORKSPACE, "run_id": "acqrun_missing"}),
    "lvke-deliverable-review": ("review_get", {"workspace_id": WORKSPACE, "review_id": "review_missing"}),
    "lvke-knowledge-governance": ("knowledge_list_candidates", {"workspace_id": WORKSPACE}),
    "lvke-zero-material-delivery": ("delivery_list_resources", {"workspace_id": WORKSPACE}),
    "finance-calc": ("calc_irr", {"cashflows": [-1000, 600, 600]}),
    "lvke-archive": ("search_archive", {"keyword": "光伏", "limit": 3}),
    "lvke-templates": ("list_templates", {}),
    "lvke-clients": ("search_clients", {"industry": "光伏"}),
    "lvke-experts": ("list_specialties", {}),
    "policy-search": ("search_policy", {"keyword": "长江"}),
    "statistics-cn": ("list_dictionaries", {}),
    "industry-research": ("search_report", {"industry": "汽车制造"}),
    "environmental-data": ("list_monitored_locations", {}),
    "map-geo": ("geocode", {"address": "武汉天河国际机场"}),
    "excel-bridge": ("list_sheets", {"path": "/dev/null"}),
}


def modules() -> dict[str, str]:
    payload = json.loads((ROOT / ".mcp.example.json").read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for name, descriptor in payload["mcpServers"].items():
        args = descriptor.get("args") or []
        if "-m" in args:
            result[name] = str(args[args.index("-m") + 1])
    return result


# Compatibility map used by the protocol tests.  It is derived from the same
# checked-in manifest used by the smoke command, so discovery cannot drift.
SERVERS = {name: {"module": module} for name, module in modules().items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("server", choices=sorted(PROBES))
    args = parser.parse_args()
    tool, arguments = PROBES[args.server]
    try:
        responses, stderr = run_raw(
            modules()[args.server],
            [
                initialize_message(1, "2025-11-25"),
                initialized_notification(),
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                tool_call(3, tool, arguments),
            ],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"smoke 失败: {exc}", file=sys.stderr)
        return 1
    if stderr:
        print(stderr, file=sys.stderr)
    init_response, list_response, call_response = responses[:3]
    assert init_response["result"]["serverInfo"]["name"] == args.server
    listed = list_response["result"]["tools"]
    assert any(item["name"] == tool for item in listed)
    print(json.dumps({"server": args.server, "tools": len(listed), "probe": tool, "response": call_response}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
