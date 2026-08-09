"""Public output contracts used on both sides of the Deep Research adapter."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


QUALITY_CONFIRM_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "success": {"type": "boolean"},
        "status": {"type": "string"},
        "resource_uris": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
        "source": {"type": "string"},
        "code": {"type": "string"},
        "message": {"type": "string"},
        "detail": {},
        "research_package_id": {"type": "string"},
        "parent_research_package_id": {"type": "string"},
        "quality_review_id": {"type": "string"},
        "quality_review_status": {"type": "string"},
        "quality": {"type": "object"},
        "evidence_policy": {"type": "string"},
        "project_fact_certified": {"type": "boolean"},
        "release_limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "success",
        "status",
        "resource_uris",
        "warnings",
        "blockers",
        "next_actions",
    ],
    "if": {
        "properties": {"success": {"const": True}},
        "required": ["success"],
    },
    "then": {
        "required": [
            "research_package_id",
            "quality_review_id",
            "quality_review_status",
        ],
    },
}


def validate_quality_confirmation_output(value: dict[str, Any]) -> None:
    """Raise ``ValidationError`` before any quality-confirmation write."""

    validator = Draft202012Validator(
        QUALITY_CONFIRM_OUTPUT_SCHEMA,
        format_checker=FormatChecker(),
    )
    validator.validate(value)
