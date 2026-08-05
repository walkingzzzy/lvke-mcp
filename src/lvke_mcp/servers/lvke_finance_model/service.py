"""Stable in-process facade for the public finance-model MCP operations."""

from __future__ import annotations

from typing import Any


def prepare_spec(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.domains.finance.model_application import prepare_spec as use_case

    return use_case(args)


def validate_spec(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.domains.finance.model_application import validate_spec as use_case

    return use_case(args)


def confirm_spec(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.domains.finance.model_application import confirm_spec as use_case

    return use_case(args)


def run_model(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.domains.finance.model_application import run_model as use_case

    return use_case(args)


def get_run(args: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.domains.finance.model_application import get_run as use_case

    return use_case(args)


__all__ = ["confirm_spec", "get_run", "prepare_spec", "run_model", "validate_spec"]