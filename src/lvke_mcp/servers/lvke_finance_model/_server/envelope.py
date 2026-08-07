"""幂等记录、URI 构造、信封与输入归一化基座。"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any


from lvke_mcp.adapters.finance_model_repository import IDEMPOTENCY_STORE, SPEC_STORE
from lvke_mcp.runtime.responses import err, ok
from lvke_mcp.domains.finance.parameter_resolver import finance_input_schema

from .schemas import (
    SERVER_NAME,
    logger,
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
