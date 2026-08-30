"""spec 与 fact pack 工具入口，含 legacy 兼容实现。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone


from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.adapters.finance_model_repository import IDEMPOTENCY_STORE, SPEC_STORE
from lvke_mcp.runtime.responses import ok
from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE

from .envelope import (
    _active_idempotency_record,
    _canonical_candidate_inputs,
    _err_env,
    _exception_env,
    _idempotency_ttl_seconds,
    _ok_env,
    _revenue_input_complete,
    _str_list,
    _ws,
)

from .schemas import (
    SERVER_NAME,
)


def _tool_prepare_spec(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import prepare_spec

    return prepare_spec(args)


def _tool_prepare_fact_pack(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import prepare_fact_pack

    return prepare_fact_pack(args)


def _tool_confirm_fact_pack(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import confirm_fact_pack

    return confirm_fact_pack(args)


def _tool_get_fact_pack(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import get_fact_pack

    return get_fact_pack(args)


def _legacy_tool_prepare_spec(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    evidence_ids = _str_list(args.get("evidence_pack_ids"))
    evidence_records = []
    for evidence_id in evidence_ids:
        record = EVIDENCE_STORE.get(wsid, evidence_id)
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
            wsid,
            strategy=str(args.get("strategy") or "propose_from_project"),
            force_refresh=bool(args.get("force_refresh") or False),
            # The MCP boundary is invoked by an Agent already capable of
            # reasoning over supplied evidence.  Do not make a second LLM
            # gateway a hidden dependency of FinanceSpec preparation.
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
            field_errors = [
                {
                    "path": str(item.get("path") or f"/input_revision/{item.get('input') or 'unknown'}"),
                    "code": str(item.get("reason") or "candidate_input_invalid"),
                    "input": item.get("input"),
                    **(
                        {"conflicts_with": item.get("conflicts_with")}
                        if item.get("conflicts_with")
                        else {}
                    ),
                }
                for item in rejected
            ]
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
                field_errors=field_errors,
                next_actions=["修正未知、冲突或非法的 input_revision 字段后重试"],
            )
        data["input_revision"] = normalized_inputs
        data["input_adoption_ledger"] = adoption
        compute_hash = getattr(run_service, "compute_input_hash", None)
        data["input_hash"] = (
            compute_hash(
                normalized_inputs,
                invest_type=str(data.get("invest_type") or normalized_inputs.get("invest_type") or ""),
                build_period_months=data.get("build_period_months") or normalized_inputs.get("build_period_months"),
                industry=str(data.get("industry") or normalized_inputs.get("industry") or ""),
            )
            if callable(compute_hash)
            else sha256_json(normalized_inputs)
        )
        data["missing_inputs"] = (
            [] if normalized_inputs.get("total_investment_wan") else ["total_investment_wan"]
        )
        missing = _str_list(data.get("missing_inputs"))
        spec = data.get("spec") if isinstance(data.get("spec"), dict) else None
        if not _revenue_input_complete(spec if isinstance(spec, dict) else supplied_spec, normalized_inputs):
            missing.append("annual_revenue_wan_or_revenue_driver")
        assumptions = _str_list(data.get("assumptions_to_confirm"))
        if spec is None and "finance_spec" not in missing:
            missing.append("finance_spec")
        spec_record = None
        evidence_binding_hash = sha256_json({
            "evidence_pack_ids": evidence_ids,
            "evidence_basis_hashes": [record.get("basis_hash") for record in evidence_records],
        })
        if spec is not None:
            spec_record = SPEC_STORE.put(
                wsid,
                {
                    "spec": spec,
                    "spec_hash": data.get("spec_hash"),
                    "input_revision": data.get("input_revision") or {},
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
                ([
                    "提供候选 spec 后重新调用 finance_prepare_spec；"
                    "缺少可审计收入驱动时不得创建 FinanceRun"
                ] if "finance_spec" in missing else ["补齐缺失输入后重新调用 finance_prepare_spec"])
                if missing
                else ["调用 finance_confirm_spec 确认候选 Spec，再调用 finance_run_model"]
            ),
            resource_uris=[spec_record["resource_uri"]] if spec_record else [],
            spec_id=spec_record["object_id"] if spec_record else None,
            spec_hash=data.get("spec_hash"),
            evidence_binding_hash=evidence_binding_hash,
            missing_inputs=missing,
            assumptions_to_confirm=assumptions,
            input_hash=data.get("input_hash"),
            input_revision_id=data.get("input_revision_id"),
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_prepare_spec failed",
            f"{SERVER_NAME}.prepare_failed",
            "准备 FinanceSpec 失败",
        )


def _tool_confirm_spec(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import confirm_spec

    return confirm_spec(args)


def _legacy_tool_confirm_spec(args: dict) -> dict:
    wsid = _ws(args)
    spec_id = str(args.get("spec_id") or "").strip()
    if not wsid or not spec_id:
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "workspace_id 与 spec_id 必填",
        )
    source = SPEC_STORE.get(wsid, spec_id, )
    if source is None:
        return _err_env(f"{SERVER_NAME}.spec_not_found", "未找到候选 FinanceSpec", status="blocked")
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else None
    if spec is None:
        return _err_env(f"{SERVER_NAME}.spec_invalid", "候选 FinanceSpec 快照无效", status="blocked")
    input_revision = payload.get("input_revision") if isinstance(payload.get("input_revision"), dict) else {}
    missing_inputs = [] if input_revision.get("total_investment_wan") else ["total_investment_wan"]
    if not _revenue_input_complete(spec, input_revision):
        missing_inputs.append("annual_revenue_wan_or_revenue_driver")
    try:
        from lvke_mcp.domains.finance.spec import mark_spec_confirmed, validate_for_formal

        formal_candidate = mark_spec_confirmed(spec)
        formal_ok, formal_errors = validate_for_formal(formal_candidate)
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_confirm_spec validation failed",
            f"{SERVER_NAME}.confirm_failed",
            "确认 FinanceSpec 失败",
        )
    if missing_inputs or not formal_ok:
        blockers = [*(f"missing_input:{item}" for item in missing_inputs), *formal_errors]
        return _ok_env(
            {
                "spec_id": spec_id,
                "valid": False,
                "missing_inputs": missing_inputs,
                "validation_errors": formal_errors,
            },
            source=f"{SERVER_NAME}.finance_confirm_spec",
            status="blocked",
            blockers=blockers,
            next_actions=["修正候选 Spec 或补齐输入后重新 prepare，再确认新候选"],
        )
    note = str(args.get("note") or "")
    idempotency_key = str(args.get("idempotency_key") or "").strip()
    content_fingerprint = sha256_json({
        "spec_id": spec_id,
        "spec_content_hash": source.get("content_hash"),
        "note": note,
    })
    key_hash = "sha256:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    prior = _active_idempotency_record(wsid, key_hash)
    if prior is not None:
        saved = prior.get("payload") or {}
        if saved.get("content_fingerprint") != content_fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已绑定不同 FinanceSpec 确认请求",
                status="blocked",
                content_fingerprint=content_fingerprint,
                replayed=False,
                reused=False,
                idempotency_expires_at=saved.get("expires_at"),
            )
        replay = dict(saved.get("result") or {})
        replay.update({
            "content_fingerprint": content_fingerprint,
            "replayed": True,
            "reused": True,
            "idempotency_expires_at": saved.get("expires_at"),
        })
        return replay
    try:
        from lvke_mcp.domains.finance.run_service import compute_spec_hash

        confirmed = formal_candidate
        record = SPEC_STORE.put(
            wsid,
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
            basis={
                "parent_spec_id": spec_id,
                "spec_hash": compute_spec_hash(confirmed),
                "note": note,
                "idempotency_key_hash": key_hash,
            },
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_confirm_spec failed",
            f"{SERVER_NAME}.confirm_failed",
            "确认 FinanceSpec 失败",
        )
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=_idempotency_ttl_seconds())
    ).isoformat()
    result = _ok_env(
        {"spec_id": record["object_id"], "parent_spec_id": spec_id, "spec_hash": record["payload"]["spec_hash"]},
        source=f"{SERVER_NAME}.finance_confirm_spec",
        status="ok",
        resource_uris=[record["resource_uri"]],
        next_actions=["调用 finance_run_model，传入已确认 spec_id"],
        spec_id=record["object_id"],
        spec_hash=record["payload"]["spec_hash"],
        content_fingerprint=content_fingerprint,
        replayed=False,
        reused=False,
        idempotency_expires_at=expires_at,
    )
    IDEMPOTENCY_STORE.put(
        wsid,
        {
            "operation": "finance_confirm_spec",
            "key_hash": key_hash,
            "content_fingerprint": content_fingerprint,
            "expires_at": expires_at,
            "result": result,
        },
        producer=f"{SERVER_NAME}.finance_confirm_spec",
        source_ids=[record["object_id"]],
        basis={
            "operation": "finance_confirm_spec",
            "key_hash": key_hash,
            "content_fingerprint": content_fingerprint,
        },
    )
    return result


def _tool_validate_spec(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import validate_spec

    return validate_spec(args)

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "EVIDENCE_STORE",
    "IDEMPOTENCY_STORE",
    "SERVER_NAME",
    "SPEC_STORE",
    "_active_idempotency_record",
    "_canonical_candidate_inputs",
    "_err_env",
    "_exception_env",
    "_idempotency_ttl_seconds",
    "_legacy_tool_confirm_spec",
    "_legacy_tool_prepare_spec",
    "_ok_env",
    "_revenue_input_complete",
    "_str_list",
    "_tool_confirm_fact_pack",
    "_tool_confirm_spec",
    "_tool_get_fact_pack",
    "_tool_prepare_fact_pack",
    "_tool_prepare_spec",
    "_tool_validate_spec",
    "_ws",
    "datetime",
    "hashlib",
    "ok",
    "sha256_json",
    "timedelta",
    "timezone",
]
