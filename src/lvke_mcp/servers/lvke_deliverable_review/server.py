"""Official stdio MCP server for unified deliverable review."""

from __future__ import annotations

from typing import Any

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.runtime.schemas import make_tool_output_schema
from lvke_mcp.servers.lvke_deliverable_review import rubrics, service

SERVER_NAME = "lvke-deliverable-review"
SERVER_VERSION = "0.1.0"
_REVIEW_TARGET_SCHEMA_URI = "lvke://schemas/review-target"
_REVIEW_FINDING_DISPOSITION_SCHEMA_URI = (
    "lvke://schemas/review-finding-disposition"
)
logger = get_logger(SERVER_NAME)

_TARGET_TYPES = [
    "finance_run",
    "finance_tables_package",
    "finance_xlsx",
    "finance_xlsx_source",
    "acquisition_run",
    "acquisition_tables_package",
    "report_revision",
    "report_artifact",
    "combined_deliverable",
]
_COMPONENT_TARGET_TYPES = [item for item in _TARGET_TYPES if item != "combined_deliverable"]
_RULE_PACK_IDS = [
    "core-deliverable",
    "finance-core",
    "report-core",
    "combined-core",
    "generic-feasibility",
    "amusement-feasibility",
    "asset-acquisition",
    "hotel-acquisition",
    "solar-acquisition",
    "mineral-processing",
]
_FINDING_STATUSES = [
    "open",
    "confirmed",
    "rejected",
    "remediation_in_progress",
    "false_positive_appeal",
    "waiver_requested",
    "waived",
    "resolved",
    "superseded",
]

_SAFE_ID = {
    "type": "string",
    "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
}
_STRING = {"type": "string", "minLength": 1, "maxLength": 2000}
_ID = {"type": "string", "minLength": 1, "maxLength": 256}
_IDEMPOTENCY_KEY = {"type": "string", "minLength": 1, "maxLength": 160}
_ARTIFACT_DOMAIN = {
    "type": "string",
    "enum": ["generic_feasibility", "asset_acquisition"],
    "description": "报告工件所属的唯一存储域；用于阻止跨域同 ID 歧义。",
}
_SHA256 = {
    "type": "string",
    "pattern": r"^(?:sha256:)?[0-9a-fA-F]{64}$",
}
def _target_variant(
    target_type: str,
    *,
    extra_properties: dict[str, Any] | None = None,
    extra_required: tuple[str, ...] = (),
    description: str = "",
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "description": description,
        "properties": {
            "target_type": {"const": target_type},
            "target_id": {
                "type": "string", "minLength": 1, "maxLength": 4096,
                "description": "不可变目标对象 ID 或受控 Resource 标识",
            },
            **(extra_properties or {}),
        },
        "required": ["target_type", "target_id", *extra_required],
    }


def _target_variants(*, include_combined: bool) -> list[dict[str, Any]]:
    variants = [
        _target_variant(target_type, description=f"审查 {target_type} 不可变对象")
        for target_type in (
            "finance_run", "finance_tables_package", "acquisition_run",
            "acquisition_tables_package", "report_revision",
        )
    ]
    variants.extend([
        _target_variant(
            "finance_xlsx",
            extra_properties={"file_path": {"type": "string", "minLength": 1, "maxLength": 4096}},
            description="审查受控财务 XLSX；target_id 可直接使用 Resource URI",
        ),
        _target_variant(
            "finance_xlsx_source",
            extra_properties={"source_file_id": _ID},
            extra_required=("source_file_id",),
            description="审查已上传并完成解析的财务 XLSX source",
        ),
        _target_variant(
            "report_artifact",
            extra_properties={"artifact_domain": {"const": "generic_feasibility"}},
            extra_required=("artifact_domain",),
            description="审查通用可研报告工件",
        ),
        _target_variant(
            "report_artifact",
            extra_properties={"artifact_domain": {"const": "asset_acquisition"}},
            extra_required=("artifact_domain",),
            description="审查资产收购报告工件",
        ),
    ])
    if include_combined:
        component_schema = {
            "type": "object",
            "additionalProperties": False,
            "description": "combined deliverable 的非递归不可变组件",
            "properties": {
                "target_type": {"type": "string", "enum": _COMPONENT_TARGET_TYPES},
                "target_id": _ID,
                "file_path": {"type": "string", "minLength": 1, "maxLength": 4096},
                "source_file_id": _ID,
                "artifact_domain": _ARTIFACT_DOMAIN,
            },
            "required": ["target_type", "target_id"],
        }
        variants.append(_target_variant(
            "combined_deliverable",
            extra_properties={
                "components": {
                    "type": "array", "items": component_schema,
                    "minItems": 2, "maxItems": 20,
                },
            },
            extra_required=("components",),
            description="由两个及以上不可变组件组成的统一交付对象",
        ))
    return variants


