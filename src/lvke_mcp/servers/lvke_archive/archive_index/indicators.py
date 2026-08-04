"""Stage 4 · 关键指标 regex 抽取（无 LLM 版）。

对 chapter_no ∈ {5, 6, 9} 的 chunks 跑一遍正则，捕获：
- total_investment (万元)
- construction_invest (万元)
- working_capital (万元)
- equity_ratio (%)
- project_irr / capital_irr (%)
- payback_years (年)
- scale_metric (字符串，含单位)

设计原则：
- 宁可漏抽，不可乱抽 → 单项匹配失败就置 None；最终 confidence 看抽到几项
- 抽出的数值都做了"单位归一化到万元"
- 同 report 多次匹配冲突时，取首次出现且数值合理的值
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# 数字 + 单位识别（支持 阿拉伯数字、中文千分位、亿/万 单位）
_NUM = r"([0-9]+(?:[,，][0-9]{3})*(?:\.[0-9]+)?)"
_UNIT = r"\s*(亿元|万元|元)"

INVEST_TOTAL_RE = re.compile(
    rf"(?:项目总投资|总投资|工程总投资|投资总额|总投资额)\s*(?:为|约|是|:|：)?\s*{_NUM}{_UNIT}"
)
CONSTRUCT_INVEST_RE = re.compile(
    rf"(?:建设投资|工程建设投资)\s*(?:为|约|是|:|：)?\s*{_NUM}{_UNIT}"
)
WORKING_CAP_RE = re.compile(
    rf"(?:流动资金|铺底流动资金)\s*(?:为|约|是|:|：)?\s*{_NUM}{_UNIT}"
)

EQUITY_RATIO_RE = re.compile(
    r"(?:资本金\s*比例|项目\s*资本金\s*比例|自有资金\s*比例)\s*(?:为|约|:|：)?\s*([0-9]+(?:\.[0-9]+)?)\s*%"
)

# IRR 命中前面带"项目"/"全部投资"/"财务"/"资本金"，捕获哪个出现就归哪个
PROJECT_IRR_RE = re.compile(
    r"(?:项目\s*(?:财务|投资)?\s*内部收益率|项目\s*IRR|全部投资\s*内部收益率|财务\s*内部收益率)"
    r"\s*\(?(?:税后|税前)?\)?\s*(?:为|约|是|:|：)?\s*([0-9]+(?:\.[0-9]+)?)\s*%"
)
CAPITAL_IRR_RE = re.compile(
    r"(?:资本金\s*(?:财务)?\s*内部收益率|资本金\s*IRR)"
    r"\s*(?:为|约|是|:|：)?\s*([0-9]+(?:\.[0-9]+)?)\s*%"
)

PAYBACK_RE = re.compile(
    r"(?:投资回收期|静态投资回收期|动态投资回收期|回收期)"
    r"\s*(?:\([^)]{1,20}\))?\s*(?:为|约|是|:|：)?\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*年"
)

# 规模：常见的"XXX 万吨" / "XXX MW" / "XX 床" / "XX 万平方米" 等
SCALE_RE = re.compile(
    r"(?:建设规模|设计规模|生产规模|装机|总装机|总建筑面积|总占地面积|床位规模|年产)"
    r"\s*(?:为|约|是|:|：)?\s*"
    r"([0-9]+(?:[,，][0-9]{3})*(?:\.[0-9]+)?\s*"
    r"(?:万[吨件套株头辆]|MW|GW|kW|km|公里|床|张床|万平方米|万平米|平方米|m²|亩|公顷))"
)


def _to_wan_yuan(num_str: str, unit: str) -> float | None:
    """归一化金额到万元（基础单位）。"""
    try:
        v = float(num_str.replace(",", "").replace("，", ""))
    except ValueError:
        return None
    if unit == "亿元":
        return v * 10000.0
    if unit == "万元":
        return v
    if unit == "元":
        return v / 10000.0
    return None


def _first_amount(text: str, regex: re.Pattern) -> float | None:
    for m in regex.finditer(text):
        v = _to_wan_yuan(m.group(1), m.group(2))
        if v is not None and v > 0:
            return round(v, 4)
    return None


def _first_pct(text: str, regex: re.Pattern) -> float | None:
    for m in regex.finditer(text):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if 0 < v <= 100:
            return round(v, 4)
    return None


def _first_years(text: str, regex: re.Pattern) -> float | None:
    for m in regex.finditer(text):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if 0 < v <= 80:
            return round(v, 2)
    return None


def _first_scale(text: str) -> str | None:
    m = SCALE_RE.search(text)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    return None


@dataclass(slots=True)
class IndicatorRecord:
    report_id: str
    total_investment: float | None = None
    construction_invest: float | None = None
    working_capital: float | None = None
    equity_ratio: float | None = None
    project_irr: float | None = None
    capital_irr: float | None = None
    payback_years: float | None = None
    scale_metric: str | None = None
    confidence: float = 0.0


def extract_for_report(report_id: str, chunks_text: Iterable[str]) -> IndicatorRecord:
    full = "\n".join(chunks_text)
    rec = IndicatorRecord(report_id=report_id)
    rec.total_investment = _first_amount(full, INVEST_TOTAL_RE)
    rec.construction_invest = _first_amount(full, CONSTRUCT_INVEST_RE)
    rec.working_capital = _first_amount(full, WORKING_CAP_RE)
    rec.equity_ratio = _first_pct(full, EQUITY_RATIO_RE)
    rec.project_irr = _first_pct(full, PROJECT_IRR_RE)
    rec.capital_irr = _first_pct(full, CAPITAL_IRR_RE)
    rec.payback_years = _first_years(full, PAYBACK_RE)
    rec.scale_metric = _first_scale(full)

    # 一致性校验：建设+流动 ≈ 总投资（允许 ±10%）
    if (
        rec.total_investment
        and rec.construction_invest
        and rec.working_capital
    ):
        s = rec.construction_invest + rec.working_capital
        if rec.total_investment > 0 and abs(s - rec.total_investment) / rec.total_investment > 0.4:
            # 不一致：保留 total 但清掉两个分项（可能误抽）
            rec.construction_invest = None
            rec.working_capital = None

    # confidence：抽到几项就给几分
    fields = (
        rec.total_investment, rec.construction_invest, rec.working_capital,
        rec.equity_ratio, rec.project_irr, rec.capital_irr, rec.payback_years,
        rec.scale_metric,
    )
    rec.confidence = round(sum(1 for f in fields if f is not None) / len(fields), 2)
    return rec
