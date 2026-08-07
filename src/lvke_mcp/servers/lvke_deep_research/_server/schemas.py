"""lvke-deep-research 工具的 outputSchema 定义与辅助构造器。"""

from __future__ import annotations

from typing import Any

# ── 输出契约（envelope 风格同 lvke-finance-tables）─────────────────────────
# 每个工具都返回统一 envelope：status/resource_uris/warnings/blockers/
# next_actions；success/data/source/code/message 保持与既有响应包装兼容。

_ENVELOPE_PROPS: dict[str, Any] = {
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
}
_ENVELOPE_REQUIRED = ("success", "status", "resource_uris", "warnings", "blockers", "next_actions")


def _output_schema(
    extra: dict[str, Any] | None = None,
    *,
    success_requires: list[str] | None = None,
) -> dict[str, Any]:
    """构造单个工具的专属 outputSchema。

    ``success_requires`` 列出成功（success=True）时必须存在的工具特有字段；
    失败响应只需满足 envelope 基线，err 路径不被误伤。
    """

    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": True,
        "properties": {**_ENVELOPE_PROPS, **(extra or {})},
        "required": list(_ENVELOPE_REQUIRED),
    }
    if success_requires:
        schema["if"] = {"properties": {"success": {"const": True}}, "required": ["success"]}
        schema["then"] = {"required": list(success_requires)}
    return schema


_PREPARE_OUTPUT = _output_schema(
    {
        "research_brief": {"type": "object"},
        "plan_items": {"type": "array", "items": {"type": "object"}},
        "budget": {"type": "object"},
        "expected_deliverables": {"type": "array", "items": {"type": "string"}},
    },
    success_requires=["research_brief", "plan_items", "budget", "expected_deliverables"],
)
_START_OUTPUT = _output_schema(
    {
        "data": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string"},
                "profile": {},
                "hint": {"type": "string"},
            },
            "required": ["task_id", "status"],
        },
    },
    success_requires=["data"],
)
_CONTINUE_OUTPUT = _output_schema(
    {
        "task_id": {"type": "string"},
        "continued_from_task_id": {"type": "string"},
        "quality_thresholds_relaxed": {"type": "boolean"},
    },
    success_requires=["task_id", "continued_from_task_id", "quality_thresholds_relaxed"],
)
_STATUS_OUTPUT = _output_schema(
    {
        "data": {
            "type": "object",
            "properties": {
                "task_id": {},
                "status": {"type": "string"},
                "round_no": {},
                "budget": {},
                "quality": {},
                "updated_at": {},
                "is_terminal": {"type": "boolean"},
                "note": {"type": "string"},
            },
            "required": ["status", "is_terminal", "note"],
        },
    },
    success_requires=["data"],
)
_CANCEL_OUTPUT = _output_schema(
    {
        "data": {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "task_id": {},
                "cancel_requested": {"type": "boolean"},
                "reason": {"type": "string"},
            },
        },
    },
    success_requires=["data"],
)
_REPORT_OUTPUT = _output_schema(
    {
        "data": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "report_md": {"type": "string"},
                "citation_audit": {},
                "quality": {},
                "note": {"type": "string"},
            },
            "required": ["task_id", "report_md", "note"],
        },
    },
    success_requires=["data"],
)
_EVIDENCE_OUTPUT = _output_schema(
    {
        "data": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "evidence_graph": {},
                "sources": {},
                "references": {},
            },
            "required": ["task_id"],
        },
    },
    success_requires=["data"],
)
_BUNDLE_OUTPUT = _output_schema(
    {
        "research_package_id": {"type": "string"},
        "task_id": {"type": "string"},
        "basis_hash": {"type": "string"},
        # 各 artifact 名到 Resource URI 的映射（只含真实存在的产物）。
        "resources": {"type": "object", "additionalProperties": {"type": "string"}},
    },
    success_requires=["research_package_id", "task_id", "resources"],
)
_QUALITY_CONFIRM_OUTPUT = _output_schema(
    {
        "research_package_id": {"type": "string"},
        "parent_research_package_id": {"type": "string"},
        "quality_review_id": {"type": "string"},
        "quality_review_status": {"type": "string"},
        "quality": {"type": "object"},
        "evidence_policy": {"type": "string"},
        "project_fact_certified": {"type": "boolean"},
        "release_limitations": {"type": "array", "items": {"type": "string"}},
    },
    success_requires=["research_package_id", "quality_review_id", "quality_review_status"],
)
_RESOURCE_LIST_OUTPUT = _output_schema(
    {
        "resources": {"type": "array", "items": {"type": "object"}},
        "next_cursor": {"type": ["string", "null"]},
        "has_more": {"type": "boolean"},
        "snapshot_hash": {"type": "string"},
    },
    success_requires=["resources"],
)
_RESOURCE_READ_OUTPUT = _output_schema(
    {
        "uri": {"type": "string"},
        "mime_type": {"type": "string"},
        "content": {},
    },
    success_requires=["uri", "mime_type", "content"],
)
_PLAN_OUTPUT = _output_schema(
    {
        "task_id": {"type": "string"},
        "plan_revision_id": {"type": "string"},
        "proposal_id": {"type": "string"},
        "basis_hash": {"type": "string"},
        "content_hash": {"type": "string"},
        "plan": {"type": "object"},
        "replayed": {"type": "boolean"},
    }
)
_EVENT_OUTPUT = _output_schema(
    {
        "task_id": {"type": "string"},
        "events": {"type": "array", "items": {"type": "object"}},
        "next_cursor": {"type": ["string", "null"]},
        "has_more": {"type": "boolean"},
    }
)
_CHECKPOINT_OUTPUT = _output_schema(
    {
        "task_id": {"type": "string"},
        "checkpoint_id": {"type": "string"},
        "basis_hash": {"type": "string"},
        "resume_token": {"type": "string"},
        "expires_at": {"type": "string"},
    }
)
_RESUME_OUTPUT = _output_schema(
    {
        "task_id": {"type": "string"},
        "resumed_from_task_id": {"type": "string"},
        "checkpoint_id": {"type": "string"},
        "plan_revision_id": {"type": "string"},
        "plan_basis_hash": {"type": "string"},
        "replayed": {"type": "boolean"},
    }
)

