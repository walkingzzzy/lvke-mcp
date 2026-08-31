"""Sentence-to-industry routing and delivery run record construction."""

from __future__ import annotations

import re
from typing import Any


from lvke_mcp.runtime.storage import sha256_json

from .acceptance import empty_acceptance as _empty_acceptance
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
        compatibility_warnings: list[str] = []
        if explicit_route["code"] == "environment_utilities" and any(
            keyword.lower() in haystack for keyword in ("光伏", "风电", "储能", "solar", "photovoltaic", "pv")
        ):
            energy_route = next((item for item in _ROUTE_RULES if item["code"] == "energy_utilities"), None)
            if energy_route is not None:
                explicit_route = energy_route
                compatibility_warnings.append("environment_utilities_deprecated_for_energy_project")
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
            "factory_archetype": explicit_route.get("factory_archetype", ""),
            "asset_type": explicit_route.get("asset_type", "general"),
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
            "compatibility_warnings": compatibility_warnings,
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
        "factory_archetype": route.get("factory_archetype", ""),
        "asset_type": route.get("asset_type", "general"),
        "matched_keywords": matched,
        "confidence": min(0.95, 0.58 + 0.08 * len(matched)),
    }


#: 请求语与交付物名，不属于项目名称本身。整句直接当项目名会把
#: "帮我做一份……的可行性研究报告" 整条塞进 project_name，之后每一章标题、
#: manifest 和报告正文都带着这句客套话。
_REQUEST_PREFIXES = (
    "帮我做一份", "帮我做个", "帮我做", "帮忙做一份", "帮忙做", "请帮我做",
    "请做一份", "我想做", "我要做", "麻烦做一份", "麻烦做", "做一份", "做个",
    "生成一份", "生成", "编制一份", "编制", "写一份", "写",
)
_DELIVERABLE_SUFFIXES = (
    "的可行性研究报告", "的可研报告", "的项目建议书", "的资金申请报告",
    "的初步设计", "的实施方案", "可行性研究报告", "可研报告", "项目建议书",
)
#: 省/市/区县三级行政区划。用于从句子里抽出 region —— 此前 region 只认显式
#: 参数，一句话里写了"武汉市江夏区"也照样留空，后续每一章的地区槽位都缺。
#: 逐级独立匹配，每级都要求"后缀前只有 1~4 个汉字"。整体一次性匹配会让
#: 非贪婪量词从句首开始吞字符（"帮我做一份武汉市"被整段当成城市名）。
_PROVINCE_RE = re.compile(r"[一-鿿]{2,4}(?:省|自治区|特别行政区)|北京市|上海市|天津市|重庆市")
_CITY_RE = re.compile(r"[一-鿿]{2,4}(?:市|自治州|地区|盟)")
_DISTRICT_RE = re.compile(r"[一-鿿]{2,4}(?:区|县|自治县|旗)")


def _project_name(sentence: str, supplied: str = "") -> str:
    if supplied.strip():
        return supplied.strip()
    compact = re.sub(r"\s+", "", sentence).strip("，。；;,. ")
    for prefix in _REQUEST_PREFIXES:
        if compact.startswith(prefix):
            compact = compact[len(prefix):]
            break
    for suffix in _DELIVERABLE_SUFFIXES:
        if compact.endswith(suffix):
            compact = compact[: -len(suffix)]
            break
    compact = compact.strip("，。；;,. 的")
    return compact[:80] or "零材料技术预估项目"


def _region_from_sentence(sentence: str, supplied: str = "") -> str:
    """Extract a province/city/district region string from the sentence.

    只做确定性的行政区划后缀匹配，抽不到就返回空串让上游报缺口——不猜、
    不按项目名硬凑一个地区。
    """

    if supplied.strip():
        return supplied.strip()
    compact = re.sub(r"\s+", "", sentence)
    # 从项目名而非原句里抽：原句含"帮我做一份"等请求语，逐级正则仍可能把它
    # 的尾字并进省市名。项目名已剥离请求语与交付物名。
    haystack = _project_name(sentence) or compact
    # 逐级向后扫：每级都从上一级命中的末尾开始，否则市级正则会在
    # "武汉市江夏区" 里从第二个字再匹配出 "汉市"，拼出 "武汉市汉市江夏区"。
    parts: list[str] = []
    cursor = 0
    for pattern in (_PROVINCE_RE, _CITY_RE, _DISTRICT_RE):
        match = pattern.search(haystack, cursor)
        if match is None:
            continue
        parts.append(match.group(0))
        cursor = match.end()
    # 只接受至少两级（如"武汉市江夏区"或"湖北省武汉市"）：单一个"区"或"市"
    # 极易命中普通词（"园区""上市"），宁缺勿错。
    if len(parts) < 2:
        return ""
    return "".join(parts)


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
    report_profile: dict[str, Any] | None = None,
    missing_inputs: list[dict[str, Any]] | None = None,
    skipped_fields: list[dict[str, Any]] | None = None,
    acceptance: dict[str, Any] | None = None,
    release_limitations: list[str] | None = None,
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
        # 所选报告配置随运行冻结：历史运行因此可重放，新配置只影响新运行。
        "report_profile": dict(report_profile or {}),
        "missing_inputs": [dict(item) for item in missing_inputs or []],
        "skipped_fields": [dict(item) for item in skipped_fields or []],
        # 分级验收状态。缺省是"未开始"而不是空字典：读的人不该从缺字段推断状态。
        "acceptance": dict(acceptance or _empty_acceptance()),
        "release_limitations": sorted({str(item) for item in release_limitations or []}),
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
