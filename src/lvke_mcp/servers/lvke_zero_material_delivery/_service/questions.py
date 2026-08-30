"""Deterministic gap computation for dynamic follow-up questions.

缺口按**所选报告配置**的 ``required_fields`` 计算，而不是按一张写死的字段表：
换配置就换追问集合，这是"配置化"必须传递到追问环节的部分。

每个缺口给出字段名、所属章节、是否关键、单位、允许范围、不回答时的受控假设来源，
以及对技术验收和正式候选的影响。缺这些的追问等于让用户猜"这个数为什么重要"。

跳过是被允许的：用户可以只回答关键项。跳过登记为 ``skipped``，进入报告披露、
manifest 与验收限制——但**关键字段跳过不得因此获得正式资格**，那条线由
``acceptance``/``promotion`` 的门禁执行，本模块只负责如实标注。
"""

from __future__ import annotations

from typing import Any

from .explicit_inputs import SOURCE_SENTENCE

#: 内置字段元数据。**只是**已知字段的默认值，不是判据的唯一来源：
#: profile 的 ``field_specs`` 优先，且 ``report_profiles`` 在加载期强制要求
#: 每个 required_field 都能取到元数据（内置或配置提供其一）。
#:
#: 此前这里是唯一来源，于是配置里写 ``custom_critical`` 这类新字段会静默拿到
#: critical=False、无单位、无范围——未回答也不触发正式候选门禁，等于把方案要求的
#: "关键字段未答不得取得正式资格"绕过去了。
#:
#: 只写确定性可校验的约束，不写"建议值"——把建议值写进范围会让校验把合理但超出
#: 建议的输入判成非法。
_FIELD_SPECS: dict[str, dict[str, Any]] = {
    "total_investment_wan": {"unit": "万元", "minimum": 0.01, "critical": True},
    "annual_revenue_wan": {"unit": "万元/年", "minimum": 0.0, "critical": True},
    "build_period_months": {"unit": "月", "minimum": 1, "maximum": 240, "critical": True},
    "operating_period_years": {"unit": "年", "minimum": 1, "maximum": 50, "critical": False},
    "loan_ratio": {"unit": "比例", "minimum": 0.0, "maximum": 0.95, "critical": False},
    "loan_rate": {"unit": "比例/年", "minimum": 0.0, "maximum": 0.25, "critical": False},
    "route_length_km": {"unit": "公里", "minimum": 0.1, "critical": True},
    "station_count": {"unit": "座", "minimum": 1, "critical": True},
    "design_speed_kmh": {"unit": "km/h", "minimum": 10, "maximum": 400, "critical": False},
    "annual_passenger_trips": {"unit": "万人次/年", "minimum": 0.0, "critical": True},
    "average_fare_yuan": {"unit": "元/人次", "minimum": 0.0, "critical": True},
    "installed_capacity_mw": {"unit": "MW", "minimum": 0.01, "critical": True},
    "annual_generation_mwh": {"unit": "MWh", "minimum": 0.0, "critical": True},
    "utilization_hours": {"unit": "小时", "minimum": 0.0, "maximum": 8760, "critical": False},
    "tariff_yuan_per_kwh": {"unit": "元/kWh", "minimum": 0.0, "critical": True},
    "purchase_price_wan": {"unit": "万元", "minimum": 0.01, "critical": True},
    "remaining_operating_years": {"unit": "年", "minimum": 1, "maximum": 50, "critical": False},
    "financing_ratio": {"unit": "比例", "minimum": 0.0, "maximum": 0.95, "critical": False},
    "interest_rate": {"unit": "比例/年", "minimum": 0.0, "maximum": 0.25, "critical": False},
    "minimum_dscr": {"unit": "倍", "minimum": 0.5, "maximum": 5.0, "critical": False},
    "project_name": {"unit": "", "critical": True},
    "region": {"unit": "", "critical": True},
    "industry_label": {"unit": "", "critical": True},
}

#: 由 Intent 而非 AssumptionPackage 承载的字段：它们的"已回答"判据不同。
_INTENT_FIELDS = {"project_name", "region", "industry_label", "project_nature", "report_type"}

_CRITICAL_IMPACT = (
    "关键字段。未回答时按受控假设取值：技术验收可通过但必然带限制项，"
    "且不得据此取得正式候选资格。"
)
_OPTIONAL_IMPACT = (
    "非关键字段。未回答时按行业种子取值，计入交付限制，不单独阻断技术验收。"
)


#: profile 里 ``field_specs.<字段>`` 允许声明的键。
FIELD_SPEC_KEYS = ("critical", "unit", "minimum", "maximum")


