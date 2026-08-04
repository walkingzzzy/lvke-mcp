"""Stable in-process facade for the public finance-model MCP operations."""

from __future__ import annotations

from typing import Any


def prepare_spec(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.servers.lvke_finance_model.server import _tool_prepare_spec

    return _tool_prepare_spec(args)


def validate_spec(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.servers.lvke_finance_model.server import _tool_validate_spec

    return _tool_validate_spec(args)


def confirm_spec(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.servers.lvke_finance_model.server import _tool_confirm_spec

    return _tool_confirm_spec(args)


def run_model(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.servers.lvke_finance_model.server import _tool_run_model

    return _tool_run_model(args)


def get_run(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.servers.lvke_finance_model.server import _tool_get_run

    return _tool_get_run(args)


__all__ = ["confirm_spec", "get_run", "prepare_spec", "run_model", "validate_spec"]