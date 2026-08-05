"""Application use cases for immutable finance specifications and runs."""

from __future__ import annotations

from typing import Any
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
from lvke_mcp.adapters.finance_model_repository import (
    BASIS_OF_ESTIMATE_STORE,
    IDEMPOTENCY_STORE,
    SPEC_STORE,
)
from lvke_mcp.domains.finance.parameter_resolver import (
    canonicalize_finance_inputs,
    finance_input_schema,
)
from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.responses import err, ok
from lvke_mcp.runtime.storage import sha256_json

SERVER_NAME = "lvke-finance-model"
logger = get_logger(SERVER_NAME)


def prepare_spec(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_id(args)
    if not workspace_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    evidence_ids = _str_list(args.get("evidence_pack_ids"))
    evidence_records = []
    for evidence_id in evidence_ids:
        record = EVIDENCE_STORE.get(workspace_id, evidence_id)
        if record is None:
            return _err_env(
                f"{SERVER_NAME}.evidence_pack_not_found",
                f"未找到 evidence pack：{evidence_id}",
                status="blocked",
                blockers=[f"evidence_pack_not_found:{evidence_id}"],
            )
        evidence_records.append(record)
    try:
        from lvke_mcp.domains.finance import run_service

        supplied_spec = args.get("spec") if isinstance(args.get("spec"), dict) else None
        if supplied_spec is not None:
            supplied_spec = dict(supplied_spec)
            revenue = supplied_spec.get("revenue")
            if isinstance(revenue, dict) and str(revenue.get("model") or "") == "tourism":
                from lvke_mcp.domains.finance.revenue_models import normalize_tourism_revenue

                normalized_revenue, revenue_errors = normalize_tourism_revenue(revenue)
                if revenue_errors:
                    return _ok_env(
                        {"available": False, "missing_inputs": []},
                        source=f"{SERVER_NAME}.finance_prepare_spec",
                        status="blocked",
                        blockers=["revenue_component_conflict"],
                        field_errors=revenue_errors,
                        next_actions=["修正文旅收入组件与兼容别名冲突后重试"],
                    )
                supplied_spec["revenue"] = normalized_revenue
        data = run_service.prepare_workspace_finance_spec(
            workspace_id,
            strategy=str(args.get("strategy") or "propose_from_project"),
            force_refresh=bool(args.get("force_refresh") or False),
            force_flat=bool(args.get("force_flat", supplied_spec is None)),
        )
        if supplied_spec is not None:
            data["spec"] = supplied_spec
            data["spec_hash"] = run_service.compute_spec_hash(supplied_spec)
            data["force_flat"] = False
        normalized_inputs, adoption, rejected = _canonical_candidate_inputs(
            supplied_spec,
            args.get("input_revision") if isinstance(args.get("input_revision"), dict) else None,
            data.get("input_revision") if isinstance(data.get("input_revision"), dict) else {},
        )
        if rejected:
            return _ok_env(
                {
                    "available": False,
                    "missing_inputs": [],
                    "input_rejections": rejected,
                    "input_adoption_ledger": adoption,
                },
                source=f"{SERVER_NAME}.finance_prepare_spec",
                status="blocked",
                blockers=["candidate_input_invalid"],
                field_errors=[
                    {
                        "path": str(item.get("path") or f"/input_revision/{item.get('input') or 'unknown'}"),
                        "code": str(item.get("reason") or "candidate_input_invalid"),
                        "input": item.get("input"),
                        **({"conflicts_with": item.get("conflicts_with")} if item.get("conflicts_with") else {}),
                    }
                    for item in rejected
                ],
                next_actions=["修正未知、冲突或非法的 input_revision 字段后重试"],
            )
        data["input_revision"] = normalized_inputs
        data["input_adoption_ledger"] = adoption
        data["input_hash"] = run_service.compute_input_hash(
            normalized_inputs,
            invest_type=str(data.get("invest_type") or normalized_inputs.get("invest_type") or ""),
            build_period_months=data.get("build_period_months") or normalized_inputs.get("build_period_months"),
            industry=str(data.get("industry") or normalized_inputs.get("industry") or ""),
        )
        missing = [] if normalized_inputs.get("total_investment_wan") else ["total_investment_wan"]
        spec = data.get("spec") if isinstance(data.get("spec"), dict) else None
        if not _revenue_input_complete(spec or supplied_spec, normalized_inputs):
            missing.append("annual_revenue_wan_or_revenue_driver")
        if spec is None:
            missing.append("finance_spec")
        evidence_binding_hash = sha256_json(
            {
                "evidence_pack_ids": evidence_ids,
                "evidence_basis_hashes": [record.get("basis_hash") for record in evidence_records],
            }
        )
        spec_record = None
        if spec is not None:
            spec_record = SPEC_STORE.put(
                workspace_id,
                {
                    "spec": spec,
                    "spec_hash": data.get("spec_hash"),
                    "input_revision": normalized_inputs,
                    "input_hash": data.get("input_hash"),
                    "input_revision_id": data.get("input_revision_id"),
                    "confirmation_status": "candidate",
                    "evidence_pack_ids": evidence_ids,
                    "evidence_binding_hash": evidence_binding_hash,
                },
                producer=f"{SERVER_NAME}.finance_prepare_spec",
                status="missing_inputs" if missing else "ok",
                source_ids=evidence_ids,
                basis={
                    "spec_hash": data.get("spec_hash"),
                    "input_hash": data.get("input_hash"),
                    "evidence_binding_hash": evidence_binding_hash,
                },
            )
            data["spec_id"] = spec_record["object_id"]
            data["evidence_binding_hash"] = evidence_binding_hash
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_prepare_spec",
            status="missing_inputs" if missing else "ok",
            warnings=_str_list(data.get("warnings")),
            blockers=[f"缺少关键输入：{item}" for item in missing],
            next_actions=(
                ["补齐缺失输入后重新调用 finance_prepare_spec"]
                if missing
                else ["调用 finance_confirm_spec 确认候选 Spec，再调用 finance_run_model"]
            ),
            resource_uris=[spec_record["resource_uri"]] if spec_record else [],
            spec_id=spec_record["object_id"] if spec_record else None,
            spec_hash=data.get("spec_hash"),
            evidence_binding_hash=evidence_binding_hash,
            missing_inputs=missing,
            assumptions_to_confirm=_str_list(data.get("assumptions_to_confirm")),
            input_hash=data.get("input_hash"),
            input_revision_id=data.get("input_revision_id"),
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_prepare_spec failed",
            f"{SERVER_NAME}.prepare_failed",
            "准备 FinanceSpec 失败",
        )


