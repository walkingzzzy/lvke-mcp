"""正式版升级链：在首版基础上创建新版本，保留结构化差异。

职责：
- 接受前序 FinanceRun ID，创建新 Run 并链接 parent_run_id
- 生成结构化 diff（字段/假设/指标/标准覆盖/工件哈希）
- 不修改或覆盖前序版本
- formal_grade 是版本等级，不是生成操作的前置许可
"""

from __future__ import annotations

from typing import Any

from lvke_mcp.domains.finance.run_store import load_run, record_run


def promote_to_formal(
    workspace_id: str,
    *,
    prior_run_id: str,
    new_fin: dict[str, Any],
    validation_report: dict[str, Any] | None = None,
    idempotency_key: str = "",
    model_version: str = "",
    template_version: str = "",
    input_hash: str = "",
    table_bundle_hash: str = "",
    agent_trace_id: str = "",
    tool_call_id: str = "",
) -> dict[str, Any]:
    """Create a formal-grade FinanceRun linked to a prior run.

    Never overwrites or modifies the prior run.  Produces a structured diff
    between the prior and new runs.

    Args:
        workspace_id: target workspace
        prior_run_id: the previous run to link from (must exist)
        new_fin: the new finance computation result dict
        validation_report: optional pre-computed validation report
        idempotency_key: passed through to record_run
        model_version / template_version / input_hash / table_bundle_hash /
        agent_trace_id / tool_call_id: passed through to record_run

    Returns:
        dict with:
            - success: True
            - run_id: the new run ID
            - prior_run_id: the linked parent run ID
            - formal_grade: "v2" (or higher if prior is already upgraded)
            - diff: structured diff dict
            - validation_report: passed-through or empty dict
    """

    # 1. Load prior run
    prior = load_run(workspace_id, prior_run_id)
    if not prior:
        return _failure(
            "prior_run_not_found",
            f"前序 run {prior_run_id} 不存在",
            prior_run_id=prior_run_id,
        )

    prior_run_id_val = str(prior.get("run_id") or prior_run_id)

    # 2. Compute formal grade
    prior_formal_grade = str(prior.get("formal_grade") or "v1")
    # Increment grade: v1 -> v2, v2 -> v3, etc.
    try:
        prior_num = int(prior_formal_grade.lstrip("v"))
        new_num = prior_num + 1
    except (ValueError, AttributeError):
        new_num = 2
    formal_grade = f"v{new_num}"

    # 3. Create the new run linked to prior
    new_run_id = record_run(
        workspace_id,
        new_fin,
        sources=None,
        model_version=model_version or str(prior.get("model_version") or "finance_model.v1"),
        input_hash=input_hash or str(prior.get("input_hash") or ""),
        idempotency_key=idempotency_key or "",
        template_version=template_version or str(prior.get("template_version") or ""),
        table_bundle_hash=table_bundle_hash or str(prior.get("table_bundle_hash") or ""),
        agent_trace_id=agent_trace_id or "",
        tool_call_id=tool_call_id or "",
        input_revision=int(prior.get("input_revision") or 0) + 1,
        result_snapshot=None,
        force_new=True,
        parent_run_id=prior_run_id_val,
    )
    if not new_run_id:
        return _failure(
            "run_creation_failed",
            "创建新 run 失败",
            prior_run_id=prior_run_id_val,
        )

    # 4. Compute structured diff
    diff = _compute_diff(prior, new_fin)

    # 5. Stamp formal_grade on the new run record
    _stamp_formal_grade(workspace_id, new_run_id, formal_grade, prior_run_id_val)

    return {
        "success": True,
        "transport_success": True,
        "status": "ok",
        "run_id": new_run_id,
        "prior_run_id": prior_run_id_val,
        "formal_grade": formal_grade,
        "version_sequence": new_num,
        "diff": diff,
        "validation_report": validation_report or {},
        "message": f"已创建 {formal_grade} 版 run，链接前序 {prior_run_id_val}",
    }


# ── diff ─────────────────────────────────────────────────────────────────────


