"""确定性模型运行事务。"""

from __future__ import annotations

import copy
from typing import Any, Optional


from .base import (
    MODEL_VERSION,
    TEMPLATE_VERSION,
    _ensure_workspace,
    _read_workspace_req,
    _resolve_valuation_date_for_mode,
    _table_manifest,
    compute_idempotency_key,
    compute_input_hash,
    compute_spec_hash,
    compute_table_bundle_hash,
    resolve_model_manifest,
)

from .spec_prepare import (
    _inject_linked_cost_items,
    _nonnegative_cost_issues,
    prepare_workspace_finance_spec,
)


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
    evidence_metadata: Optional[dict[str, Any]] = None,
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
            expected_build_years=max(
                1,
                -(
                    -int(
                        (input_revision or {}).get("build_period_months")
                        or build_period_months
                        or 12
                    )
                    // 12
                ),
            ),
            expected_calc_years=int((input_revision or {}).get("calc_period_years") or 12),
        )
        # A sealed fact pack is the stronger confirmed authority.  Its product
        # tree may legitimately replace a prior flat/candidate spec, so the
        # caller's pre-projection spec hash cannot be reused.
        if fact_pack_projection.get("applied"):
            spec_hash = ""

    confirmed_fact_pack = (
        copy.deepcopy((input_revision or {}).get("finance_fact_pack"))
        if isinstance((input_revision or {}).get("finance_fact_pack"), dict)
        else None
    )

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
    if confirmed_fact_pack is not None and fact_pack_projection.get("applied"):
        # The Fact Pack is non-compute metadata, but it is part of the formal
        # input hash and downstream source-grade verification.  Preserve the
        # exact sealed snapshot after canonical compute fields are resolved.
        input_revision["finance_fact_pack"] = confirmed_fact_pack
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
                    fin.update(dict(evidence_metadata or {}))
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
    result.update(dict(evidence_metadata or {}))
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
