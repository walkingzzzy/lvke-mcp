"""报告生成任务：准备、启动、状态查询与就绪度评估。"""

from __future__ import annotations

from typing import Any

from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.runtime.formal_promotion import FormalLineageError
from lvke_mcp.domains.reports.formal_lineage import (
    formal_report_lineage as _formal_report_lineage,
    validate_report_preparation_lineage,
    validate_report_revision_lineage,
)

from lvke_mcp.adapters.report_repository import (
    BINDING_STORE,
    PREPARATION_STORE,
    REVISION_STORE,
)
from lvke_mcp.domains.asset_acquisition.tables import get_package_record
from lvke_mcp.domains.reports._doc_service.outline import (
    report_chapter_titles,
    report_outline_descriptors,
    structure_uses_full_outline,
)
from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_STORE
from lvke_mcp.adapters.finance_tables_repository import PACKAGE_STORE as TABLE_STORE
from lvke_mcp.runtime.evidence_qualification import (
    CERTIFYING_POLICIES,
    SIM_A_FORMAL,
    combine_evidence_policies,
    declared_evidence_policy,
    project_fact_may_be_certified,
)
from lvke_mcp.runtime.quality_severity import split_quality_codes


from .base import (
    _TASK_TERMINAL,
    _capture_document_snapshot,
    _failure,
    _materialize_local_document_snapshot,
    _normalize_finance_binding,
    _normalize_outline,
    _resolve_revision_record,
    _supplied_document_snapshot,
)

from .sections import (
    validate,
)


def _build_report_prepare_next_actions(
    blockers: list[str],
    formal_blockers: list[str],
    formal_ready: bool,
    research_ids: list[str],
    evidence_ids: list[str],
    run_id: str,
    tables_id: str,
) -> list[str]:
    """Build actionable next_actions based on specific blockers.

    Differentiates missing ResearchPackage vs. missing evidence vs. missing
    finance artifacts, so the caller knows what object to create first.
    """
    actions: list[str] = []
    if "research_package_required" in blockers:
        actions.append(
            "缺少 ResearchPackage：请先调 dr_start 创建研究会话，"
            "完成内容收集后调 dr_submit 提交研究包，将返回的 research_package_id 传入 report_prepare"
        )
    if "evidence_pack_required" in blockers:
        actions.append(
            "缺少 EvidencePack：请先调分析工具生成 EvidencePack，"
            "将返回的 evidence_pack_id 传入 report_prepare"
        )
    if "finance_run_required" in blockers:
        actions.append(
            "缺少可用 FinanceRun：请先调 finance_run_model 完成财务计算，"
            "将返回的 run_id 传入 report_prepare"
        )
    if "finance_tables_package_required" in blockers:
        actions.append(
            "缺少十三表包：请先调 finance_run_model 确认生成 run_id，"
            "再调 tables_render 生成十三表 package，将返回的 package_id 传入 report_prepare"
        )
    if "legacy_finance_binding_ignored" in blockers:
        # 只报"旧绑定被忽略"不说该删哪个字段，调用方只能猜。
        actions.append(
            "同时传入了新契约 finance_binding 与旧字段 run_id / "
            "finance_tables_package_id：请删除顶层 run_id 与 "
            "finance_tables_package_id，只保留 "
            "finance_binding={kind, run_id, package_id}"
        )
    if not actions:
        actions.append("补齐或修复上游不可变对象后重新 report_prepare")
    research_ids_without_prefix = [rid for rid in research_ids if not rid.startswith("rpack_")]
    if research_ids_without_prefix:
        actions.append(
            f"传入的 research_package_ids 中 {len(research_ids_without_prefix)} 个不以 rpack_ 开头，"
            f"可能不是正确的 ResearchPackage 对象 ID；请确认使用 dr_submit 返回的 research_package_id"
        )
    return actions


