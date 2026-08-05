"""lvke-finance-model MCP server 入口(stdio)。

工作区级完整财务模型工具（与 finance-calc 低层计算器分离）：

- finance_prepare_spec
- finance_validate_spec
- finance_run_model
- finance_get_run
- finance_build_balance_sheet / finance_get_balance_sheet
- finance_run_monte_carlo / finance_get_monte_carlo
- finance_list_analyses / finance_read_analysis_resource
- finance_render_tables（DEPRECATED → lvke-finance-tables.tables_render）
- finance_generate_package（DEPRECATED → lvke-finance-authoring 编排 run → tables）
- finance_import_vendor_review

启动方式::

    python -m lvke_mcp.servers.lvke_finance_model.server

契约约定（方案 5.4）：每个工具有专属 outputSchema，公共 envelope 至少含
``status/resource_uris/warnings/blockers/next_actions``；同时保留
``success/data/source``（成功）与 ``code/message``（失败）以兼容既有调用方。
业务缺项返回 ``status=missing_inputs``，阻断返回 ``status=blocked``，
均不与系统错误（``status=failed``）混淆。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents

from lvke_mcp.runtime.storage import (
    paginate_resource_entries,
    sha256_json,
)
from lvke_mcp.adapters.finance_model_repository import (
    BALANCE_SHEET_STORE,
    BASIS_OF_ESTIMATE_STORE,
    FACT_PACK_STORE,
    IDEMPOTENCY_STORE,
    MONTE_CARLO_STORE,
    SPEC_STORE,
)
from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.runtime.responses import err, ok
from lvke_mcp.domains.finance.parameter_resolver import (
    finance_input_schema,
    finance_spec_candidate_schema,
)
from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
from lvke_mcp.runtime.source_reconstruction import reconstruction_errors, normalize_reconstruction

SERVER_NAME = "lvke-finance-model"
SERVER_VERSION = "0.3.0"
logger = get_logger(SERVER_NAME)

_BOE_ENTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target_pointer": {"type": "string", "pattern": r"^/(?:spec|input_revision)/"},
        "value": {},
        "unit": {"type": "string", "minLength": 1},
        "period": {"type": "string", "minLength": 1},
        "source_type": {
            "type": "string",
            "enum": [
                "evidence_pack",
                "market_sizing_case",
                "build_scale_case",
                "revenue_driver_set",
                "cost_driver_set",
                "labor_plan",
                "source_reconstructed",
                "technical_fixture",
                "controlled_assumption",
            ],
        },
        "source_object_id": {"type": "string", "minLength": 1},
        "method": {"type": "string", "minLength": 1},
        "selection_reason": {"type": "string", "minLength": 10},
        "uncertainty": {"type": "string"},
        "candidate_values": {"type": "array", "items": {}},
        "rejected_values": {"type": "array", "items": {}},
        "locator": {"type": "string", "minLength": 1},
        "content_hash": {
            "type": "string",
            "pattern": r"^(?:sha256:)?[0-9a-fA-F]{64}$",
        },
        "evidence_eligibility": {
            "type": "string",
            "enum": ["formal_evidence", "source_reconstructed", "technical_fixture", "controlled_assumption"],
        },
        "reconstruction": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "reconstruction_id": {"type": "string", "minLength": 1},
                "source_uri": {"type": "string", "pattern": r"^lvke://.+"},
                "content_hash": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
                "locator": {"type": "string", "minLength": 1},
                "source_kind": {"type": "string", "enum": ["client_report", "finance_template", "historical_statement", "scenario_note"]},
                "method": {"type": "string", "enum": ["table_extract", "formula_replay", "explicit_mapping"]},
                "original_formula_available": {"type": "boolean"},
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["reconstruction_id", "source_uri", "content_hash", "locator", "source_kind", "method", "original_formula_available", "limitations"],
        },
        "reconstruction_record": {
            "type": "object",
            "additionalProperties": True,
            "description": "reconstruction 的兼容别名",
        },
    },
    "required": [
        "target_pointer",
        "value",
        "unit",
        "period",
        "source_type",
        "source_object_id",
        "method",
        "selection_reason",
        "locator",
        "content_hash",
        "evidence_eligibility",
    ],
}

_DISTRIBUTION_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "field": {
                    "type": "string",
                    "enum": ["revenue_scale", "operating_cost_scale", "construction_scale"],
                },
                "distribution": {"const": "uniform"},
                "low": {"type": "number", "exclusiveMinimum": 0},
                "high": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["field", "distribution", "low", "high"],
        },
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "field": {
                    "type": "string",
                    "enum": ["revenue_scale", "operating_cost_scale", "construction_scale"],
                },
                "distribution": {"const": "triangular"},
                "low": {"type": "number", "exclusiveMinimum": 0},
                "mode": {"type": "number", "exclusiveMinimum": 0},
                "high": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["field", "distribution", "low", "mode", "high"],
        },
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "field": {
                    "type": "string",
                    "enum": ["revenue_scale", "operating_cost_scale", "construction_scale"],
                },
                "distribution": {"const": "normal"},
                "mean": {"type": "number", "exclusiveMinimum": 0},
                "stddev": {"type": "number", "exclusiveMinimum": 0},
                "low": {"type": "number", "exclusiveMinimum": 0},
                "high": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["field", "distribution", "mean", "stddev", "low", "high"],
        },
    ]
}

_STATUS_VALUES = ["ok", "partial", "missing_inputs", "blocked", "failed"]

# 兼容期迁移提示（方案 8.4：旧工具在一个兼容周期内返回 deprecation 信息）
_DEPRECATED_RENDER_HINT = (
    "deprecated：finance_render_tables 将移除，请迁移到 "
    "lvke-finance-tables.tables_render（同一 run_id 渲染，不重算）"
)
_DEPRECATED_PACKAGE_HINT = (
    "deprecated：finance_generate_package 将移除，请由 lvke-finance-authoring "
    "Skill 显式编排 finance_run_model → lvke-finance-tables.tables_render"
)


def _idempotency_ttl_seconds() -> int:
    try:
        return max(60, min(int(os.getenv("LVKE_MCP_IDEMPOTENCY_TTL_SECONDS", "86400")), 604800))
    except ValueError:
        return 86400


def _active_idempotency_record(
    workspace_id: str,
    key_hash: str,
    *,
    operation: str = "finance_confirm_spec",
) -> dict | None:
    records = sorted(
        IDEMPOTENCY_STORE.list(workspace_id),
        key=lambda record: str(record.get("created_at") or ""),
        reverse=True,
    )
    now = datetime.now(timezone.utc)
    for record in records:
        payload = record.get("payload") or {}
        if payload.get("operation") != operation or payload.get("key_hash") != key_hash:
            continue
        try:
            expires_at = datetime.fromisoformat(str(payload.get("expires_at") or ""))
        except ValueError:
            continue
        if expires_at > now:
            return record
    return None


def _run_uri(workspace_id: str, run_id: str | None) -> str | None:
    if not run_id:
        return None
    return f"lvke://finance-model/workspaces/{workspace_id}/runs/{run_id}"


def _spec_uri(
    workspace_id: str,
    spec_id: str | None,
) -> str | None:
    if not spec_id:
        return None
    return SPEC_STORE.uri(workspace_id, spec_id)


def _finalize(
    payload: dict,
    *,
    status: str,
    resource_uris: list | tuple = (),
    warnings: list | tuple = (),
    blockers: list | tuple = (),
    next_actions: list | tuple = (),
    deprecated: bool = False,
    **extra,
) -> dict:
    """把 ok/err 载荷补齐为方案 5.4 envelope；工具特有字段经 extra 平铺。"""
    payload["status"] = status
    payload["resource_uris"] = [str(u) for u in resource_uris if u]
    payload["warnings"] = [str(w) for w in warnings if w]
    payload["blockers"] = [str(b) for b in blockers if b]
    payload["next_actions"] = [str(n) for n in next_actions if n]
    if deprecated:
        payload["deprecated"] = True
    payload.update(extra)
    return payload


def _ok_env(data, *, source: str, status: str, **kw) -> dict:
    payload = _finalize(ok(data, source=source), status=status, **kw)
    if status in {"partial", "missing_inputs", "blocked", "failed"}:
        raw_code = (
            data.get("error") or data.get("reason") or status
            if isinstance(data, dict)
            else status
        )
        raw_message = (
            data.get("message") or raw_code
            if isinstance(data, dict)
            else raw_code
        )
        payload.update({
            "success": False,
            "transport_success": True,
            "business_success": False,
            "completed": False,
            "outcome": status,
            "code": f"{SERVER_NAME}.{raw_code}",
            "message": str(raw_message),
        })
    return payload


def _err_env(
    code: str,
    message: str,
    *,
    detail=None,
    status: str = "failed",
    trace_id: str | None = None,
    **kw,
) -> dict:
    # The finance output schemas require tool-specific success fields.  Keep a
    # regular error envelope for blocked business outcomes; the MCP runtime
    # maps ``status=blocked`` to ``isError=false`` for clients.
    # Public MCP responses never expose Python exception classes, paths, or
    # exception text. Call sites log full diagnostics before reaching here.
    payload = err(code, message, trace_id=trace_id)
    env = _finalize(payload, status=status, **kw)
    if not env["blockers"]:
        env["blockers"] = [message]
    return env


def _exception_env(
    log_message: str,
    code: str,
    message: str,
    *,
    status: str = "failed",
    **kw,
) -> dict:
    """Log full diagnostics while returning only a correlated safe envelope."""

    trace_id = f"mcp_{uuid.uuid4().hex}"
    logger.exception("%s trace_id=%s", log_message, trace_id)
    return _err_env(code, message, status=status, trace_id=trace_id, **kw)


def _revenue_input_complete(spec: dict[str, Any] | None, input_revision: dict[str, Any] | None) -> bool:
    """Require an auditable revenue driver before persisting a FinanceRun."""

    revision = input_revision if isinstance(input_revision, dict) else {}
    annual = revision.get("annual_revenue_wan")
    if isinstance(annual, (int, float)) and annual > 0:
        return True
    candidate = spec if isinstance(spec, dict) else {}
    revenue = candidate.get("revenue")
    if not isinstance(revenue, dict):
        revenue = candidate.get("finance_inputs", {}).get("revenue") if isinstance(candidate.get("finance_inputs"), dict) else None
    if not isinstance(revenue, dict):
        return False
    model = str(revenue.get("model") or "")
    if model == "product_sales":
        products = revenue.get("products")
        return isinstance(products, list) and bool(products) and all(
            isinstance(item, dict)
            and float(item.get("capacity") or 0) > 0
            and float(item.get("price_per_unit") or 0) > 0
            for item in products
        )
    if model == "property_sales":
        return float(revenue.get("saleable_area") or 0) > 0 and float(revenue.get("price_per_sqm") or 0) > 0
    if model == "tourism":
        visitors = float(revenue.get("annual_visitors") or 0)
        spend = max(
            float(revenue.get("spend_per_visitor") or 0),
            float(revenue.get("ticket_price_yuan") or 0) + float(revenue.get("secondary_spend_yuan") or 0),
        )
        return visitors > 0 and spend > 0
    series = revision.get("revenue_by_year")
    return isinstance(series, list) and any(isinstance(value, (int, float)) and value > 0 for value in series)


def _output_schema(
    tool_properties: dict | None = None,
    *,
    success_required: list[str] | tuple[str, ...] = (),
    deprecated: bool = False,
) -> dict:
    """构造单工具专属输出契约。

    - envelope 字段全响应必含（成功与失败路径一致）；
    - 使用平坦对象 schema，避免 MCP 客户端把条件交叉类型显示为
      ``unknown & unknown``；
    - 成功/失败的领域字段由处理器和契约测试校验，不在公开 schema 中使用
      ``allOf/if/then``；
    - DEPRECATED 工具全响应必含 ``deprecated: true``。
    """
    props: dict = {
        "success": {"type": "boolean"},
        "status": {"type": "string", "enum": list(_STATUS_VALUES)},
        "resource_uris": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
        "data": {},
        "source": {"type": "string"},
        "code": {"type": "string"},
        "message": {"type": "string"},
    }
    required = ["success", "status", "resource_uris", "warnings", "blockers", "next_actions"]
    if deprecated:
        props["deprecated"] = {"const": True}
        required.append("deprecated")
    if tool_properties:
        props.update(tool_properties)
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": props,
        "required": required,
    }


def _ws(args: dict) -> str | None:
    wsid = args.get("workspace_id") or args.get("doc_id") or args.get("project_id")
    if not isinstance(wsid, str) or not wsid.strip():
        return None
    return wsid.strip()


def _str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _unique_strings(value) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in _str_list(value):
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _blocking_rules(data: dict) -> list[str]:
    """只取 blocking_issues 的 rule 名，避免 detail 携带内部路径/堆栈。"""
    rules: list[str] = []
    for issue in data.get("blocking_issues") or []:
        if isinstance(issue, dict) and issue.get("rule"):
            rules.append(str(issue["rule"]))
    return rules


def _canonical_candidate_inputs(
    supplied_spec: dict[str, Any] | None,
    explicit_revision: dict[str, Any] | None,
    workspace_revision: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge candidate finance inputs with one canonical resolver contract."""

    from lvke_mcp.domains.finance.parameter_resolver import (
        canonicalize_finance_inputs,
    )

    spec_inputs: dict[str, Any] = {}
    if isinstance(supplied_spec, dict):
        nested = supplied_spec.get("finance_inputs")
        if isinstance(nested, dict):
            spec_inputs.update(nested)
        candidate_input_fields = set(finance_input_schema().get("properties") or {})
        for key in candidate_input_fields:
            if key in supplied_spec:
                if key in spec_inputs and spec_inputs[key] != supplied_spec[key]:
                    return {}, [], [{
                        "input": key,
                        "reason": "candidate_input_conflict",
                        "path": f"/spec/{key}",
                        "conflicts_with": f"/spec/finance_inputs/{key}",
                    }]
                spec_inputs[key] = supplied_spec[key]

    merged = dict(workspace_revision or {})
    adoption: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    normalized_sources: list[tuple[str, dict[str, Any]]] = []
    for source_name, values in (("candidate_spec", spec_inputs), ("explicit_input_revision", explicit_revision or {})):
        normalized, ledger, errors = canonicalize_finance_inputs(values)
        adoption.extend({**item, "source": source_name} for item in ledger)
        rejected.extend({**item, "source": source_name} for item in errors)
        normalized_sources.append((source_name, normalized))
    candidate_values = normalized_sources[0][1]
    explicit_values = normalized_sources[1][1]
    for key in sorted(set(candidate_values) & set(explicit_values)):
        if candidate_values[key] != explicit_values[key]:
            rejected.append({
                "input": key,
                "reason": "candidate_input_conflict",
                "source": "candidate_spec_vs_explicit_input_revision",
                "path": f"/input_revision/{key}",
                "conflicts_with": f"/spec/finance_inputs/{key}",
            })
    for _source_name, normalized in normalized_sources:
        merged.update(normalized)
    normalized, workspace_ledger, workspace_errors = canonicalize_finance_inputs(merged)
    adoption.extend({**item, "source": "effective"} for item in workspace_ledger)
    rejected.extend({**item, "source": "effective"} for item in workspace_errors)
    return normalized, adoption, rejected


