"""专业复核身份校验 —— MCP 自有实现。

仅保留被引子集：``ProfessionalReviewError`` 与 ``validate_authenticated_actor``。
原样复制，只去掉持久化与签审其余语义；行为与既有侧一致。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ProfessionalReviewError(RuntimeError):
    """Machine-readable service error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def validate_authenticated_actor(actor: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and sanitize an authenticated actor without persisting tokens."""

    if not isinstance(actor, Mapping):
        raise ProfessionalReviewError(
            "AUTHENTICATION_REQUIRED", "专业复核必须提供已认证身份",
        )
    actor_id = str(actor.get("actor_id") or "").strip()
    if actor.get("authenticated") is not True or not actor_id:
        raise ProfessionalReviewError(
            "AUTHENTICATION_REQUIRED", "专业复核必须提供已认证身份",
        )
    if len(actor_id) > 160 or any(ord(char) < 32 for char in actor_id):
        raise ProfessionalReviewError("INVALID_ACTOR", "复核人标识不合法")

    return {
        "actor_id": actor_id,
        "authenticated": True,
        "display_name": str(actor.get("display_name") or "").strip()[:160],
        "auth_method": str(actor.get("auth_method") or "").strip()[:80],
    }


__all__ = ["ProfessionalReviewError", "validate_authenticated_actor"]