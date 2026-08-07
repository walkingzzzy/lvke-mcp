"""spec 准备：联动成本项注入与非负成本校验。"""

from __future__ import annotations

from typing import Any, Optional


from .base import (
    MODEL_VERSION,
    TEMPLATE_VERSION,
    _ensure_workspace,
    _project_brief,
    _read_workspace_req,
    compute_input_hash,
    compute_spec_hash,
)


def _inject_linked_cost_items(req: dict[str, Any], finance_in: dict[str, Any]) -> dict[str, Any]:
    """定员/环保运行费联动注入（与历史 workspace_finance_model 行为一致）。

    Fix-P1-2：写入 ``_auto_injected_cost_items`` 审计清单，禁止静默改 cost 后无痕迹。
    """
    out = dict(finance_in)
    # 工作区正式入口默认采用城建/教育附加的 VAT 基数路径；仅用户显式 false 时降级。
    out.setdefault("surtax_on_vat", True)
    injected: list[dict[str, Any]] = list(out.get("_auto_injected_cost_items") or [])

    try:
        from lvke_mcp.domains.finance import scale_infer as build_scale_infer

        bs = build_scale_infer.build_scale_summary(req)
        hc = bs.get("headcount") or {}
        labor = hc.get("labor_cost_wan")
        if labor and out.get("is_operating"):
            items = dict(out.get("cost_items") or {})
            if not any(k for k in items if ("工资" in str(k) or "人工" in str(k))):
                items["工资及福利"] = labor
                out["cost_items"] = items
                injected.append({
                    "key": "工资及福利",
                    "amount_wan": labor,
                    "source": "build_scale_infer.headcount.labor_cost_wan",
                    "reason": "cost_items 缺工资项，按定员联动注入",
                })
    except Exception:  # noqa: BLE001
        pass

    try:
        from lvke_mcp.domains.finance import env_templates

        if out.get("is_operating"):
            bd = out.get("invest_breakdown") or {}
            construction = bd.get("construction_wan") or out.get("total_investment_wan") or 0.0
            revenue = out.get("annual_revenue_wan") or 0.0
            est = env_templates.env_cost_estimate(
                str(req.get("industry") or ""),
                construction_wan=construction, revenue_wan=revenue,
            )
            env_opex = est.get("env_opex_wan") or 0.0
            # 环保运行费属于经营假设，默认只给建议，不得静默改写业主 cost_items。
            # 只有用户显式开启 auto_inject_env_opex 才落地到计算口径。
            if env_opex and est.get("available") and bool(out.get("auto_inject_env_opex")):
                items = dict(out.get("cost_items") or {})
                if not any(k for k in items if ("环保" in str(k) or "环境" in str(k))):
                    items["环保运行费"] = env_opex
                    out["cost_items"] = items
                    injected.append({
                        "key": "环保运行费",
                        "amount_wan": env_opex,
                        "source": "env_templates.env_cost_estimate",
                        "reason": (
                            f"cost_items 缺环保项；"
                            f"营收×env_opex_ratio={est.get('env_opex_ratio')}"
                            f"（matched={est.get('matched') or 'default'}）"
                        ),
                        "env_opex_ratio": est.get("env_opex_ratio"),
                        "matched": est.get("matched"),
                    })
            elif env_opex and est.get("available"):
                out.setdefault("_cost_suggestions", []).append({
                    "key": "环保运行费",
                    "amount_wan": env_opex,
                    "source": "env_templates.env_cost_estimate",
                    "reason": "未开启 auto_inject_env_opex，仅作为待确认建议，不改变经营成本",
                    "env_opex_ratio": est.get("env_opex_ratio"),
                })
    except Exception:  # noqa: BLE001
        pass

    if injected:
        out["_auto_injected_cost_items"] = injected
    return out