def field_spec(profile: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Resolve one field's metadata: profile declaration first, built-in table next.

    返回 ``None`` 表示两处都没有元数据——调用方（配置加载期）据此阻断，而不是
    默认成"非关键、无单位、无范围"。
    """

    declared = dict(profile.get("field_specs") or {}).get(str(name))
    if isinstance(declared, dict):
        spec = {
            key: declared[key] for key in FIELD_SPEC_KEYS if key in declared
        }
        # 配置可只覆盖部分键，其余回落到内置默认值。
        for key, value in dict(_FIELD_SPECS.get(str(name)) or {}).items():
            spec.setdefault(key, value)
        if "critical" in spec:
            return spec
        return None
    builtin = _FIELD_SPECS.get(str(name))
    return dict(builtin) if builtin else None


def fields_without_metadata(profile: dict[str, Any]) -> list[str]:
    """Return required fields whose criticality/unit/range are undeclared."""

    return sorted(
        {
            str(name)
            for name in profile.get("required_fields") or []
            if field_spec(profile, str(name)) is None
        }
    )


def _chapter_index(profile: dict[str, Any]) -> dict[str, str]:
    """Map each slot name to the first chapter/section that consumes it."""

    index: dict[str, str] = {}
    for chapter in profile.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        chapter_title = str(chapter.get("title") or "")
        for sub in chapter.get("subs") or []:
            if not isinstance(sub, dict):
                continue
            location = f"{chapter_title} / {str(sub.get('title') or '')}"
            for slot in sub.get("slots") or []:
                index.setdefault(str(slot), location)
    return index


def compute_missing_inputs(
    *,
    profile: dict[str, Any],
    intent: dict[str, Any],
    assumption_package: dict[str, Any] | None = None,
    skipped: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return prioritized structured gaps for the selected report profile.

    "已回答"的判据分三种：句中明确写出（``sentence_explicit_input``）、用户确认过
    （``confirmed``）、或 Intent 上已有非占位值。仅由行业种子填出的值**不算**已回答
    ——它恰恰是需要用户确认的那一类。
    """

    package = dict(assumption_package or {})
    fields = [item for item in package.get("fields") or [] if isinstance(item, dict)]
    answered: set[str] = set()
    seeded: dict[str, Any] = {}
    # 句子里已写明的参数算已回答。此前只看 AssumptionPackage 与 Intent 顶层字段，
    # 于是"50公里10座车站"这类明确输入在创建期（假设包尚未生成）会被重复追问——
    # 而 source_precedence 明确声明 sentence_explicit_input 高于行业种子。
    explicit = dict(intent.get("explicit_inputs") or {})
    for name, spec in explicit.items():
        if isinstance(spec, dict) and spec.get("value") not in (None, ""):
            answered.add(str(name))
            seeded.setdefault(str(name), spec.get("value"))
    for item in fields:
        name = str(item.get("name") or "")
        if not name:
            continue
        seeded[name] = item.get("value")
        if item.get("confirmed") or str(item.get("source_ref") or "") == SOURCE_SENTENCE:
            answered.add(name)
    for name in _INTENT_FIELDS:
        value = str(intent.get(name) or "").strip()
        if name == "industry_label":
            value = str(dict(intent.get("industry") or {}).get("industry_label") or "").strip()
        if value and value != "待确认":
            answered.add(name)

    skipped_names = {
        str(item.get("field") or "")
        for item in (skipped or [])
        if isinstance(item, dict)
    }
    locations = _chapter_index(profile)
    rows: list[dict[str, Any]] = []
    for name in [str(item) for item in profile.get("required_fields") or []]:
        if name in answered:
            continue
        resolved = field_spec(profile, name)
        # 元数据缺失时**按关键字段处理**（fail-closed）。配置加载期已经挡住这种
        # 配置，这里是第二道：未知字段宁可多问一次、多阻断一次，也不能静默降级成
        # 可选项而让正式门禁失效。
        spec = dict(resolved or {"critical": True})
        critical = bool(spec.get("critical", True))
        fallback = (
            "sentence_explicit_input"
            if name in answered
            else (
                "deterministic_industry_scenario_seed"
                if name in seeded
                else "controlled_assumption_pending"
            )
        )
        row = {
            "field": name,
            "section": locations.get(name, "未在配置章节中直接引用"),
            "critical": critical,
            "unit": str(spec.get("unit") or ""),
            "controlled_assumption_source": fallback,
            "current_seed_value": seeded.get(name),
            "impact": _CRITICAL_IMPACT if critical else _OPTIONAL_IMPACT,
            "status": "skipped" if name in skipped_names else "pending",
            "priority": 1 if critical else 2,
        }
        for bound in ("minimum", "maximum"):
            if bound in spec:
                row[bound] = spec[bound]
        rows.append(row)
    rows.sort(key=lambda item: (item["priority"], item["field"]))
    return rows


def summarize_gaps(missing_inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold gaps into the counters that acceptance and disclosure both read."""

    pending = [item for item in missing_inputs if item.get("status") == "pending"]
    skipped = [item for item in missing_inputs if item.get("status") == "skipped"]
    critical_open = [item for item in pending + skipped if item.get("critical")]
    return {
        "missing_input_count": len(missing_inputs),
        "pending_count": len(pending),
        "skipped_count": len(skipped),
        "critical_unanswered_fields": sorted(
            str(item.get("field") or "") for item in critical_open
        ),
        # 关键字段未回答仍允许技术预览，但必须留下这条限制：正式候选门禁读它。
        "release_limitations": sorted(
            {
                f"required_field_unanswered:{item.get('field')}"
                for item in critical_open
            }
            | {
                f"required_field_skipped:{item.get('field')}"
                for item in skipped
                if not item.get("critical")
            }
        ),
    }


__all__ = ["compute_missing_inputs", "summarize_gaps"]
