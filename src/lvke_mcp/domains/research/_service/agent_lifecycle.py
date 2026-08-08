"""Agent DR 会话生命周期：取消、启动、状态查询、提交与质量确认。"""

from __future__ import annotations

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE

from lvke_mcp.adapters.research_repository import AGENT_SESSION_STORE, AGENT_TRANSITION_STORE, IDEMPOTENCY_STORE, PACKAGE_STORE, QUALITY_REVIEW_STORE
from lvke_mcp.runtime.storage import sha256_json

from .base import _agent_transition_guard, _active_idempotency_record, _append_event, _failure, _idempotency_ttl_seconds

from .planning import (
    _create_plan_revision,
)


_PACKAGE_STATUS_RANK = {"completed": 2, "partial": 1}


def select_task_package(workspace_id: str, task_id: str) -> dict[str, Any] | None:
    """Select the head research package for a task.

    ``PACKAGE_STORE.list`` 按文件名排序，而文件名是内容哈希，因此
    ``packages[-1]`` 取到的是哈希序末位而非最新修订——同一任务同时存在
    dr_submit 的 partial 与 dr_confirm_quality 的 completed 时，会随机
    返回其中一个。这里显式按 (status 优先级, created_at) 选头，使确认后的
    completed 修订稳定胜出。
    """
    packages = [
        record for record in PACKAGE_STORE.list(workspace_id)
        if task_id in (record.get("source_ids") or [])
    ]
    if not packages:
        return None
    return max(
        packages,
        key=lambda record: (
            _PACKAGE_STATUS_RANK.get(str(record.get("status") or "").strip(), 0),
            str(record.get("created_at") or ""),
        ),
    )


