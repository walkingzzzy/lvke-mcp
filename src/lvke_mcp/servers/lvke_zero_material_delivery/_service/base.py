"""Artifact stores, stage table and the envelope/idempotency foundation.

Every ``JSONArtifactStore`` in this package is defined here and only here;
re-defining or re-exporting one elsewhere would create two views of the
same on-disk state.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Callable

from filelock import FileLock
from lvke_mcp.runtime.workspace import workspace_root

from lvke_mcp.runtime.storage import (
    JSONArtifactStore,
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
TEMPLATE_PACK_STORE = JSONArtifactStore(
    "zero-material-delivery", "template_packs", "zmtp", "template-packs"
)
PROMOTION_STORE = JSONArtifactStore(
    "zero-material-delivery", "promotions", "zmprom", "promotions"
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
    (TEMPLATE_PACK_STORE, "TemplatePack", "template_pack_id"),
    (PROMOTION_STORE, "FormalPromotion", "promotion_id"),
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
        "code": "urban_rail_transit",
        "label": "城市轨道交通",
        # 轨道交通此前被并入 park_infrastructure，其 factory_industry 为
        # construction_real_estate，默认原型是"住宅开发项目"、收入模型
        # property_sales——对轨道项目是明确错误的语义。改为独立路由并指向
        # transport_logistics（收费公路等 gov_payment 型交通基础设施）。
        "keywords": (
            "轨道", "地铁", "轻轨", "市域铁路", "有轨电车", "城市轨道",
            "urban_rail_transit", "rail_transit", "urban_rail", "metro", "subway",
        ),
        "strong_keywords": (
            "轨道", "地铁", "轻轨", "市域铁路", "有轨电车", "城市轨道",
            "urban_rail_transit", "rail_transit", "urban_rail", "metro", "subway",
        ),
        "factory_industry": "transport_logistics",
        "factory_archetype": "urban_rail",
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
    {
        "code": "real_estate",
        "label": "房地产开发",
        "keywords": ("房地产", "住宅", "商品房", "置业", "楼盘", "房地产开发"),
        "strong_keywords": ("房地产", "住宅", "商品房", "置业", "楼盘"),
        "factory_industry": "construction_real_estate",
    },
    {
        "code": "cemetery_funeral",
        "label": "殡葬与墓地",
        "keywords": ("墓地", "公墓", "殡仪", "殡葬", "陵园", "骨灰堂", "cemetery", "funeral", "burial"),
        "strong_keywords": ("墓地", "公墓", "殡仪", "殡葬", "陵园"),
        "factory_industry": "construction_real_estate",
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
