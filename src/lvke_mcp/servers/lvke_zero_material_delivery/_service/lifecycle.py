"""Delivery lifecycle routes, assumption confirmation and resource surface."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Iterable


from lvke_mcp.runtime.quality_severity import split_quality_codes
from lvke_mcp.runtime.storage import (
    paginate_resource_entries,
    require_safe_id,
)

from .acceptance import (
    dimension_rows_from_review,
    empty_acceptance,
    fold_formal,
    fold_internal,
)
from .assumptions import _build_assumption_package
from .explicit_inputs import SOURCE_SENTENCE
from .questions import compute_missing_inputs, summarize_gaps
from .report_profiles import (
    ReportProfileError,
    load_profile_document,
    verified_snapshot,
)
from .technical_acceptance import run_technical_acceptance
from .base import (
    ASSUMPTION_PROFILE_VERSION,
    ASSUMPTION_REGISTER_STORE,
    ASSUMPTION_STORE,
    EVIDENCE_MANIFEST_STORE,
    GAP_REGISTER_STORE,
    INTENT_STORE,
    MANIFEST_STORE,
    REPORT_STORE,
    RUN_STORE,
    SERVICE_NAME,
    SERVICE_VERSION,
    _RESOURCE_STORES,
    _blocked,
    _envelope,
    _idempotent_mutation,
    _view,
)
from .orchestration import execute
from .routing import _new_run, _planned_run_id


def start(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    idempotency_key = str(args.get("idempotency_key") or "")
    request_payload = {"delivery_run_id": run_id}

    def mutation() -> dict[str, Any]:
        run_record = RUN_STORE.get(workspace_id, run_id)
        if run_record is None:
            return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
        run = _view(run_record, "delivery_run_id")
        if run["stage"] == "cancelled":
            return _blocked(
                "delivery_run_cancelled",
                "该运行已取消，须调用 delivery_transition(operation=\"resume\") 创建恢复快照",
            )
        intent_record = INTENT_STORE.get(
            workspace_id,
            str(run["intent_id"]),
        )
        if intent_record is None:
            return _blocked("delivery_intent_not_found", "运行引用的 DeliveryIntent 不存在")
        intent = _view(intent_record, "delivery_intent_id")
        if not dict(intent.get("industry") or {}).get("resolved"):
            return _blocked(
                "missing_route",
                "行业路线未解析，不能生成行业假设",
                status="missing_inputs",
            )
        existing_package_id = str(run.get("assumption_package_id") or "")
        assumption = (
            ASSUMPTION_STORE.get(
                workspace_id,
                existing_package_id,
            )
            if existing_package_id
            else None
        )
        if assumption is None:
            assumption_payload = _build_assumption_package(intent)
            assumption = ASSUMPTION_STORE.put(
                workspace_id,
                assumption_payload,
                producer=f"{SERVICE_NAME}.delivery_start",
                status="ok",
                source_ids=[intent["delivery_intent_id"]],
                basis={
                    "intent_id": intent["delivery_intent_id"],
                    "profile_version": ASSUMPTION_PROFILE_VERSION,
                },
            )
        assumption_view = _view(assumption, "assumption_package_id")
        domain = execute(
            workspace_id,
            intent,
            assumption_view,
            operation_key=idempotency_key,
        )
        from lvke_mcp.servers.lvke_zero_material_delivery.artifact_delivery import (
            build_delivery_artifacts,
        )

        planned_delivery_run_id = _planned_run_id(
            workspace_id,
            run_id,
            idempotency_key,
        )
        delivery_artifacts = build_delivery_artifacts(
            workspace_id,
            intent,
            assumption_view,
            run,
            domain,
            stores={
                "report": REPORT_STORE,
                "assumption_register": ASSUMPTION_REGISTER_STORE,
                "gap_register": GAP_REGISTER_STORE,
                "evidence_manifest": EVIDENCE_MANIFEST_STORE,
                "manifest": MANIFEST_STORE,
            },
            service_version=SERVICE_VERSION,
            delivery_run_id=planned_delivery_run_id,
        )
        object_refs = {
            "assumption_package_id": assumption["object_id"],
            **{
                str(key): str(value)
                for key, value in dict(domain.get("object_refs") or {}).items()
                if value
            },
            **dict(delivery_artifacts.get("object_refs") or {}),
        }
        artifact_uris = sorted(
            {
                *list(domain.get("resource_uris") or []),
                *list(delivery_artifacts.get("resource_uris") or []),
            }
        )
        # 配置必填字段的缺口按所选配置重算：确认过的字段会从 pending 消失，
        # 用户显式跳过的保留为 skipped 并进入限制项。
        profile_selection = dict(delivery_artifacts.get("report_profile") or {})
        field_gaps: list[dict[str, Any]] = []
        gap_summary = {"release_limitations": [], "critical_unanswered_fields": []}
        # 先解析出配置文档，**再**统一算缺口。
        #
        # 缺口计算曾放在 elif 分支内部：而新运行都带 profile_snapshot，于是那段
        # 代码永远不执行，未回答的关键字段被清空、不产生
        # required_field_unanswered:*，正式资格门禁整体失效。
        # 两个分支只负责"拿到哪份配置"，判据必须在分支**之后**执行一次。
        profile_document: dict[str, Any] = {}
        # 用随运行冻结的快照算缺口：配置升级后磁盘那份可能已换了 required_fields，
        # 据此追问会问出本运行用不到的字段。采信走 verified_snapshot 复算 hash。
        verified = verified_snapshot(profile_selection)
        if verified is not None:
            profile_document = verified
        elif profile_selection.get("profile_id"):
            try:
                profile_document = load_profile_document(
                    f"{profile_selection['profile_id']}.v1.json"
                )
            except ReportProfileError:
                profile_document = {}
        if profile_document:
            field_gaps = compute_missing_inputs(
                profile=profile_document,
                intent=intent,
                assumption_package=assumption_view,
                skipped=list(run.get("skipped_fields") or []),
            )
            gap_summary = summarize_gaps(field_gaps)
        blocking_codes, quality_issues = split_quality_codes([
            *(domain.get("blockers") or []),
            *(domain.get("quality_issues") or []),
            *(delivery_artifacts.get("blockers") or []),
            *(delivery_artifacts.get("quality_issues") or []),
            *gap_summary["release_limitations"],
            # 跳过史里"已补答"的条目也进限制清单：配置缺口重算只看当前状态，
            # 追不到那些已被回答、因此从 missing_inputs 消失的跳过决策。
            *_skip_limitations(
                list(run.get("skipped_fields") or []),
                list(run.get("skip_history") or []),
            ),
        ])
        technical = run_technical_acceptance(
            workspace_id,
            intent=intent,
            domain=domain,
            delivery_artifacts=delivery_artifacts,
            finance_summary=dict(delivery_artifacts.get("finance_summary") or {}),
            extra_blockers=blocking_codes,
            extra_limitations=quality_issues,
        )
        acceptance = {
            "technical": technical,
            "internal": fold_internal(
                technical_status=str(technical.get("status") or "not_started"),
                dimension_results=[],
                inherited_limitations=list(technical.get("limitations") or []),
            ),
            "formal": fold_formal(
                technical=technical,
                internal={"status": "not_started"},
            ),
        }
        # 技术验收自己发现的阻断项必须并回信封判据。
        #
        # 此前 blocking_codes 只含验收**前**算出的码，于是 review_start 失败、
        # 组件缺失、hash/谱系断裂这些只写进 acceptance——顶层照样报
        # success=True / completed=True / technical_preview_ready=True，
        # 而 acceptance.technical.status=failed。同一个响应给出两个矛盾的答案，
        # 且调用方最可能读的是顶层那个。
        blocking_codes, quality_issues = split_quality_codes([
            *blocking_codes,
            *quality_issues,
            *(str(item) for item in technical.get("blockers") or []),
            *(str(item) for item in technical.get("limitations") or []),
        ])
        # 就绪判据放在验收之后：验收有阻断项就不是可交付的技术预览。
        technical_preview_ready = (
            str(domain.get("stage") or "") == "tables_ready"
            and not delivery_artifacts.get("blockers")
            and not blocking_codes
            and str(technical.get("status") or "")
            in {"passed", "passed_with_limitations"}
        )
        next_run = _new_run(
            workspace_id,
            intent_id=intent["delivery_intent_id"],
            assumption_package_id=assumption["object_id"],
            previous_run_id=run_id,
            stage="preview_ready" if technical_preview_ready else str(domain.get("stage") or "assumptions_ready"),
            blockers=blocking_codes,
            status_reason=str(domain.get("status") or "partial"),
            object_refs=object_refs,
            artifact_uris=artifact_uris,
            manifest_uri=str(delivery_artifacts.get("manifest_uri") or ""),
            domain_results={
                "research_status": str((domain.get("research") or {}).get("status") or ""),
                "finance_status": str((domain.get("finance_run") or {}).get("status") or ""),
                "tables_status": str((domain.get("tables") or {}).get("status") or ""),
                "csv_status": str((domain.get("csv_export") or {}).get("status") or ""),
                "xlsx_status": str((domain.get("xlsx_export") or {}).get("status") or ""),
                "report_preparation_status": str((domain.get("report_preparation") or {}).get("status") or ""),
                "technical_preview_ready": technical_preview_ready,
            },
            object_id=planned_delivery_run_id,
            report_profile=profile_selection,
            missing_inputs=field_gaps,
            skipped_fields=list(run.get("skipped_fields") or []),
            # 跳过史随每个新版本继承。重算不是"重新开始"：漏传就等于每次
            # delivery_start 都把跳过历史清一次，Z3 那条审计链在这里断掉。
            skip_history=list(run.get("skip_history") or []),
            acceptance=acceptance,
            release_limitations=quality_issues,
        )
        return _envelope(
            not blocking_codes,
            "blocked" if blocking_codes else ("partial" if quality_issues else "ok"),
            warnings=[
                "行业场景仅作为受控假设种子，不是项目证据",
                *list(domain.get("warnings") or []),
                *(f"阻断项：{item}" for item in blocking_codes),
                *(
                    f"质量提示：{item}"
                    for item in quality_issues
                    if item not in set(blocking_codes)
                ),
            ],
            blockers=blocking_codes,
            quality_issues=quality_issues,
            release_limitations=quality_issues,
            completed=not blocking_codes,
            technical_preview_ready=technical_preview_ready,
            next_actions=(
                ["按 quality_issues 补充资料或修复工件后重算；当前运行快照仍可读取"]
                if quality_issues
                else ["读取交付 Resources；正式发布资格仍保持质量受限"]
            ),
            resource_uris=sorted(
                {
                    assumption["resource_uri"],
                    next_run["resource_uri"],
                    *artifact_uris,
                }
            ),
            assumption_package=assumption_view,
            delivery_run=_view(next_run, "delivery_run_id"),
            domain_status=str(domain.get("status") or ""),
            report_profile=profile_selection,
            missing_inputs=field_gaps,
            gap_summary=gap_summary,
            # 跳过状态在顶层与 delivery_run 里都给：调用方最可能只读顶层，
            # 只放进不可变记录等于把"这些数字无人确认"藏进一层嵌套。
            skipped_fields=[dict(item) for item in run.get("skipped_fields") or []],
            skip_history=[dict(item) for item in run.get("skip_history") or []],
            acceptance=acceptance,
            validation_complete=False,
            input_evidence_complete=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="delivery_start",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )


def get_delivery(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    object_id = require_safe_id(args.get("object_id"), "object_id")
    for store, object_type, id_field in _RESOURCE_STORES:
        record = store.get(workspace_id, object_id)
        if record is not None:
            view = _view(record, id_field)
            return _envelope(
                True,
                "ok",
                resource_uris=[record["resource_uri"]],
                object_type=object_type,
                object=view,
                validation_complete=False,
                input_evidence_complete=False,
            )
    return _blocked("delivery_object_not_found", "未找到指定交付对象")


def _artifact_kind(uri: str) -> str:
    """Classify an artifact URI so per-artifact usability can be reported."""

    lowered = uri.lower()
    if lowered.endswith("/xlsx") or "xlsx" in lowered:
        return "xlsx"
    if "/csv" in lowered:
        return "csv"
    if "docx" in lowered or "report-artifact" in lowered:
        return "docx"
    if "/packages/" in lowered or "finance-tables" in lowered:
        return "finance_tables_package"
    # 按对象集合名精确分类，不按域名粗判：finance-model 域下同时有 specs、
    # runs、fact-packs，都归成 finance_run 会让 validation_status 张冠李戴。
    if "/runs/" in lowered:
        return "finance_run"
    if "/specs/" in lowered:
        return "finance_spec"
    if "/report-revisions/" in lowered or "/report-preparations/" in lowered:
        return "report_revision"
    if "/project-contexts/" in lowered:
        return "project_context"
    if "/research" in lowered:
        return "research_package"
    return "object"


def _artifact_states(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Annotate every artifact URI with usability, not just list it.

    此前 delivery_status 顶层返回 ok/blockers=[]，同时把 DOCX、XLSX 的 URI 一并
    列出，而内部 domain_results 其实是 artifact_failed——调用方据此以为工件可交付。
    每个工件必须自带 usable / validation_status / release_grade。
    """

    domain_results = dict(run.get("domain_results") or {})
    blockers = [str(item) for item in (run.get("blockers") or [])]
    status_by_kind = {
        "csv": str(domain_results.get("csv_status") or ""),
        "xlsx": str(domain_results.get("xlsx_status") or ""),
        "finance_tables_package": str(domain_results.get("tables_status") or ""),
        "finance_run": str(domain_results.get("finance_status") or ""),
        "docx": str(domain_results.get("report_preparation_status") or ""),
        "report_revision": str(domain_results.get("report_preparation_status") or ""),
        "research_package": str(domain_results.get("research_status") or ""),
    }
    # 中间对象（spec、project context）不是交付物：把它们标成 deliverable_failed
    # 会掩盖真正失败的 DOCX/XLSX。它们只标"非交付物"，既不假称可用也不算失败。
    intermediate_kinds = {"finance_spec", "project_context", "object"}
    passing = {"ok", "completed", "ready", "succeeded"}
    states: list[dict[str, Any]] = []
    for uri in [str(item) for item in (run.get("artifact_uris") or [])]:
        kind = _artifact_kind(uri)
        if kind in intermediate_kinds:
            states.append(
                {
                    "uri": uri,
                    "artifact_kind": kind,
                    "usable": False,
                    "validation_status": "not_a_deliverable",
                    "release_grade": "unavailable",
                    "blocking_reasons": [],
                    "is_deliverable": False,
                }
            )
            continue
        validation_status = status_by_kind.get(kind, "") or "unknown"
        usable = validation_status in passing
        states.append(
            {
                "uri": uri,
                "artifact_kind": kind,
                "usable": usable,
                "validation_status": validation_status,
                # 零材料链恒为预览级：即便某个工件自身校验通过，也不取得正式发布资格。
                "release_grade": "technical_preview" if usable else "unavailable",
                "blocking_reasons": (
                    []
                    if usable
                    else [item for item in blockers if kind in item or item.endswith("_failed")]
                ),
                "is_deliverable": True,
            }
        )
    return states


