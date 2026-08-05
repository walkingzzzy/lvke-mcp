"""Immutable state and intent parsing for zero-material delivery orchestration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from copy import deepcopy
from datetime import date
from typing import Any, Callable

from filelock import FileLock
from lvke_mcp.runtime.workspace import workspace_root

from lvke_mcp.runtime.storage import (
    JSONArtifactStore,
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
)

SERVICE_NAME = "lvke-zero-material-delivery"
SERVICE_VERSION = "0.1.0"
ASSUMPTION_PROFILE_VERSION = "zero-material-assumptions.2026-08.v1"

INTENT_STORE = JSONArtifactStore(
    "zero-material-delivery", "intents", "zmi", "intents"
)
ASSUMPTION_STORE = JSONArtifactStore(
    "zero-material-delivery", "assumptions", "zma", "assumptions"
)
RUN_STORE = JSONArtifactStore(
    "zero-material-delivery", "runs", "zmr", "runs"
)
REPORT_STORE = JSONArtifactStore(
    "zero-material-delivery", "technical_reports", "zmrep", "reports"
)
ASSUMPTION_REGISTER_STORE = JSONArtifactStore(
    "zero-material-delivery", "assumption_registers", "zmareg", "assumption-registers"
)
GAP_REGISTER_STORE = JSONArtifactStore(
    "zero-material-delivery", "gap_registers", "zmgap", "gap-registers"
)
EVIDENCE_MANIFEST_STORE = JSONArtifactStore(
    "zero-material-delivery", "evidence_manifests", "zmev", "evidence-manifests"
)
MANIFEST_STORE = JSONArtifactStore(
    "zero-material-delivery", "manifests", "zmman", "manifests"
)
IDEMPOTENCY_STORE = JSONArtifactStore(
    "zero-material-delivery", "idempotency", "zmid", "idempotency"
)

_RESOURCE_STORES = (
    (INTENT_STORE, "DeliveryIntent", "delivery_intent_id"),
    (ASSUMPTION_STORE, "AssumptionPackage", "assumption_package_id"),
    (RUN_STORE, "DeliveryRun", "delivery_run_id"),
    (REPORT_STORE, "TechnicalReport", "technical_report_id"),
    (ASSUMPTION_REGISTER_STORE, "AssumptionRegister", "assumption_register_id"),
    (GAP_REGISTER_STORE, "GapRegister", "gap_register_id"),
    (EVIDENCE_MANIFEST_STORE, "EvidenceManifest", "evidence_manifest_id"),
    (MANIFEST_STORE, "RunManifest", "run_manifest_id"),
)

_ACTIVE_STAGES = {
    "received",
    "intent_resolved",
    "researching",
    "assumptions_ready",
    "planning_ready",
    "finance_ready",
    "tables_ready",
    "report_ready",
    "preview_ready",
    "awaiting_confirmation",
    "confirmed_estimate_ready",
}

# 首期只做明确行业路由；匹配分数并列时 fail-closed，不套通用模型。
_ROUTE_RULES: tuple[dict[str, Any], ...] = (
    {
        "code": "tourism_catering",
        "label": "文旅与休闲服务",
        "keywords": ("文旅", "旅游", "景区", "乐园", "游乐园", "度假", "酒店", "餐饮", "营地"),
        "strong_keywords": ("文旅", "旅游", "景区", "乐园", "游乐园", "度假", "酒店", "营地"),
        "factory_industry": "tourism_catering",
    },
    {
        "code": "manufacturing",
        "label": "制造业",
        "keywords": ("制造", "工厂", "生产线", "装备", "零部件", "加工", "医药", "器械"),
        "strong_keywords": ("制造", "工厂", "生产线", "装备", "零部件", "加工", "医药", "器械"),
        "factory_industry": "manufacturing",
    },
    {
        "code": "environment_utilities",
        "label": "环保与公用事业",
        "keywords": ("环保", "污水", "供水", "垃圾", "固废", "光伏", "风电", "储能", "公用事业"),
        "strong_keywords": ("环保", "污水", "供水", "垃圾", "固废", "光伏", "风电", "储能", "公用事业"),
        "factory_industry": "energy_utilities",
    },
    {
        "code": "park_infrastructure",
        "label": "园区与基础设施",
        "keywords": ("园区", "产业园", "基础设施", "市政", "道路", "物流园", "城市更新", "停车"),
        "strong_keywords": ("产业园", "基础设施", "市政", "道路", "物流园", "城市更新", "停车"),
        "factory_industry": "construction_real_estate",
    },
    {
        "code": "commercial_professional_services",
        "label": "商业与专业服务",
        "keywords": ("商业", "零售", "电商", "咨询", "检测", "研发中心", "媒体", "广告", "专业服务"),
        "strong_keywords": ("零售", "电商", "咨询", "检测", "研发中心", "媒体", "广告", "专业服务"),
        "factory_industry": "professional_research_media",
    },
)


def _envelope(
    success: bool,
    status: str,
    *,
    code: str = "",
    message: str = "",
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    next_actions: list[str] | None = None,
    resource_uris: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "success": success,
        "business_success": success,
        "system_success": True,
        "transport_success": True,
        "status": status,
        "resource_uris": resource_uris or [],
        "warnings": warnings or [],
        "blockers": blockers or [],
        "next_actions": next_actions or [],
        **extra,
    }
    if code:
        result["code"] = code
    if message:
        result["message"] = message
    return result


def _blocked(
    code: str,
    message: str,
    *,
    status: str = "blocked",
    next_actions: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return _envelope(
        False,
        status,
        code=code,
        message=message,
        blockers=[code],
        next_actions=next_actions,
        **extra,
    )


def _view(record: dict[str, Any], id_field: str) -> dict[str, Any]:
    return {
        **dict(record.get("payload") or {}),
        id_field: record["object_id"],
        "workspace_id": record["workspace_id"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "created_at": record["created_at"],
        "resource_uri": record["resource_uri"],
    }


def _idempotency_lock(workspace_id: str) -> FileLock:
    directory = (
        workspace_root(require_safe_id(workspace_id, "workspace_id"))
        / "mcp_objects"
        / "zero-material-delivery"
    )
    directory.mkdir(parents=True, exist_ok=True)
    return FileLock(str(directory / ".idempotency.lock"), timeout=30)


def _idempotent_mutation(
    workspace_id: str,
    *,
    operation: str,
    idempotency_key: str,
    request_payload: dict[str, Any],
    mutation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    key_hash = "sha256:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    request_hash = sha256_json(request_payload)
    with _idempotency_lock(workspace_id):
        for record in IDEMPOTENCY_STORE.list(workspace_id):
            payload = dict(record.get("payload") or {})
            if payload.get("operation") != operation:
                continue
            if not hmac.compare_digest(str(payload.get("key_hash") or ""), key_hash):
                continue
            if not hmac.compare_digest(str(payload.get("request_hash") or ""), request_hash):
                return _blocked(
                    "idempotency_conflict",
                    "同一 idempotency_key 已用于不同请求",
                )
            replay = dict(payload.get("response") or {})
            replay["idempotent_replay"] = True
            return replay
        response = mutation()
        IDEMPOTENCY_STORE.put(
            workspace_id,
            {
                "operation": operation,
                "key_hash": key_hash,
                "request_hash": request_hash,
                "response": response,
            },
            producer=f"{SERVICE_NAME}.{operation}",
        )
        return response


def _resolve_route(sentence: str, explicit_industry: str = "") -> dict[str, Any]:
    haystack = f"{sentence} {explicit_industry}".lower()
    explicit = explicit_industry.strip().lower()
    explicit_route = next(
        (
            route
            for route in _ROUTE_RULES
            if explicit
            and explicit in {str(route["code"]).lower(), str(route["label"]).lower()}
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
        intent_payload = {
            "object_type": "DeliveryIntent",
            "sentence": sentence,
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


def _assumption_field(
    name: str,
    value: Any,
    *,
    unit: str,
    source_ref: str,
    sensitivity: str,
    uncertainty: str,
    decision_impact: str,
    low: Any,
    high: Any,
    confirmed: bool = False,
) -> dict[str, Any]:
    ranking = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    priority_score = (
        ranking.get(sensitivity, 0)
        * ranking.get(uncertainty, 0)
        * ranking.get(decision_impact, 0)
    )
    return {
        "name": name,
        "value": value,
        "range": {"low": low, "base": value, "high": high},
        "unit": unit,
        "period": "模型期",
        "source_type": "user_confirmed" if confirmed else "controlled_assumption",
        "source_ref": source_ref,
        "method": "user_override" if confirmed else "deterministic_industry_scenario_seed",
        "confidence": 1.0 if confirmed else 0.42,
        "sensitivity": sensitivity,
        "uncertainty": uncertainty,
        "decision_impact": decision_impact,
        "confirmation_priority_score": 0 if confirmed else priority_score,
        "confirmed": confirmed,
        "validation_condition": (
            "已确认参数仍需与后续原始材料进行 hash 和数值一致性校验"
            if confirmed
            else "须确认参数，并以合同、测绘、报价或权属等材料替换"
        ),
    }


def _build_assumption_package(intent: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
    from lvke_mcp.servers.lvke_zero_material_delivery.industry_profiles import get_profile

    route = dict(intent["industry"])
    profile = get_profile(str(route["industry_code"]))
    scenarios = build_industry_scenarios(str(route["factory_industry"]))
    base = next(item for item in scenarios if item["variant_id"] == "base")
    low = next(item for item in scenarios if item["variant_id"] == "small_low_debt")
    high = next(item for item in scenarios if item["variant_id"] == "large_high_leverage")
    source_ref = f"{base['matrix_version']}:{base['scenario_id']}"
    base_finance = base["finance"]
    fields = [
        _assumption_field(
            "total_investment_wan",
            base_finance["total_investment_wan"],
            unit="万元",
            source_ref=source_ref,
            sensitivity="critical",
            uncertainty="critical",
            decision_impact="critical",
            low=low["finance"]["total_investment_wan"],
            high=high["finance"]["total_investment_wan"],
        ),
        _assumption_field(
            "annual_revenue_wan",
            base_finance["annual_revenue_wan"],
            unit="万元/年",
            source_ref=source_ref,
            sensitivity="critical",
            uncertainty="critical",
            decision_impact="critical",
            low=round(base_finance["annual_revenue_wan"] * 0.72, 2),
            high=round(base_finance["annual_revenue_wan"] * 1.28, 2),
        ),
        _assumption_field(
            "build_period_months",
            base["build_period_months"],
            unit="月",
            source_ref=source_ref,
            sensitivity="high",
            uncertainty="high",
            decision_impact="high",
            low=max(6, int(base["build_period_months"] * 0.75)),
            high=int(base["build_period_months"] * 1.35),
        ),
        _assumption_field(
            "loan_ratio",
            round(base_finance["loan_wan"] / base_finance["total_investment_wan"], 4),
            unit="比例",
            source_ref=source_ref,
            sensitivity="high",
            uncertainty="critical",
            decision_impact="high",
            low=0.2,
            high=0.72,
        ),
        _assumption_field(
            "loan_rate",
            base_finance["loan_rate"],
            unit="比例/年",
            source_ref=source_ref,
            sensitivity="high",
            uncertainty="medium",
            decision_impact="high",
            low=0.038,
            high=0.061,
        ),
        _assumption_field(
            "operating_period_years",
            int(base_finance["calc_period_years"] - (base["build_period_months"] + 11) // 12),
            unit="年",
            source_ref=source_ref,
            sensitivity="medium",
            uncertainty="medium",
            decision_impact="medium",
            low=8,
            high=20,
        ),
    ]
    return {
        "object_type": "AssumptionPackage",
        "revision": 1,
        "profile_version": ASSUMPTION_PROFILE_VERSION,
        "industry_profile": profile,
        "matrix_version": base["matrix_version"],
        "industry_code": route["industry_code"],
        "industry_label": route["industry_label"],
        "factory_scenario_id": base["scenario_id"],
        "archetype_name": base["archetype_name"],
        "fields": fields,
        "source_precedence": [
            "sentence_explicit_input",
            "immutable_public_evidence",
            "industry_region_benchmark",
            "controlled_assumption",
        ],
        "evidence_boundary": {
            "grade": "C",
            "production_claim_allowed": False,
            "statement": "场景仅作为确定性行业种子，所有项目特定数字均为受控假设",
        },
        "validation_complete": False,
        "input_evidence_complete": False,
    }


def _field_values(package: dict[str, Any]) -> dict[str, Any]:
    return {
        str(item.get("name")): item.get("value")
        for item in package.get("fields") or []
        if isinstance(item, dict) and item.get("name")
    }


def _sync_working_capital(
    finance: dict[str, Any],
    *,
    base_revenue: float,
    target_revenue: float,
) -> float:
    """Keep turnover detail and stated investment working capital in one lineage."""

    if base_revenue <= 0 or target_revenue < 0:
        return float(finance.get("invest_breakdown", {}).get("working_capital_wan") or 0.0)
    ratio = target_revenue / base_revenue
    turnover = finance.get("wc_turnover")
    breakdown = finance.get("invest_breakdown")
    if not isinstance(breakdown, dict):
        return 0.0
    working_capital = round(float(breakdown.get("working_capital_wan") or 0.0) * ratio, 2)
    breakdown["working_capital_wan"] = working_capital
    working_series = finance.get("working_capital_by_year")
    if isinstance(working_series, list):
        finance["working_capital_by_year"] = [
            round(float(value) * ratio, 2) if isinstance(value, (int, float)) else value
            for value in working_series
        ]
    from lvke_mcp.domains.finance.working_capital import estimate_from_turnover

    cost_items = finance.get("cost_items")
    cash_cost = (
        sum(float(value) for value in cost_items.values() if isinstance(value, (int, float)))
        if isinstance(cost_items, dict)
        else 0.0
    )
    computed = estimate_from_turnover(
        revenue=target_revenue,
        cash_cost=cash_cost,
        turnover=turnover if isinstance(turnover, dict) else {},
    )
    working_capital = round(float(computed.get("total") or working_capital), 2)
    breakdown["working_capital_wan"] = working_capital
    if isinstance(turnover, dict):
        turnover["self_funded_wan"] = working_capital
    return working_capital


def _scale_investment_breakdown(
    breakdown: dict[str, Any],
    *,
    base_construction: float,
    target_construction: float,
) -> None:
    if base_construction <= 0 or target_construction <= 0:
        return
    ratio = target_construction / base_construction
    for key in ("construction_wan", "other_wan", "reserve_wan"):
        if isinstance(breakdown.get(key), (int, float)):
            breakdown[key] = round(float(breakdown[key]) * ratio, 2)
    for key in ("construction_detail", "other_detail", "contingency_detail"):
        detail = breakdown.get(key)
        if isinstance(detail, dict):
            for name, value in detail.items():
                if isinstance(value, (int, float)):
                    detail[name] = round(float(value) * ratio, 2)
    items = breakdown.get("construction_items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("amount_wan"), (int, float)):
                item["amount_wan"] = round(float(item["amount_wan"]) * ratio, 2)
            if isinstance(item.get("indicator_yuan"), (int, float)):
                item["indicator_yuan"] = round(float(item["indicator_yuan"]) * ratio, 2)


def _reconcile_funding(
    finance: dict[str, Any],
    *,
    target_total: float,
    base_total: float,
    working_capital: float,
    build_months: int,
    values: dict[str, Any],
) -> None:
    from lvke_mcp.domains.finance.industry_scenario_factory import _funding

    breakdown = finance.get("invest_breakdown")
    if not isinstance(breakdown, dict):
        return
    construction = float(breakdown.get("construction_wan") or 0.0)
    if construction <= 0:
        return
    if abs(target_total - base_total) > 0.005:
        target_construction = max(target_total - working_capital, 0.0)
        for _ in range(8):
            projected_total, *_ = _funding(
                target_construction,
                working_capital,
                float(values.get("loan_ratio", finance.get("loan_ratio") or 0.0)),
                float(values.get("loan_rate", finance.get("loan_rate") or 0.0)),
                max(1, (build_months + 11) // 12),
                0.0,
            )
            correction = target_total - projected_total
            if abs(correction) <= 0.005:
                break
            target_construction = max(round(target_construction + correction, 2), 0.0)
        if target_construction <= 0:
            return
        _scale_investment_breakdown(
            breakdown,
            base_construction=construction,
            target_construction=target_construction,
        )
        construction = round(target_construction, 2)
    loan_ratio = float(values.get("loan_ratio", finance.get("loan_ratio") or 0.0))
    loan_rate = float(values.get("loan_rate", finance.get("loan_rate") or 0.0))
    build_years = max(1, (build_months + 11) // 12)
    subsidy_ratio = (
        float(finance.get("gov_subsidy_wan") or 0.0) / max(target_total, 1.0)
    )
    total, capital, loan, subsidy, interest = _funding(
        construction,
        working_capital,
        loan_ratio,
        loan_rate,
        build_years,
        subsidy_ratio,
    )
    finance.update(
        {
            "total_investment_wan": total,
            "capital_own_wan": capital,
            "loan_wan": loan,
            "gov_subsidy_wan": subsidy,
            "loan_ratio": loan_ratio,
        }
    )
    breakdown["interest_wan"] = interest


def _apply_revenue_target(spec: dict[str, Any], old: float, target: float) -> None:
    revenue = spec.get("revenue") if isinstance(spec.get("revenue"), dict) else {}
    if old <= 0 or target < 0:
        return
    ratio = target / old
    model = str(revenue.get("model") or "")
    if model == "tourism" and isinstance(revenue.get("annual_visitors"), (int, float)):
        revenue["annual_visitors"] = round(float(revenue["annual_visitors"]) * ratio, 6)
    elif model == "product_sales" and isinstance(revenue.get("products"), list):
        for product in revenue["products"]:
            if isinstance(product, dict) and isinstance(product.get("capacity"), (int, float)):
                product["capacity"] = round(float(product["capacity"]) * ratio, 6)
    elif model == "property_sales" and isinstance(revenue.get("saleable_area"), (int, float)):
        revenue["saleable_area"] = round(float(revenue["saleable_area"]) * ratio, 6)
    elif model == "gov_payment":
        revenue["annual_gov_payment_wan"] = target
    else:
        revenue["annual_revenue_wan"] = target


def _effective_revenue_target(spec: dict[str, Any], fallback: float) -> float:
    revenue = spec.get("revenue")
    if not isinstance(revenue, dict):
        return fallback
    try:
        from lvke_mcp.domains.finance import revenue_models

        expanded = revenue_models.expand({"revenue": revenue}, 20)
    except Exception:  # noqa: BLE001
        return fallback
    values = [
        float(item)
        for item in expanded.get("revenue_by_year") or []
        if isinstance(item, (int, float))
    ]
    return round(max(values), 2) if values else fallback


def _scenario_inputs(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios

    scenario_id = str(package["factory_scenario_id"])
    industry_code = scenario_id.split(".", 1)[0]
    scenario = next(
        item
        for item in build_industry_scenarios(industry_code)
        if item["scenario_id"] == scenario_id
    )
    spec = deepcopy(scenario["spec"])
    finance = deepcopy(scenario["finance"])
    values = _field_values(package)

    base_total = float(finance["total_investment_wan"])
    target_total = float(values.get("total_investment_wan", base_total))
    base_revenue = float(finance.get("annual_revenue_wan") or 0)
    base_effective_revenue = _effective_revenue_target(spec, base_revenue)
    target_revenue = float(values.get("annual_revenue_wan", base_revenue))
    _apply_revenue_target(spec, base_revenue, target_revenue)
    effective_revenue = _effective_revenue_target(spec, target_revenue)
    finance["annual_revenue_wan"] = effective_revenue

    if "loan_rate" in values:
        finance["loan_rate"] = float(values["loan_rate"])
    if "loan_ratio" in values:
        finance["loan_ratio"] = float(values["loan_ratio"])
    build_months = int(values.get("build_period_months", scenario["build_period_months"]))
    working_capital = _sync_working_capital(
        finance,
        base_revenue=base_effective_revenue,
        target_revenue=effective_revenue,
    )
    _reconcile_funding(
        finance,
        target_total=target_total,
        base_total=base_total,
        working_capital=working_capital,
        build_months=build_months,
        values=values,
    )
    operating_years = int(values.get("operating_period_years", 10))
    finance["calc_period_years"] = max(
        finance.get("loan_years", 1) + (build_months + 11) // 12,
        operating_years + (build_months + 11) // 12,
    )
    finance.update(
        {
            "industry": scenario["industry_label"],
            "invest_type": scenario["invest_type"],
            "build_period_months": build_months,
        }
    )
    spec.update(
        {
            "confirmation_status": "candidate",
            "source_hint": "zero_material_controlled_assumption",
            "selected_scenario_id": scenario_id,
            "assumptions": [
                "零材料受控假设，仅用于 estimate_preview",
                "计算口径冻结不代表项目事实已获证据支持",
            ],
            "field_sources": {
                field: {
                    "source": "controlled_assumption",
                    "source_ref": str(item.get("source_ref") or package.get("profile_version") or ""),
                    "confirmed": bool(item.get("confirmed")),
                }
                for item in package.get("fields") or []
                if isinstance(item, dict)
                for field in [str(item.get("name") or "")]
                if field
            },
        }
    )
    spec.pop("confirmed_by", None)
    context = {
        "scenario_id": scenario_id,
        "scenario": scenario,
        "build_period_months": build_months,
    }
    return spec, finance, context


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
                "该运行已取消，须先调用 delivery_resume 创建恢复快照",
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
        blockers = [
            *list(domain.get("blockers") or []),
            *list(delivery_artifacts.get("blockers") or []),
        ]
        technical_preview_ready = (
            str(domain.get("stage") or "") == "tables_ready"
            and not delivery_artifacts.get("blockers")
        )
        next_run = _new_run(
            workspace_id,
            intent_id=intent["delivery_intent_id"],
            assumption_package_id=assumption["object_id"],
            previous_run_id=run_id,
            stage="preview_ready" if technical_preview_ready else str(domain.get("stage") or "assumptions_ready"),
            blockers=blockers,
            status_reason=str(domain.get("status") or "upstream_partial"),
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
        )
        return _envelope(
            technical_preview_ready,
            "partial" if blockers else "ok",
            warnings=[
                "行业场景仅作为受控假设种子，不是项目证据",
                *list(domain.get("warnings") or []),
            ],
            blockers=blockers,
            next_actions=(
                ["提交公开研究结果后继续规划与正式报告准备；或先确认关键假设并重算"]
                if blockers
                else ["读取交付 Resources；正式发布资格仍保持阻断"]
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


def status(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    record = RUN_STORE.get(workspace_id, run_id)
    if record is None:
        return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
    run = _view(record, "delivery_run_id")
    return _envelope(
        True,
        "ok",
        resource_uris=[record["resource_uri"]],
        delivery_run=run,
        stage=run["stage"],
        progress=_stage_progress(str(run["stage"])),
        resume_token=record["content_hash"],
        validation_complete=False,
        input_evidence_complete=False,
    )


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
    return _envelope(
        True,
        "ok",
        resource_uris=[record["resource_uri"]],
        assumption_package_id=package_id,
        assumptions=fields,
        confirmation_items=[item for item in fields if not item.get("confirmed")][:limit],
        validation_complete=False,
        input_evidence_complete=False,
    )


def confirm_assumptions(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    package_id = require_safe_id(args.get("assumption_package_id"), "assumption_package_id")
    idempotency_key = str(args.get("idempotency_key") or "")
    confirmations = [dict(item) for item in args.get("confirmations") or []]
    request_payload = {
        "assumption_package_id": package_id,
        "confirmations": confirmations,
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
                not item.get("confirmed") for item in known.values()
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
        next_run = _new_run(
            workspace_id,
            intent_id=intent_id,
            assumption_package_id=revised["object_id"],
            previous_run_id=str(source_run["object_id"]),
            stage="assumptions_ready",
            blockers=["recalculation_required"],
            status_reason="confirmed_inputs_require_new_domain_objects",
            object_refs={"assumption_package_id": revised["object_id"]},
        )
        return _envelope(
            True,
            "accepted",
            warnings=["用户确认值仍不是合同、测绘、报价或权属证据"],
            blockers=["recalculation_required"],
            next_actions=["使用新的 delivery_run_id 重算财务、十三表和报告"],
            resource_uris=[revised["resource_uri"], next_run["resource_uri"]],
            assumption_package=_view(revised, "assumption_package_id"),
            delivery_run=_view(next_run, "delivery_run_id"),
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
        )
        return _envelope(
            True,
            "accepted",
            blockers=[] if operation == "resume" else ["cancelled"],
            next_actions=["调用 delivery_start 继续运行"] if operation == "resume" else [],
            resource_uris=[next_run["resource_uri"]],
            delivery_run=_view(next_run, "delivery_run_id"),
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
    run = _view(record, "delivery_run_id")
    refs = dict(run.get("object_refs") or {})
    uris = [record["resource_uri"]]
    for ref in refs.values():
        for store, _object_type, _id_field in _RESOURCE_STORES:
            linked = store.get(workspace_id, str(ref))
            if linked is not None:
                uris.append(str(linked["resource_uri"]))
                break
    artifact_uris = [str(item) for item in run.get("artifact_uris") or []]
    return _envelope(
        True,
        "ok" if artifact_uris else "empty",
        warnings=[] if artifact_uris else ["当前运行尚未生成财务、十三表或报告工件"],
        resource_uris=sorted(set([*uris, *artifact_uris])),
        artifacts=artifact_uris,
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
    return _envelope(True, "ok", resource_uris=[], **page)


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


def standard_resource_entries() -> list[dict[str, str]]:
    """List persisted resources visible for the workspace scope."""

    workspaces_root = workspace_root(".").parent
    if not workspaces_root.is_dir():
        return []
    entries: dict[str, dict[str, str]] = {}
    for workspace in sorted(workspaces_root.iterdir(), key=lambda item: item.name):
        if not workspace.is_dir() or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", workspace.name
        ):
            continue
        workspace_id = workspace.name
        for store, object_type, _id_field in _RESOURCE_STORES:
            for record in store.list(workspace_id):
                uri = str(record["resource_uri"])
                entries[uri] = {
                    "uri": uri,
                    "name": f"{object_type} {record['object_id']}",
                    "mime_type": "application/json",
                }
                if object_type == "TechnicalReport":
                    for filename, mime_type in (
                        ("report.md", "text/markdown; charset=utf-8"),
                        (
                            "report.docx",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        ),
                    ):
                        file_uri = f"{uri}/files/{filename}"
                        if resolve_resource(file_uri) is not None:
                            entries[file_uri] = {
                                "uri": file_uri,
                                "name": f"{record['object_id']} {filename}",
                                "mime_type": mime_type,
                            }
        for run_record in RUN_STORE.list(workspace_id):
            for uri in (run_record.get("payload") or {}).get("artifact_uris") or []:
                uri = str(uri)
                if not uri.startswith("lvke://finance-tables/workspaces/"):
                    continue
                resolved = resolve_resource(uri)
                if resolved is None:
                    continue
                _content, mime_type = resolved
                entries[uri] = {
                    "uri": uri,
                    "name": uri.rsplit("/", 1)[-1],
                    "mime_type": mime_type,
                }
    return [entries[uri] for uri in sorted(entries)]


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