def _tool_prepare_spec(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import prepare_spec

    return prepare_spec(args)


def _tool_prepare_fact_pack(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import prepare_fact_pack

    return prepare_fact_pack(args)


def _tool_confirm_fact_pack(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import confirm_fact_pack

    return confirm_fact_pack(args)


def _tool_get_fact_pack(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import get_fact_pack

    return get_fact_pack(args)


def _legacy_tool_prepare_spec(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    evidence_ids = _str_list(args.get("evidence_pack_ids"))
    evidence_records = []
    for evidence_id in evidence_ids:
        record = EVIDENCE_STORE.get(wsid, evidence_id)
        if record is None:
            return _err_env(
                f"{SERVER_NAME}.evidence_pack_not_found",
                f"未找到 evidence pack：{evidence_id}",
                status="blocked",
                blockers=[f"evidence_pack_not_found:{evidence_id}"],
            )
        evidence_records.append(record)
    try:
        from lvke_mcp.domains.finance import run_service

        supplied_spec = args.get("spec") if isinstance(args.get("spec"), dict) else None
        if supplied_spec is not None:
            supplied_spec = dict(supplied_spec)
            revenue = supplied_spec.get("revenue")
            if isinstance(revenue, dict) and str(revenue.get("model") or "") == "tourism":
                from lvke_mcp.domains.finance.revenue_models import normalize_tourism_revenue

                normalized_revenue, revenue_errors = normalize_tourism_revenue(revenue)
                if revenue_errors:
                    return _ok_env(
                        {"available": False, "missing_inputs": []},
                        source=f"{SERVER_NAME}.finance_prepare_spec",
                        status="blocked",
                        blockers=["revenue_component_conflict"],
                        field_errors=revenue_errors,
                        next_actions=["修正文旅收入组件与兼容别名冲突后重试"],
                    )
                supplied_spec["revenue"] = normalized_revenue
        data = run_service.prepare_workspace_finance_spec(
            wsid,
            strategy=str(args.get("strategy") or "propose_from_project"),
            force_refresh=bool(args.get("force_refresh") or False),
            # The MCP boundary is invoked by an Agent already capable of
            # reasoning over supplied evidence.  Do not make a second LLM
            # gateway a hidden dependency of FinanceSpec preparation.
            force_flat=bool(args.get("force_flat", supplied_spec is None)),
        )
        if supplied_spec is not None:
            data["spec"] = supplied_spec
            data["spec_hash"] = run_service.compute_spec_hash(supplied_spec)
            data["force_flat"] = False
        normalized_inputs, adoption, rejected = _canonical_candidate_inputs(
            supplied_spec,
            args.get("input_revision") if isinstance(args.get("input_revision"), dict) else None,
            data.get("input_revision") if isinstance(data.get("input_revision"), dict) else {},
        )
        if rejected:
            field_errors = [
                {
                    "path": str(item.get("path") or f"/input_revision/{item.get('input') or 'unknown'}"),
                    "code": str(item.get("reason") or "candidate_input_invalid"),
                    "input": item.get("input"),
                    **(
                        {"conflicts_with": item.get("conflicts_with")}
                        if item.get("conflicts_with")
                        else {}
                    ),
                }
                for item in rejected
            ]
            return _ok_env(
                {
                    "available": False,
                    "missing_inputs": [],
                    "input_rejections": rejected,
                    "input_adoption_ledger": adoption,
                },
                source=f"{SERVER_NAME}.finance_prepare_spec",
                status="blocked",
                blockers=["candidate_input_invalid"],
                field_errors=field_errors,
                next_actions=["修正未知、冲突或非法的 input_revision 字段后重试"],
            )
        data["input_revision"] = normalized_inputs
        data["input_adoption_ledger"] = adoption
        compute_hash = getattr(run_service, "compute_input_hash", None)
        data["input_hash"] = (
            compute_hash(
                normalized_inputs,
                invest_type=str(data.get("invest_type") or normalized_inputs.get("invest_type") or ""),
                build_period_months=data.get("build_period_months") or normalized_inputs.get("build_period_months"),
                industry=str(data.get("industry") or normalized_inputs.get("industry") or ""),
            )
            if callable(compute_hash)
            else sha256_json(normalized_inputs)
        )
        data["missing_inputs"] = (
            [] if normalized_inputs.get("total_investment_wan") else ["total_investment_wan"]
        )
        missing = _str_list(data.get("missing_inputs"))
        spec = data.get("spec") if isinstance(data.get("spec"), dict) else None
        if not _revenue_input_complete(spec if isinstance(spec, dict) else supplied_spec, normalized_inputs):
            missing.append("annual_revenue_wan_or_revenue_driver")
        assumptions = _str_list(data.get("assumptions_to_confirm"))
        if spec is None and "finance_spec" not in missing:
            missing.append("finance_spec")
        spec_record = None
        evidence_binding_hash = sha256_json({
            "evidence_pack_ids": evidence_ids,
            "evidence_basis_hashes": [record.get("basis_hash") for record in evidence_records],
        })
        if spec is not None:
            spec_record = SPEC_STORE.put(
                wsid,
                {
                    "spec": spec,
                    "spec_hash": data.get("spec_hash"),
                    "input_revision": data.get("input_revision") or {},
                    "input_hash": data.get("input_hash"),
                    "input_revision_id": data.get("input_revision_id"),
                    "confirmation_status": "candidate",
                    "evidence_pack_ids": evidence_ids,
                    "evidence_binding_hash": evidence_binding_hash,
                },
                producer=f"{SERVER_NAME}.finance_prepare_spec",
                status="missing_inputs" if missing else "ok",
                source_ids=evidence_ids,
                basis={
                    "spec_hash": data.get("spec_hash"),
                    "input_hash": data.get("input_hash"),
                    "evidence_binding_hash": evidence_binding_hash,
                },
            )
            data["spec_id"] = spec_record["object_id"]
            data["evidence_binding_hash"] = evidence_binding_hash
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_prepare_spec",
            status="missing_inputs" if missing else "ok",
            warnings=_str_list(data.get("warnings")),
            blockers=[f"缺少关键输入：{item}" for item in missing],
            next_actions=(
                ([
                    "提供候选 spec 后重新调用 finance_prepare_spec；"
                    "缺少可审计收入驱动时不得创建 FinanceRun"
                ] if "finance_spec" in missing else ["补齐缺失输入后重新调用 finance_prepare_spec"])
                if missing
                else ["调用 finance_confirm_spec 确认候选 Spec，再调用 finance_run_model"]
            ),
            resource_uris=[spec_record["resource_uri"]] if spec_record else [],
            spec_id=spec_record["object_id"] if spec_record else None,
            spec_hash=data.get("spec_hash"),
            evidence_binding_hash=evidence_binding_hash,
            missing_inputs=missing,
            assumptions_to_confirm=assumptions,
            input_hash=data.get("input_hash"),
            input_revision_id=data.get("input_revision_id"),
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_prepare_spec failed",
            f"{SERVER_NAME}.prepare_failed",
            "准备 FinanceSpec 失败",
        )



def _tool_confirm_spec(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import confirm_spec

    return confirm_spec(args)


def _legacy_tool_confirm_spec(args: dict) -> dict:
    wsid = _ws(args)
    spec_id = str(args.get("spec_id") or "").strip()
    if not wsid or not spec_id:
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "workspace_id 与 spec_id 必填",
        )
    source = SPEC_STORE.get(wsid, spec_id, )
    if source is None:
        return _err_env(f"{SERVER_NAME}.spec_not_found", "未找到候选 FinanceSpec", status="blocked")
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else None
    if spec is None:
        return _err_env(f"{SERVER_NAME}.spec_invalid", "候选 FinanceSpec 快照无效", status="blocked")
    input_revision = payload.get("input_revision") if isinstance(payload.get("input_revision"), dict) else {}
    missing_inputs = [] if input_revision.get("total_investment_wan") else ["total_investment_wan"]
    if not _revenue_input_complete(spec, input_revision):
        missing_inputs.append("annual_revenue_wan_or_revenue_driver")
    try:
        from lvke_mcp.domains.finance.spec import mark_spec_confirmed, validate_for_formal

        formal_candidate = mark_spec_confirmed(spec)
        formal_ok, formal_errors = validate_for_formal(formal_candidate)
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_confirm_spec validation failed",
            f"{SERVER_NAME}.confirm_failed",
            "确认 FinanceSpec 失败",
        )
    if missing_inputs or not formal_ok:
        blockers = [*(f"missing_input:{item}" for item in missing_inputs), *formal_errors]
        return _ok_env(
            {
                "spec_id": spec_id,
                "valid": False,
                "missing_inputs": missing_inputs,
                "validation_errors": formal_errors,
            },
            source=f"{SERVER_NAME}.finance_confirm_spec",
            status="blocked",
            blockers=blockers,
            next_actions=["修正候选 Spec 或补齐输入后重新 prepare，再确认新候选"],
        )
    note = str(args.get("note") or "")
    idempotency_key = str(args.get("idempotency_key") or "").strip()
    content_fingerprint = sha256_json({
        "spec_id": spec_id,
        "spec_content_hash": source.get("content_hash"),
        "note": note,
    })
    key_hash = "sha256:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    prior = _active_idempotency_record(wsid, key_hash)
    if prior is not None:
        saved = prior.get("payload") or {}
        if saved.get("content_fingerprint") != content_fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已绑定不同 FinanceSpec 确认请求",
                status="blocked",
                content_fingerprint=content_fingerprint,
                replayed=False,
                reused=False,
                idempotency_expires_at=saved.get("expires_at"),
            )
        replay = dict(saved.get("result") or {})
        replay.update({
            "content_fingerprint": content_fingerprint,
            "replayed": True,
            "reused": True,
            "idempotency_expires_at": saved.get("expires_at"),
        })
        return replay
    try:
        from lvke_mcp.domains.finance.run_service import compute_spec_hash

        confirmed = formal_candidate
        record = SPEC_STORE.put(
            wsid,
            {
                **payload,
                "spec": confirmed,
                "spec_hash": compute_spec_hash(confirmed),
                "confirmation_status": "confirmed",
                "parent_spec_id": spec_id,
                "confirmation": {"note": note},
            },
            producer=f"{SERVER_NAME}.finance_confirm_spec",
            status="ok",
            source_ids=[spec_id, *_str_list(payload.get("evidence_pack_ids"))],
            basis={
                "parent_spec_id": spec_id,
                "spec_hash": compute_spec_hash(confirmed),
                "note": note,
                "idempotency_key_hash": key_hash,
            },
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_confirm_spec failed",
            f"{SERVER_NAME}.confirm_failed",
            "确认 FinanceSpec 失败",
        )
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=_idempotency_ttl_seconds())
    ).isoformat()
    result = _ok_env(
        {"spec_id": record["object_id"], "parent_spec_id": spec_id, "spec_hash": record["payload"]["spec_hash"]},
        source=f"{SERVER_NAME}.finance_confirm_spec",
        status="ok",
        resource_uris=[record["resource_uri"]],
        next_actions=["调用 finance_run_model，传入已确认 spec_id"],
        spec_id=record["object_id"],
        spec_hash=record["payload"]["spec_hash"],
        content_fingerprint=content_fingerprint,
        replayed=False,
        reused=False,
        idempotency_expires_at=expires_at,
    )
    IDEMPOTENCY_STORE.put(
        wsid,
        {
            "operation": "finance_confirm_spec",
            "key_hash": key_hash,
            "content_fingerprint": content_fingerprint,
            "expires_at": expires_at,
            "result": result,
        },
        producer=f"{SERVER_NAME}.finance_confirm_spec",
        source_ids=[record["object_id"]],
        basis={
            "operation": "finance_confirm_spec",
            "key_hash": key_hash,
            "content_fingerprint": content_fingerprint,
        },
    )
    return result


