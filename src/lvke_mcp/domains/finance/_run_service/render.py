"""十三表渲染。"""

from __future__ import annotations

from typing import Any


from .base import (
    DELIVERY_TABLE_KEYS,
    TEMPLATE_VERSION,
    _ensure_workspace,
    _table_manifest,
    compute_table_bundle_hash,
)

from .query import (
    get_workspace_finance_run,
)

from .spec_prepare import (
    _nonnegative_cost_issues,
)


def render_workspace_finance_tables(
    workspace_id: str,
    run_id: str = "",
    *,
    format: str = "structured",  # noqa: A002 - 与工具契约字段同名
    include_control_tables: bool = True,
) -> dict[str, Any]:
    """只从指定 run 渲染 13 表；不重算。"""
    _ensure_workspace(workspace_id)
    fin = get_workspace_finance_run(workspace_id, run_id=run_id, view="full")
    if not fin.get("available") and not fin.get("result", {}).get("available"):
        # get 可能直接返回 fin 本体
        body = fin.get("result") if "result" in fin else fin
        if not (body or {}).get("available"):
            return {
                "ok": False,
                "error": "run_unavailable",
                "message": "指定 run 不可用或未成功计算，无法渲染 13 表",
                "run_id": run_id or fin.get("run_id"),
                "missing_inputs": (body or {}).get("missing_inputs") or [],
            }

    body = fin.get("result") if isinstance(fin.get("result"), dict) and fin.get("result") else fin
    nonnegative_issues = _nonnegative_cost_issues(body)
    if nonnegative_issues:
        return {
            "ok": False,
            "error": "negative_operating_cost",
            "message": "历史财务运行含负现金经营成本或负工资，拒绝渲染十三表",
            "run_id": body.get("run_id") or run_id,
            "field_errors": nonnegative_issues,
            "missing_inputs": [
                "annual_operating_cost_wan",
                "cost_items",
                "operating_cost_by_year",
            ],
        }
    tables = body.get("tables") or {}
    if not tables:
        return {
            "ok": False,
            "error": "tables_missing",
            "message": "run 中无 tables 快照，无法渲染",
            "run_id": body.get("run_id") or run_id,
        }

    rid = body.get("run_id") or run_id
    delivery = {k: tables.get(k) for k in DELIVERY_TABLE_KEYS if k in tables}
    missing_keys = [k for k in DELIVERY_TABLE_KEYS if k not in tables]
    control = {}
    if include_control_tables:
        for k, v in tables.items():
            if k not in DELIVERY_TABLE_KEYS:
                control[k] = v

    out: dict[str, Any] = {
        "ok": True,
        "run_id": rid,
        "template_version": body.get("template_version") or TEMPLATE_VERSION,
        "table_bundle_hash": body.get("table_bundle_hash") or compute_table_bundle_hash(tables),
        "table_manifest": _table_manifest(body, rid),
        "delivery_keys": list(DELIVERY_TABLE_KEYS),
        "missing_delivery_keys": missing_keys,
        "tables": delivery if not include_control_tables else tables,
        "control_tables": control,
    }
    if format == "markdown":
        try:
            from lvke_mcp.domains.finance.finance_model import finance_tables_markdown

            out["markdown"] = finance_tables_markdown(body)
        except Exception as exc:  # noqa: BLE001
            out["markdown_error"] = str(exc)[:200]
    return out
