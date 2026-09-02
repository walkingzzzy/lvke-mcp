"""模型运行与 run 读取用例。"""

from __future__ import annotations

from typing import Any
import hashlib
import json
import time

from lvke_mcp.adapters.finance_model_repository import BASIS_OF_ESTIMATE_STORE, IDEMPOTENCY_STORE, SPEC_STORE
from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    SIM_A_FORMAL,
    validate_finance_run,
    validate_same_formal_lineage,
)
from lvke_mcp.runtime.evidence_qualification import project_fact_may_be_certified
from lvke_mcp.runtime.responses import ok
from lvke_mcp.runtime.storage import sha256_json

from .spec_cases import _canonical_candidate_inputs

from .base import (
    SERVER_NAME,
    _active_idempotency_record,
    _blocked_run,
    _blocking_rules,
    _err_env,
    _exception_env,
    _expires_at,
    _latest_formal_boe,
    _missing_run,
    _ok_env,
    _run_uri,
    _str_list,
    _workspace_id,
)

from lvke_mcp.domains.finance.rail_validation import (
    rail_transit_missing_inputs as _rail_transit_missing_inputs,
    revenue_input_complete as _revenue_input_complete,
)
from lvke_mcp.domains.finance.scale_reconciliation import check_finance_run_scale


def run_model(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_id(args)
    if not workspace_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    key = str(args.get("idempotency_key") or "").strip()
    if not key:
        return _err_env(
            f"{SERVER_NAME}.idempotency_key_required",
            "finance_run_model 写操作必须提供 idempotency_key",
            status="blocked",
            run_id=None,
            missing_inputs=[],
        )
    mode = str(args.get("mode") or "estimate_preview")
    mode = mode if mode in {"estimate_preview", "review_candidate"} else "estimate_preview"
    request_basis = {k: v for k, v in args.items() if k not in {"idempotency_key", "agent_trace_id", "tool_call_id"}}
    request_basis["mode"] = mode
    fingerprint = sha256_json(request_basis)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    prior = _active_idempotency_record(workspace_id, key_hash, "finance_run_model")
    if prior is not None:
        saved = prior.get("payload") or {}
        if saved.get("content_fingerprint") != fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已用于不同的财务运行请求",
                status="blocked",
                blockers=["idempotency_conflict"],
                next_actions=["使用新的 idempotency_key 提交变更后的财务请求"],
                run_id=None,
                original_run_id=saved.get("run_id"),
                missing_inputs=[],
                replayed=False,
            )
        if saved.get("in_progress") is True:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                time.sleep(0.05)
                latest = _active_idempotency_record(
                    workspace_id, key_hash, "finance_run_model"
                )
                latest_payload = (latest or {}).get("payload") or {}
                if latest is not None and latest_payload.get("in_progress") is not True:
                    replay = json.loads(json.dumps(latest_payload.get("result") or {}))
                    if replay.get("evidence_policy") == SIM_A_FORMAL:
                        try:
                            validate_finance_run(workspace_id, str(replay.get("run_id") or ""))
                        except FormalLineageError as exc:
                            return _err_env(
                                f"{SERVER_NAME}.{exc.code}",
                                exc.message,
                                status="blocked",
                                blockers=[exc.code],
                                run_id=replay.get("run_id"),
                            )
                    replay.update({"replayed": True, "reused": True})
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
        replay = json.loads(json.dumps(saved.get("result") or {}))
        if replay.get("evidence_policy") == SIM_A_FORMAL:
            try:
                validate_finance_run(workspace_id, str(replay.get("run_id") or ""))
            except FormalLineageError as exc:
                return _err_env(
                    f"{SERVER_NAME}.{exc.code}",
                    exc.message,
                    status="blocked",
                    blockers=[exc.code],
                    run_id=replay.get("run_id"),
                )
        replay.update({"replayed": True, "reused": True})
        return replay
    spec_id = str(args.get("spec_id") or "").strip()
    force_flat = bool(args.get("force_flat") or False)
    spec = args.get("spec") if isinstance(args.get("spec"), dict) else None
    if spec_id and (spec is not None or force_flat):
        return _err_env(f"{SERVER_NAME}.invalid_argument", "spec_id 与 spec/force_flat 不可同时传入", status="blocked")
    stored = SPEC_STORE.get(workspace_id, spec_id) if spec_id else None
    if spec_id and stored is None:
        return _err_env(f"{SERVER_NAME}.spec_not_found", "未找到已固化 FinanceSpec", status="blocked")
    payload = stored.get("payload") if isinstance((stored or {}).get("payload"), dict) else {}
    if stored:
        spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else None
    preflight_quality_issues: list[str] = []
    if mode == "review_candidate" and payload.get("confirmation_status") != "confirmed":
        preflight_quality_issues.append("spec_confirmation_missing")
    boe_id = str(args.get("basis_of_estimate_id") or "").strip()
    boe_hash = ""
    boe_payload: dict[str, Any] = {}
    boe: dict[str, Any] | None = None
    if mode == "review_candidate":
        boe = BASIS_OF_ESTIMATE_STORE.get(workspace_id, boe_id) if boe_id else _latest_formal_boe(workspace_id, spec_id)
        boe_payload = boe.get("payload") if isinstance((boe or {}).get("payload"), dict) else {}
        if boe is None or boe_payload.get("spec_id") != spec_id or not boe_payload.get("formal_ready"):
            preflight_quality_issues.append("basis_of_estimate_incomplete")
        elif boe is not None:
            boe_id, boe_hash = boe["object_id"], boe["basis_hash"]
    elif boe_id:
        boe = BASIS_OF_ESTIMATE_STORE.get(workspace_id, boe_id)
        boe_payload = boe.get("payload") if isinstance((boe or {}).get("payload"), dict) else {}
        if boe is None or boe_payload.get("spec_id") != spec_id or not boe_payload.get("technical_ready"):
            preflight_quality_issues.append("basis_of_estimate_incomplete")
        elif boe is not None:
            boe_hash = boe["basis_hash"]
    canonical_lineage: dict[str, Any] = {}
    parent_policies = {
        str(item.get("evidence_policy") or "")
        for item in (payload, boe_payload)
        if isinstance(item, dict) and item
    }
    if SIM_A_FORMAL in parent_policies:
        if boe is None or stored is None or parent_policies != {SIM_A_FORMAL}:
            return _err_env(
                f"{SERVER_NAME}.formal_lineage_parent_mismatch",
                "sim_a_formal FinanceRun 必须绑定同一工作区的正式 FinanceSpec 与 BoE",
                status="blocked",
                blockers=["formal_lineage_parent_mismatch"],
                run_id=None,
            )
        try:
            canonical_lineage = validate_same_formal_lineage(workspace_id, [stored, boe])
        except FormalLineageError as exc:
            return _err_env(
                f"{SERVER_NAME}.{exc.code}",
                exc.message,
                status="blocked",
                blockers=[exc.code],
                run_id=None,
            )
    if spec is None and not force_flat:
        preflight_quality_issues.append("finance_spec_missing_using_flat_baseline")
    input_revision = args.get("input_revision") if isinstance(args.get("input_revision"), dict) else payload.get("input_revision")
    input_revision = input_revision if isinstance(input_revision, dict) else {}
    if not input_revision and spec is not None:
        # 内联 spec 路径：`finance_prepare_spec` 会把 spec 里的扁平财务字段抽成
        # `input_revision` 并随 Spec 一起固化，走 spec_id 时这份 revision 已在
        # payload 里。直接传 spec 时没有那一步，若不在这里补抽，计算层读到的
        # 是空 revision —— 总投资、资金结构、行业全部落到通用默认值，却只留
        # 一条不阻断的 `total_investment_missing_using_default`，最终产出一份
        # 与调用方输入完全无关的十三表。所以复用同一个规范化函数。
        derived_inputs, _adoption, derived_rejections = _canonical_candidate_inputs(spec, None, {})
        if derived_rejections:
            return _err_env(
                f"{SERVER_NAME}.candidate_input_invalid",
                "内联 spec 的财务输入字段非法或冲突，无法派生 input_revision",
                status="blocked",
                blockers=["candidate_input_invalid"],
                field_errors=[
                    {
                        "path": str(item.get("path") or f"/spec/{item.get('input') or 'unknown'}"),
                        "code": str(item.get("reason") or "candidate_input_invalid"),
                        "input": item.get("input"),
                        **({"conflicts_with": item.get("conflicts_with")} if item.get("conflicts_with") else {}),
                    }
                    for item in derived_rejections
                ],
                next_actions=[
                    "修正 spec 中的财务输入字段后重试",
                    "或改用 finance_prepare_spec → finance_confirm_spec → finance_run_model(spec_id=...)",
                ],
                run_id=None,
                missing_inputs=[],
            )
        input_revision = derived_inputs
    if not input_revision.get("total_investment_wan"):
        preflight_quality_issues.append("total_investment_missing_using_default")
    rail_missing = _rail_transit_missing_inputs(
        spec,
        input_revision,
        build_period_months=input_revision.get("build_period_months"),
    )
    preflight_quality_issues.extend(f"missing_input:{item}" for item in rail_missing)
    if rail_missing:
        # 城轨这几个字段没有可用的通用默认值：建设期、计算期、资本金比例、
        # 贷款条件、折现率、成本项、财政补贴口径缺一个，模型只能拿通用默认
        # 值顶上，算出来的 IRR/DSCR 与这条线路无关。所以缺失即 missing_inputs
        # 且不建 run，而不是"照算并附一条质量提示"。
        return _ok_env(
            {
                "available": False,
                "error": "missing_inputs",
                "missing_inputs": list(rail_missing),
                "spec_id": spec_id,
                "quality_issues": sorted(set(preflight_quality_issues)),
            },
            source=f"{SERVER_NAME}.finance_run_model",
            status="missing_inputs",
            blockers=[f"缺少城轨必需输入：{item}" for item in rail_missing],
            warnings=[f"质量提示：{item}" for item in sorted(set(preflight_quality_issues))],
            next_actions=[
                "补齐 input_revision 中的城轨治理输入后重试；这些字段没有通用默认值",
            ],
            run_id=None,
            spec_id=spec_id or None,
            missing_inputs=list(rail_missing),
            quality_issues=sorted(set(preflight_quality_issues)),
        )
    if not _revenue_input_complete(spec, input_revision):
        preflight_quality_issues.append("revenue_driver_missing_using_default")
    scale_check = check_finance_run_scale(spec, input_revision)
    scale_blockers: list[str] = []
    if not scale_check["ok"]:
        scale_codes = [
            str(item.get("code") or "project_scale_inconsistent")
            for item in scale_check["issues"]
        ]
        preflight_quality_issues.extend(scale_codes)
        # 尺度对账失败是阻断项，不是质量提示。投资额与行业规模量级不符时
        # 继续建 FinanceRun 等于让后续十三表、报告和审查全部建立在一个
        # 不可信的规模基准上；那时再提示已经太晚。城轨 Skill 的要求是
        # 这种情况下不得创建 FinanceRun。
        scale_blockers = sorted(set(scale_codes))
    if mode == "review_candidate" and bool(input_revision.get("is_operating")):
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
        if not turnover and isinstance(input_revision.get("finance_fact_pack"), dict):
            pack = input_revision.get("finance_fact_pack") or {}
            from lvke_mcp.domains.finance.fact_pack import verify_fact_pack_seal

            pack_seal = verify_fact_pack_seal(pack, workspace_id=workspace_id)
            pack_domains = pack.get("domains") if isinstance(pack.get("domains"), dict) else {}
            pack_turnover = pack_domains.get("wc_turnover")
            if pack_seal.get("ok") and isinstance(pack_turnover, dict):
                turnover = pack_turnover
                input_revision["wc_turnover"] = pack_turnover
        from lvke_mcp.domains.finance.working_capital import (
            apply_operating_turnover_to_inputs,
            turnover_component_present,
        )

        injected_turnover = apply_operating_turnover_to_inputs(input_revision)
        turnover = input_revision.get("wc_turnover")
        turnover = turnover if isinstance(turnover, dict) else {}
        if injected_turnover:
            preflight_quality_issues.extend(
                f"injected_default:wc_turnover.{name}" for name in injected_turnover
            )
        missing_turnover = [
            name
            for name in ("receivable", "inventory", "cash", "payable")
            if not turnover_component_present(turnover, name)
        ]
        if has_working_capital and missing_turnover:
            preflight_quality_issues.extend(
                f"missing_input:wc_turnover.{name}" for name in missing_turnover
            )
    if scale_blockers:
        # 在预留幂等键与调用模型之前短路：一旦建了 run，下游十三表与报告
        # 就会引用一个规模基准不可信的对象，事后提示无法收回。
        detail = "；".join(
            str(item.get("detail") or item.get("code") or "")
            for item in scale_check["issues"]
        )
        return _ok_env(
            {
                "available": False,
                "error": scale_blockers[0],
                "spec_id": spec_id,
                "scale_check": scale_check,
                "quality_issues": sorted(set(preflight_quality_issues)),
            },
            source=f"{SERVER_NAME}.finance_run_model",
            status="blocked",
            blockers=scale_blockers,
            warnings=[f"质量提示：{item}" for item in sorted(set(preflight_quality_issues))],
            next_actions=[
                "核对投资额与行业规模量级（线路长度、车站数、建设期）后重新提交 InputRevision",
            ],
            run_id=None,
            spec_id=spec_id or None,
            missing_inputs=[],
            quality_issues=sorted(set(preflight_quality_issues)),
            scale_check=scale_check,
            message=detail or "项目规模与投资额对账不一致",
        )
    expires_at = _expires_at()
    IDEMPOTENCY_STORE.put(
        workspace_id,
        {
            "operation": "finance_run_model",
            "key_hash": key_hash,
            "content_fingerprint": fingerprint,
            "expires_at": expires_at,
            "run_id": None,
            "in_progress": True,
        },
        producer=f"{SERVER_NAME}.finance_run_model",
        basis={
            "operation": "finance_run_model",
            "key_hash": key_hash,
            "content_fingerprint": fingerprint,
        },
    )
    from lvke_mcp.domains.finance import run_service

    try:
        data = run_service.run_workspace_finance_model(
            workspace_id,
            spec=spec,
            spec_id=spec_id,
            spec_hash=str(payload.get("spec_hash") or args.get("spec_hash") or ""),
            basis_of_estimate_id=boe_id,
            basis_of_estimate_hash=boe_hash,
            input_revision=input_revision,
            input_revision_id=(
                int(args.get("input_revision_id", payload.get("input_revision_id")))
                if args.get("input_revision_id", payload.get("input_revision_id")) is not None
                else None
            ),
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
                if isinstance(args.get("requested_manifest"), dict)
                else None
            ),
            selected_scenario_id=str(args.get("selected_scenario_id") or "base"),
            evidence_metadata={
                **canonical_lineage,
                "evidence_policy": str(canonical_lineage.get("evidence_policy") or boe_payload.get("evidence_policy") or payload.get("evidence_policy") or "formal_evidence"),
                "evidence_origin": str(canonical_lineage.get("evidence_origin") or boe_payload.get("evidence_origin") or payload.get("evidence_origin") or ""),
                "project_fact_certified": bool(canonical_lineage.get("project_fact_certified", False)),
                "reconstruction_records": list(boe_payload.get("reconstruction_records") or payload.get("reconstruction_records") or []),
                "reconstructed_source_ids": list(boe_payload.get("reconstructed_source_ids") or payload.get("reconstructed_source_ids") or []),
                "unresolved_inputs": list(boe_payload.get("unresolved_inputs") or payload.get("unresolved_inputs") or []),
                "release_limitations": [
                    *list(boe_payload.get("release_limitations") or payload.get("release_limitations") or []),
                    *preflight_quality_issues,
                ],
            },
        )
        data = dict(data or {})
        data["scale_check"] = scale_check
        engine_missing = [
            str(item).strip() for item in data.get("missing_inputs") or [] if str(item).strip()
        ]
        missing_quality = [
            item if item.startswith("missing_input:") else f"missing_input:{item}"
            for item in engine_missing
        ]
        data["quality_issues"] = sorted(set([
            *preflight_quality_issues,
            *[str(item) for item in data.get("quality_issues") or []],
            *missing_quality,
        ]))
        data["warnings"] = [
            *list(data.get("warnings") or []),
            *(f"质量提示：{item}" for item in preflight_quality_issues),
        ]
        # Preserve reconciliation blockers from the engine/run layer. Clearing
        # them on every available result made consistency_ok=false appear safe
        # to callers and caused F-9 in live acceptance.
        data["blocking_issues"] = [
            item for item in (data.get("blocking_issues") or [])
            if isinstance(item, dict)
        ]
        run_id = str(data.get("run_id") or "") or None
        if data.get("available") and not run_id:
            data = dict(data)
            data.update(
                {
                    "available": False,
                    "ok": False,
                    "calculation_status": "failed",
                    "reason": "finance_run_persistence_failed",
                    "blocking_issues": [
                        *list(data.get("blocking_issues") or []),
                        {
                            "rule": "finance_run_persistence_failed",
                            "detail": "财务计算结果未形成可读取的不可变 FinanceRun",
                        },
                    ],
                }
            )
        uri = _run_uri(workspace_id, run_id)
        if uri:
            data["resource_uri"] = uri
        missing = _str_list(data.get("missing_inputs"))
        viability_status = str(data.get("viability_status") or "not_assessed")
        viability_issues = list(data.get("viability_issues") or [])
        quality_issues = [str(item) for item in data.get("quality_issues") or []]
        if data.get("available") and run_id:
            blockers = [
                str(item.get("rule") or item.get("code") or "finance_consistency_failed")
                for item in data.get("blocking_issues") or []
            ]
            # A persisted run with an integrity blocker remains readable for
            # technical diagnosis, but must not advertise business success.
            # ``blocked`` keeps the preview available while making the release
            # gate explicit in the outer envelope.
            status = "blocked" if blockers else ("partial" if quality_issues else "ok")
            if blockers:
                next_actions = [
                    "先修复财务勾稽阻断项，再继续后续正式流程",
                    "如需技术排查，可生成受限预览；不得将当前 run 视为正式候选",
                ]
            elif viability_issues:
                next_actions = [
                    "项目经济性结论为负面；先核对财务勾稽与风险项",
                    "当前运行仅可作受限技术预览，修复可行性问题后再用于正式链",
                ]
            else:
                next_actions = ["用 run_id 调用 lvke-finance-tables.tables_render 渲染 14 张交付表"]
        elif missing:
            status = "partial"
            blockers = []
            next_actions = ["当前结果已保留诊断；补充输入可提高估算置信度"]
        else:
            status = "blocked"
            blockers = _blocking_rules(data) or [str(data.get("reason") or "run_not_available")]
            next_actions = (
                ["检查财务审计存储后重试；计算结果未成功持久化"]
                if data.get("reason") == "finance_run_persistence_failed"
                else ["检查参数格式或计算异常后重试"]
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
            viability_status=viability_status,
            viability_issues=viability_issues,
            replayed=False,
            reused=False,
            idempotency_expires_at=expires_at,
        )
    except FormalLineageError as exc:
        result = _err_env(
            f"{SERVER_NAME}.{exc.code}",
            exc.message,
            status="blocked",
            blockers=[exc.code],
            run_id=None,
        )
        run_id = None
    except Exception:  # noqa: BLE001
        result = _exception_env(
            "finance_run_model failed",
            f"{SERVER_NAME}.run_failed",
            "运行财务模型失败",
        )
        run_id = None
    IDEMPOTENCY_STORE.put(
        workspace_id,
        {
            "operation": "finance_run_model",
            "key_hash": key_hash,
            "content_fingerprint": fingerprint,
            "expires_at": expires_at,
            "run_id": run_id,
            "result": json.loads(json.dumps(result, ensure_ascii=False, default=str)),
        },
        producer=f"{SERVER_NAME}.finance_run_model",
        source_ids=[run_id] if run_id else [],
        basis={
            "operation": "finance_run_model",
            "key_hash": key_hash,
            "content_fingerprint": fingerprint,
        },
    )
    return result