def confirm_spec(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_id(args)
    spec_id = str(args.get("spec_id") or "").strip()
    if not workspace_id or not spec_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 与 spec_id 必填")
    source = SPEC_STORE.get(workspace_id, spec_id)
    if source is None:
        return _err_env(f"{SERVER_NAME}.spec_not_found", "未找到候选 FinanceSpec", status="blocked")
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else None
    if spec is None:
        return _err_env(f"{SERVER_NAME}.spec_invalid", "候选 FinanceSpec 快照无效", status="blocked")
    input_revision = payload.get("input_revision") if isinstance(payload.get("input_revision"), dict) else {}
    missing = [] if input_revision.get("total_investment_wan") else ["total_investment_wan"]
    if not _revenue_input_complete(spec, input_revision):
        missing.append("annual_revenue_wan_or_revenue_driver")
    from lvke_mcp.domains.finance.spec import mark_spec_confirmed, validate_for_formal

    confirmed = mark_spec_confirmed(spec)
    formal_ok, formal_errors = validate_for_formal(confirmed)
    if missing or not formal_ok:
        return _ok_env(
            {
                "spec_id": spec_id,
                "valid": False,
                "missing_inputs": missing,
                "validation_errors": formal_errors,
            },
            source=f"{SERVER_NAME}.finance_confirm_spec",
            status="blocked",
            blockers=[*(f"missing_input:{item}" for item in missing), *formal_errors],
            next_actions=["修正候选 Spec 或补齐输入后重新 prepare，再确认新候选"],
        )
    key = str(args.get("idempotency_key") or "").strip()
    if not key:
        return _err_env(
            f"{SERVER_NAME}.idempotency_key_required",
            "finance_confirm_spec 写操作必须提供 idempotency_key",
            status="blocked",
        )
    note = str(args.get("note") or "")
    fingerprint = sha256_json(
        {"spec_id": spec_id, "spec_content_hash": source.get("content_hash"), "note": note}
    )
    key_hash = "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()
    prior = _active_idempotency_record(workspace_id, key_hash, "finance_confirm_spec")
    if prior is not None:
        saved = prior.get("payload") or {}
        if saved.get("content_fingerprint") != fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已绑定不同 FinanceSpec 确认请求",
                status="blocked",
                replayed=False,
                reused=False,
            )
        replay = json.loads(json.dumps(saved.get("result") or {}))
        replay.update({"replayed": True, "reused": True})
        return replay
    from lvke_mcp.domains.finance.run_service import compute_spec_hash

    record = SPEC_STORE.put(
        workspace_id,
        {
            **payload,
            "spec": confirmed,
            "spec_hash": compute_spec_hash(confirmed),
            "confirmation_status": "confirmed",
            "parent_spec_id": spec_id,
            "confirmation": {"note": note},
        },
        producer=f"{SERVER_NAME}.finance_confirm_spec",
        status="ok",
        source_ids=[spec_id, *_str_list(payload.get("evidence_pack_ids"))],
        basis={"parent_spec_id": spec_id, "spec_hash": compute_spec_hash(confirmed), "note": note},
    )
    expires_at = _expires_at()
    result = _ok_env(
        {"spec_id": record["object_id"], "parent_spec_id": spec_id, "spec_hash": record["payload"]["spec_hash"]},
        source=f"{SERVER_NAME}.finance_confirm_spec",
        status="ok",
        resource_uris=[record["resource_uri"]],
        next_actions=["调用 finance_run_model，传入已确认 spec_id"],
        spec_id=record["object_id"],
        spec_hash=record["payload"]["spec_hash"],
        content_fingerprint=fingerprint,
        replayed=False,
        reused=False,
        idempotency_expires_at=expires_at,
    )
    IDEMPOTENCY_STORE.put(
        workspace_id,
        {"operation": "finance_confirm_spec", "key_hash": key_hash, "content_fingerprint": fingerprint, "expires_at": expires_at, "result": result},
        producer=f"{SERVER_NAME}.finance_confirm_spec",
        source_ids=[record["object_id"]],
        basis={"operation": "finance_confirm_spec", "key_hash": key_hash, "content_fingerprint": fingerprint},
    )
    return result


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


