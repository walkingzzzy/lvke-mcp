"""Full G1 synthetic golden chain — all governed stages in acceptance order."""

from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from acceptance_common import call_tool, classify_outcome, object_id_from_payload


@dataclass
class ChainStep:
    step: str
    tool: str
    server: str
    classification: str
    status: str
    object_id: str
    trace_id: str
    code: str
    protocol_error: str
    notes: str = ""


def run_golden_chain(workspace_id: str, data_dir: Path) -> list[ChainStep]:
    shared = str(data_dir)
    steps: list[ChainStep] = []
    idem = f"g1-golden-{uuid.uuid4().hex[:12]}"

    def chain(module: str, tool: str, args: dict[str, Any], *, timeout: float = 90) -> dict[str, Any]:
        payload, protocol_error = call_tool(module, tool, args, timeout=timeout, data_dir=shared)
        payload["_protocol_error"] = protocol_error
        return payload

    def record(
        step: str,
        server: str,
        tool: str,
        payload: dict[str, Any],
        *,
        notes: str = "",
    ) -> dict[str, Any]:
        protocol_error = payload.pop("_protocol_error", None)
        steps.append(
            ChainStep(
                step=step,
                tool=tool,
                server=server,
                classification=classify_outcome(payload, protocol_error=protocol_error),
                status=str(payload.get("status") or ""),
                object_id=object_id_from_payload(payload),
                trace_id=str(payload.get("trace_id") or ""),
                code=str(payload.get("code") or ""),
                protocol_error=str(protocol_error or ""),
                notes=notes,
            )
        )
        return payload

    m_plan = "lvke_mcp.servers.lvke_project_planning.server"
    m_src = "lvke_mcp.servers.lvke_source_files.server"
    m_data = "lvke_mcp.servers.lvke_data_acquisition.server"
    m_analysis = "lvke_mcp.servers.lvke_data_analysis.server"
    m_dr = "lvke_mcp.servers.lvke_deep_research.server"
    m_fdr = "lvke_mcp.servers.lvke_feasibility_delivery.server"
    m_fin = "lvke_mcp.servers.lvke_finance_model.server"
    m_tbl = "lvke_mcp.servers.lvke_finance_tables.server"
    m_rev = "lvke_mcp.servers.lvke_deliverable_review.server"
    m_rpt = "lvke_mcp.servers.lvke_report_generation.server"

    ctx = record(
        "ProjectContext",
        "lvke-project-planning",
        "project_context_create",
        chain(
            m_plan,
            "project_context_create",
            {
                "workspace_id": workspace_id,
                "idempotency_key": f"{idem}-ctx",
                "context": {
                    "project_name": "G1 Synthetic Golden Chain",
                    "industry_code": "tourism_catering",
                    "project_type": "new_build",
                    "region": {"province": "湖北省", "city": "咸宁市"},
                    "objective": "G1 controlled_assumption acceptance fixture",
                    "report_type": "feasibility_study",
                    "target_type": "project",
                    "asset_type": "other",
                    "evidence_track": "controlled_assumption",
                },
            },
        ),
    )
    context_id = str(ctx.get("context_id") or ctx.get("object_id") or "")

    fixture = (
        "G1 controlled_assumption fixture — 咸安区低空经济农文旅\n"
        "track=source_reconstructed; not formal_evidence\n"
    )
    imported = record(
        "SourceSnapshot",
        "lvke-source-files",
        "source_import_content",
        chain(
            m_src,
            "source_import_content",
            {
                "workspace_id": workspace_id,
                "original_filename": "g1_fixture.txt",
                "declared_mime": "text/plain",
                "content_base64": base64.b64encode(fixture.encode()).decode(),
                "idempotency_key": f"{idem}-src",
            },
        ),
    )
    source_id = str(imported.get("file_id") or imported.get("source_file_id") or "")

    discover = record(
        "DiscoverySet",
        "lvke-data-acquisition",
        "data_discover",
        chain(
            m_data,
            "data_discover",
            {
                "workspace_id": workspace_id,
                "queries": ["咸安区 低空经济 农文旅 政策"],
                "limit_per_query": 3,
            },
            timeout=120,
        ),
        notes="candidate summaries ≠ evidence",
    )

    ingested = record(
        "CandidateSet",
        "lvke-data-analysis",
        "analysis_ingest",
        chain(
            m_analysis,
            "analysis_ingest",
            {
                "workspace_id": workspace_id,
                "file_ids": [source_id] if source_id else [],
            },
        ),
    )
    analysis_id = str(ingested.get("analysis_task_id") or ingested.get("analysis_id") or "")

    evidence: dict[str, Any] = {}
    if analysis_id:
        evidence = record(
            "EvidencePack",
            "lvke-data-analysis",
            "analysis_build_evidence_pack",
            chain(
                m_analysis,
                "analysis_build_evidence_pack",
                {
                    "workspace_id": workspace_id,
                    "analysis_task_id": analysis_id,
                    "evidence_track": "source_reconstructed",
                },
            ),
        )
    evidence_pack_id = str(evidence.get("evidence_pack_id") or "")

    dr_prep = record(
        "ResearchPackage",
        "lvke-deep-research",
        "dr_prepare",
        chain(
            m_dr,
            "dr_prepare",
            {
                "workspace_id": workspace_id,
                "topic": "咸安区低空经济农文旅融合 — G1 synthetic research brief",
                "industry": "tourism_catering",
                "region": "湖北省咸宁市咸安区",
                "profile": "quick",
            },
        ),
    )

    fdr = record(
        "DeliveryRun",
        "lvke-feasibility-delivery",
        "feasibility_start",
        chain(
            m_fdr,
            "feasibility_start",
            {
                "workspace_id": workspace_id,
                "project_context_id": context_id,
                "delivery_mode": "estimate_preview",
                "evidence_policy": "controlled_assumption",
                "idempotency_key": f"{idem}-fdr",
            },
        ),
    )

    record(
        "IndustryConstraints",
        "lvke-project-planning",
        "planning_get_industry_constraints",
        chain(
            m_plan,
            "planning_get_industry_constraints",
            {
                "workspace_id": workspace_id,
                "project_context_id": context_id,
            },
        ),
        notes="planning chain entry without market_case dependency",
    )

    prep = record(
        "FinanceSpec",
        "lvke-finance-model",
        "finance_prepare_spec",
        chain(
            m_fin,
            "finance_prepare_spec",
            {"workspace_id": workspace_id, "strategy": "propose_from_project"},
            timeout=120,
        ),
    )
    spec_id = str(prep.get("spec_id") or "")
    conf = record(
        "FinanceSpecConfirmed",
        "lvke-finance-model",
        "finance_confirm_spec",
        chain(
            m_fin,
            "finance_confirm_spec",
            {
                "workspace_id": workspace_id,
                "spec_id": spec_id,
                "idempotency_key": f"{idem}-confirm",
            },
            timeout=120,
        ),
    )
    confirmed_id = str(conf.get("spec_id") or spec_id)
    run = record(
        "FinanceRun",
        "lvke-finance-model",
        "finance_run_model",
        chain(
            m_fin,
            "finance_run_model",
            {
                "workspace_id": workspace_id,
                "spec_id": confirmed_id,
                "mode": "estimate_preview",
                "valuation_date": "2026-08-20",
                "idempotency_key": f"{idem}-run",
                "input_revision": {
                    "total_investment_wan": 10000.0,
                    "annual_revenue_wan": 3000.0,
                    "is_operating": True,
                    "capital_own_wan": 4000.0,
                    "loan_wan": 6000.0,
                    "loan_rate": 0.045,
                    "loan_years": 8,
                    "loan_repay_method": "equal_principal",
                    "calc_period_years": 12,
                    "build_period_months": 12,
                    "depreciation_years": 10,
                },
            },
            timeout=180,
        ),
    )
    run_id = str(run.get("run_id") or "")

    tables = record(
        "FinanceTablesPackage",
        "lvke-finance-tables",
        "tables_render",
        chain(m_tbl, "tables_render", {"workspace_id": workspace_id, "run_id": run_id}, timeout=180),
        notes="validation_complete may be false for estimate_preview",
    )
    package_id = str(tables.get("package_id") or run_id)

    record(
        "TablesExportCsv",
        "lvke-finance-tables",
        "tables_export_csv",
        chain(
            m_tbl,
            "tables_export_csv",
            {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "finance_tables_package_id": package_id,
                "validation_scope": "technical",
            },
            timeout=180,
        ),
        notes="technical scope process artifact",
    )

    review_prep = record(
        "ReviewPrepare",
        "lvke-deliverable-review",
        "review_prepare",
        chain(
            m_rev,
            "review_prepare",
            {
                "workspace_id": workspace_id,
                "idempotency_key": f"{idem}-review-prep",
                "target": {"target_type": "finance_run", "target_id": run_id},
                "project_context": {
                    "review_purpose": "process_acceptance",
                    "evidence_track": "controlled_assumption",
                },
                "rule_pack_ids": ["finance-core"],
            },
            timeout=120,
        ),
    )
    review_prep_id = str(review_prep.get("review_preparation_id") or review_prep.get("object_id") or "")

    review = record(
        "Review",
        "lvke-deliverable-review",
        "review_start",
        chain(
            m_rev,
            "review_start",
            {
                "workspace_id": workspace_id,
                "review_preparation_id": review_prep_id,
                "mode": "quick",
                "execution": "sync",
                "idempotency_key": f"{idem}-review-start",
            },
            timeout=180,
        ),
    )
    review_id = str(review.get("review_id") or review.get("object_id") or "")

    if review_id:
        record(
            "ReviewFindings",
            "lvke-deliverable-review",
            "review_list_findings",
            chain(
                m_rev,
                "review_list_findings",
                {"workspace_id": workspace_id, "review_id": review_id, "limit": 20},
            ),
        )

    rpt_prep = record(
        "ReportPreparation",
        "lvke-report-generation",
        "report_prepare",
        chain(
            m_rpt,
            "report_prepare",
            {
                "workspace_id": workspace_id,
                "evidence_pack_ids": [evidence_pack_id] if evidence_pack_id else [],
                "research_package_ids": [],
                "project_context_id": context_id,
                "finance_binding": {
                    "kind": "generic_feasibility",
                    "run_id": run_id,
                    "package_id": package_id,
                },
            },
            timeout=120,
        ),
    )
    rpt_prep_id = str(rpt_prep.get("report_preparation_id") or rpt_prep.get("object_id") or "")

    rpt_start = record(
        "ReportRevisionDraft",
        "lvke-report-generation",
        "report_start",
        chain(
            m_rpt,
            "report_start",
            {
                "workspace_id": workspace_id,
                "report_preparation_id": rpt_prep_id,
                "document_snapshot": {
                    "content": "# G1 合成金标链\n\n本稿为 controlled_assumption 技术验收 fixture。\n",
                    "report_type": "feasibility_study",
                },
            },
            timeout=120,
        ),
    )
    task_id = str(rpt_start.get("task_id") or "")

    revision_id = ""
    basis_hash = str(rpt_prep.get("basis_hash") or "")
    if task_id:
        rpt_status = record(
            "ReportRevision",
            "lvke-report-generation",
            "report_status",
            chain(m_rpt, "report_status", {"workspace_id": workspace_id, "task_id": task_id}),
        )
        revision_id = str(rpt_status.get("report_revision_id") or "")
        basis_hash = basis_hash or str(rpt_status.get("basis_hash") or "")

    if revision_id and basis_hash:
        proposal = record(
            "ReportPropose",
            "lvke-report-generation",
            "report_propose",
            chain(
                m_rpt,
                "report_propose",
                {
                    "workspace_id": workspace_id,
                    "summary": "G1 section update",
                    "proposed_content": "## 1.1 项目概况\nG1 controlled_assumption fixture paragraph.\n",
                    "target_sections": ["sec_overview"],
                    "basis": {
                        "report_preparation_id": rpt_prep_id,
                        "basis_hash": basis_hash,
                        "report_revision_id": revision_id,
                    },
                },
                timeout=120,
            ),
        )
        proposal_id = str(proposal.get("proposal_id") or "")
        if proposal_id:
            record(
                "ReportDiff",
                "lvke-report-generation",
                "report_diff",
                chain(m_rpt, "report_diff", {"workspace_id": workspace_id, "proposal_id": proposal_id}),
            )
            record(
                "ReportApply",
                "lvke-report-generation",
                "report_apply",
                chain(
                    m_rpt,
                    "report_apply",
                    {"workspace_id": workspace_id, "proposal_id": proposal_id, "enforce_structure": False},
                    timeout=120,
                ),
            )

    record(
        "ReviewRetest",
        "lvke-deliverable-review",
        "review_retest",
        chain(
            m_rev,
            "review_retest",
            {
                "workspace_id": workspace_id,
                "review_id": review_id,
                "target": {"target_type": "finance_run", "target_id": run_id},
                "remediation_evidence": [
                    {
                        "source_id": source_id or run_id,
                        "locator": "g1-golden-chain/retest",
                        "content_hash": "sha256:" + hashlib.sha256(b"g1-retest-fixture").hexdigest(),
                        "note": "G1 controlled_assumption retest fixture",
                    }
                ],
                "idempotency_key": f"{idem}-retest",
            },
            timeout=120,
        ) if review_id else {"success": False, "status": "skipped", "code": "review_id_missing", "_protocol_error": None},
        notes="formal export blocked without EVD-2",
    )

    record(
        "ReviewExport",
        "lvke-deliverable-review",
        "review_export",
        chain(
            m_rev,
            "review_export",
            {
                "workspace_id": workspace_id,
                "review_id": review_id,
                "formats": ["json"],
                "idempotency_key": f"{idem}-export",
            },
            timeout=120,
        ) if review_id else {"success": False, "status": "skipped", "_protocol_error": None},
        notes="formal DOCX requires EVD-2 — process JSON only",
    )

    record(
        "FeasibilityValidate",
        "lvke-feasibility-delivery",
        "feasibility_validate",
        chain(
            m_fdr,
            "feasibility_validate",
            {
                "workspace_id": workspace_id,
                "delivery_run_id": str(fdr.get("delivery_run_id") or ""),
                "scope": "technical",
            },
            timeout=120,
        ),
    )

    return steps