def prepare_workspace_finance_spec(
    workspace_id: str,
    *,
    strategy: str = "propose_from_project",
    force_refresh: bool = False,
    force_flat: bool = False,
) -> dict[str, Any]:
    """准备 FinanceSpec（可走 LLM；失败回退 flat 默认 spec）。

    返回：
    - ok / available
    - spec / spec_hash
    - assumptions_to_confirm / warnings / missing_inputs
    - input_revision 快照（标准化后的 finance 输入，含联动项）
    - input_hash
    """
    _ensure_workspace(workspace_id)
    _meta, req, finance_raw = _read_workspace_req(workspace_id)
    finance_in = _inject_linked_cost_items(req, finance_raw)
    invest_type = str(req.get("invest_type") or "")
    industry = str(req.get("industry") or "")
    build_period_months = req.get("build_period_months")
    input_hash = compute_input_hash(
        finance_in,
        invest_type=invest_type,
        build_period_months=build_period_months,
        industry=industry,
    )

    missing: list[str] = []
    if not finance_in.get("total_investment_wan"):
        missing.append("total_investment_wan")
    if (
        force_flat
        and finance_in.get("is_operating") is not False
        and not finance_in.get("annual_revenue_wan")
    ):
        missing.append("annual_revenue_wan")

    warnings: list[str] = []
    assumptions: list[str] = []
    spec: Optional[dict[str, Any]] = None
    used_llm = False
    llm_attempted = False

    if force_flat or strategy == "reuse_confirmed":
        # 强制 flat / 复用确认：不调用 LLM
        if strategy == "reuse_confirmed" and not force_flat:
            try:
                from lvke_mcp.domains.finance import run_store

                manual = finance_in.get("finance_spec")
                if isinstance(manual, dict):
                    spec = dict(manual)
                    assumptions.append("复用工作区已确认 FinanceSpec")

                latest = run_store.latest_run(workspace_id) or {}
                if spec is None and latest.get("spec_json"):
                    import json as _json
                    try:
                        spec = _json.loads(latest["spec_json"])
                        assumptions.append("复用最近一次 run 的已固化 FinanceSpec")
                    except Exception:  # noqa: BLE001
                        spec = None
            except Exception:  # noqa: BLE001
                spec = None
        if spec is None:
            warnings.append("force_flat 或无已确认 spec，使用 flat 默认口径（不调用 LLM）")
    elif not force_refresh and not force_flat:
        # 默认：可走 LLM 定规范
        try:
            from lvke_mcp.domains.finance import spec_builder

            brief = _project_brief(workspace_id)
            spec = spec_builder.build_finance_spec(brief, req)
            _hint = str((spec or {}).get("source_hint") or "") if isinstance(spec, dict) else ""
            used_llm = _hint == "llm_spec"
            llm_attempted = _hint.startswith("llm")  # llm_spec / llm_invalid / llm_error
            if not used_llm:
                warnings.append(
                    f"spec_builder 回退默认（source_hint={_hint or None}），"
                    "未采用 LLM 有效规范"
                )
            if not spec:
                warnings.append("spec_builder 返回空，回退 flat")
                used_llm = False
                llm_attempted = False
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"spec 构建失败，回退 flat：{type(exc).__name__}")
            spec = None
            used_llm = False
            llm_attempted = False
    else:
        # force_refresh=True 且非 force_flat：强制走 LLM 路径
        try:
            from lvke_mcp.domains.finance import spec_builder

            brief = _project_brief(workspace_id)
            spec = spec_builder.build_finance_spec(brief, req)
            _hint = str((spec or {}).get("source_hint") or "") if isinstance(spec, dict) else ""
            used_llm = _hint == "llm_spec"
            llm_attempted = _hint.startswith("llm")
            if not used_llm:
                warnings.append(
                    f"spec_builder 回退默认（source_hint={_hint or None}），"
                    "未采用 LLM 有效规范"
                )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"spec 构建失败，回退 flat：{type(exc).__name__}")
            spec = None
            used_llm = False
            llm_attempted = False

    # force_flat 时对齐达产营收（与历史 workspace_finance_model 轨二一致）
    # 注意：force_flat 路径不得调用 LLM；仅用默认/已有 flat 参数回填
    if force_flat and not finance_in.get("annual_revenue_wan"):
        try:
            from lvke_mcp.domains.finance import revenue_models, spec_builder

            # 只用 fallback 默认 spec，不触发 LLM
            _spec = spec_builder._default_spec(req)  # noqa: SLF001 - 同包强制 flat 禁 LLM
            _model = ((_spec or {}).get("revenue") or {}).get("model", "flat")
            if _model != "flat":
                _bpm = build_period_months or 12
                _cy = int(finance_in.get("calc_period_years") or 12)
                _by = max(1, -(-int(_bpm) // 12))
                _exp = revenue_models.expand(_spec, max(_cy - _by, 1))
                _peak = max((_exp.get("revenue_by_year") or [0.0]) or [0.0])
                if _peak > 0:
                    finance_in["annual_revenue_wan"] = _peak
                    input_hash = compute_input_hash(
                        finance_in,
                        invest_type=invest_type,
                        build_period_months=build_period_months,
                        industry=industry,
                    )
                    assumptions.append("force_flat：以默认 spec 达产年营回填 annual_revenue_wan（未调 LLM）")
        except Exception:  # noqa: BLE001
            pass

    spec_hash = compute_spec_hash(spec if isinstance(spec, dict) else None)
    spec_source_hint = None
    validate_errors: list[str] = []
    llm_raw_preview = None
    if isinstance(spec, dict):
        spec_source_hint = spec.get("source_hint")
        validate_errors = list(spec.get("_validate_errors") or [])
        llm_raw_preview = spec.get("_llm_raw_preview")
    elif force_flat:
        spec_source_hint = "force_flat_no_spec"
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "available": not bool(missing),
        "missing_inputs": missing,
        "spec": spec if isinstance(spec, dict) else None,
        "spec_hash": spec_hash,
        "assumptions_to_confirm": assumptions,
        "warnings": warnings,
        "used_llm": used_llm,
        "llm_attempted": llm_attempted,
        "llm_role": (
            "none"
            if force_flat
            else ("prepare_spec" if used_llm else "none_or_fallback")
        ),
        "llm_fills_cells": False,
        "spec_source_hint": spec_source_hint,
        "validate_errors": validate_errors,
        "llm_raw_preview": llm_raw_preview,
        "force_flat": force_flat,
        "strategy": strategy,
        "input_revision": finance_in,
        "input_hash": input_hash,
        "input_revision_id": int((_read_workspace_req(workspace_id)[0].get("finance_input_revision") or 0)),
        "invest_type": invest_type,
        "industry": industry,
        "build_period_months": build_period_months,
        "model_version": MODEL_VERSION,
        "template_version": TEMPLATE_VERSION,
    }


def _nonnegative_cost_issues(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return public field errors for negative cash-cost or payroll values."""

    issues: list[dict[str, Any]] = []

    def check(path: str, value: Any) -> None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return
        if numeric < -0.005:
            issues.append({
                "path": path,
                "code": "negative_value",
                "value": round(numeric, 6),
                "minimum": 0,
            })

    indicators = result.get("indicators") or {}
    check(
        "/indicators/operating_cost",
        float(indicators.get("op_cost") or 0.0)
        - float(indicators.get("depreciation") or 0.0),
    )
    annual = result.get("annual") or {}
    for table_name, fields in (
        ("income_statement", ("operating_cost",)),
        ("total_cost", ("operating_cost",)),
        ("wage", ("wage", "welfare", "total")),
    ):
        for index, row in enumerate(annual.get(table_name) or []):
            if not isinstance(row, dict):
                continue
            for field in fields:
                check(f"/annual/{table_name}/{index}/{field}", row.get(field))
    for issue in result.get("blocking_issues") or []:
        if isinstance(issue, dict) and issue.get("rule") == "negative_operating_cost":
            if not any(item["path"] == "/indicators/operating_cost" for item in issues):
                issues.append({
                    "path": "/indicators/operating_cost",
                    "code": "negative_operating_cost",
                    "minimum": 0,
                })
            break
    return issues