def validate_spec(args: dict[str, Any]) -> dict[str, Any]:
    spec = args.get("spec")
    if not isinstance(spec, dict):
        return _err_env(f"{SERVER_NAME}.invalid_argument", "spec 必填且必须是对象")
    try:
        from lvke_mcp.domains.finance.spec import validate, validate_for_formal

        structural_ok, errors = validate(spec)
        formal = bool(args.get("for_formal", False))
        formal_ok, formal_errors = validate_for_formal(spec) if formal else (structural_ok, [])
        errors = _unique_strings(errors)
        formal_errors = _unique_strings(formal_errors)
        valid = bool(structural_ok and (formal_ok if formal else True))
        missing = [
            item
            for item in [*errors, *formal_errors]
            if any(word in item for word in ("缺", "missing", "尚未确认"))
        ]
        status = "missing_inputs" if missing else ("ok" if valid else "blocked")
        return _ok_env(
            {
                "valid": valid,
                "structural_valid": structural_ok,
                "formal_valid": formal_ok if formal else None,
                "errors": errors,
                "formal_errors": formal_errors,
                "missing_inputs": missing,
                "note": "缺关键输入时不得运行出 IRR；校验状态由结构、输入与一致性检查决定。",
            },
            source=f"{SERVER_NAME}.finance_validate_spec",
            status=status,
            blockers=[] if valid else _unique_strings([*errors, *formal_errors]),
            next_actions=(
                ["按 errors/missing_inputs 修正 spec 后重新校验"]
                if not valid or missing
                else ["spec 可用于 finance_run_model；生成固化 run 后仍须调用统一审查"]
            ),
            valid=valid,
            missing_inputs=_str_list(missing),
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_validate_spec failed",
            f"{SERVER_NAME}.validate_failed",
            "校验 FinanceSpec 失败",
        )


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


