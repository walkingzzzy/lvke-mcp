"""14 个公开 MCP Server 协议合规测试的可复用 helper。

集中提供子进程启动、initialize 握手报文与原始 JSON-RPC 批量交互。
调用环境决定包的安装来源和工作目录，驱动本身不注入源码路径。
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import tempfile
import time
from typing import Any

from mcp.types.version import LATEST_HANDSHAKE_VERSION, LATEST_MODERN_VERSION

from lvke_mcp.testing.server_manifest import SERVER_SPECS

SIX_LAYER_MODULES: tuple[str, ...] = tuple(spec.module for spec in SERVER_SPECS)

SCHEMA_PROBE_TOOLS: dict[str, str] = {
    spec.module: spec.probe_tool for spec in SERVER_SPECS
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
    data_dir: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """以子进程启动 Server，按行灌入报文，返回 (stdout 响应列表, stderr)。

    非 dict 的 message 原样写入（用于 Parse error 等非法行用例）。
    进程须以 returncode 0 正常退出，stdout 每行都必须是合法 JSON——
    这本身就是"stdout 纯 JSON-RPC"的合规断言前提。

    Args:
        data_dir: 若提供，子进程使用该持久化目录（金标链等多步验收）；
            否则每调用一次创建隔离临时目录（逐工具探测）。
    """

    env = os.environ.copy()
    temporary_data: tempfile.TemporaryDirectory[str] | None = None
    if data_dir:
        env["LVKE_MCP_DATA_DIR"] = data_dir
    else:
        temporary_data = tempfile.TemporaryDirectory(prefix="lvke-protocol-")
        env["LVKE_MCP_DATA_DIR"] = temporary_data.name
    process = subprocess.Popen(
        [sys.executable, "-m", module],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        raise
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        if temporary_data is not None:
            temporary_data.cleanup()
