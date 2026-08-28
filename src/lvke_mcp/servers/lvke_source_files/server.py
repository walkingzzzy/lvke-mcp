"""Official-SDK MCP server for governed source files."""

from __future__ import annotations

from mcp import types

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.servers.lvke_source_files import service

SERVER_NAME = "lvke-source-files"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)

_STRING = {"type": "string", "minLength": 1}
_WS = {**_STRING, "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"}
_ID = {**_STRING, "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$"}
_KEY = {**_STRING, "maxLength": 200}
_SHA256 = {
    "type": "string",
    "pattern": r"^(?:sha256:)?[0-9a-fA-F]{64}$",
}
_MIME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 160,
    "description": "声明 MIME；仍须通过文件 magic-byte 与逻辑格式检查",
}
_OUTPUT = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "success": {"type": "boolean"},
        "business_success": {"type": "boolean"},
        "system_success": {"type": "boolean"},
        "transport_success": {"type": "boolean"},
        "status": {
            "type": "string",
            "enum": ["ok", "partial", "missing_inputs", "blocked", "failed", "upstream_failure"],
        },
        "resource_uris": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "success",
        "business_success",
        "system_success",
        "transport_success",
        "status",
        "resource_uris",
        "warnings",
        "blockers",
        "next_actions",
    ],
}


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_id": _WS,
            **properties,
        },
        "required": ["workspace_id", *required],
    }


