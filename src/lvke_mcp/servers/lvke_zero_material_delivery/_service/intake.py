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
from .routing import _new_run, _project_name, _resolve_route


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
    }

    def mutation() -> dict[str, Any]:
        route = _resolve_route(sentence, request_payload["industry"])
        # 句子里写明的参数必须固化，否则行业种子会覆盖明确输入。
        explicit = extract_explicit_inputs(sentence)
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
            "validation_complete": False,
        }
        intent = INTENT_STORE.put(
            workspace_id,
            intent_payload,
            producer=f"{SERVICE_NAME}.delivery_create_from_sentence",
            status="ok" if route["resolved"] else "missing_inputs",
            basis=request_payload,
        )
        blockers = [] if route["resolved"] else [str(route["reason"])]
        run = _new_run(
            workspace_id,
            intent_id=intent["object_id"],
            stage="intent_resolved" if route["resolved"] else "received",
            blockers=blockers,
            status_reason=str(route.get("reason") or ""),
        )
        intent_view = _view(intent, "delivery_intent_id")
        run_view = _view(run, "delivery_run_id")
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
            warnings=["零材料结果固定为技术预估版，受当前输入快照与受控假设约束"],
            next_actions=["调用 delivery_start 生成受控假设包并推进交付链"],
            resource_uris=[intent["resource_uri"], run["resource_uri"]],
            delivery_intent=intent_view,
            delivery_run=run_view,
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
