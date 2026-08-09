"""Assumption package construction from a parsed intent."""

from __future__ import annotations

from typing import Any


from .base import ASSUMPTION_PROFILE_VERSION
from .explicit_inputs import SOURCE_SENTENCE


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
    explicit: bool = False,
) -> dict[str, Any]:
    """Build one assumption field.

    ``explicit`` 标记句子里写明的参数：它不是行业种子，也不是用户事后确认，
    因此不得继续用 ``deterministic_industry_scenario_seed`` 作 method，
    也不得进入 confirmation_items 让用户再确认一遍已写明的数字。
    """

    ranking = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    priority_score = (
        ranking.get(sensitivity, 0)
        * ranking.get(uncertainty, 0)
        * ranking.get(decision_impact, 0)
    )
    if explicit:
        method = SOURCE_SENTENCE
    elif confirmed:
        method = "user_override"
    else:
        method = "deterministic_industry_scenario_seed"
    if explicit:
        validation_condition = "句中已写明的参数，仍需与后续原始材料做 hash 与数值一致性校验"
    elif confirmed:
        validation_condition = "已确认参数仍需与后续原始材料进行 hash 和数值一致性校验"
    else:
        validation_condition = "须确认参数，并以合同、测绘、报价或权属等材料替换"
    return {
        "name": name,
        "value": value,
        "range": {"low": low, "base": value, "high": high},
        "unit": unit,
        "period": "模型期",
        "source_type": "user_confirmed" if confirmed else "controlled_assumption",
        "source_ref": source_ref,
        "method": method,
        "confidence": 1.0 if (confirmed or explicit) else 0.42,
        "sensitivity": sensitivity,
        "uncertainty": uncertainty,
        "decision_impact": decision_impact,
        "confirmation_priority_score": 0 if (confirmed or explicit) else priority_score,
        "confirmed": confirmed,
        "validation_condition": validation_condition,
    }


def _build_assumption_package(intent: dict[str, Any]) -> dict[str, Any]:
    from lvke_mcp.domains.finance.industry_scenario_factory import build_industry_scenarios
    from lvke_mcp.servers.lvke_zero_material_delivery.industry_profiles import get_profile

    route = dict(intent["industry"])
    profile = get_profile(str(route["industry_code"]))
    scenarios = build_industry_scenarios(str(route["factory_industry"]))
    # 路由可显式指定原型；缺省时沿用行业首个原型。不显式指定会让
    # 轨道交通落到 transport_logistics 的首个原型（收费公路），
    # 与项目性质不符。
    wanted_archetype = str(route.get("factory_archetype") or "").strip()
    if wanted_archetype:
        narrowed = [
            item for item in scenarios
            if str(item.get("archetype_id") or item.get("archetype") or "") == wanted_archetype
        ]
        if narrowed:
            scenarios = narrowed
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
    # source_precedence 声明 sentence_explicit_input 高于行业种子，但此前
    # 无人执行：句中写明的 50 公里 / 10 站 / 2028-2032 年被种子覆盖。
    # 这里让明确输入改写同名字段，其余字段种子只作补缺。
    explicit = dict(intent.get("explicit_inputs") or {})
    applied: list[str] = []
    by_name = {str(item.get("name")): item for item in fields if isinstance(item, dict)}
    for name, spec in explicit.items():
        if not isinstance(spec, dict) or "value" not in spec:
            continue
        target = by_name.get(name)
        if target is None:
            fields.append(
                _assumption_field(
                    name,
                    spec.get("value"),
                    unit=str(spec.get("unit") or ""),
                    source_ref=SOURCE_SENTENCE,
                    sensitivity="critical",
                    uncertainty="low",
                    decision_impact="critical",
                    # 明确输入是给定值而非区间，low/high 取同值以示无展开。
                    low=spec.get("value"),
                    high=spec.get("value"),
                    explicit=True,
                )
            )
        else:
            value = spec.get("value")
            target["value"] = value
            target["source_ref"] = SOURCE_SENTENCE
            target["uncertainty"] = "low"
            # 区间在嵌套的 range 里，早期实现 pop 顶层 low/high 静默无效，
            # 于是 value=60 与 range.base=24 并存，读包的人同时看到两个数。
            # 明确输入是给定值而非区间，三档收敛到同一值以示无展开。
            target["range"] = {"low": value, "base": value, "high": value}
            # 方法与资格必须随来源一起更新，否则明确输入仍被标成行业种子，
            # 并因此继续出现在 confirmation_items 里要求用户确认已写明的参数。
            target["method"] = SOURCE_SENTENCE
            target["confidence"] = 1.0
            target["confirmation_priority_score"] = 0
            target["validation_condition"] = (
                "句中已写明的参数，仍需与后续原始材料做 hash 与数值一致性校验"
            )
        target_or_new = by_name.get(name) or fields[-1]
        target_or_new["source"] = SOURCE_SENTENCE
        target_or_new["explicit_raw"] = spec.get("raw")
        if spec.get("derivation"):
            target_or_new["derivation"] = spec.get("derivation")
        applied.append(name)
    unmapped = list(intent.get("explicit_input_unmapped") or [])
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
        "explicit_inputs_applied": applied,
        "explicit_input_unmapped": unmapped,
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