def _tool_run_model(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import run_model

    return run_model(args)


def _legacy_tool_run_model(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    mode = str(args.get("mode") or "estimate_preview")
    if mode not in {"estimate_preview", "review_candidate"}:
        mode = "estimate_preview"
    idempotency_key = str(args.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return _err_env(
            f"{SERVER_NAME}.idempotency_key_required",
            "finance_run_model 写操作必须提供 idempotency_key",
            status="blocked",
            run_id=None,
            missing_inputs=[],
        )
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    request_basis = {
        key: value
        for key, value in args.items()
        if key not in {"idempotency_key", "agent_trace_id", "tool_call_id"}
    }
    request_basis["mode"] = mode
    request_fingerprint = sha256_json(request_basis)
    idempotency_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=_idempotency_ttl_seconds())
    ).isoformat()
    reservation_created = False
    prior = _active_idempotency_record(
        wsid,
        key_hash,
        operation="finance_run_model",
    )
    if prior is not None:
        prior_payload = prior.get("payload") or {}
        if prior_payload.get("content_fingerprint") != request_fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已用于不同的财务运行请求",
                status="blocked",
                blockers=["idempotency_conflict"],
                next_actions=["使用新的 idempotency_key 提交变更后的财务请求"],
                run_id=None,
                original_run_id=prior_payload.get("run_id"),
                missing_inputs=[],
                replayed=False,
            )
        if prior_payload.get("in_progress") is True:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                time.sleep(0.05)
                latest = _active_idempotency_record(
                    wsid, key_hash, operation="finance_run_model"
                )
                latest_payload = (latest or {}).get("payload") or {}
                if latest is not None and latest_payload.get("in_progress") is not True:
                    replay = json.loads(json.dumps(latest_payload.get("result") or {}))
                    replay["replayed"] = True
                    replay["reused"] = True
                    return replay
            return _err_env(
                f"{SERVER_NAME}.idempotency_timeout",
                "同一财务运行请求在幂等等待窗口内未完成",
                status="upstream_failure",
                blockers=["idempotency_timeout"],
                next_actions=["使用相同 idempotency_key 重试以取得最终结果"],
                run_id=None,
                missing_inputs=[],
                retryable=True,
                replayed=False,
            )
        replay = json.loads(json.dumps(prior_payload.get("result") or {}))
        replay["replayed"] = True
        replay["reused"] = True
        return replay
    try:
        from lvke_mcp.domains.finance import run_service

        # 纯确定性运行：优先消费不可变 spec_id。原 spec/force_flat 只作兼容。
        spec = args.get("spec") if isinstance(args.get("spec"), dict) else None
        spec_id = str(args.get("spec_id") or "").strip()
        basis_of_estimate_id = str(args.get("basis_of_estimate_id") or "").strip()
        basis_of_estimate_hash = ""
        force_flat = bool(args.get("force_flat") or False)
        stored_spec = None
        if spec_id and (spec is not None or force_flat):
            return _err_env(
                f"{SERVER_NAME}.invalid_argument",
                "spec_id 与 spec/force_flat 不可同时传入",
                status="blocked",
            )
        if spec_id:
            stored_spec = SPEC_STORE.get(wsid, spec_id)
            if stored_spec is None:
                return _err_env(f"{SERVER_NAME}.spec_not_found", "未找到已固化 FinanceSpec", status="blocked")
            stored_payload = stored_spec.get("payload") if isinstance(stored_spec.get("payload"), dict) else {}
            spec = stored_payload.get("spec") if isinstance(stored_payload.get("spec"), dict) else None
            if spec is None:
                return _err_env(f"{SERVER_NAME}.spec_invalid", "FinanceSpec 快照无效", status="blocked")
            if mode == "review_candidate" and stored_payload.get("confirmation_status") != "confirmed":
                return _ok_env(
                    {"available": False, "error": "spec_confirmation_required", "spec_id": spec_id},
                    source=f"{SERVER_NAME}.finance_run_model",
                    status="blocked",
                    blockers=["spec_confirmation_required"],
                    next_actions=["先调用 finance_confirm_spec 确认候选 Spec"],
                    run_id=None,
                    missing_inputs=[],
                )
            if mode == "review_candidate":
                boe_record = (
                    BASIS_OF_ESTIMATE_STORE.get(
                        wsid, basis_of_estimate_id
                    )
                    if basis_of_estimate_id
                    else _latest_formal_boe(wsid, spec_id)
                )
                boe_payload = (
                    boe_record.get("payload")
                    if isinstance((boe_record or {}).get("payload"), dict)
                    else {}
                )
                if (
                    boe_record is None
                    or boe_payload.get("spec_id") != spec_id
                    or not boe_payload.get("formal_ready")
                ):
                    return _ok_env(
                        {
                            "available": False,
                            "error": "basis_of_estimate_required",
                            "spec_id": spec_id,
                        },
                        source=f"{SERVER_NAME}.finance_run_model",
                        status="blocked",
                        blockers=["basis_of_estimate_required"],
                        next_actions=[
                            "调用 finance_build_basis_of_estimate，完整绑定重大输入来源与选择理由"
                        ],
                        run_id=None,
                        missing_inputs=[],
                    )
                basis_of_estimate_id = boe_record["object_id"]
                basis_of_estimate_hash = boe_record["basis_hash"]
            elif basis_of_estimate_id:
                boe_record = BASIS_OF_ESTIMATE_STORE.get(
                    wsid, basis_of_estimate_id
                )
                boe_payload = (
                    boe_record.get("payload")
                    if isinstance((boe_record or {}).get("payload"), dict)
                    else {}
                )
                if (
                    boe_record is None
                    or boe_payload.get("spec_id") != spec_id
                    or not boe_payload.get("technical_ready")
                ):
                    return _ok_env(
                        {
                            "available": False,
                            "error": "basis_of_estimate_invalid",
                            "spec_id": spec_id,
                        },
                        source=f"{SERVER_NAME}.finance_run_model",
                        status="blocked",
                        blockers=["basis_of_estimate_invalid"],
                        next_actions=["使用同一 spec 的完整 BoE，或省略它运行 estimate preview"],
                        run_id=None,
                        missing_inputs=[],
                    )
                basis_of_estimate_hash = boe_record["basis_hash"]
        if spec is None and not force_flat:
            return _ok_env(
                {
                    "ok": False,
                    "available": False,
                    "error": "spec_required",
                    "message": "finance_run_model 需要已固化 spec；请先调用 finance_prepare_spec",
                },
                source=f"{SERVER_NAME}.finance_run_model",
                status="blocked",
                blockers=["spec_required：缺已固化 FinanceSpec"],
                next_actions=[
                    "先调用 finance_prepare_spec 固化 spec，或显式 force_flat=true"
                ],
                run_id=None,
                missing_inputs=[],
            )
        stored_payload = stored_spec.get("payload") if stored_spec else {}
        input_revision = args.get("input_revision") if isinstance(args.get("input_revision"), dict) else stored_payload.get("input_revision")
        input_revision_id = args.get("input_revision_id", stored_payload.get("input_revision_id"))
        spec_hash = str(stored_payload.get("spec_hash") or args.get("spec_hash") or "")
        if spec_id and not isinstance(input_revision, dict):
            input_revision = {}
        if spec_id and not input_revision.get("total_investment_wan"):
            return _ok_env(
                {
                    "available": False,
                    "error": "missing_inputs",
                    "missing_inputs": ["total_investment_wan"],
                    "spec_id": spec_id,
                },
                source=f"{SERVER_NAME}.finance_run_model",
                status="missing_inputs",
                blockers=["缺少必要输入：total_investment_wan"],
                next_actions=["重新 prepare 并确认包含总投资的 FinanceSpec"],
                run_id=None,
                spec_id=spec_id,
                missing_inputs=["total_investment_wan"],
            )
        if not _revenue_input_complete(spec, input_revision):
            return _ok_env(
                {
                    "available": False,
                    "error": "revenue_inputs_required",
                    "missing_inputs": ["annual_revenue_wan_or_revenue_driver"],
                    "spec_id": spec_id,
                },
                source=f"{SERVER_NAME}.finance_run_model",
                status="missing_inputs",
                blockers=["revenue_inputs_required"],
                next_actions=["补充 annual_revenue_wan 或完整收入模型后重新 prepare/confirm"],
                run_id=None,
                spec_id=spec_id,
                missing_inputs=["annual_revenue_wan_or_revenue_driver"],
            )
        if (
            mode == "review_candidate"
            and isinstance(input_revision, dict)
            and bool(input_revision.get("is_operating"))
        ):
            breakdown = input_revision.get("invest_breakdown")
            breakdown = breakdown if isinstance(breakdown, dict) else {}
            working_capital = breakdown.get("working_capital_wan")
            working_series = input_revision.get("working_capital_by_year") or []
            has_working_capital = (
                isinstance(working_capital, (int, float)) and working_capital > 0
            ) or any(
                isinstance(value, (int, float)) and value > 0
                for value in (working_series if isinstance(working_series, list) else [])
            )
            turnover = input_revision.get("wc_turnover")
            turnover = turnover if isinstance(turnover, dict) else {}
            required_turnover = ("receivable", "inventory", "cash", "payable")
            missing_turnover = [
                name for name in required_turnover
                if turnover.get(name) is None and turnover.get(f"{name}_days") is None
            ]
            if has_working_capital and missing_turnover:
                return _ok_env(
                    {
                        "available": False,
                        "error": "working_capital_turnover_required",
                        "missing_inputs": [f"wc_turnover.{name}" for name in missing_turnover],
                        "field_errors": [{
                            "path": f"/input_revision/wc_turnover/{name}",
                            "code": "required_for_review_candidate",
                            "message": f"正式候选缺少 {name} 周转参数",
                        } for name in missing_turnover],
                    },
                    source=f"{SERVER_NAME}.finance_run_model",
                    status="missing_inputs",
                    blockers=["working_capital_turnover_required"],
                    next_actions=["补充 wc_turnover 分项周转天数后重新运行"],
                    run_id=None,
                    missing_inputs=[f"wc_turnover.{name}" for name in missing_turnover],
                )

        IDEMPOTENCY_STORE.put(
            wsid,
            {
                "operation": "finance_run_model",
                "key_hash": key_hash,
                "content_fingerprint": request_fingerprint,
                "expires_at": idempotency_expires_at,
                "run_id": None,
                "in_progress": True,
            },
            producer=f"{SERVER_NAME}.finance_run_model",
            basis={
                "operation": "finance_run_model",
                "key_hash": key_hash,
                "content_fingerprint": request_fingerprint,
            },
        )
        reservation_created = True
        data = run_service.run_workspace_finance_model(
            wsid,
            spec=spec,
            spec_id=spec_id,
            spec_hash=spec_hash,
            basis_of_estimate_id=basis_of_estimate_id,
            basis_of_estimate_hash=basis_of_estimate_hash,
            input_revision=input_revision,
            input_revision_id=(int(input_revision_id) if input_revision_id is not None else None),
            mode=mode,
            force_recompute=bool(args.get("force_recompute") or False),
            force_flat=force_flat,
            allow_prepare_llm=False,
            record_audit=True,
            agent_trace_id=str(args.get("agent_trace_id") or ""),
            tool_call_id=str(args.get("tool_call_id") or ""),
            report_file="mcp/finance_run_model",
            valuation_date=str(args.get("valuation_date") or ""),
            requested_manifest=(
                args.get("requested_manifest")
                if isinstance(args.get("requested_manifest"), dict) else None
            ),
            selected_scenario_id=str(args.get("selected_scenario_id") or "base"),
        )
        run_id = str(data.get("run_id") or "") or None
        if data.get("available") and not run_id:
            data = dict(data)
            data["available"] = False
            data["ok"] = False
            data["calculation_status"] = "failed"
            data["reason"] = "finance_run_persistence_failed"
            data["blocking_issues"] = [
                *list(data.get("blocking_issues") or []),
                {
                    "rule": "finance_run_persistence_failed",
                    "detail": "财务计算结果未形成可读取的不可变 FinanceRun",
                },
            ]
        uri = _run_uri(wsid, run_id)
        if uri:
            data["resource_uri"] = uri  # 兼容旧调用方
        missing = _str_list(data.get("missing_inputs"))
        if data.get("available") and data.get("consistency_ok") is False:
            status = "blocked"
            blockers = _blocking_rules(data) or ["finance_consistency_failed"]
            next_actions = ["修正财务勾稽问题后重新运行；当前 run 不可进入十三表正式候选"]
        elif data.get("available") and run_id:
            status = "ok"
            blockers: list[str] = []
            next_actions = ["用 run_id 调用 lvke-finance-tables.tables_render 渲染 13 表"]
        elif missing:
            # 缺关键输入：如实 missing_inputs，不生成 IRR。
            status = "missing_inputs"
            blockers = [f"缺少必要输入：{item}" for item in missing]
            next_actions = ["补齐缺失输入后重试 finance_run_model"]
        else:
            status = "blocked"
            blockers = _blocking_rules(data) or [
                str(data.get("reason") or "run_not_available")
            ]
            next_actions = (
                ["检查财务审计存储后重试；不得使用未持久化结果生成十三表"]
                if data.get("reason") == "finance_run_persistence_failed"
                else ["按 blocking_issues 修正输入或 spec 后重试"]
            )
        result = _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_run_model",
            status=status,
            resource_uris=[uri] if uri else [],
            blockers=blockers,
            next_actions=next_actions,
            run_id=run_id,
            spec_id=spec_id or None,
            missing_inputs=missing,
            field_errors=list(data.get("field_errors") or []),
        )
        result["replayed"] = False
        result["reused"] = False
        result["idempotency_expires_at"] = idempotency_expires_at
        cached_result = json.loads(json.dumps(result, ensure_ascii=False, default=str))
        IDEMPOTENCY_STORE.put(
            wsid,
            {
                "operation": "finance_run_model",
                "key_hash": key_hash,
                "content_fingerprint": request_fingerprint,
                "expires_at": idempotency_expires_at,
                "run_id": run_id,
                "result": cached_result,
            },
            producer=f"{SERVER_NAME}.finance_run_model",
            source_ids=[run_id] if run_id else [],
            basis={
                "operation": "finance_run_model",
                "key_hash": key_hash,
                "content_fingerprint": request_fingerprint,
            },
        )
        return result
    except Exception:  # noqa: BLE001
        failure = _exception_env(
            "finance_run_model failed",
            f"{SERVER_NAME}.run_failed",
            "运行财务模型失败",
        )
        if reservation_created:
            IDEMPOTENCY_STORE.put(
                wsid,
                {
                    "operation": "finance_run_model",
                    "key_hash": key_hash,
                    "content_fingerprint": request_fingerprint,
                    "expires_at": idempotency_expires_at,
                    "run_id": None,
                    "result": failure,
                },
                producer=f"{SERVER_NAME}.finance_run_model",
                basis={
                    "operation": "finance_run_model",
                    "key_hash": key_hash,
                    "content_fingerprint": request_fingerprint,
                },
            )
        return failure


