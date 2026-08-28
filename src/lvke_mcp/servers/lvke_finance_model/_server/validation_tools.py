"""validate_post_generation / promote_to_formal 的 MCP 工具 handler。

Handler 遵循 _server 模块惯例：从 model_application 导入领域函数，
补充信封字段（resource_uris, warnings, blockers, next_actions）以满足
_output_schema 的 required 约束。
"""

from __future__ import annotations


def _run_uri(workspace_id: str, run_id: str | None) -> str | None:
    if not run_id:
        return None
    return f"lvke://finance-model/workspaces/{workspace_id}/runs/{run_id}"


def _tool_validate_post_generation(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import validate_post_generation

    result = validate_post_generation(
        workspace_id=args["workspace_id"],
        run_id=args["run_id"],
        spec=args.get("spec"),
        validation_scope=args.get("validation_scope", "technical"),
        finance_inputs=args.get("finance_inputs"),
        table_manifest=args.get("table_manifest"),
        report_sections=args.get("report_sections"),
    )
    # 补充信封字段（_output_schema required）
    result.setdefault("resource_uris", [u for u in [_run_uri(args["workspace_id"], args["run_id"])] if u])
    result.setdefault("next_actions", [])
    # 归一化 status：'unverified' 不在 _output_schema 的 enum 中，映射为 'partial'
    if result.get("status") == "unverified":
        result["status"] = "partial"
    return result


def _tool_promote_to_formal(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import promote_to_formal

    result = promote_to_formal(
        workspace_id=args["workspace_id"],
        prior_run_id=args["prior_run_id"],
        new_fin=args["new_fin"],
        validation_report=args.get("validation_report"),
        idempotency_key=args.get("idempotency_key", ""),
        model_version=args.get("model_version", ""),
        template_version=args.get("template_version", ""),
        input_hash=args.get("input_hash", ""),
        table_bundle_hash=args.get("table_bundle_hash", ""),
        agent_trace_id=args.get("agent_trace_id", ""),
        tool_call_id=args.get("tool_call_id", ""),
    )
    # 补充信封字段（_output_schema required）
    result.setdefault("resource_uris", [u for u in [_run_uri(args["workspace_id"], result.get("run_id"))] if u])
    result.setdefault("warnings", [])
    result.setdefault("blockers", [])
    result.setdefault("next_actions", [])
    return result