_SOURCE_ALLOWED_USE = {
    "type": "string",
    "enum": [
        "discovery",
        "fact_extraction",
        "evidence_candidate",
        "technical_validation",
        "narrative_context",
        "estimate_preview",
    ],
}


def _source_descriptor(source_type: str, description: str) -> dict[str, Any]:
    evidence_track_schema: dict[str, Any] = (
        {"type": "string", "const": "technical_fixture"}
        if source_type == "technical_fixture"
        else {
            "type": "string",
            "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption"],
        }
    )
    return {
        "type": "object",
        "description": description,
        "additionalProperties": False,
        "properties": {
            "source_type": {"type": "string", "const": source_type},
            "object_id": {"type": "string", "minLength": 1, "maxLength": 160},
            "resource_uri": {"type": "string", "pattern": r"^lvke://.+"},
            "content_hash": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
            "locator": {"type": "string", "minLength": 1, "maxLength": 2000},
            "evidence_track": evidence_track_schema,
            "allowed_uses": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": _SOURCE_ALLOWED_USE,
            },
            "title": {"type": "string", "maxLength": 500},
        },
        "required": [
            "source_type",
            "object_id",
            "resource_uri",
            "content_hash",
            "locator",
            "evidence_track",
            "allowed_uses",
        ],
    }


_MIXED_SOURCE_DESCRIPTOR = {
    "type": "object",
    "oneOf": [
        _source_descriptor("source_snapshot", "不可变网页或项目文件快照"),
        _source_descriptor("evidence_pack", "已固化证据包"),
        _source_descriptor("archive_chapter", "历史档案章节"),
        _source_descriptor("reviewed_knowledge", "已复核知识发布版"),
        _source_descriptor("policy_record", "政策记录"),
        _source_descriptor("industry_record", "行业记录"),
        _source_descriptor("source_reconstructed", "依据现有项目资料重建的来源记录"),
        _source_descriptor("technical_fixture", "仅限技术金标验证的 fixture"),
    ]
}
