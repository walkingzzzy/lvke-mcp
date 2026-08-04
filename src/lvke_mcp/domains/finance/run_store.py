"""MCP 自有 finance run 存储（hermes audit_db 的 JSON 替代）。

MCP 独立化后不引入 sqlite：run 记录以 JSON 文件落在 MCP 自有
workspace 存储（``runtime.workspace.workspace_root``），字段语义与
历史 audit_db 的 calculation_runs 视图对齐，保证
``run_service`` 消费面（spec_json / review_status / results / snapshot 等）
零改动可读。

单租户：``tenant_scope`` 为 no-op，``current_tenant_id`` 恒为 "default"。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from lvke_mcp.runtime.workspace import workspace_root

DEFAULT_TENANT_ID = "default"

# 元素映射（与 hermes audit_db 的 _INDICATOR_MAP/_INVESTMENT_MAP/_FUNDING_MAP 一致）
_INDICATOR_MAP: dict[str, tuple[str, str]] = {
    "revenue": ("annual_revenue", "万元"),
    "op_cost": ("annual_total_cost", "万元"),
    "net_profit": ("annual_net_profit", "万元"),
    "project_irr_pct": ("project_irr", "%"),
    "npv_wan": ("npv", "万元"),
    "static_payback_years": ("static_payback", "年"),
    "dynamic_payback_years": ("dynamic_payback", "年"),
    "bep_pct": ("bep", "%"),
}
_INVESTMENT_MAP: dict[str, tuple[str, str]] = {
    "total": ("total_investment", "万元"),
    "construction": ("construction_investment", "万元"),
    "interest": ("interest_during_construction", "万元"),
    "working_capital": ("working_capital", "万元"),
    "fixed_asset": ("fixed_asset", "万元"),
}
_FUNDING_MAP: dict[str, tuple[str, str]] = {
    "capital": ("equity_capital", "万元"),
    "loan": ("loan", "万元"),
    "subsidy": ("subsidy", "万元"),
}

# 报告正文映射元素（与 hermes audit_db._REPORT_KEY_ELEMENTS 对齐的子集语义）
_REPORT_KEY_ELEMENTS: list[tuple[str, str, str]] = [
    ("total_investment", "investment", "total"),
    ("construction_investment", "investment", "construction"),
    ("interest_during_construction", "investment", "interest"),
    ("working_capital", "investment", "working_capital"),
    ("equity_capital", "funding", "capital"),
    ("loan", "funding", "loan"),
    ("subsidy", "funding", "subsidy"),
    ("annual_revenue", "indicators", "revenue"),
    ("annual_total_cost", "indicators", "op_cost"),
    ("annual_net_profit", "indicators", "net_profit"),
    ("project_irr", "indicators", "project_irr_pct"),
    ("npv", "indicators", "npv_wan"),
    ("static_payback", "indicators", "static_payback_years"),
    ("dynamic_payback", "indicators", "dynamic_payback_years"),
    ("bep", "indicators", "bep_pct"),
]


def current_tenant_id() -> str:
    """MCP 单租户恒为 default。"""
    return DEFAULT_TENANT_ID


@contextmanager
def tenant_scope(tenant_id: str | None = None) -> Iterator[str]:
    """单租户 no-op 作用域（保持 run_service 装饰器调用面不变）。"""
    yield current_tenant_id()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _run_dir(workspace_id: str) -> Path:
    root = workspace_root(workspace_id) / "finance_runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_path(workspace_id: str, run_id: str) -> Path:
    return _run_dir(workspace_id) / f"{run_id}.json"


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 损坏记录视为不存在
        return None
    return data if isinstance(data, dict) else None


def _write_record(path: Path, record: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, default=str),
        encoding="utf-8",
    )
    tmp.replace(path)  # 同目录原子替换，避免并发读半文件


def _list_records(workspace_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in _run_dir(workspace_id).glob("*.json"):
        record = _read_record(path)
        if record and record.get("run_id"):
            records.append(record)
    return records


def load_run(workspace_id: str, run_id: str) -> dict[str, Any]:
    """读回一次运行的审计视图（元数据 + results + assumptions + mappings）。"""
    if not run_id:
        return {}
    record = _read_record(_run_path(workspace_id, run_id))
    if record is None:
        return {}
    return _view_from_record(record)


def _view_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """把存储记录投影为 run_service 消费的审计视图（对齐 audit_db.load_run）。"""
    view = dict(record)
    view.pop("_result_snapshot", None)
    view.setdefault("consistency_ok", False)
    view.setdefault("review_status", "draft")
    view.setdefault("consistency", [])
    view.setdefault("results", [])
    view.setdefault("assumptions", [])
    view.setdefault("report_mappings", [])
    view.setdefault("issues", [])
    view.setdefault("formulas", [])
    view.setdefault("scenarios", [])
    view.setdefault("dependencies", [])
    view.setdefault("input_elements", [])
    view.setdefault("migration_events", [])
    view.setdefault("review_actions", [])
    view.setdefault("stale_reasons", [])
    view.setdefault("field_source_ledger", [])
    view.setdefault("period_count", 0)
    return view


def latest_run(workspace_id: str) -> dict[str, Any]:
    """该工作区最近一次运行的审计视图（新→旧）。"""
    records = _list_records(workspace_id)
    if not records:
        return {}
    records.sort(key=lambda r: (str(r.get("started_at") or ""), str(r.get("run_id") or "")))
    return _view_from_record(records[-1])


def find_run_by_idempotency_key(workspace_id: str, idempotency_key: str) -> dict[str, Any]:
    """按幂等键查找已有 run；返回 {run_id, review_status, result?} 或空 dict。"""
    if not idempotency_key:
        return {}
    records = [
        r for r in _list_records(workspace_id)
        if str(r.get("idempotency_key") or "") == str(idempotency_key)
    ]
    if not records:
        return {}
    records.sort(key=lambda r: (str(r.get("started_at") or ""), str(r.get("run_id") or "")))
    record = records[-1]
    out: dict[str, Any] = {
        "run_id": record.get("run_id"),
        "review_status": str(record.get("review_status") or "draft"),
        "input_hash": record.get("input_hash"),
        "spec_hash": record.get("spec_hash"),
        "model_version": record.get("model_version"),
        "template_version": record.get("template_version"),
        "table_bundle_hash": record.get("table_bundle_hash"),
        "assurance_level": record.get("assurance_level"),
        "manifest_hash": record.get("manifest_hash"),
        "policy_version": record.get("policy_version"),
        "industry_profile_version": record.get("industry_profile_version"),
        "gate_version": record.get("gate_version"),
        "valuation_date": record.get("valuation_date"),
        "selected_scenario_id": record.get("selected_scenario_id"),
    }
    snapshot = record.get("_result_snapshot")
    if isinstance(snapshot, dict):
        out["result"] = snapshot
    return out


def load_result_snapshot(workspace_id: str, run_id: str) -> Optional[dict[str, Any]]:
    """读取 run 的完整 result 快照（13 表只读重放）。无则 None。"""
    if not run_id:
        return None
    record = _read_record(_run_path(workspace_id, run_id))
    if record is None:
        return None
    snapshot = record.get("_result_snapshot")
    return snapshot if isinstance(snapshot, dict) else None


def get_approved_run(workspace_id: str) -> dict[str, Any]:
    """最新已批准运行（MCP 无审批流程，恒空）。"""
    records = [
        r for r in _list_records(workspace_id)
        if str(r.get("review_status") or "") == "approved"
    ]
    if not records:
        return {}
    records.sort(
        key=lambda r: (
            str(r.get("approved_at") or r.get("finished_at") or r.get("started_at") or ""),
            str(r.get("run_id") or ""),
        )
    )
    return _view_from_record(records[-1])


def record_run(
    workspace_id: str,
    fin: dict[str, Any],
    *,
    sources: Optional[list[dict[str, Any]]] = None,
    model_version: str = "finance_model.v1",
    input_hash: str = "",
    idempotency_key: str = "",
    template_version: str = "",
    table_bundle_hash: str = "",
    agent_trace_id: str = "",
    tool_call_id: str = "",
    input_revision: int = 0,
    result_snapshot: Optional[dict[str, Any]] = None,
    force_new: bool = False,
) -> Optional[str]:
    """把一次 compute_financials 结果落 run 存储，返回 run_id（不可用则 None）。

    与 audit_db.record_run 语义一致：幂等键命中且非 force_new 时复用原 run_id。
    """
    if not fin or not fin.get("available"):
        return None

    if idempotency_key and not force_new:
        existing = find_run_by_idempotency_key(workspace_id, idempotency_key)
        if existing and existing.get("run_id"):
            return existing["run_id"]

    run_id = _gen_id("run")
    now = _now()
    inv = fin.get("investment") or {}
    fund = fin.get("funding") or {}
    ind = fin.get("indicators") or {}

    # 勾稽摘要（对齐 record_run 的 consistency_ok 判定）
    consistency: list[dict[str, Any]] = []
    try:
        from lvke_mcp.domains.finance.finance_model import check_consistency

        consistency = check_consistency(fin) or []
    except Exception:  # noqa: BLE001
        consistency = []
    blocking_fail = any(
        (not c.get("ok")) and bool(c.get("blocking", True))
        for c in consistency
        if isinstance(c, dict)
    )
    consistency_ok = bool(consistency) and not blocking_fail

    spec_obj = fin.get("spec")
    spec_json = (
        json.dumps(spec_obj, ensure_ascii=False, sort_keys=True, default=str)
        if spec_obj
        else None
    )
    spec_hash = fin.get("spec_hash") or None
    manifest = fin.get("model_manifest") or {}

    results: list[dict[str, Any]] = []

    def _put_result(code: str, value: Any, unit: str, table_code: str) -> None:
        if value is None:
            return
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        results.append({
            "element_code": code,
            "table_code": table_code,
            "period": "aggregate",
            "value": v,
            "unit": unit,
            "formula_id": None,
        })

    for key, (code, unit) in _INVESTMENT_MAP.items():
        _put_result(code, inv.get(key), unit, "investment")
    for key, (code, unit) in _FUNDING_MAP.items():
        _put_result(code, fund.get(key), unit, "funding")
    for key, (code, unit) in _INDICATOR_MAP.items():
        _put_result(code, ind.get(key), unit, "indicators")

    assumptions = [
        {"element": "", "note": str(a), "method": "model_default"}
        for a in (fin.get("assumptions") or [])
    ]
    snap = result_snapshot if result_snapshot is not None else fin

    record: dict[str, Any] = {
        "run_id": run_id,
        "workspace_id": str(workspace_id),
        "model_version": model_version,
        "invest_type": fin.get("invest_type", ""),
        "industry": fin.get("industry", ""),
        "available": True,
        "started_at": now,
        "finished_at": now,
        "consistency_ok": consistency_ok,
        "consistency": consistency,
        "spec_json": spec_json,
        "spec_hash": spec_hash,
        "review_status": "draft",
        "approved_at": None,
        "approved_by": None,
        "parent_run_id": None,
        "input_hash": input_hash or fin.get("input_hash") or "",
        "idempotency_key": idempotency_key or "",
        "template_version": template_version or fin.get("template_version") or "",
        "input_revision": int(input_revision or 0),
        "table_bundle_hash": table_bundle_hash or fin.get("table_bundle_hash") or "",
        "agent_trace_id": agent_trace_id or fin.get("agent_trace_id") or "",
        "tool_call_id": tool_call_id or fin.get("tool_call_id") or "",
        "assurance_level": fin.get("assurance_level") or "estimate_preview",
        "manifest_json": (
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, default=str)
            if manifest else None
        ),
        "manifest_hash": fin.get("manifest_hash") or None,
        "policy_version": manifest.get("policy_version") or None,
        "industry_profile_version": manifest.get("industry_profile_version") or None,
        "gate_version": manifest.get("gate_version") or None,
        "valuation_date": fin.get("valuation_date") or None,
        "selected_scenario_id": fin.get("selected_scenario_id") or "base",
        "stale_reasons": list(fin.get("stale_reasons") or []),
        "field_source_ledger": list(fin.get("field_source_ledger") or []),
        "results": results,
        "assumptions": assumptions,
        "report_mappings": [],
        "formulas": [],
        "issues": [],
        "period_count": 0,
        "scenarios": [],
        "dependencies": [],
        "input_elements": [],
        "migration_events": [],
        "review_actions": [],
        "reference_review_status": "n_a",
        "business_review_status": "pending",
        "_result_snapshot": snap,
    }
    _write_record(_run_path(workspace_id, run_id), record)
    return run_id


def map_key_report_values(
    workspace_id: str,
    run_id: str,
    fin: dict[str, Any],
    *,
    report_file: str = "",
    section: str = "",
    require_approved: bool = False,
) -> int:
    """把关键正文数字映射到已有 run（MCP 版记录到 run 文件，供 report_mappings 回读）。"""
    if not fin or not run_id:
        return 0
    inv = fin.get("investment") or {}
    fund = fin.get("funding") or {}
    ind = fin.get("indicators") or {}
    _part = {"investment": inv, "funding": fund, "indicators": ind}
    values: list[tuple[str, str]] = []
    for element_code, part, key in _REPORT_KEY_ELEMENTS:
        val = _part.get(part, {}).get(key)
        if val is None:
            continue
        values.append((element_code, str(val)))
    if not values:
        return 0

    path = _run_path(workspace_id, run_id)
    record = _read_record(path)
    if record is None:
        return 0
    if require_approved and str(record.get("review_status") or "draft") != "approved":
        return 0
    existing = {m.get("element_code") for m in (record.get("report_mappings") or [])}
    now = _now()
    mappings = list(record.get("report_mappings") or [])
    added = 0
    for element_code, rendered_value in values:
        if element_code in existing:
            continue
        mappings.append({
            "mapping_id": _gen_id("map"),
            "run_id": run_id,
            "report_file": report_file,
            "section": section,
            "element_code": element_code,
            "result_id": None,
            "rendered_value": rendered_value,
            "created_at": now,
        })
        existing.add(element_code)
        added += 1
    record["report_mappings"] = mappings
    _write_record(path, record)
    return added


# =====================================================================
# vendor reference 存储（audit_db.vendor_references 的 JSON 替代）
# =====================================================================

_REFERENCE_REVIEW_STATES = {
    "pending", "approved", "n_a", "converged", "out_of_tolerance",
    "explain_pending", "rejected",
}
_BUSINESS_REVIEW_STATES = {"pending", "approved", "n_a", "rejected"}


def _vendor_dir(workspace_id: str) -> Path:
    root = workspace_root(workspace_id) / "vendor_references"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _vendor_path(workspace_id: str, reference_id: str) -> Path:
    return _vendor_dir(workspace_id) / f"{reference_id}.json"


def record_vendor_reference(
    workspace_id: str,
    reference_pack: dict[str, Any],
) -> dict[str, Any]:
    """持久化不可变甲方工作簿快照；同 workspace+workbook_sha256 幂等复用。"""
    if not isinstance(reference_pack, dict) or not reference_pack:
        raise ValueError("reference_pack must be a non-empty dict")
    source = reference_pack.get("source") or {}
    payload = json.dumps(reference_pack, ensure_ascii=False, sort_keys=True, default=str)
    workbook_hash = str(
        source.get("workbook_sha256")
        or reference_pack.get("workbook_sha256")
        or hashlib.sha256(payload.encode("utf-8")).hexdigest()
    )
    workbook_path = str(source.get("path") or reference_pack.get("workbook_path") or "")
    title = str(source.get("workbook_name") or Path(workbook_path).name or "甲方计算表")
    grade = str(reference_pack.get("reliability_grade") or "C").upper()
    now = _now()
    reference_id = _gen_id("vref")
    reused_record: Optional[dict[str, Any]] = None
    for path in _vendor_dir(workspace_id).glob("*.json"):
        record = _read_record(path)
        if (
            record
            and str(record.get("workspace_id") or "") == str(workspace_id)
            and str(record.get("workbook_sha256") or "") == workbook_hash
        ):
            reference_id = str(record["reference_id"])
            reused_record = record
            break
    if reused_record:
        return {
            "reference_id": reference_id,
            "source_id": str(reused_record.get("source_id") or ""),
            "created_at": str(reused_record.get("created_at") or now),
            "reused": True,
        }
    source_id = _gen_id("src")
    _write_record(_vendor_path(workspace_id, reference_id), {
        "reference_id": reference_id,
        "workspace_id": str(workspace_id),
        "source_id": source_id,
        "workbook_path": workbook_path,
        "workbook_sha256": workbook_hash,
        "reliability_grade": grade,
        "title": title,
        "reference_pack": reference_pack,
        "run_ids": [],
        "created_at": now,
    })
    return {
        "reference_id": reference_id,
        "source_id": source_id,
        "created_at": now,
        "reused": False,
    }


def load_vendor_reference(workspace_id: str, reference_id: str) -> dict[str, Any]:
    """读回一个不可变甲方快照及其元数据。"""
    if not reference_id:
        return {}
    record = _read_record(_vendor_path(workspace_id, reference_id))
    if record is None:
        return {}
    return record


def _vendor_sheet_decisions(
    workspace_id: str,
    reference_id: str,
) -> dict[str, Any]:
    """从快照 sheet_map 计算裁决状态。

    MCP 无人工 mapped/ignored 裁决入口：非空表一律 pending，formal_ok 恒为
    False（与「无批准入口 → approved 恒不通过」的产品语义一致）。
    """
    record = load_vendor_reference(workspace_id, reference_id)
    if not record:
        return {
            "ok": False,
            "error": "reference_not_found",
            "workspace_id": workspace_id,
            "reference_id": reference_id,
        }
    pack = record.get("reference_pack") or {}
    sheets: list[dict[str, Any]] = []
    pending: list[str] = []
    ignored: list[str] = []
    mapped: list[str] = []
    current_delivery_owners: dict[str, list[str]] = {}
    for sheet_name, sheet in (pack.get("sheets") or {}).items():
        mapping = sheet.get("mapping") or {}
        values = sheet.get("values") or {}
        formulas = sheet.get("formulas") or {}
        non_empty = any(
            value is not None and (not isinstance(value, str) or value.strip())
            for value in values.values()
        ) or bool(formulas)
        state = "not_required" if not non_empty else "pending"
        effective_business = str(mapping.get("candidate_business") or mapping.get("business") or "")
        item = {
            "sheet_name": str(sheet_name),
            "non_empty": non_empty,
            "candidate": {
                "business": effective_business,
                "mapped": bool(mapping.get("mapped")),
                "confidence": mapping.get("confidence"),
                "mapping_rule": mapping.get("mapping_rule") or "",
                "rule_hits": list(mapping.get("rule_hits") or []),
                "conflict_reasons": list(mapping.get("conflict_reasons") or []),
                "delivery_no": mapping.get("delivery_no") or "",
                "template_id": mapping.get("template_id"),
            },
            "decision_status": state,
            "effective_business": effective_business if state == "mapped" else "",
            "decision": None,
        }
        sheets.append(item)
        if state == "pending":
            pending.append(str(sheet_name))
        elif state == "ignored":
            ignored.append(str(sheet_name))
        elif state == "mapped":
            mapped.append(str(sheet_name))
            try:
                from lvke_mcp.servers.lvke_templates.catalog import map_vendor_sheet

                canonical = map_vendor_sheet(business=effective_business) or {}
            except Exception:  # noqa: BLE001
                canonical = {}
            delivery_no = str(canonical.get("delivery_no") or "")
            if delivery_no:
                current_delivery_owners.setdefault(delivery_no, []).append(str(sheet_name))

    conflicts = [
        {
            "code": "duplicate_delivery_mapping",
            "delivery_no": delivery_no,
            "sheet_names": owners,
        }
        for delivery_no, owners in sorted(current_delivery_owners.items())
        if len(owners) > 1
    ]
    current_payload = [
        {
            "sheet_name": item["sheet_name"],
            "decision": item["decision_status"],
            "business": item["effective_business"],
            "decision_id": None,
        }
        for item in sheets
        if item["non_empty"]
    ]
    decision_hash = "sha256:" + hashlib.sha256(
        json.dumps(
            current_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "reference_id": reference_id,
        "complete": not pending and not conflicts,
        "formal_ok": not pending and not conflicts,
        "decision_revision": 0,
        "decision_hash": decision_hash,
        "sheet_count": len(sheets),
        "non_empty_sheet_count": sum(1 for item in sheets if item["non_empty"]),
        "mapped_sheet_count": len(mapped),
        "ignored_sheet_count": len(ignored),
        "pending_sheet_count": len(pending),
        "pending_sheets": pending,
        "conflicts": conflicts,
        "sheets": sheets,
    }


def bind_vendor_reference_run(
    workspace_id: str,
    reference_id: str,
    run_id: str,
) -> dict[str, Any]:
    """绑定甲方快照与引擎 run；已绑定幂等复用；approved run 不可变。"""
    record = load_vendor_reference(workspace_id, reference_id)
    if not record:
        return {"ok": False, "error": "reference_not_found", "reference_id": reference_id}
    run_path = _run_path(workspace_id, run_id)
    run_record = _read_record(run_path)
    if run_record is None:
        return {"ok": False, "error": "run_not_found", "run_id": run_id}
    if str(run_record.get("review_status") or "") == "approved":
        return {"ok": False, "error": "approved_run_immutable", "run_id": run_id}
    run_ids = list(record.get("run_ids") or [])
    reused = str(run_id) in run_ids
    if not reused:
        run_ids.append(str(run_id))
        record["run_ids"] = run_ids
        _write_record(_vendor_path(workspace_id, reference_id), record)
        run_record["reference_review_status"] = "pending"
        run_record["business_review_status"] = "pending"
        _write_record(run_path, run_record)
    decision_status = _vendor_sheet_decisions(workspace_id, reference_id)
    return {
        "ok": True,
        "reference_id": reference_id,
        "run_id": run_id,
        "sheet_decisions": decision_status,
        "idempotent_replay": reused,
    }


def record_model_issues(
    workspace_id: str,
    run_id: str,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """把 review verdict 追加到 run 记录 issues（open 项按 rule+detail 去重）。"""
    if not isinstance(issues, list):
        raise ValueError("issues must be a list")
    path = _run_path(workspace_id, run_id)
    record = _read_record(path)
    if record is None:
        return {"ok": False, "error": "run_not_found", "run_id": run_id}
    now = _now()
    existing = list(record.get("issues") or [])
    inserted: list[str] = []
    reused: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        rule = str(issue.get("rule") or issue.get("code") or "").strip()
        if not rule:
            continue
        detail = str(issue.get("detail") or issue.get("message") or "").strip()
        duplicate = next(
            (
                item for item in existing
                if isinstance(item, dict)
                and str(item.get("rule") or "") == rule
                and str(item.get("detail") or "") == detail
                and str(item.get("status") or "open") == "open"
            ),
            None,
        )
        if duplicate:
            reused.append(str(duplicate.get("issue_id") or ""))
            continue
        issue_id = _gen_id("iss")
        ok_value = bool(issue.get("ok", False))
        status = str(issue.get("status") or ("closed" if ok_value else "open"))
        existing.append({
            "issue_id": issue_id,
            "run_id": run_id,
            "rule": rule,
            "severity": str(issue.get("severity") or "warning"),
            "blocking": bool(issue.get("blocking")),
            "ok": ok_value,
            "detail": detail,
            "status": status,
            "close_note": str(issue.get("close_note") or ""),
            "created_at": now,
        })
        inserted.append(issue_id)
    record["issues"] = existing
    _write_record(path, record)
    return {
        "ok": True,
        "run_id": run_id,
        "inserted": inserted,
        "reused": reused,
        "count": len(inserted) + len(reused),
    }


def _update_run_review_gates(
    workspace_id: str,
    run_id: str,
    *,
    reference_review_status: str | None = None,
    business_review_status: str | None = None,
    actor: str = "",
    note: str = "",
) -> dict[str, Any]:
    """设置参考轨复核 / 业务复核状态（MCP 版，仅显式字段）。"""
    updates: list[tuple[str, str]] = []
    if reference_review_status is not None:
        if reference_review_status not in _REFERENCE_REVIEW_STATES:
            return {"ok": False, "error": "invalid_reference_review_status",
                    "value": reference_review_status}
        updates.append(("reference_review_status", reference_review_status))
    if business_review_status is not None:
        if business_review_status not in _BUSINESS_REVIEW_STATES:
            return {"ok": False, "error": "invalid_business_review_status",
                    "value": business_review_status}
        updates.append(("business_review_status", business_review_status))
    if not updates:
        return {"ok": False, "error": "no_updates"}
    actor = str(actor or "").strip()
    if business_review_status == "approved" and not actor:
        return {"ok": False, "error": "authenticated_actor_required", "run_id": run_id}
    if reference_review_status == "approved" and not actor:
        return {"ok": False, "error": "authenticated_actor_required", "run_id": run_id}
    path = _run_path(workspace_id, run_id)
    record = _read_record(path)
    if record is None:
        return {"ok": False, "error": "run_not_found", "run_id": run_id}
    if str(record.get("review_status") or "") == "approved":
        return {"ok": False, "error": "approved_run_immutable", "run_id": run_id}
    for col, val in updates:
        record[col] = val
    if actor:
        record["review_actions"] = list(record.get("review_actions") or []) + [{
            "action_id": _gen_id("act"),
            "run_id": run_id,
            "actor": actor,
            "action": "reference_review" if reference_review_status is not None else "business_review",
            "payload_json": json.dumps({"note": note}, ensure_ascii=False),
            "created_at": _now(),
        }]
    _write_record(path, record)
    return {"ok": True, "run_id": run_id, **dict(updates)}


def set_reference_review_status(
    workspace_id: str,
    run_id: str,
    status: str,
    *,
    actor: str = "",
    note: str = "",
    request_id: str = "",
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """兼容包装：vendor/reference-track 编排调用。"""
    return _update_run_review_gates(
        workspace_id, run_id, reference_review_status=str(status or "pending"),
        actor=actor, note=note,
    )


def set_business_review_status(
    workspace_id: str,
    run_id: str,
    status: str,
    *,
    actor: str = "",
    note: str = "",
    request_id: str = "",
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """兼容包装：业务差异裁决调用。"""
    return _update_run_review_gates(
        workspace_id, run_id, business_review_status=str(status or "pending"),
        actor=actor, note=note,
    )