def _cancel_transition(
    workspace_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    records = [
        record for record in AGENT_TRANSITION_STORE.list(workspace_id)
        if str((record.get("payload") or {}).get("task_id") or "") == task_id
        and str((record.get("payload") or {}).get("status") or "") == "cancelled"
    ]
    return records[-1] if records else None

def cancel_agent(
    workspace_id: str,
    task_id: str,
    *,
    reason: str = "",
) -> dict[str, Any]:
    if AGENT_SESSION_STORE.get(workspace_id, task_id) is None:
        return _failure("task_not_found", "未找到 Agent DR 会话")
    with _agent_transition_guard(workspace_id, task_id):
        existing = _cancel_transition(workspace_id, task_id)
        if existing is not None:
            payload = existing.get("payload") or {}
            return {
                "success": True,
                "status": "cancelled",
                "task_id": task_id,
                "cancel_requested": True,
                "cancelled_at": payload.get("cancelled_at"),
                "replayed": True,
                "resource_uris": [str(existing.get("resource_uri") or "")],
                "warnings": [], "blockers": [], "next_actions": [],
            }
        if any(
            task_id in (record.get("source_ids") or [])
            for record in PACKAGE_STORE.list(workspace_id)
        ):
            return _failure("task_already_terminal", "研究会话已提交，不能再取消")
        payload = {
            "task_id": task_id,
            "status": "cancelled",
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
            "reason": str(reason or "用户请求停止研究")[:2000],
        }
        record = AGENT_TRANSITION_STORE.put(
            workspace_id,
            payload,
            producer="lvke-deep-research.dr_cancel",
            status="cancelled",
            source_ids=[task_id],
            basis={"task_id": task_id, "status": "cancelled"},
        )
        _append_event(
            workspace_id,
            task_id,
            "task_cancelled",
            {"transition_id": record["object_id"], "reason": payload["reason"]},
        )
        return {
            "success": True,
            "status": "cancelled",
            "task_id": task_id,
            "cancel_requested": True,
            "cancelled_at": payload["cancelled_at"],
            "replayed": False,
            "resource_uris": [record["resource_uri"]],
            "warnings": [], "blockers": [], "next_actions": [],
        }

def start_agent(args: dict[str, Any]) -> dict[str, Any]:
    """Create a research hand-off for the calling Agent, without a nested LLM."""

    workspace_id = str(args["workspace_id"])
    topic = str(args.get("topic") or "").strip()
    payload = {
        "topic": topic,
        "industry": str(args.get("industry") or ""),
        "region": str(args.get("region") or ""),
        "research_brief": args.get("research_brief") if isinstance(args.get("research_brief"), dict) else {},
        "plan_items": list(args.get("plan_items") or []),
        "subqueries": [str(item) for item in (args.get("subqueries") or [])],
        "budget": args.get("budget") if isinstance(args.get("budget"), dict) else {},
        "source_policy": args.get("source_policy") if isinstance(args.get("source_policy"), dict) else {},
        "analysis_inputs": list(args.get("analysis_inputs") or []),
        "lineage": str(args.get("continued_from_task_id") or ""),
        "chapters": [str(item) for item in (args.get("chapters") or [])],
        "profile": str(args.get("profile") or "quick"),
        "verify_urls": bool(args.get("verify_urls", False)),
        "output_contract": args.get("output_contract") if isinstance(args.get("output_contract"), dict) else {},
        "resumed_from_checkpoint_id": str(args.get("resumed_from_checkpoint_id") or ""),
    }
    content_fingerprint = sha256_json(payload)
    idempotency_key = str(args.get("idempotency_key") or "").strip()
    key_hash = (
        "sha256:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        if idempotency_key else ""
    )
    if key_hash:
        prior = _active_idempotency_record(workspace_id, key_hash)
        if prior is not None:
            saved = prior.get("payload") or {}
            if saved.get("content_fingerprint") != content_fingerprint:
                conflict = _failure(
                    "idempotency_conflict",
                    "同一 idempotency_key 已绑定不同研究请求",
                )
                conflict.update({
                    "content_fingerprint": content_fingerprint,
                    "replayed": False,
                    "reused": False,
                    "idempotency_expires_at": saved.get("expires_at"),
                })
                return conflict
            replay = dict(saved.get("result") or {})
            replay.update({
                "content_fingerprint": content_fingerprint,
                "replayed": True,
                "reused": True,
                "idempotency_expires_at": saved.get("expires_at"),
            })
            return replay
    record = AGENT_SESSION_STORE.put(
        workspace_id,
        payload,
        producer="lvke-deep-research.dr_start",
        status="agent_collecting",
        source_ids=[str(args.get("continued_from_task_id") or "")],
        basis=payload,
    )
    initial_plan = _create_plan_revision(
        workspace_id,
        record["object_id"],
        {
            "research_brief": payload["research_brief"],
            "plan_items": payload["plan_items"],
            "budget": payload["budget"],
            "sources": list(args.get("source_descriptors") or []),
            "excluded_sources": [],
            "quality_state": args.get("quality_state") if isinstance(args.get("quality_state"), dict) else {},
            "pending_work": list(args.get("pending_work") or payload["subqueries"]),
        },
        parent_plan_revision_id="",
        source_ids=[str(args.get("resumed_from_checkpoint_id") or "")],
        producer="lvke-deep-research.dr_start",
    )
    _append_event(
        workspace_id,
        record["object_id"],
        "task_started",
        {
            "session_id": record["object_id"],
            "plan_revision_id": initial_plan["object_id"],
            "resumed_from_checkpoint_id": payload["resumed_from_checkpoint_id"],
        },
    )
    result = {
        "success": True,
        "task_id": record["object_id"],
        "status": "agent_collecting",
        "profile": "agent_orchestrated",
        "hint": "MCP 不运行内置 LLM。由当前 Agent 通过采集/分析工具完成研究，再调用 dr_submit 记录有 locator 的发现。",
        "resource_uri": record["resource_uri"],
        "plan_revision_id": initial_plan["object_id"],
        "plan_basis_hash": initial_plan["basis_hash"],
        "content_fingerprint": content_fingerprint,
        "replayed": False,
        "reused": False,
    }
    if key_hash:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=_idempotency_ttl_seconds())
        ).isoformat()
        result["idempotency_expires_at"] = expires_at
        IDEMPOTENCY_STORE.put(
            workspace_id,
            {
                "operation": "dr_start",
                "key_hash": key_hash,
                "content_fingerprint": content_fingerprint,
                "expires_at": expires_at,
                "result": result,
            },
            producer="lvke-deep-research.dr_start",
            source_ids=[record["object_id"]],
            basis={
                "operation": "dr_start",
                "key_hash": key_hash,
                "content_fingerprint": content_fingerprint,
            },
        )
    return result