def _paging_schema(extra: dict | None = None) -> dict:
    return _schema(
        {
            **(extra or {}),
            "cursor": {"type": "string", "default": ""},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
        [],
    )


_TASK_STATUS_BRANCHES = {
    "parse": ("source_parse_status", "job_id", r"^job_"),
    "upload": ("source_upload_status", "upload_id", r"^ups_"),
}


def _install_task_status_aggregate(
    server: OfficialStdioServer,
    annotations: types.ToolAnnotations,
) -> None:
    legacy = {
        name: server._tools[name]  # noqa: SLF001
        for name, _id_field, _prefix in _TASK_STATUS_BRANCHES.values()
    }
    server._round2_legacy_specs = legacy  # type: ignore[attr-defined]  # noqa: SLF001
    schema = _schema(
        {
            "task_kind": {"type": "string", "enum": list(_TASK_STATUS_BRANCHES)},
            "job_id": {**_ID, "pattern": r"^job_"},
            "upload_id": {**_ID, "pattern": r"^ups_"},
        },
        ["task_kind"],
    )

    def dispatch(args: dict) -> dict:
        legacy_name, id_field, _prefix = _TASK_STATUS_BRANCHES[str(args["task_kind"])]
        target_id = str(args.get(id_field) or "")
        other_id = "upload_id" if id_field == "job_id" else "job_id"
        if not target_id or args.get(other_id):
            return {
                "success": False, "business_success": False,
                "system_success": True, "transport_success": True,
                "status": "blocked", "code": "task_status_identifier_invalid",
                "message": f"task_kind={args['task_kind']} 必须且只能提供 {id_field}",
                "resource_uris": [], "warnings": [],
                "blockers": ["task_status_identifier_invalid"],
                "next_actions": [f"提供与 task_kind 匹配的 {id_field}"],
            }
        return legacy[legacy_name].handler(
            {"workspace_id": args["workspace_id"], id_field: target_id}
        )

    server.register_tool(
        "source_task_status",
        "按任务类型读取上传或解析状态；任务类型必须与对象 ID 命名空间一致。",
        schema,
        dispatch,
        _OUTPUT,
        annotations,
    )
    for name in legacy:
        server._tools.pop(name)  # noqa: SLF001


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
        "source_external_corpus_resolve",
        "按用户指令中的项目名称校验外部资料清单、marker 和路径边界，返回确定性财务路线与可导入资料根；不导入文件、不执行 OCR。",
        _schema(
            {"project_name": {**_STRING, "maxLength": 300}},
            ["project_name"],
        ),
        lambda a: service.resolve_external_corpus(a["project_name"]),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "source_import_content",
        "导入不超过 8 MiB 的 Base64 原始文件，并复用统一安全扫描、不可变版本与解析链。",
        _schema(
            {
                "original_filename": {**_STRING, "maxLength": 220},
                "declared_mime": _MIME,
                "content_base64": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 11_184_812,
                    "contentEncoding": "base64",
                },
                "expected_sha256": {**_SHA256, "default": ""},
                "parse_immediately": {"type": "boolean", "default": True},
                "evidence_policy": {
                    "type": "string",
                    "enum": [
                        "candidate",
                        "sim_a_formal",
                        "formal_evidence",
                        "source_reconstructed",
                        "controlled_assumption",
                        "technical_fixture",
                    ],
                    "default": "candidate",
                },
                "evidence_origin": {"type": "string", "maxLength": 80, "default": ""},
                "project_fact_certified": {"type": "boolean", "default": False},
                "idempotency_key": _KEY,
            },
            ["original_filename", "declared_mime", "content_base64", "idempotency_key"],
            
        ),
        lambda a: service.import_content(
            a["workspace_id"],
            original_filename=a["original_filename"],
            declared_mime=a["declared_mime"],
            content_base64=a["content_base64"],
            expected_sha256=a.get("expected_sha256", ""),
            parse_immediately=bool(a.get("parse_immediately", True)),
            evidence_policy=str(a.get("evidence_policy") or ""),
            evidence_origin=str(a.get("evidence_origin") or ""),
            project_fact_certified=bool(a.get("project_fact_certified")),
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "source_import_local_path",
        "仅在 stdio/local transport 下从 LVKE_SOURCE_IMPORT_ROOTS，或 LVKE_EXTERNAL_CORPUS_ROOT 受控清单解析的允许目录导入普通单链接文件。",
        _schema(
            {
                "local_path": _STRING,
                "original_filename": {**_STRING, "maxLength": 220},
                "declared_mime": _MIME,
                "expected_sha256": {**_SHA256, "default": ""},
                "parse_immediately": {"type": "boolean", "default": True},
                "idempotency_key": _KEY,
            },
            ["local_path", "original_filename", "declared_mime", "idempotency_key"],
            
        ),
        lambda a: service.import_local_path(
            a["workspace_id"],
            local_path=a["local_path"],
            original_filename=a["original_filename"],
            declared_mime=a["declared_mime"],
            expected_sha256=a.get("expected_sha256", ""),
            parse_immediately=bool(a.get("parse_immediately", True)),
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "source_upload_begin",
        "创建 24 小时有效、工作区内可恢复的分块上传会话。",
        _schema(
            {
                "original_filename": {**_STRING, "maxLength": 220},
                "declared_mime": _MIME,
                "total_size": {"type": "integer", "minimum": 1, "maximum": 134_217_728},
                "expected_sha256": _SHA256,
                "idempotency_key": _KEY,
            },
            ["original_filename", "declared_mime", "total_size", "expected_sha256", "idempotency_key"],
            
        ),
        lambda a: service.upload_begin(
            a["workspace_id"],
            original_filename=a["original_filename"],
            declared_mime=a["declared_mime"],
            total_size=int(a["total_size"]),
            expected_sha256=a["expected_sha256"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "source_upload_chunk",
        "按字节偏移上传不超过 4 MiB 的 Base64 分块；重叠范围和幂等冲突会被拒绝。",
        _schema(
            {
                "upload_id": _ID,
                "offset_bytes": {"type": "integer", "minimum": 0},
                "content_base64": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 5_592_408,
                    "contentEncoding": "base64",
                },
                "idempotency_key": _KEY,
            },
            ["upload_id", "offset_bytes", "content_base64", "idempotency_key"],
            
        ),
        lambda a: service.upload_chunk(
            a["workspace_id"],
            a["upload_id"],
            offset_bytes=int(a["offset_bytes"]),
            content_base64=a["content_base64"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "source_upload_commit",
        "校验分块连续性、总大小和完整 SHA-256 后提交统一安全扫描与解析链。",
        _schema(
            {
                "upload_id": _ID,
                "parse_immediately": {"type": "boolean", "default": True},
                "idempotency_key": _KEY,
            },
            ["upload_id", "idempotency_key"],
            
        ),
        lambda a: service.upload_commit(
            a["workspace_id"],
            a["upload_id"],
            idempotency_key=a["idempotency_key"],
            parse_immediately=bool(a.get("parse_immediately", True)),
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "source_upload_abort",
        "中止未提交的分块上传并清除暂存分块。",
        _schema(
            {"upload_id": _ID, "idempotency_key": _KEY},
            ["upload_id", "idempotency_key"],
            
        ),
        lambda a: service.upload_abort(
            a["workspace_id"],
            a["upload_id"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "source_upload_status",
        "读取工作区内分块上传进度、过期状态和提交结果。",
        _schema({"upload_id": _ID}, ["upload_id"]),
        lambda a: service.upload_status(
            a["workspace_id"],
            a["upload_id"],
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "source_file_list",
        "分页列出当前工作区的不可变原始资料记录。",
        _paging_schema(),
        lambda a: service.list_source_files(
            a["workspace_id"],
            cursor=a.get("cursor", ""),
            limit=int(a.get("limit", 50)),
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "source_file_get",
        "读取原始资料元数据、安全扫描和当前解析投影，不暴露服务器绝对路径。",
        _schema({"file_id": _ID}, ["file_id"]),
        lambda a: service.get_source_file(
            a["workspace_id"], a["file_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "source_parse_status",
        "读取解析任务状态；partial、failed 与正式证据资格分开表达。",
        _schema({"job_id": _ID}, ["job_id"]),
        lambda a: service.parse_status(
            a["workspace_id"], a["job_id"]
        ),
        _OUTPUT,
        read,
    )
    server.register_tool(
        "source_parse_retry",
        "为 failed/partial/cancelled 解析创建新的不可变尝试，旧尝试保持可读。",
        _schema(
            {
                "job_id": _ID,
                "parse_immediately": {"type": "boolean", "default": True},
                "idempotency_key": _KEY,
            },
            ["job_id", "idempotency_key"],
            
        ),
        lambda a: service.parse_retry(
            a["workspace_id"],
            a["job_id"],
            idempotency_key=a["idempotency_key"],
            parse_immediately=bool(a.get("parse_immediately", True)),
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "source_parse_cancel",
        "取消 queued/running 解析；不删除已固化原件或历史解析尝试。",
        _schema(
            {"job_id": _ID, "idempotency_key": _KEY},
            ["job_id", "idempotency_key"],
            
        ),
        lambda a: service.parse_cancel(
            a["workspace_id"],
            a["job_id"],
            idempotency_key=a["idempotency_key"],
        ),
        _OUTPUT,
        write,
    )
    server.register_tool(
        "source_inspect_workbook",
        "对已导入且完整性校验通过的工作簿执行 sheet、单元格、公式、跨表引用或依赖树检查。",
        _schema(
            {
                "file_id": _ID,
                "operation": {
                    "type": "string",
                    "enum": [
                        "list_sheets", "read_cells", "read_formulas",
                        "cross_sheet_refs", "dependency_tree",
                    ],
                },
                "sheet": _STRING,
                "range": {
                    "type": "string",
                    "pattern": r"^\$?[A-Za-z]{1,3}\$?[1-9][0-9]*(?::\$?[A-Za-z]{1,3}\$?[1-9][0-9]*)?$",
                    "description": "read_cells/read_formulas 的 A1:C20 范围，或 dependency_tree 的单个根单元格。",
                },
                "options": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "max_rows": {"type": "integer", "minimum": 1, "maximum": 10000},
                        "max_cols": {"type": "integer", "minimum": 1, "maximum": 1000},
                        "max_depth": {"type": "integer", "minimum": 1, "maximum": 64},
                    },
                    "default": {},
                },
            },
            ["file_id", "operation"],
        ),
        lambda a: service.inspect_workbook(
            a["workspace_id"], a["file_id"], a["operation"],
            sheet=a.get("sheet", ""), range_ref=a.get("range", ""),
            options=a.get("options", {}),
        ),
        _OUTPUT,
        read,
    )
    _install_task_status_aggregate(server, read)
    server.register_resource_provider(lambda: [], lambda _uri: None)
    return server


SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()
