"""语义财务索引与 claim graph 构造。"""

from __future__ import annotations

import re
from typing import Any

from lvke_mcp.runtime.storage import sha256_json

from .normalize import (
    _canonical_unit,
    _canonical_value,
    _flatten_numbers,
    _number,
    _period_near,
    _semantic_near,
    _within_tolerance,
)

from .patterns import (
    _FINANCIAL_METRICS,
    _NUMBER_PATTERN,
    _PATH_PATTERNS,
)


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
