"""运行、渲染、读取、整包与甲方导入工具，含 legacy 兼容实现。"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone


from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.adapters.finance_model_repository import BASIS_OF_ESTIMATE_STORE, IDEMPOTENCY_STORE, SPEC_STORE
from lvke_mcp.runtime.responses import ok

from .analysis_tools import (
    _latest_formal_boe,
)

from .envelope import (
    _active_idempotency_record,
    _blocking_rules,
    _err_env,
    _exception_env,
    _idempotency_ttl_seconds,
    _ok_env,
    _revenue_input_complete,
    _run_uri,
    _str_list,
    _ws,
)

from .schemas import (
    SERVER_NAME,
    _DEPRECATED_PACKAGE_HINT,
    _DEPRECATED_RENDER_HINT,
)


def _tool_run_model(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import run_model

    return run_model(args)


def _legacy_tool_run_model(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    mode = str(args.get("mode") or "estimate_preview")
    if mode not in {"estimate_preview", "review_candidate"}:
        mode = "estimate_preview"
    idempotency_key = str(args.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return _err_env(
            f"{SERVER_NAME}.idempotency_key_required",
            "finance_run_model 写操作必须提供 idempotency_key",
            status="blocked",
            run_id=None,
            missing_inputs=[],
        )
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    request_basis = {
        key: value
        for key, value in args.items()
        if key not in {"idempotency_key", "agent_trace_id", "tool_call_id"}
    }
    request_basis["mode"] = mode
    request_fingerprint = sha256_json(request_basis)
    idempotency_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=_idempotency_ttl_seconds())
    ).isoformat()
    reservation_created = False
    prior = _active_idempotency_record(
        wsid,
        key_hash,
        operation="finance_run_model",
    )
    if prior is not None:
        prior_payload = prior.get("payload") or {}
        if prior_payload.get("content_fingerprint") != request_fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已用于不同的财务运行请求",
                status="blocked",
                blockers=["idempotency_conflict"],
                next_actions=["使用新的 idempotency_key 提交变更后的财务请求"],
                run_id=None,
                original_run_id=prior_payload.get("run_id"),
                missing_inputs=[],
                replayed=False,
            )
        if prior_payload.get("in_progress") is True:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                time.sleep(0.05)
                latest = _active_idempotency_record(
                    wsid, key_hash, operation="finance_run_model"
                )
                latest_payload = (latest or {}).get("payload") or {}
                if latest is not None and latest_payload.get("in_progress") is not True:
                    replay = json.loads(json.dumps(latest_payload.get("result") or {}))
                    replay["replayed"] = True
                    replay["reused"] = True
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
        replay = json.loads(json.dumps(prior_payload.get("result") or {}))
        replay["replayed"] = True
        replay["reused"] = True
        return replay
    try:
        from lvke_mcp.domains.finance import run_service

        # 纯确定性运行：优先消费不可变 spec_id。原 spec/force_flat 只作兼容。
        spec = args.get("spec") if isinstance(args.get("spec"), dict) else None
        spec_id = str(args.get("spec_id") or "").strip()
        basis_of_estimate_id = str(args.get("basis_of_estimate_id") or "").strip()
        basis_of_estimate_hash = ""
        force_flat = bool(args.get("force_flat") or False)
        stored_spec = None
        if spec_id and (spec is not None or force_flat):
            return _err_env(
                f"{SERVER_NAME}.invalid_argument",
                "spec_id 与 spec/force_flat 不可同时传入",
                status="blocked",
            )
        if spec_id:
            stored_spec = SPEC_STORE.get(wsid, spec_id)
            if stored_spec is None:
                return _err_env(f"{SERVER_NAME}.spec_not_found", "未找到已固化 FinanceSpec", status="blocked")
            stored_payload = stored_spec.get("payload") if isinstance(stored_spec.get("payload"), dict) else {}
            spec = stored_payload.get("spec") if isinstance(stored_payload.get("spec"), dict) else None
            if spec is None:
                return _err_env(f"{SERVER_NAME}.spec_invalid", "FinanceSpec 快照无效", status="blocked")
            if mode == "review_candidate" and stored_payload.get("confirmation_status") != "confirmed":
                return _ok_env(
                    {"available": False, "error": "spec_confirmation_required", "spec_id": spec_id},
                    source=f"{SERVER_NAME}.finance_run_model",
                    status="blocked",
                    blockers=["spec_confirmation_required"],
                    next_actions=["先调用 finance_confirm_spec 确认候选 Spec"],
                    run_id=None,
                    missing_inputs=[],
                )
            if mode == "review_candidate":
                boe_record = (
                    BASIS_OF_ESTIMATE_STORE.get(
                        wsid, basis_of_estimate_id
                    )
                    if basis_of_estimate_id
                    else _latest_formal_boe(wsid, spec_id)
                )
                boe_payload = (
                    boe_record.get("payload")
                    if isinstance((boe_record or {}).get("payload"), dict)
                    else {}
                )
                if (
                    boe_record is None
                    or boe_payload.get("spec_id") != spec_id
                    or not boe_payload.get("formal_ready")
                ):
                    return _ok_env(
                        {
                            "available": False,
                            "error": "basis_of_estimate_required",
                            "spec_id": spec_id,
                        },
                        source=f"{SERVER_NAME}.finance_run_model",
                        status="blocked",
                        blockers=["basis_of_estimate_required"],
                        next_actions=[
                            "调用 finance_build_basis_of_estimate，完整绑定重大输入来源与选择理由"
                        ],
                        run_id=None,
                        missing_inputs=[],
                    )
                basis_of_estimate_id = boe_record["object_id"]
                basis_of_estimate_hash = boe_record["basis_hash"]
            elif basis_of_estimate_id:
                boe_record = BASIS_OF_ESTIMATE_STORE.get(
                    wsid, basis_of_estimate_id
                )
                boe_payload = (
                    boe_record.get("payload")
                    if isinstance((boe_record or {}).get("payload"), dict)
                    else {}
                )
                if (
                    boe_record is None
                    or boe_payload.get("spec_id") != spec_id
                    or not boe_payload.get("technical_ready")
                ):
                    return _ok_env(
                        {
                            "available": False,
                            "error": "basis_of_estimate_invalid",
                            "spec_id": spec_id,
                        },
                        source=f"{SERVER_NAME}.finance_run_model",
                        status="blocked",
                        blockers=["basis_of_estimate_invalid"],
                        next_actions=["使用同一 spec 的完整 BoE，或省略它运行 estimate preview"],
                        run_id=None,
                        missing_inputs=[],
                    )
                basis_of_estimate_hash = boe_record["basis_hash"]
        if spec is None and not force_flat:
            return _ok_env(
                {
                    "ok": False,
                    "available": False,
                    "error": "spec_required",
                    "message": "finance_run_model 需要已固化 spec；请先调用 finance_prepare_spec",
                },
                source=f"{SERVER_NAME}.finance_run_model",
                status="blocked",
                blockers=["spec_required：缺已固化 FinanceSpec"],
                next_actions=[
                    "先调用 finance_prepare_spec 固化 spec，或显式 force_flat=true"
                ],
                run_id=None,
                missing_inputs=[],
            )
        stored_payload = stored_spec.get("payload") if stored_spec else {}
        input_revision = args.get("input_revision") if isinstance(args.get("input_revision"), dict) else stored_payload.get("input_revision")
        input_revision_id = args.get("input_revision_id", stored_payload.get("input_revision_id"))
        spec_hash = str(stored_payload.get("spec_hash") or args.get("spec_hash") or "")
        if spec_id and not isinstance(input_revision, dict):
            input_revision = {}
        if spec_id and not input_revision.get("total_investment_wan"):
            return _ok_env(
                {
                    "available": False,
                    "error": "missing_inputs",
                    "missing_inputs": ["total_investment_wan"],
                    "spec_id": spec_id,
                },
                source=f"{SERVER_NAME}.finance_run_model",
                status="missing_inputs",
                blockers=["缺少必要输入：total_investment_wan"],
                next_actions=["重新 prepare 并确认包含总投资的 FinanceSpec"],
                run_id=None,
                spec_id=spec_id,
                missing_inputs=["total_investment_wan"],
            )
        if not _revenue_input_complete(spec, input_revision):
            return _ok_env(
                {
                    "available": False,
                    "error": "revenue_inputs_required",
                    "missing_inputs": ["annual_revenue_wan_or_revenue_driver"],
                    "spec_id": spec_id,
                },
                source=f"{SERVER_NAME}.finance_run_model",
                status="missing_inputs",
                blockers=["revenue_inputs_required"],
                next_actions=["补充 annual_revenue_wan 或完整收入模型后重新 prepare/confirm"],
                run_id=None,
                spec_id=spec_id,
                missing_inputs=["annual_revenue_wan_or_revenue_driver"],
            )
        if (
            mode == "review_candidate"
            and isinstance(input_revision, dict)
            and bool(input_revision.get("is_operating"))
        ):
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
            required_turnover = ("receivable", "inventory", "cash", "payable")
            missing_turnover = [
                name for name in required_turnover
                if turnover.get(name) is None and turnover.get(f"{name}_days") is None
            ]
            if has_working_capital and missing_turnover:
                return _ok_env(
                    {
                        "available": False,
                        "error": "working_capital_turnover_required",
                        "missing_inputs": [f"wc_turnover.{name}" for name in missing_turnover],
                        "field_errors": [{
                            "path": f"/input_revision/wc_turnover/{name}",
                            "code": "required_for_review_candidate",
                            "message": f"正式候选缺少 {name} 周转参数",
                        } for name in missing_turnover],
                    },
                    source=f"{SERVER_NAME}.finance_run_model",
                    status="missing_inputs",
                    blockers=["working_capital_turnover_required"],
                    next_actions=["补充 wc_turnover 分项周转天数后重新运行"],
                    run_id=None,
                    missing_inputs=[f"wc_turnover.{name}" for name in missing_turnover],
                )

        IDEMPOTENCY_STORE.put(
            wsid,
            {
                "operation": "finance_run_model",
                "key_hash": key_hash,
                "content_fingerprint": request_fingerprint,
                "expires_at": idempotency_expires_at,
                "run_id": None,
                "in_progress": True,
            },
            producer=f"{SERVER_NAME}.finance_run_model",
            basis={
                "operation": "finance_run_model",
                "key_hash": key_hash,
                "content_fingerprint": request_fingerprint,
            },
        )
        reservation_created = True
        data = run_service.run_workspace_finance_model(
            wsid,
            spec=spec,
            spec_id=spec_id,
            spec_hash=spec_hash,
            basis_of_estimate_id=basis_of_estimate_id,
            basis_of_estimate_hash=basis_of_estimate_hash,
            input_revision=input_revision,
            input_revision_id=(int(input_revision_id) if input_revision_id is not None else None),
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
                if isinstance(args.get("requested_manifest"), dict) else None
            ),
            selected_scenario_id=str(args.get("selected_scenario_id") or "base"),
        )
        run_id = str(data.get("run_id") or "") or None
        if data.get("available") and not run_id:
            data = dict(data)
            data["available"] = False
            data["ok"] = False
            data["calculation_status"] = "failed"
            data["reason"] = "finance_run_persistence_failed"
            data["blocking_issues"] = [
                *list(data.get("blocking_issues") or []),
                {
                    "rule": "finance_run_persistence_failed",
                    "detail": "财务计算结果未形成可读取的不可变 FinanceRun",
                },
            ]
        uri = _run_uri(wsid, run_id)
        if uri:
            data["resource_uri"] = uri  # 兼容旧调用方
        missing = _str_list(data.get("missing_inputs"))
        if data.get("available") and data.get("consistency_ok") is False:
            status = "blocked"
            blockers = _blocking_rules(data) or ["finance_consistency_failed"]
            next_actions = ["修正财务勾稽问题后重新运行；当前 run 不可进入十三表正式候选"]
        elif data.get("available") and run_id:
            status = "ok"
            blockers: list[str] = []
            next_actions = ["用 run_id 调用 lvke-finance-tables.tables_render 渲染 13 表"]
        elif missing:
            # 缺关键输入：如实 missing_inputs，不生成 IRR。
            status = "missing_inputs"
            blockers = [f"缺少必要输入：{item}" for item in missing]
            next_actions = ["补齐缺失输入后重试 finance_run_model"]
        else:
            status = "blocked"
            blockers = _blocking_rules(data) or [
                str(data.get("reason") or "run_not_available")
            ]
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
        )
        result["replayed"] = False
        result["reused"] = False
        result["idempotency_expires_at"] = idempotency_expires_at
        cached_result = json.loads(json.dumps(result, ensure_ascii=False, default=str))
        IDEMPOTENCY_STORE.put(
            wsid,
            {
                "operation": "finance_run_model",
                "key_hash": key_hash,
                "content_fingerprint": request_fingerprint,
                "expires_at": idempotency_expires_at,
                "run_id": run_id,
                "result": cached_result,
            },
            producer=f"{SERVER_NAME}.finance_run_model",
            source_ids=[run_id] if run_id else [],
            basis={
                "operation": "finance_run_model",
                "key_hash": key_hash,
                "content_fingerprint": request_fingerprint,
            },
        )
        return result
    except Exception:  # noqa: BLE001
        failure = _exception_env(
            "finance_run_model failed",
            f"{SERVER_NAME}.run_failed",
            "运行财务模型失败",
        )
        if reservation_created:
            IDEMPOTENCY_STORE.put(
                wsid,
                {
                    "operation": "finance_run_model",
                    "key_hash": key_hash,
                    "content_fingerprint": request_fingerprint,
                    "expires_at": idempotency_expires_at,
                    "run_id": None,
                    "result": failure,
                },
                producer=f"{SERVER_NAME}.finance_run_model",
                basis={
                    "operation": "finance_run_model",
                    "key_hash": key_hash,
                    "content_fingerprint": request_fingerprint,
                },
            )
        return failure


def _tool_render_tables(args: dict) -> dict:
    run_id = args.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return _err_env(
            f"{SERVER_NAME}.invalid_argument", "run_id 必填",
            deprecated=True, warnings=[_DEPRECATED_RENDER_HINT],
        )
    wsid = _ws(args)
    if not wsid:
        # 允许只传 run_id 时从审计库反查困难；仍要求 workspace_id
        return _err_env(
            f"{SERVER_NAME}.invalid_argument", "workspace_id 必填",
            deprecated=True, warnings=[_DEPRECATED_RENDER_HINT],
        )
    try:
        from lvke_mcp.domains.finance import run_service

        data = run_service.render_workspace_finance_tables(
            wsid,
            run_id=run_id.strip(),
            format=str(args.get("format") or "structured"),
            include_control_tables=bool(args.get("include_control_tables", True)),
        )
        if not data.get("ok"):
            return _err_env(
                f"{SERVER_NAME}.{data.get('error') or 'render_failed'}",
                data.get("message") or "渲染 13 表失败",
                detail=data,
                status="blocked",
                deprecated=True,
                warnings=[_DEPRECATED_RENDER_HINT],
                next_actions=["迁移到 lvke-finance-tables.tables_render"],
            )
        rid = str(data.get("run_id") or "") or None
        missing_keys = _str_list(data.get("missing_delivery_keys"))
        warnings = [_DEPRECATED_RENDER_HINT]
        if missing_keys:
            warnings.append(f"缺失交付表：{'、'.join(missing_keys)}")
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_render_tables",
            status="partial" if missing_keys else "ok",
            resource_uris=[_run_uri(wsid, rid)] if rid else [],
            warnings=warnings,
            next_actions=["迁移到 lvke-finance-tables.tables_render"],
            deprecated=True,
            run_id=rid,
            missing_delivery_keys=missing_keys,
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_render_tables failed",
            f"{SERVER_NAME}.render_failed",
            "渲染 13 表失败",
            deprecated=True,
            warnings=[_DEPRECATED_RENDER_HINT],
        )


def _tool_get_run(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import get_run

    return get_run(args)


def _tool_generate_package(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(
            f"{SERVER_NAME}.invalid_argument", "workspace_id 必填",
            deprecated=True, warnings=[_DEPRECATED_PACKAGE_HINT],
        )
    mode = str(args.get("mode") or "estimate_preview")
    if mode not in {"estimate_preview", "review_candidate"}:
        mode = "estimate_preview"
    try:
        from lvke_mcp.domains.finance import run_service

        data = run_service.generate_workspace_finance_package(
            wsid,
            mode=mode,
            force_refresh_spec=bool(args.get("force_refresh_spec") or False),
            force_recompute=bool(args.get("force_recompute") or False),
            force_flat=bool(args.get("force_flat") or False),
            confirmed_spec=args.get("confirmed_spec") if isinstance(args.get("confirmed_spec"), dict) else None,
            agent_trace_id=str(args.get("agent_trace_id") or ""),
            tool_call_id=str(args.get("tool_call_id") or ""),
            valuation_date=str(args.get("valuation_date") or ""),
            requested_manifest=(
                args.get("requested_manifest")
                if isinstance(args.get("requested_manifest"), dict) else None
            ),
            selected_scenario_id=str(args.get("selected_scenario_id") or "base"),
        )
        run_id = str(data.get("run_id") or "") or None
        uri = _run_uri(wsid, run_id)
        missing = _str_list(data.get("missing_inputs"))
        stage = str(data.get("stage") or "")
        if data.get("ok"):
            status = "ok"
            blockers: list[str] = []
        elif missing:
            status = "missing_inputs"
            blockers = [f"缺少必要输入：{item}" for item in missing]
        else:
            status = "blocked"
            blockers = _blocking_rules(data) or [f"stage={stage or 'unknown'} 未完成"]
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_generate_package",
            status=status,
            resource_uris=[uri] if uri else [],
            warnings=[_DEPRECATED_PACKAGE_HINT, *_str_list(data.get("prepare_warnings"))],
            blockers=blockers,
            next_actions=[
                "迁移：finance_prepare_spec → finance_run_model → lvke-finance-tables.tables_render",
            ],
            deprecated=True,
            run_id=run_id,
            stage=stage or None,
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_generate_package failed",
            f"{SERVER_NAME}.package_failed",
            "生成财务包失败",
            deprecated=True,
            warnings=[_DEPRECATED_PACKAGE_HINT],
        )


def _tool_import_vendor_review(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    xlsx_path = args.get("xlsx_path") or args.get("path")
    if not isinstance(xlsx_path, str) or not xlsx_path.strip():
        return _err_env(f"{SERVER_NAME}.invalid_argument", "xlsx_path 必填")
    cohort = args.get("cohort_xlsx_paths")
    if cohort is not None and not (
        isinstance(cohort, list) and all(isinstance(item, str) for item in cohort)
    ):
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "cohort_xlsx_paths 必须是字符串数组",
        )
    try:
        from lvke_mcp.domains.finance.vendor_review import import_vendor_workbook_review

        data = import_vendor_workbook_review(
            wsid,
            xlsx_path.strip(),
            valuation_date=str(args.get("valuation_date") or ""),
            force_recompute=bool(args.get("force_recompute") or False),
            cohort_xlsx_paths=cohort or None,
        )
        run_id = str(data.get("run_id") or "") or None
        uri = _run_uri(wsid, run_id)
        missing = _str_list(data.get("missing_inputs"))
        blocking = [
            str(issue.get("rule") or issue.get("code") or "blocking_issue")
            for issue in (data.get("blocking_issues") or [])
            if isinstance(issue, dict)
        ]
        if missing:
            status = "missing_inputs"
        elif not data.get("available"):
            status = "blocked"
        elif blocking:
            # 复核完成但存在阻断预警：不冒充复核通过。
            status = "blocked"
        else:
            status = "ok"
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_import_vendor_review",
            status=status,
            resource_uris=[uri] if uri else [],
            warnings=_str_list(((data.get("reference") or {}).get("warnings"))),
            blockers=blocking or (
                [f"缺少必要输入：{item}" for item in missing] if missing else []
            ),
            next_actions=(
                ["修复阻断预警并重新运行确定性校验"] if blocking else []
            ),
            reference_id=data.get("reference_id"),
            review_passed=bool(data.get("review_passed")),
            run_id=run_id,
            missing_inputs=missing,
        )
    except FileNotFoundError:
        return _exception_env(
            "finance_import_vendor_review workbook missing",
            f"{SERVER_NAME}.vendor_workbook_not_found",
            "甲方工作簿不存在",
        )
    except ImportError:  # pragma: no cover - environment dependent
        return _exception_env(
            "finance_import_vendor_review parser unavailable",
            f"{SERVER_NAME}.vendor_workbook_parser_unavailable",
            "甲方工作簿解析依赖不可用",
        )
    except (ValueError, OSError, zipfile.BadZipFile):
        return _exception_env(
            "finance_import_vendor_review parse failed",
            f"{SERVER_NAME}.vendor_workbook_parse_failed",
            "甲方工作簿格式无效或解析失败",
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_import_vendor_review failed",
            f"{SERVER_NAME}.vendor_review_failed",
            "导入并复核甲方计算表失败",
        )
