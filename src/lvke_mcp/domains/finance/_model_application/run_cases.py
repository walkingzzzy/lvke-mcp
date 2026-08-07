"""模型运行与 run 读取用例。"""

from __future__ import annotations

from typing import Any
import hashlib
import json
import time

from lvke_mcp.adapters.finance_model_repository import BASIS_OF_ESTIMATE_STORE, IDEMPOTENCY_STORE, SPEC_STORE
from lvke_mcp.runtime.responses import ok
from lvke_mcp.runtime.storage import sha256_json

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

from .spec_cases import (
    _revenue_input_complete,
)


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
    if mode == "review_candidate" and payload.get("confirmation_status") != "confirmed":
        return _blocked_run("spec_confirmation_required", "先调用 finance_confirm_spec 确认候选 Spec", spec_id)
    boe_id = str(args.get("basis_of_estimate_id") or "").strip()
    boe_hash = ""
    boe_payload: dict[str, Any] = {}
    if mode == "review_candidate":
        boe = BASIS_OF_ESTIMATE_STORE.get(workspace_id, boe_id) if boe_id else _latest_formal_boe(workspace_id, spec_id)
        boe_payload = boe.get("payload") if isinstance((boe or {}).get("payload"), dict) else {}
        if boe is None or boe_payload.get("spec_id") != spec_id or not boe_payload.get("formal_ready"):
            return _blocked_run("basis_of_estimate_required", "调用 finance_build_basis_of_estimate 完整绑定重大输入来源", spec_id)
        boe_id, boe_hash = boe["object_id"], boe["basis_hash"]
    elif boe_id:
        boe = BASIS_OF_ESTIMATE_STORE.get(workspace_id, boe_id)
        boe_payload = boe.get("payload") if isinstance((boe or {}).get("payload"), dict) else {}
        if boe is None or boe_payload.get("spec_id") != spec_id or not boe_payload.get("technical_ready"):
            return _blocked_run(
                "basis_of_estimate_invalid",
                "使用同一 spec 的完整 BoE，或省略它运行 estimate preview",
                spec_id,
            )
        boe_hash = boe["basis_hash"]
    if spec is None and not force_flat:
        return _blocked_run("spec_required", "先调用 finance_prepare_spec 固化 spec", spec_id)
    input_revision = args.get("input_revision") if isinstance(args.get("input_revision"), dict) else payload.get("input_revision")
    input_revision = input_revision if isinstance(input_revision, dict) else {}
    if spec_id and not input_revision.get("total_investment_wan"):
        return _missing_run("total_investment_wan", spec_id)
    if not _revenue_input_complete(spec, input_revision):
        return _missing_run("annual_revenue_wan_or_revenue_driver", spec_id)
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
        missing_turnover = [
            name
            for name in ("receivable", "inventory", "cash", "payable")
            if turnover.get(name) is None and turnover.get(f"{name}_days") is None
        ]
        if has_working_capital and missing_turnover:
            missing = [f"wc_turnover.{name}" for name in missing_turnover]
            return _ok_env(
                {
                    "available": False,
                    "error": "working_capital_turnover_required",
                    "missing_inputs": missing,
                    "field_errors": [
                        {
                            "path": f"/input_revision/wc_turnover/{name}",
                            "code": "required_for_review_candidate",
                            "message": f"正式候选缺少 {name} 周转参数",
                        }
                        for name in missing_turnover
                    ],
                },
                source=f"{SERVER_NAME}.finance_run_model",
                status="missing_inputs",
                blockers=["working_capital_turnover_required"],
                next_actions=["补充 wc_turnover 分项周转天数后重新运行"],
                run_id=None,
                missing_inputs=missing,
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
                "evidence_policy": str(boe_payload.get("evidence_policy") or payload.get("evidence_policy") or "formal_evidence"),
                "project_fact_certified": bool(boe_payload.get("project_fact_certified", payload.get("project_fact_certified", False))),
                "reconstruction_records": list(boe_payload.get("reconstruction_records") or payload.get("reconstruction_records") or []),
                "reconstructed_source_ids": list(boe_payload.get("reconstructed_source_ids") or payload.get("reconstructed_source_ids") or []),
                "unresolved_inputs": list(boe_payload.get("unresolved_inputs") or payload.get("unresolved_inputs") or []),
                "release_limitations": list(boe_payload.get("release_limitations") or payload.get("release_limitations") or []),
            },
        )
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
        if data.get("available") and data.get("consistency_ok") is False:
            status = "blocked"
            blockers = _blocking_rules(data) or ["finance_consistency_failed"]
            next_actions = ["修正财务勾稽问题后重新运行；当前 run 不可进入十三表正式候选"]
        elif data.get("available") and run_id:
            status, blockers = "ok", []
            next_actions = ["用 run_id 调用 lvke-finance-tables.tables_render 渲染 13 表"]
        elif missing:
            status = "missing_inputs"
            blockers = [f"缺少必要输入：{item}" for item in missing]
            next_actions = ["补齐缺失输入后重试 finance_run_model"]
        else:
            status = "blocked"
            blockers = _blocking_rules(data) or [str(data.get("reason") or "run_not_available")]
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
            replayed=False,
            reused=False,
            idempotency_expires_at=expires_at,
        )
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
        consistency_failed = bool(
            data.get("available") and data.get("consistency_ok") is False
        )
        read_status = "blocked" if (no_run or consistency_failed) else "ok"
        read_blockers = (
            ["尚无财务模型运行记录"]
            if no_run
            else (["finance_consistency_failed"] if consistency_failed else [])
        )
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_get_run",
            status=read_status,
            resource_uris=[uri] if uri else [],
            blockers=read_blockers,
            next_actions=(
                (["先调用 finance_run_model 生成 run"] if no_run else [])
                or (
                    ["修正财务勾稽问题后重新运行；当前 run 不可作为正式候选"]
                    if consistency_failed
                    else []
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