_ENVELOPE_STATUS_BY_DELIVERY_STATE = {
    "ready": "ok",
    "partial": "partial",
    "blocked": "blocked",
    "cancelled": "blocked",
    "in_progress": "partial",
}
# 顶层 domain_status 按要求严格三态，便于调用方一眼判断能不能用。
_DOMAIN_STATUS_BY_DELIVERY_STATE = {
    "ready": "ready",
    "partial": "partial",
    "in_progress": "partial",
    "blocked": "blocked",
    "cancelled": "blocked",
}


def _domain_status(delivery_state: str) -> str:
    return _DOMAIN_STATUS_BY_DELIVERY_STATE.get(delivery_state, "partial")


def _acceptance_blockers(run: dict[str, Any]) -> list[str]:
    """Collect technical-acceptance blockers, which run['blockers'] does not carry.

    ``run["blockers"]`` 只含**验收之前**算出的 blocking_codes：技术验收自己发现的
    组件缺失、manifest/hash 缺失、谱系断裂、审查未跑起来都只写进 ``acceptance``。
    状态折叠若只看 run blockers，就会出现"工件可读 + acceptance.blocked"仍报
    ``delivery_state=ready`` —— 正是本服务反复要避免的那类不诚实。
    """

    technical = dict(dict(run.get("acceptance") or {}).get("technical") or {})
    codes = [str(item) for item in technical.get("blockers") or [] if str(item)]
    if str(technical.get("status") or "") in {"failed", "blocked"}:
        codes.append(f"technical_acceptance_{technical['status']}")
    return sorted(set(codes))


