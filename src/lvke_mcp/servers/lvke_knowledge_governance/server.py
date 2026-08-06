"""Official-SDK MCP server for immutable knowledge snapshots."""

from __future__ import annotations

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.runtime.schemas import make_tool_output_schema
from lvke_mcp.servers.lvke_knowledge_governance import service

SERVER_NAME = "lvke-knowledge-governance"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)

_STRING = {"type": "string", "minLength": 1, "maxLength": 4000}
_SAFE_ID = {
    "type": "string",
    "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
}
_KEY = {"type": "string", "minLength": 1, "maxLength": 200}
_HASH = {"type": "string", "pattern": r"^(?:sha256:)?[0-9a-fA-F]{64}$"}
_EVIDENCE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_type": {
            "type": "string",
            "enum": [
                "source_snapshot",
                "evidence_pack",
                "report_revision",
                "review_finding",
                "knowledge_snapshot",
                "technical_fixture",
                "source_reconstructed",
                "search_summary",
            ],
        },
        "resource_uri": {
            "type": "string",
            "pattern": r"^lvke://",
            "maxLength": 8192,
        },
        "content_hash": _HASH,
        "locator": _STRING,
        "evidence_track": {
            "type": "string",
            "enum": ["real", "source_reconstructed", "technical_fixture", "controlled_assumption"],
        },
        "note": {"type": "string", "maxLength": 1000},
    },
    "required": [
        "source_type",
        "resource_uri",
        "content_hash",
        "locator",
        "evidence_track",
    ],
}
_CANDIDATE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate_type": {
            "type": "string",
            "enum": [
                "accepted_revision",
                "high_quality_section",
                "review_experience",
                "procedure",
                "domain_rule",
            ],
        },
        "title": {"type": "string", "minLength": 1, "maxLength": 160},
        "content": {"type": "string", "minLength": 1, "maxLength": 4000},
        "layer": {
            "type": "string",
            "enum": ["semantic", "procedural", "episodic"],
        },
        "knowledge_type": {"type": "string", "minLength": 1, "maxLength": 128},
        "scope": {
            "type": "string",
            "enum": ["global", "industry", "project_type", "case", "workspace"],
        },
        "scope_key": {"type": "string", "maxLength": 512},
        "industry": {"type": "string", "maxLength": 256},
        "project_type": {"type": "string", "maxLength": 256},
        "section_id": {"type": "string", "maxLength": 160},
        "source_revision_id": {"type": "string", "maxLength": 256},
        "source_diff_hash": _HASH,
        "rubric_assessment_id": _SAFE_ID,
        "previous_rubric_assessment_id": _SAFE_ID,
        "tags": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 64},
            "maxItems": 24,
            "uniqueItems": True,
        },
        "evidence_bindings": {
            "type": "array",
            "items": _EVIDENCE,
            "minItems": 1,
            "maxItems": 20,
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "supersedes_memory_id": {"type": "string", "maxLength": 160},
        "conflict_key": {"type": "string", "maxLength": 160},
    },
    "required": [
        "candidate_type",
        "title",
        "content",
        "layer",
        "knowledge_type",
        "scope",
        "rubric_assessment_id",
        "evidence_bindings",
        "confidence",
    ],
}
_OUTPUT = make_tool_output_schema(
    {
        "candidate_id": {"type": "string"},
        "knowledge_snapshot_id": {"type": "string"},
        "knowledge_review_id": {"type": "string"},
        "knowledge_release_id": {"type": "string"},
        "candidate_status": {"type": "string"},
    },
    required=("resource_uris", "warnings", "blockers", "next_actions"),
)


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_id": _SAFE_ID,
            **properties,
        },
        "required": ["workspace_id", *required],
    }


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
        "knowledge_submit_candidate",
        "提交证据化知识候选；服务端验证不可变证据与 rubric 绑定后固化。",
        _schema(
            {"candidate": _CANDIDATE, "idempotency_key": _KEY},
            ["candidate", "idempotency_key"],
            
        ),
        service.submit_candidate,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "knowledge_list_candidates",
        "按状态、行业、章节和候选类型查询当前工作区知识候选。",
        _schema(
            {
                "candidate_status": {
                    "type": "string",
                    "enum": ["validated", "snapshotted", "accepted", "rejected", "needs_revision", "published"],
                },
                "industry": {"type": "string", "maxLength": 256},
                "section_id": {"type": "string", "maxLength": 160},
                "candidate_type": {
                    "type": "string",
                    "enum": [
                        "accepted_revision", "high_quality_section", "review_experience",
                        "procedure", "domain_rule",
                    ],
                },
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            [],
        ),
        service.list_candidates,
        _OUTPUT,
        read,
    )
    server.register_tool(
        "knowledge_get_candidate",
        "读取候选原文、来源 revision、rubric 变化和快照 lineage。",
        _schema({"candidate_id": _SAFE_ID}, ["candidate_id"]),
        service.get_candidate,
        _OUTPUT,
        read,
    )
    server.register_tool(
        "knowledge_create_snapshot",
        "从已验证候选生成内容寻址的不可变知识快照。",
        _schema(
            {"candidate_id": _SAFE_ID, "idempotency_key": _KEY},
            ["candidate_id", "idempotency_key"],
        ),
        service.create_snapshot,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "knowledge_review_candidate",
        "记录知识候选审核结论；accepted、rejected、needs_revision 均生成不可变审核快照。",
        _schema(
            {
                "candidate_id": _SAFE_ID,
                "decision": {"type": "string", "enum": ["accepted", "rejected", "needs_revision"]},
                "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
                "review_note": {"type": "string", "maxLength": 2000},
                "required_changes": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 30},
                "rubric_assessment_id": _SAFE_ID,
                "idempotency_key": _KEY,
            },
            ["candidate_id", "decision", "reason", "idempotency_key"],
        ),
        service.review_candidate,
        _OUTPUT,
        write,
    )
    server.register_tool(
        "knowledge_publish_release",
        "仅将 accepted 候选发布为不可变 KnowledgeRelease。",
        _schema(
            {
                "candidate_id": _SAFE_ID,
                "review_id": _SAFE_ID,
                "knowledge_review_id": _SAFE_ID,
                "release_note": {"type": "string", "maxLength": 2000},
                "idempotency_key": _KEY,
            },
            ["candidate_id", "review_id", "idempotency_key"],
        ),
        service.publish_release,
        _OUTPUT,
        write,
    )
    def read_standard_resource(uri: str):
        resolved = service.resolve_resource(uri)
        if resolved is None:
            return None
        content, mime_type = resolved
        return ReadResourceContents(content, mime_type)

    server.register_resource_provider(lambda: [], read_standard_resource)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
