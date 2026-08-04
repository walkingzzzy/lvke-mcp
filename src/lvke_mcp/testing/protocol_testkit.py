"""十一服务 MCP Server 协议合规测试的可复用 helper（方案 §13 目标模块）。

从 ``tests/mcp_servers/test_protocol_compliance.py`` 收口子进程启动、
initialize 握手报文与原始 JSON-RPC 批量交互，让协议边界断言可以对
全部十一个 Server 参数化复用，而不是每个测试文件各写一套进程管理。

本模块不 import pytest：断言留在测试内，这里只提供确定性驱动。
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mcp.types.version import LATEST_HANDSHAKE_VERSION, LATEST_MODERN_VERSION

REPO_ROOT = Path(__file__).resolve().parents[4]  # src/lvke_mcp/testing -> 仓库根

# 十一个 Codex 业务 Server 的启动模块，与注册主链保持同序。
SIX_LAYER_MODULES: tuple[str, ...] = (
    "lvke_mcp.servers.lvke_data_acquisition.server",
    "lvke_mcp.servers.lvke_data_analysis.server",
    "lvke_mcp.servers.lvke_project_planning.server",
    "lvke_mcp.servers.lvke_source_files.server",
    "lvke_mcp.servers.lvke_finance_model.server",
    "lvke_mcp.servers.lvke_deep_research.server",
    "lvke_mcp.servers.lvke_finance_tables.server",
    "lvke_mcp.servers.lvke_report_generation.server",
    "lvke_mcp.servers.lvke_asset_acquisition.server",
    "lvke_mcp.servers.lvke_deliverable_review.server",
    "lvke_mcp.servers.lvke_knowledge_governance.server",
    "lvke_mcp.servers.lvke_zero_material_delivery.server",
)

# 每个 Server 一个"必存在且 workspace_id 必填"的探针工具，
# 供入参 schema 边界测试构造真实（而非撞上 Unknown tool 的）失败。
SCHEMA_PROBE_TOOLS: dict[str, str] = {
    "lvke_mcp.servers.lvke_data_acquisition.server": "data_search",
    "lvke_mcp.servers.lvke_data_analysis.server": "analysis_status",
    "lvke_mcp.servers.lvke_project_planning.server": "project_context_get",
    "lvke_mcp.servers.lvke_source_files.server": "source_file_get",
    "lvke_mcp.servers.lvke_finance_model.server": "finance_get_run",
    "lvke_mcp.servers.lvke_deep_research.server": "dr_status",
    "lvke_mcp.servers.lvke_finance_tables.server": "tables_render",
    "lvke_mcp.servers.lvke_report_generation.server": "report_status",
    "lvke_mcp.servers.lvke_asset_acquisition.server": "acquisition_get_run",
    "lvke_mcp.servers.lvke_deliverable_review.server": "review_get",
    "lvke_mcp.servers.lvke_knowledge_governance.server": "knowledge_get_candidate",
    "lvke_mcp.servers.lvke_zero_material_delivery.server": "delivery_status",
}


def initialize_message(
    request_id: int,
    version: str = LATEST_HANDSHAKE_VERSION,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 initialize 请求；传 params 时原样使用（用于非法参数用例）。"""

    if params is None:
        params = {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "lvke-protocol-test", "version": "1.0.0"},
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": params,
    }


def initialized_notification() -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": "notifications/initialized"}


def tool_call(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def modern_request(
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a 2026-07-28 per-request envelope without an initialize handshake."""

    request_params = dict(params or {})
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": LATEST_MODERN_VERSION,
        "io.modelcontextprotocol/clientInfo": {
            "name": "lvke-protocol-test",
            "version": "1.0.0",
        },
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": request_params,
    }


def run_raw(
    module: str,
    messages: list[str | dict[str, Any]],
    *,
    timeout: float = 30,
) -> tuple[list[dict[str, Any]], str]:
    """以子进程启动 Server，按行灌入报文，返回 (stdout 响应列表, stderr)。

    非 dict 的 message 原样写入（用于 Parse error 等非法行用例）。
    进程须以 returncode 0 正常退出，stdout 每行都必须是合法 JSON——
    这本身就是"stdout 纯 JSON-RPC"的合规断言前提。
    """

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    process = subprocess.Popen(
        [sys.executable, "-m", module],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    responses: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    try:
        for message in messages:
            payload = message if isinstance(message, str) else json.dumps(message)
            process.stdin.write(payload + "\n")
            process.stdin.flush()
            is_notification = isinstance(message, dict) and "id" not in message
            if is_notification:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise TimeoutError(f"timed out waiting for {module} response")
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(f"{module} closed stdout before responding")
            responses.append(json.loads(line))
    finally:
        process.stdin.close()
    remaining = max(0.1, deadline - time.monotonic())
    process.wait(timeout=remaining)
    trailing_stdout = process.stdout.read()
    stderr = process.stderr.read()
    if process.returncode != 0:
        raise RuntimeError(
            f"{module} exited with {process.returncode}: {stderr[-2000:]}"
        )
    responses.extend(json.loads(line) for line in trailing_stdout.splitlines() if line)
    return responses, stderr
