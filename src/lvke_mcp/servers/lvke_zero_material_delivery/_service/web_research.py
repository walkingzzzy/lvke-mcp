"""Automatic public research for zero-material feasibility previews.

The zero-material route owns the search attempt so a user does not need to
upload project materials merely to get a preview.  Search summaries are never
treated as evidence: selected URLs must become immutable snapshots, then pass
through analysis ingestion and EvidencePack creation.  When that path yields
no usable source, the caller receives an explicit controlled-assumption pack.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from typing import Any

from lvke_mcp.adapters.data_acquisition_repository import SOURCE_STORE
from lvke_mcp.domains.research import application as research
from lvke_mcp.runtime import service_gateway


_EXPECTED_FIELDS = (
    "visitor_volume",
    "spend_per_visitor",
    "total_investment_wan",
    "annual_revenue_wan",
    "build_period_months",
    "loan_ratio",
    "loan_rate",
    "operating_period_years",
)


def _queries(intent: dict[str, Any], route: dict[str, Any]) -> list[str]:
    project = str(intent.get("project_name") or "文旅综合体")
    region = str(intent.get("region") or "")
    industry = str((intent.get("industry") or {}).get("industry_label") or "文旅与休闲服务")
    prefix = f"{region} {project} {industry}".strip()
    return [
        f"{prefix} 政策 规划 审批 用地 环评 节能 招采 官方",
        f"{prefix} 旅游人数 旅游收入 客流 统计 官方",
        f"{prefix} 文旅市场 竞争 景区 酒店 公开数据",
        f"{prefix} 建设方案 投资 造价 运营 案例",
        f"{prefix} 风险 融资 收入 成本 财务 可比项目",
    ]


def _run_collect(
    workspace_id: str,
    discovery_set_id: str,
    candidate_ids: list[str],
) -> dict[str, Any]:
    """Run async collection from both sync tests and the async MCP loop.

    ``delivery_start`` is a synchronous domain handler invoked by the async
    MCP transport.  Calling ``asyncio.run`` directly there raises when the
    transport loop is already running, so use a short-lived worker thread in
    that case while preserving the synchronous orchestration contract.
    """

    coroutine = lambda: service_gateway.collect_public_sources(  # noqa: E731
        workspace_id,
        discovery_set_id,
        candidate_ids,
        content_mode="readable",
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine())

    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def worker() -> None:
        try:
            result.update(asyncio.run(coroutine()))
        except BaseException as exc:  # propagate the original provider error
            error.append(exc)

    thread = threading.Thread(target=worker, name="lvke-public-source-collect")
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result


def _source_citations(workspace_id: str, source_ids: list[str]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for source_id in source_ids:
        record = SOURCE_STORE.get(workspace_id, source_id)
        payload = (record or {}).get("payload") if isinstance(record, dict) else {}
        content = str((payload or {}).get("content") or "")
        if not content:
            continue
        content_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        end = min(len(content), 1200)
        citations.append(
            {
                "source_id": source_id,
                "resource_uri": str((record or {}).get("resource_uri") or ""),
                "content_hash": content_hash,
                "locator": {"kind": "web_snapshot", "start_offset": 0, "end_offset": end},
                "fragment_text": content[:end],
                "fragment_hash": "sha256:" + hashlib.sha256(content[:end].encode("utf-8")).hexdigest(),
                "claim": "公开来源快照已采集；具体主张仍需 Agent/人工判断语义支持",
            }
        )
    return citations


def _source_report(workspace_id: str, source_ids: list[str]) -> str:
    lines = [
        "# 零材料公开检索记录",
        "",
        "以下内容仅记录自动检索到的来源快照，不把搜索摘要直接当作项目事实。",
        "",
    ]
    for index, source_id in enumerate(source_ids, 1):
        record = SOURCE_STORE.get(workspace_id, source_id)
        payload = (record or {}).get("payload") if isinstance(record, dict) else {}
        lines.append(
            f"{index}. {str((payload or {}).get('title') or source_id)} "
            f"({str((payload or {}).get('url') or '')})"
        )
    return "\n".join(lines) + "\n"


def _source_summaries(workspace_id: str, source_ids: list[str]) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    for source_id in source_ids:
        record = SOURCE_STORE.get(workspace_id, source_id)
        payload = (record or {}).get("payload") if isinstance(record, dict) else {}
        summaries.append({
            "source_id": source_id,
            "title": str((payload or {}).get("title") or source_id),
            "url": str((payload or {}).get("url") or ""),
            "content_hash": str((record or {}).get("content_hash") or ""),
        })
    return summaries


def _fallback_pack(
    workspace_id: str,
    task_id: str,
    expected_fields: list[str],
) -> dict[str, Any]:
    return service_gateway.build_evidence_pack(
        workspace_id,
        task_id,
        [],
        [],
        [],
        expected_fields=expected_fields,
        evidence_track="controlled_assumption",
    )


def run(
    workspace_id: str,
    intent: dict[str, Any],
    route: dict[str, Any],
    research_task: dict[str, Any],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    """Search, snapshot and assemble zero-material public evidence."""

    task_id = str(research_task.get("task_id") or "")
    queries = _queries(intent, route)
    discovery = service_gateway.discover_public_sources(
        workspace_id,
        queries,
        limit_per_query=5,
        target_count=10,
        auto_expand=True,
        total_timeout_seconds=60,
    )
    source_ids: list[str] = []
    collection: dict[str, Any] = {}
    ingest: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    research_package: dict[str, Any] = {}

    if discovery.get("candidates"):
        candidate_ids = [
            str(item.get("candidate_id"))
            for item in list(discovery.get("candidates") or [])[:10]
            if str(item.get("candidate_id") or "")
        ]
        collection = _run_collect(workspace_id, str(discovery["discovery_set_id"]), candidate_ids)
        source_ids = [str(item) for item in collection.get("source_snapshot_ids") or [] if str(item)]

    if source_ids:
        ingest = service_gateway.ingest_sources(workspace_id, source_ids, [])
        analysis_task_id = str(ingest.get("analysis_task_id") or "")
        if analysis_task_id:
            evidence = service_gateway.build_evidence_pack(
                workspace_id,
                analysis_task_id,
                source_ids,
                [],
                [],
                expected_fields=[],
                evidence_track="real",
            )
        citations = _source_citations(workspace_id, source_ids)
        if citations and evidence.get("evidence_pack_id"):
            research_package = research.submit_agent(
                {
                    "workspace_id": workspace_id,
                    "task_id": task_id,
                    "report_md": _source_report(workspace_id, source_ids),
                    "citations": citations,
                    "evidence_pack_ids": [str(evidence["evidence_pack_id"])],
                    "source_snapshot_ids": source_ids,
                    "quality_summary": {
                        "missing_fields": list(_EXPECTED_FIELDS),
                    },
                    "unresolved_inputs": list(_EXPECTED_FIELDS),
                    "release_limitations": [
                        "公开来源已采集，但项目特定参数仍需语义核验或受控假设",
                        "ResearchPackage 由 Agent 生成，尚无独立质量审计",
                    ],
                }
            )

    if not evidence.get("evidence_pack_id"):
        evidence = _fallback_pack(workspace_id, task_id, list(_EXPECTED_FIELDS))

    fallback = not source_ids or not research_package.get("research_package_id")
    limitations = [
        "零材料路径不要求用户先提供原始资料；系统先尝试公开检索",
        *list(evidence.get("limitations") or []),
    ]
    if fallback:
        limitations.extend([
            "公开检索未形成可用于当前字段的完整来源链，已回退为受控假设",
            "受控假设仅用于 estimate_preview，不是项目事实或正式财务依据",
        ])
    return {
        "status": "fallback_assumptions" if fallback else "public_sources_collected",
        "fallback_used": fallback,
        "discovery_set_id": str(discovery.get("discovery_set_id") or ""),
        "discovery_status": str(discovery.get("status") or ""),
        "discovery_actual_count": int(discovery.get("actual_count") or 0),
        "collection_status": str(collection.get("status") or ""),
        "source_snapshot_ids": source_ids,
        "source_summaries": _source_summaries(workspace_id, source_ids),
        "analysis_task_id": str(ingest.get("analysis_task_id") or ""),
        "evidence_pack_id": str(evidence.get("evidence_pack_id") or ""),
        "research_package_id": str(research_package.get("research_package_id") or ""),
        "expected_fields": list(_EXPECTED_FIELDS),
        "unresolved_inputs": list(_EXPECTED_FIELDS),
        "limitations": sorted(set(limitations)),
        "next_actions": [
            "继续使用公开来源并在 Agent/人工审查中绑定具体主张"
            if not fallback
            else "后续获得来源或项目事实后创建新 EvidencePack、FinanceSpec 和 ReportRevision",
        ],
        "resource_uris": sorted({
            *list(discovery.get("resource_uris") or []),
            *list(collection.get("resource_uris") or []),
            *list(ingest.get("resource_uris") or []),
            *list(evidence.get("resource_uris") or []),
            *list(research_package.get("resource_uris") or []),
        } - {""}),
        "discovery_warnings": list(discovery.get("warnings") or []),
        "collection_warnings": list(collection.get("warnings") or []),
        "evidence": evidence,
        "research_package": research_package,
        "idempotency_key": idempotency_key,
    }
