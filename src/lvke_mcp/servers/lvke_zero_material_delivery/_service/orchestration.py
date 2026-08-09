"""Cross-domain orchestration: research, planning context and the finance run."""

from __future__ import annotations

from datetime import date
from typing import Any


from lvke_mcp.runtime.storage import sha256_json

from .assumptions import _field_values
from .finance_align import _scenario_inputs


def _start_research(
    workspace_id: str,
    intent: dict[str, Any],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    from lvke_mcp.domains.research import application as research

    industry = dict(intent.get("industry") or {})
    return research.start_agent(
        {
            "workspace_id": workspace_id,
            "topic": f"{intent.get('project_name')}公开研究缺口登记",
            "industry": industry.get("industry_label"),
            "region": intent.get("region") or "待确认",
            "profile": "quick",
            "verify_urls": True,
            "research_brief": {
                "purpose": "登记零材料技术预估所需的公开研究缺口",
                "evidence_boundary": "会话仅用于采集公开来源；未提交带 locator 的来源前不得形成 ResearchPackage。",
            },
            "plan_items": [
                "识别行业、地区和政策公开资料",
                "收集可验证来源及其 locator",
                "缺少来源时保持研究会话和下游报告为 partial",
            ],
            "subqueries": ["行业公开资料", "地区政策与统计口径", "可比项目公开信息"],
            "source_policy": {"public_sources_only": True},
            "idempotency_key": idempotency_key,
        }
    )


def _create_project_context(
    workspace_id: str,
    intent: dict[str, Any],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    from lvke_mcp.domains.project_planning import application as planning

    industry = dict(intent.get("industry") or {})
    return planning.create_project_context(
        workspace_id,
        {
            "project_name": intent.get("project_name"),
            "industry_code": industry.get("industry_code"),
            "project_type": "new_build",
            "region": intent.get("region") or "待确认",
            "objective": "形成零材料技术预估和关键参数确认项",
            "report_type": "feasibility_study",
            "transaction_structure": "none",
            "evidence_track": "controlled_assumption",
            "description": intent.get("sentence"),
            "tags": ["zero_material", "estimate_preview"],
        },
        idempotency_key=idempotency_key,
    )


def execute(
    workspace_id: str,
    intent: dict[str, Any],
    assumption_package: dict[str, Any],
    *,
    operation_key: str,
) -> dict[str, Any]:
    """Execute only through existing domain boundaries; never grant release."""

    from lvke_mcp.domains.finance import model_application as finance
    from lvke_mcp.domains.finance import tables_service as tables

    lineage_key = sha256_json(
        {
            "intent_id": intent.get("delivery_intent_id"),
            "assumption_package_id": assumption_package.get("assumption_package_id"),
            "operation_key": operation_key,
        }
    ).removeprefix("sha256:")[:24]
    research = _start_research(
        workspace_id,
        intent,
        idempotency_key=f"zmd-research-{lineage_key}",
    )
    project_context = _create_project_context(
        workspace_id,
        intent,
        idempotency_key=f"zmd-context-{lineage_key}",
    )
    spec, finance_inputs, scenario_context = _scenario_inputs(assumption_package)
    validation = finance.validate_spec({"spec": spec, "for_formal": False})
    if not validation.get("valid"):
        return {
            "status": "model_blocked",
            "stage": "planning_ready",
            "research": research,
            "project_context": project_context,
            "finance_validation": validation,
            "blockers": ["finance_spec_validation_failed", *list(validation.get("blockers") or [])],
            "warnings": [],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
            ],
        }
    prepared = finance.prepare_spec(
        {
            "workspace_id": workspace_id,
            "spec": spec,
            "input_revision": finance_inputs,
            "evidence_pack_ids": [],
        }
    )
    candidate_spec_id = str(prepared.get("spec_id") or "")
    if not candidate_spec_id:
        return {
            "status": "model_blocked",
            "stage": "planning_ready",
            "research": research,
            "project_context": project_context,
            "finance_preparation": prepared,
            "blockers": ["finance_spec_prepare_failed", *list(prepared.get("blockers") or [])],
            "warnings": [],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
                *list(prepared.get("resource_uris") or []),
            ],
        }
    confirmed = finance.confirm_spec(
        {
            "workspace_id": workspace_id,
            "spec_id": candidate_spec_id,
            "note": "零材料技术预估使用受控假设确认 FinanceSpec；结果仅绑定当前输入快照。",
            "idempotency_key": f"zmd-confirm-{lineage_key}",
        }
    )
    confirmed_spec_id = str(confirmed.get("spec_id") or "")
    if not confirmed_spec_id:
        return {
            "status": "model_blocked",
            "stage": "planning_ready",
            "research": research,
            "project_context": project_context,
            "finance_preparation": prepared,
            "finance_confirmation": confirmed,
            "blockers": ["finance_spec_confirm_failed", *list(confirmed.get("blockers") or [])],
            "warnings": [],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
                "finance_candidate_spec_id": candidate_spec_id,
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
                *list(prepared.get("resource_uris") or []),
                *list(confirmed.get("resource_uris") or []),
            ],
        }
    # FinanceRun 之前做尺度对账：算术自洽不代表业务尺度成立。
    # 50 公里轨道线套用通用单体种子会全程通过校验并标记 finance_status=ok。
    from .scale_guard import check_project_scale

    scale_check = check_project_scale(
        industry_code=str((intent.get("industry") or {}).get("industry_code") or ""),
        explicit_inputs=intent.get("explicit_inputs"),
        field_values=_field_values(assumption_package),
    )
    if not scale_check["ok"]:
        return {
            "status": "model_blocked",
            "stage": "planning_ready",
            "research": research,
            "project_context": project_context,
            "finance_preparation": prepared,
            "finance_confirmation": confirmed,
            "scale_check": scale_check,
            "blockers": [
                *sorted({str(item.get("code")) for item in scale_check["issues"]}),
            ],
            "warnings": [str(item.get("detail")) for item in scale_check["advisories"]],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
                "finance_candidate_spec_id": candidate_spec_id,
                "finance_confirmed_spec_id": confirmed_spec_id,
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
                *list(prepared.get("resource_uris") or []),
                *list(confirmed.get("resource_uris") or []),
            ],
        }
    finance_run = finance.run_model(
        {
            "workspace_id": workspace_id,
            "spec_id": confirmed_spec_id,
            "mode": "estimate_preview",
            "valuation_date": date.today().isoformat(),
            "selected_scenario_id": scenario_context["scenario_id"],
            "idempotency_key": f"zmd-finance-run-{lineage_key}",
        }
    )
    finance_run_id = str(finance_run.get("run_id") or "")
    if not finance_run_id:
        return {
            "status": "model_blocked",
            "stage": "planning_ready",
            "research": research,
            "project_context": project_context,
            "finance_preparation": prepared,
            "finance_confirmation": confirmed,
            "finance_run": finance_run,
            "blockers": ["finance_run_failed", *list(finance_run.get("blockers") or [])],
            "warnings": [],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
                "finance_spec_id": confirmed_spec_id,
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
                *list(confirmed.get("resource_uris") or []),
                *list(finance_run.get("resource_uris") or []),
            ],
        }
    rendered = tables.render(workspace_id, finance_run_id)
    package_id = str(rendered.get("finance_tables_package_id") or "")
    if not package_id:
        return {
            "status": "artifact_failed",
            "stage": "finance_ready",
            "research": research,
            "project_context": project_context,
            "finance_run": finance_run,
            "tables": rendered,
            "blockers": ["finance_tables_render_failed", *list(rendered.get("blockers") or [])],
            "warnings": [],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
                "finance_spec_id": confirmed_spec_id,
                "finance_run_id": finance_run_id,
            },
            "resource_uris": [
                *list(finance_run.get("resource_uris") or []),
                *list(rendered.get("resource_uris") or []),
            ],
        }
    csv_export = tables.export_csv(workspace_id, finance_run_id)
    xlsx_export = tables.export_xlsx(workspace_id, finance_run_id)
    from lvke_mcp.domains.reports import application as report_generation

    report_preparation = report_generation.prepare(
        {
            "workspace_id": workspace_id,
            "evidence_pack_ids": [],
            "research_package_ids": [],
            "finance_binding": {
                "kind": "generic_feasibility",
                "run_id": finance_run_id,
                "package_id": package_id,
            },
            "outline": [
                "项目识别与交付边界",
                "受控假设与关键参数确认",
                "财务技术预估",
                "十三表与工件清单",
                "资料缺口与后续行动",
            ],
            "template_version": "zero-material-estimate-preview.v1",
        }
    )
    export_blockers = [
        *([] if csv_export.get("csv_resource_uris") else ["finance_tables_csv_export_failed"]),
        *([] if xlsx_export.get("xlsx_resource") else ["finance_tables_xlsx_export_failed"]),
    ]
    return {
        "status": "upstream_partial" if not export_blockers else "artifact_failed",
        "stage": "tables_ready" if not export_blockers else "finance_ready",
        "research": research,
        "project_context": project_context,
        "finance_preparation": prepared,
        "finance_confirmation": confirmed,
        "finance_run": finance_run,
        "tables": rendered,
        "csv_export": csv_export,
        "xlsx_export": xlsx_export,
        "report_preparation": report_preparation,
        "blockers": [
            "research_evidence_pending",
            "planning_market_evidence_pending",
            *list(report_preparation.get("blockers") or []),
            *export_blockers,
        ],
        "warnings": [
            "研究、规划与财务由 MCP 状态机编排，不依赖自由文本编排。",
            "受控假设只能用于 estimate_preview，不得升级为正式项目证据。",
        ],
        "object_refs": {
            "research_task_id": str(research.get("task_id") or ""),
            "project_context_id": str(project_context.get("project_context_id") or ""),
            "finance_spec_id": confirmed_spec_id,
            "finance_run_id": finance_run_id,
            "finance_tables_package_id": package_id,
            "csv_manifest_id": str(csv_export.get("csv_manifest_id") or ""),
            "report_preparation_id": str(report_preparation.get("report_preparation_id") or ""),
        },
        "resource_uris": sorted(
            {
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
                *list(prepared.get("resource_uris") or []),
                *list(confirmed.get("resource_uris") or []),
                *list(finance_run.get("resource_uris") or []),
                *list(rendered.get("resource_uris") or []),
                *list(csv_export.get("resource_uris") or []),
                *list(xlsx_export.get("resource_uris") or []),
                *list(report_preparation.get("resource_uris") or []),
            }
            - {""}
        ),
    }