def _compute_diff(
    prior: dict[str, Any],
    new_fin: dict[str, Any],
) -> dict[str, Any]:
    """Compare prior run record against new finance result.

    Returns a structured diff with:
    - indicators: {metric: {prior, current, delta_abs, delta_pct}}
    - assumptions: {added, removed, changed}
    - field_diff: top-level scalar field changes
    - has_changes: bool
    """

    # Indicators
    prior_ind = dict(prior.get("indicators") or {})
    new_ind = dict(new_fin.get("indicators") or {})
    indicator_diff: dict[str, dict[str, Any]] = {}
    all_metrics = sorted(set(prior_ind.keys()) | set(new_ind.keys()))
    for metric in all_metrics:
        old_val = prior_ind.get(metric)
        new_val = new_ind.get(metric)
        if old_val == new_val:
            continue
        delta_abs = None
        delta_pct = None
        if old_val is not None and new_val is not None:
            try:
                delta_abs = round(float(new_val) - float(old_val), 4)
                if float(old_val) != 0:
                    delta_pct = round(delta_abs / abs(float(old_val)) * 100, 2)
            except (TypeError, ValueError):
                pass
        indicator_diff[metric] = {
            "prior": old_val,
            "current": new_val,
            "delta_abs": delta_abs,
            "delta_pct": delta_pct,
        }

    # Assumptions
    prior_assumptions = {str(a.get("element", "")): a for a in (prior.get("assumptions") or []) if isinstance(a, dict)}
    new_assumptions_list = list(new_fin.get("assumptions") or [])
    new_assumptions: dict[str, Any] = {}
    for a in new_assumptions_list:
        if isinstance(a, dict):
            new_assumptions[str(a.get("element", ""))] = a
        elif isinstance(a, str):
            new_assumptions[a] = {"note": a}
    added = [k for k in new_assumptions if k not in prior_assumptions]
    removed = [k for k in prior_assumptions if k not in new_assumptions]
    changed = [
        k for k in prior_assumptions if k in new_assumptions
        and prior_assumptions[k] != new_assumptions[k]
    ]

    # Top-level field changes (investment, funding, scalar fields)
    field_diff: dict[str, dict[str, Any]] = {}
    for section in ("investment", "funding"):
        prior_section = dict(prior.get(section) or {})
        new_section = dict(new_fin.get(section) or {})
        all_keys = sorted(set(prior_section.keys()) | set(new_section.keys()))
        for key in all_keys:
            old_val = prior_section.get(key)
            new_val = new_section.get(key)
            if old_val != new_val:
                field_diff[f"{section}.{key}"] = {
                    "prior": old_val,
                    "current": new_val,
                }

    has_changes = bool(indicator_diff or added or removed or changed or field_diff)

    return {
        "has_changes": has_changes,
        "indicators": indicator_diff,
        "assumptions": {
            "added": added,
            "removed": removed,
            "changed": changed,
            "prior_count": len(prior_assumptions),
            "current_count": len(new_assumptions),
        },
        "field_diff": field_diff,
        "prior_run_id": prior.get("run_id"),
        "prior_formal_grade": prior.get("formal_grade"),
    }


# ── helpers ──────────────────────────────────────────────────────────────────


def _stamp_formal_grade(
    workspace_id: str,
    run_id: str,
    formal_grade: str,
    parent_run_id: str,
) -> None:
    """Stamp formal_grade fields onto a persisted run record."""
    import json
    from pathlib import Path

    from lvke_mcp.runtime.workspace import workspace_root

    path = workspace_root(workspace_id) / "finance_runs" / f"{run_id}.json"
    if not path.is_file():
        return
    record = json.loads(path.read_text(encoding="utf-8"))
    record["formal_grade"] = formal_grade
    record["parent_run_id"] = parent_run_id or record.get("parent_run_id")
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _failure(
    code: str,
    message: str,
    *,
    prior_run_id: str = "",
) -> dict[str, Any]:
    return {
        "success": False,
        "transport_success": True,
        "status": "blocked",
        "code": code,
        "message": message,
        "run_id": None,
        "prior_run_id": prior_run_id or None,
        "formal_grade": None,
        "version_sequence": None,
        "diff": {},
        "validation_report": {},
    }