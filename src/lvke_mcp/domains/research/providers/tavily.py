"""Tavily 检索 provider —— MCP 自有 stdio 客户端（计划 §23.3）。

Tavily 通过已配置的 MCP 搜索服务（``tavily-hikari``）调用，由该 MCP 服务
持有并管理凭据；本模块不直接持有外部 API key。服务命令经
``LVKE_MCP_TAVILY_SERVER`` 环境变量配置（如 ``python -m tavily_hikari.server``），
未配置或连接失败时 provider 不可用（如实上报，不构成单点硬依赖）。
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from typing import Any

PROVIDER_NAME = "tavily-hikari"
SEARCH_TOOL = "tavily_search"
EXTRACT_TOOL = "tavily_extract"
_SERVER_ENV = "LVKE_MCP_TAVILY_SERVER"
_CHILD_ENV = ("TAVILY_MCP_URL", "TAVILY_MCP_BEARER_TOKEN")
_OPEN_TIMEOUT = 8.0
_CALL_TIMEOUT = 45.0


def server_command() -> list[str] | None:
    """返回 tavily-hikari 服务的启动命令；未配置返回 None。"""
    raw = str(os.getenv(_SERVER_ENV) or "").strip()
    if not raw:
        return None
    return shlex.split(raw)


async def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    """经 stdio 调用 tavily-hikari MCP 工具；失败抛异常由调用方降级。"""
    command = server_command()
    if not command:
        raise RuntimeError(f"{_SERVER_ENV} 未配置")

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    child_env = {
        name: os.environ[name]
        for name in _CHILD_ENV
        if str(os.environ.get(name) or "").strip()
    }
    params = StdioServerParameters(
        command=command[0],
        args=command[1:],
        env=child_env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=_OPEN_TIMEOUT)
            result = await asyncio.wait_for(
                session.call_tool(name, arguments), timeout=_CALL_TIMEOUT
            )
            is_error = bool(
                getattr(result, "isError", getattr(result, "is_error", False))
            )
            if is_error:
                raise RuntimeError(f"{name} 调用失败: {result.content}")
            structured = getattr(
                result,
                "structuredContent",
                getattr(result, "structured_content", None),
            )
            if structured is not None:
                return structured
            text = "\n".join(
                item.text for item in (result.content or []) if hasattr(item, "text")
            )
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                return text


def _as_search_payload(payload: Any) -> dict[str, Any]:
    """把 tavily-hikari 返回规范化为 ``{"success": bool, "data": {"web": [...]}}``。"""
    if isinstance(payload, dict) and payload.get("success") is not None:
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("web"), list):
            return {"success": True, "data": data}
        return dict(payload)
    if isinstance(payload, dict):
        for key in ("data", "results", "web"):
            value = payload.get(key)
            if isinstance(value, list):
                return {"success": True, "data": {"web": value}}
        if isinstance(payload.get("data"), dict):
            return {"success": True, "data": payload["data"]}
        return {"success": False, "error": "tavily-hikari 响应结构未知"}
    return {"success": False, "error": "tavily-hikari 响应无效"}


async def tavily_search(query: str, limit: int = 5) -> dict[str, Any]:
    """搜索；返回与 hermes ``web_search_tool`` 同 schema 的 JSON 结构。"""
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 5
    try:
        payload = await _call_tool(SEARCH_TOOL, {"query": query, "limit": limit})
    except Exception as exc:  # noqa: BLE001 - provider 不可用是环境条件
        return {"success": False, "error": f"tavily-hikari 不可用: {type(exc).__name__}"}
    return _as_search_payload(payload)


async def tavily_extract(
    urls: list[str],
    output_format: str = "markdown",
) -> list[dict[str, Any]]:
    """抽取 URL 正文；失败返回空列表（调用方按 direct_http 降级）。"""
    try:
        payload = await _call_tool(
            EXTRACT_TOOL, {"urls": list(urls), "format": output_format}
        )
    except Exception:  # noqa: BLE001
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("results", "data", "extracted"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


async def provider_status() -> dict[str, Any]:
    """探测 provider 可用性；返回与 hermes ``list_providers`` 兼容的单条结构。"""
    command = server_command()
    if not command:
        return {
            "name": PROVIDER_NAME,
            "display_name": "Tavily (tavily-hikari)",
            "available": False,
            "search": True,
            "extract": True,
            "capabilities": {"reason": f"{_SERVER_ENV} 未配置"},
        }
    try:
        await _call_tool(SEARCH_TOOL, {"query": "连通性探测", "limit": 1})
        available = True
    except Exception:  # noqa: BLE001
        available = False
    return {
        "name": PROVIDER_NAME,
        "display_name": "Tavily (tavily-hikari)",
        "available": available,
        "search": True,
        "extract": True,
        "capabilities": {},
    }
