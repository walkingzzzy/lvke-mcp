"""整包生成编排。"""

from __future__ import annotations

import json
from typing import Any, Optional


from .base import (
    MODEL_VERSION,
    _read_workspace_req,
    _resolve_valuation_date_for_mode,
    compute_input_hash,
    compute_spec_hash,
)

from .render import (
    render_workspace_finance_tables,
)

from .run_model import (
    run_workspace_finance_model,
)

from .spec_prepare import (
    _inject_linked_cost_items,
    prepare_workspace_finance_spec,
)


def generate_workspace_finance_package(
    workspace_id: str,
    *,
    mode: str = "estimate_preview",
    force_refresh_spec: bool = False,
    force_recompute: bool = False,
    force_flat: bool = False,
    prefer_llm_spec: bool = False,
    confirmed_spec: Optional[dict[str, Any]] = None,
    agent_trace_id: str = "",
    tool_call_id: str = "",
    valuation_date: str = "",
    requested_manifest: Optional[dict[str, Any]] = None,
    selected_scenario_id: str = "base",
) -> dict[str, Any]:
    """一句话组合：prepare → deterministic run → render tables。

    默认 ``prefer_llm_spec=False``（与参数默认值一致）：
    - 复用已确认 spec 或 flat 单点收入 + 系统默认成本/税率；
    - **不会**静默调用 LLM 生成工程量/产品量价/人员等 formal 输入；
    - 结果通常为 summary 级 13 表，不得宣称 reference/formal。

    仅当调用方显式 ``prefer_llm_spec=True`` 且 ``force_flat=False`` 时：
    prepare 会走 LLM 提出 **候选** FinanceSpec（失败回退 flat），算术与 13 表仍只由引擎生成。
    ``force_flat=True`` 时禁用 LLM，仅确定性估算（回归/对照基线）。
    正式交付应传入 ``confirmed_spec``（人工确认），package 内禁止再让 LLM 改写。
    """
    effective_valuation_date, valuation_errors = _resolve_valuation_date_for_mode(
        mode, valuation_date,
    )
    if valuation_errors:
        return {
            "ok": False,
            "available": False,
            "stage": "valuation_date",
            "missing_inputs": ["valuation_date"],
            "blocking_issues": [
                {"rule": "valuation_date_required", "detail": item}
                for item in valuation_errors
            ],
            "message": "正式/评审级财务交付必须显式传入估值日期",
            "validation_complete": False,
            "professional_finance_appendices": False,
        }
    # 正式交付可直接传入人工确认并冻结的 spec，package 内不得再次让 LLM 改写。
    if isinstance(confirmed_spec, dict):
        from lvke_mcp.domains.finance.spec import mark_spec_confirmed

        confirmed_spec = mark_spec_confirmed(confirmed_spec)
        _meta, req, finance_raw = _read_workspace_req(workspace_id)
        input_revision = _inject_linked_cost_items(req, finance_raw)
        prep = {
            "ok": True,
            "available": bool(input_revision.get("total_investment_wan")),
            "missing_inputs": [] if input_revision.get("total_investment_wan") else ["total_investment_wan"],
            "spec": confirmed_spec,
            "spec_hash": compute_spec_hash(confirmed_spec),
            "input_revision": input_revision,
            "input_hash": compute_input_hash(
                input_revision,
                invest_type=str(req.get("invest_type") or ""),
                build_period_months=req.get("build_period_months"),
                industry=str(req.get("industry") or ""),
            ),
            "invest_type": str(req.get("invest_type") or ""),
            "industry": str(req.get("industry") or ""),
            "build_period_months": req.get("build_period_months"),
            "used_llm": False,
            "llm_attempted": False,
            "llm_role": "confirmed_spec",
            "spec_source_hint": confirmed_spec.get("source_hint") or "confirmed_spec",
            "warnings": [],
            "validate_errors": [],
            "assumptions_to_confirm": [],
            "force_flat": False,
        }
        _refresh = False
    # 刷新规则：force_flat 永不调 LLM；默认复用已确认 spec，只有明确要求才刷新。
    elif force_flat:
        _refresh = False
    elif prefer_llm_spec:
        _refresh = True
    else:
        _refresh = bool(force_refresh_spec)

    if not isinstance(confirmed_spec, dict):
        prep = prepare_workspace_finance_spec(
            workspace_id,
            strategy="propose_from_project" if _refresh else "reuse_confirmed",
            force_refresh=_refresh,
            force_flat=force_flat,
        )
    if prep.get("missing_inputs"):
        return {
            "ok": False,
            "available": False,
            "stage": "prepare",
            "missing_inputs": prep["missing_inputs"],
            "assumptions_to_confirm": prep.get("assumptions_to_confirm") or [],
            "warnings": prep.get("warnings") or [],
            "spec_hash": prep.get("spec_hash"),
            "input_hash": prep.get("input_hash"),
            "prepare": {
                "used_llm": bool(prep.get("used_llm")),
                "llm_attempted": bool(prep.get("llm_attempted")),
                "llm_role": prep.get("llm_role"),
                "spec_source_hint": prep.get("spec_source_hint"),
                "force_flat": bool(force_flat),
            },
            "llm_participated_in_tables": False,
            "message": "输入不足，不能生成 13 表",
        }

    run = run_workspace_finance_model(
        workspace_id,
        spec=None if force_flat else prep.get("spec"),
        spec_hash="" if force_flat else (prep.get("spec_hash") or ""),
        input_revision=prep.get("input_revision"),
        mode=mode,
        force_recompute=force_recompute,
        force_flat=force_flat,
        agent_trace_id=agent_trace_id,
        tool_call_id=tool_call_id,
        report_file="finance_generate_package",
        valuation_date=effective_valuation_date,
        requested_manifest=requested_manifest,
        selected_scenario_id=selected_scenario_id,
    )
    if not run.get("available"):
        return {
            "ok": False,
            "available": False,
            "stage": "run",
            "run": run,
            "prepare": {
                "used_llm": bool(prep.get("used_llm")),
                "llm_attempted": bool(prep.get("llm_attempted")),
                "llm_role": prep.get("llm_role"),
                "spec_hash": prep.get("spec_hash"),
                "spec_source_hint": prep.get("spec_source_hint"),
                "warnings": prep.get("warnings"),
                "assumptions_to_confirm": prep.get("assumptions_to_confirm"),
                "validate_errors": prep.get("validate_errors"),
                "force_flat": bool(force_flat),
            },
            "llm_participated_in_tables": False,
        }

    tables = render_workspace_finance_tables(
        workspace_id,
        run_id=str(run.get("run_id") or ""),
        format="structured",
        include_control_tables=True,
    )

    # 交付产物：可读包 + 专业 xlsx。是否可正式发布由 table_quality/validation_complete 决定。
    artifacts: dict[str, Any] = {}
    tables_structured: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    excel_artifact: dict[str, Any] = {}
    table_quality: dict[str, Any] = {}
    prep_meta = {
        "used_llm": bool(prep.get("used_llm")),
        "llm_attempted": bool(prep.get("llm_attempted")),
        "llm_role": prep.get("llm_role") or ("none" if force_flat else "none_or_fallback"),
        "llm_fills_cells": False,
        "spec_source_hint": prep.get("spec_source_hint"),
        "spec_hash": prep.get("spec_hash"),
        "force_flat": bool(force_flat),
        "prefer_llm_spec": bool(prefer_llm_spec) and not force_flat,
        "force_refresh_spec": bool(_refresh),
        "warnings": prep.get("warnings") or [],
        "validate_errors": prep.get("validate_errors") or [],
        "assumptions_to_confirm": prep.get("assumptions_to_confirm") or [],
    }
    try:
        from lvke_mcp.domains.finance import table_pack

        # 把 prepare 边界写入 run 快照，供 evidence 读取
        run = dict(run)
        run["force_flat"] = bool(force_flat)
        if not force_flat and isinstance(prep.get("spec"), dict):
            run["spec"] = prep.get("spec")
        run["_prepare_meta"] = prep_meta

        art_dir = table_pack.default_artifact_dir(
            workspace_id,
            str(run.get("run_id") or "unknown"),
        )
        artifacts = table_pack.write_readable_artifacts(
            run,
            art_dir,
            workspace_id=workspace_id,
            extra_evidence={
                "mode": mode,
                "force_flat": force_flat,
                "agent_trace_id": agent_trace_id,
                "tool_call_id": tool_call_id,
                "prepare_used_llm": prep_meta["used_llm"],
                "prepare_llm_role": prep_meta["llm_role"],
                "prepare_spec_source_hint": prep_meta["spec_source_hint"],
                "prepare_warnings": prep_meta["warnings"],
                "prepare_validate_errors": prep_meta["validate_errors"],
                "tables_generated_by": "finance_model.compute_financials",
                "llm_participated_in_tables": False,
                "llm_landed_as": (
                    "confirmed_spec"
                    if prep_meta.get("llm_role") == "confirmed_spec"
                    else ("prepare_spec" if prep_meta["used_llm"]
                          else ("none_force_flat" if force_flat else "prepare_fallback"))
                ),
            },
        )
        tables_structured = table_pack.build_tables_structured(run)
        from lvke_mcp.domains.finance import table_render

        table_quality = (table_render.build_all_structured(run).get("_meta") or {})
        try:
            from lvke_mcp.adapters.spreadsheets.finance_export import export_finance_workbook

            excel_path = art_dir / "财务专业附表.xlsx"
            excel_artifact = export_finance_workbook(
                run,
                excel_path,
                model_version=str(run.get("model_version") or MODEL_VERSION),
                run_id=str(run.get("run_id") or ""),
            )
            artifacts["xlsx"] = excel_artifact.get("path")
        except Exception as excel_exc:  # noqa: BLE001
            excel_artifact = {"ok": False, "error": f"{type(excel_exc).__name__}: {excel_exc}"}
        evidence = table_pack.build_evidence(
            run, workspace_id=workspace_id,
            extra={"artifacts_dir": artifacts.get("out_dir")},
        )
    except Exception as exc:  # noqa: BLE001 - 可读产物失败不阻断 package 数字
        artifacts = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    annual_income = ((run.get("annual") or {}).get("income_statement") or [])
    achieved = run.get("indicators") or {}
    first = annual_income[0] if annual_income else {}
    op_rows = [r for r in annual_income if isinstance(r, dict)]
    avg_revenue = round(sum(float(r.get("revenue") or 0.0) for r in op_rows) / len(op_rows), 2) if op_rows else None
    avg_net = round(sum(float(r.get("net_profit") or 0.0) for r in op_rows) / len(op_rows), 2) if op_rows else None
    indicator_cards = {
        "achieved_year": {
            "label": "达产年（融资前）",
            "revenue_wan": achieved.get("revenue"),
            "net_profit_wan": achieved.get("net_profit"),
        },
        "first_operating_year": {
            "label": "首个运营年（融资前）",
            "revenue_wan": first.get("revenue"),
            "net_profit_wan": first.get("net_profit"),
        },
        "operating_period_average": {
            "label": "运营期年均（融资前）",
            "revenue_wan": avg_revenue,
            "net_profit_wan": avg_net,
        },
    }
    cost_path = (run.get("raw") or {}).get("cost_path")
    selected_scenario = {
        "run_id": run.get("run_id"),
        "role": "delivery_selected",
        "name": (
            "已确认规范情景" if prep_meta.get("llm_role") == "confirmed_spec"
            else ("AI规范情景" if prep_meta.get("used_llm")
                  else ("业主输入基线" if not force_flat else "Flat对照基线"))
        ),
        "cost_path": cost_path,
        "is_recommended": cost_path not in {"spec_variable", "user_cost_items_var_ignored"},
        "note": "spec_variable 仅作为对照情景，不作为默认对外推荐" if cost_path == "spec_variable" else "本 package 的唯一交付选用 run",
    }
    delivery_quality = excel_artifact.get("delivery_quality") or {}
    semantic_checks = delivery_quality.get("semantic_checks") or {}
    semantic_blockers = [
        {"rule": key, **value}
        for key, value in semantic_checks.items()
        if isinstance(value, dict) and not value.get("ok")
    ]
    quality_dimensions = delivery_quality.get("quality_dimensions") or {}
    semantic_blockers.extend(
        {
            "rule": f"{name}_coverage_failed",
            "ok": False,
            "detail": value,
        }
        for name, value in quality_dimensions.items()
        if isinstance(value, dict) and not value.get("ok")
    )
    governance_blockers = [
        {"rule": "model_manifest_invalid", "ok": False, "detail": detail}
        for detail in (run.get("manifest_errors") or [])
    ]
    governance_blockers.extend(
        {
            "rule": "industry_required_input_missing",
            "ok": False,
            "detail": f"行业 profile 缺少正式交付输入：{field}",
        }
        for field in (run.get("industry_required_missing") or [])
    )
    governance_blockers.extend(
        {"rule": "fact_pack_schedule_invalid", "ok": False, "detail": str(detail)}
        for detail in ((run.get("fact_pack_projection") or {}).get("schedule_issues") or [])
    )
    from lvke_mcp.domains.finance.spec import validate_for_formal

    _spec_ok, spec_formal_errors = validate_for_formal(run.get("spec") or {})
    governance_blockers.extend(
        {"rule": "finance_spec_not_formal", "ok": False, "detail": detail}
        for detail in spec_formal_errors
    )
    semantic_blockers.extend(governance_blockers)
    pack = {}
    for source in (
        (run.get("input_revision") or {}),
        (run.get("finance_inputs") or {}),
        (run.get("result") or {}).get("raw") or {},
    ):
        if isinstance(source, dict) and isinstance(source.get("finance_fact_pack"), dict):
            pack = source.get("finance_fact_pack") or {}
            break
    ceiling = str(
        pack.get("delivery_grade_ceiling")
        or table_quality.get("delivery_grade_ceiling")
        or "summary"
    )
    depth_ok = bool((pack.get("depth_assessment") or {}).get("ok")) if isinstance(
        pack.get("depth_assessment"), dict
    ) else bool(table_quality.get("depth_ok"))
    if ceiling != "formal_candidate" or not depth_ok:
        semantic_blockers.append({
            "rule": "delivery_grade_ceiling_not_formal",
            "ok": False,
            "detail": (
                f"delivery_grade_ceiling={ceiling}, depth_ok={depth_ok}; "
                "formal 要求 formal_candidate 且 depth_assessment.ok"
            ),
        })
    formal_ready = (
        bool(table_quality.get("reference_structure_ready"))
        and bool(excel_artifact.get("ok"))
        and bool(delivery_quality.get("validation_complete"))
        and not semantic_blockers
        and ceiling == "formal_candidate"
        and depth_ok
    )
    table_quality = dict(table_quality)
    table_quality["validation_complete"] = formal_ready
    table_quality["delivery_grade_ceiling"] = ceiling
    table_quality["depth_ok"] = depth_ok
    table_quality["semantic_checks"] = semantic_checks
    table_quality["semantic_blockers"] = semantic_blockers
    table_quality["quality_dimensions"] = quality_dimensions
    evidence["validation_complete"] = formal_ready
    evidence["professional_finance_appendices"] = formal_ready
    evidence["delivery_grade_ceiling"] = ceiling
    evidence["depth_ok"] = depth_ok
    evidence["semantic_checks"] = semantic_checks
    evidence["semantic_blockers"] = semantic_blockers
    evidence["quality_dimensions"] = quality_dimensions
    evidence["runtime_source_validation"] = table_quality.get("runtime_source_validation") or {}
    evidence["missing_fact_paths"] = table_quality.get("missing_fact_paths") or []
    evidence["model_manifest"] = run.get("model_manifest") or {}
    evidence["manifest_hash"] = run.get("manifest_hash")
    evidence["valuation_date"] = run.get("valuation_date")
    try:
        evidence_path = (artifacts.get("files") or {}).get("evidence")
        if evidence_path:
            from pathlib import Path

            Path(evidence_path).write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "available": True,
        "stage": "done",
        "run_id": run.get("run_id"),
        "input_hash": run.get("input_hash"),
        "spec_hash": run.get("spec_hash") or prep.get("spec_hash"),
        "model_version": run.get("model_version"),
        "template_version": run.get("template_version"),
        "model_manifest": run.get("model_manifest") or {},
        "manifest_hash": run.get("manifest_hash"),
        "manifest_errors": run.get("manifest_errors") or [],
        "valuation_date": run.get("valuation_date"),
        "policy_profile": run.get("policy_profile") or {},
        "industry_profile": run.get("industry_profile") or {},
        "assurance_level": run.get("assurance_level"),
        "calculation_status": run.get("calculation_status"),
        "indicators": run.get("indicators") or {},
        "summary_md": run.get("summary_md") or "",
        "table_manifest": tables.get("table_manifest") or run.get("table_manifest") or [],
        "tables": tables.get("tables") or run.get("tables") or {},
        "tables_structured": tables_structured,
        "evidence": evidence,
        "artifacts": artifacts,
        "delivery_format": "xlsx+json+readable_md",
        "grade": table_quality.get("grade") or "summary",
        "validation_complete": formal_ready,
        "professional_finance_appendices": formal_ready,
        "table_quality": table_quality,
        "semantic_blockers": semantic_blockers,
        "excel_artifact": excel_artifact,
        "selected_run_id": run.get("run_id"),
        "selected_scenario": selected_scenario,
        "selected_scenario_id": run.get("selected_scenario_id") or "base",
        "indicator_cards": indicator_cards,
        "checks": run.get("checks") or [],
        "consistency_ok": run.get("consistency_ok"),
        "reused_run": run.get("reused_run"),
        "prepare_warnings": prep.get("warnings") or [],
        "assumptions_to_confirm": prep.get("assumptions_to_confirm") or [],
        "finance_inputs": run.get("finance_inputs") or {},
        "run": run,
        "workflow": {
            "trace_id": agent_trace_id or f"finance:{run.get('run_id') or 'unavailable'}",
            "run_id": run.get("run_id"),
            "steps": [
                {"id": "inputs", "label": "核对输入", "status": "completed"},
                {"id": "spec", "label": "确定口径", "status": "completed"},
                {"id": "run", "label": "运行模型", "status": "completed"},
                {"id": "tables", "label": "生成13表", "status": "completed" if tables.get("ok") else "failed"},
                {"id": "checks", "label": "执行勾稽", "status": "completed" if run.get("consistency_ok") else "attention"},
            ],
        },
        "prepare": prep_meta,
        "tables_generated_by": "lvke_mcp.domains.finance.finance_model.compute_financials",
        "llm_participated_in_tables": False,
        "llm_landed": bool(prep_meta.get("used_llm")),
        "llm_landed_as": (
            "confirmed_spec" if prep_meta.get("llm_role") == "confirmed_spec"
            else ("prepare_spec" if prep_meta.get("used_llm")
                  else ("none_force_flat" if force_flat else "prepare_fallback"))
        ),
        "cost_path": cost_path,
    }