def _tool_validate_spec(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import validate_spec

    return validate_spec(args)


def _tool_render_tables(args: dict) -> dict:
    run_id = args.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return _err_env(
            f"{SERVER_NAME}.invalid_argument", "run_id 必填",
            deprecated=True, warnings=[_DEPRECATED_RENDER_HINT],
        )
    wsid = _ws(args)
    if not wsid:
        # 允许只传 run_id 时从审计库反查困难；仍要求 workspace_id
        return _err_env(
            f"{SERVER_NAME}.invalid_argument", "workspace_id 必填",
            deprecated=True, warnings=[_DEPRECATED_RENDER_HINT],
        )
    try:
        from lvke_mcp.domains.finance import run_service

        data = run_service.render_workspace_finance_tables(
            wsid,
            run_id=run_id.strip(),
            format=str(args.get("format") or "structured"),
            include_control_tables=bool(args.get("include_control_tables", True)),
        )
        if not data.get("ok"):
            return _err_env(
                f"{SERVER_NAME}.{data.get('error') or 'render_failed'}",
                data.get("message") or "渲染 13 表失败",
                detail=data,
                status="blocked",
                deprecated=True,
                warnings=[_DEPRECATED_RENDER_HINT],
                next_actions=["迁移到 lvke-finance-tables.tables_render"],
            )
        rid = str(data.get("run_id") or "") or None
        missing_keys = _str_list(data.get("missing_delivery_keys"))
        warnings = [_DEPRECATED_RENDER_HINT]
        if missing_keys:
            warnings.append(f"缺失交付表：{'、'.join(missing_keys)}")
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_render_tables",
            status="partial" if missing_keys else "ok",
            resource_uris=[_run_uri(wsid, rid)] if rid else [],
            warnings=warnings,
            next_actions=["迁移到 lvke-finance-tables.tables_render"],
            deprecated=True,
            run_id=rid,
            missing_delivery_keys=missing_keys,
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_render_tables failed",
            f"{SERVER_NAME}.render_failed",
            "渲染 13 表失败",
            deprecated=True,
            warnings=[_DEPRECATED_RENDER_HINT],
        )