def _delivery_state(run: dict[str, Any], artifact_states: list[dict[str, Any]]) -> str:
    """Fold stage / blockers / acceptance / artifact usability into one state."""

    stage = str(run.get("stage") or "")
    # 技术验收的阻断项与 run blockers 同权：两者都表示"这份交付不可按可交付物引用"。
    blockers = [
        *(str(item) for item in (run.get("blockers") or [])),
        *_acceptance_blockers(run),
    ]
    if stage == "cancelled":
        return "cancelled"
    if stage == "failed":
        return "blocked"
    deliverables = [item for item in artifact_states if item.get("is_deliverable")]
    unusable = [item for item in deliverables if not item["usable"]]
    if blockers and not deliverables:
        return "blocked"
    if blockers or unusable:
        return "partial"
    if deliverables:
        return "ready"
    return "in_progress"


def status(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    record = RUN_STORE.get(workspace_id, run_id)
    if record is None:
        return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
    stored = _view(record, "delivery_run_id")
    # 刷新出的实时验收状态只用于**折叠状态**与顶层字段，绝不写回 delivery_run。
    #
    # ``_view`` 返回的是不可变记录视图，自带 content_hash / basis_hash。把刷新后的
    # acceptance 覆盖进去，视图内容就与它宣称的 hash 不再一致——调用方按那个 hash
    # 复算会失败，而不可变对象最基本的承诺就是"内容与 hash 相符"。
    #
    # 因此：``delivery_run`` 保持落库原样（含原 hash，可复算），实时验收只在顶层
    # ``acceptance`` 暴露；``run`` 仅作为本函数内部的折叠输入，不进响应。
    acceptance = _refresh_acceptance(workspace_id, stored)
    run = {**stored, "acceptance": acceptance}
    artifact_states = _artifact_states(run)
    delivery_state = _delivery_state(run, artifact_states)
    domain_results = dict(run.get("domain_results") or {})
    run_blockers = sorted(
        {
            *(str(item) for item in (run.get("blockers") or [])),
            *_acceptance_blockers(run),
        }
    )
    # status 走信封通用枚举（表达"这次查询本身"的结果），delivery_state /
    # domain_status 表达交付真实状态。绝不用 status=ok 暗示交付可用。
    envelope_status = _ENVELOPE_STATUS_BY_DELIVERY_STATE.get(delivery_state, "partial")
    # 技术验收阻断时不得声称预览就绪：那是"顶层报就绪、内部有阻断项"的老毛病。
    technical_preview_ready = bool(
        domain_results.get("technical_preview_ready", False)
    ) and not _acceptance_blockers(run)
    deliverables = [item for item in artifact_states if item.get("is_deliverable")]
    unusable = [item["uri"] for item in deliverables if not item["usable"]]
    warnings: list[str] = []
    if unusable:
        warnings.append(
            f"{len(unusable)} 个交付工件当前不可用，禁止按可交付物对外引用："
            + "、".join(unusable[:3])
        )
    if not deliverables and artifact_states:
        warnings.append(
            "当前只有中间对象（spec/context），尚未产出十三表、XLSX、CSV 或 DOCX 交付工件"
        )
    if not technical_preview_ready and artifact_states:
        warnings.append(
            "technical_preview_ready=false：工件仅为中间产物或技术验收存在阻断项，"
            "不构成可交付的技术预览"
        )
    skipped_now = [
        dict(item) for item in run.get("skipped_fields") or [] if isinstance(item, dict)
    ]
    if skipped_now:
        warnings.append(
            f"{len(skipped_now)} 个字段由用户显式跳过、未经确认："
            + "、".join(str(item.get("field") or "") for item in skipped_now[:5])
            + "；已按受控假设取值并计入交付限制"
        )
    internal_status = str(dict(acceptance.get("internal") or {}).get("status") or "")
    formal_status = str(dict(acceptance.get("formal") or {}).get("status") or "")
    if internal_status in {"not_started", "pending"}:
        warnings.append(
            "内部分领域验收未完成：七域责任确认齐全前不得取得正式候选资格"
        )
    if formal_status != "eligible" and formal_status != "promoted":
        warnings.append(f"正式资格 {formal_status}：不得按正式件对外交付")
    return _envelope(
        # query_success 表达「本次查询成功」，与交付状态严格分离：
        # 查得到 run 就是 True，绝不因此暗示交付可用。
        True,
        envelope_status,
        resource_uris=[record["resource_uri"]],
        query_success=True,
        delivery_state=delivery_state,
        domain_status=_domain_status(delivery_state),
        # 返回落库原样：内容与它自带的 content_hash 相符，可被复算校验。
        # 实时验收状态看顶层 acceptance（见上方注释）。
        delivery_run=stored,
        stage=run["stage"],
        progress=_stage_progress(str(run["stage"])),
        resume_token=record["content_hash"],
        # 明确指出该读哪一个：delivery_run.acceptance 是落库时的快照（与其
        # content_hash 相符、可复算），顶层 acceptance 才是实时状态。
        # 两者不同不是矛盾，是"不可变记录"与"当前状态"的正常分工。
        acceptance_source="top_level_acceptance_is_current",
        artifacts=artifact_states,
        deliverable_artifact_count=len(deliverables),
        usable_artifact_count=len(deliverables) - len(unusable),
        unusable_artifact_uris=unusable,
        technical_preview_ready=technical_preview_ready,
        domain_results=domain_results,
        acceptance=acceptance,
        report_profile=dict(run.get("report_profile") or {}),
        missing_inputs=[dict(item) for item in run.get("missing_inputs") or []],
        skipped_fields=[dict(item) for item in run.get("skipped_fields") or []],
        # 跳过史独立暴露：``skipped_fields`` 只答"现在还缺谁的确认"，
        # 审计要问的是"这个字段的确认过程有没有变过"。
        skip_history=[dict(item) for item in run.get("skip_history") or []],
        release_limitations=sorted(
            {
                *(str(item) for item in run.get("release_limitations") or []),
                *_skip_limitations(
                    list(run.get("skipped_fields") or []),
                    list(run.get("skip_history") or []),
                ),
            }
        ),
        warnings=warnings,
        blockers=run_blockers,
        next_actions=(
            ["按 blockers 与逐工件 blocking_reasons 修复后重算；不要把不可用工件当交付物"]
            if delivery_state != "ready"
            else [
                "读取工件 Resource；正式发布资格仍保持阻断",
                *(
                    ["七域责任人调用 review_submit_assessment 与 review_confirm_dimension"]
                    if internal_status in {"not_started", "pending"}
                    else []
                ),
            ]
        ),
        validation_complete=False,
        input_evidence_complete=False,
    )


def _configured_required_fields(
    workspace_id: str,
    package_id: str,
) -> set[str]:
    """Return required_fields of the profile frozen on this package's run.

    跳过项的合法集合 = 假设包字段 ∪ 所选配置的必填字段。后者不可省：配置声明的
    必填字段（如轨道的 route_length_km）在假设包里未必有对应条目，但它确实是
    追问集合的一部分，用户理当能跳过它。

    找不到运行或配置时返回空集合——由调用方与假设包字段求并，因此退化为
    "只允许跳过假设包里的字段"，是安全侧。
    """

    for record in reversed(RUN_STORE.list(workspace_id)):
        payload = dict(record.get("payload") or {})
        selection = dict(payload.get("report_profile") or {})
        if not selection:
            continue
        if package_id and str(payload.get("assumption_package_id") or "") != package_id:
            continue
        document = verified_snapshot(selection)
        if document is None and selection.get("profile_id"):
            try:
                document = load_profile_document(
                    f"{selection['profile_id']}.v1.json"
                )
            except ReportProfileError:
                document = None
        if document:
            return {str(item) for item in document.get("required_fields") or []}
    return set()


def _refresh_acceptance(workspace_id: str, run: dict[str, Any]) -> dict[str, Any]:
    """Re-read internal per-domain confirmations from the review domain.

    内部验收状态**不缓存**：责任人可能在 delivery_start 之后才逐个确认领域。
    每次读状态都回 review 域取最新 dimension 结果，否则 status 会一直显示
    "pending" 而实际七域已确认齐全。

    本函数只 *读* review 的确认记录，从不代为提交——那是"把系统自动检查伪装成
    人工签章"，也是这条链最不能越的线。
    """

    acceptance = dict(run.get("acceptance") or empty_acceptance())
    technical = dict(acceptance.get("technical") or {})
    review_id = str(technical.get("review_id") or "")
    if not review_id:
        return acceptance
    from lvke_mcp.runtime import service_gateway
    from .acceptance import REQUIRED_DIMENSIONS

    # 逐维度读，**不**调用 review_finalize：finalize 是写操作（落 7 条
    # ReviewDimensionResult + 1 个 ReviewDossier），而这里是 delivery_status /
    # delivery_get_artifacts 的只读路径。让读状态顺手写对象会在每次查询时产生新
    # 的不可变记录，并把"谁触发了 finalize"变得不可追溯。
    # review_get_dimension 是纯读接口，正是这里需要的。
    dimension_results: list[dict[str, Any]] = []
    confirmations: dict[str, dict[str, Any]] = {}
    for dimension in REQUIRED_DIMENSIONS:
        try:
            detail = service_gateway.review_get_dimension(
                {
                    "workspace_id": workspace_id,
                    "review_id": review_id,
                    "dimension": dimension,
                }
            )
        except (ValueError, RuntimeError, OSError):
            continue
        result = dict(detail.get("dimension_result") or {})
        if not result:
            continue
        dimension_results.append(result)
        confirmations[dimension] = result
    if not dimension_results:
        return acceptance
    internal = fold_internal(
        technical_status=str(technical.get("status") or "not_started"),
        dimension_results=dimension_rows_from_review(dimension_results, confirmations),
        review_id=review_id,
        inherited_limitations=list(technical.get("limitations") or []),
    )
    formal = fold_formal(
        technical=technical,
        internal=internal,
        promotion_id=str(dict(acceptance.get("formal") or {}).get("promotion_id") or ""),
    )
    return {"technical": technical, "internal": internal, "formal": formal}


def _stage_progress(stage: str) -> int:
    stages = [
        "received", "intent_resolved", "researching", "assumptions_ready",
        "planning_ready", "finance_ready", "tables_ready", "report_ready",
        "preview_ready", "awaiting_confirmation", "confirmed_estimate_ready",
    ]
    if stage == "cancelled":
        return 0
    try:
        return round(stages.index(stage) * 100 / (len(stages) - 1))
    except ValueError:
        return 0


def list_assumptions(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    package_id = require_safe_id(args.get("assumption_package_id"), "assumption_package_id")
    record = ASSUMPTION_STORE.get(workspace_id, package_id)
    if record is None:
        return _blocked("assumption_package_not_found", "未找到指定 AssumptionPackage")
    package = _view(record, "assumption_package_id")
    fields = sorted(
        [dict(item) for item in package.get("fields") or []],
        key=lambda item: (
            -int(item.get("confirmation_priority_score") or 0),
            str(item.get("name")),
        ),
    )
    limit = max(5, min(int(args.get("limit") or 10), 10))
    # 句中已写明的参数不再要求用户确认一遍：它们的来源已高于行业种子，
    # 混进 confirmation_items 只会挤掉真正需要确认的种子字段。
    pending = [
        item
        for item in fields
        if not item.get("confirmed") and item.get("source_ref") != SOURCE_SENTENCE
    ]
    return _envelope(
        True,
        "ok",
        resource_uris=[record["resource_uri"]],
        assumption_package_id=package_id,
        assumptions=fields,
        confirmation_items=pending[:limit],
        explicit_input_fields=[
            str(item.get("name"))
            for item in fields
            if item.get("source_ref") == SOURCE_SENTENCE
        ],
        validation_complete=False,
        input_evidence_complete=False,
    )


def _skip_limitations(
    # Iterable 而非 list：调用点有传 ``dict.values()`` 的，不值得为此多一次拷贝。
    skipped_fields: Iterable[dict[str, Any]] | None,
    skip_history: Iterable[dict[str, Any]] | None = None,
) -> list[str]:
    """Turn current skips and resolved skip history into disclosure codes.

    两类码分开：``required_field_skipped:*`` 是"此刻仍未确认"，
    ``required_field_skip_resolved:*`` 是"曾跳过、已补答"。后者不是缺陷，但它是
    决策变更的凭据——只留前者会让"跳过又补答"这段历史在限制清单里彻底消失，
    而审计正是靠限制清单发现"这个数字的确认过程不寻常"。
    """

    current = {
        str(item.get("field") or "")
        for item in skipped_fields or []
        if isinstance(item, dict) and str(item.get("field") or "")
    }
    resolved = {
        str(item.get("field") or "")
        for item in skip_history or []
        if isinstance(item, dict)
        and str(item.get("field") or "")
        and str(item.get("resolution") or "") == "answered"
        and str(item.get("field") or "") not in current
    }
    return sorted(
        {f"required_field_skipped:{name}" for name in current}
        | {f"required_field_skip_resolved:{name}" for name in resolved}
    )


def confirm_assumptions(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    package_id = require_safe_id(args.get("assumption_package_id"), "assumption_package_id")
    idempotency_key = str(args.get("idempotency_key") or "")
    confirmations = [dict(item) for item in args.get("confirmations") or []]
    # 显式跳过：用户可以只回答关键项。跳过登记进 DeliveryRun 并进入报告披露、
    # manifest 与验收限制，但不因此获得正式资格。
    skipped = [
        {
            "field": str(item.get("field") or ""),
            "reason": str(item.get("reason") or "user_skipped"),
        }
        for item in args.get("skip_fields") or []
        if isinstance(item, dict) and str(item.get("field") or "")
    ]
    request_payload = {
        "assumption_package_id": package_id,
        "confirmations": confirmations,
        "skip_fields": skipped,
    }

    def mutation() -> dict[str, Any]:
        prior = ASSUMPTION_STORE.get(workspace_id, package_id)
        if prior is None:
            return _blocked("assumption_package_not_found", "未找到指定 AssumptionPackage")
        prior_payload = dict(prior.get("payload") or {})
        known = {str(item.get("name")): dict(item) for item in prior_payload.get("fields") or []}
        unknown = sorted({str(item.get("name") or "") for item in confirmations} - set(known))
        if unknown:
            return _blocked(
                "unknown_assumption_field",
                "确认请求包含未知假设字段",
                unknown_fields=unknown,
            )
        # 跳过项也必须是真实存在的字段：假设包里的字段，或所选配置的必填字段。
        #
        # 此前只校验 confirmations，skip_fields 原样写进 lineage —— 于是
        # "totally_unknown" 这类拼错或凭空的名字会进入披露与审计记录，
        # 制造不属于追问集合的跳过项。审计记录不能包含无从核对的条目。
        skippable = set(known) | _configured_required_fields(workspace_id, package_id)
        unknown_skips = sorted({item["field"] for item in skipped} - skippable)
        if unknown_skips:
            return _blocked(
                "unknown_skip_field",
                "跳过请求包含未知字段：既不在假设包中，也不是所选配置的必填字段",
                unknown_fields=unknown_skips,
                next_actions=[
                    "用 delivery_status 读取 missing_inputs 后按其中的 field 名跳过",
                ],
            )
        for confirmation in confirmations:
            name = str(confirmation["name"])
            current = known[name]
            current.update(
                {
                    "value": confirmation["value"],
                    "source_type": "user_confirmed",
                    "source_ref": str(confirmation.get("source_ref") or "user_confirmation"),
                    "method": "user_override",
                    "confidence": 1.0,
                    "confirmed": True,
                    "confirmation_note": str(confirmation.get("note") or ""),
                    "validation_condition": "已确认参数仍需与后续原始材料进行 hash 和数值一致性校验",
                }
            )
        payload = {
            **prior_payload,
            "revision": int(prior_payload.get("revision") or 1) + 1,
            "previous_assumption_package_id": package_id,
            "fields": [known[str(item.get("name"))] for item in prior_payload.get("fields") or []],
            "confirmation_status": "partially_confirmed" if any(
                not item.get("confirmed") and item.get("source_ref") != SOURCE_SENTENCE
                for item in known.values()
            ) else "confirmed",
            "validation_complete": False,
            "input_evidence_complete": False,
        }
        revised = ASSUMPTION_STORE.put(
            workspace_id,
            payload,
            producer=f"{SERVICE_NAME}.delivery_confirm_assumptions",
            status="ok",
            source_ids=[package_id],
            basis=request_payload,
        )
        source_run = next(
            (
                record for record in reversed(RUN_STORE.list(workspace_id))
                if str((record.get("payload") or {}).get("assumption_package_id") or "") == package_id
            ),
            None,
        )
        intent_id = str((source_run.get("payload") or {}).get("intent_id") or "") if source_run else ""
        if not intent_id:
            return _blocked("delivery_run_lineage_missing", "假设包缺少 DeliveryRun lineage")
        prior_run_payload = dict(source_run.get("payload") or {})
        merged_skips = {
            str(item.get("field")): dict(item)
            for item in prior_run_payload.get("skipped_fields") or []
            if isinstance(item, dict) and str(item.get("field") or "")
        }
        # 跳过史继承自前版并只增不减：``skipped_fields`` 表示"当前仍跳过"，
        # 回答后会从那里移除，而"曾经选择跳过"这个决策必须留痕。
        skip_history = {
            str(item.get("field")): dict(item)
            for item in prior_run_payload.get("skip_history") or []
            if isinstance(item, dict) and str(item.get("field") or "")
        }
        # 已回答的字段从跳过清单移除：回答优先于此前的跳过登记。
        answered = {str(item.get("name") or "") for item in confirmations}
        for item in skipped:
            merged_skips[item["field"]] = item
            # 首次登记时记下"在哪个 run / 哪个假设包版本上跳过的"，
            # 后续版本只改 resolution，不覆盖首次登记事实。
            skip_history.setdefault(
                item["field"],
                {
                    **item,
                    "skipped_in_run_id": str(source_run["object_id"]),
                    "skipped_from_assumption_package_id": package_id,
                    "resolution": "skipped",
                },
            )
        for name in answered:
            merged_skips.pop(name, None)
            historical = skip_history.get(name)
            if historical is not None:
                # 不删除历史条目：把它标成"已回答"，并指名是哪个版本回答的。
                # 删除会让 rev3 看起来"从来没人跳过这个字段"。
                historical["resolution"] = "answered"
                historical["answered_in_assumption_package_id"] = revised["object_id"]
        skip_history_rows = sorted(skip_history.values(), key=lambda item: item["field"])
        next_run = _new_run(
            workspace_id,
            intent_id=intent_id,
            assumption_package_id=revised["object_id"],
            previous_run_id=str(source_run["object_id"]),
            stage="assumptions_ready",
            blockers=["recalculation_required"],
            status_reason="confirmed_inputs_require_new_domain_objects",
            object_refs={"assumption_package_id": revised["object_id"]},
            report_profile=dict(prior_run_payload.get("report_profile") or {}),
            missing_inputs=[
                dict(item) for item in prior_run_payload.get("missing_inputs") or []
            ],
            skipped_fields=sorted(merged_skips.values(), key=lambda item: item["field"]),
            skip_history=skip_history_rows,
            release_limitations=_skip_limitations(
                merged_skips.values(), skip_history_rows
            ),
        )
        return _envelope(
            True,
            "accepted",
            warnings=[
                "用户确认值仍不是合同、测绘、报价或权属证据",
                *(
                    [
                        f"{len(merged_skips)} 个字段仍处于用户跳过状态，"
                        "将按受控假设取值并在交付物中披露"
                    ]
                    if merged_skips
                    else []
                ),
            ],
            blockers=["recalculation_required"],
            next_actions=["使用新的 delivery_run_id 重算财务、十三表和报告"],
            resource_uris=[revised["resource_uri"], next_run["resource_uri"]],
            assumption_package=_view(revised, "assumption_package_id"),
            delivery_run=_view(next_run, "delivery_run_id"),
            skipped_fields=sorted(merged_skips.values(), key=lambda item: item["field"]),
            skip_history=skip_history_rows,
            release_limitations=_skip_limitations(
                merged_skips.values(), skip_history_rows
            ),
            validation_complete=False,
            input_evidence_complete=False,
        )

    confirmation = _idempotent_mutation(
        workspace_id,
        operation="delivery_confirm_assumptions",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )
    if not confirmation.get("success"):
        return confirmation
    recalculation_run = dict(confirmation.get("delivery_run") or {})
    recalculation_run_id = str(recalculation_run.get("delivery_run_id") or "")
    if not recalculation_run_id:
        return _blocked(
            "automatic_recalculation_lineage_missing",
            "确认已保存，但未形成可自动重算的 DeliveryRun",
            assumption_package=confirmation.get("assumption_package"),
        )
    recalculation_key = "zmd-auto-recalc-" + hashlib.sha256(
        f"{idempotency_key}:{recalculation_run_id}".encode("utf-8")
    ).hexdigest()[:32]
    recalculated = start(
        {
            "workspace_id": workspace_id,
            "delivery_run_id": recalculation_run_id,
            "idempotency_key": recalculation_key,
        }
    )
    return {
        **recalculated,
        "assumption_package": confirmation.get("assumption_package"),
        "confirmation_run": recalculation_run,
        "automatic_recalculation": True,
        "confirmation_idempotent_replay": bool(confirmation.get("idempotent_replay")),
        # 跳过状态取**确认动作**这一侧：重算被阻断时 recalculated 可能根本没算到
        # 缺口那一步，而"这次调用跳过了哪些字段"是确认动作自己的事实，不该因为
        # 下游重算失败而从响应里消失。
        "skipped_fields": confirmation.get("skipped_fields") or [],
        "skip_history": confirmation.get("skip_history") or [],
        "release_limitations": sorted(
            {
                *(str(item) for item in recalculated.get("release_limitations") or []),
                *(str(item) for item in confirmation.get("release_limitations") or []),
            }
        ),
    }


def cancel(args: dict[str, Any]) -> dict[str, Any]:
    return _transition_control(args, operation="cancel")


def resume(args: dict[str, Any]) -> dict[str, Any]:
    return _transition_control(args, operation="resume")


def _transition_control(args: dict[str, Any], *, operation: str) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    idempotency_key = str(args.get("idempotency_key") or "")
    reason = str(args.get("reason") or "").strip()
    request_payload = {"delivery_run_id": run_id, "reason": reason}

    def mutation() -> dict[str, Any]:
        prior = RUN_STORE.get(workspace_id, run_id)
        if prior is None:
            return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
        payload = dict(prior.get("payload") or {})
        stage = str(payload.get("stage") or "")
        if operation == "cancel" and stage == "cancelled":
            return _blocked("delivery_run_already_cancelled", "该运行已经取消")
        if operation == "resume" and stage != "cancelled":
            return _blocked("delivery_run_not_cancelled", "只有 cancelled 运行可以恢复")
        next_stage = "cancelled" if operation == "cancel" else str(payload.get("resume_stage") or "received")
        # cancel/resume 是**状态迁移**，不是重新开始：所选报告配置、字段缺口、
        # 跳过登记与跳过史都必须随快照传下去。
        #
        # 此前这四项一个都没传，于是新快照的 report_profile 是 {}——与
        # ``routing._new_run`` 里"所选报告配置随运行冻结：历史运行因此可重放"
        # 的承诺直接矛盾：恢复出来的运行不知道自己该用哪份配置，
        # ``promotion.generate_template_pack`` 读到空配置便认为"未冻结"，
        # 于是允许用另一份配置覆盖——验收对象与晋升对象因此可以不是同一份配置。
        next_run = _new_run(
            workspace_id,
            intent_id=str(payload.get("intent_id") or ""),
            assumption_package_id=str(payload.get("assumption_package_id") or ""),
            previous_run_id=run_id,
            stage=next_stage,
            blockers=[] if operation == "resume" else ["cancelled"],
            resume_stage=stage if operation == "cancel" else "",
            status_reason=reason or operation,
            object_refs=dict(payload.get("object_refs") or {}),
            report_profile=dict(payload.get("report_profile") or {}),
            missing_inputs=[
                dict(item)
                for item in payload.get("missing_inputs") or []
                if isinstance(item, dict)
            ],
            skipped_fields=[
                dict(item)
                for item in payload.get("skipped_fields") or []
                if isinstance(item, dict)
            ],
            skip_history=[
                dict(item)
                for item in payload.get("skip_history") or []
                if isinstance(item, dict)
            ],
            # 限制项同样继承：取消再恢复不得洗掉已披露的限制。
            release_limitations=[
                str(item) for item in payload.get("release_limitations") or []
            ],
        )
        return _envelope(
            True,
            "accepted",
            blockers=[] if operation == "resume" else ["cancelled"],
            next_actions=["调用 delivery_start 继续运行"] if operation == "resume" else [],
            resource_uris=[next_run["resource_uri"]],
            delivery_run=_view(next_run, "delivery_run_id"),
            # 顶层回显继承下来的配置与跳过状态：调用方一眼可见迁移没有洗掉冻结项。
            report_profile=dict(payload.get("report_profile") or {}),
            skipped_fields=[
                dict(item)
                for item in payload.get("skipped_fields") or []
                if isinstance(item, dict)
            ],
            skip_history=[
                dict(item)
                for item in payload.get("skip_history") or []
                if isinstance(item, dict)
            ],
            validation_complete=False,
            input_evidence_complete=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation=f"delivery_{operation}",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )


def get_artifacts(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    record = RUN_STORE.get(workspace_id, run_id)
    if record is None:
        return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
    stored = _view(record, "delivery_run_id")
    # 与 status 同一处理：先刷新验收，状态折叠与返回的 run 视图都用刷新后的值。
    acceptance = _refresh_acceptance(workspace_id, stored)
    run = {**stored, "acceptance": acceptance}
    refs = dict(run.get("object_refs") or {})
    uris = [record["resource_uri"]]
    for ref in refs.values():
        for store, _object_type, _id_field in _RESOURCE_STORES:
            linked = store.get(workspace_id, str(ref))
            if linked is not None:
                uris.append(str(linked["resource_uri"]))
                break
    artifact_uris = [str(item) for item in run.get("artifact_uris") or []]
    artifact_states = _artifact_states(run)
    delivery_state = _delivery_state(run, artifact_states)
    deliverables = [item for item in artifact_states if item.get("is_deliverable")]
    unusable = [item["uri"] for item in deliverables if not item["usable"]]
    warnings: list[str] = []
    if not artifact_uris:
        warnings.append("当前运行尚未生成财务、十三表或报告工件")
    if unusable:
        warnings.append(
            f"{len(unusable)} 个交付工件不可用（validation_status 非通过），"
            "URI 可读但不得作为交付物引用"
        )
    skipped_now = [
        dict(item) for item in run.get("skipped_fields") or [] if isinstance(item, dict)
    ]
    if skipped_now:
        warnings.append(
            f"{len(skipped_now)} 个字段由用户显式跳过、未经确认，"
            "工件中相关数字为受控假设"
        )
    return _envelope(
        True,
        (
            _ENVELOPE_STATUS_BY_DELIVERY_STATE.get(delivery_state, "partial")
            if artifact_uris
            else "empty"
        ),
        warnings=warnings,
        resource_uris=sorted(set([*uris, *artifact_uris])),
        query_success=True,
        delivery_state=delivery_state,
        domain_status=_domain_status(delivery_state),
        # artifacts 由裸 URI 列表升级为逐工件可用性记录：
        # 只给 URI 会让调用方默认"能读即可交付"。
        artifacts=artifact_states,
        artifact_uris=artifact_uris,
        deliverable_artifact_count=len(deliverables),
        usable_artifact_count=len(deliverables) - len(unusable),
        unusable_artifact_uris=unusable,
        # 与 status 同一判据：技术验收有阻断项就不是可交付的技术预览。
        technical_preview_ready=bool(
            dict(run.get("domain_results") or {}).get("technical_preview_ready", False)
        )
        and not _acceptance_blockers(run),
        acceptance=acceptance,
        report_profile=dict(run.get("report_profile") or {}),
        skipped_fields=[dict(item) for item in run.get("skipped_fields") or []],
        skip_history=[dict(item) for item in run.get("skip_history") or []],
        release_limitations=sorted(
            {
                *(str(item) for item in run.get("release_limitations") or []),
                *_skip_limitations(
                    list(run.get("skipped_fields") or []),
                    list(run.get("skip_history") or []),
                ),
            }
        ),
        blockers=sorted(
            {
                *(str(item) for item in (run.get("blockers") or [])),
                *_acceptance_blockers(run),
            }
        ),
        manifest_uri=str(run.get("manifest_uri") or ""),
        validation_complete=False,
        input_evidence_complete=False,
    )


def list_resources(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    selected = str(args.get("resource_type") or "")
    entries: list[dict[str, Any]] = []
    for store, object_type, _id_field in _RESOURCE_STORES:
        if selected and selected != object_type:
            continue
        for record in store.list(workspace_id):
            entries.append(
                {
                    "uri": record["resource_uri"],
                    "name": f"{object_type} {record['object_id']}",
                    "mime_type": "application/json",
                    "object_type": object_type,
                    "content_hash": record["content_hash"],
                }
            )
    try:
        page = paginate_resource_entries(
            entries,
            cursor=str(args.get("cursor") or ""),
            limit=int(args.get("limit") or 50),
        )
    except ValueError as exc:
        return _blocked(str(exc), "Resource cursor 无效或资源集合已变化")
    return _envelope(
        True,
        "ok",
        resource_uris=[str(item["uri"]) for item in page.get("resources") or []],
        **page,
    )


def read_resource(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    uri = str(args.get("uri") or "")
    resolved = resolve_resource(uri)
    if resolved is None:
        return _blocked("resource_not_found", "Resource 不存在")
    content, mime_type = resolved
    if isinstance(content, bytes):
        if f"/workspaces/{workspace_id}/" not in uri:
            return _blocked("resource_scope_mismatch", "Resource 不属于指定 workspace")
        return _envelope(
            True,
            "ok",
            resource_uris=[uri],
            uri=uri,
            mime_type=mime_type,
            content_hash="sha256:" + hashlib.sha256(content).hexdigest(),
            encoding="base64",
            content_base64=base64.b64encode(content).decode("ascii"),
        )
    loaded = json.loads(content)
    if str(loaded.get("workspace_id") or "") != workspace_id:
        return _blocked("resource_scope_mismatch", "Resource 不属于指定 workspace")
    return _envelope(
        True,
        "ok",
        resource_uris=[uri],
        uri=uri,
        mime_type=mime_type,
        content_hash=str(loaded.get("content_hash") or ""),
        resource=loaded,
    )


def resolve_resource(
    uri: str,
) -> tuple[str | bytes, str] | None:
    from lvke_mcp.servers.lvke_zero_material_delivery.artifact_delivery import (
        resolve_report_file,
    )

    report_file = resolve_report_file(
        uri,
        report_store=REPORT_STORE,
    )
    if report_file is not None:
        return report_file
    if uri.startswith("lvke://finance-tables/workspaces/"):
        remainder = uri.removeprefix("lvke://finance-tables/workspaces/")
        workspace_id = remainder.split("/", 1)[0]
        try:
            require_safe_id(workspace_id, "workspace_id")
        except ValueError:
            return None
        from lvke_mcp.domains.finance import tables_service as finance_tables

        return finance_tables.resolve_resource(
            uri,
            workspace_id,
        )
    for store, _object_type, _id_field in _RESOURCE_STORES:
        record = store.resolve_uri(uri)
        if record is not None:
            return json.dumps(record, ensure_ascii=False, indent=2), "application/json"
    return None
