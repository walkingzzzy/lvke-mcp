"""工作区级财务模型运行服务（P0/P1 编排真源）。

把原先 ``workspace_finance_model`` 中的“读输入 → 可选 LLM 定 spec → 确定性算数 →
回显输入”拆成可单独调用、可审计、可幂等复用的阶段：

1. ``prepare_workspace_finance_spec`` —— 可读项目资料 / 可走 LLM 生成 FinanceSpec
2. ``run_workspace_finance_model`` —— **禁止内部再调 LLM**，只消费已固化输入 + spec
3. ``render_workspace_finance_tables`` —— 只从指定 run 渲染 13 表
4. ``get_workspace_finance_run`` —— 纯查询，不重算、不写库
5. ``generate_workspace_finance_package`` —— 宿主固定编排：prepare → run → render

设计原则（对齐《财务模型与13表上下级关系及AI调用流程方案_20260712》）：
- 模型是数值真源，run 是版本真源，13 表是交付视图
- 相同 input/spec/model/template 幂等复用 run
- GET 路径不得隐式写审计库；写副作用只在显式 run 命令里
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date
from typing import Any, Optional

from lvke_mcp.domains.finance.industry_registry import select_industry_profile
from lvke_mcp.domains.finance.model_manifest import (
    ModelManifest,
    build_manifest,
    manifest_from_dict,
)
from lvke_mcp.domains.finance.policy_registry import select_policy_profile

MODEL_VERSION = "finance_model.v2.4"
TEMPLATE_VERSION = "finance_tables.v3"

# 13 张交付附表 key（与 finance_model / 测试对齐，不含控制/展示表）
DELIVERY_TABLE_KEYS: tuple[str, ...] = (
    "investment",
    "interest-during-construction",
    "working-capital",
    "funding",
    "income-statement",
    "total-cost",
    "wage",
    "depreciation",
    "amortization",
    "profit-distribution",
    "debt-service",
    "cashflow",
    "capital-cashflow",
)

# 正式交付编号（附表6-1/6-2/6-3不是简单的第7/8/9张表）。
DELIVERY_TABLE_META: tuple[tuple[str, str, str], ...] = (
    ("investment", "附表1", "固定资产投资估算表"),
    ("interest-during-construction", "附表2", "建设期贷款利息表"),
    ("working-capital", "附表3", "流动资金估算表"),
    ("funding", "附表4", "投资使用计划与资金筹措表"),
    ("income-statement", "附表5", "营业收入、税金及附加和增值税估算表"),
    ("total-cost", "附表6", "总成本费用估算表"),
    ("wage", "附表6-1", "工资及附加估算表"),
    ("depreciation", "附表6-2", "固定资产折旧费估算表"),
    ("amortization", "附表6-3", "无形资产及其他资产摊销估算表"),
    ("profit-distribution", "附表7", "利润与利润分配表"),
    ("debt-service", "附表8", "还款付息测算表"),
    ("cashflow", "附表9", "项目投资现金流量表"),
    ("capital-cashflow", "附表10", "项目资本金流量表"),
)


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _sha256_hex(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_input_hash(finance_inputs: dict[str, Any], *, invest_type: str = "",
                       build_period_months: Any = None, industry: str = "") -> str:
    payload = {
        "finance": finance_inputs or {},
        "invest_type": invest_type or "",
        "build_period_months": build_period_months,
        "industry": industry or "",
    }
    return _sha256_hex(_stable_json(payload))


def compute_spec_hash(spec: Optional[dict[str, Any]]) -> str:
    if not spec:
        return _sha256_hex("null")
    return _sha256_hex(_stable_json(spec))


def compute_table_bundle_hash(tables: dict[str, Any]) -> str:
    delivery = {k: (tables or {}).get(k) for k in DELIVERY_TABLE_KEYS}
    return _sha256_hex(_stable_json(delivery))


def compute_idempotency_key(
    workspace_id: str,
    *,
    input_hash: str,
    spec_hash: str,
    model_version: str = MODEL_VERSION,
    template_version: str = TEMPLATE_VERSION,
    manifest_hash: str = "",
    valuation_date: str = "",
    basis_of_estimate_hash: str = "",
) -> str:
    raw = "|".join([
        str(workspace_id),
        input_hash or "",
        spec_hash or "",
        model_version or MODEL_VERSION,
        template_version or TEMPLATE_VERSION,
        manifest_hash or "",
        valuation_date or "",
        basis_of_estimate_hash or "",
    ])
    return _sha256_hex(raw)


def _resolve_valuation_date_for_mode(mode: str, valuation_date: str = "") -> tuple[str, list[str]]:
    """Return the valuation date used by this run and blocking errors, if any.

    Deterministic runs require an explicit valuation date whenever their mode
    is not ``estimate_preview`` so policy and manifest selection are reproducible.
    """
    value = str(valuation_date or "").strip()
    if value:
        try:
            date.fromisoformat(value)
        except ValueError:
            return "", [f"valuation_date 格式无效：{value}，应为 YYYY-MM-DD"]
        return value, []
    if mode != "estimate_preview":
        return "", ["非预览财务 run 必须显式传入 valuation_date，禁止依赖服务器当天日期"]
    return date.today().isoformat(), []


def resolve_model_manifest(
    *,
    industry: str = "",
    valuation_date: str = "",
    requested_manifest: Optional[dict[str, Any]] = None,
) -> tuple[ModelManifest, dict[str, Any], dict[str, Any], list[str]]:
    """Resolve governed model, policy, industry and template versions for a run."""
    as_of = valuation_date or date.today().isoformat()
    errors: list[str] = []
    try:
        policy = select_policy_profile(as_of=as_of)
    except Exception as exc:  # noqa: BLE001
        policy = {}
        errors.append(f"policy_profile: {exc}")
    try:
        industry_profile = select_industry_profile(industry)
    except Exception as exc:  # noqa: BLE001
        industry_profile = {}
        errors.append(f"industry_profile: {exc}")

    if isinstance(requested_manifest, dict):
        manifest = manifest_from_dict(requested_manifest)
    else:
        manifest = build_manifest(
            industry_profile_version=str(industry_profile.get("version") or "general.v1"),
            policy_version=str(policy.get("version") or "cn_tax_policy.2026-01"),
            model_version=MODEL_VERSION,
            template_version=TEMPLATE_VERSION,
            effective_from=str(policy.get("effective_from") or "2026-01-01"),
        )
    errors.extend(manifest.validate(as_of=as_of))
    if policy and manifest.policy_version != policy.get("version"):
        errors.append(
            f"manifest policy_version={manifest.policy_version} does not match active policy={policy.get('version')}"
        )
    if industry_profile and manifest.industry_profile_version != industry_profile.get("version"):
        errors.append(
            "manifest industry_profile_version="
            f"{manifest.industry_profile_version} does not match resolved profile={industry_profile.get('version')}"
        )
    return manifest, policy, industry_profile, errors


def _read_workspace_req(workspace_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """读 MCP 自有 workspace 的 requirement 快照（无则空，调用方以参数覆盖）。"""
    from lvke_mcp.runtime.workspace import workspace_root

    meta: dict[str, Any] = {}
    try:
        path = workspace_root(str(workspace_id)) / "requirement.json"
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
    except Exception:  # noqa: BLE001
        meta = {}
    req = meta.get("requirement") or {}
    if not isinstance(req, dict):
        req = {}
    finance_in = dict(req.get("finance") or {})
    return meta, req, finance_in


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


def run_workspace_finance_model(
    workspace_id: str,
    *,
    spec: Optional[dict[str, Any]] = None,
    spec_id: str = "",
    spec_hash: str = "",
    basis_of_estimate_id: str = "",
    basis_of_estimate_hash: str = "",
    input_revision: Optional[dict[str, Any]] = None,
    input_revision_id: Optional[int] = None,
    mode: str = "estimate_preview",
    force_recompute: bool = False,
    record_audit: bool = True,
    agent_trace_id: str = "",
    tool_call_id: str = "",
    report_file: str = "finance_run_model",
    section: str = "财务测算模型",
    force_flat: bool = False,
    allow_prepare_llm: bool = False,
    valuation_date: str = "",
    requested_manifest: Optional[dict[str, Any]] = None,
    selected_scenario_id: str = "base",
    project_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """确定性运行财务模型。**内部禁止调用 LLM（默认）。**

    - 默认：复用已固化 spec / flat，不在 run 内发起 LLM
    - ``allow_prepare_llm=True``：仅兼容旧 ``workspace_finance_model`` 一站式入口，
      允许在缺 spec 时先 prepare（可含 LLM），但算术本身仍是确定性的
    - 幂等：相同 input/spec/model/template 复用已有 run
    - 返回完整 fin 结果 + run_id / hashes / table_manifest
    """
    from lvke_mcp.domains.finance import finance_model

    _ensure_workspace(workspace_id)
    effective_valuation_date, valuation_errors = _resolve_valuation_date_for_mode(
        mode, valuation_date,
    )
    if valuation_errors:
        return {
            "ok": False,
            "available": False,
            "workspace_id": workspace_id,
            "reason": "valuation_date_required",
            "missing_inputs": ["valuation_date"],
            "blocking_issues": [
                {"rule": "valuation_date_required", "detail": item}
                for item in valuation_errors
            ],
            "model_version": MODEL_VERSION,
            "template_version": TEMPLATE_VERSION,
            "assurance_level": "none",
            "calculation_status": "failed",
        }
    _meta, req, finance_raw = _read_workspace_req(workspace_id)
    context = project_context if isinstance(project_context, dict) else {}
    invest_type = str(context.get("invest_type") or req.get("invest_type") or "")
    industry = str(context.get("industry") or req.get("industry") or "")
    build_period_months = context.get("build_period_months", req.get("build_period_months"))
    manifest, policy_profile, industry_profile, manifest_errors = resolve_model_manifest(
        industry=industry,
        valuation_date=effective_valuation_date,
        requested_manifest=requested_manifest,
    )

    if input_revision is None:
        input_revision = _inject_linked_cost_items(req, finance_raw)
    raw_input_revision = copy.deepcopy(input_revision or {})
    if input_revision_id is None:
        try:
            input_revision_id = int((_meta or {}).get("finance_input_revision") or 0)
        except (TypeError, ValueError):
            input_revision_id = 0

    resolved_spec: Optional[dict[str, Any]] = None
    if force_flat:
        resolved_spec = None
    elif isinstance(spec, dict):
        resolved_spec = spec
    elif allow_prepare_llm:
        # 兼容旧一站式入口：允许 prepare 阶段走 LLM；run 算术仍确定
        prep = prepare_workspace_finance_spec(
            workspace_id, strategy="propose_from_project", force_flat=False,
        )
        input_revision = prep.get("input_revision") or input_revision
        input_revision_id = prep.get("input_revision_id") or input_revision_id
        resolved_spec = prep.get("spec") if isinstance(prep.get("spec"), dict) else None
        invest_type = prep.get("invest_type") or invest_type
        industry = prep.get("industry") or industry
        build_period_months = prep.get("build_period_months", build_period_months)
    else:
        # 纯 run：只复用最近 run 的 spec，否则 flat
        try:
            from lvke_mcp.domains.finance import run_store

            latest = run_store.latest_run(workspace_id) or {}
            if latest.get("spec_json"):
                import json as _json

                loaded = _json.loads(latest["spec_json"])
                if isinstance(loaded, dict):
                    resolved_spec = loaded
        except Exception:  # noqa: BLE001
            resolved_spec = None

    # force_flat 时对齐达产营收
    if force_flat and not (input_revision or {}).get("annual_revenue_wan"):
        try:
            from lvke_mcp.domains.finance import revenue_models, spec_builder

            # force_flat 路径禁止访问 LLM；这里只读取本地默认 spec。
            _spec = spec_builder._default_spec(req)  # noqa: SLF001
            _model = ((_spec or {}).get("revenue") or {}).get("model", "flat")
            if _model != "flat":
                _bpm = build_period_months or 12
                _cy = int((input_revision or {}).get("calc_period_years") or 12)
                _by = max(1, -(-int(_bpm) // 12))
                _exp = revenue_models.expand(_spec, max(_cy - _by, 1))
                _peak = max((_exp.get("revenue_by_year") or [0.0]) or [0.0])
                if _peak > 0:
                    input_revision = dict(input_revision or {})
                    input_revision["annual_revenue_wan"] = _peak
        except Exception:  # noqa: BLE001
            pass

    fact_pack_projection: dict[str, Any] = {
        "applied": False,
        "issues": ["force_flat 明确禁用事实包投影"] if force_flat else [],
    }
    if not force_flat:
        from lvke_mcp.domains.finance.fact_pack import project_confirmed_fact_pack

        input_revision, resolved_spec, fact_pack_projection = project_confirmed_fact_pack(
            dict(input_revision or {}),
            resolved_spec,
            expected_workspace_id=workspace_id,
            expected_build_years=max(1, -(-int(build_period_months or 12) // 12)),
            expected_calc_years=int((input_revision or {}).get("calc_period_years") or 12),
        )
        # A sealed fact pack is the stronger confirmed authority.  Its product
        # tree may legitimately replace a prior flat/candidate spec, so the
        # caller's pre-projection spec hash cannot be reused.
        if fact_pack_projection.get("applied"):
            spec_hash = ""

    from lvke_mcp.domains.finance.parameter_resolver import resolve_run_inputs

    raw_input_hash = compute_input_hash(
        raw_input_revision,
        invest_type=invest_type,
        build_period_months=build_period_months,
        industry=industry,
    )
    (
        input_revision,
        field_source_ledger,
        industry_required_missing,
        input_adoption,
        rejected_inputs,
    ) = resolve_run_inputs(
        input_revision or {},
        spec=resolved_spec,
        policy_profile=policy_profile,
        industry_profile=industry_profile,
    )
    # These canonical fields are model-call context rather than entries read
    # from ``finance_model.fin``.  Apply them before hashing and computing so
    # accepting them can never produce a hash-only, behavior-free change.
    if input_revision.get("build_period_months") is not None:
        build_period_months = input_revision["build_period_months"]
    if input_revision.get("industry") is not None:
        industry = str(input_revision["industry"])
    if input_revision.get("invest_type") is not None:
        invest_type = str(input_revision["invest_type"])
    input_hash = compute_input_hash(
        input_revision or {},
        invest_type=invest_type,
        build_period_months=build_period_months,
        industry=industry,
    )
    if rejected_inputs:
        return {
            "ok": False,
            "available": False,
            "workspace_id": workspace_id,
            "reason": "unknown_or_conflicting_finance_inputs",
            "missing_inputs": [],
            "blocking_issues": [{
                "rule": "finance_input_rejected",
                "detail": ",".join(str(item.get("input") or "") for item in rejected_inputs),
            }],
            "raw_input_hash": raw_input_hash,
            "effective_input_hash": input_hash,
            "input_hash": input_hash,
            "input_adoption": input_adoption,
            "rejected_inputs": rejected_inputs,
            "model_version": MODEL_VERSION,
            "template_version": TEMPLATE_VERSION,
            "assurance_level": "none",
            "calculation_status": "blocked",
        }
    missing: list[str] = []
    if not (input_revision or {}).get("total_investment_wan"):
        missing.append("total_investment_wan")
    if (
        force_flat
        and (input_revision or {}).get("is_operating") is not False
        and not (input_revision or {}).get("annual_revenue_wan")
    ):
        missing.append("annual_revenue_wan")

    resolved_spec_hash = spec_hash or compute_spec_hash(resolved_spec)
    if spec_hash and isinstance(resolved_spec, dict) and spec_hash != compute_spec_hash(resolved_spec):
        return {
            "ok": False, "available": False, "workspace_id": workspace_id,
            "reason": "spec_hash_mismatch", "missing_inputs": [],
            "blocking_issues": [{"rule": "spec_hash_mismatch", "detail": "spec_hash 与 spec 内容不一致"}],
            "input_hash": input_hash, "spec_hash": compute_spec_hash(resolved_spec),
            "model_version": MODEL_VERSION, "template_version": TEMPLATE_VERSION,
            "assurance_level": "none", "calculation_status": "failed",
        }
    if missing:
        return {
            "ok": False,
            "available": False,
            "workspace_id": workspace_id,
            "missing_inputs": missing,
            "blocking_issues": [{"rule": "missing_inputs", "detail": ",".join(missing)}],
            "input_hash": input_hash,
            "spec_hash": resolved_spec_hash,
            "model_version": MODEL_VERSION,
            "template_version": TEMPLATE_VERSION,
            "assurance_level": "none",
            "calculation_status": "unavailable",
            "reason": f"缺少必要输入：{', '.join(missing)}",
            "finance_inputs": dict((_read_workspace_req(workspace_id)[2]) or {}),
        }

    idem = compute_idempotency_key(
        workspace_id,
        input_hash=input_hash,
        spec_hash=resolved_spec_hash,
        model_version=MODEL_VERSION,
        template_version=TEMPLATE_VERSION,
        manifest_hash=manifest.hash,
        valuation_date=effective_valuation_date,
        basis_of_estimate_hash=basis_of_estimate_hash,
    )

    # 幂等复用
    if not force_recompute:
        try:
            from lvke_mcp.domains.finance import run_store

            existing = run_store.find_run_by_idempotency_key(workspace_id, idem)
            if existing and existing.get("run_id"):
                fin = existing.get("result") or {}
                if fin.get("available"):
                    fin = dict(fin)
                    fin["run_id"] = existing["run_id"]
                    fin["reused_run"] = True
                    fin["idempotency_key"] = idem
                    fin["input_hash"] = input_hash
                    fin["raw_input_hash"] = raw_input_hash
                    fin["effective_input_hash"] = input_hash
                    fin["input_adoption"] = input_adoption
                    fin["rejected_inputs"] = []
                    fin["spec_hash"] = resolved_spec_hash
                    fin["spec_id"] = spec_id
                    fin["basis_of_estimate_id"] = basis_of_estimate_id
                    fin["basis_of_estimate_hash"] = basis_of_estimate_hash
                    fin["model_version"] = MODEL_VERSION
                    fin["template_version"] = TEMPLATE_VERSION
                    fin["model_manifest"] = manifest.to_dict()
                    fin["manifest_hash"] = manifest.hash
                    fin["policy_profile"] = policy_profile
                    fin["industry_profile"] = industry_profile
                    fin["manifest_errors"] = manifest_errors
                    fin["field_source_ledger"] = field_source_ledger
                    fin["industry_required_missing"] = industry_required_missing
                    fin["assurance_level"] = fin.get("assurance_level") or mode
                    fin["calculation_status"] = "computed"
                    fin.pop("review_status", None)
                    # Replays must preserve the original business validity.
                    # An immutable run with failed consistency checks cannot
                    # become an ``ok`` result merely because it was reused.
                    fin["consistency_ok"] = bool(existing.get("consistency_ok"))
                    fin["ok"] = bool(fin.get("available") and fin["consistency_ok"])
                    # Historical result snapshots may contain a manifest that
                    # was rendered before ``run_id`` was allocated.  Never
                    # replay that stale child lineage: rebuild the manifest
                    # from the immutable tables and bind every entry to the
                    # authoritative outer run.
                    fin["table_manifest"] = _table_manifest(fin, existing["run_id"])
                    return fin
        except Exception:  # noqa: BLE001
            pass

    # 确定性计算（禁止 LLM）
    try:
        compute_inputs = dict(input_revision or {})
        # Source coverage is evaluated while the engine renders tables; bind
        # that render to the request workspace rather than a pack self-claim.
        compute_inputs["workspace_id"] = workspace_id
        result = finance_model.compute_financials(
            compute_inputs,
            invest_type=invest_type,
            build_period_months=build_period_months,
            industry=industry,
            spec=resolved_spec,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "available": False,
            "workspace_id": workspace_id,
            "reason": str(exc)[:200],
            "missing_inputs": [],
            "blocking_issues": [{"rule": "compute_failed", "detail": str(exc)[:200]}],
            "input_hash": input_hash,
            "spec_hash": resolved_spec_hash,
            "model_version": MODEL_VERSION,
            "template_version": TEMPLATE_VERSION,
            "assurance_level": "none",
            "calculation_status": "failed",
            "finance_inputs": dict((_read_workspace_req(workspace_id)[2]) or {}),
        }

    result = dict(result or {})
    # A rejected calculation is still an immutable diagnostic snapshot for
    # the caller.  Attach the resolved basis before any fail-closed return so
    # Codex can repair the exact inputs without mistaking the response for a
    # persisted FinanceRun or a deliverable tables package.
    _diagnostic_meta, _diagnostic_req, diagnostic_finance_raw = _read_workspace_req(
        workspace_id,
    )
    result["finance_inputs"] = dict(diagnostic_finance_raw or {})
    result["input_revision"] = dict(input_revision or {})
    result["input_hash"] = input_hash
    result["raw_input_hash"] = raw_input_hash
    result["effective_input_hash"] = input_hash
    result["input_adoption"] = input_adoption
    result["rejected_inputs"] = []
    result["input_revision_id"] = int(input_revision_id or 0)
    result["spec_hash"] = resolved_spec_hash
    result["spec_id"] = spec_id
    result["basis_of_estimate_id"] = basis_of_estimate_id
    result["basis_of_estimate_hash"] = basis_of_estimate_hash
    result["model_version"] = MODEL_VERSION
    result["template_version"] = TEMPLATE_VERSION
    result["fact_pack_projection"] = fact_pack_projection
    result["project_context"] = {
        "invest_type": invest_type,
        "industry": industry,
        "build_period_months": build_period_months,
        "source": str(context.get("source") or "workspace_requirement"),
    }
    result["spec"] = (
        resolved_spec if isinstance(resolved_spec, dict) else result.get("spec")
    )
    nonnegative_issues = _nonnegative_cost_issues(result)
    if nonnegative_issues:
        return {
            "ok": False,
            "available": False,
            "workspace_id": workspace_id,
            "reason": "negative_operating_cost",
            "code": "negative_operating_cost",
            "message": "现金经营成本或工资福利出现负值，拒绝固化财务运行",
            "missing_inputs": [
                "annual_operating_cost_wan",
                "cost_items",
                "operating_cost_by_year",
            ],
            "field_errors": nonnegative_issues,
            "blocking_issues": [{
                "rule": "negative_operating_cost",
                "detail": "须提供可验证的非负经营成本输入，不能用折旧倒推出负现金成本",
            }],
            "input_hash": input_hash,
            "spec_hash": resolved_spec_hash,
            "model_version": MODEL_VERSION,
            "template_version": TEMPLATE_VERSION,
            "assurance_level": "none",
            "calculation_status": "unavailable",
        }

    # A deterministic calculation can still be unusable when its cross-table
    # reconciliation contains a blocking failure.  Do this check before the
    # audit write so a diagnostic result cannot acquire a run_id or become a
    # source for the finance-tables package.
    try:
        consistency = finance_model.check_consistency(result)
    except Exception:  # noqa: BLE001
        consistency = []
    blocking_consistency = [
        item for item in consistency
        if isinstance(item, dict)
        and not item.get("ok")
        and bool(item.get("blocking", True))
    ]
    # Every entry point fails closed before persistence.  A package assembler
    # must never turn an inconsistent diagnostic calculation into a run ID.
    if blocking_consistency:
        result["ok"] = False
        result["available"] = False
        result["consistency_ok"] = False
        result["checks"] = consistency
        result["blocking_issues"] = [
            {
                "rule": item.get("code") or item.get("rule") or "finance_consistency_failed",
                "detail": item.get("detail") or "财务勾稽失败",
            }
            for item in blocking_consistency
        ]
        primary_code = str(blocking_consistency[0].get("code") or "consistency_failed")
        result["reason"] = primary_code
        result["code"] = primary_code
        result["message"] = "财务勾稽未通过，未固化 run 或十三表包"
        result["assurance_level"] = "none"
        result["calculation_status"] = "blocked"
        # These are in-memory diagnostics only.  Keeping rendered tables or a
        # bundle hash here would make a failed calculation look deliverable.
        result.pop("tables", None)
        result.pop("table_manifest", None)
        result.pop("table_bundle_hash", None)
        return result

    result["workspace_id"] = workspace_id
    # 回显用户真源输入（不含联动注入），与历史行为一致
    _meta2, _req2, finance_raw2 = _read_workspace_req(workspace_id)
    result["finance_inputs"] = dict(finance_raw2 or {})
    result["input_revision"] = dict(input_revision or {})
    # Fix-P1-2：注入清单挂到 result 顶层，供 evidence / READABLE 审计
    _inj = (raw_input_revision or {}).get("_auto_injected_cost_items") or []
    if _inj:
        result["_auto_injected_cost_items"] = list(_inj)
        raw = dict(result.get("raw") or {})
        raw["_auto_injected_cost_items"] = list(_inj)
        result["raw"] = raw
    result["input_hash"] = input_hash
    result["raw_input_hash"] = raw_input_hash
    result["effective_input_hash"] = input_hash
    result["input_adoption"] = input_adoption
    result["rejected_inputs"] = []
    result["input_revision_id"] = int(input_revision_id or 0)
    result["spec_hash"] = resolved_spec_hash
    result["model_version"] = MODEL_VERSION
    try:
        from lvke_mcp.domains.finance.feasibility_params import params_basis_block

        result.setdefault("raw", {})
        if isinstance(result.get("raw"), dict):
            result["raw"]["feasibility_params_basis"] = params_basis_block()
    except Exception:  # noqa: BLE001
        pass
    result["template_version"] = TEMPLATE_VERSION
    result["model_manifest"] = manifest.to_dict()
    result["manifest_hash"] = manifest.hash
    result["policy_profile"] = policy_profile
    result["industry_profile"] = industry_profile
    result["manifest_errors"] = manifest_errors
    result["valuation_date"] = effective_valuation_date
    result["selected_scenario_id"] = selected_scenario_id or "base"
    result["field_source_ledger"] = field_source_ledger
    result["industry_required_missing"] = industry_required_missing
    result["fact_pack_projection"] = fact_pack_projection
    result["project_context"] = {
        "invest_type": invest_type,
        "industry": industry,
        "build_period_months": build_period_months,
        "source": str(context.get("source") or "workspace_requirement"),
    }
    result["idempotency_key"] = idem
    result["assurance_level"] = mode if result.get("available") else "none"
    result["calculation_status"] = "computed" if result.get("available") else "unavailable"
    result["agent_trace_id"] = agent_trace_id or ""
    result["tool_call_id"] = tool_call_id or ""
    result["reused_run"] = False
    result["ok"] = bool(result.get("available"))
    result["force_flat"] = bool(force_flat)
    result["spec"] = resolved_spec if isinstance(resolved_spec, dict) else result.get("spec")

    if result.get("available"):
        tables = result.get("tables") or {}
        result["table_bundle_hash"] = compute_table_bundle_hash(tables)
        result["table_manifest"] = _table_manifest(result, run_id="")

    run_id: Optional[str] = None
    if record_audit and result.get("available"):
        try:
            from lvke_mcp.domains.finance import run_store

            run_id = run_store.record_run(
                workspace_id,
                result,
                model_version=MODEL_VERSION,
                input_hash=input_hash,
                idempotency_key=idem,
                template_version=TEMPLATE_VERSION,
                table_bundle_hash=result.get("table_bundle_hash") or "",
                agent_trace_id=agent_trace_id,
                tool_call_id=tool_call_id,
                input_revision=int(input_revision_id or 0),
                result_snapshot=result,
                # force_recompute 必须新建审计身份，不能因幂等键复用旧 run_id
                force_new=bool(force_recompute),
            )
            if run_id:
                # 同步关键正文映射（与 record_finance_run 一致，但不二次 record_run）
                try:
                    run_store.map_key_report_values(
                        workspace_id, run_id, result,
                        report_file=report_file, section=section,
                    )
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            run_id = None

    if run_id:
        result["run_id"] = run_id
        result["table_manifest"] = _table_manifest(result, run_id)
        try:
            from lvke_mcp.domains.finance import run_store

            view = run_store.load_run(workspace_id, run_id) or {}
            result["consistency_ok"] = bool(view.get("consistency_ok"))
        except Exception:  # noqa: BLE001
            result["consistency_ok"] = bool(result.get("consistency_ok"))
        result.pop("review_status", None)
        result.pop("approved_run_id", None)
        result.pop("approved_run_stale", None)
        result["assurance_level"] = mode or "estimate_preview"
        result["calculation_status"] = "computed"

    return result


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


def get_workspace_finance_run(
    workspace_id: str,
    *,
    run_id: str = "",
    view: str = "summary",
) -> dict[str, Any]:
    """纯查询：不重算、默认不写库。

    view:
    - summary: 指标 + 状态
    - full: 完整 result 快照（若有）
    - tables: 仅 tables
    - checks: 勾稽 / issues
    """
    _ensure_workspace(workspace_id)
    from lvke_mcp.domains.finance import run_store

    if run_id:
        audit_view = run_store.load_run(workspace_id, run_id) or {}
    else:
        audit_view = run_store.latest_run(workspace_id) or {}

    if not audit_view:
        # 无 run：返回输入与 unavailable 提示（不触发计算）
        _meta, req, finance_raw = _read_workspace_req(workspace_id)
        return {
            "ok": True,
            "available": False,
            "workspace_id": workspace_id,
            "run_id": None,
            "reason": "no_finance_run",
            "message": "尚无财务模型运行记录；请 POST /finance-runs 或调用 finance_run_model",
            "finance_inputs": dict(finance_raw or {}),
            "calculation_status": "none",
            "assurance_level": "none",
        }

    rid = audit_view.get("run_id")
    snapshot = run_store.load_result_snapshot(workspace_id, rid) if rid else None
    base: dict[str, Any] = {
        "ok": True,
        "workspace_id": workspace_id,
        "run_id": rid,
        "model_version": audit_view.get("model_version"),
        "template_version": audit_view.get("template_version") or TEMPLATE_VERSION,
        "spec_hash": audit_view.get("spec_hash"),
        "input_hash": audit_view.get("input_hash"),
        "input_revision_id": audit_view.get("input_revision"),
        "idempotency_key": audit_view.get("idempotency_key"),
        "table_bundle_hash": audit_view.get("table_bundle_hash"),
        "consistency_ok": bool(audit_view.get("consistency_ok")),
        "available": True,
    }

    if snapshot and isinstance(snapshot, dict):
        # 合并快照（快照优先数值，base 保留审计元数据）
        merged = dict(snapshot)
        merged.update({k: v for k, v in base.items() if v is not None})
        merged["available"] = bool(snapshot.get("available", True))
        if view == "tables":
            return {
                "ok": True,
                "run_id": rid,
                "tables": snapshot.get("tables") or {},
                "table_manifest": _table_manifest(snapshot, rid),
                "table_bundle_hash": base.get("table_bundle_hash"),
            }
        if view == "checks":
            return {
                "ok": True,
                "run_id": rid,
                "consistency_ok": base["consistency_ok"],
                "consistency": audit_view.get("consistency") or [],
                "issues": audit_view.get("issues") or [],
                "checks": snapshot.get("checks") or [],
            }
        if view == "summary":
            return {
                **base,
                "indicators": snapshot.get("indicators") or {},
                "investment": snapshot.get("investment") or {},
                "funding": snapshot.get("funding") or {},
                "summary_md": snapshot.get("summary_md") or "",
                "missing_inputs": snapshot.get("missing_inputs") or [],
                "table_manifest": _table_manifest(snapshot, rid),
                "assurance_level": snapshot.get("assurance_level") or "estimate_preview",
                "calculation_status": "computed",
            }
        # full
        merged["audit"] = {
            "issues": audit_view.get("issues") or [],
            "consistency": audit_view.get("consistency") or [],
            "report_mappings": audit_view.get("report_mappings") or [],
        }
        # Do not preserve a pre-allocation manifest with empty child run_ids.
        merged["table_manifest"] = _table_manifest(merged, rid)
        return merged

    # 无快照：仅返回审计摘要
    if view == "checks":
        return {
            "ok": True,
            "run_id": rid,
            "consistency_ok": base["consistency_ok"],
            "consistency": audit_view.get("consistency") or [],
            "issues": audit_view.get("issues") or [],
        }
    # 从 results 重建粗指标
    indicators = {}
    investment = {}
    funding = {}
    for r in audit_view.get("results") or []:
        code = r.get("element_code")
        val = r.get("value")
        if code in {"project_irr", "npv", "static_payback", "dynamic_payback",
                    "annual_revenue", "annual_net_profit", "annual_total_cost", "bep"}:
            indicators[code] = val
        elif code in {"total_investment", "construction_investment", "working_capital",
                      "interest_during_construction", "fixed_asset"}:
            investment[code] = val
        elif code in {"equity_capital", "loan", "subsidy"}:
            funding[code] = val
    return {
        **base,
        "indicators": indicators,
        "investment": investment,
        "funding": funding,
        "snapshot_missing": True,
        "message": "历史 run 无完整 result 快照；请重新 POST /finance-runs 生成可重放包",
    }


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


# ── helpers ──────────────────────────────────────────────────────────────


def _markdown_table_row_count(md: str) -> int:
    """Count data rows in a rendered Markdown table.

    Delivery tables are stored as GFM Markdown strings (header + ``| --- |``
    separator + data rows), so a plain ``isinstance(list/dict)`` check misses
    them and reports 0.  Count pipe rows, drop the separator row(s), then drop
    one header row; never go below zero.
    """

    data_rows = 0
    saw_header = False
    for line in md.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = stripped.strip("|").split("|")
        if cells and all(re.fullmatch(r"[\s:\-]*", cell) for cell in cells):
            continue  # the ``| --- | --- |`` separator row is not data
        if not saw_header:
            saw_header = True  # first non-separator pipe row is the header
            continue
        data_rows += 1
    return data_rows


def _table_manifest(fin: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    tables = (fin or {}).get("tables") or {}
    out: list[dict[str, Any]] = []
    meta_by_key = {key: (delivery_no, title) for key, delivery_no, title in DELIVERY_TABLE_META}
    for key in DELIVERY_TABLE_KEYS:
        tbl = tables.get(key)
        if tbl is None:
            continue
        delivery_no, title = meta_by_key.get(key, ("", key))
        content = _stable_json(tbl)
        if isinstance(tbl, list):
            row_count = len(tbl)
        elif isinstance(tbl, dict):
            row_count = len(tbl.get("rows") or [])
        elif isinstance(tbl, str):
            row_count = _markdown_table_row_count(tbl)
        else:
            row_count = 0
        out.append({
            "table_id": key,
            "delivery_no": delivery_no,
            "title": title,
            "run_id": run_id or fin.get("run_id") or "",
            "template_version": fin.get("template_version") or TEMPLATE_VERSION,
            "row_count": row_count,
            "content_hash": _sha256_hex(content),
        })
    return out


def _ensure_workspace(workspace_id: str) -> None:
    """确保 MCP workspace 目录存在。"""
    from lvke_mcp.runtime.workspace import workspace_root

    workspace_root(str(workspace_id)).mkdir(parents=True, exist_ok=True)


def _project_brief(workspace_id: str) -> str:
    """项目简述（MCP 边界无资料链；LLM 定 spec 时回退空简述）。"""
    return ""