def _tool_get_run(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import get_run

    return get_run(args)


def _tool_generate_package(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(
            f"{SERVER_NAME}.invalid_argument", "workspace_id 必填",
            deprecated=True, warnings=[_DEPRECATED_PACKAGE_HINT],
        )
    mode = str(args.get("mode") or "estimate_preview")
    if mode not in {"estimate_preview", "review_candidate"}:
        mode = "estimate_preview"
    try:
        from lvke_mcp.domains.finance import run_service

        data = run_service.generate_workspace_finance_package(
            wsid,
            mode=mode,
            force_refresh_spec=bool(args.get("force_refresh_spec") or False),
            force_recompute=bool(args.get("force_recompute") or False),
            force_flat=bool(args.get("force_flat") or False),
            confirmed_spec=args.get("confirmed_spec") if isinstance(args.get("confirmed_spec"), dict) else None,
            agent_trace_id=str(args.get("agent_trace_id") or ""),
            tool_call_id=str(args.get("tool_call_id") or ""),
            valuation_date=str(args.get("valuation_date") or ""),
            requested_manifest=(
                args.get("requested_manifest")
                if isinstance(args.get("requested_manifest"), dict) else None
            ),
            selected_scenario_id=str(args.get("selected_scenario_id") or "base"),
        )
        run_id = str(data.get("run_id") or "") or None
        uri = _run_uri(wsid, run_id)
        missing = _str_list(data.get("missing_inputs"))
        stage = str(data.get("stage") or "")
        if data.get("ok"):
            status = "ok"
            blockers: list[str] = []
        elif missing:
            status = "missing_inputs"
            blockers = [f"缺少必要输入：{item}" for item in missing]
        else:
            status = "blocked"
            blockers = _blocking_rules(data) or [f"stage={stage or 'unknown'} 未完成"]
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_generate_package",
            status=status,
            resource_uris=[uri] if uri else [],
            warnings=[_DEPRECATED_PACKAGE_HINT, *_str_list(data.get("prepare_warnings"))],
            blockers=blockers,
            next_actions=[
                "迁移：finance_prepare_spec → finance_run_model → lvke-finance-tables.tables_render",
            ],
            deprecated=True,
            run_id=run_id,
            stage=stage or None,
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_generate_package failed",
            f"{SERVER_NAME}.package_failed",
            "生成财务包失败",
            deprecated=True,
            warnings=[_DEPRECATED_PACKAGE_HINT],
        )


def _tool_import_vendor_review(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    xlsx_path = args.get("xlsx_path") or args.get("path")
    if not isinstance(xlsx_path, str) or not xlsx_path.strip():
        return _err_env(f"{SERVER_NAME}.invalid_argument", "xlsx_path 必填")
    cohort = args.get("cohort_xlsx_paths")
    if cohort is not None and not (
        isinstance(cohort, list) and all(isinstance(item, str) for item in cohort)
    ):
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "cohort_xlsx_paths 必须是字符串数组",
        )
    try:
        from lvke_mcp.domains.finance.vendor_review import import_vendor_workbook_review

        data = import_vendor_workbook_review(
            wsid,
            xlsx_path.strip(),
            valuation_date=str(args.get("valuation_date") or ""),
            force_recompute=bool(args.get("force_recompute") or False),
            cohort_xlsx_paths=cohort or None,
        )
        run_id = str(data.get("run_id") or "") or None
        uri = _run_uri(wsid, run_id)
        missing = _str_list(data.get("missing_inputs"))
        blocking = [
            str(issue.get("rule") or issue.get("code") or "blocking_issue")
            for issue in (data.get("blocking_issues") or [])
            if isinstance(issue, dict)
        ]
        if missing:
            status = "missing_inputs"
        elif not data.get("available"):
            status = "blocked"
        elif blocking:
            # 复核完成但存在阻断预警：不冒充复核通过。
            status = "blocked"
        else:
            status = "ok"
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_import_vendor_review",
            status=status,
            resource_uris=[uri] if uri else [],
            warnings=_str_list(((data.get("reference") or {}).get("warnings"))),
            blockers=blocking or (
                [f"缺少必要输入：{item}" for item in missing] if missing else []
            ),
            next_actions=(
                ["修复阻断预警并重新运行确定性校验"] if blocking else []
            ),
            reference_id=data.get("reference_id"),
            review_passed=bool(data.get("review_passed")),
            run_id=run_id,
            missing_inputs=missing,
        )
    except FileNotFoundError:
        return _exception_env(
            "finance_import_vendor_review workbook missing",
            f"{SERVER_NAME}.vendor_workbook_not_found",
            "甲方工作簿不存在",
        )
    except ImportError:  # pragma: no cover - environment dependent
        return _exception_env(
            "finance_import_vendor_review parser unavailable",
            f"{SERVER_NAME}.vendor_workbook_parser_unavailable",
            "甲方工作簿解析依赖不可用",
        )
    except (ValueError, OSError, zipfile.BadZipFile):
        return _exception_env(
            "finance_import_vendor_review parse failed",
            f"{SERVER_NAME}.vendor_workbook_parse_failed",
            "甲方工作簿格式无效或解析失败",
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_import_vendor_review failed",
            f"{SERVER_NAME}.vendor_review_failed",
            "导入并复核甲方计算表失败",
        )


def _load_consistent_run(workspace_id: str, run_id: str) -> dict | None:
    from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

    run = get_workspace_finance_run(
        workspace_id,
        run_id=run_id,
        view="full",
    )
    if not run.get("available") or run.get("consistency_ok") is not True:
        return None
    return run


def _planning_record(
    workspace_id: str,
    object_id: str,
) -> dict[str, Any] | None:
    from lvke_mcp.adapters.project_planning_repository import get_record

    return get_record(workspace_id, object_id)


def _required_boe_pointers(spec_payload: dict[str, Any]) -> list[str]:
    input_revision = spec_payload.get("input_revision")
    input_revision = input_revision if isinstance(input_revision, dict) else {}
    required = ["/input_revision/total_investment_wan", "/spec/revenue"]
    for field in (
        "annual_operating_cost_wan",
        "invest_breakdown",
        "wc_turnover",
        "labor_plan",
        "fixed_asset_categories",
        "taxes",
    ):
        if input_revision.get(field) not in (None, "", [], {}):
            required.append(f"/input_revision/{field}")
    return required


def _latest_formal_boe(
    workspace_id: str,
    spec_id: str,
) -> dict[str, Any] | None:
    matches = [
        record
        for record in BASIS_OF_ESTIMATE_STORE.list(workspace_id)
        if (record.get("payload") or {}).get("spec_id") == spec_id
        and bool((record.get("payload") or {}).get("formal_ready"))
    ]
    return max(matches, key=lambda record: str(record.get("created_at") or ""), default=None)


def _tool_build_basis_of_estimate(args: dict) -> dict:
    wsid = _ws(args)
    spec_id = str(args.get("spec_id") or "").strip()
    idempotency_key = str(args.get("idempotency_key") or "").strip()
    planning_ids = _unique_strings(args.get("planning_object_ids"))
    evidence_ids = _unique_strings(args.get("evidence_pack_ids"))
    entries = args.get("entries") if isinstance(args.get("entries"), list) else []
    if not wsid or not spec_id or not idempotency_key or not entries:
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "workspace_id、spec_id、entries 与 idempotency_key 必填",
            status="blocked",
        )
    spec_record = SPEC_STORE.get(wsid, spec_id)
    spec_payload = spec_record.get("payload") if isinstance((spec_record or {}).get("payload"), dict) else {}
    if spec_record is None or spec_payload.get("confirmation_status") != "confirmed":
        return _err_env(
            f"{SERVER_NAME}.confirmed_spec_required",
            "Basis of Estimate 只能绑定同作用域已确认 FinanceSpec",
            status="blocked",
        )
    bound_evidence_ids = set(_str_list(spec_payload.get("evidence_pack_ids")))
    if not set(evidence_ids) <= bound_evidence_ids:
        return _err_env(
            f"{SERVER_NAME}.evidence_basis_mismatch",
            "BoE EvidencePack 必须已绑定到 FinanceSpec",
            status="blocked",
        )
    evidence_records = []
    for evidence_id in evidence_ids:
        record = EVIDENCE_STORE.get(wsid, evidence_id)
        if record is None:
            return _err_env(
                f"{SERVER_NAME}.evidence_pack_not_found",
                "BoE 引用的 EvidencePack 不存在或跨越作用域",
                status="blocked",
            )
        evidence_records.append(record)
    planning_records = []
    for object_id in planning_ids:
        record = _planning_record(wsid, object_id)
        payload = record.get("payload") if isinstance((record or {}).get("payload"), dict) else {}
        if record is None or payload.get("status") != "confirmed":
            return _err_env(
                f"{SERVER_NAME}.confirmed_planning_object_required",
                f"BoE planning basis 必须是同作用域 confirmed 对象：{object_id}",
                status="blocked",
            )
        planning_records.append(record)
    source_records = {
        record["object_id"]: record
        for record in [*planning_records, *evidence_records]
    }
    allowed_sources = set(source_records)
    field_errors = []
    pointers: set[str] = set()
    for index, entry in enumerate(entries):
        pointer = str(entry.get("target_pointer") or "")
        if pointer in pointers:
            field_errors.append({
                "path": f"/entries/{index}/target_pointer",
                "code": "duplicate_target_pointer",
            })
        pointers.add(pointer)
        source_object_id = str(entry.get("source_object_id") or "")
        if source_object_id not in allowed_sources:
            field_errors.append({
                "path": f"/entries/{index}/source_object_id",
                "code": "source_object_not_bound",
            })
        else:
            source_payload = source_records[source_object_id].get("payload") or {}
            source_track = str(source_payload.get("evidence_track") or "")
            declared_eligibility = str(entry.get("evidence_eligibility") or "")
            eligible_tracks = {
                "formal_evidence": {"real", "formal_evidence"},
                "source_reconstructed": {"source_reconstructed"},
                "technical_fixture": {"technical_fixture"},
                "controlled_assumption": {"controlled_assumption"},
            }
            if source_track not in eligible_tracks.get(declared_eligibility, set()):
                field_errors.append({
                    "path": f"/entries/{index}/evidence_eligibility",
                    "code": "evidence_eligibility_mismatch",
                })
            if declared_eligibility == "source_reconstructed":
                reconstruction = entry.get("reconstruction") or entry.get("reconstruction_record")
                errors = reconstruction_errors(reconstruction)
                field_errors.extend({
                    "path": f"/entries/{index}/reconstruction/{code.split('_required')[0] if code.endswith('_required') else 'record'}",
                    "code": code,
                } for code in errors)
        if not all(entry.get(field) for field in (
            "target_pointer", "unit", "period", "source_type", "source_object_id",
            "method", "selection_reason", "locator", "content_hash", "evidence_eligibility"
        )):
            field_errors.append({"path": f"/entries/{index}", "code": "boe_entry_incomplete"})
    required_pointers = _required_boe_pointers(spec_payload)
    missing_pointers = sorted(set(required_pointers) - pointers)
    field_errors.extend({"path": pointer, "code": "major_input_basis_missing"} for pointer in missing_pointers)
    if field_errors:
        return _ok_env(
            {"available": False, "field_errors": field_errors},
            source=f"{SERVER_NAME}.finance_build_basis_of_estimate",
            status="missing_inputs",
            blockers=sorted({str(item["code"]) for item in field_errors}),
            next_actions=["为每个重大 FinanceSpec 输入补充已绑定对象、locator、hash 和选择理由"],
            basis_of_estimate_id=None,
            spec_id=spec_id,
            technical_ready=False,
            formal_ready=False,
        )
    content_fingerprint = sha256_json({
        "spec_id": spec_id,
        "spec_basis_hash": spec_record["basis_hash"],
        "fact_pack_id": spec_payload.get("fact_pack_id"),
        "fact_pack_basis_hash": spec_payload.get("fact_pack_basis_hash"),
        "planning_object_ids": planning_ids,
        "evidence_pack_ids": evidence_ids,
        "entries": entries,
    })
    key_hash = "sha256:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    prior = next((
        record
        for record in BASIS_OF_ESTIMATE_STORE.list(wsid)
        if (record.get("payload") or {}).get("idempotency_key_hash") == key_hash
    ), None)
    if prior is not None:
        prior_payload = prior.get("payload") or {}
        if prior_payload.get("content_fingerprint") != content_fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已用于不同 BoE 请求",
                status="blocked",
            )
        return _ok_env(
            prior,
            source=f"{SERVER_NAME}.finance_build_basis_of_estimate",
            status="ok" if prior_payload.get("formal_ready") else "partial",
            resource_uris=[prior["resource_uri"]],
            basis_of_estimate_id=prior["object_id"],
            spec_id=spec_id,
            technical_ready=bool(prior_payload.get("technical_ready")),
            formal_ready=bool(prior_payload.get("formal_ready")),
            replayed=True,
        )
    technical_ready = all(
        entry.get("evidence_eligibility") in {"formal_evidence", "source_reconstructed", "technical_fixture"}
        for entry in entries
    )
    formal_ready = all(
        entry.get("evidence_eligibility") in {"formal_evidence", "source_reconstructed"}
        for entry in entries
    )
    reconstructed = any(entry.get("evidence_eligibility") == "source_reconstructed" for entry in entries)
    reconstruction_records = [
        record
        for entry in entries
        for record in [entry.get("reconstruction") or entry.get("reconstruction_record")]
        if isinstance(record, dict)
    ]
    payload = {
        "object_type": "BasisOfEstimate",
        "spec_id": spec_id,
        "spec_hash": spec_payload.get("spec_hash"),
        "entries": entries,
        "required_major_input_pointers": required_pointers,
        "planning_object_ids": planning_ids,
        "evidence_pack_ids": evidence_ids,
        "technical_ready": technical_ready,
        "formal_ready": formal_ready,
        "evidence_policy": "source_reconstructed" if reconstructed else "formal_evidence",
        "project_fact_certified": not reconstructed,
        "reconstruction_records": reconstruction_records,
        "reconstructed_source_ids": [str(item.get("reconstruction_id") or "") for item in reconstruction_records if item.get("reconstruction_id")],
        "unresolved_inputs": list(args.get("unresolved_inputs") or spec_payload.get("unresolved_inputs") or []),
        "release_limitations": list(args.get("release_limitations") or spec_payload.get("release_limitations") or []),
        "evidence_eligibility": (
            "source_reconstructed"
            if reconstructed
            else "formal_evidence"
            if formal_ready
            else "technical_fixture"
            if technical_ready
            else "estimate_only"
        ),
        "idempotency_key_hash": key_hash,
        "content_fingerprint": content_fingerprint,
        "fact_pack_id": spec_payload.get("fact_pack_id"),
        "fact_pack_hash": spec_payload.get("fact_pack_hash"),
        "fact_pack_basis_hash": spec_payload.get("fact_pack_basis_hash"),
        "parent_object_ids": [
            spec_id,
            *planning_ids,
            *evidence_ids,
            *([str(spec_payload.get("fact_pack_id"))] if spec_payload.get("fact_pack_id") else []),
        ],
    }
    record = BASIS_OF_ESTIMATE_STORE.put(
        wsid,
        payload,
        producer=f"{SERVER_NAME}.finance_build_basis_of_estimate",
        status="ok" if formal_ready else "partial",
        source_ids=payload["parent_object_ids"],
        basis={
            "spec_basis_hash": spec_record["basis_hash"],
            "planning_basis_hashes": [record["basis_hash"] for record in planning_records],
            "evidence_basis_hashes": [record["basis_hash"] for record in evidence_records],
            "fact_pack_basis_hash": spec_payload.get("fact_pack_basis_hash"),
            "fact_pack_hash": spec_payload.get("fact_pack_hash"),
            "content_fingerprint": content_fingerprint,
        },
    )
    return _ok_env(
        record,
        source=f"{SERVER_NAME}.finance_build_basis_of_estimate",
        status="ok" if formal_ready else "partial",
        resource_uris=[record["resource_uri"]],
        warnings=(
            []
            if formal_ready and not reconstructed
            else ["本 BoE 使用 source_reconstructed，仅代表流程验收，不认证项目原始事实"]
            if formal_ready and reconstructed
            else ["技术夹具 BoE 只能验证技术链，不得触发正式候选或正式发布"]
            if technical_ready
            else ["BoE 含 controlled_assumption，仅可用于 estimate preview"]
        ),
        next_actions=(
            ["将 basis_of_estimate_id 与 spec_id 一起用于 review_candidate 财务运行"]
            if formal_ready
            else ["仅在 estimate_preview 中绑定该 BoE；正式候选需换用 formal_evidence"]
        ),
        basis_of_estimate_id=record["object_id"],
        spec_id=spec_id,
        technical_ready=technical_ready,
        formal_ready=formal_ready,
        replayed=False,
    )