def _workspace_id(args: dict[str, Any]) -> str | None:
    value = args.get("workspace_id")
    return str(value).strip() if value is not None and str(value).strip() else None


def _run_uri(workspace_id: str, run_id: str | None) -> str | None:
    if not run_id:
        return None
    return f"lvke://finance-model/workspaces/{workspace_id}/runs/{run_id}"


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _unique_strings(value: Any) -> list[str]:
    return list(dict.fromkeys(_str_list(value)))


def _canonical_candidate_inputs(
    supplied_spec: dict[str, Any] | None,
    explicit_revision: dict[str, Any] | None,
    workspace_revision: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    spec_inputs: dict[str, Any] = {}
    if isinstance(supplied_spec, dict):
        nested = supplied_spec.get("finance_inputs")
        if isinstance(nested, dict):
            spec_inputs.update(nested)
        for key in set(finance_input_schema().get("properties") or {}):
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
    normalized_sources = []
    for source_name, values in (
        ("candidate_spec", spec_inputs),
        ("explicit_input_revision", explicit_revision or {}),
    ):
        normalized, ledger, errors = canonicalize_finance_inputs(values)
        adoption.extend({**item, "source": source_name} for item in ledger)
        rejected.extend({**item, "source": source_name} for item in errors)
        normalized_sources.append(normalized)
    for key in sorted(set(normalized_sources[0]) & set(normalized_sources[1])):
        if normalized_sources[0][key] != normalized_sources[1][key]:
            rejected.append({
                "input": key,
                "reason": "candidate_input_conflict",
                "source": "candidate_spec_vs_explicit_input_revision",
                "path": f"/input_revision/{key}",
                "conflicts_with": f"/spec/finance_inputs/{key}",
            })
    for normalized in normalized_sources:
        merged.update(normalized)
    normalized, ledger, errors = canonicalize_finance_inputs(merged)
    adoption.extend({**item, "source": "effective"} for item in ledger)
    rejected.extend({**item, "source": "effective"} for item in errors)
    return normalized, adoption, rejected


def _revenue_input_complete(
    spec: dict[str, Any] | None,
    input_revision: dict[str, Any] | None,
) -> bool:
    revision = input_revision if isinstance(input_revision, dict) else {}
    annual = revision.get("annual_revenue_wan")
    if isinstance(annual, (int, float)) and annual > 0:
        return True
    candidate = spec if isinstance(spec, dict) else {}
    revenue = candidate.get("revenue")
    if not isinstance(revenue, dict) and isinstance(candidate.get("finance_inputs"), dict):
        revenue = candidate["finance_inputs"].get("revenue")
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
        return float(revenue.get("saleable_area") or 0) > 0 and float(
            revenue.get("price_per_sqm") or 0
        ) > 0
    if model == "tourism":
        visitors = float(revenue.get("annual_visitors") or 0)
        spend = max(
            float(revenue.get("spend_per_visitor") or 0),
            float(revenue.get("ticket_price_yuan") or 0)
            + float(revenue.get("secondary_spend_yuan") or 0),
        )
        return visitors > 0 and spend > 0
    series = revision.get("revenue_by_year")
    return isinstance(series, list) and any(
        isinstance(value, (int, float)) and value > 0 for value in series
    )


def _active_idempotency_record(
    workspace_id: str,
    key_hash: str,
    operation: str,
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    records = sorted(
        IDEMPOTENCY_STORE.list(workspace_id),
        key=lambda record: str(record.get("created_at") or ""),
        reverse=True,
    )
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


def _expires_at() -> str:
    try:
        ttl = max(
            60,
            min(int(os.getenv("LVKE_MCP_IDEMPOTENCY_TTL_SECONDS", "86400")), 604800),
        )
    except ValueError:
        ttl = 86400
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()


def _latest_formal_boe(workspace_id: str, spec_id: str) -> dict[str, Any] | None:
    matches = [
        record
        for record in BASIS_OF_ESTIMATE_STORE.list(workspace_id)
        if (record.get("payload") or {}).get("spec_id") == spec_id
        and bool((record.get("payload") or {}).get("formal_ready"))
    ]
    return max(
        matches,
        key=lambda record: str(record.get("created_at") or ""),
        default=None,
    )


def _blocking_rules(data: dict[str, Any]) -> list[str]:
    return [
        str(issue["rule"])
        for issue in data.get("blocking_issues") or []
        if isinstance(issue, dict) and issue.get("rule")
    ]


def _blocked_run(code: str, action: str, spec_id: str) -> dict[str, Any]:
    return _ok_env(
        {"available": False, "error": code, "spec_id": spec_id},
        source=f"{SERVER_NAME}.finance_run_model",
        status="blocked",
        blockers=[code],
        next_actions=[action],
        run_id=None,
        spec_id=spec_id or None,
        missing_inputs=[],
    )


def _missing_run(field: str, spec_id: str) -> dict[str, Any]:
    return _ok_env(
        {
            "available": False,
            "error": "missing_inputs",
            "missing_inputs": [field],
            "spec_id": spec_id,
        },
        source=f"{SERVER_NAME}.finance_run_model",
        status="missing_inputs",
        blockers=[f"缺少必要输入：{field}"],
        next_actions=["重新 prepare 并确认包含必要输入的 FinanceSpec"],
        run_id=None,
        spec_id=spec_id or None,
        missing_inputs=[field],
    )


def _finalize(
    payload: dict[str, Any],
    *,
    status: str,
    resource_uris: list | tuple = (),
    warnings: list | tuple = (),
    blockers: list | tuple = (),
    next_actions: list | tuple = (),
    deprecated: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    payload["status"] = status
    payload["resource_uris"] = [str(uri) for uri in resource_uris if uri]
    payload["warnings"] = [str(warning) for warning in warnings if warning]
    payload["blockers"] = [str(blocker) for blocker in blockers if blocker]
    payload["next_actions"] = [str(action) for action in next_actions if action]
    if deprecated:
        payload["deprecated"] = True
    payload.update(extra)
    return payload


def _ok_env(data: Any, *, source: str, status: str, **extra: Any) -> dict[str, Any]:
    payload = _finalize(ok(data, source=source), status=status, **extra)
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
        payload.update(
            {
                "success": False,
                "transport_success": True,
                "business_success": False,
                "completed": False,
                "outcome": status,
                "code": f"{SERVER_NAME}.{raw_code}",
                "message": str(raw_message),
            }
        )
    return payload


def _err_env(
    code: str,
    message: str,
    *,
    detail: Any = None,
    status: str = "failed",
    trace_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    del detail
    env = _finalize(
        err(code, message, trace_id=trace_id),
        status=status,
        **extra,
    )
    if not env["blockers"]:
        env["blockers"] = [message]
    return env


def _exception_env(
    log_message: str,
    code: str,
    message: str,
    *,
    status: str = "failed",
    **extra: Any,
) -> dict[str, Any]:
    trace_id = f"mcp_{uuid.uuid4().hex}"
    logger.exception("%s trace_id=%s", log_message, trace_id)
    return _err_env(
        code,
        message,
        status=status,
        trace_id=trace_id,
        **extra,
    )
