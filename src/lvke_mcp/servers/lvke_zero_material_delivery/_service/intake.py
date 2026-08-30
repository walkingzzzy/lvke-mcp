"""``create_from_sentence``: the single natural-language entry point."""

from __future__ import annotations

from typing import Any


from lvke_mcp.runtime.storage import require_safe_id

from .base import (
    INTENT_STORE,
    SERVICE_NAME,
    _envelope,
    _idempotent_mutation,
    _view,
)
from .explicit_inputs import extract_explicit_inputs
from .questions import compute_missing_inputs, summarize_gaps
from .report_profiles import ReportProfileError, resolve_profile
from .routing import _new_run, _project_name, _resolve_route


def _sentence_is_acquisition(sentence: str, request_payload: dict[str, Any]) -> bool:
    """Detect acquisition intent with the same keywords the orchestrator uses.

    刻意复用 ``orchestration._ACQUISITION_KEYWORDS`` 而不是另写一份：两处判据分叉
    会让同一句话在编排侧走收购模型、在报告侧选通用配置，而这种不一致只有等到
    正文与财务对不上时才会被发现。
    """

    from .orchestration import _ACQUISITION_KEYWORDS

    haystack = " ".join(
        [
            sentence,
            str(request_payload.get("project_name") or ""),
            str(request_payload.get("project_nature") or ""),
        ]
    ).lower()
    return any(keyword in haystack for keyword in _ACQUISITION_KEYWORDS)