def _tool_get_basis_of_estimate(args: dict) -> dict:
    return _tool_get_analysis(
        args,
        store=BASIS_OF_ESTIMATE_STORE,
        id_field="basis_of_estimate_id",
        source=f"{SERVER_NAME}.finance_get_basis_of_estimate",
    )


def _tool_build_balance_sheet(args: dict) -> dict:
    wsid = _ws(args)
    run_id = str(args.get("run_id") or "").strip()
    if not wsid or not run_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 与 run_id 必填")
    try:
        run = _load_consistent_run(wsid, run_id)
        if run is None:
            return _err_env(
                f"{SERVER_NAME}.base_run_unavailable",
                "基准 FinanceRun 不存在或未通过勾稽",
                status="blocked",
                next_actions=["选择同工作区且 consistency_ok=true 的 FinanceRun"],
            )
        from lvke_mcp.domains.finance.advanced_analysis import build_balance_sheet_schedule

        schedule = build_balance_sheet_schedule(run)
        if not schedule.get("available"):
            return _ok_env(
                schedule,
                source=f"{SERVER_NAME}.finance_build_balance_sheet",
                status="missing_inputs",
                blockers=["annual.financial_plan 缺失"],
                next_actions=["重新生成包含财务计划现金流的 FinanceRun"],
                balance_sheet_id=None,
                run_id=run_id,
                formal_ready=False,
            )
        payload = {
            **schedule,
            "workspace_id": wsid,
            "run_id": run_id,
            "run_input_hash": run.get("input_hash"),
            "run_spec_hash": run.get("spec_hash"),
            "run_model_version": run.get("model_version"),
        }
        record = BALANCE_SHEET_STORE.put(
            wsid,
            payload,
            producer=f"{SERVER_NAME}.finance_build_balance_sheet",
            status="ok" if schedule.get("formal_ready") else "partial",
            source_ids=[run_id],
            basis={
                "run_id": run_id,
                "input_hash": run.get("input_hash"),
                "spec_hash": run.get("spec_hash"),
                "model_version": run.get("model_version"),
            },
        )
        status = "ok" if schedule.get("formal_ready") else "partial"
        return _ok_env(
            record,
            source=f"{SERVER_NAME}.finance_build_balance_sheet",
            status=status,
            resource_uris=[record["resource_uri"]],
            warnings=([] if status == "ok" else ["资产负债表权益组成与计算残差尚未勾稽"]),
            blockers=[],
            next_actions=([] if status == "ok" else ["核对资本金、利润分配与终值回收口径"]),
            balance_sheet_id=record["object_id"],
            run_id=run_id,
            formal_ready=bool(schedule.get("formal_ready")),
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_build_balance_sheet failed",
            f"{SERVER_NAME}.balance_sheet_failed",
            "生成资产负债表失败",
        )


def _tool_get_analysis(args: dict, *, store: JSONArtifactStore, id_field: str, source: str) -> dict:
    wsid = _ws(args)
    object_id = str(args.get(id_field) or "").strip()
    if not wsid or not object_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", f"workspace_id 与 {id_field} 必填")
    record = store.get(wsid, object_id)
    if record is None:
        return _err_env(
            f"{SERVER_NAME}.analysis_not_found",
            "未找到同工作区下的高级分析对象",
            status="blocked",
        )
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    readiness = {
        "formal_ready": bool(payload.get("formal_ready", record.get("status") == "ok")),
    }
    if id_field == "basis_of_estimate_id":
        readiness["technical_ready"] = bool(
            payload.get("technical_ready", payload.get("formal_ready"))
        )
    return _ok_env(
        record,
        source=source,
        status="ok" if record.get("status") == "ok" else "partial",
        resource_uris=[record["resource_uri"]],
        warnings=([] if record.get("status") == "ok" else ["分析对象存在未完成勾稽"]),
        **{
            id_field: record["object_id"],
            "run_id": payload.get("run_id"),
            **readiness,
        },
    )


def _tool_get_balance_sheet(args: dict) -> dict:
    return _tool_get_analysis(
        args,
        store=BALANCE_SHEET_STORE,
        id_field="balance_sheet_id",
        source=f"{SERVER_NAME}.finance_get_balance_sheet",
    )


def _tool_run_monte_carlo(args: dict) -> dict:
    wsid = _ws(args)
    run_id = str(args.get("run_id") or "").strip()
    distributions = args.get("distributions")
    sample_count = args.get("sample_count", 1000)
    seed = args.get("seed", 0)
    if not wsid or not run_id or not isinstance(distributions, list):
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "workspace_id、run_id 与 distributions 必填",
        )
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or not 10 <= sample_count <= 10_000:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "sample_count 必须为 10..10000 的整数")
    if not isinstance(seed, int) or isinstance(seed, bool):
        return _err_env(f"{SERVER_NAME}.invalid_argument", "seed 必须为整数")
    try:
        run = _load_consistent_run(wsid, run_id)
        if run is None:
            return _err_env(
                f"{SERVER_NAME}.base_run_unavailable",
                "基准 FinanceRun 不存在或未通过勾稽",
                status="blocked",
            )
        from lvke_mcp.domains.finance import finance_model
        from lvke_mcp.domains.finance.advanced_analysis import run_monte_carlo

        finance_inputs = run.get("input_revision")
        if not isinstance(finance_inputs, dict) or not finance_inputs:
            return _ok_env(
                {"available": False, "missing_inputs": ["input_revision"]},
                source=f"{SERVER_NAME}.finance_run_monte_carlo",
                status="missing_inputs",
                blockers=["基准 run 缺少可重放 input_revision"],
                monte_carlo_id=None,
                run_id=run_id,
                sample_count=sample_count,
            )
        context = run.get("project_context") if isinstance(run.get("project_context"), dict) else {}
        spec = run.get("spec") if isinstance(run.get("spec"), dict) else None

        def rerun(scales: dict[str, float]) -> dict[str, Any] | None:
            result = finance_model.compute_financials(
                finance_inputs,
                invest_type=str(context.get("invest_type") or run.get("invest_type") or ""),
                build_period_months=(
                    int(context["build_period_months"])
                    if context.get("build_period_months") is not None else None
                ),
                industry=str(context.get("industry") or run.get("industry") or ""),
                spec=spec,
                _apply_custom=False,
                _with_analysis=False,
                _revenue_scale=scales.get("revenue_scale", 1.0),
                _op_cost_scale=scales.get("operating_cost_scale", 1.0),
                _construction_scale=scales.get("construction_scale", 1.0),
            )
            blocking = [
                item for item in finance_model.check_consistency(result)
                if isinstance(item, dict) and not item.get("ok") and item.get("blocking", True)
            ]
            if blocking:
                result = dict(result)
                result["available"] = False
            return result

        summary = run_monte_carlo(
            distributions=distributions,
            sample_count=sample_count,
            seed=seed,
            rerun=rerun,
        )
        if summary.get("field_errors"):
            return _ok_env(
                summary,
                source=f"{SERVER_NAME}.finance_run_monte_carlo",
                status="blocked",
                blockers=["distribution_manifest_invalid"],
                next_actions=["仅使用允许字段和合法的 uniform/triangular/normal 边界"],
                monte_carlo_id=None,
                run_id=run_id,
                sample_count=sample_count,
                field_errors=summary["field_errors"],
            )
        manifest_hash = sha256_json({
            "run_id": run_id,
            "input_hash": run.get("input_hash"),
            "spec_hash": run.get("spec_hash"),
            "distributions": distributions,
            "sample_count": sample_count,
            "seed": seed,
        })
        payload = {
            **summary,
            "workspace_id": wsid,
            "run_id": run_id,
            "run_input_hash": run.get("input_hash"),
            "run_spec_hash": run.get("spec_hash"),
            "distribution_manifest": distributions,
            "distribution_manifest_hash": manifest_hash,
            "formal_ready": bool(summary.get("available")),
        }
        record = MONTE_CARLO_STORE.put(
            wsid,
            payload,
            producer=f"{SERVER_NAME}.finance_run_monte_carlo",
            status="ok" if summary.get("available") else "blocked",
            source_ids=[run_id],
            basis={"manifest_hash": manifest_hash},
        )
        status = "ok" if summary.get("available") else "blocked"
        return _ok_env(
            record,
            source=f"{SERVER_NAME}.finance_run_monte_carlo",
            status=status,
            resource_uris=[record["resource_uri"]],
            blockers=[] if status == "ok" else ["所有 Monte Carlo 样本均未产生有效 IRR/NPV"],
            monte_carlo_id=record["object_id"],
            run_id=run_id,
            sample_count=sample_count,
            field_errors=[],
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_run_monte_carlo failed",
            f"{SERVER_NAME}.monte_carlo_failed",
            "执行 Monte Carlo 分析失败",
        )


def _tool_get_monte_carlo(args: dict) -> dict:
    return _tool_get_analysis(
        args,
        store=MONTE_CARLO_STORE,
        id_field="monte_carlo_id",
        source=f"{SERVER_NAME}.finance_get_monte_carlo",
    )


def _tool_list_analyses(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    resource_type = str(args.get("resource_type") or "all")
    stores = []
    if resource_type in {"all", "balance_sheet"}:
        stores.append(("balance_sheet", BALANCE_SHEET_STORE))
    if resource_type in {"all", "monte_carlo"}:
        stores.append(("monte_carlo", MONTE_CARLO_STORE))
    if resource_type in {"all", "basis_of_estimate"}:
        stores.append(("basis_of_estimate", BASIS_OF_ESTIMATE_STORE))
    if resource_type in {"all", "fact_pack"}:
        stores.append(("fact_pack", FACT_PACK_STORE))
    entries = [
        {
            "uri": record["resource_uri"],
            "name": record["object_id"],
            "mimeType": "application/json",
            "resource_type": kind,
            "content_hash": record["content_hash"],
            "basis_hash": record["basis_hash"],
            "status": record["status"],
        }
        for kind, store in stores
        for record in store.list(wsid)
    ]
    try:
        page = paginate_resource_entries(
            entries,
            cursor=str(args.get("cursor") or ""),
            limit=int(args.get("limit") or 50),
        )
    except ValueError as exc:
        return _err_env(f"{SERVER_NAME}.{exc}", "Resource 分页游标无效", status="blocked")
    return _ok_env(
        page,
        source=f"{SERVER_NAME}.finance_list_analyses",
        status="ok",
        analysis_count=len(page["resources"]),
        next_cursor=page["next_cursor"],
    )


def _resolve_analysis_resource(uri: str) -> dict | None:
    return (
        BALANCE_SHEET_STORE.resolve_uri(uri)
        or MONTE_CARLO_STORE.resolve_uri(uri)
        or BASIS_OF_ESTIMATE_STORE.resolve_uri(uri)
        or FACT_PACK_STORE.resolve_uri(uri)
    )


def _tool_read_analysis_resource(args: dict) -> dict:
    wsid = _ws(args)
    uri = str(args.get("uri") or "").strip()
    if not wsid or not uri:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 与 uri 必填")
    record = _resolve_analysis_resource(uri)
    if record is None or record.get("workspace_id") != wsid:
        return _err_env(
            f"{SERVER_NAME}.resource_not_found",
            "Resource 不存在或不属于当前工作区",
            status="blocked",
        )
    return _ok_env(
        record,
        source=f"{SERVER_NAME}.finance_read_analysis_resource",
        status="ok" if record.get("status") == "ok" else "partial",
        resource_uris=[record["resource_uri"]],
        object_id=record["object_id"],
        content_hash=record["content_hash"],
        basis_hash=record["basis_hash"],
    )


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    # annotations 仅是客户端提示，不是安全控制（方案 4.6）。
    read_closed = types.ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    write_deterministic = types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    write_nonidempotent = types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
    fact_pack_schema = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "version": {"type": "string"},
            "project_id": {"type": "string"},
            "valuation_date": {"type": "string"},
            "evidence_policy": {
                "type": "string",
                "enum": ["formal_evidence", "source_reconstructed"],
            },
            "project_fact_certified": {"type": "boolean"},
            "domains": {"type": "object"},
            "evidence": {"type": "array", "items": {"type": "object"}},
            "reconstruction_records": {"type": "array", "items": {"type": "object"}},
            "unresolved_inputs": {"type": "array", "items": {"type": "string"}},
            "release_limitations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["version", "evidence_policy", "domains", "evidence"],
    }
    server.register_tool(
        name="finance_prepare_fact_pack",
        description=(
            "规范化并固化 finance_fact_pack.v1 候选；来源重建模式必须绑定已导入的 "
            "Source Snapshot、hash、locator 和 method，不认证项目原始事实。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "fact_pack": fact_pack_schema,
                "idempotency_key": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "fact_pack", "idempotency_key"],
        },
        handler=_tool_prepare_fact_pack,
        output_schema=_output_schema(
            {
                "fact_pack_id": {"type": ["string", "null"]},
                "confirmation_status": {"type": "string"},
                "delivery_grade_ceiling": {"type": "string"},
                "fact_pack_hash": {"type": ["string", "null"]},
                "depth_assessment": {"type": "object"},
                "binding_assessment": {"type": "object"},
                "replayed": {"type": "boolean"},
            },
            success_required=["fact_pack_id", "confirmation_status", "delivery_grade_ceiling"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_confirm_fact_pack",
        description=(
            "复核 Fact Pack 深度和逐事实来源绑定，生成服务端确认的 formal_candidate "
            "不可变修订；source_reconstructed 始终保持 project_fact_certified=false。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "fact_pack_id": {"type": "string", "minLength": 1},
                "idempotency_key": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "fact_pack_id", "idempotency_key"],
        },
        handler=_tool_confirm_fact_pack,
        output_schema=_output_schema(
            {
                "fact_pack_id": {"type": ["string", "null"]},
                "confirmation_status": {"type": "string"},
                "delivery_grade_ceiling": {"type": "string"},
                "fact_pack_hash": {"type": ["string", "null"]},
                "depth_assessment": {"type": "object"},
                "binding_assessment": {"type": "object"},
                "replayed": {"type": "boolean"},
            },
            success_required=["fact_pack_id", "confirmation_status", "delivery_grade_ceiling"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_get_fact_pack",
        description="读取不可变 Finance Fact Pack、深度评估、证据覆盖和 hash，不重算。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "fact_pack_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "fact_pack_id"],
        },
        handler=_tool_get_fact_pack,
        output_schema=_output_schema(
            {
                "fact_pack_id": {"type": ["string", "null"]},
                "confirmation_status": {"type": "string"},
                "delivery_grade_ceiling": {"type": "string"},
                "fact_pack_hash": {"type": ["string", "null"]},
                "depth_assessment": {"type": "object"},
                "binding_assessment": {"type": "object"},
                "replayed": {"type": "boolean"},
            },
            success_required=["fact_pack_id", "confirmation_status", "delivery_grade_ceiling"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_prepare_spec",
        description=(
            "准备/复用确定性 FinanceSpec（收入/成本/税务口径），不调用内置 LLM。"
            "Agent 应先显式提交证据支持的假设；返回 spec、spec_hash、assumptions_to_confirm、missing_inputs。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "description": "工作区 ID"},
                "strategy": {
                    "type": "string",
                    "enum": ["reuse_confirmed", "propose_from_project"],
                    "default": "propose_from_project",
                },
                "force_refresh": {"type": "boolean", "default": False},
                "force_flat": {"type": "boolean", "default": False},
                "spec": finance_spec_candidate_schema(),
                "input_revision": finance_input_schema(),
                "evidence_pack_ids": {
                    "type": "array", "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
                "fact_pack_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "同工作区已 confirmed 且达到 formal_candidate 的 Finance Fact Pack ID。",
                },
                "unresolved_inputs": {"type": "array", "items": {"type": "string"}},
                "release_limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["workspace_id"],
        },
        handler=_tool_prepare_spec,
        output_schema=_output_schema(
            {
                "spec_hash": {"type": ["string", "null"]},
                "spec_id": {"type": ["string", "null"]},
                "evidence_binding_hash": {"type": "string"},
                "fact_pack_id": {"type": ["string", "null"]},
                "fact_pack_hash": {"type": ["string", "null"]},
                "fact_pack_errors": {"type": "array", "items": {"type": "string"}},
                "missing_inputs": {"type": "array", "items": {"type": "string"}},
                "assumptions_to_confirm": {"type": "array", "items": {"type": "string"}},
                "field_errors": {"type": "array", "items": {"type": "object"}},
                "input_hash": {"type": ["string", "null"]},
                "input_revision_id": {"type": ["integer", "null"]},
            },
            success_required=["spec_id", "spec_hash", "evidence_binding_hash", "missing_inputs", "assumptions_to_confirm"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_confirm_spec",
        description="将候选 FinanceSpec 固化为新的已确认修订；不原地改写候选对象。",
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "spec_id": {"type": "string", "minLength": 1},
                "note": {"type": "string", "maxLength": 2000},
                "idempotency_key": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "spec_id", "idempotency_key"],
        },
        handler=_tool_confirm_spec,
        output_schema=_output_schema(
            {"spec_id": {"type": "string"}, "spec_hash": {"type": "string"}},
            success_required=["spec_id", "spec_hash"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_validate_spec",
        description="校验 FinanceSpec 结构、数值和可选正式交付缺项；不计算任何财务指标。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "spec": finance_spec_candidate_schema(),
                "for_formal": {"type": "boolean", "default": False},
            },
            "required": ["spec"],
        },
        handler=_tool_validate_spec,
        output_schema=_output_schema(
            {
                "valid": {"type": "boolean"},
                "missing_inputs": {"type": "array", "items": {"type": "string"}},
            },
            success_required=[
                "valid",
                "missing_inputs",
            ],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_run_model",
        description=(
            "以固化输入与 spec 运行确定性财务模型，返回 run_id、indicators、checks、"
            "table_manifest。工具内部不调用 LLM 做算术。缺输入时返回 missing_inputs，"
            "不伪造 13 表。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string"},
                "idempotency_key": {
                    "type": "string",
                    "minLength": 8,
                    "maxLength": 256,
                    "description": "调用方生成的稳定幂等键；同键异载荷将 fail closed。",
                },
                "spec_id": {"type": "string"},
                "basis_of_estimate_id": {"type": "string"},
                "spec_hash": {"type": "string"},
                "spec": finance_spec_candidate_schema(),
                "input_revision": finance_input_schema(),
                "input_revision_id": {"type": "integer", "minimum": 0},
                "mode": {
                    "type": "string",
                    "enum": ["estimate_preview", "review_candidate"],
                    "default": "estimate_preview",
                },
                "force_recompute": {"type": "boolean", "default": False},
                "force_flat": {"type": "boolean", "default": False},
                "agent_trace_id": {"type": "string"},
                "tool_call_id": {"type": "string"},
                "valuation_date": {"type": "string", "format": "date"},
                "requested_manifest": {"type": "object"},
                "selected_scenario_id": {"type": "string", "default": "base"},
            },
            "required": ["workspace_id", "idempotency_key"],
        },
        handler=_tool_run_model,
        output_schema=_output_schema(
            {
                "run_id": {"type": ["string", "null"]},
                "missing_inputs": {"type": "array", "items": {"type": "string"}},
                "field_errors": {"type": "array", "items": {"type": "object"}},
            },
            success_required=["run_id", "missing_inputs"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_render_tables",
        description=(
            "[DEPRECATED] 已迁移到 lvke-finance-tables.tables_render。"
            "兼容期仍只从指定 run_id 渲染，不重算。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string"},
                "run_id": {"type": "string"},
                "format": {
                    "type": "string",
                    "enum": ["structured", "markdown"],
                    "default": "structured",
                },
                "include_control_tables": {"type": "boolean", "default": True},
            },
            "required": ["workspace_id", "run_id"],
        },
        handler=_tool_render_tables,
        output_schema=_output_schema(
            {
                "run_id": {"type": ["string", "null"]},
                "missing_delivery_keys": {"type": "array", "items": {"type": "string"}},
            },
            success_required=["run_id", "missing_delivery_keys"],
            deprecated=True,
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_get_run",
        description="纯查询财务 run（summary/full/tables/checks），不重算、不写库。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string"},
                "run_id": {"type": "string", "description": "省略则取最新 run"},
                "view": {
                    "type": "string",
                    "enum": ["summary", "full", "tables", "checks"],
                    "default": "summary",
                },
            },
            "required": ["workspace_id"],
        },
        handler=_tool_get_run,
        output_schema=_output_schema(
            {
                "run_id": {"type": ["string", "null"]},
                "view": {
                    "type": "string",
                    "enum": ["summary", "full", "tables", "checks"],
                },
            },
            success_required=["run_id", "view"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_build_basis_of_estimate",
        description=(
            "从已确认 FinanceSpec、EvidencePack 与 confirmed planning 对象固化不可变 BoE。"
            "每个重大输入必须含方法、选择理由、locator、hash 和证据资格。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "spec_id": {"type": "string", "minLength": 1},
                "planning_object_ids": {
                    "type": "array", "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "evidence_pack_ids": {
                    "type": "array", "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "entries": {
                    "type": "array", "minItems": 1, "maxItems": 500,
                    "items": _BOE_ENTRY_SCHEMA,
                },
                "unresolved_inputs": {"type": "array", "items": {"type": "string"}},
                "release_limitations": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "required": [
                "workspace_id", "spec_id", "planning_object_ids",
                "evidence_pack_ids", "entries", "idempotency_key"
            ],
        },
        handler=_tool_build_basis_of_estimate,
        output_schema=_output_schema(
            {
                "basis_of_estimate_id": {"type": ["string", "null"]},
                "spec_id": {"type": "string"},
                "technical_ready": {"type": "boolean"},
                "formal_ready": {"type": "boolean"},
                "replayed": {"type": "boolean"},
            },
            success_required=[
                "basis_of_estimate_id", "spec_id", "technical_ready", "formal_ready"
            ],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_get_basis_of_estimate",
        description="读取已固化 Basis of Estimate 及其输入来源、选择和 hash，不重算。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "basis_of_estimate_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "basis_of_estimate_id"],
        },
        handler=_tool_get_basis_of_estimate,
        output_schema=_output_schema(
            {
                "basis_of_estimate_id": {"type": "string"},
                "run_id": {"type": ["string", "null"]},
                "technical_ready": {"type": "boolean"},
                "formal_ready": {"type": "boolean"},
            },
            success_required=["basis_of_estimate_id", "technical_ready", "formal_ready"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_build_balance_sheet",
        description=(
            "仅从已通过勾稽的不可变 FinanceRun 派生资产负债计划。"
            "同时披露账面权益组成、计算权益残差及勾稽差额，不用残差静默补平。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "run_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "run_id"],
        },
        handler=_tool_build_balance_sheet,
        output_schema=_output_schema(
            {
                "balance_sheet_id": {"type": ["string", "null"]},
                "run_id": {"type": "string"},
                "formal_ready": {"type": "boolean"},
            },
            success_required=["balance_sheet_id", "run_id", "formal_ready"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_get_balance_sheet",
        description="读取已固化的资产负债计划，不重算。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "balance_sheet_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "balance_sheet_id"],
        },
        handler=_tool_get_balance_sheet,
        output_schema=_output_schema(
            {
                "balance_sheet_id": {"type": "string"},
                "run_id": {"type": ["string", "null"]},
                "formal_ready": {"type": "boolean"},
            },
            success_required=["balance_sheet_id", "run_id", "formal_ready"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_run_monte_carlo",
        description=(
            "以不可变 FinanceRun 为基准执行带 seed 的确定性 Monte Carlo。"
            "样本只在内存中重算，仅固化 IRR/NPV P5、P50、P95 与失败统计。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "run_id": {"type": "string", "minLength": 1},
                "distributions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": _DISTRIBUTION_SCHEMA,
                },
                "sample_count": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 10000,
                    "default": 1000,
                },
                "seed": {"type": "integer", "minimum": -2147483648, "maximum": 2147483647},
            },
            "required": ["workspace_id", "run_id", "distributions", "seed"],
        },
        handler=_tool_run_monte_carlo,
        output_schema=_output_schema(
            {
                "monte_carlo_id": {"type": ["string", "null"]},
                "run_id": {"type": "string"},
                "sample_count": {"type": "integer"},
                "field_errors": {"type": "array", "items": {"type": "object"}},
            },
            success_required=["monte_carlo_id", "run_id", "sample_count"],
        ),
        annotations=write_deterministic,
    )
    server.register_tool(
        name="finance_get_monte_carlo",
        description="读取已固化的 Monte Carlo 分位数摘要与分布清单，不重算。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "monte_carlo_id": {"type": "string", "minLength": 1},
            },
            "required": ["workspace_id", "monte_carlo_id"],
        },
        handler=_tool_get_monte_carlo,
        output_schema=_output_schema(
            {
                "monte_carlo_id": {"type": "string"},
                "run_id": {"type": ["string", "null"]},
                "formal_ready": {"type": "boolean"},
            },
            success_required=["monte_carlo_id", "run_id", "formal_ready"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_list_analyses",
        description="在显式工作区内分页列出高级财务分析 Resource。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "resource_type": {
                    "type": "string",
                    "enum": ["all", "balance_sheet", "monte_carlo", "basis_of_estimate", "fact_pack"],
                    "default": "all",
                },
                "cursor": {"type": "string", "maxLength": 8192},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "required": ["workspace_id"],
        },
        handler=_tool_list_analyses,
        output_schema=_output_schema(
            {
                "analysis_count": {"type": "integer"},
                "next_cursor": {"type": ["string", "null"]},
            },
            success_required=["analysis_count", "next_cursor"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_read_analysis_resource",
        description="按 URI 读取同工作区下的资产负债或 Monte Carlo 不可变 Resource。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "uri": {
                    "type": "string",
                    "pattern": r"^lvke://finance-model/workspaces/",
                    "maxLength": 8192,
                },
            },
            "required": ["workspace_id", "uri"],
        },
        handler=_tool_read_analysis_resource,
        output_schema=_output_schema(
            {
                "object_id": {"type": "string"},
                "content_hash": {"type": "string"},
                "basis_hash": {"type": "string"},
            },
            success_required=["object_id", "content_hash", "basis_hash"],
        ),
        annotations=read_closed,
    )
    server.register_tool(
        name="finance_generate_package",
        description=(
            "[DEPRECATED] 巨型组合入口；新工作流应显式调用 finance_run_model → "
            "lvke-finance-tables.tables_render，不隐藏跨层绑定。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["estimate_preview", "review_candidate"],
                    "default": "estimate_preview",
                },
                "force_refresh_spec": {"type": "boolean", "default": False},
                "force_recompute": {"type": "boolean", "default": False},
                "force_flat": {"type": "boolean", "default": False},
                "confirmed_spec": {
                    "type": "object",
                    "description": "人工确认并冻结的 FinanceSpec；提供后 package 不再调用 LLM 改写",
                },
                "agent_trace_id": {"type": "string"},
                "tool_call_id": {"type": "string"},
                "valuation_date": {"type": "string", "format": "date"},
                "requested_manifest": {"type": "object"},
                "selected_scenario_id": {"type": "string", "default": "base"},
            },
            "required": ["workspace_id"],
        },
        handler=_tool_generate_package,
        output_schema=_output_schema(
            {
                "run_id": {"type": ["string", "null"]},
                "stage": {"type": ["string", "null"]},
            },
            success_required=["run_id", "stage"],
            deprecated=True,
        ),
        annotations=write_nonidempotent,
    )
    server.register_tool(
        name="finance_import_vendor_review",
        description=(
            "导入甲方原生 xlsx 为只读公式参考档，检测本金重复/手工IRR/僵尸公式，"
            "用我方确定性模型重算并生成双轨对照、阻断预警和复核报告。"
            "甲方原值永不作为对外数字源。"
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string"},
                "xlsx_path": {"type": "string", "description": "甲方 .xlsx/.xlsm 路径"},
                "valuation_date": {
                    "type": "string",
                    "format": "date",
                    "description": "审查估值日；省略时使用调用日",
                },
                "force_recompute": {"type": "boolean", "default": False},
                "cohort_xlsx_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选同批工作簿，用于识别跨模板重复僵尸公式",
                },
            },
            "required": ["workspace_id", "xlsx_path"],
        },
        handler=_tool_import_vendor_review,
        output_schema=_output_schema(
            {
                "reference_id": {"type": ["string", "null"]},
                "review_passed": {"type": "boolean"},
                "run_id": {"type": ["string", "null"]},
                "missing_inputs": {"type": "array", "items": {"type": "string"}},
            },
            success_required=[
                "reference_id",
                "review_passed",
                "run_id",
                "missing_inputs",
            ],
        ),
        annotations=write_deterministic,
    )

    def read_run_resource(uri: str):
        analysis_record = _resolve_analysis_resource(uri)
        if analysis_record is not None:
            return ReadResourceContents(
                json.dumps(analysis_record, ensure_ascii=False, indent=2, default=str),
                "application/json",
            )
        spec_record = SPEC_STORE.resolve_uri(uri)
        if spec_record is not None:
            return ReadResourceContents(
                json.dumps(spec_record, ensure_ascii=False, indent=2, default=str),
                "application/json",
            )
        fact_pack_record = FACT_PACK_STORE.resolve_uri(uri)
        if fact_pack_record is not None:
            return ReadResourceContents(
                json.dumps(fact_pack_record, ensure_ascii=False, indent=2, default=str),
                "application/json",
            )
        prefix = "lvke://finance-model/workspaces/"
        if not uri.startswith(prefix):
            return None
        parts = uri[len(prefix) :].split("/")
        if len(parts) != 3 or parts[1] != "runs":
            return None
        try:
            from lvke_mcp.runtime.storage import require_safe_id
            from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

            workspace_id = require_safe_id(parts[0], "workspace_id")
            run_id = require_safe_id(parts[2], "run_id")
            value = get_workspace_finance_run(
                workspace_id,
                run_id=run_id,
                view="full",
            )
        except Exception:  # noqa: BLE001
            return None
        if not value.get("available"):
            return None
        return ReadResourceContents(
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
            "application/json",
        )

    server.register_resource_provider(lambda: [], read_run_resource)
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
