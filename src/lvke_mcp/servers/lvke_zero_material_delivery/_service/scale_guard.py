"""Pre-run project-scale reconciliation across intent, assumptions and spec.

50 公里、10 座车站的轨道线按通用单体项目种子（约 11.6 亿元）运行时，算术
全部自洽，十三表也能通过勾稽，因此 ``finance_status`` 会显示 ok——但业务尺度
明显错误。本模块在 FinanceRun 之前做四方对账，尺度明显不匹配时返回
``project_scale_inconsistent`` 并阻止建 run。

设计约束：
- 只报不改。区间用于提示，绝不用区间自动改写用户或证据给定的值。
- 只在**双方都有值**时比较；缺值是 missing_inputs 问题，不在本检查范围。
- 阈值宽松（数量级级别），只拦明显错配，不做精度审查。
"""

from __future__ import annotations

from typing import Any

# 城市轨道交通投资强度经验区间（亿元/公里）。仅用于提示，不用于改值。
# 下限对应地面/高架简易制式，上限对应全地下高造价段。
URBAN_RAIL_INVEST_INTENSITY = (3.0, 12.0)

# 通用尺度失配容忍倍数：实际值与按强度推算值相差超过该倍数即视为明显错配。
_SCALE_TOLERANCE = 3.0

# 以下区间只用于"明显错配"判定与提示，不参与改值。范围刻意放宽到数量级级别：
# 拦住 50 公里配 3 座车站这类结构性错误，不做精度审查。
_STATION_SPACING_KM = (0.5, 8.0)
_BUILD_PERIOD_MONTHS = (6, 180)
_OPERATING_PERIOD_YEARS = (5, 100)


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _spec_scale_values(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Project the reconciliation dimensions out of a FinanceSpec/InputRevision."""

    source = dict(spec or {})
    revision = source.get("input_revision")
    revision = dict(revision) if isinstance(revision, dict) else {}
    inputs = source.get("finance_inputs")
    inputs = dict(inputs) if isinstance(inputs, dict) else {}
    financing = source.get("financing")
    financing = dict(financing) if isinstance(financing, dict) else {}

    def pick(*names: str) -> Any:
        for name in names:
            for holder in (source, revision, inputs, financing):
                if name in holder and holder.get(name) not in (None, ""):
                    return holder.get(name)
        return None

    # 只对账口径完全相同的维度。``total_investment_wan`` 在假设包里不含建设期
    # 利息，在 InputRevision 里已含利息，属于合法口径差，比较它会产生假阳性。
    return {
        "build_period_months": pick("build_period_months", "build_months"),
        "operating_period_years": pick("operating_period_years", "operating_years"),
        "loan_ratio": pick("loan_ratio", "debt_ratio", "financing_ratio"),
    }


def check_project_scale(
    *,
    industry_code: str,
    explicit_inputs: dict[str, Any] | None,
    field_values: dict[str, Any],
    project_context: dict[str, Any] | None = None,
    input_revision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``{"ok": bool, "issues": [...], "advisories": [...]}``.

    四方对账：DeliveryIntent 的明确输入、ProjectContext 的行业口径、
    AssumptionPackage 的字段取值、以及送进 FinanceRun 的 InputRevision。
    ``issues`` 非空即应阻断 FinanceRun；``advisories`` 只是提示。
    """

    issues: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    explicit = dict(explicit_inputs or {})
    context = dict(project_context or {})
    revision_values = _spec_scale_values(input_revision)
    reconciled: dict[str, Any] = {}

    def explicit_value(name: str) -> float | None:
        item = explicit.get(name)
        if isinstance(item, dict):
            return _number(item.get("value"))
        return _number(item)

    route_km = explicit_value("route_length_km")
    station_count = explicit_value("station_count")
    total_investment_wan = _number(field_values.get("total_investment_wan"))

    # 0. ProjectContext 的行业口径必须与 Intent 路由一致，否则后续所有
    #    行业参照都建立在错误的行业上。
    context_industry = str(context.get("industry_code") or "").strip()
    if context_industry and industry_code and context_industry != industry_code:
        issues.append({
            "code": "project_scale_inconsistent",
            "field": "industry_code",
            "detail": (
                f"DeliveryIntent 路由行业 {industry_code}，"
                f"ProjectContext 为 {context_industry}"
            ),
            "resolution": "重建 ProjectContext 使行业口径与交付意图一致",
        })

    # 1. 线路长度 × 投资强度 vs 总投资（仅轨道类且两者都有值时）
    if str(industry_code) == "urban_rail_transit" and route_km and total_investment_wan:
        low_wan = route_km * URBAN_RAIL_INVEST_INTENSITY[0] * 10000.0
        high_wan = route_km * URBAN_RAIL_INVEST_INTENSITY[1] * 10000.0
        if total_investment_wan * _SCALE_TOLERANCE < low_wan:
            issues.append({
                "code": "project_scale_inconsistent",
                "field": "total_investment_wan",
                "detail": (
                    f"线路长度 {route_km} 公里，按 "
                    f"{URBAN_RAIL_INVEST_INTENSITY[0]}~{URBAN_RAIL_INVEST_INTENSITY[1]} 亿元/公里"
                    f"推算投资约 {low_wan / 10000:.0f}~{high_wan / 10000:.0f} 亿元，"
                    f"当前 {total_investment_wan / 10000:.2f} 亿元，明显偏小"
                ),
                "observed_wan": total_investment_wan,
                "expected_range_wan": [round(low_wan, 2), round(high_wan, 2)],
                "resolution": "以工可批复或可比项目证据显式提供总投资，不得沿用通用行业种子",
            })
        elif total_investment_wan > high_wan * _SCALE_TOLERANCE:
            issues.append({
                "code": "project_scale_inconsistent",
                "field": "total_investment_wan",
                "detail": (
                    f"线路长度 {route_km} 公里，当前投资 "
                    f"{total_investment_wan / 10000:.2f} 亿元明显偏大"
                ),
                "observed_wan": total_investment_wan,
                "expected_range_wan": [round(low_wan, 2), round(high_wan, 2)],
                "resolution": "核对总投资口径或线路长度",
            })
        elif not (low_wan <= total_investment_wan <= high_wan):
            advisories.append({
                "code": "invest_intensity_outside_reference",
                "detail": (
                    f"投资强度 {total_investment_wan / max(route_km, 1e-9) / 10000:.2f} 亿元/公里"
                    f"落在参考区间 {URBAN_RAIL_INVEST_INTENSITY[0]}~"
                    f"{URBAN_RAIL_INVEST_INTENSITY[1]} 之外，需证据说明"
                ),
            })

    # 1b. 站数与线路长度的平均站间距必须落在物理可行范围内。
    if route_km and station_count and station_count > 1:
        spacing_km = route_km / station_count
        if not (_STATION_SPACING_KM[0] <= spacing_km <= _STATION_SPACING_KM[1]):
            issues.append({
                "code": "project_scale_inconsistent",
                "field": "station_count",
                "detail": (
                    f"{route_km} 公里设 {station_count:.0f} 座车站，平均站间距 "
                    f"{spacing_km:.2f} 公里，超出 {_STATION_SPACING_KM[0]}~"
                    f"{_STATION_SPACING_KM[1]} 公里的常规范围"
                ),
                "resolution": "核对线路长度与车站数量，或以线站位方案证据说明",
            })
        else:
            reconciled["average_station_spacing_km"] = round(spacing_km, 3)

    # 1c. 建设期与运营期的合理区间，以及建设期与年份区间的自洽。
    build_months = _number(field_values.get("build_period_months"))
    if build_months and not (
        _BUILD_PERIOD_MONTHS[0] <= build_months <= _BUILD_PERIOD_MONTHS[1]
    ):
        issues.append({
            "code": "project_scale_inconsistent",
            "field": "build_period_months",
            "detail": (
                f"建设期 {build_months:.0f} 个月超出 {_BUILD_PERIOD_MONTHS[0]}~"
                f"{_BUILD_PERIOD_MONTHS[1]} 个月的常规范围"
            ),
            "resolution": "核对建设期口径或以批复工期证据说明",
        })
    start_year = explicit_value("construction_start_year")
    end_year = explicit_value("construction_end_year")
    if build_months and start_year and end_year and end_year >= start_year:
        span_months = (end_year - start_year + 1) * 12
        if abs(span_months - build_months) > 12:
            issues.append({
                "code": "project_scale_inconsistent",
                "field": "build_period_months",
                "detail": (
                    f"建设年份 {start_year:.0f}-{end_year:.0f} 对应约 "
                    f"{span_months:.0f} 个月，建设期取值 {build_months:.0f} 个月"
                ),
                "resolution": "统一建设期与建设年份区间口径",
            })

    operating_years = _number(field_values.get("operating_period_years"))
    if operating_years and not (
        _OPERATING_PERIOD_YEARS[0] <= operating_years <= _OPERATING_PERIOD_YEARS[1]
    ):
        advisories.append({
            "code": "operating_period_outside_reference",
            "detail": (
                f"运营期 {operating_years:.0f} 年落在 {_OPERATING_PERIOD_YEARS[0]}~"
                f"{_OPERATING_PERIOD_YEARS[1]} 年参考区间之外，需证据说明"
            ),
        })

    # 1d. 融资比例必须是 0~1 的比率，且与资本金比例互补。
    loan_ratio = _number(field_values.get("loan_ratio"))
    if loan_ratio is not None and not (0.0 <= loan_ratio <= 1.0):
        issues.append({
            "code": "project_scale_inconsistent",
            "field": "loan_ratio",
            "detail": f"融资比例 {loan_ratio} 不是 0~1 的比率",
            "resolution": "以小数比率表达融资比例",
        })
    elif loan_ratio is not None:
        equity_ratio = _number(field_values.get("equity_ratio"))
        if equity_ratio is not None and abs(loan_ratio + equity_ratio - 1.0) > 0.01:
            issues.append({
                "code": "project_scale_inconsistent",
                "field": "loan_ratio",
                "detail": (
                    f"融资比例 {loan_ratio} 与资本金比例 {equity_ratio} 之和不为 1"
                ),
                "resolution": "核对资本金与债务比例口径",
            })

    # 2. 明确输入与最终取值是否一致（防止下游把明确值改写回种子）
    for name, item in explicit.items():
        if not isinstance(item, dict) or "value" not in item:
            continue
        stated = _number(item.get("value"))
        actual = _number(field_values.get(name))
        if stated is None or actual is None:
            continue
        if abs(stated - actual) > max(abs(stated) * 0.001, 1e-6):
            issues.append({
                "code": "explicit_input_overridden",
                "field": name,
                "detail": f"句子明确 {stated}，运行取值 {actual}",
                "resolution": "行业种子只能补缺，不得覆盖明确输入",
            })

    # 3. 送进 FinanceRun 的 InputRevision 必须与已对账的假设包一致，
    #    否则前面所有对账都可能被最后一步的漂移绕过。
    for name, revision_value in revision_values.items():
        expected = _number(field_values.get(name))
        actual = _number(revision_value)
        if expected is None or actual is None:
            continue
        if abs(expected - actual) > max(abs(expected) * 0.001, 1e-6):
            issues.append({
                "code": "input_revision_scale_drift",
                "field": name,
                "detail": f"假设包 {expected}，送入 FinanceRun 的取值 {actual}",
                "resolution": "以同一 AssumptionPackage 取值构建 InputRevision",
            })

    return {
        "ok": not issues,
        "issues": issues,
        "advisories": advisories,
        "reconciled": reconciled,
        "dimensions_checked": [
            "industry_code",
            "route_length_km",
            "station_count",
            "build_period_months",
            "operating_period_years",
            "loan_ratio",
            "total_investment_wan",
        ],
    }
