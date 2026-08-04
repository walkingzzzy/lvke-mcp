"""研究准备、续研（lineage 级）与不可变研究包投影。

注意 checkpoint 的真实落点：引擎把 checkpoint 写在
``task_dir/checkpoint.json``（见 ``research_engine.task_service`` 的
``_checkpoint_path``），而 ``artifacts/`` 落盘清单从不包含
``checkpoint.json``。因此 ``load_artifact('checkpoint')`` 恒为 None，
历史版本据此生成的 checkpoint Resource URI 是死链——本模块统一经
:func:`load_checkpoint` 回退读取真实落点，读不到就诚实省略。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from filelock import FileLock
from lvke_mcp.runtime.workspace import workspace_root

from lvke_mcp.runtime.storage import (
    JSONArtifactStore,
    canonical_json,
    sha256_json,
)

PACKAGE_STORE = JSONArtifactStore(
    "deep-research", "packages", "drp", "packages"
)
AGENT_SESSION_STORE = JSONArtifactStore(
    "deep-research", "agent-sessions", "drs", "sessions"
)
IDEMPOTENCY_STORE = JSONArtifactStore(
    "deep-research", "idempotency", "dridem", "idempotency"
)
AGENT_TRANSITION_STORE = JSONArtifactStore(
    "deep-research", "agent-transitions", "drstate", "transitions"
)
PLAN_STORE = JSONArtifactStore(
    "deep-research", "plan-revisions", "drplan", "plan-revisions"
)
PLAN_PROPOSAL_STORE = JSONArtifactStore(
    "deep-research", "plan-proposals", "drpp", "plan-proposals"
)
EVENT_STORE = JSONArtifactStore(
    "deep-research", "events", "drevent", "events"
)
CHECKPOINT_STORE = JSONArtifactStore(
    "deep-research", "checkpoints", "drcp", "checkpoints"
)

_TERMINAL = {"done", "partial", "needs_clarification", "blocked", "failed", "failed_report", "cancelled"}
_CONTINUABLE = {"partial", "needs_clarification", "blocked", "failed_report"}

# bundle 只固化 artifacts/ 目录真实存在的产物；checkpoint 单独经回退探测。
_BUNDLE_ARTIFACTS = ("report", "sources", "evidence", "extracts", "citation_audit", "quality")


@contextmanager
def _agent_transition_guard(workspace_id: str, task_id: str):
    directory = workspace_root(workspace_id) / "mcp_objects" / "deep-research" / "agent-locks"
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / f"{task_id}.lock"), timeout=30):
        yield


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


def _idempotency_ttl_seconds() -> int:
    try:
        return max(60, min(int(os.getenv("LVKE_MCP_IDEMPOTENCY_TTL_SECONDS", "86400")), 604800))
    except ValueError:
        return 86400


def _active_idempotency_record(
    workspace_id: str,
    key_hash: str,
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    records = sorted(
        IDEMPOTENCY_STORE.list(workspace_id),
        key=lambda record: str(record.get("created_at") or ""),
        reverse=True,
    )
    for record in records:
        saved = record.get("payload") or {}
        if saved.get("operation") != "dr_start" or saved.get("key_hash") != key_hash:
            continue
        try:
            expires_at = datetime.fromisoformat(str(saved.get("expires_at") or ""))
        except ValueError:
            continue
        if expires_at > now:
            return record
    return None


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
    packages = [
        record for record in PACKAGE_STORE.list(workspace_id)
        if task_id in (record.get("source_ids") or [])
    ]
    package = packages[-1] if packages else None
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
    blockers: list[str] = []
    if not report_md:
        blockers.append("report_required")
    if not citations:
        blockers.append("citations_required")
    if not evidence_pack_ids and not source_snapshot_ids:
        blockers.append("source_basis_required")
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
        "checkpoint": {"task_id": task_id, "stage": "agent_submitted"},
    }
    payload = {
        "task_id": task_id,
        "status": "partial",
        "profile": "agent_orchestrated",
        "artifact_names": list(artifacts),
        "agent_artifacts": artifacts,
        "limitations": ["正文由调用 Agent 撰写，尚无独立 DR 质量审计；下游必须披露 partial 限制"],
    }
    record = PACKAGE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-deep-research.dr_submit",
        status="partial",
        source_ids=[task_id, *evidence_pack_ids, *source_snapshot_ids],
        basis={"task_id": task_id, "citations": citations, "evidence_pack_ids": evidence_pack_ids, "source_snapshot_ids": source_snapshot_ids},
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


def prepare(args: dict[str, Any]) -> dict[str, Any]:
    topic = str(args.get("topic") or "").strip()
    industry = str(args.get("industry") or "").strip()
    region = str(args.get("region") or "").strip()
    objective = str(args.get("objective") or "形成可追溯的专题研究报告与证据包").strip()
    known_materials = [str(item).strip() for item in (args.get("known_materials") or []) if str(item).strip()]
    clarification = []
    if not region:
        clarification.append("研究地域范围未明确")
    if not industry:
        clarification.append("行业/项目类型未明确")
    questions = [
        f"{topic}的政策与合规约束是什么？",
        f"{topic}的市场规模、供需与竞争格局如何？",
        f"{topic}的建设条件、技术路径与主要风险是什么？",
        f"{topic}有哪些反证、不确定性和数据缺口？",
    ]
    if region:
        questions = [question.replace(f"{topic}", f"{region}{topic}") for question in questions]
    profile = _normalize_profile(str(args.get("profile") or "deep_standard"))
    default_budget = {
        "max_search_calls": 20 if profile == "quick" else 80,
        "max_rounds": 1 if profile == "quick" else 4,
    }
    budget = {**default_budget, **(args.get("budget") or {})}
    return {
        "success": True,
        "status": "partial" if clarification else "ok",
        "research_brief": {
            "topic": topic,
            "objective": objective,
            "scope": {"industry": industry, "region": region},
            "required_dimensions": ["政策", "市场", "技术", "风险", "反证"],
            "source_priorities": ["政府/统计", "监管/标准", "权威行业资料", "多源交叉验证"],
            "open_questions": clarification,
            "known_materials": known_materials,
            "status": "needs_clarification" if clarification else "ready",
        },
        "plan_items": [{"subquery": question, "priority": "normal"} for question in questions],
        "budget": budget,
        "expected_deliverables": ["report", "sources", "evidence", "quality", "citation_audit", "checkpoint"],
        "resource_uris": [],
        "warnings": clarification,
        "blockers": [],
        "next_actions": ["可补充澄清后调用 dr_start；也可接受限制并启动，最终状态可能为 partial"],
    }


def continue_task(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    agent = agent_status(workspace_id, task_id)
    if agent is not None:
        if agent.get("status") == "cancelled":
            return _failure("task_cancelled", "研究会话已取消，不能续研")
        if not agent.get("is_terminal"):
            return _failure("task_not_continuable", "Agent 研究尚未提交；请先完成 dr_submit")
        started = start_agent({
            **args,
            "workspace_id": workspace_id,
            "topic": str(((AGENT_SESSION_STORE.get(workspace_id, task_id) or {}).get("payload") or {}).get("topic") or "补充研究"),
            "continued_from_task_id": task_id,
        })
        return {
            "success": True, "status": started["status"], "task_id": started["task_id"],
            "continued_from_task_id": task_id, "quality_thresholds_relaxed": False,
            "resource_uris": [started["resource_uri"]],
            "warnings": ["新 Agent 研究会话保留原 partial lineage；continue 不会把原结果改写为 done"],
            "blockers": [], "next_actions": ["补充来源后调用 dr_submit"],
        }
    if task_id.startswith("drs_"):
        return _failure("task_not_found", "未找到研究任务")
    # 旧引擎任务仅保留 status/report/evidence/bundle 只读兼容；
    # 任何新研究或续研都必须由 Agent 会话编排。
    return {
        "success": False, "status": "blocked",
        "code": "legacy_task_read_only",
        "message": "旧 Deep Research 任务只读，不再支持续研创建",
        "resource_uris": [], "warnings": [],
        "blockers": ["legacy_task_read_only"],
        "next_actions": ["调用 dr_start 创建新 Agent 编排会话，并在 research_brief 中记录旧 task_id"],
    }


def _append_event(
    workspace_id: str,
    task_id: str,
    event_type: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Persist structured lifecycle state without model reasoning or hidden traces."""

    payload = {
        "task_id": task_id,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    linked_ids = [task_id]
    for key in (
        "session_id",
        "plan_revision_id",
        "proposal_id",
        "checkpoint_id",
        "new_task_id",
        "transition_id",
        "research_package_id",
        "base_plan_revision_id",
    ):
        if data.get(key):
            linked_ids.append(str(data[key]))
    linked_ids.extend(str(item) for item in data.get("source_ids") or [])
    return EVENT_STORE.put(
        workspace_id,
        payload,
        producer=f"lvke-deep-research.{event_type}",
        status="ok",
        source_ids=linked_ids,
        basis=payload,
    )


def _plan_records(
    workspace_id: str,
    task_id: str,
) -> list[dict[str, Any]]:
    records = [
        record
        for record in PLAN_STORE.list(workspace_id)
        if str((record.get("payload") or {}).get("task_id") or "") == task_id
    ]
    return sorted(
        records,
        key=lambda record: (
            int((record.get("payload") or {}).get("revision_no") or 0),
            str(record.get("created_at") or ""),
            str(record.get("object_id") or ""),
        ),
    )


def _latest_plan(
    workspace_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    records = _plan_records(workspace_id, task_id)
    return records[-1] if records else None


def _create_plan_revision(
    workspace_id: str,
    task_id: str,
    content: dict[str, Any],
    *,
    parent_plan_revision_id: str,
    source_ids: list[str],
    producer: str,
) -> dict[str, Any]:
    previous = _plan_records(workspace_id, task_id)
    revision_no = (int((previous[-1].get("payload") or {}).get("revision_no") or 0) + 1) if previous else 1
    normalized = {
        "research_brief": content.get("research_brief") if isinstance(content.get("research_brief"), dict) else {},
        "plan_items": list(content.get("plan_items") or []),
        "budget": content.get("budget") if isinstance(content.get("budget"), dict) else {},
        "sources": list(content.get("sources") or []),
        "excluded_sources": list(content.get("excluded_sources") or []),
        "quality_state": content.get("quality_state") if isinstance(content.get("quality_state"), dict) else {},
        "pending_work": list(content.get("pending_work") or []),
    }
    payload = {
        "task_id": task_id,
        "revision_no": revision_no,
        "parent_plan_revision_id": parent_plan_revision_id,
        **normalized,
    }
    return PLAN_STORE.put(
        workspace_id,
        payload,
        producer=producer,
        status="active",
        source_ids=[task_id, parent_plan_revision_id, *source_ids],
        basis={"task_id": task_id, "parent_plan_revision_id": parent_plan_revision_id, **normalized},
    )


def get_plan(
    workspace_id: str,
    task_id: str,
    *,
    plan_revision_id: str = "",
) -> dict[str, Any]:
    session = AGENT_SESSION_STORE.get(workspace_id, task_id)
    if session is None:
        return _failure("task_not_found", "未找到 Agent DR 会话")
    record = (
        PLAN_STORE.get(workspace_id, plan_revision_id)
        if plan_revision_id
        else _latest_plan(workspace_id, task_id)
    )
    if record is None or str((record.get("payload") or {}).get("task_id") or "") != task_id:
        return _failure("plan_not_found", "未找到当前任务的研究计划版本")
    return {
        "success": True,
        "status": "ok",
        "task_id": task_id,
        "plan_revision_id": record["object_id"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "plan": record["payload"],
        "resource_uris": [record["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def propose_plan_revision(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    current = _latest_plan(workspace_id, task_id)
    if current is None:
        return _failure("plan_not_found", "未找到可修订的研究计划")
    expected = str(args.get("expected_basis_hash") or "")
    if expected != str(current.get("basis_hash") or ""):
        return _failure("basis_hash_conflict", "研究计划已变更，请重新读取后提案")
    changes = args.get("changes") if isinstance(args.get("changes"), dict) else {}
    base = current.get("payload") or {}
    proposed = {
        key: changes.get(key, base.get(key))
        for key in (
            "research_brief",
            "plan_items",
            "budget",
            "sources",
            "excluded_sources",
            "quality_state",
            "pending_work",
        )
    }
    if proposed == {key: base.get(key) for key in proposed}:
        return _failure("empty_plan_revision", "提案未改变研究计划")
    payload = {
        "task_id": task_id,
        "base_plan_revision_id": current["object_id"],
        "base_basis_hash": current["basis_hash"],
        "reason": str(args.get("reason") or "")[:2000],
        "proposed_plan": proposed,
    }
    proposal = PLAN_PROPOSAL_STORE.put(
        workspace_id,
        payload,
        producer="lvke-deep-research.dr_propose_plan_revision",
        status="proposed",
        source_ids=[task_id, current["object_id"]],
        basis=payload,
    )
    _append_event(
        workspace_id,
        task_id,
        "plan_revision_proposed",
        {
            "proposal_id": proposal["object_id"],
            "base_plan_revision_id": current["object_id"],
        },
    )
    return {
        "success": True,
        "status": "ok",
        "task_id": task_id,
        "proposal_id": proposal["object_id"],
        "base_basis_hash": current["basis_hash"],
        "proposed_basis_hash": sha256_json(proposed),
        "resource_uris": [proposal["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": ["审阅提案后调用 dr_apply_plan_revision"],
    }


def apply_plan_revision(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    proposal_id = str(args["proposal_id"])
    with _agent_transition_guard(workspace_id, task_id):
        proposal = PLAN_PROPOSAL_STORE.get(workspace_id, proposal_id)
        if proposal is None or str((proposal.get("payload") or {}).get("task_id") or "") != task_id:
            return _failure("proposal_not_found", "未找到当前任务的研究计划提案")
        applied = [
            record
            for record in _plan_records(workspace_id, task_id)
            if proposal_id in (record.get("source_ids") or [])
        ]
        if applied:
            record = applied[-1]
            return {
                "success": True,
                "status": "ok",
                "task_id": task_id,
                "plan_revision_id": record["object_id"],
                "basis_hash": record["basis_hash"],
                "replayed": True,
                "resource_uris": [record["resource_uri"]],
                "warnings": [],
                "blockers": [],
                "next_actions": [],
            }
        current = _latest_plan(workspace_id, task_id)
        expected = str(args.get("expected_basis_hash") or "")
        proposal_payload = proposal.get("payload") or {}
        if (
            current is None
            or expected != str(current.get("basis_hash") or "")
            or expected != str(proposal_payload.get("base_basis_hash") or "")
        ):
            return _failure("basis_hash_conflict", "提案 basis 已过期，不能应用")
        record = _create_plan_revision(
            workspace_id,
            task_id,
            dict(proposal_payload.get("proposed_plan") or {}),
            parent_plan_revision_id=current["object_id"],
            source_ids=[proposal_id],
            producer="lvke-deep-research.dr_apply_plan_revision",
        )
        _append_event(
            workspace_id,
            task_id,
            "plan_revision_applied",
            {
                "proposal_id": proposal_id,
                "plan_revision_id": record["object_id"],
            },
        )
        return {
            "success": True,
            "status": "ok",
            "task_id": task_id,
            "plan_revision_id": record["object_id"],
            "basis_hash": record["basis_hash"],
            "replayed": False,
            "resource_uris": [record["resource_uri"]],
            "warnings": [],
            "blockers": [],
            "next_actions": [],
        }


def _source_identity(source: dict[str, Any]) -> str:
    return str(source.get("object_id") or "")


def add_sources(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    with _agent_transition_guard(workspace_id, task_id):
        current = _latest_plan(workspace_id, task_id)
        if current is None:
            return _failure("plan_not_found", "未找到可绑定来源的研究计划")
        if str(args.get("expected_basis_hash") or "") != str(current.get("basis_hash") or ""):
            return _failure("basis_hash_conflict", "研究计划已变更，请重新读取后添加来源")
        current_payload = dict(current.get("payload") or {})
        existing = {str(item.get("object_id") or ""): item for item in current_payload.get("sources") or []}
        for source in list(args.get("sources") or []):
            source_id = _source_identity(source)
            if source_id in existing and existing[source_id] != source:
                return _failure("source_binding_conflict", "同一来源对象已绑定不同 hash 或 locator")
            existing[source_id] = source
        current_payload["sources"] = sorted(existing.values(), key=_source_identity)
        record = _create_plan_revision(
            workspace_id,
            task_id,
            current_payload,
            parent_plan_revision_id=current["object_id"],
            source_ids=[_source_identity(source) for source in args.get("sources") or []],
            producer="lvke-deep-research.dr_add_sources",
        )
        _append_event(
            workspace_id,
            task_id,
            "sources_added",
            {
                "plan_revision_id": record["object_id"],
                "source_ids": [_source_identity(source) for source in args.get("sources") or []],
            },
        )
        return _plan_write_result(task_id, record)


def remove_sources(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    remove_ids = sorted({str(item) for item in args.get("source_object_ids") or []})
    with _agent_transition_guard(workspace_id, task_id):
        current = _latest_plan(workspace_id, task_id)
        if current is None:
            return _failure("plan_not_found", "未找到可修订的研究计划")
        if str(args.get("expected_basis_hash") or "") != str(current.get("basis_hash") or ""):
            return _failure("basis_hash_conflict", "研究计划已变更，请重新读取后移除来源")
        current_payload = dict(current.get("payload") or {})
        sources = list(current_payload.get("sources") or [])
        known = {_source_identity(source) for source in sources}
        missing = sorted(set(remove_ids) - known)
        if missing:
            return _failure("source_binding_not_found", "待移除来源不在当前计划中")
        current_payload["sources"] = [source for source in sources if _source_identity(source) not in remove_ids]
        exclusion = {
            "source_object_ids": remove_ids,
            "reason": str(args.get("reason") or "")[:2000],
            "excluded_at": datetime.now(timezone.utc).isoformat(),
        }
        current_payload["excluded_sources"] = [*(current_payload.get("excluded_sources") or []), exclusion]
        record = _create_plan_revision(
            workspace_id,
            task_id,
            current_payload,
            parent_plan_revision_id=current["object_id"],
            source_ids=remove_ids,
            producer="lvke-deep-research.dr_remove_sources",
        )
        _append_event(
            workspace_id,
            task_id,
            "sources_excluded",
            {
                "plan_revision_id": record["object_id"],
                **exclusion,
            },
        )
        result = _plan_write_result(task_id, record)
        result["excluded_source_object_ids"] = remove_ids
        return result


def _plan_write_result(task_id: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "status": "ok",
        "task_id": task_id,
        "plan_revision_id": record["object_id"],
        "basis_hash": record["basis_hash"],
        "resource_uris": [record["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def _encode_event_cursor(created_at: str, event_id: str) -> str:
    raw = canonical_json({"created_at": created_at, "event_id": event_id}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_event_cursor(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode((cursor + padding).encode("ascii")))
        created_at = str(payload.get("created_at") or "")
        event_id = str(payload.get("event_id") or "")
        if not created_at or not event_id:
            raise ValueError
        return created_at, event_id
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("event_cursor_invalid") from exc


def list_events(
    workspace_id: str,
    task_id: str,
    *,
    after_cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    if AGENT_SESSION_STORE.get(workspace_id, task_id) is None:
        return _failure("task_not_found", "未找到 Agent DR 会话")
    records = [
        record
        for record in EVENT_STORE.list(workspace_id)
        if str((record.get("payload") or {}).get("task_id") or "") == task_id
    ]
    records.sort(key=lambda record: (str(record.get("created_at") or ""), str(record.get("object_id") or "")))
    if after_cursor:
        try:
            marker = _decode_event_cursor(after_cursor)
        except ValueError:
            return _failure("event_cursor_invalid", "事件游标无效")
        records = [
            record
            for record in records
            if (str(record.get("created_at") or ""), str(record.get("object_id") or "")) > marker
        ]
    bounded = max(1, min(int(limit), 200))
    page = records[:bounded]
    has_more = len(records) > bounded
    next_cursor = (
        _encode_event_cursor(str(page[-1].get("created_at") or ""), str(page[-1].get("object_id") or ""))
        if page
        else after_cursor or None
    )
    return {
        "success": True,
        "status": "ok",
        "task_id": task_id,
        "events": [record.get("payload") or {} for record in page],
        "next_cursor": next_cursor,
        "has_more": has_more,
        "resource_uris": [str(record.get("resource_uri") or "") for record in page],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def _resume_signing_key(workspace_id: str) -> bytes:
    configured = os.getenv("LVKE_DR_RESUME_SIGNING_KEY", "").strip()
    if configured:
        return configured.encode("utf-8")
    directory = workspace_root(workspace_id) / "mcp_objects" / "deep-research"
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / ".resume-signing-key"
    with FileLock(str(key_path) + ".lock", timeout=30):
        if key_path.is_file():
            value = key_path.read_bytes()
            if len(value) >= 32:
                return value
        value = os.urandom(32)
        temporary = key_path.with_name(f".{key_path.name}.tmp")
        temporary.write_bytes(value)
        os.chmod(temporary, 0o600)
        os.replace(temporary, key_path)
        return value


def _sign_resume_token(workspace_id: str, claims: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(canonical_json(claims).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_resume_signing_key(workspace_id), payload.encode("ascii"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"drresume.v1.{payload}.{encoded_signature}"


def _verify_resume_token(workspace_id: str, token: str) -> dict[str, Any]:
    try:
        prefix, version, payload, signature = token.split(".")
        if (prefix, version) != ("drresume", "v1"):
            raise ValueError
        expected = hmac.new(_resume_signing_key(workspace_id), payload.encode("ascii"), hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode((signature + "=" * (-len(signature) % 4)).encode("ascii"))
        canonical_signature = base64.urlsafe_b64encode(actual).decode("ascii").rstrip("=")
        if not hmac.compare_digest(signature, canonical_signature):
            raise ValueError
        if not hmac.compare_digest(expected, actual):
            raise ValueError
        raw = base64.urlsafe_b64decode((payload + "=" * (-len(payload) % 4)).encode("ascii"))
        claims = json.loads(raw.decode("utf-8"))
        if not isinstance(claims, dict):
            raise ValueError
        return claims
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("resume_token_invalid") from exc


def create_checkpoint(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    task_id = str(args["task_id"])
    session = AGENT_SESSION_STORE.get(workspace_id, task_id)
    if session is None:
        return _failure("task_not_found", "未找到 Agent DR 会话")
    current = _latest_plan(workspace_id, task_id)
    if current is None:
        return _failure("plan_not_found", "未找到可固化的研究计划")
    if str(args.get("expected_basis_hash") or "") != str(current.get("basis_hash") or ""):
        return _failure("basis_hash_conflict", "研究计划已变更，不能创建 checkpoint")
    current_payload = current.get("payload") or {}
    task_state = agent_status(workspace_id, task_id) or {}
    payload = {
        "task_id": task_id,
        "plan_revision_id": current["object_id"],
        "plan_basis_hash": current["basis_hash"],
        "budget": current_payload.get("budget") or (session.get("payload") or {}).get("budget") or {},
        "sources": list(current_payload.get("sources") or []),
        "quality_state": current_payload.get("quality_state") or task_state.get("quality") or {},
        "pending_work": list(current_payload.get("pending_work") or []),
        "task_status": str(task_state.get("status") or "agent_collecting"),
        "reason": str(args.get("reason") or "")[:2000],
    }
    checkpoint = CHECKPOINT_STORE.put(
        workspace_id,
        payload,
        producer="lvke-deep-research.dr_create_checkpoint",
        status="checkpointed",
        source_ids=[task_id, current["object_id"]],
        basis=payload,
    )
    ttl = max(60, min(int(args.get("expires_in_seconds") or 86400), 604800))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    claims = {
        "workspace_id": workspace_id,
        "task_id": task_id,
        "checkpoint_id": checkpoint["object_id"],
        "basis_hash": checkpoint["basis_hash"],
        "expires_at": expires_at.isoformat(),
    }
    token = _sign_resume_token(workspace_id, claims)
    _append_event(
        workspace_id,
        task_id,
        "checkpoint_created",
        {
            "checkpoint_id": checkpoint["object_id"],
            "expires_at": claims["expires_at"],
        },
    )
    return {
        "success": True,
        "status": "ok",
        "task_id": task_id,
        "checkpoint_id": checkpoint["object_id"],
        "basis_hash": checkpoint["basis_hash"],
        "resume_token": token,
        "expires_at": claims["expires_at"],
        "resource_uris": [checkpoint["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": ["需恢复时调用 dr_resume；令牌不可跨 workspace 使用"],
    }


def resume_from_checkpoint(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    token = str(args.get("resume_token") or "")
    try:
        claims = _verify_resume_token(workspace_id, token)
    except ValueError:
        return _failure("resume_token_invalid", "恢复令牌无效或已被篡改")
    if claims.get("workspace_id") != workspace_id:
        return _failure("resume_scope_mismatch", "恢复令牌不属于当前 workspace")
    try:
        expires_at = datetime.fromisoformat(str(claims.get("expires_at") or ""))
    except ValueError:
        return _failure("resume_token_invalid", "恢复令牌缺少有效期")
    if expires_at <= datetime.now(timezone.utc):
        return _failure("resume_token_expired", "恢复令牌已过期")
    checkpoint_id = str(claims.get("checkpoint_id") or "")
    checkpoint = CHECKPOINT_STORE.get(workspace_id, checkpoint_id)
    if checkpoint is None or str(checkpoint.get("basis_hash") or "") != str(claims.get("basis_hash") or ""):
        return _failure("checkpoint_not_found", "恢复令牌对应的 checkpoint 不存在")
    checkpoint_payload = checkpoint.get("payload") or {}
    original_task_id = str(checkpoint_payload.get("task_id") or "")
    session = AGENT_SESSION_STORE.get(workspace_id, original_task_id)
    plan = PLAN_STORE.get(
        workspace_id,
        str(checkpoint_payload.get("plan_revision_id") or ""),
    )
    if session is None or plan is None:
        return _failure("checkpoint_lineage_missing", "checkpoint 的任务或计划 lineage 不完整")
    session_payload = session.get("payload") or {}
    plan_payload = plan.get("payload") or {}
    started = start_agent(
        {
            "workspace_id": workspace_id,
            "topic": session_payload.get("topic") or "恢复研究",
            "industry": session_payload.get("industry") or "",
            "region": session_payload.get("region") or "",
            "research_brief": plan_payload.get("research_brief") or {},
            "plan_items": plan_payload.get("plan_items") or [],
            "subqueries": list(args.get("supplemental_questions") or []),
            "budget": checkpoint_payload.get("budget") or {},
            "source_policy": session_payload.get("source_policy") or {},
            "source_descriptors": checkpoint_payload.get("sources") or [],
            "quality_state": checkpoint_payload.get("quality_state") or {},
            "pending_work": checkpoint_payload.get("pending_work") or [],
            "continued_from_task_id": original_task_id,
            "resumed_from_checkpoint_id": checkpoint_id,
            "idempotency_key": str(args.get("idempotency_key") or f"resume:{checkpoint_id}"),
        }
    )
    if not started.get("success"):
        return started
    if not started.get("replayed"):
        _append_event(
            workspace_id,
            original_task_id,
            "task_resumed",
            {
                "checkpoint_id": checkpoint_id,
                "new_task_id": started["task_id"],
            },
        )
    return {
        "success": True,
        "status": "ok",
        "task_id": started["task_id"],
        "resumed_from_task_id": original_task_id,
        "checkpoint_id": checkpoint_id,
        "plan_revision_id": started.get("plan_revision_id"),
        "plan_basis_hash": started.get("plan_basis_hash"),
        "replayed": bool(started.get("replayed")),
        "resource_uris": [str(started.get("resource_uri") or "")],
        "warnings": ["恢复创建新任务；原任务和 checkpoint 保持不变"],
        "blockers": [],
        "next_actions": ["调用 dr_get_plan 核对恢复后的计划与来源"],
    }


def load_checkpoint(workspace_id: str, task_id: str) -> Any:
    """Read an MCP-owned checkpoint for a task, if one exists."""
    records = [
        record for record in CHECKPOINT_STORE.list(workspace_id)
        if str((record.get("payload") or {}).get("task_id") or "") == str(task_id)
    ]
    if not records:
        return None
    return records[-1].get("payload") or None


def bundle(workspace_id: str, task_id: str) -> dict[str, Any]:
    agent = agent_status(workspace_id, task_id)
    if agent is not None:
        if agent.get("status") == "cancelled":
            return _failure("task_cancelled", "研究会话已取消，不能生成研究包")
        packages = [
            record for record in PACKAGE_STORE.list(workspace_id)
            if task_id in (record.get("source_ids") or [])
        ]
        if not packages:
            return _failure("task_not_terminal", "Agent 尚未提交研究发现；先调用 dr_submit")
        record = packages[-1]
        payload = record.get("payload") or {}
        base = record["resource_uri"]
        resources = {name: f"{base}/{name}" for name in payload.get("artifact_names") or []}
        return {
            "success": True, "status": "partial", "research_package_id": record["object_id"],
            "task_id": task_id, "basis_hash": record["basis_hash"], "resources": resources,
            "resource_uris": [base, *resources.values()],
            "warnings": list(payload.get("limitations") or []), "blockers": [],
            "next_actions": ["下游必须保留 partial 研究限制；财务数字仍只来自 run_id"],
        }
    return _failure("task_not_found", "未找到 MCP 自有研究任务")


def list_resources(workspace_id: str) -> list[dict[str, Any]]:
    """枚举单一工作区的研究包资源。

    MCP ``resources/list`` 没有工作区参数，也没有可供本地 stdio server 使用的
    授权 scope，因此不得在该协议入口枚举动态工作区对象。调用方必须通过显式
    携带 ``workspace_id`` 的 ``dr_list_resources`` 工具进入这里。
    """
    entries: list[dict[str, Any]] = []
    try:
        records = PACKAGE_STORE.list(workspace_id)
    except (OSError, ValueError):
        return []
    for record in records:
        base = str(record.get("resource_uri") or "").rstrip("/")
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        object_id = str(record.get("object_id") or "")
        if not base or not object_id:
            continue
        status = str(record.get("status") or "")
        task_id = str(payload.get("task_id") or "")
        entries.append(
            {
                "uri": base,
                "name": object_id,
                "description": f"研究包快照（status={status}，task_id={task_id}）",
                "mime_type": "application/json",
            }
        )
        for artifact in payload.get("artifact_names") or []:
            name = str(artifact)
            if not name:
                continue
            entries.append(
                {
                    "uri": f"{base}/{name}",
                    "name": f"{object_id}/{name}",
                    "description": f"研究包 {object_id} 的 {name} artifact",
                    "mime_type": "text/markdown" if name == "report" else "application/json",
                }
            )
    object_stores = (
        (AGENT_SESSION_STORE, "Agent 研究会话"),
        (PLAN_STORE, "研究计划不可变 revision"),
        (PLAN_PROPOSAL_STORE, "研究计划提案"),
        (EVENT_STORE, "研究结构化事件"),
        (CHECKPOINT_STORE, "研究恢复 checkpoint"),
    )
    for store, description in object_stores:
        try:
            records = store.list(workspace_id)
        except (OSError, ValueError):
            continue
        for record in records:
            uri = str(record.get("resource_uri") or "")
            object_id = str(record.get("object_id") or "")
            if uri and object_id:
                entries.append(
                    {
                        "uri": uri,
                        "name": object_id,
                        "description": description,
                        "mime_type": "application/json",
                    }
                )
    return sorted(entries, key=lambda entry: str(entry.get("uri") or ""))


def resolve_resource(
    uri: str,
    workspace_id: str,
) -> tuple[Any, str] | None:
    """Resolve a resource only when its URI belongs to the authorized workspace."""

    base_prefix = "lvke://deep-research/workspaces/"
    if not uri.startswith(base_prefix):
        return None
    parts = uri[len(base_prefix) :].split("/")
    if len(parts) not in {3, 4}:
        return None
    if parts[0] != str(workspace_id or "").strip():
        return None
    stores = {
        "packages": PACKAGE_STORE,
        "sessions": AGENT_SESSION_STORE,
        "plan-revisions": PLAN_STORE,
        "plan-proposals": PLAN_PROPOSAL_STORE,
        "events": EVENT_STORE,
        "checkpoints": CHECKPOINT_STORE,
    }
    store = stores.get(parts[1])
    if store is None or (len(parts) == 4 and parts[1] != "packages"):
        return None
    try:
        record = store.get(parts[0], parts[2])
    except ValueError:
        return None
    if record is None:
        return None
    if len(parts) == 3:
        return record, "application/json"
    name = parts[3]
    if name not in (record.get("payload") or {}).get("artifact_names", []):
        return None
    agent_artifacts = (record.get("payload") or {}).get("agent_artifacts")
    if isinstance(agent_artifacts, dict):
        value = agent_artifacts.get(name)
        if value is None:
            return None
        return value, "text/markdown" if name == "report" and isinstance(value, str) else "application/json"
    task_id = str((record.get("payload") or {}).get("task_id") or "")
    value = load_checkpoint(parts[0], task_id) if name == "checkpoint" else None
    if value is None:
        return None
    return value, "text/markdown" if name == "report" and isinstance(value, str) else "application/json"


def _normalize_profile(value: str) -> str:
    return "deep_standard" if value == "deep" else value if value in {"quick", "deep_assist", "deep_standard", "deep_max"} else "deep_standard"


def _failure(code: str, message: str) -> dict[str, Any]:
    # Some DR output schemas require task-specific fields on success.  Keep
    # this business block as a failure envelope; OfficialStdioServer exposes
    # blocked results to MCP clients with ``isError=false``.
    return {"success": False, "status": "blocked", "code": code, "message": message, "resource_uris": [], "warnings": [], "blockers": [code], "next_actions": []}
