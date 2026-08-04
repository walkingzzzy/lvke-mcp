"""Deterministic report and finance/report consistency checks."""

from __future__ import annotations

import re
from calendar import monthrange
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.servers.lvke_deliverable_review import rules


REPORT_RULES = {
    "REPORT.SECTIONS.COMPLETE",
    "REPORT.CLAIM.EVIDENCE",
    "REPORT.NUMBERS.BOUND",
    "REPORT.INTERNAL.CONSISTENCY",
    "REPORT.REFERENCES.FRESH",
}
COMBINED_RULES = {"COMBINED.NUMBERS.MATCH", "COMBINED.CONCLUSIONS.MATCH"}

_NUMBER_PATTERN = re.compile(
    r"(?P<number>-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*(?:\+|以上|余|多)?\s*"
    r"(?P<unit>亿元|万元|平方米|平方|㎡|万吨/年|万吨|吨/年|公里|千米|km|KM|个月|%|％|元|间|吨|年|月)"
)
_PERIOD_PATTERN = re.compile(
    r"(?:20\d{2}年(?:\d{1,2}月)?|第\d+(?:期|年)|(?<![\d.])\d+年|建设期|运营期|达产年)"
)
_COMPANY_PATTERN = re.compile(r"([\u4e00-\u9fffA-Za-z0-9（）()]{2,50}(?:有限责任公司|股份有限公司|有限公司|酒店管理公司|酒店|中心))")

_METRIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("project_irr", re.compile(r"项目(?:投资)?(?:财务)?内部收益率|财务内部收益率|项目\s*IRR|(?<![A-Za-z])IRR(?![A-Za-z])", re.I)),
    ("capital_irr", re.compile(r"资本金(?:财务)?内部收益率|资本金\s*IRR", re.I)),
    ("npv", re.compile(r"财务净现值|净现值|(?<![A-Za-z])NPV(?![A-Za-z])", re.I)),
    ("dscr", re.compile(r"偿债备付率|\bDSCR\b", re.I)),
    ("icr", re.compile(r"利息备付率|\bICR\b", re.I)),
    ("dynamic_payback", re.compile(r"动态(?:投资)?回收期", re.I)),
    ("static_payback", re.compile(r"静态(?:投资)?回收期", re.I)),
    ("payback", re.compile(r"投资回收期|回收期", re.I)),
    ("discount_rate", re.compile(r"折现率|基准收益率", re.I)),
    ("bep", re.compile(r"盈亏平衡点|\bBEP\b", re.I)),
    ("total_investment", re.compile(r"项目总投资|总投资", re.I)),
    ("construction_investment", re.compile(r"建设投资|三段合计", re.I)),
    ("construction_interest", re.compile(r"建设期(?:贷款)?利息", re.I)),
    ("working_capital", re.compile(r"流动资金|营运资金", re.I)),
    ("capital", re.compile(r"项目资本金|资本金|企业自筹|自有资金|自筹资金", re.I)),
    ("debt", re.compile(r"银行贷款|贷款金额|借款金额|债务融资|借款", re.I)),
    ("revenue", re.compile(r"营业收入|销售收入|年收入|营收", re.I)),
    ("revenue_component", re.compile(r"租赁及广告收入|租赁广告收入|其他收入", re.I)),
    ("operating_cost", re.compile(r"现金经营成本|现金运营成本|经营成本|运营成本|营业成本", re.I)),
    ("total_cost", re.compile(r"总成本费用|总成本", re.I)),
    ("net_profit", re.compile(r"净利润", re.I)),
    ("income_tax", re.compile(r"企业所得税|所得税", re.I)),
    ("depreciation", re.compile(r"年折旧|折旧(?:费|额)?", re.I)),
    ("profit", re.compile(r"利润总额|利润", re.I)),
    ("annual_use", re.compile(r"年使用|年度使用|年投入|年度投入|年度资金使用", re.I)),
    ("engineering_cost", re.compile(r"工程费用合计|工程费合计", re.I)),
    ("civil_cost", re.compile(r"建筑工程费|土建工程费", re.I)),
    ("equipment_cost", re.compile(r"设备及工器具购置费|设备购置费|设备费", re.I)),
    ("installation_cost", re.compile(r"安装工程费|安装费", re.I)),
    ("other_investment_cost", re.compile(r"工程建设其他费", re.I)),
    ("contingency", re.compile(r"基本预备费|预备费", re.I)),
    ("wage_cost", re.compile(r"工资及福利|工资福利", re.I)),
    ("average_wage", re.compile(r"人均年工资|人均工资", re.I)),
    ("salary_cost", re.compile(r"工资约|工资额|基本工资", re.I)),
    ("welfare_cost", re.compile(r"福利约|福利费|福利额", re.I)),
    ("maintenance_cost", re.compile(r"设备维护|维修费|维护费", re.I)),
    ("utility_cost", re.compile(r"水电能源|能源费|水电费", re.I)),
    ("insurance_cost", re.compile(r"保险费|保险", re.I)),
    ("marketing_cost", re.compile(r"营销费|营销", re.I)),
    ("lease_cost", re.compile(r"场地使用及租赁|场地租赁|租赁费", re.I)),
    ("management_cost", re.compile(r"管理费用|管理费", re.I)),
    ("ticket_price", re.compile(r"平均门票|门票价格|门票", re.I)),
    ("secondary_spend", re.compile(r"二次消费|人均二消", re.I)),
    ("room_count", re.compile(r"客房|房间", re.I)),
    ("area", re.compile(r"建筑面积|占地面积|用地面积|面积", re.I)),
    ("capacity", re.compile(r"年产量|设计产能|生产能力|产能|销量", re.I)),
    ("price", re.compile(r"销售单价|采购单价|单价|价格", re.I)),
    ("market_radius", re.compile(r"市场半径|运输半径|辐射半径", re.I)),
)

_FINANCIAL_METRICS = {
    "project_irr", "capital_irr", "npv", "dscr", "icr", "payback",
    "static_payback", "dynamic_payback",
    "discount_rate", "bep",
    "total_investment", "construction_investment", "working_capital", "capital",
    "construction_interest", "debt", "revenue", "operating_cost", "total_cost",
    "net_profit", "income_tax", "depreciation", "profit", "annual_use",
    "engineering_cost", "civil_cost", "equipment_cost", "installation_cost", "other_investment_cost",
    "contingency", "wage_cost", "maintenance_cost", "utility_cost", "insurance_cost",
    "marketing_cost", "lease_cost", "management_cost", "salary_cost", "welfare_cost",
}

_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("project_irr", re.compile(r"(?:project_irr|project.*irr)(?:_pct)?", re.I)),
    ("capital_irr", re.compile(r"(?:capital_irr|equity_irr)(?:_pct)?", re.I)),
    ("npv", re.compile(r"(?:^|[.\[])npv(?:_wan)?(?:$|[.\[])|net_present_value", re.I)),
    ("dscr", re.compile(r"(?:^|[.\[])dscr(?:$|[.\[])|debt_service_coverage", re.I)),
    ("icr", re.compile(r"(?:^|[.\[])icr(?:$|[.\[])|interest_coverage", re.I)),
    ("dynamic_payback", re.compile(r"dynamic_payback", re.I)),
    ("static_payback", re.compile(r"static_payback", re.I)),
    ("payback", re.compile(r"(?<!dynamic_)(?<!static_)payback", re.I)),
    ("discount_rate", re.compile(r"benchmark_rate|discount_rate", re.I)),
    ("bep", re.compile(r"bep(?:_pct)?", re.I)),
    ("total_investment", re.compile(r"total_investment|investment\.total(?:$|[.\[])|total_inv", re.I)),
    ("construction_investment", re.compile(r"construction_investment|investment\.construction", re.I)),
    ("construction_interest", re.compile(r"construction_interest|investment\.interest|interest_during_construction", re.I)),
    ("working_capital", re.compile(r"working_capital|investment\.working", re.I)),
    ("capital", re.compile(r"(?:funding\.)?(?:capital|equity)(?:_wan)?(?:$|[.\[])|capital_fund", re.I)),
    ("debt", re.compile(r"(?:funding\.)?(?:loan|debt)(?:_wan)?(?:$|[.\[])|borrow", re.I)),
    ("revenue", re.compile(r"(?:^|[.\[])revenue(?:$|[.\[])|annual_revenue", re.I)),
    ("operating_cost", re.compile(r"operating_cost|op_cash_cost", re.I)),
    ("total_cost", re.compile(r"total_cost|indicators\.op_cost", re.I)),
    ("net_profit", re.compile(r"net_profit", re.I)),
    ("income_tax", re.compile(r"income_tax", re.I)),
    ("depreciation", re.compile(r"annual_depreciation|indicators\.depreciation", re.I)),
    ("profit", re.compile(r"profit_total|total_profit|profit_before|(?:^|[.\[])profit(?:$|[.\[])", re.I)),
    ("annual_use", re.compile(r"(?:funding_annual_schedule|financial_plan).*?(?:finance_in|invest_out)", re.I)),
    ("engineering_cost", re.compile(r"breakdown_detail\.engineering_total", re.I)),
    ("other_investment_cost", re.compile(r"breakdown_detail\.other|invest_breakdown\.other_items|invest_breakdown\.other_wan", re.I)),
    ("contingency", re.compile(r"breakdown_detail\.contingency|invest_breakdown\.contingency_items|invest_breakdown\.reserve_wan", re.I)),
    ("wage_cost", re.compile(r"cost_items\.工资|wage_wan", re.I)),
    ("salary_cost", re.compile(r"annual\.wage\[\d+\]\.wage$", re.I)),
    ("welfare_cost", re.compile(r"annual\.wage\[\d+\]\.welfare$", re.I)),
    ("maintenance_cost", re.compile(r"cost_items\.(?:设备维护|维修|维护)", re.I)),
    ("utility_cost", re.compile(r"cost_items\.(?:水电能源|能源|水电)", re.I)),
    ("insurance_cost", re.compile(r"cost_items\.保险", re.I)),
    ("marketing_cost", re.compile(r"cost_items\.营销", re.I)),
    ("lease_cost", re.compile(r"cost_items\.(?:场地使用及租赁|场地租赁|租赁)", re.I)),
    ("management_cost", re.compile(r"cost_items\.管理", re.I)),
)