_COMPONENT_TARGET = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target_type": {"type": "string", "enum": _COMPONENT_TARGET_TYPES},
        "target_id": _ID,
        "file_path": {"type": "string", "minLength": 1, "maxLength": 4096},
        "source_file_id": _ID,
        "artifact_domain": _ARTIFACT_DOMAIN,
    },
    "required": ["target_type", "target_id"],
}
_TARGET = {
    "oneOf": _target_variants(include_combined=True),
    "description": "按 target_type 判别的统一审查目标",
    "x-lvke-schema-uri": _REVIEW_TARGET_SCHEMA_URI,
    "examples": [{"target_type": "report_revision", "target_id": "rrv_example"}],
}
_PUBLIC_TARGET = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target_type": {"type": "string", "enum": _TARGET_TYPES},
        "target_id": {"type": "string", "minLength": 1, "maxLength": 4096},
        "file_path": {"type": "string", "minLength": 1, "maxLength": 4096},
        "source_file_id": _ID,
        "artifact_domain": _ARTIFACT_DOMAIN,
        "components": {
            "type": "array",
            "minItems": 2,
            "maxItems": 20,
            "items": _COMPONENT_TARGET,
        },
    },
    "required": ["target_type", "target_id"],
    "x-lvke-schema-uri": _REVIEW_TARGET_SCHEMA_URI,
}
_RULE_PACK_LIST = {
    "type": "array",
    "items": {"type": "string", "enum": _RULE_PACK_IDS},
    "maxItems": len(_RULE_PACK_IDS),
    "uniqueItems": True,
}
_PROJECT_CONTEXT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target_type": {"type": "string", "enum": _TARGET_TYPES},
        "industry_code": {"type": "string", "minLength": 1, "maxLength": 128},
        "project_type": {"type": "string", "enum": ["generic_feasibility", "asset_acquisition"]},
        "transaction_structure": {"type": "string", "enum": ["new_build", "operation_lease", "asset_acquisition", "equity_acquisition", "ppp", "other"]},
        "asset_type": {"type": "string", "enum": ["general", "amusement_park", "solar_power", "hotel_lease", "mineral_processing"]},
        "evidence_track": {"type": "string", "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption"], "default": "real"},
        "review_purpose": {
            "type": "string",
            "enum": ["process_acceptance", "project_delivery"],
            "description": "审查用途；process_acceptance 只判技术链，project_delivery 叠加正式发布资格。",
        },
        "release_scope": {
            "type": "string",
            "enum": ["process_acceptance", "project_delivery"],
            "description": "review_purpose 的兼容别名；两者同时给出时必须一致。",
        },
    },
}
_FACILITIES = {
    "type": "array",
    "maxItems": 500,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "facility_id": _ID,
            "name": _STRING,
            "facility_type": _STRING,
            "model": _STRING,
            "quantity": {"type": "integer", "minimum": 1, "maximum": 100000},
        },
        "required": ["facility_type"],
    },
}
_EVIDENCE_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "file_id": _ID,
        "source_id": _ID,
        "url": {"type": "string", "format": "uri", "maxLength": 4096},
        "locator": _STRING,
        "page": {"oneOf": [{"type": "integer", "minimum": 1}, _STRING]},
        "paragraph": {"oneOf": [{"type": "integer", "minimum": 1}, _STRING]},
        "table": _STRING,
        "cell": _STRING,
        "range": _STRING,
        "row": {"oneOf": [{"type": "integer", "minimum": 1}, _STRING]},
        "column": {"oneOf": [{"type": "integer", "minimum": 1}, _STRING]},
        "fetched_at": {"type": "string", "maxLength": 100},
        "content_hash": _SHA256,
        "sha256": _SHA256,
        "note": {"type": "string", "maxLength": 4000},
    },
    "required": ["source_id", "locator", "content_hash"],
    "description": "受控整改证据。兼容字段仍可读取，但公共调用固定使用 source_id、locator、content_hash。",
}
_EVIDENCE_LIST = {
    "type": "array",
    "items": _EVIDENCE_ITEM,
    "minItems": 1,
    "maxItems": 200,
}
def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {**properties},
        "required": required,
    }