def prepare(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(args["workspace_id"])
    evidence_ids = list(args.get("evidence_pack_ids") or [])
    research_ids = list(args.get("research_package_ids") or [])
    finance_binding, binding_errors = _normalize_finance_binding(args)
    binding_kind = finance_binding["kind"]
    run_id = finance_binding["run_id"]
    tables_id = finance_binding["package_id"]
    # 未传 outline 时回落到**正典大纲**的真实章节，而不是伪造一个
    # section_id="all"：那个假章节让 report_list_sections / review_score_section
    # 拿不到可评分的 section（报 section_not_found），并连带阻断知识治理
    # （knowledge_submit_candidate 必须绑真实 RubricAssessment）。
    # 走同一个 _normalize_outline，不另造一套 descriptor 生成逻辑。
    _requested_outline = list(args.get("outline") or [])
    _outline_defaulted = not _requested_outline
    if _outline_defaulted:
        # 显式传了 report_type 就按它；没传按 DEFAULT_REPORT_TYPE(gov10)。
        # 这里刻意**不**走 resolve_report_type 的 legacy_b9 回退——那条回退是给
        # 历史工作区兼容用的（9 章旧结构），新建 preparation 用它会与结构校验
        # 期望的 2023 通用大纲十章不一致。
        _meta_report_type = str((args.get("project_metadata") or {}).get("report_type") or "").strip()
        # 细粒度结构（声明了 full_outline）回落到**全层级** descriptor：只给章标题时，
        # 节/叶没有 descriptor，report_propose_section 拿不到 section_id，整篇只能整章落稿，
        # 而整章在细粒度结构下是 1 万字量级。其余结构保持只用章标题，避免把结构
        # 校验从「必须有 10 个章」无缘无故收紧为「必须有 46 个标题且严格按序」。
        if structure_uses_full_outline(_meta_report_type):
            _requested_outline = report_outline_descriptors(_meta_report_type)
        else:
            _requested_outline = report_chapter_titles(_meta_report_type)
    outline, sections, outline_errors = _normalize_outline(_requested_outline)
    quality_issues: list[str] = [*binding_errors, *outline_errors]
    warnings: list[str] = []
    formal_blockers: list[str] = []
    viability_status = "not_assessed"
    viability_issues: list[Any] = []
    table_contract_snapshot: dict[str, Any] = {}
    evidence = []
    for object_id in evidence_ids:
        record = EVIDENCE_STORE.get(
            workspace_id,
            object_id,
        )
        if record is None:
            quality_issues.append(f"evidence_pack_not_found:{object_id}")
        else:
            evidence.append(record)
    research = []
    for object_id in research_ids:
        record = RESEARCH_STORE.get(
            workspace_id,
            object_id,
        )
        if record is None:
            quality_issues.append(f"research_package_not_found:{object_id}")
        else:
            research.append(record)
            research_status = str(record.get("status") or "")
            if research_status == "partial":
                warnings.append(f"{object_id}: DR 为 partial，正文必须披露研究限制")
                formal_blockers.append(f"research_package_partial:{object_id}")
            elif research_status not in {"done", "completed", "ok"}:
                # A package ID alone is not evidence that the corresponding DR
                # task produced usable research artifacts.  In particular, a
                # failed task may only contain a checkpoint and must never be
                # presented as a complete research basis for report drafting.
                quality_issues.append(
                    f"research_package_not_usable:{object_id}:{research_status or 'unknown'}"
                )
    if not evidence_ids:
        quality_issues.append("evidence_pack_required")
    if not research_ids:
        policy = str(args.get("evidence_policy") or "")
        pack_policies = {
            declared_evidence_policy(record.get("payload") or record)
            for record in evidence
        }
        process_draft = (
            policy in {
                "controlled_assumption", "technical_fixture", "sim_a_formal", "source_reconstructed",
            }
            or str(args.get("release_scope") or "") == "process_acceptance"
            or bool(pack_policies) and pack_policies <= {
                "controlled_assumption", "technical_fixture", "sim_a_formal", "source_reconstructed",
            }
        )
        if process_draft:
            formal_blockers.append("research_package_required")
            warnings.append("无 ResearchPackage：过程草稿可继续，正式发布仍须绑定已确认研究包")
        else:
            quality_issues.append("research_package_required")
    if binding_kind == "asset_acquisition":
        from lvke_mcp.domains.asset_acquisition.backend import get_run

        run = (
            get_run(workspace_id, run_id)
            if run_id
            else {}
        )
        run_available = bool(run.get("available") and run.get("status") == "succeeded")
        table_record = (
            get_package_record(
                workspace_id,
                tables_id,
            )
            if tables_id
            else None
        )
        package_required = "acquisition_tables_package_required"
        package_mismatch = "acquisition_tables_run_mismatch"
    else:
        from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

        run = (
            get_workspace_finance_run(
                workspace_id,
                run_id=run_id,
                view="summary",
            )
            if run_id
            else {}
        )
        run_available = bool(run.get("available"))
        viability_status = str(run.get("viability_status") or "not_assessed")
        viability_issues = list(run.get("viability_issues") or [])
        if run_available and viability_status == "infeasible":
            warnings.append(
                f"财务评估结论为不可行（viability=infeasible），"
                f"含 {len(viability_issues)} 项可行性阻断指标；"
                f"报告正文须明确披露此结论，不得声称项目可行"
            )
        table_record = (
            TABLE_STORE.get(workspace_id, tables_id)
            if tables_id
            else None
        )
        package_required = "finance_tables_package_required"
        package_mismatch = "finance_tables_run_mismatch"
    if not run_id or not run_available:
            quality_issues.append("finance_run_required")
    if table_record is None:
        quality_issues.append(package_required)
    else:
        table_run = str((table_record.get("payload") or {}).get("run_id") or "")
        if table_run != run_id:
            quality_issues.append(package_mismatch)
        table_payload = table_record.get("payload") or {}
        if binding_kind == "asset_acquisition":
            package_formal = (
                str(table_record.get("status") or "") == "ok"
                and (table_payload.get("integrity") or {}).get("status") == "passed"
            )
        else:
            package_formal = (
                str(table_record.get("status") or "") == "ok"
                and table_payload.get("validation_complete") is True
            )
        if not package_formal:
            formal_blockers.append("finance_tables_package_not_formal")
        if binding_kind != "asset_acquisition":
            from lvke_mcp.domains.finance.tables_application import validate_render

            package_contract = validate_render(table_payload)
            contract_issues = [
                str(item) for item in package_contract.get("blockers") or []
            ]
            formal_blockers.extend(
                f"finance_tables_contract:{item}" for item in contract_issues
            )
            table_contract_snapshot = {
                "table_contract_hash": str(
                    package_contract.get("table_contract_hash") or ""
                ),
                "table_manifest_hash": sha256_json(
                    table_payload.get("table_manifest") or []
                ),
                "engine_delivery_count": package_contract.get(
                    "engine_delivery_count"
                ),
                "reference_source_sheet_count": package_contract.get(
                    "reference_source_sheet_count"
                ),
                "review_workbook_sheet_count": package_contract.get(
                    "review_workbook_sheet_count"
                ),
                "manifest_count": package_contract.get("manifest_count"),
                "contract_valid": not contract_issues,
                "quality_issues": contract_issues,
            }
        if binding_kind == "asset_acquisition":
            integrity = table_payload.get("integrity") or {}
            if integrity.get("status") != "passed":
                quality_issues.append("acquisition_tables_integrity_failed")
            for field in (
                "spec_hash", "input_hash", "model_version", "evidence_binding_hash",
            ):
                if table_payload.get(field) != run.get(field):
                    quality_issues.append(f"acquisition_tables_{field}_mismatch")
    upstream_evidence_payloads = [
        record.get("payload") or {}
        for record in [*evidence, *research]
        if isinstance(record, dict)
    ]
    if isinstance(run, dict) and run:
        upstream_evidence_payloads.append(run)
    if isinstance(table_record, dict):
        upstream_evidence_payloads.append(table_record.get("payload") or {})
    evidence_policy = combine_evidence_policies(upstream_evidence_payloads)
    canonical_lineage: dict[str, Any] = {}
    formal_requested = evidence_policy == SIM_A_FORMAL or any(
        declared_evidence_policy(item) == SIM_A_FORMAL
        for item in upstream_evidence_payloads
    )
    if binding_kind != "asset_acquisition" and formal_requested:
        try:
            canonical_lineage = _formal_report_lineage(
                workspace_id,
                evidence_records=evidence,
                research_records=research,
                run_id=run_id,
                table_record=table_record,
            )
        except FormalLineageError as exc:
            formal_blockers.append(f"formal_lineage:{exc.code}")
            warnings.append(f"正式 promotion 谱系无效：{exc.message}")
            evidence_policy = SIM_A_FORMAL
            project_fact_certified = False
        else:
            evidence_policy = SIM_A_FORMAL
            project_fact_certified = True
    else:
        project_fact_certified = project_fact_may_be_certified(
            evidence_policy,
            own_qualification_passed=True,
            parents=upstream_evidence_payloads,
        )
    if not project_fact_certified:
        formal_blockers.append("project_fact_not_certified")
    basis = {
        "evidence_pack_ids": evidence_ids,
        "research_package_ids": research_ids,
        "run_id": run_id,
        "finance_tables_package_id": tables_id,
        "finance_binding": finance_binding,
        "finance_table_contract": table_contract_snapshot,
        "outline": outline,
        "sections": sections,
        "template_version": str(args.get("template_version") or "default"),
        "upstream_hashes": {
            "evidence": [record.get("basis_hash") for record in evidence],
            "research": [record.get("basis_hash") for record in research],
            "finance_spec": run.get("spec_hash"),
            "finance_tables": table_record.get("basis_hash") if table_record else None,
        },
        "evidence_policy": evidence_policy,
        "evidence_origin": canonical_lineage.get("evidence_origin"),
        "project_fact_certified": project_fact_certified,
        "formal_promotion": canonical_lineage.get("formal_promotion"),
        "reconstruction_records": list(args.get("reconstruction_records") or [item for record in evidence for item in ((record.get("payload") or {}).get("reconstruction_records") or []) if isinstance(item, dict)]),
        "reconstructed_source_ids": list(args.get("reconstructed_source_ids") or []),
        "unresolved_inputs": list(args.get("unresolved_inputs") or []),
        "release_limitations": list(args.get("release_limitations") or []),
        "project_context_id": str(args.get("project_context_id") or ""),
        "project_metadata": dict(args.get("project_metadata") or {}),
        "upstream_refs": list(args.get("upstream_refs") or [*evidence_ids, *research_ids, run_id, tables_id]),
        "viability_status": viability_status,
        "viability_issues": viability_issues,
    }
    quality_issues = sorted(set([*quality_issues, *formal_blockers]))
    warnings.extend(f"质量提示：{item}" for item in quality_issues)
    draft_ready = True
    formal_ready = True
    status = "partial" if warnings or quality_issues else "ok"
    record = PREPARATION_STORE.put(
        workspace_id,
        {
            **basis,
            "blockers": [],
            "warnings": warnings,
            "formal_blockers": [],
            "quality_issues": quality_issues,
            "draft_ready": draft_ready,
            "formal_ready": formal_ready,
            "artifact_kind": "internal_diagnostic_draft",
            "confirmation_status": "not_required",
            "diagnostic_only": True,
            "human_confirmation_required": False,
            "formal_report_allowed": True,
            "uncertainty_summary": [],
            "quality_diagnostic_ids": [],
        },
        producer="lvke-report-generation.report_prepare",
        status=status,
        source_ids=[*evidence_ids, *research_ids, run_id, tables_id],
        basis=basis,
    )
    return {
        "success": True,
        "transport_success": True,
        "business_success": True,
        "completed": True,
        "outcome": status,
        "status": status,
        "ready": draft_ready,
        "draft_ready": draft_ready,
        "formal_ready": formal_ready,
        "report_preparation_id": record["object_id"],
        "basis_hash": record["basis_hash"],
        "generatable_sections": sections,
        "outline_source": "default_standard_outline" if _outline_defaulted else "caller_outline",
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": [],
        "formal_blockers": [],
        "quality_issues": quality_issues,
        "finance_table_contract": table_contract_snapshot,
        "viability_status": viability_status,
        "viability_issues": viability_issues,
        "artifact_kind": "internal_diagnostic_draft",
        "confirmation_status": "not_required",
        "diagnostic_only": True,
        "human_confirmation_required": False,
        "formal_report_allowed": True,
        "uncertainty_summary": [],
        "quality_diagnostic_ids": [],
        "next_actions": ["可调用 report_start 生成内部诊断草稿；质量问题必须在正文披露"],
    }


def start(args: dict[str, Any]) -> dict[str, Any]:
    """Create a bound draft workspace; the calling Agent writes the prose.

    MCP is the execution and integrity layer, not a nested LLM client.  The
    previous implementation delegated to the legacy web report generator,
    requiring a second model gateway and producing an opaque second-agent
    workflow.  Keep the public tool for compatibility, but make it a
    deterministic hand-off to ``report_propose → report_diff → report_apply``.
    """
    workspace_id = str(args["workspace_id"])
    preparation_id = str(args["report_preparation_id"])
    prep = PREPARATION_STORE.get(
        workspace_id,
        preparation_id,
    )
    if prep is None:
        return _failure("preparation_not_found", "未找到研报准备记录")
    prep_payload = prep.get("payload") or {}
    if str(prep_payload.get("evidence_policy") or "") == SIM_A_FORMAL:
        try:
            validate_report_preparation_lineage(workspace_id, prep)
        except FormalLineageError as exc:
            # Formal provenance is diagnostic metadata, not a prerequisite for
            # starting the report drafting workflow.
            prep_payload = {
                **prep_payload,
                "quality_issues": sorted(
                    set(str(item) for item in (prep_payload.get("quality_issues") or []))
                    | {exc.code}
                ),
            }
    supplied_document = _supplied_document_snapshot(
        workspace_id,
        args.get("document_snapshot"),
    )
    if supplied_document is not None:
        # An explicit immutable snapshot is authoritative for this revision.
        # This prevents a caller-supplied draft from being silently replaced
        # by the native workspace's current text.
        document = _materialize_local_document_snapshot(workspace_id, supplied_document)
    else:
        document = _capture_document_snapshot(workspace_id)
    native_revision = str(document.get("revision_id") or "")
    payload = {
        "native_revision_id": native_revision,
        "report_preparation_id": preparation_id,
        "basis_hash": prep.get("basis_hash"),
        "upstream": prep_payload,
        "task_status": "agent_drafting",
        "requested_chapters": list(args.get("chapters") or []),
        "document_snapshot": document,
        "artifact_kind": "internal_diagnostic_draft",
        "confirmation_status": "not_required",
        "diagnostic_only": True,
        "human_confirmation_required": False,
        "formal_report_allowed": True,
        "uncertainty_summary": list(prep_payload.get("uncertainty_summary") or []),
        "quality_diagnostic_ids": list(prep_payload.get("quality_diagnostic_ids") or []),
    }
    revision = REVISION_STORE.put(
        workspace_id,
        payload,
        producer="lvke-report-generation.report_start",
        status="agent_drafting",
        source_ids=[preparation_id],
        basis={"preparation_id": preparation_id, "basis_hash": prep.get("basis_hash")},
    )
    return {
        "success": True,
        "status": "agent_action_required",
        "task_id": revision["object_id"],
        "report_revision_id": revision["object_id"],
        "resource_uris": [revision["resource_uri"]],
        "warnings": ["MCP 不调用内置 LLM；正文由当前 Agent 基于上游依据起草"],
        "blockers": [],
        # 技术验收阶段：报告修订固定为内部诊断草稿（§6/§9-8~14）
        "artifact_kind": "internal_diagnostic_draft",
        "confirmation_status": "pending_external",
        "uncertainty_summary": [],
        "quality_diagnostic_ids": [],
        "next_actions": ["调用 report_propose → report_diff → report_apply；完成后用 report_status 固化新修订"],
    }


def status(
    workspace_id: str,
    task_id: str,
) -> dict[str, Any]:
    agent_revision = REVISION_STORE.get(
        workspace_id,
        task_id,
    )
    if agent_revision is not None:
        prior = agent_revision.get("payload") or {}
        document = _capture_document_snapshot(workspace_id)
        native_revision = str(document.get("revision_id") or "")
        payload = {
            **prior,
            "task_id": task_id,
            "native_revision_id": native_revision,
            "task_status": "agent_drafted",
            "document_snapshot": document,
        }
        revision = _existing_status_revision(
            workspace_id,
            task_id=task_id,
            native_revision_id=native_revision,
            task_status="agent_drafted",
            payload=payload,
        ) or REVISION_STORE.put(
            workspace_id,
            payload,
            producer="lvke-report-generation.report_status",
            status="partial",
            source_ids=[str(prior.get("report_preparation_id") or "")],
            basis={"native_revision_id": native_revision, "upstream_basis_hash": prior.get("basis_hash")},
        )
        return {
            "success": True,
            "status": "agent_drafted",
            "task_id": task_id,
            "report_revision_id": revision["object_id"],
            "chapter_progress": [],
            "failed_or_partial_chapters": [],
            "resource_uris": [revision["resource_uri"]],
            "warnings": ["已绑定 Agent 当前草稿修订；仍须 report_validate 后才能导出候选工件"],
            "blockers": [],
            "next_actions": ["调用 report_validate；需要修改时继续 propose→diff→apply"],
        }
    from lvke_mcp.domains.reports.doc_service import load_gen_task as _load_gen_task

    task = _load_gen_task(workspace_id, task_id)
    if task is None:
        return _failure("task_not_found", "未找到研报生成任务")
    task_status = str(task.get("status") or "")
    revision_result = None
    resource_uris: list[str] = []
    if task_status in _TASK_TERMINAL:
        from lvke_mcp.domains.reports.doc_service import load_workspace_snapshot

        snapshot = load_workspace_snapshot(workspace_id)
        native_revision = str(snapshot.get("current_revision_id") or "")
        binding = next(
            (
                row
                for row in BINDING_STORE.list(workspace_id)
                if str((row.get("payload") or {}).get("task_id") or "") == task_id
            ),
            None,
        )
        binding_payload = (binding or {}).get("payload") or {}
        prep = PREPARATION_STORE.get(
            workspace_id,
            str(binding_payload.get("report_preparation_id") or ""),
        )
        document = _capture_document_snapshot(workspace_id)
        payload = {
            "task_id": task_id,
            "native_revision_id": native_revision,
            "report_preparation_id": binding_payload.get("report_preparation_id"),
            "basis_hash": (prep or {}).get("basis_hash"),
            "upstream": (prep or {}).get("payload") or {},
            "task_status": task_status,
            "document_snapshot": document,
        }
        revision = _existing_status_revision(
            workspace_id,
            task_id=task_id,
            native_revision_id=native_revision,
            task_status=task_status,
            payload=payload,
        ) or REVISION_STORE.put(
            workspace_id, payload, producer="lvke-report-generation.report_status",
            status="partial" if task_status == "partial" else ("ok" if task_status in {"done", "completed"} else task_status),
            source_ids=[str(binding_payload.get("report_preparation_id") or "")],
            basis={"native_revision_id": native_revision, "upstream_basis_hash": (prep or {}).get("basis_hash")},
        )
        revision_result = revision["object_id"]
        resource_uris.append(revision["resource_uri"])
    chapters = task.get("chapters") or []
    failures = [
        item
        for item in chapters
        if isinstance(item, dict) and str(item.get("state") or "") in {"failed", "partial"}
    ]
    return {
        "success": task_status not in {"failed", "cancelled"},
        "status": task_status or "pending",
        "task_id": task_id,
        "report_revision_id": revision_result,
        "chapter_progress": chapters,
        "failed_or_partial_chapters": failures,
        "resource_uris": resource_uris,
        "warnings": ["任务成功只代表草稿生成；正式工件仍由 readiness 门禁决定"],
        "blockers": [str(task.get("error") or "report_generation_failed")] if task_status == "failed" else [],
        "next_actions": ["终态后调用 report_validate，再导出 draft 或 formal_candidate"],
    }


def _existing_status_revision(
    workspace_id: str,
    *,
    task_id: str,
    native_revision_id: str,
    task_status: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the immutable status projection already created for this state."""

    expected_hash = sha256_json(payload)
    candidates = sorted(
        REVISION_STORE.list(workspace_id),
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    for record in candidates:
        if str(record.get("producer") or "") != "lvke-report-generation.report_status":
            continue
        record_payload = record.get("payload") or {}
        if str(record_payload.get("native_revision_id") or "") != native_revision_id:
            continue
        if str(record_payload.get("task_status") or "") != task_status:
            continue
        bound_task = str(record_payload.get("task_id") or "")
        if bound_task and bound_task != task_id:
            continue
        if str(record.get("content_hash") or "") == expected_hash:
            return record
    return None


def readiness(
    workspace_id: str,
    revision_id: str = "",
) -> dict[str, Any]:
    resolved_revision_id = str(revision_id or "").strip()
    if resolved_revision_id:
        record, _native_alias = _resolve_revision_record(
            workspace_id, resolved_revision_id
        )
    else:
        revisions = sorted(
            REVISION_STORE.list(workspace_id),
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )
        record = revisions[0] if revisions else None
    if record is None:
        return _failure("revision_not_found", "未找到指定工作区的研报修订")
    resolved_revision_id = str(record.get("object_id") or "")
    checked = validate(
        workspace_id,
        resolved_revision_id,
    )
    if not checked.get("success"):
        checked_issues = sorted(set([
            *(str(item) for item in (checked.get("blockers") or [])),
            *(str(item) for item in (checked.get("quality_issues") or [])),
        ]))
        return {
            "success": True,
            "transport_success": True,
            "business_success": True,
            "completed": True,
            "outcome": "partial",
            "status": "partial",
            "ready": True,
            "resolved_report_revision_id": resolved_revision_id,
            "run_id": str(checked.get("run_id") or ""),
            "finance_tables_package_id": str(checked.get("finance_tables_package_id") or ""),
            "basis_hash": str(checked.get("basis_hash") or ""),
            "readiness": checked.get("readiness") or {},
            "validation": checked,
            "resource_uris": list(checked.get("resource_uris") or []),
            "warnings": list(checked.get("warnings") or []),
            "blockers": [],
            "quality_issues": checked_issues,
            "release_limitations": checked_issues,
            "next_actions": list(checked.get("next_actions") or []),
        }
    quality_issues = sorted(
        set(str(item) for item in (checked.get("quality_issues") or []))
    )
    status = "partial" if quality_issues else "ok"
    return {
        "success": True,
        "transport_success": True,
        "business_success": True,
        "completed": True,
        "outcome": status,
        "status": status,
        "ready": True,
        "resolved_report_revision_id": resolved_revision_id,
        "run_id": str(checked.get("run_id") or ""),
        "finance_tables_package_id": str(checked.get("finance_tables_package_id") or ""),
        "basis_hash": str(checked.get("basis_hash") or ""),
        "readiness": checked.get("readiness") or {},
        "validation": checked,
        "resource_uris": list(checked.get("resource_uris") or []),
        "warnings": list(checked.get("warnings") or []),
        "blockers": [],
        "quality_issues": quality_issues,
        "release_limitations": quality_issues,
        "next_actions": list(checked.get("next_actions") or []),
    }

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "BINDING_STORE",
    "CERTIFYING_POLICIES",
    "EVIDENCE_STORE",
    "FormalLineageError",
    "PREPARATION_STORE",
    "RESEARCH_STORE",
    "REVISION_STORE",
    "SIM_A_FORMAL",
    "TABLE_STORE",
    "_TASK_TERMINAL",
    "_build_report_prepare_next_actions",
    "_capture_document_snapshot",
    "_existing_status_revision",
    "_failure",
    "_formal_report_lineage",
    "_materialize_local_document_snapshot",
    "_normalize_finance_binding",
    "_normalize_outline",
    "_resolve_revision_record",
    "_supplied_document_snapshot",
    "combine_evidence_policies",
    "declared_evidence_policy",
    "get_package_record",
    "prepare",
    "project_fact_may_be_certified",
    "readiness",
    "sha256_json",
    "start",
    "status",
    "validate",
    "validate_report_preparation_lineage",
    "validate_report_revision_lineage",
]