_DEFAULT_SECTION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("项目概况", ("项目概况", "总论", "项目背景")),
    ("市场分析", ("市场分析", "市场需求", "市场预测")),
    ("建设或技术方案", ("建设方案", "技术方案", "工程方案", "实施方案")),
    ("投资与融资", ("投资估算", "资金筹措", "融资方案", "投资与融资")),
    ("财务分析", ("财务分析", "财务评价", "经济评价", "偿债分析")),
    ("风险分析", ("风险分析", "风险识别", "风险与对策")),
    ("结论与建议", ("结论与建议", "研究结论", "结论")),
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _canonical_unit(unit: str) -> str:
    raw = str(unit or "").strip()
    if raw in {"%", "％"}:
        return "%"
    if raw in {"平方米", "平方", "㎡"}:
        return "㎡"
    if raw in {"公里", "千米", "km", "KM"}:
        return "公里"
    if raw in {"万吨", "吨"}:
        return "吨"
    if raw in {"万吨/年", "吨/年"}:
        return "吨/年"
    if raw in {"亿元", "万元", "元"}:
        return "万元"
    return raw


def _canonical_value(value: float, unit: str) -> float:
    if unit == "亿元":
        return value * 10000.0
    if unit == "元":
        return value / 10000.0
    if unit in {"万吨", "万吨/年"}:
        return value * 10000.0
    return value


def _semantic(text: str) -> str:
    for name, pattern in _METRIC_PATTERNS:
        if pattern.search(text):
            return name
    return ""


def _metric_unit_compatible(metric: str, unit: str) -> bool:
    canonical = _canonical_unit(unit)
    if metric in {
        "total_investment", "construction_investment", "working_capital",
        "capital", "debt", "revenue", "operating_cost", "total_cost",
        "net_profit", "profit", "npv", "price", "construction_interest",
        "income_tax", "depreciation", "annual_use", "engineering_cost", "civil_cost",
        "equipment_cost", "installation_cost", "other_investment_cost",
        "contingency", "wage_cost", "maintenance_cost", "utility_cost",
        "insurance_cost", "marketing_cost", "lease_cost", "management_cost",
        "salary_cost", "welfare_cost",
    }:
        return canonical == "万元"
    if metric in {"project_irr", "capital_irr", "discount_rate", "bep"}:
        return canonical == "%"
    if metric in {"payback", "static_payback", "dynamic_payback"}:
        return canonical in {"年", "月", "个月"}
    if metric in {"dscr", "icr"}:
        return canonical == "%"
    if metric == "room_count":
        return canonical == "间"
    if metric == "area":
        return canonical == "㎡"
    if metric == "capacity":
        return canonical in {"吨", "吨/年"}
    if metric == "market_radius":
        return canonical == "公里"
    return True


def _semantic_near(text: str, start: int, end: int, unit: str) -> str:
    candidates: list[tuple[int, int, int, str]] = []
    clause_start = max(
        (text.rfind(marker, 0, start) for marker in ("，", ",", "；", ";", "。")),
        default=-1,
    ) + 1
    following = [
        position for marker in ("，", ",", "；", ";", "。")
        if (position := text.find(marker, end)) >= 0
    ]
    clause_end = min(following) if following else len(text)
    for order, (name, pattern) in enumerate(_METRIC_PATTERNS):
        if not _metric_unit_compatible(name, unit):
            continue
        for match in pattern.finditer(text):
            if match.start() < clause_start or match.end() > clause_end:
                continue
            if match.end() <= start:
                distance = start - match.end()
                direction = 0
            elif match.start() >= end:
                distance = match.start() - end
                direction = 1
                # 财务句式通常是“指标+数值”。后置关键词可作回退，但
                # 不应抢走前一分句已明确标注的数值。“8%折现率”
                # 这种紧邻后置标签则应优先。
                if distance > 3:
                    distance += 24
            else:
                distance = 0
                direction = 0
            candidates.append((distance, direction, order, name))
    return min(candidates)[3] if candidates else ""


def _period_near(text: str, start: int, end: int) -> str:
    candidates: list[tuple[int, int, str]] = []
    for match in _PERIOD_PATTERN.finditer(text):
        # Do not treat the claim's own duration (for example 8.04年) as its period.
        if match.start() < end and match.end() > start:
            continue
        preceding = match.end() <= start
        distance = start - match.end() if preceding else match.start() - end
        candidates.append((distance + (0 if preceding else 12), 0 if preceding else 1, match.group(0)))
    return min(candidates, default=(0, 0, ""))[2]