def agent_status(
    workspace_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    if not str(task_id or "").strip():
        # 空 task_id 语义是"取最新引擎任务"，由调用方回退到 drt.load_task；
        # 空串传进 store 会被 require_safe_id 以 ValueError 拒绝，炸掉整个 dr_status。
        return None
    session = AGENT_SESSION_STORE.get(workspace_id, task_id)
    if session is None:
        return None
    cancelled = _cancel_transition(workspace_id, task_id)
    if cancelled is not None:
        payload = cancelled.get("payload") or {}
        return {
            "task_id": task_id,
            "status": "cancelled",
            "round_no": None,
            "budget": (session.get("payload") or {}).get("budget") or {},
            "quality": None,
            "updated_at": payload.get("cancelled_at") or cancelled.get("created_at"),
            "is_terminal": True,
            "note": "研究会话已取消，不能提交、续研或生成研究包。",
        }
    package = select_task_package(workspace_id, task_id)
    status = str(package.get("status") or "partial") if package else "agent_collecting"
    return {
        "task_id": task_id,
        "status": status,
        "round_no": None,
        "budget": (session.get("payload") or {}).get("budget") or {},
        "quality": ((package or {}).get("payload") or {}).get("agent_artifacts", {}).get("quality"),
        "updated_at": (package or session).get("created_at"),
        "is_terminal": package is not None,
        "note": (
            "Agent 正在整理带 locator 的来源与结论。提交后固定为 partial，"
            "除非另有独立质量审计，不能描述为完成。"
        ),
    }

def _submit_agent_unlocked(args: dict[str, Any]) -> dict[str, Any]:
    """Freeze Agent-authored findings with explicit source locators.

    This deliberately creates a ``partial`` package: the MCP can preserve and
    validate provenance, but it cannot pretend to independently grade prose
    produced in the caller's conversation.
    """

    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    session = AGENT_SESSION_STORE.get(workspace_id, task_id)
    if session is None:
        return _failure("task_not_found", "未找到 Agent DR 会话")
    report_md = str(args.get("report_md") or "").strip()
    citations = list(args.get("citations") or [])
    evidence_pack_ids = [str(item) for item in (args.get("evidence_pack_ids") or []) if str(item)]
    source_snapshot_ids = [str(item) for item in (args.get("source_snapshot_ids") or []) if str(item)]
    quality_summary = args.get("quality_summary") if isinstance(args.get("quality_summary"), dict) else {}
    market_field_bindings = [
        dict(item) for item in (args.get("market_field_bindings") or [])
        if isinstance(item, dict)
    ]
    blockers: list[str] = []
    if not report_md:
        blockers.append("report_required")
    if not citations:
        blockers.append("citations_required")
    if not evidence_pack_ids and not source_snapshot_ids:
        blockers.append("source_basis_required")
    evidence_records = [EVIDENCE_STORE.get(workspace_id, item) for item in evidence_pack_ids]
    for evidence_id, evidence_record in zip(evidence_pack_ids, evidence_records):
        if evidence_record is None:
            blockers.append(f"evidence_pack_not_found:{evidence_id}")
    if blockers:
        return {
            "success": False, "status": "blocked", "code": "agent_submission_incomplete",
            "message": "研究提交缺少正文、引用或来源依据", "resource_uris": [],
            "warnings": [], "blockers": blockers,
            "next_actions": ["补齐 report_md、citations 和 evidence_pack_id/source_snapshot_id 后重试"],
        }
    artifacts = {
        "report": report_md,
        "sources": citations,
        "evidence": {"evidence_pack_ids": evidence_pack_ids, "source_snapshot_ids": source_snapshot_ids},
        "citation_audit": {"status": "agent_supplied", "citation_count": len(citations), "passed": False},
        "quality": {"status": "not_independently_audited", "passed": False},
        "quality_summary": {
            "query_rounds": int(quality_summary.get("query_rounds") or 0),
            "source_count": len(citations),
            "usable_source_count": int(quality_summary.get("usable_source_count") or 0),
            "citation_coverage": quality_summary.get("citation_coverage"),
            "missing_fields": [str(item) for item in quality_summary.get("missing_fields") or []],
            "conflicts": [dict(item) for item in quality_summary.get("conflicts") or [] if isinstance(item, dict)],
            "submitted_by_agent": True,
        },
        "market_field_bindings": market_field_bindings,
        "checkpoint": {"task_id": task_id, "stage": "agent_submitted"},
    }
    if not quality_summary:
        artifacts.pop("quality_summary", None)
    if not market_field_bindings:
        artifacts.pop("market_field_bindings", None)
    evidence_payloads = [(item.get("payload") or {}) for item in evidence_records if isinstance(item, dict)]
    evidence_policies = {str(item.get("evidence_policy") or "") for item in evidence_payloads if item.get("evidence_policy")}
    # P0-009 修复：同时从 evidence_pack_ids（已聚合）和 citations（EvidencePack
    # 写入时带 evidence_policy）读取证据策略；任一为 source_reconstructed 则整包降级
    for citation in citations:
        if isinstance(citation, dict) and str(citation.get("evidence_policy") or "") == "source_reconstructed":
            evidence_policies.add("source_reconstructed")
    evidence_policy = "source_reconstructed" if "source_reconstructed" in evidence_policies else (sorted(evidence_policies)[0] if evidence_policies else "formal_evidence")
    reconstruction_records = [row for item in evidence_payloads for row in (item.get("reconstruction_records") or []) if isinstance(row, dict)]
    payload = {
        "task_id": task_id,
        "status": "partial",
        "profile": "agent_orchestrated",
        "artifact_names": list(artifacts),
        "agent_artifacts": artifacts,
        "limitations": ["正文由调用 Agent 撰写，尚无独立 DR 质量审计；下游必须披露 partial 限制"],
        "evidence_policy": evidence_policy,
        "project_fact_certified": False if evidence_policy == "source_reconstructed" else True,
        "reconstruction_records": reconstruction_records,
        "reconstructed_source_ids": [str(row.get("reconstruction_id") or "") for row in reconstruction_records if row.get("reconstruction_id")],
        "unresolved_inputs": list(args.get("unresolved_inputs") or []),
        "release_limitations": list(args.get("release_limitations") or []),
    }
    record = PACKAGE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-deep-research.dr_submit",
        status="partial",
        source_ids=[task_id, *evidence_pack_ids, *source_snapshot_ids],
        basis={
            "task_id": task_id,
            "citations": citations,
            "evidence_pack_ids": evidence_pack_ids,
            "source_snapshot_ids": source_snapshot_ids,
            "quality_summary": quality_summary,
            "market_field_bindings": market_field_bindings,
        },
    )
    _append_event(
        workspace_id,
        task_id,
        "research_submitted",
        {"research_package_id": record["object_id"], "status": "partial"},
    )
    base = record["resource_uri"]
    resources = {name: f"{base}/{name}" for name in artifacts}
    return {
        "success": True, "status": "partial", "research_package_id": record["object_id"],
        "task_id": task_id, "basis_hash": record["basis_hash"], "resources": resources,
        "resource_uris": [base, *resources.values()], "warnings": payload["limitations"], "blockers": [],
        "next_actions": ["将 partial 研究限制带入 report_prepare；财务数字仍只来自 run_id"],
    }