def get_run(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_id(args)
    if not workspace_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    view = str(args.get("view") or "summary")
    try:
        from lvke_mcp.domains.finance import run_service

        data = run_service.get_workspace_finance_run(
            workspace_id,
            run_id=str(args.get("run_id") or ""),
            view=view,
        )
        if data.get("available") and data.get("consistency_ok") is False:
            data = dict(data)
            data.setdefault("reason", "consistency_failed")
        run_id = str(data.get("run_id") or "") or None
        uri = _run_uri(workspace_id, run_id)
        if uri:
            data["resource_uri"] = uri
        no_run = data.get("available") is False and not run_id
        read_blockers = ["尚无财务模型运行记录"] if no_run else []
        consistency_failed = bool(
            data.get("available") and data.get("consistency_ok") is False
        )
        quality_issues = [str(item) for item in data.get("quality_issues") or []]
        if consistency_failed and "finance_consistency_failed" not in quality_issues:
            quality_issues.append("finance_consistency_failed")
        data["quality_issues"] = quality_issues
        blocking_issues = [
            item for item in (data.get("blocking_issues") or [])
            if isinstance(item, dict)
        ]
        data["blocking_issues"] = blocking_issues
        read_blockers.extend(
            str(item.get("rule") or item.get("code") or "finance_consistency_failed")
            for item in blocking_issues
        )
        read_status = (
            "blocked"
            if no_run or blocking_issues
            else ("partial" if consistency_failed else "ok")
        )
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_get_run",
            status=read_status,
            resource_uris=[uri] if uri else [],
            blockers=read_blockers,
            next_actions=(
                ["先调用 finance_run_model 生成 run"]
                if no_run
                else (
                    [
                        "先修复财务勾稽阻断项；当前 run 仅可作受限技术预览",
                        "修复后重新运行并重新校验，再继续正式表格/报告流程",
                    ]
                    if consistency_failed else []
                )
            ),
            run_id=run_id,
            view=view,
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_get_run failed",
            f"{SERVER_NAME}.get_failed",
            "读取财务 run 失败",
        )

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "BASIS_OF_ESTIMATE_STORE",
    "FormalLineageError",
    "IDEMPOTENCY_STORE",
    "SERVER_NAME",
    "SIM_A_FORMAL",
    "SPEC_STORE",
    "_active_idempotency_record",
    "_blocked_run",
    "_blocking_rules",
    "_err_env",
    "_exception_env",
    "_expires_at",
    "_latest_formal_boe",
    "_missing_run",
    "_ok_env",
    "_rail_transit_missing_inputs",
    "_revenue_input_complete",
    "_run_uri",
    "_str_list",
    "_workspace_id",
    "check_finance_run_scale",
    "get_run",
    "hashlib",
    "json",
    "ok",
    "project_fact_may_be_certified",
    "run_model",
    "sha256_json",
    "time",
    "validate_finance_run",
    "validate_same_formal_lineage",
]
