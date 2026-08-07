"""Blocked-response envelope shared by every data-analysis entry point."""

from __future__ import annotations

from typing import Any


def _missing(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "success": False,
        "transport_success": True,
        "business_success": False,
        "completed": False,
        "outcome": "blocked",
        "status": "blocked",
        "code": code,
        "message": message,
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [],
        **extra,
    }