def submit_agent(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    with _agent_transition_guard(workspace_id, task_id):
        if _cancel_transition(workspace_id, task_id) is not None:
            return _failure("task_cancelled", "研究会话已取消，不能提交")
        if any(
            task_id in (record.get("source_ids") or [])
            for record in PACKAGE_STORE.list(workspace_id)
        ):
            return _failure("task_already_terminal", "研究会话已经提交")
        return _submit_agent_unlocked(args)

def confirm_quality(args: dict[str, Any]) -> dict[str, Any]:
    """Record an independent quality decision for an Agent-authored package.

    ``dr_submit`` remains intentionally partial.  This operation creates a
    separate immutable review and a new package projection only after the
    caller supplies quality metrics; source-reconstructed material may pass
    with explicitly accepted limitations without becoming certified project
    facts.
    """
    workspace_id = str(args.get("workspace_id") or "").strip()
    package_id = str(args.get("research_package_id") or "").strip()
    if not workspace_id or not package_id:
        return _failure("research_package_required", "workspace_id 与 research_package_id 必填")
    source = PACKAGE_STORE.get(workspace_id, package_id)
    if source is None:
        return _failure("research_package_not_found", "研究包不存在")
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    if str(payload.get("status") or source.get("status") or "") not in {"partial", "completed", "done"}:
        return _failure("research_package_not_usable", "研究包当前状态不能进行质量确认")
    artifacts = payload.get("agent_artifacts") if isinstance(payload.get("agent_artifacts"), dict) else {}
    summary = artifacts.get("quality_summary") if isinstance(artifacts.get("quality_summary"), dict) else {}
    citations = artifacts.get("sources") if isinstance(artifacts.get("sources"), list) else []
    metrics = {
        "query_rounds": int(args.get("query_rounds", summary.get("query_rounds") or 0)),
        "source_count": len(citations),
        "usable_source_count": int(args.get("usable_source_count", summary.get("usable_source_count") or 0)),
        "citation_coverage": args.get("citation_coverage", summary.get("citation_coverage")),
        "missing_fields": [str(item) for item in (args.get("missing_fields", summary.get("missing_fields") or []) or [])],
        "conflicts": [dict(item) for item in (args.get("conflicts", summary.get("conflicts") or []) or []) if isinstance(item, dict)],
    }
    evidence_policy = str(payload.get("evidence_policy") or payload.get("evidence_track") or "")
    accepted_limitations = bool(args.get("accept_material_limitations", False))
    limitations = list(payload.get("limitations") or [])
    missing = list(metrics["missing_fields"])
    conflicts = list(metrics["conflicts"])
    coverage = metrics["citation_coverage"]
    quality_passed = bool(
        metrics["source_count"] > 0
        and metrics["usable_source_count"] >= 1
        and not conflicts
        and not missing
        and (coverage is None or float(coverage) >= 0.8)
    )
    accepted = quality_passed or (accepted_limitations and bool(limitations or evidence_policy == "source_reconstructed"))
    if not accepted:
        return {
            "success": False, "status": "blocked", "code": "research_quality_failed",
            "message": "研究质量尚未通过，或未明确接受资料限制", "resource_uris": [source["resource_uri"]],
            "warnings": [], "blockers": ["research_quality_failed"],
            "next_actions": ["补齐来源、引用覆盖和缺失字段，或显式 accept_material_limitations=true"],
            "quality": metrics,
        }
    review_status = "passed" if quality_passed else "accepted_with_limitations"
    review_payload = {
        "research_package_id": package_id,
        "task_id": payload.get("task_id"),
        "status": review_status,
        "quality": metrics,
        "accepted_limitations": accepted_limitations and not quality_passed,
        "limitations": limitations,
        "evidence_policy": evidence_policy,
        # 【修复门禁泄漏】只有 review_status="passed" (完全通过，零 missing_fields、
        # 零 conflicts、非 source_reconstructed) 才认证项目事实。
        # accepted_with_limitations 意味着存在 missing_fields/conflicts 或
        # source_reconstructed，都不应认证项目事实，否则绕过正式交付门禁。
        "project_fact_certified": (
            review_status == "passed" and evidence_policy != "source_reconstructed"
        ),
    }
    # P0-009 修复：先推导 identity（object_id 是 payload 的确定性函数），构建并
    # 校验完整响应，最后再写 QualityReview 和 completed 包。这确保 outputSchema
    # 校验失败时零写入（不留 completed 状态污染），符合原子性约定。
    review_identity = QUALITY_REVIEW_STORE.preview_identity(workspace_id, review_payload)
    confirmed_payload = {
        **payload,
        "status": "completed",
        "quality_review_id": review_identity["object_id"],
        "quality_review_status": review_status,
        "quality": metrics,
        "project_fact_certified": review_payload["project_fact_certified"],
        "release_limitations": sorted(set([*(payload.get("release_limitations") or []), *limitations])),
    }
    confirmed_identity = PACKAGE_STORE.preview_identity(workspace_id, confirmed_payload)
    response = {
        "success": True, "status": "completed", "research_package_id": confirmed_identity["object_id"],
        "parent_research_package_id": package_id, "quality_review_id": review_identity["object_id"],
        "quality_review_status": review_status, "quality": metrics,
        "evidence_policy": evidence_policy,
        "project_fact_certified": review_payload["project_fact_certified"],
        "resource_uris": [review_identity["resource_uri"], confirmed_identity["resource_uri"]],
        "warnings": limitations, "blockers": [], "next_actions": [],
    }
    # 响应已构建且可被 outputSchema 校验（transport.py:566-580），现在落盘
    review = QUALITY_REVIEW_STORE.put(
        workspace_id, review_payload,
        producer="lvke-deep-research.dr_confirm_quality",
        source_ids=[package_id], basis=review_payload,
    )
    confirmed = PACKAGE_STORE.put(
        workspace_id, confirmed_payload,
        producer="lvke-deep-research.dr_confirm_quality",
        status="completed", source_ids=[package_id, str(payload.get("task_id") or ""), review["object_id"]],
        basis={"parent_package_id": package_id, "quality_review_id": review["object_id"]},
    )
    _append_event(workspace_id, str(payload.get("task_id") or ""), "research_quality_confirmed", {
        "research_package_id": confirmed["object_id"], "quality_review_id": review["object_id"], "status": review_status,
    })
    return response