def create_from_sentence(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    sentence = str(args.get("sentence") or "").strip()
    idempotency_key = str(args.get("idempotency_key") or "")
    request_payload = {
        "sentence": sentence,
        "project_name": str(args.get("project_name") or "").strip(),
        "region": str(args.get("region") or "").strip(),
        "industry": str(args.get("industry") or "").strip(),
        "project_nature": str(args.get("project_nature") or "").strip(),
        "report_type": str(args.get("report_type") or "可行性研究报告").strip(),
        "report_profile_id": str(args.get("report_profile_id") or "").strip(),
        "template_set_id": str(args.get("template_set_id") or "").strip(),
    }

    def mutation() -> dict[str, Any]:
        route = _resolve_route(sentence, request_payload["industry"])
        # 句子里写明的参数必须固化，否则行业种子会覆盖明确输入。
        explicit = extract_explicit_inputs(sentence)
        # 报告配置在创建期就选定并冻结：追问集合按所选配置的 required_fields 算，
        # 不按一张写死的字段表。行业未解析时先不选配置（选择器缺关键维度）。
        profile_selection: dict[str, Any] = {}
        profile_document: dict[str, Any] = {}
        profile_error = ""
        profile_detail: Any = {}
        if route["resolved"]:
            # 收购判定必须与 orchestration._resolve_project_route 用同一批关键词，
            # 否则两处对同一句话给出不同 project_type：编排走收购模型、报告却选
            # 通用配置。此前这里额外要求 asset_type != general，"收购一家酒店"
            # （asset_type 仍是 general）会被判成通用可研。
            is_acquisition = _sentence_is_acquisition(sentence, request_payload)
            try:
                resolved_profile = resolve_profile(
                    industry_code=str(route["industry_code"]),
                    project_type=(
                        "asset_acquisition" if is_acquisition else "generic_feasibility"
                    ),
                    transaction_structure=(
                        "asset_acquisition" if is_acquisition else "new_build"
                    ),
                    asset_type=str(route.get("asset_type") or "general"),
                    report_type=request_payload["report_type"],
                    requested_profile_id=request_payload["report_profile_id"],
                    requested_template_set_id=request_payload["template_set_id"],
                )
            except ReportProfileError as exc:
                profile_error = exc.code
                profile_detail = exc.detail
            else:
                profile_selection = dict(resolved_profile["selection"])
                profile_document = dict(resolved_profile["profile"])
        intent_payload = {
            "object_type": "DeliveryIntent",
            "sentence": sentence,
            "explicit_inputs": explicit["fields"],
            "explicit_input_unmapped": explicit["unmapped"],
            "project_name": _project_name(sentence, request_payload["project_name"]),
            "region": request_payload["region"],
            "industry": route,
            "project_nature": request_payload["project_nature"] or "待确认",
            "report_type": request_payload["report_type"],
            "delivery_mode": "zero_material",
            "assurance_level": "estimate_preview",
            "material_state": "client_materials_absent",
            "report_profile": profile_selection,
            "validation_complete": False,
        }
        intent = INTENT_STORE.put(
            workspace_id,
            intent_payload,
            producer=f"{SERVICE_NAME}.delivery_create_from_sentence",
            status="ok" if route["resolved"] and not profile_error else "missing_inputs",
            basis=request_payload,
        )
        intent_view = _view(intent, "delivery_intent_id")
        # 配置缺口按所选配置的 required_fields 动态计算；行业未解析或配置未选中时
        # 无从计算，此时只返回上游那一个缺口，不假装知道后面要问什么。
        field_gaps = (
            compute_missing_inputs(profile=profile_document, intent=intent_view)
            if profile_document
            else []
        )
        gap_summary = summarize_gaps(field_gaps)
        blockers = [] if route["resolved"] else [str(route["reason"])]
        if profile_error:
            blockers.append(profile_error)
        run = _new_run(
            workspace_id,
            intent_id=intent["object_id"],
            stage="intent_resolved" if route["resolved"] and not profile_error else "received",
            blockers=blockers,
            status_reason=str(route.get("reason") or profile_error or ""),
            report_profile=profile_selection,
            missing_inputs=field_gaps,
        )
        run_view = _view(run, "delivery_run_id")
        if profile_error and route["resolved"]:
            return _envelope(
                False,
                "blocked",
                code=profile_error,
                message="未能唯一确定报告配置；不套用通用模板",
                blockers=blockers,
                next_actions=[
                    "显式传入 report_profile_id 或 template_set_id",
                    "或修订 config/report_profiles/manifest.v1.json 的适用条件",
                ],
                resource_uris=[intent["resource_uri"], run["resource_uri"]],
                delivery_intent=intent_view,
                delivery_run=run_view,
                report_profile_detail=profile_detail,
                validation_complete=False,
                input_evidence_complete=False,
            )
        if not route["resolved"]:
            return _envelope(
                False,
                "missing_inputs",
                code=str(route["reason"]),
                message="一句话无法唯一确定首期行业路线",
                blockers=blockers,
                next_actions=["明确选择一个 industry_code 后重新创建交付意图"],
                resource_uris=[intent["resource_uri"], run["resource_uri"]],
                delivery_intent=intent_view,
                delivery_run=run_view,
                missing_inputs=[
                    {
                        "field": "industry",
                        "reason": route["reason"],
                        "candidates": route["candidates"],
                    }
                ],
                validation_complete=False,
                input_evidence_complete=False,
            )
        return _envelope(
            True,
            "ok",
            warnings=[
                "零材料结果固定为技术预估版，受当前输入快照与受控假设约束",
                *(
                    [
                        f"{gap_summary['pending_count']} 个配置必填字段尚未回答，"
                        "未回答项将按受控假设取值并计入交付限制"
                    ]
                    if gap_summary["pending_count"]
                    else []
                ),
            ],
            next_actions=[
                *(
                    ["按 missing_inputs 回答关键字段，或显式跳过后继续"]
                    if gap_summary["pending_count"]
                    else []
                ),
                "调用 delivery_start 生成受控假设包并推进交付链",
            ],
            resource_uris=[intent["resource_uri"], run["resource_uri"]],
            delivery_intent=intent_view,
            delivery_run=run_view,
            report_profile=profile_selection,
            missing_inputs=field_gaps,
            gap_summary=gap_summary,
            release_limitations=gap_summary["release_limitations"],
            validation_complete=False,
            input_evidence_complete=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="delivery_create_from_sentence",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )
