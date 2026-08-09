"""lvke-deep-research 工具的 outputSchema 定义与辅助构造器。"""

from __future__ import annotations

from typing import Any

from lvke_mcp.domains.research.output_contracts import (
    QUALITY_CONFIRM_OUTPUT_SCHEMA,
)

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
_QUALITY_CONFIRM_OUTPUT = QUALITY_CONFIRM_OUTPUT_SCHEMA
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


SOURCE_TYPES: tuple[str, ...] = (
    "source_snapshot",
    "source_file",
    "evidence_pack",
    "archive_chapter",
    "reviewed_knowledge",
    "policy_record",
    "industry_record",
    "source_reconstructed",
    "technical_fixture",
)

# 每类来源的 lvke:// 域前缀。此前所有分支同构，既没有 source_file，也不校验
# resource_uri 是否真来自该类对象所属的域，于是"证据包 id 配快照 uri"能过。
SOURCE_URI_DOMAINS: dict[str, str] = {
    "source_snapshot": "data-acquisition",
    "source_file": "source-files",
    "evidence_pack": "data-analysis",
}

_SOURCE_BASE_REQUIRED = (
    "source_type",
    "object_id",
    "resource_uri",
    "content_hash",
    "locator",
    "evidence_track",
    "allowed_uses",
)


def _uri_branch(source_type: str, domain: str) -> dict[str, Any]:
    return {
        "if": {"properties": {"source_type": {"const": source_type}}},
        "then": {
            "properties": {
                "resource_uri": {
                    "type": "string",
                    "pattern": rf"^lvke://{domain}/.+",
                    "description": f"{source_type} 的 Resource URI 必须来自 lvke://{domain}/",
                }
            }
        },
    }


# 扁平 + allOf/if-then 判别式，而不是 oneOf 同构分支：oneOf 失败时校验器只会回报
# 最后一个分支的错误（"'technical_fixture' was expected"），完全指不到真正缺的字段。
# 扁平结构下 source_type 是显式枚举判别式，缺字段/域不符都会直接点到该字段。
_MIXED_SOURCE_DESCRIPTOR = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "混合来源描述符。source_type 为判别式；各类型必填字段一致："
        + "、".join(_SOURCE_BASE_REQUIRED)
        + "。另按类型约束 resource_uri 所属域："
        + "；".join(
            f"{key}→lvke://{value}/" for key, value in SOURCE_URI_DOMAINS.items()
        )
        + "。technical_fixture 的 evidence_track 恒为 technical_fixture 且只允许 "
        "technical_validation 用途；source_reconstructed 的 evidence_track 恒为 "
        "source_reconstructed。"
    ),
    "properties": {
        "source_type": {
            "type": "string",
            "enum": list(SOURCE_TYPES),
            "description": (
                "判别式：source_snapshot=不可变网页/项目文件快照；"
                "source_file=已导入并解析的受控项目文件；"
                "evidence_pack=已固化证据包；archive_chapter=历史档案章节；"
                "reviewed_knowledge=已复核知识发布版；policy_record=政策记录；"
                "industry_record=行业记录；source_reconstructed=依据现有资料重建；"
                "technical_fixture=仅限技术金标验证"
            ),
        },
        "object_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "resource_uri": {"type": "string", "pattern": r"^lvke://.+"},
        "content_hash": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
        "locator": {"type": "string", "minLength": 1, "maxLength": 2000},
        "evidence_track": {
            "type": "string",
            "enum": [
                "real",
                "source_reconstructed",
                "technical_fixture",
                "controlled_assumption",
            ],
        },
        "allowed_uses": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": _SOURCE_ALLOWED_USE,
        },
        "title": {"type": "string", "maxLength": 500},
    },
    "required": list(_SOURCE_BASE_REQUIRED),
    "allOf": [
        *(
            _uri_branch(source_type, domain)
            for source_type, domain in SOURCE_URI_DOMAINS.items()
        ),
        {
            "if": {"properties": {"source_type": {"const": "technical_fixture"}}},
            "then": {
                "properties": {
                    "evidence_track": {"type": "string", "const": "technical_fixture"},
                    "allowed_uses": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "const": "technical_validation"},
                    },
                }
            },
        },
        {
            "if": {"properties": {"source_type": {"const": "source_reconstructed"}}},
            "then": {
                "properties": {
                    "evidence_track": {
                        "type": "string",
                        "const": "source_reconstructed",
                    }
                }
            },
        },
    ],
}
