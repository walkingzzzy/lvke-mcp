"""Sentence-to-industry routing and delivery run record construction."""

from __future__ import annotations

import re
from typing import Any


from lvke_mcp.runtime.storage import sha256_json

from .base import (
    RUN_STORE,
    SERVICE_NAME,
    SERVICE_VERSION,
    _ACTIVE_STAGES,
    _ROUTE_RULES,
)


def _resolve_route(sentence: str, explicit_industry: str = "") -> dict[str, Any]:
    haystack = f"{sentence} {explicit_industry}".lower()
    explicit = explicit_industry.strip().lower()
    explicit_route = next(
        (
            route
            for route in _ROUTE_RULES
            if explicit
            and (
                explicit in {str(route["code"]).lower(), str(route["label"]).lower()}
                # 显式行业也可用路由关键词指定（如 urban_rail_transit），
                # 否则调用方必须先知道内部 code 才能选中路由。
                or explicit in {str(keyword).lower() for keyword in route["keywords"]}
            )
        ),
        None,
    )
    scored: list[tuple[int, dict[str, Any], list[str]]] = []
    strong_matches: list[tuple[dict[str, Any], list[str]]] = []
    for route in _ROUTE_RULES:
        matched = [keyword for keyword in route["keywords"] if keyword.lower() in haystack]
        strong = [
            keyword
            for keyword in route.get("strong_keywords", route["keywords"])
            if keyword.lower() in haystack
        ]
        if strong:
            strong_matches.append((route, sorted(set(strong))))
        if matched:
            scored.append((len(set(matched)), route, sorted(set(matched))))

    if explicit_route is not None:
        matched = sorted(
            {
                keyword
                for keyword in explicit_route["keywords"]
                if keyword.lower() in haystack
            }
            | {explicit_industry}
        )
        return {
            "resolved": True,
            "industry_code": explicit_route["code"],
            "industry_label": explicit_route["label"],
            "factory_industry": explicit_route["factory_industry"],
            "matched_keywords": matched,
            "confidence": min(0.95, 0.58 + 0.08 * len(matched)),
            "explicit_selection": True,
            "route_conflicts": [
                {
                    "industry_code": route["code"],
                    "matched_keywords": keywords,
                }
                for route, keywords in strong_matches
                if route["code"] != explicit_route["code"]
            ],
        }

    if len(strong_matches) > 1:
        return {
            "resolved": False,
            "reason": "ambiguous_route",
            "candidates": [
                {
                    "industry_code": route["code"],
                    "industry_label": route["label"],
                    "matched_keywords": keywords,
                }
                for route, keywords in strong_matches
            ],
        }
    if not scored:
        return {
            "resolved": False,
            "reason": "missing_route",
            "candidates": [
                {"industry_code": route["code"], "industry_label": route["label"]}
                for route in _ROUTE_RULES
            ],
        }
    scored.sort(key=lambda item: (-item[0], str(item[1]["code"])))
    best_score = scored[0][0]
    tied = [item for item in scored if item[0] == best_score]
    if len(tied) != 1:
        return {
            "resolved": False,
            "reason": "ambiguous_route",
            "candidates": [
                {
                    "industry_code": item[1]["code"],
                    "industry_label": item[1]["label"],
                    "matched_keywords": item[2],
                }
                for item in tied
            ],
        }
    _, route, matched = tied[0]
    return {
        "resolved": True,
        "industry_code": route["code"],
        "industry_label": route["label"],
        "factory_industry": route["factory_industry"],
        "matched_keywords": matched,
        "confidence": min(0.95, 0.58 + 0.08 * len(matched)),
    }


def _project_name(sentence: str, supplied: str = "") -> str:
    if supplied.strip():
        return supplied.strip()
    compact = re.sub(r"\s+", "", sentence).strip("，。；;,. ")
    return compact[:80] or "零材料技术预估项目"


def _new_run(
    workspace_id: str,
    *,
    intent_id: str,
    stage: str,
    assumption_package_id: str = "",
    previous_run_id: str = "",
    blockers: list[str] | None = None,
    resume_stage: str = "",
    status_reason: str = "",
    object_refs: dict[str, str] | None = None,
    artifact_uris: list[str] | None = None,
    manifest_uri: str = "",
    domain_results: dict[str, Any] | None = None,
    object_id: str | None = None,
) -> dict[str, Any]:
    if stage not in _ACTIVE_STAGES | {"cancelled", "failed"}:
        raise ValueError("invalid delivery stage")
    payload = {
        "object_type": "DeliveryRun",
        "delivery_mode": "zero_material",
        "assurance_level": "estimate_preview",
        "stage": stage,
        "intent_id": intent_id,
        "assumption_package_id": assumption_package_id,
        "previous_run_id": previous_run_id,
        "resume_stage": resume_stage,
        "status_reason": status_reason,
        "object_refs": dict(object_refs or {}),
        "artifact_uris": sorted(set(artifact_uris or [])),
        "manifest_uri": manifest_uri,
        "domain_results": dict(domain_results or {}),
        "blockers": list(blockers or []),
        "validation_condition": "甲方原始材料缺失，结果按受控假设范围校验",
        "service_version": SERVICE_VERSION,
    }
    return RUN_STORE.put(
        workspace_id,
        payload,
        producer=f"{SERVICE_NAME}.delivery_run",
        status="blocked" if blockers else ("cancelled" if stage == "cancelled" else "ok"),
        source_ids=[item for item in (intent_id, assumption_package_id, previous_run_id) if item],
        basis=payload,
        object_id=object_id,
    )


def _planned_run_id(
    workspace_id: str,
    previous_run_id: str,
    operation_key: str,
) -> str:
    digest = sha256_json(
        {
            "workspace_id": workspace_id,
            "previous_run_id": previous_run_id,
            "operation_key": operation_key,
        }
    ).removeprefix("sha256:")
    return f"zmr_{digest[:24]}"