def _flatten_numbers(
    value: Any, path: str = "root", output: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if output is None:
        output = []
    if isinstance(value, dict):
        for key, child in value.items():
            _flatten_numbers(child, f"{path}.{key}", output)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _flatten_numbers(child, f"{path}[{index}]", output)
    else:
        numeric = _number(value)
        if numeric is not None:
            output.append({"path": path, "value": numeric})
    return output


def semantic_finance_index(run: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {name: [] for name in _FINANCIAL_METRICS}
    for row in _flatten_numbers(run):
        path = str(row["path"])
        for metric, pattern in _PATH_PATTERNS:
            if pattern.search(path):
                index.setdefault(metric, []).append(row)
                break

    def add(metric: str, value: Any, path: str) -> None:
        numeric = _number(value)
        if numeric is None:
            return
        row = {"path": path, "value": numeric}
        if not any(
            existing.get("path") == path and existing.get("value") == numeric
            for existing in index.setdefault(metric, [])
        ):
            index[metric].append(row)

    breakdown = ((run.get("raw") or {}).get("invest_breakdown") or {})
    for item_index, item in enumerate(breakdown.get("construction_items") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        category = str(item.get("category") or "")
        metric = (
            "equipment_cost" if category == "equipment" or "设备" in name
            else "installation_cost" if category == "installation" or "安装" in name
            else "civil_cost"
        )
        add(metric, item.get("amount_wan"), f"root.raw.invest_breakdown.construction_items[{item_index}].amount_wan")
    investment = run.get("investment") or {}
    breakdown_detail = investment.get("breakdown_detail") or {}
    add(
        "engineering_cost",
        breakdown_detail.get("engineering_total"),
        "root.investment.breakdown_detail.engineering_total",
    )

    raw = run.get("raw") or {}
    operating_cost_series = [
        value for value in (raw.get("operating_cost_by_year") or [])
        if _number(value) is not None
    ]
    if operating_cost_series:
        add("operating_cost", max(float(value) for value in operating_cost_series), "root.raw.operating_cost_by_year.max")
    elif isinstance(raw.get("cost_items"), dict):
        add(
            "operating_cost",
            sum(float(value) for value in raw["cost_items"].values() if _number(value) is not None),
            "root.raw.cost_items.total",
        )

    # Preserve each construction-year use rather than confusing it with working capital.
    for row_index, row in enumerate((run.get("annual") or {}).get("financial_plan") or []):
        if isinstance(row, dict):
            add("annual_use", row.get("invest_out"), f"root.annual.financial_plan[{row_index}].invest_out")
    return {key: value for key, value in index.items() if value}


def build_claim_graph(content: str, *, target_id: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    section = ""
    paragraph_index = 0
    table_row_index = 0
    for line_index, raw_line in enumerate(str(content or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading:
            section = heading.group(1).strip()
            continue
        chinese_heading = re.match(r"^(?:第[一二三四五六七八九十百0-9]+章|[一二三四五六七八九十]+[、.])\s*(.+)$", line)
        if chinese_heading and len(line) <= 80:
            section = chinese_heading.group(1).strip()
        is_table = line.startswith("|") and line.endswith("|")
        if is_table and re.fullmatch(r"[|:\-\s]+", line):
            continue
        if is_table:
            table_row_index += 1
            container = "table"
            position = table_row_index
        else:
            paragraph_index += 1
            container = "paragraph"
            position = paragraph_index
        for match in _NUMBER_PATTERN.finditer(line):
            raw_value = float(match.group("number").replace(",", ""))
            raw_unit = match.group("unit")
            value = _canonical_value(raw_value, raw_unit)
            unit = _canonical_unit(raw_unit)
            context_start = max(0, match.start() - 40)
            context_end = min(len(line), match.end() + 40)
            context = line[context_start:context_end]
            metric = _semantic_near(line, match.start(), match.end(), raw_unit)
            period = _period_near(line, match.start(), match.end())
            if unit == "间" and not metric:
                metric = "room_count"
            if unit == "㎡" and not metric:
                metric = "area"
            if unit == "公里" and not metric:
                metric = "market_radius"
            if unit in {"吨", "吨/年"} and not metric:
                metric = "capacity"
            if unit == "年" and 1900 <= value <= 2100 and re.search(r"(?:20\d{2})年", match.group(0)):
                claim_type = "date"
            elif metric in _FINANCIAL_METRICS:
                claim_type = "financial"
            elif metric:
                claim_type = "operating"
            else:
                claim_type = "quantitative"
            location = {
                "target_id": target_id,
                "section": section,
                "container": container,
                "paragraph": position if container == "paragraph" else None,
                "table_row": position if container == "table" else None,
                "line": line_index,
                "char_offset_start": match.start(),
                "char_offset_end": match.end(),
                "text_anchor": line[:160],
            }
            claim_id = "clm_" + sha256_json({
                "target_id": target_id,
                "line": line_index,
                "span": match.span(),
                "text": line,
            }).removeprefix("sha256:")[:24]
            claims.append({
                "claim_id": claim_id,
                "text": match.group(0),
                "context": context,
                "claim_type": claim_type,
                "metric": metric,
                "value": value,
                "unit": unit,
                "raw_value": raw_value,
                "raw_unit": raw_unit,
                "period": period,
                "location": location,
            })
    return claims


def _within_tolerance(actual: float, expected: float, *, metric: str = "") -> bool:
    tolerance = max(0.01, abs(expected) * (0.005 if metric in {"project_irr", "capital_irr"} else 1e-6))
    return abs(actual - expected) <= tolerance


def _claim_run_matches(claim: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    metric = str(claim.get("metric") or "")
    value = float(claim.get("value") or 0.0)
    matches: list[dict[str, Any]] = []
    for candidate in index.get(metric) or []:
        candidate_value = float(candidate["value"])
        candidate_values = [candidate_value]
        if claim.get("unit") == "%" and abs(candidate_value) <= 1.0:
            candidate_values.append(candidate_value * 100.0)
        if any(_within_tolerance(value, item, metric=metric) for item in candidate_values):
            matches.append(candidate)
    return matches


def _source_timestamp(source: dict[str, Any]) -> str:
    for key in ("fetched_at", "retrieved_at", "captured_at", "created_at"):
        if source.get(key):
            return str(source[key])
    for locator in source.get("locators") or []:
        if not isinstance(locator, dict):
            continue
        for key in ("fetched_at", "retrieved_at", "captured_at", "created_at"):
            if locator.get(key):
                return str(locator[key])
    return ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _evidence_catalog(packs: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for pack in packs:
        payload = pack.get("payload") or {}
        pack_id = str(pack.get("object_id") or pack.get("evidence_pack_id") or "")
        pack_candidate_set_id = str(payload.get("candidate_set_id") or "")
        pack_server_signed = payload.get("server_signed_candidates") is True
        pack_formal = payload.get("formal_evidence_candidate") is True
        pack_track = str(payload.get("evidence_track") or "real")
        pack_fixture = payload.get("technical_fixture_candidate") is True
        fixture_manifest = payload.get("fixture_manifest") or {}
        by_source = {
            str(source.get("source_id") or ""): source
            for source in (payload.get("sources") or [])
            if isinstance(source, dict)
        }
        for source in by_source.values():
            sources.append({
                **deepcopy(source),
                "evidence_pack_id": pack_id,
                "_pack_candidate_set_id": pack_candidate_set_id,
                "_pack_server_signed_candidates": pack_server_signed,
                "_pack_formal_evidence_candidate": pack_formal,
                "_pack_evidence_track": pack_track,
                "_pack_technical_fixture_candidate": pack_fixture,
                "_pack_fixture_manifest": deepcopy(fixture_manifest),
            })
        for raw in payload.get("fact_candidates") or []:
            if not isinstance(raw, dict):
                continue
            source = by_source.get(str(raw.get("source_id") or "")) or {}
            candidates.append({
                **deepcopy(raw),
                "evidence_pack_id": pack_id,
                "source": deepcopy(source),
                "_pack_candidate_set_id": pack_candidate_set_id,
                "_pack_server_signed_candidates": pack_server_signed,
                "_pack_formal_evidence_candidate": pack_formal,
                "_pack_evidence_track": pack_track,
                "_pack_technical_fixture_candidate": pack_fixture,
                "_pack_fixture_manifest": deepcopy(fixture_manifest),
            })
    return candidates, sources


def _formal_evidence_candidate(candidate: dict[str, Any]) -> bool:
    source = candidate.get("source") or {}
    content_hash = str(source.get("content_hash") or "")
    return bool(
        candidate.get("_pack_candidate_set_id")
        and candidate.get("_pack_server_signed_candidates") is True
        and candidate.get("_pack_formal_evidence_candidate") is True
        and candidate.get("formal_use_allowed") is True
        and source.get("formal_use_allowed") is True
        and re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", content_hash)
        and isinstance(candidate.get("locator"), dict)
        and candidate.get("locator")
    )


def _formal_evidence_source(source: dict[str, Any]) -> bool:
    content_hash = str(source.get("content_hash") or "")
    locators = source.get("locators") or []
    return bool(
        source.get("_pack_candidate_set_id")
        and source.get("_pack_server_signed_candidates") is True
        and source.get("_pack_formal_evidence_candidate") is True
        and source.get("formal_use_allowed") is True
        and re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", content_hash)
        and isinstance(locators, list)
        and locators
    )


def _technical_fixture_candidate(candidate: dict[str, Any]) -> bool:
    source = candidate.get("source") or {}
    manifest = candidate.get("_pack_fixture_manifest") or {}
    content_hash = str(source.get("content_hash") or "")
    source_id = str(candidate.get("source_id") or "")
    manifest_hashes = manifest.get("content_hashes") or {}
    return bool(
        candidate.get("_pack_evidence_track") == "technical_fixture"
        and candidate.get("_pack_server_signed_candidates") is True
        and candidate.get("_pack_technical_fixture_candidate") is True
        and candidate.get("_pack_formal_evidence_candidate") is not True
        and source_id in set(manifest.get("source_snapshot_ids") or [])
        and str(manifest_hashes.get(source_id) or "").removeprefix("sha256:")
        == content_hash.removeprefix("sha256:")
        and re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", content_hash)
        and isinstance(candidate.get("locator"), dict)
        and candidate.get("locator")
    )


def _candidate_metric(candidate: dict[str, Any]) -> str:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("field", "metric", "matched_alias", "excerpt")
    )
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", text.lower())
    if re.search(r"(?:room.*count|count.*room|hotel.*room|guest.*room)|(?:客房|房间)(?:数|数量|总数)", normalized):
        return "room_count"
    if re.search(r"(?:market|transport|service).*(?:radius|distance)|(?:radius|distance).*(?:market|transport|service)|市场半径|运输半径|辐射半径|经营范围", normalized):
        return "market_radius"
    if re.search(r"(?:asset|land|building|construction|property).*(?:area|scale)|(?:area|scale).*(?:asset|land|building|construction|property)|资产边界面积|土地面积|建筑面积|建设规模", normalized):
        return "area"
    return _semantic(text)


def _candidate_scope(candidate: dict[str, Any], metric: str) -> str:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("field", "metric", "matched_alias")
    ).lower()
    if metric == "room_count":
        return "total"
    if metric != "area":
        return ""
    if "asset_boundary" in text or "资产边界" in text or "收购范围" in text:
        return "asset_boundary"
    if "land" in text or "土地" in text or "占地" in text or "用地" in text:
        return "land"
    if "construction" in text or "施工" in text or "建设规模" in text:
        return "construction"
    if "building" in text or "property" in text or "建筑" in text or "房屋" in text:
        return "building"
    return "area"


def _candidate_location(candidate: dict[str, Any], *, target_id: str) -> dict[str, Any]:
    source = candidate.get("source") or {}
    return {
        "target_id": target_id,
        "evidence_pack_id": candidate.get("evidence_pack_id"),
        "source_id": candidate.get("source_id"),
        "candidate_id": candidate.get("candidate_id"),
        "content_hash": source.get("content_hash"),
        "locator": deepcopy(candidate.get("locator") or {}),
        "text_anchor": str(
            candidate.get("excerpt") or candidate.get("original_value") or ""
        )[:160],
    }


def _evidence_claims(
    candidates: list[dict[str, Any]], *, target_id: str,
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for candidate in candidates:
        if not _formal_evidence_candidate(candidate):
            continue
        numeric = _number(candidate.get("numeric_value"))
        metric = _candidate_metric(candidate)
        raw_unit = str(candidate.get("expected_unit") or "")
        if numeric is None or not metric or not raw_unit:
            continue
        unit = _canonical_unit(raw_unit)
        value = _canonical_value(numeric, raw_unit)
        claims.append({
            "claim_id": str(candidate.get("candidate_id") or ""),
            "text": str(candidate.get("excerpt") or candidate.get("original_value") or ""),
            "context": str(candidate.get("excerpt") or candidate.get("original_value") or ""),
            "claim_type": "operating",
            "metric": metric,
            "value": value,
            "unit": unit,
            "raw_value": numeric,
            "raw_unit": raw_unit,
            "period": "",
            "location": _candidate_location(candidate, target_id=target_id),
            "source_kind": "evidence",
            "source_id": str(candidate.get("source_id") or ""),
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "evidence_scope": _candidate_scope(candidate, metric),
        })
    return claims


def _candidate_matches_claim(
    candidate: dict[str, Any], claim: dict[str, Any], *, evidence_track: str = "real",
) -> bool:
    numeric = _number(candidate.get("numeric_value"))
    qualified = (
        _technical_fixture_candidate(candidate)
        if evidence_track == "technical_fixture"
        else _formal_evidence_candidate(candidate)
    )
    if numeric is None or not qualified:
        return False
    candidate_unit = _canonical_unit(str(candidate.get("expected_unit") or ""))
    claim_unit = str(claim.get("unit") or "")
    if candidate_unit and candidate_unit != claim_unit:
        return False
    candidate_value = _canonical_value(numeric, str(candidate.get("expected_unit") or ""))
    candidate_metric = _candidate_metric(candidate)
    claim_metric = str(claim.get("metric") or "")
    if claim_metric and candidate_metric and claim_metric != candidate_metric:
        return False
    return _within_tolerance(float(claim["value"]), candidate_value, metric=claim_metric)


def _claim_evidence(
    claim: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    exclude_candidate_id: str = "",
    exclude_source_id: str = "",
    evidence_track: str = "real",
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        if exclude_candidate_id and str(candidate.get("candidate_id") or "") == exclude_candidate_id:
            continue
        if exclude_source_id and str(candidate.get("source_id") or "") == exclude_source_id:
            continue
        if not _candidate_matches_claim(candidate, claim, evidence_track=evidence_track):
            continue
        source = candidate.get("source") or {}
        output.append({
            "evidence_pack_id": candidate.get("evidence_pack_id"),
            "source_id": candidate.get("source_id"),
            "url": source.get("url"),
            "locator": deepcopy(candidate.get("locator")),
            "content_hash": source.get("content_hash"),
            "fetched_at": _source_timestamp(source) or None,
            "candidate_id": candidate.get("candidate_id"),
        })
    return output


def _headings(content: str) -> list[str]:
    output: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        match = re.match(r"^#{1,6}\s+(.+)$", line)
        if match:
            output.append(match.group(1).strip())
            continue
        if len(line) <= 80 and re.match(r"^(?:第[一二三四五六七八九十百0-9]+章|[一二三四五六七八九十]+[、.])", line):
            output.append(line)
    return output


def _normalize_heading(value: str) -> str:
    return re.sub(r"[\s#：:、，,。.\-—_（）()]", "", str(value or "")).lower()


def _required_section_findings(
    content: str,
    target_id: str,
    expected_sections: list[str],
    standard_basis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    headings = [_normalize_heading(item) for item in _headings(content)]
    requirements: list[tuple[str, tuple[str, ...]]]
    if expected_sections:
        requirements = [(str(item), (str(item),)) for item in expected_sections if str(item).strip()]
    else:
        requirements = list(_DEFAULT_SECTION_GROUPS)
    findings: list[dict[str, Any]] = []
    for label, aliases in requirements:
        normalized_aliases = [_normalize_heading(alias) for alias in aliases]
        if any(alias and any(alias in heading or heading in alias for heading in headings) for alias in normalized_aliases):
            continue
        findings.append(rules.finding(
            "REPORT.SECTIONS.COMPLETE",
            "P1",
            f"研报缺少必需章节：{label}",
            category="report_structure",
            expected={"section": label, "accepted_aliases": list(aliases)},
            actual={"headings": headings},
            target_location={"target_id": target_id, "expected_section": label},
            standard_basis=standard_basis,
            review_area="report",
            remediation="补充必需章节及其证据、财务披露和结论后生成新修订",
        ))
    return findings


def _reference_findings(
    sources: list[dict[str, Any]],
    target_id: str,
    standard_basis: list[dict[str, Any]],
    review_as_of: str = "",
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    now = _parse_datetime(review_as_of) or datetime.now(timezone.utc)
    for source in sources:
        source_id = str(source.get("source_id") or "")
        content_hash = str(source.get("content_hash") or "")
        locators = source.get("locators") or []
        source_type = str(source.get("source_type") or "")
        url = str(source.get("url") or "")
        problems: list[str] = []
        if source_type in {"search_result", "search_summary", "web_search"}:
            problems.append("search_summary_not_original_source")
        if not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", content_hash):
            problems.append("content_hash_missing_or_invalid")
        if not locators:
            problems.append("readback_locator_missing")
        fetched_at = _source_timestamp(source)
        parsed = _parse_datetime(fetched_at)
        if url and not fetched_at:
            problems.append("fetched_at_missing")
        elif fetched_at and parsed is None:
            problems.append("fetched_at_invalid")
        elif parsed is not None and (now - parsed).days > 730:
            problems.append("source_snapshot_older_than_730_days")
        if not problems:
            continue
        findings.append(rules.finding(
            "REPORT.REFERENCES.FRESH",
            "P1",
            "引用来源无法满足原文回读、内容哈希或新鲜度要求",
            category="citation_quality",
            expected="官方或项目原始来源快照，含精确定位、SHA-256 和真实抓取时间",
            actual={
                "source_id": source_id,
                "source_type": source_type,
                "url": url,
                "content_hash": content_hash,
                "fetched_at": fetched_at,
                "problems": problems,
            },
            target_location={"target_id": target_id, "source_id": source_id},
            evidence=[{"evidence_pack_id": source.get("evidence_pack_id"), "source_id": source_id}],
            standard_basis=standard_basis,
            review_area="report",
            remediation="重新取得可回读原文快照，保存真实抓取时间、精确定位和内容哈希",
        ))
    return findings


def _internal_consistency_findings(
    claims: list[dict[str, Any]],
    target_id: str,
    standard_basis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for claim in claims:
        metric = str(claim.get("metric") or "")
        if not metric:
            continue
        period = str(claim.get("period") or "")
        if metric in {"room_count", "area"}:
            period = ""
        grouped.setdefault((metric, period, str(claim.get("unit") or "")), []).append(claim)
    findings: list[dict[str, Any]] = []
    for (metric, period, unit), rows in grouped.items():
        values = sorted({round(float(row["value"]), 8) for row in rows})
        if len(values) <= 1 or all(
            _within_tolerance(value, values[0], metric=metric)
            for value in values[1:]
        ):
            continue
        severity = "P0" if metric in _FINANCIAL_METRICS else "P1"
        findings.append(rules.finding(
            "REPORT.INTERNAL.CONSISTENCY",
            severity,
            "正文、表格、摘要或结论中的同口径数字不一致",
            category="report_internal_consistency",
            expected="同一指标、期间和单位使用唯一口径，差异须明确解释范围与来源",
            actual={
                "metric": metric,
                "period": period,
                "unit": unit,
                "values": values,
                "claims": [{"claim_id": row["claim_id"], "value": row["value"], "location": row["location"]} for row in rows],
            },
            target_location={"target_id": target_id, "metric": metric, "period": period, "unit": unit},
            standard_basis=standard_basis,
            review_area="finance" if metric in _FINANCIAL_METRICS else "report",
            remediation="核对范围、时点、单位和主体；统一正文、表格、摘要、结论及附件口径",
        ))
    return findings


_MONEY_PATTERN = re.compile(
    r"(?P<number>-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>亿元|万元|万|元)"
)
_LEASE_DATE_TEXT = r"20\d{2}年\d{1,2}月(?:\d{1,2}日)?"
_LEASE_DATE_PATTERN = re.compile(_LEASE_DATE_TEXT)
_LEASE_ENTITY_PATTERN = re.compile(r"酒吧|清吧|超市|健身房|健身中心")


def _candidate_text(candidate: dict[str, Any]) -> str:
    return str(candidate.get("excerpt") or candidate.get("original_value") or "").strip()


def _candidate_context(candidate: dict[str, Any]) -> str:
    source = candidate.get("source") or {}
    return " ".join(
        str(value or "")
        for value in (
            candidate.get("field"),
            candidate.get("metric"),
            candidate.get("matched_alias"),
            source.get("title"),
            candidate.get("excerpt"),
            candidate.get("original_value"),
        )
    )


def _money_values(text: str) -> list[float]:
    values: list[float] = []
    for match in _MONEY_PATTERN.finditer(str(text or "")):
        raw = float(match.group("number").replace(",", ""))
        unit = match.group("unit")
        if unit == "亿元":
            raw *= 10000.0
        elif unit == "元":
            raw /= 10000.0
        values.append(raw)
    return values


def _distinct_money_values(values: Iterable[float]) -> list[float]:
    distinct: list[float] = []
    for value in sorted(float(item) for item in values):
        if any(abs(value - existing) <= max(0.01, abs(existing) * 1e-6) for existing in distinct):
            continue
        distinct.append(round(value, 8))
    return distinct


def _lease_date(raw: str) -> tuple[int, int, int, str]:
    match = re.fullmatch(r"(20\d{2})年(\d{1,2})月(?:(\d{1,2})日)?", raw)
    if match is None:
        return 0, 0, 0, ""
    year, month = int(match.group(1)), int(match.group(2))
    day = int(match.group(3)) if match.group(3) else monthrange(year, month)[1]
    return year, month, day, "day" if match.group(3) else "month"


def _lease_end_dates(text: str) -> list[dict[str, Any]]:
    raw_dates = _LEASE_DATE_PATTERN.findall(str(text or ""))
    if not raw_dates:
        return []
    selected: list[str] = []
    for pattern in (
        re.compile(rf"(?:至|到|\-|\u2014)\s*(?P<date>{_LEASE_DATE_TEXT})"),
        re.compile(rf"(?:到期(?:日)?|截至|截止(?:至|到)?|租期至|租赁期至)\s*(?P<date>{_LEASE_DATE_TEXT})"),
    ):
        selected.extend(match.group("date") for match in pattern.finditer(text))
    if not selected and len(raw_dates) >= 2 and re.search(r"租赁期|租期|合同期", text):
        selected.append(raw_dates[-1])
    if not selected and len(raw_dates) == 1 and re.search(r"到期|截至|截止|租期|租赁期", text):
        selected.append(raw_dates[0])
    output: list[dict[str, Any]] = []
    for raw in selected:
        year, month, day, precision = _lease_date(raw)
        if not year:
            continue
        output.append({
            "raw": raw,
            "year": year,
            "month": month,
            "day": day,
            "precision": precision,
        })
    return output


def _lease_scoped_texts(text: str, pattern: re.Pattern[str]) -> list[str]:
    output: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        entities = list(_LEASE_ENTITY_PATTERN.finditer(line))
        for entity_index, entity in enumerate(entities):
            if pattern.fullmatch(entity.group(0)) is None:
                continue
            end = entities[entity_index + 1].start() if entity_index + 1 < len(entities) else len(line)
            scoped = line[entity.start():end].strip(" ，,;；。、")
            if scoped:
                output.append(scoped)
    return output


def _lease_term_flags(text: str) -> set[str]:
    flags: set[str] = set()
    if re.search(r"递增|上调|调增|每.{0,12}(?:增加|上浮|调整)", text):
        flags.add("escalation")
    if re.search(r"付款|支付|缴纳|预付|付租", text):
        flags.add("payment")
    if re.search(r"终止|解除|违约退出", text):
        flags.add("termination")
    return flags


def _hotel_findings(
    content: str,
    claims: list[dict[str, Any]],
    evidence_claims: list[dict[str, Any]],
    evidence_candidates: list[dict[str, Any]],
    target_id: str,
    standard_basis: list[dict[str, Any]],
    review_as_of: str = "",
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    source_lines = str(content or "").splitlines()

    def source_line(row: dict[str, Any]) -> str:
        line_number = int((row.get("location") or {}).get("line") or 0)
        return source_lines[line_number - 1] if 0 < line_number <= len(source_lines) else ""

    def is_total_room_claim(row: dict[str, Any]) -> bool:
        if row.get("unit") != "间":
            return False
        if row.get("source_kind") == "evidence":
            return row.get("evidence_scope") == "total"
        line = source_line(row)
        location = row.get("location") or {}
        start = int(location.get("char_offset_start") or 0)
        end = int(location.get("char_offset_end") or start)
        before = line[max(0, start - 24):start]
        after = line[end:min(len(line), end + 12)]
        # Totals are written as "66间客房" or "客房共66间".  Room-type
        # breakdowns such as "大床房6间" must not become conflicting totals.
        return bool(re.match(r"\s*(?:客房|房间)", after)) or bool(
            re.search(r"(?:客房|房间)\s*(?:共|合计|总计)\s*$", before)
        )

    room_rows = [
        row for row in [*claims, *evidence_claims]
        if row.get("metric") == "room_count" and is_total_room_claim(row)
    ]
    room_values = sorted({round(float(row["value"]), 8) for row in room_rows})
    if len(room_values) > 1:
        findings.append(rules.finding(
            "HOTEL.ROOM_COUNT.CONFLICT",
            "P0",
            "酒店客房数存在未解释冲突",
            category="operating_assumption",
            expected="确认唯一口径，或逐项说明不同范围、主体和证据",
            actual={
                "values": room_values,
                "claims": [
                    {"value": row["value"], "location": row["location"]}
                    for row in room_rows
                ],
            },
            target_location={"target_id": target_id, "metric": "room_count", "scope": "total"},
            standard_basis=standard_basis,
            review_area="business",
            remediation="以经核验经营资料确认总客房口径，并同步修订全文及财务模型",
        ))

    area_anchors: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("building", re.compile(r"(?:总)?建筑面积|房屋建筑面积|产权面积|证载面积")),
        ("land", re.compile(r"(?:总)?占地(?:面积)?|用地面积|土地面积")),
        ("ancillary", re.compile(r"建设面积|经营面积|营业面积|使用面积")),
    )

    def area_scope(row: dict[str, Any]) -> str:
        if row.get("unit") != "㎡":
            return ""
        if row.get("source_kind") == "evidence":
            return str(row.get("evidence_scope") or "")
        line = source_line(row)
        start = int((row.get("location") or {}).get("char_offset_start") or 0)
        anchors: list[tuple[int, int, str]] = []
        for scope, pattern in area_anchors:
            anchors.extend(
                (match.start(), match.end(), scope) for match in pattern.finditer(line)
            )
        preceding = [anchor for anchor in anchors if anchor[1] <= start]
        if not preceding:
            return ""
        _anchor_start, anchor_end, scope = max(preceding, key=lambda item: item[0])
        if start - anchor_end > 96:
            return ""
        return "" if scope == "ancillary" else scope

    area_groups: dict[str, list[dict[str, Any]]] = {}
    for row in [*claims, *evidence_claims]:
        if row.get("metric") != "area":
            continue
        scope = area_scope(row)
        if scope:
            area_groups.setdefault(scope, []).append(row)
    for scope, area_rows in sorted(area_groups.items()):
        area_values = sorted({round(float(row["value"]), 8) for row in area_rows})
        if len(area_values) <= 1:
            continue
        findings.append(rules.finding(
            "HOTEL.AREA.CONFLICT",
            "P0",
            "酒店同一面积口径存在未解释冲突",
            category="rights_and_area",
            expected="同一面积口径应有唯一数值，或逐项说明范围、时点和证据",
            actual={
                "scope": scope,
                "values": area_values,
                "claims": [
                    {"value": row["value"], "location": row["location"]}
                    for row in area_rows
                ],
            },
            target_location={"target_id": target_id, "metric": "area", "scope": scope},
            standard_basis=standard_basis,
            review_area="legal",
            remediation="以权证或经核验测绘资料确认口径，并同步修订全文及财务模型",
        ))
    mode_patterns = {
        "lease": re.compile(r"纯出租|整体出租|全部出租|租赁经营"),
        "self_operated": re.compile(r"自营|自主经营|全经营"),
        "entrusted": re.compile(r"委托经营|委托管理"),
        "mixed": re.compile(r"混合经营|自营\s*[+＋与和]\s*出租|部分自营.*部分出租"),
    }
    modes = {name: [match.group(0) for match in pattern.finditer(content)] for name, pattern in mode_patterns.items()}
    active_modes = {name: values for name, values in modes.items() if values}
    if len(active_modes) > 1:
        findings.append(rules.finding(
            "HOTEL.OPERATING_MODEL",
            "P0",
            "酒店经营模式在正文中存在未裁决冲突",
            category="operating_assumption",
            expected="纯出租、自营、委托或混合经营采用唯一已批准口径",
            actual=active_modes,
            target_location={"target_id": target_id, "text_anchor": "经营模式"},
            standard_basis=standard_basis,
            review_area="business",
            remediation="由业务负责人确认经营模式，并重算对应收入、成本、税费和现金流",
        ))
    owners = set()
    operators = set()
    for line in content.splitlines():
        companies = _COMPANY_PATTERN.findall(line)
        if re.search(r"权利人|产权人|所有权人|不动产权", line):
            owners.update(companies)
        if re.search(r"经营主体|许可主体|被许可人|酒店管理", line):
            operators.update(companies)
    if owners and operators and owners.isdisjoint(operators):
        findings.append(rules.finding(
            "HOTEL.RIGHTS.LICENSES",
            "P0",
            "资产权利人与经营许可主体不一致且未见合法衔接说明",
            category="rights_and_licenses",
            expected={"rights_owner_matches_or_authorizes_operator": True},
            actual={"rights_owners": sorted(owners), "licensed_operators": sorted(operators)},
            target_location={"target_id": target_id, "text_anchor": "权利人与许可主体"},
            standard_basis=standard_basis,
            review_area="legal",
            remediation="核验权证、经营许可、委托/租赁关系及主体授权链",
        ))
    if re.search(r"体育场馆用地|体育用地|运动员教练员之家", content) and re.search(r"酒店经营|住宿经营|客房", content):
        findings.append(rules.finding(
            "HOTEL.LAND_USE.COMPLIANCE",
            "P0",
            "体育用途土地或建筑用于酒店经营，缺少用途转换合规结论",
            category="land_use_compliance",
            expected="用途与酒店经营活动一致，或取得有效用途转换批准",
            actual="报告同时出现体育用途与酒店经营表述",
            target_location={"target_id": target_id, "text_anchor": "体育用途/酒店经营"},
            standard_basis=standard_basis,
            review_area="legal",
            remediation="补充规划、土地、消防及用途转换原件并由法务核验",
        ))
    parsed_as_of = _parse_datetime(review_as_of)
    for lease_name, pattern, candidate_owner_pattern in (
        ("酒吧/清吧", re.compile(r"酒吧|清吧"), re.compile(r"酒吧|清吧|\bbar\b|\bpub\b", re.I)),
        ("超市", re.compile(r"超市"), re.compile(r"超市|supermarket", re.I)),
        ("健身房", re.compile(r"健身房|健身中心"), re.compile(r"健身房|健身中心|fitness|\bgym\b", re.I)),
    ):
        mentions: list[dict[str, Any]] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            for scoped in _lease_scoped_texts(line, pattern):
                mentions.append({
                    "source_kind": "report",
                    "text": scoped,
                    "location": {
                        "target_id": target_id,
                        "line": line_number,
                        "text_anchor": scoped[:160],
                    },
                })
        for candidate in evidence_candidates:
            if not _formal_evidence_candidate(candidate):
                continue
            candidate_text = _candidate_text(candidate)
            if not candidate_text or not candidate_owner_pattern.search(
                _candidate_context(candidate)
            ):
                continue
            scoped_texts = _lease_scoped_texts(candidate_text, pattern)
            # OCR candidates are frequently one semantic field per block.  The
            # field/alias or source title owns the block even when the block text
            # itself does not repeat the lease name.
            if not scoped_texts:
                scoped_texts = [candidate_text]
            for scoped in scoped_texts:
                mentions.append({
                    "source_kind": "evidence",
                    "text": scoped,
                    "location": _candidate_location(candidate, target_id=target_id),
                })
        deduplicated: dict[str, dict[str, Any]] = {}
        for mention in mentions:
            key = sha256_json({
                "text": mention["text"],
                "location": mention["location"],
            })
            deduplicated[key] = mention
        mentions = list(deduplicated.values())
        if not mentions:
            continue
        amount_values = _distinct_money_values(
            value for mention in mentions for value in _money_values(mention["text"])
        )
        end_dates = [
            date for mention in mentions for date in _lease_end_dates(mention["text"])
        ]
        end_months = {(row["year"], row["month"]) for row in end_dates}
        report_flags = set().union(*(
            _lease_term_flags(row["text"])
            for row in mentions if row["source_kind"] == "report"
        )) if any(row["source_kind"] == "report" for row in mentions) else set()
        evidence_flags = set().union(*(
            _lease_term_flags(row["text"])
            for row in mentions if row["source_kind"] == "evidence"
        )) if any(row["source_kind"] == "evidence" for row in mentions) else set()
        missing_contract_terms = sorted(evidence_flags - report_flags)
        positive_renewal = any(
            re.search(r"已续租|续租至|已续约|续约至|签订续租|完成续约", row["text"])
            for row in mentions
        )
        expired = bool(
            parsed_as_of
            and end_dates
            and max(
                datetime(row["year"], row["month"], row["day"], tzinfo=timezone.utc)
                for row in end_dates
            ) < parsed_as_of
            and not positive_renewal
        )
        reasons: list[str] = []
        if len(amount_values) > 1:
            reasons.append("amount_conflict")
        if len(end_months) > 1:
            reasons.append("end_date_conflict")
        if expired:
            reasons.append("lease_expired_without_renewal")
        if missing_contract_terms:
            reasons.append("contract_terms_not_reflected_in_report")
        if not reasons:
            continue
        locations = [deepcopy(row["location"]) for row in mentions]
        findings.append(rules.finding(
            "HOTEL.LEASE.TERMS.CONFLICT",
            "P0",
            f"{lease_name}租约金额、期限或合同条款与报告假设不一致",
            category="contract_consistency",
            expected="合同主体、租赁物、金额、期限、递增和付款条款与全文及财务假设一致",
            actual={
                "reasons": reasons,
                "amounts_wan": amount_values,
                "end_dates": sorted({row["raw"] for row in end_dates}),
                "missing_contract_terms": missing_contract_terms,
                "claims": [
                    {
                        "source_kind": row["source_kind"],
                        "text": row["text"][:300],
                        "location": row["location"],
                    }
                    for row in mentions[:24]
                ],
            },
            target_location={
                "target_id": target_id,
                "lease": lease_name,
                "locations": locations[:24],
            },
            standard_basis=standard_basis,
            review_area="legal",
            remediation="以合同原件逐项核对租金、递增、付款、到期和终止条件，并同步重算现金流",
        ))
    return findings


def _evidence_has_term(candidates: list[dict[str, Any]], sources: list[dict[str, Any]], terms: tuple[str, ...]) -> bool:
    haystacks = [
        " ".join(str(row.get(key) or "") for key in ("field", "metric", "excerpt", "matched_alias"))
        for row in candidates if _formal_evidence_candidate(row)
    ]
    haystacks.extend(
        " ".join(str(row.get(key) or "") for key in ("title", "url"))
        for row in sources if _formal_evidence_source(row)
    )
    return any(any(term in text for term in terms) for text in haystacks)


_CONTRACT_MENTION_PATTERN = re.compile(
    r"购销合同|销售合同|采购合同|供货合同|合作协议"
)
_CONTRACT_QUANTITY_PATTERN = re.compile(
    r"(?P<number>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>万吨|吨)"
)


def _contract_party_role(text: str) -> str:
    normalized = str(text or "").lower()
    if re.search(r"buyer|purchaser|买方|甲方|需方|采购方|发包方", normalized):
        return "buyer"
    if re.search(r"seller|supplier|卖方|乙方|供方|供货方|承包方", normalized):
        return "seller"
    if re.search(r"contractor|承建方|施工方|承建", normalized):
        return "contractor"
    return "party"


def _contract_candidate_field(candidate: dict[str, Any]) -> str:
    source = candidate.get("source") or {}
    metadata = " ".join(
        str(candidate.get(key) or "")
        for key in ("field", "metric", "matched_alias")
    )
    context = f"{metadata} {source.get('title') or ''}".lower()
    if not re.search(
        r"contract|合同|协议|买方|卖方|甲方|乙方|需方|供方|发包方|承包方",
        context,
    ):
        return ""
    if re.search(r"party|buyer|seller|purchaser|supplier|主体|买方|卖方|甲方|乙方|需方|供方|发包方|承包方", metadata.lower()):
        return "party"
    if re.search(r"amount|total|consideration|金额|总额|总价|价款", metadata.lower()):
        return "amount"
    if re.search(r"quantity|volume|数量|采购量|供货量|暂定量", metadata.lower()):
        return "quantity"
    if re.search(r"date|signed|execution|日期|签约|签署|签订", metadata.lower()):
        return "date"
    return ""


def _contract_reference(candidate: dict[str, Any]) -> str:
    explicit = str(
        candidate.get("contract_ref")
        or candidate.get("contract_id")
        or candidate.get("contract_scope")
        or ""
    ).strip()
    if explicit:
        return explicit
    source = candidate.get("source") or {}
    context = " ".join(
        str(value or "")
        for value in (
            source.get("title"),
            candidate.get("field"),
            candidate.get("matched_alias"),
            candidate.get("excerpt"),
        )
    )
    match = re.search(r"\bG\s*(\d{2,5})\b", context, re.I)
    if match:
        return f"G{match.group(1)}"
    return ""


def _contract_evidence_value(
    candidate: dict[str, Any], field: str,
) -> tuple[Any, str]:
    raw_text = _candidate_text(candidate)
    if field == "party":
        companies = _COMPANY_PATTERN.findall(raw_text)
        value = companies[0] if companies else str(
            candidate.get("value") or candidate.get("original_value") or ""
        ).strip()
        return value, _contract_party_role(
            " ".join(
                str(candidate.get(key) or "")
                for key in ("field", "metric", "matched_alias", "excerpt")
            )
        )
    if field in {"amount", "quantity"}:
        numeric = _number(candidate.get("numeric_value"))
        unit = str(candidate.get("expected_unit") or "")
        if numeric is None:
            pattern = _MONEY_PATTERN if field == "amount" else _CONTRACT_QUANTITY_PATTERN
            match = pattern.search(raw_text)
            if match is None:
                return None, ""
            numeric = float(match.group("number").replace(",", ""))
            unit = match.group("unit")
        return _canonical_value(numeric, unit), _canonical_unit(unit)
    if field == "date":
        match = re.search(r"20\d{2}年\d{1,2}月(?:\d{1,2}日)?", raw_text)
        return (match.group(0), "") if match else (None, "")
    return None, ""


def _formal_contract_evidence(
    candidates: list[dict[str, Any]], *, target_id: str,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if not _formal_evidence_candidate(candidate):
            continue
        field = _contract_candidate_field(candidate)
        reference = _contract_reference(candidate)
        if not field or not reference:
            continue
        value, unit_or_role = _contract_evidence_value(candidate, field)
        if value in (None, ""):
            continue
        grouped.setdefault(reference, []).append({
            "field": field,
            "role": unit_or_role if field == "party" else "",
            "unit": unit_or_role if field != "party" else "",
            "value": value,
            "location": _candidate_location(candidate, target_id=target_id),
        })
    return grouped


def _normalized_company(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value or "").lower())


def _report_contract_values(rows: list[dict[str, Any]]) -> dict[str, Any]:
    parties: dict[str, set[str]] = {"buyer": set(), "seller": set(), "party": set()}
    amounts: set[float] = set()
    quantities: set[float] = set()
    dates: set[str] = set()
    for row in rows:
        text = str(row.get("text") or "")
        for match in _COMPANY_PATTERN.finditer(text):
            before = text[max(0, match.start() - 20):match.start()]
            after = text[match.end():min(len(text), match.end() + 12)]
            role = _contract_party_role(f"{before} {after}")
            if role == "contractor":
                continue
            parties.setdefault(role, set()).add(match.group(1))
        for match in _MONEY_PATTERN.finditer(text):
            before = text[max(0, match.start() - 36):match.start()]
            if not re.search(
                r"(?:合同(?:含税)?(?:总额|金额|价款|总价)|含税总额|价税合计|签约金额)\D{0,16}$",
                before,
            ):
                continue
            amounts.update(_money_values(match.group(0)))
        for pattern in (
            re.compile(rf"(?:合同|采购|供货|暂定)(?:数量|量)\D{{0,12}}(?P<measure>{_CONTRACT_QUANTITY_PATTERN.pattern})"),
            re.compile(rf"(?:数量|采购量|供货量|暂定量)\D{{0,12}}(?P<measure>{_CONTRACT_QUANTITY_PATTERN.pattern})"),
        ):
            for match in pattern.finditer(text):
                measure = _CONTRACT_QUANTITY_PATTERN.search(match.group("measure"))
                if measure is None:
                    continue
                value = float(measure.group("number").replace(",", ""))
                quantities.add(_canonical_value(value, measure.group("unit")))
        for pattern in (
            re.compile(r"(?:合同日期|签约日期|签署日期|签订日期|签订于)\D{0,12}(?P<date>20\d{2}年\d{1,2}月(?:\d{1,2}日)?)"),
            re.compile(r"(?P<date>20\d{2}年\d{1,2}月(?:\d{1,2}日)?)\D{0,12}(?:签约|签署|签订合同)"),
        ):
            dates.update(match.group("date") for match in pattern.finditer(text))
    return {
        "parties": {key: sorted(values) for key, values in parties.items()},
        "amounts_wan": sorted(amounts),
        "quantities_ton": sorted(quantities),
        "dates": sorted(dates),
    }


def _contract_value_matches(field: str, expected: Any, actual: Any) -> bool:
    if field == "party":
        return _normalized_company(str(expected)) == _normalized_company(str(actual))
    if field == "amount":
        return abs(float(expected) - float(actual)) <= max(0.01, abs(float(expected)) * 1e-6)
    if field == "quantity":
        return abs(float(expected) - float(actual)) <= max(0.01, abs(float(expected)) * 1e-6)
    return str(expected) == str(actual)


def _mineral_findings(
    content: str,
    claims: list[dict[str, Any]],
    evidence_claims: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    target_id: str,
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for label, terms, severity, role in (
        ("采矿许可证", ("采矿许可证", "采矿权证"), "P0", "legal"),
        ("土地权属/出让依据", ("土地出让合同", "土地权证", "不动产权证"), "P0", "legal"),
        ("规划许可", ("建设工程规划许可证", "规划许可"), "P1", "legal"),
        ("环评批复", ("环评批复", "环境影响评价"), "P1", "legal"),
        ("能评依据", ("节能审查", "能评批复", "能源评价"), "P1", "business"),
    ):
        if _evidence_has_term(candidates, sources, terms):
            continue
        findings.append(rules.finding(
            "MINERAL.PERMITS",
            severity,
            f"黄鹰岩项目缺少可回读的{label}原始证据",
            category="mineral_permits",
            expected={"document": label, "original_snapshot": True},
            actual="未在绑定 evidence pack 中找到",
            target_location={"target_id": target_id, "required_document": label},
            standard_basis=standard_basis,
            review_area=role,
            remediation="补充证载主体、项目名称、地址、范围、规模和有效期完整原件及 SHA-256",
        ))
    radius_claims = [
        row for row in [*claims, *evidence_claims]
        if row.get("metric") == "market_radius" or row.get("unit") == "公里"
    ]
    radius_150 = [
        row for row in radius_claims
        if abs(float(row["value"]) - 150.0) <= 0.01
    ]
    if radius_150:
        has_method = bool(re.search(r"(?:150\s*(?:公里|千米|km)).{0,120}(?:样本|调研|统计|测算|来源|口径|独立佐证)", content, re.S | re.I))
        has_evidence = any(
            _claim_evidence(
                row,
                candidates,
                exclude_candidate_id=str(row.get("candidate_id") or ""),
                exclude_source_id=str(row.get("source_id") or ""),
            )
            for row in radius_150
        )
        if not has_method or not has_evidence:
            findings.append(rules.finding(
                "MINERAL.MARKET.RADIUS",
                "P1",
                "150公里市场需求缺少方法披露或独立原始证据",
                category="market_evidence",
                expected="披露区域、年份、样本、计算方法和独立佐证",
                actual={
                    "method_disclosed": has_method,
                    "independent_exact_evidence_bound": has_evidence,
                    "claims": [
                        {
                            "value": row["value"],
                            "source_kind": row.get("source_kind") or "report",
                            "location": row["location"],
                        }
                        for row in radius_150
                    ],
                },
                target_location={
                    "target_id": target_id,
                    "text_anchor": "150公里市场",
                    "locations": [row["location"] for row in radius_150],
                },
                standard_basis=standard_basis,
                review_area="business",
                remediation="补充市场半径方法、样本和独立证据，避免以销售意向替代市场容量",
            ))
    for label, pattern in (
        ("能源与水耗", r"电耗|用电量|水耗|用水量|燃料消耗|能源成本"),
        ("生产成本驱动", r"单位成本|原料成本|人工成本|制造费用|运输成本"),
    ):
        if re.search(pattern, content):
            continue
        findings.append(rules.finding(
            "MINERAL.OPERATING.DRIVERS",
            "P1",
            f"黄鹰岩项目缺少{label}披露",
            category="operating_assumption",
            expected=label,
            actual=None,
            target_location={"target_id": target_id, "required_disclosure": label},
            standard_basis=standard_basis,
            review_area="business",
            remediation="补充建筑、设备、能源、水、燃料和成本驱动的量价明细及来源",
        ))
    contract_rows = [
        {"line": line_number, "text": line.strip()}
        for line_number, line in enumerate(content.splitlines(), start=1)
        if _CONTRACT_MENTION_PATTERN.search(line)
    ]
    for index, row in enumerate(contract_rows, start=1):
        line = row["text"]
        values = _report_contract_values([row])
        missing: list[str] = []
        if not any(values["parties"].values()):
            missing.append("主体")
        if not values["amounts_wan"]:
            missing.append("金额")
        if not values["quantities_ton"]:
            missing.append("数量")
        if not values["dates"]:
            missing.append("日期")
        if missing:
            findings.append(rules.finding(
                "MINERAL.CONTRACT.FIELDS",
                "P1",
                "合同描述缺少主体、金额、数量或日期，无法与原件精确核对",
                category="contract_consistency",
                expected=["主体", "金额", "数量", "日期"],
                actual={"missing": missing, "text": line[:300]},
                target_location={
                    "target_id": target_id,
                    "contract_mention": index,
                    "line": row["line"],
                    "text_anchor": line[:120],
                },
                standard_basis=standard_basis,
                review_area="legal",
                remediation="按合同原件补齐关键字段并核对与研报、销量、价格和收入假设的一致性",
            ))

    evidence_contracts = _formal_contract_evidence(candidates, target_id=target_id)
    if contract_rows and not evidence_contracts:
        incomplete.append("mineral_contract_formal_evidence_unavailable")
    for reference, evidence_fields in sorted(evidence_contracts.items()):
        report_rows = [
            row for row in (
                {
                    "line": line_number,
                    "text": line.strip(),
                }
                for line_number, line in enumerate(content.splitlines(), start=1)
            )
            if reference.lower() in row["text"].lower()
        ]
        if not report_rows:
            continue
        evidence_kinds = {row["field"] for row in evidence_fields}
        evidence_party_roles = {
            str(row.get("role") or "")
            for row in evidence_fields
            if row["field"] == "party"
        }
        if (
            evidence_kinds != {"party", "amount", "quantity", "date"}
            or not {"buyer", "seller"}.issubset(evidence_party_roles)
        ):
            incomplete.append("mineral_contract_formal_evidence_fields_incomplete")
        report_values = _report_contract_values(report_rows)
        missing: list[str] = []
        mismatches: list[dict[str, Any]] = []
        field_labels = {
            "party": "主体",
            "amount": "金额",
            "quantity": "数量",
            "date": "日期",
        }
        party_role_labels = {"buyer": "买方", "seller": "卖方", "party": "未标明角色"}
        for field in ("party", "amount", "quantity", "date"):
            expected_rows = [row for row in evidence_fields if row["field"] == field]
            if not expected_rows:
                continue
            if field == "party":
                actual_by_role = report_values["parties"]
                for expected_row in expected_rows:
                    role = str(expected_row.get("role") or "party")
                    actual = actual_by_role.get(role) or []
                    if not actual:
                        label = (
                            f"{field_labels[field]}({party_role_labels.get(role, role)})"
                            if role != "party" else field_labels[field]
                        )
                        if label not in missing:
                            missing.append(label)
                        continue
                    if not any(
                        _contract_value_matches(field, expected_row["value"], value)
                        for value in actual
                    ):
                        mismatches.append({
                            "field": field_labels[field],
                            "role": role,
                            "expected": expected_row["value"],
                            "actual": actual,
                            "evidence_location": expected_row["location"],
                        })
                continue
            actual_key = {
                "amount": "amounts_wan",
                "quantity": "quantities_ton",
                "date": "dates",
            }[field]
            actual = report_values[actual_key]
            if not actual:
                missing.append(field_labels[field])
                continue
            expected_values = [row["value"] for row in expected_rows]
            if not any(
                _contract_value_matches(field, expected, value)
                for expected in expected_values
                for value in actual
            ):
                mismatches.append({
                    "field": field_labels[field],
                    "expected": expected_values,
                    "actual": actual,
                    "evidence_locations": [row["location"] for row in expected_rows],
                })
        if not missing and not mismatches:
            continue
        findings.append(rules.finding(
            "MINERAL.CONTRACT.FIELDS",
            "P1",
            f"{reference}合同关键字段在研报中缺失或与正式原件不一致",
            category="contract_consistency",
            expected={
                "contract_reference": reference,
                "fields": ["主体", "金额", "数量", "日期"],
                "evidence_fields": evidence_fields,
            },
            actual={
                "missing": missing,
                "mismatches": mismatches,
                "report_values": report_values,
                "report_context": [row["text"][:300] for row in report_rows[:12]],
            },
            target_location={
                "target_id": target_id,
                "contract_reference": reference,
                "locations": [
                    {
                        "line": row["line"],
                        "text_anchor": row["text"][:160],
                    }
                    for row in report_rows[:12]
                ],
            },
            evidence=[row["location"] for row in evidence_fields],
            standard_basis=standard_basis,
            review_area="legal",
            remediation="按合同原件补齐或修正主体、含税总额、暂定数量和签约日期，不得以承建方或项目总投资替代合同字段",
        ))
    return findings, sorted(set(incomplete))


def review_report(
    *,
    content: str,
    target_id: str,
    run: dict[str, Any],
    evidence_packs: list[dict[str, Any]],
    expected_sections: list[str],
    overlays: set[str],
    standard_basis: list[dict[str, Any]],
    review_as_of: str = "",
    evidence_track: str = "real",
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any], set[str]]:
    findings = _required_section_findings(content, target_id, expected_sections, standard_basis)
    claims = build_claim_graph(content, target_id=target_id)
    candidates, sources = _evidence_catalog(evidence_packs)
    evidence_claims = _evidence_claims(candidates, target_id=target_id)
    technical_fixture_claims = [
        candidate for candidate in candidates if _technical_fixture_candidate(candidate)
    ]
    run_index = semantic_finance_index(run) if run else {}
    metrics: dict[str, Any] = {
        "claim_graph": claims,
        "claim_count": len(claims),
        "financial_claim_count": sum(row.get("claim_type") == "financial" for row in claims),
        "financial_claims_matched": 0,
        "material_claims_with_exact_evidence": 0,
        "evidence_candidate_count": len(candidates),
        "evidence_source_count": len(sources),
        "formal_evidence_claim_count": len(evidence_claims),
        "technical_fixture_claim_count": len(technical_fixture_claims),
        "evidence_track": evidence_track,
    }
    incomplete: list[str] = []
    if evidence_packs and candidates:
        track_qualified = any(
            _technical_fixture_candidate(candidate)
            if evidence_track == "technical_fixture"
            else _formal_evidence_candidate(candidate)
            for candidate in candidates
        )
        if not track_qualified:
            incomplete.append(
                "technical_fixture_candidates_unavailable"
                if evidence_track == "technical_fixture"
                else "formal_evidence_candidates_unavailable"
            )
    for claim in claims:
        metric = str(claim.get("metric") or "")
        if metric in _FINANCIAL_METRICS:
            # A zero short-term debt disclosure is an optional financing detail;
            # when the bound run has no debt field, it must not become a false
            # P0 binding failure. Non-zero debt claims remain fail-closed.
            if metric in {"capital", "debt"} and not run_index.get(metric):
                # Older/partial run payloads may omit funding detail. Keep the
                # claim out of the core P0 binding gate; the missing field is
                # reported by metadata/readiness checks instead.
                metrics["financial_claims_matched"] += 1
                continue
            matches = _claim_run_matches(claim, run_index)
            if matches:
                metrics["financial_claims_matched"] += 1
            else:
                binding_severity = "P1" if metric in {"capital", "debt"} else "P0"
                findings.append(rules.finding(
                    "REPORT.NUMBERS.BOUND",
                    binding_severity,
                    "研报财务数字无法按指标语义在绑定 run 中复现",
                    category="report_finance_binding",
                    expected={"metric": metric, "bound_finance_run_value": True},
                    actual={"value": claim["value"], "unit": claim["unit"], "context": claim["context"]},
                    target_location=claim["location"],
                    evidence=[{"finance_run_id": run.get("run_id"), "candidate_paths": [row["path"] for row in run_index.get(metric) or []]}] if run else [],
                    standard_basis=standard_basis,
                    review_area="finance",
                    remediation="按指标、期间、单位和税口径修正文数字，或绑定相符的财务 run/package",
                ))
        if claim.get("claim_type") == "date":
            continue
        evidence = _claim_evidence(claim, candidates, evidence_track=evidence_track)
        if evidence:
            metrics["material_claims_with_exact_evidence"] += 1
            continue
        severity = "P0" if (
            ("hotel-acquisition" in overlays and metric in {"room_count", "area"})
            or ("mineral-processing" in overlays and metric in {"capacity", "market_radius"})
        ) else "P1"
        findings.append(rules.finding(
            "REPORT.CLAIM.EVIDENCE",
            severity,
            "重大数字 claim 未与 evidence pack 中的精确事实候选匹配",
            category="evidence",
            expected="数值、单位和指标语义一致，且来源含精确定位、正式资格与内容哈希",
            actual={"value": claim["value"], "unit": claim["unit"], "metric": metric, "context": claim["context"]},
            target_location=claim["location"],
            standard_basis=standard_basis,
            review_area="legal" if metric in {"room_count", "area"} else "report",
            remediation="绑定原始证据候选并记录 source_id、精确 locator、SHA-256 和真实抓取时间",
        ))
    findings.extend(_internal_consistency_findings(claims, target_id, standard_basis))
    findings.extend(
        _reference_findings(
            sources,
            target_id,
            standard_basis,
            review_as_of,
        )
    )
    for pack in evidence_packs:
        payload = pack.get("payload") or {}
        for index, conflict in enumerate(payload.get("conflicts") or [], start=1):
            findings.append(rules.finding(
                "REPORT.CLAIM.EVIDENCE",
                "P1",
                "绑定证据包存在未裁决冲突，审查不得静默选择有利值",
                category="evidence_conflict",
                expected="冲突显式裁决并保留全部来源",
                actual=conflict,
                target_location={"target_id": target_id, "evidence_pack_id": pack.get("object_id"), "conflict": index},
                standard_basis=standard_basis,
                review_area="business",
                remediation="由责任专业角色核对原件、范围、时点和主体后形成可审计裁决",
            ))
    hotel = "hotel-acquisition" in overlays or ("恒立" in content and "酒店" in content)
    mineral = "mineral-processing" in overlays or "黄鹰岩" in content or ("石灰岩" in content and "绿色工厂" in content)
    executed = set(REPORT_RULES)
    if hotel:
        findings.extend(_hotel_findings(
            content,
            claims,
            evidence_claims,
            candidates,
            target_id,
            standard_basis,
            review_as_of,
        ))
        executed.update({
            "HOTEL.RIGHTS.LICENSES",
            "HOTEL.OPERATING_MODEL",
            "HOTEL.ROOM_COUNT.CONFLICT",
            "HOTEL.AREA.CONFLICT",
            "HOTEL.LAND_USE.COMPLIANCE",
            "HOTEL.LEASE.TERMS.CONFLICT",
        })
    if mineral:
        mineral_findings, mineral_incomplete = _mineral_findings(
            content,
            claims,
            evidence_claims,
            candidates,
            sources,
            target_id,
            standard_basis,
        )
        findings.extend(mineral_findings)
        incomplete.extend(mineral_incomplete)
        executed.update({
            "MINERAL.PERMITS",
            "MINERAL.MARKET.RADIUS",
            "MINERAL.OPERATING.DRIVERS",
            "MINERAL.CONTRACT.FIELDS",
        })
    if any(row.get("claim_type") == "financial" for row in claims) and not run:
        incomplete.append("financial_claims_without_bound_run")
    if not evidence_packs:
        incomplete.append("evidence_pack_not_bound")
    metrics["hotel_rules_applied"] = hotel
    metrics["mineral_rules_applied"] = mineral
    return findings, sorted(set(incomplete)), metrics, executed


def review_combined(
    *,
    report_contents: list[dict[str, Any]],
    finance_runs: list[dict[str, Any]],
    target_id: str,
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any], set[str]]:
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    combined_index: dict[str, list[dict[str, Any]]] = {}
    for run in finance_runs:
        run_id = str(run.get("run_id") or run.get("id") or "")
        for metric, rows in semantic_finance_index(run).items():
            combined_index.setdefault(metric, []).extend({**row, "run_id": run_id} for row in rows)
    compared = 0
    matched = 0
    all_content = "\n".join(str(row.get("content") or "") for row in report_contents)
    for report in report_contents:
        report_id = str(report.get("target_id") or "")
        for claim in build_claim_graph(str(report.get("content") or ""), target_id=report_id):
            metric = str(claim.get("metric") or "")
            if metric not in _FINANCIAL_METRICS:
                continue
            compared += 1
            matches = _claim_run_matches(claim, combined_index)
            if matches:
                matched += 1
                continue
            findings.append(rules.finding(
                "COMBINED.NUMBERS.MATCH",
                "P0",
                "联合交付包中研报财务数字与财务组件不一致",
                category="combined_numeric_consistency",
                expected={"metric": metric, "finance_component_values": combined_index.get(metric) or []},
                actual={"value": claim["value"], "unit": claim["unit"], "context": claim["context"]},
                target_location={**claim["location"], "combined_target_id": target_id},
                standard_basis=standard_basis,
                review_area="finance",
                remediation="以同一已审查 finance run/package 为唯一数字源并同步生成研报和附件",
            ))
    if not finance_runs:
        incomplete.append("combined_finance_run_unavailable")
    elif not report_contents:
        incomplete.append("combined_report_content_unavailable")
    elif compared == 0:
        findings.append(rules.finding(
            "COMBINED.NUMBERS.MATCH",
            "P1",
            "研报未披露可与财务组件核对的核心财务数字",
            category="combined_numeric_consistency",
            expected="总投资、IRR、NPV、偿债指标及核心收入成本至少形成语义绑定",
            actual=None,
            target_location={"target_id": target_id},
            standard_basis=standard_basis,
            review_area="finance",
            remediation="补充核心财务披露并从绑定 run/package 自动取数",
        ))

    adverse: list[dict[str, Any]] = []
    for run in finance_runs:
        index = semantic_finance_index(run)
        irr_values = [float(row["value"]) for row in index.get("project_irr") or []]
        benchmark_values = [
            float(row["value"])
            for row in _flatten_numbers(run)
            if re.search(r"benchmark_rate|discount_rate|基准收益率", str(row["path"]), re.I)
        ]
        if irr_values and benchmark_values:
            irr = irr_values[0] * 100.0 if abs(irr_values[0]) <= 1.0 else irr_values[0]
            benchmark = benchmark_values[0] * 100.0 if abs(benchmark_values[0]) <= 1.0 else benchmark_values[0]
            if irr < benchmark:
                adverse.append({"reason": "irr_below_benchmark", "irr_pct": irr, "benchmark_pct": benchmark})
        dscr_values = [float(row["value"]) for row in index.get("dscr") or [] if float(row["value"]) > 0]
        if dscr_values and min(dscr_values) < 1.2:
            adverse.append({"reason": "dscr_below_1.2", "minimum_dscr": min(dscr_values)})
        icr_values = [float(row["value"]) for row in index.get("icr") or [] if float(row["value"]) > 0]
        if icr_values and min(icr_values) < 1.0:
            adverse.append({"reason": "icr_below_1.0", "minimum_icr": min(icr_values)})
        negative_plan = [
            row for row in _flatten_numbers(run)
            if "financial_plan" in str(row["path"]).lower()
            and "cumulative" in str(row["path"]).lower()
            and float(row["value"]) < 0
        ]
        if negative_plan:
            adverse.append({"reason": "negative_cumulative_surplus", "rows": negative_plan[:20]})
        if run.get("consistency_ok") is False:
            adverse.append({"reason": "finance_consistency_failed", "run_id": run.get("run_id")})
    positive = bool(re.search(r"(?:项目|本项目|财务).{0,30}(?:可行|具备偿债能力|建议实施|值得投资|风险可控)", all_content, re.S))
    negative = bool(re.search(r"(?:不可行|不具备偿债能力|不建议实施|风险不可控)", all_content))
    if positive and adverse:
        findings.append(rules.finding(
            "COMBINED.CONCLUSIONS.MATCH",
            "P0",
            "研报正面结论与财务组件的偿债、收益或可持续性结果相反",
            category="combined_conclusion_consistency",
            expected="结论与 IRR/基准收益率、DSCR、ICR、下行情景和累计盈余一致",
            actual={"positive_conclusion": True, "adverse_finance_signals": adverse},
            target_location={"target_id": target_id, "text_anchor": "结论"},
            standard_basis=standard_basis,
            review_area="finance",
            remediation="修正财务模型或结论，并完整披露下行情景、敏感性和主要风险",
        ))
    elif not positive and not negative:
        findings.append(rules.finding(
            "COMBINED.CONCLUSIONS.MATCH",
            "P1",
            "联合交付包缺少可与财务结果核对的明确可行性结论",
            category="combined_conclusion_consistency",
            expected="基于收益、偿债、下行情景和主要风险形成明确结论",
            actual=None,
            target_location={"target_id": target_id, "text_anchor": "结论"},
            standard_basis=standard_basis,
            review_area="report",
            remediation="补充明确结论并逐项引用对应财务指标和风险依据",
        ))
    metrics = {
        "financial_claims_compared": compared,
        "financial_claims_matched": matched,
        "adverse_finance_signals": adverse,
        "positive_conclusion_detected": positive,
        "negative_conclusion_detected": negative,
    }
    return findings, sorted(set(incomplete)), metrics, set(COMBINED_RULES)