def _write_schema(
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return _schema(
        {
            "workspace_id": _SAFE_ID,
            "idempotency_key": _IDEMPOTENCY_KEY,
            **properties,
        },
        ["workspace_id", "idempotency_key", *required],
    )


def _output_schema() -> dict[str, Any]:
    return make_tool_output_schema(
        {
            "code": {"type": "string"},
            "message": {"type": "string"},
            "validation_id": {"type": "string"},
            "validation_status": {"type": "string"},
            "overall_verdict": {
                "type": "string",
                "enum": ["pass", "conditional_pass", "fail", "incomplete"],
            },
            "technical_verdict": {
                "type": "string",
                "enum": ["pass", "conditional_pass", "fail", "incomplete"],
            },
            "release_verdict": {
                "type": "string",
                "enum": ["pass", "conditional_pass", "fail", "incomplete"],
            },
            "validation_complete": {"type": "boolean"},
            "deployment_mode": {
                "type": "string",
                "enum": ["enforced", "shadow"],
            },
        },
        required=("resource_uris", "warnings", "blockers", "next_actions"),
        status_values=(
            "ok",
            "accepted",
            "partial",
            "missing_inputs",
            "blocked",
            "failed",
            "incomplete",
        ),
        additional_properties=True,
    )


def _resource(uri: str) -> ReadResourceContents | None:
    resolved = service.resolve_resource(uri)
    if resolved is None:
        return None
    content, mime_type = resolved
    return ReadResourceContents(content, mime_type)


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read = types.ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    write = types.ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    server.register_tool(
        "review_list_rubrics",
        "按项目上下文列出版本化章节评分 rubric、维度、权重、来源 Skill 与通过门槛。",
        _schema(
            {"workspace_id": _SAFE_ID, "project_context": _PROJECT_CONTEXT},
            ["workspace_id"],
        ),
        rubrics.list_rubrics,
        _output_schema(),
        read,
    )
    server.register_tool(
        "review_score_section",
        "读取指定不可变报告 revision/section，以确定性规则评分并固化 RubricAssessment；不调用隐藏 LLM。",
        _schema(
            {
                "workspace_id": _SAFE_ID,
                "report_revision_id": _ID,
                "section_id": _ID,
                "rubric_id": {"type": "string", "const": "feasibility-section"},
            },
            ["workspace_id", "report_revision_id", "section_id"],
        ),
        rubrics.score_section,
        _output_schema(),
        read,
    )
    server.register_tool(
        "review_compare_assessments",
        "比较同一 rubric 版本下修改前后 assessment 的分数、维度和未关闭 blocker。",
        _schema(
            {
                "workspace_id": _SAFE_ID,
                "before_assessment_id": _ID,
                "after_assessment_id": _ID,
            },
            ["workspace_id", "before_assessment_id", "after_assessment_id"],
        ),
        rubrics.compare_assessments,
        _output_schema(),
        read,
    )

    server.register_tool(
        "review_prepare",
        "解析并锁定目标、上游依据、规则包和标准来源，返回不可变审查范围。",
        _write_schema(
            {
                "target": _TARGET,
                "rule_pack_ids": _RULE_PACK_LIST,
                "industry_overlays": _RULE_PACK_LIST,
                "project_context": _PROJECT_CONTEXT,
            },
            ["target"],
        ),
        service.prepare,
        _output_schema(),
        write,
        # Combined targets need their component contract inline.  The complete
        # public projection makes the supported combined_deliverable route
        # discoverable instead of looking like an opaque Resource-only value.
        public_input_schema=_write_schema(
            {
                "target": _PUBLIC_TARGET,
                "rule_pack_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "industry_overlays": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "project_context": {"type": "object"},
            },
            ["target"],
        ),
    )
    server.register_tool(
        "review_start",
        "从审查准备对象创建不可变运行；快速审查同步执行，深度审查可异步执行。",
        _write_schema(
            {
                "review_preparation_id": _ID,
                "mode": {
                    "type": "string", "enum": ["quick", "deep"], "default": "quick",
                    "description": "async execution 仅允许 deep；不满足时运行时返回可操作参数错误。",
                },
                "execution": {"type": "string", "enum": ["sync", "async"]},
                "deployment_mode": {
                    "type": "string",
                    "enum": ["enforced", "shadow"],
                    "default": "enforced",
                },
            },
            ["review_preparation_id"],
        ),
        service.start,
        _output_schema(),
        write,
    )
    server.register_tool(
        "review_get",
        "读取审查状态、总体结论、统计信息和质量检查结果。",
        _schema(
            {"workspace_id": _SAFE_ID, "review_id": _ID},
            ["workspace_id", "review_id"],
        ),
        service.get_review,
        _output_schema(),
        read,
    )
    server.register_tool(
        "review_list_findings",
        "按严重度、类别、状态和目标位置分页查询 findings。",
        _schema(
            {
                "workspace_id": _SAFE_ID,
                "review_id": _ID,
                "severity": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                "category": {"type": "string", "minLength": 1, "maxLength": 256},
                "status": {"type": "string", "enum": _FINDING_STATUSES},
                "location": {"type": "string", "minLength": 1, "maxLength": 2000},
                "cursor": {"type": "string", "maxLength": 8192},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            ["workspace_id", "review_id"],
        ),
        service.list_findings,
        _output_schema(),
        read,
    )
    server.register_tool(
        "review_get_finding",
        "读取 finding 的完整证据、复算轨迹、标准依据、处置历史和整改建议。",
        _schema(
            {"workspace_id": _SAFE_ID, "review_id": _ID, "finding_id": _ID},
            ["workspace_id", "review_id", "finding_id"],
        ),
        service.get_finding,
        _output_schema(),
        read,
    )

    common_disposition = {
        "review_id": _ID,
        "finding_id": _ID,
        "note": {"type": "string", "minLength": 1, "maxLength": 4000},
        "retest_review_id": _ID,
    }
    json_value = {
        "type": ["object", "array", "string", "number", "boolean", "null"],
        "description": "整改前后的可 JSON 序列化业务值",
    }
    disposition_schema = {
        "type": "object",
        "x-lvke-schema-uri": _REVIEW_FINDING_DISPOSITION_SCHEMA_URI,
        "oneOf": [
            _write_schema(
                {
                    **common_disposition,
                    "disposition": {"type": "string", "enum": [
                        "confirm", "confirmed", "remediate", "remediation_in_progress",
                    ]},
                },
                ["review_id", "finding_id", "disposition", "note"],
            ),
            _write_schema(
                {
                    **common_disposition,
                    "disposition": {"type": "string", "enum": [
                        "reject", "rejected", "false_positive", "false_positive_appeal",
                    ]},
                    "false_positive_reason": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "remediation_evidence": _EVIDENCE_LIST,
                },
                ["review_id", "finding_id", "disposition", "note", "false_positive_reason", "remediation_evidence"],
            ),
            _write_schema(
                {
                    **common_disposition,
                    "disposition": {"type": "string", "enum": [
                        "appeal_waiver", "compliance_waiver", "waiver_requested",
                    ]},
                    "waiver_scope": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "waiver_expires_at": {"type": "string", "format": "date-time"},
                    "waiver_invalidation_conditions": {
                        "type": "array", "items": _STRING, "minItems": 1,
                        "maxItems": 50, "uniqueItems": True,
                    },
                    "remediation_evidence": _EVIDENCE_LIST,
                },
                [
                    "review_id", "finding_id", "disposition", "note",
                    "waiver_scope", "waiver_expires_at",
                    "waiver_invalidation_conditions", "remediation_evidence",
                ],
            ),
            _write_schema(
                {
                    **common_disposition,
                    "disposition": {"type": "string", "enum": ["resolve", "resolved"]},
                    "closure_basis": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "before_value": json_value,
                    "after_value": json_value,
                    "remediation_evidence": _EVIDENCE_LIST,
                },
                [
                    "review_id", "finding_id", "disposition", "note",
                    "closure_basis", "before_value", "after_value",
                    "remediation_evidence",
                ],
            ),
        ],
        "description": "按 disposition 判别的完整处置契约",
    }
    server.register_tool(
        "review_disposition_finding",
        "提交 finding 确认、整改、误报申诉、限期豁免申请或基于复测的关闭处置。",
        disposition_schema,
        service.disposition_finding,
        _output_schema(),
        write,
    )
    server.register_tool(
        "review_retest",
        "对显式的新目标版本执行原规则包或显式升级包，并关联整改前后 findings。",
        _write_schema(
            {
                "review_id": _ID,
                "target": _TARGET,
                "remediation_evidence": _EVIDENCE_LIST,
                "rule_pack_ids": _RULE_PACK_LIST,
                "industry_overlays": _RULE_PACK_LIST,
                "mode": {"type": "string", "enum": ["quick", "deep"]},
            },
            ["review_id", "target", "remediation_evidence"],
        ),
        service.retest,
        _output_schema(),
        write,
    )
    server.register_tool(
        "review_export",
        "导出不可变 JSON、Markdown、DOCX 审查报告及 findings XLSX。",
        _write_schema(
            {
                "review_id": _ID,
                "formats": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["json", "markdown", "docx", "xlsx"]},
                    "minItems": 1,
                    "maxItems": 4,
                    "uniqueItems": True,
                },
            },
            ["review_id"],
        ),
        service.export_review,
        _output_schema(),
        write,
    )
    server.register_tool(
        "review_resolve_standards",
        "根据项目类型、交易结构、资产类型和设施清单解析适用及排除的标准需求；只输出适用性，不输出合规结论。",
        _schema(
            {
                "workspace_id": _SAFE_ID,
                "idempotency_key": _IDEMPOTENCY_KEY,
                "project_context": _PROJECT_CONTEXT,
                "facilities": _FACILITIES,
            },
            ["workspace_id", "project_context", "facilities"],
        ),
        service.resolve_standards,
        _output_schema(),
        write,
    )
    server.register_tool(
        "review_list_requirements",
        "列举标准适用性对象下的标准编号、版本、主题、适用设施、证明材料和当前证据状态。",
        _schema(
            {"workspace_id": _SAFE_ID, "standard_applicability_id": _ID},
            ["workspace_id", "standard_applicability_id"],
        ),
        service.list_standard_requirements,
        _output_schema(),
        read,
    )
    server.register_tool(
        "review_attach_requirement_evidence",
        "将当前工作区内已固化的 data-acquisition 或 data-analysis Resource 绑定到标准需求；不接受自由文本冒充证据。",
        _write_schema(
            {
                "standard_applicability_id": _ID,
                "requirement_id": _ID,
                "resource_uri": {
                    "type": "string",
                    "pattern": r"^lvke://(?:data-acquisition|data-analysis)/workspaces/",
                    "maxLength": 8192,
                },
                "locator": _STRING,
                "content_hash": _SHA256,
                "evidence_track": {
                    "type": "string",
                    "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption"],
                },
            },
            [
                "standard_applicability_id", "requirement_id", "resource_uri",
                "locator", "content_hash", "evidence_track",
            ],
        ),
        service.attach_requirement_evidence,
        _output_schema(),
        write,
    )
    server.register_tool(
        "review_validate_standards",
        "按标准需求汇总待补证、技术夹具满足和已附真实证据待专业复核状态；永远不返回项目已符合国家标准。",
        _schema(
            {"workspace_id": _SAFE_ID, "standard_applicability_id": _ID},
            ["workspace_id", "standard_applicability_id"],
        ),
        service.validate_standards,
        _output_schema(),
        read,
    )
    server.register_schema_resource(
        _REVIEW_TARGET_SCHEMA_URI,
        _TARGET,
        name="review-target",
        title="Review Target",
        description="统一交付审查目标的完整判别式 Schema。",
    )
    server.register_schema_resource(
        _REVIEW_FINDING_DISPOSITION_SCHEMA_URI,
        disposition_schema,
        name="review-finding-disposition",
        title="Review Finding Disposition",
        description="finding 确认、整改、申诉、豁免申请与关闭的完整处置 Schema。",
    )
    server.register_resource_provider(lambda: [], _resource)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
