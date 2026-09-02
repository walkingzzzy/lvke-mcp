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
    _UNITLESS_RATIO_PATTERN,
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


#: 引述来源标记：出现在数字左侧、说明"这是谁的口径"。命中即认为该数字引自外部
#: 规划/统计，不是本项目财务指标。只收对**主体归属**有判别力的词——"预计/约"这类
#: 不确定性副词不在此列（本项目自己的预测也会用），否则会漏掉真的错绑。
_CITATION_SCOPE_MARKERS: tuple[str, ...] = (
    "规划提出", "规划明确", "规划要求", "规划将", "发展规划",
    "力争到", "根据规划", "按照规划", "上述规划", "两份规划",
    "全省", "全市", "全国", "全区", "本省", "本市",
    "国家统计局", "统计公报", "统计年鉴", "行业协会", "白皮书",
    "同业", "可比项目", "参照", "参考值", "行业均值", "行业平均",
)

#: 项目自身归属标记：命中时优先判为项目指标，即使同句另有引述标记。
#: 例如"本项目营收 97,680 万元，占全省 5,500 亿元的 1.78%"——97,680 左侧最近的
#: 归属词是"本项目"，不能因为句尾出现"全省"就放过它。
_PROJECT_SCOPE_MARKERS: tuple[str, ...] = (
    "本项目", "该项目", "项目达产", "达产年", "本工程", "项目总投资", "本报告",
)


def _citation_scope(line: str, start: int) -> str:
    """Return ``"external"`` when the number is quoted from an outside caliber.

    只看数字左侧：右侧文本属于下一个论断，纳入会让"本项目 X，全省 Y"里的 X 也
    被误判为引文。左侧取最近的归属标记——项目标记比引述标记更近则判 project。
    返回 ``""`` 表示无法判定，按既有 fail-closed 逻辑照常校验绑定。
    """

    left = line[:start]
    if not left:
        return ""
    nearest_citation = max((left.rfind(token) for token in _CITATION_SCOPE_MARKERS), default=-1)
    nearest_project = max((left.rfind(token) for token in _PROJECT_SCOPE_MARKERS), default=-1)
    if nearest_citation < 0:
        return ""
    if nearest_project > nearest_citation:
        return "project"
    return "external"


#: 情景/敏感性语境：同一指标在这类行里**本来就该**有多个值（收入 −20%/基准/+20%
#: 各一个 NPV），按单一口径判冲突是把正确的分析判成矛盾。
_SCENARIO_MARKERS: tuple[str, ...] = (
    "敏感性", "敏感系数", "情景", "乐观", "悲观", "基准情景", "压力测试",
    "下降", "上升", "上浮", "下浮", "变动", "波动",
)

#: 差异披露语境：正文主动说明"A 与 B 差 C"是**披露**而非自相矛盾。这类行天然
#: 同时出现两三个同口径数字（分项净额 747.18 / 估算 1500.00 / 差额 752.82）。
_VARIANCE_MARKERS: tuple[str, ...] = (
    "差额", "相差", "差异", "偏差", "不一致", "未收敛", "口径存在",
    "与估算", "尚需核对", "有待核实",
)


def _variance_context(line: str) -> str:
    """标注整行语境：``scenario`` / ``variance`` / ``""``（普通论断）。

    只做行级判定，与 ``_citation_scope`` 同样是"最近标记"式的确定性规则，不猜。
    返回值进入一致性分组键，使这两类行与普通论断分桶，而**不是**豁免检查——
    同一情景内部若仍有多值冲突，照旧报出。
    """

    if any(token in line for token in _VARIANCE_MARKERS):
        return "variance"
    if any(token in line for token in _SCENARIO_MARKERS):
        return "scenario"
    return ""


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
        occupied_spans: set[tuple[int, int]] = set()

        def append_claim(
            *,
            start: int,
            end: int,
            text: str,
            raw_value: float,
            raw_unit: str,
            metric_override: str = "",
        ) -> None:
            value = _canonical_value(raw_value, raw_unit)
            unit = _canonical_unit(raw_unit)
            context_start = max(0, start - 40)
            context_end = min(len(line), end + 40)
            context = line[context_start:context_end]
            # 引述语境判别：正文引用外部规划/统计口径的数字（"规划提出…全省…达到
            # 5,500 亿元"）不是本项目财务指标，不该要求在 run 里复现。实测 7 条
            # REPORT.NUMBERS.BOUND 里 4 条是这类假阳性——省级 5,500 亿/1,000 亿
            # 被当作项目营业收入。
            # 判据是**数字左侧的引述标记**（谁的口径），不是数值大小：
            # 数值阈值会把真的量级错绑一起放过。右侧不看，避免"本项目营收 X，
            # 全省目标 Y"这种句子把 X 也误判成引文。
            citation_scope = _citation_scope(line, start)
            metric = metric_override or _semantic_near(line, start, end, raw_unit)
            period = _period_near(line, start, end)
            if unit == "间" and not metric:
                metric = "room_count"
            if unit == "㎡" and not metric:
                metric = "area"
            if unit == "公里" and not metric:
                metric = "market_radius"
            if unit in {"吨", "吨/年"} and not metric:
                metric = "capacity"
            if unit == "年" and 1900 <= value <= 2100 and re.search(r"(?:20\d{2})年", text):
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
                "char_offset_start": start,
                "char_offset_end": end,
                "text_anchor": line[:160],
            }
            claim_id = "clm_" + sha256_json({
                "target_id": target_id,
                "line": line_index,
                "span": (start, end),
                "text": line,
            }).removeprefix("sha256:")[:24]
            claims.append({
                "claim_id": claim_id,
                "text": text,
                "context": context,
                "claim_type": claim_type,
                "citation_scope": citation_scope,
                "variance_context": _variance_context(line),
                "metric": metric,
                "value": value,
                "unit": unit,
                "raw_value": raw_value,
                "raw_unit": raw_unit,
                "period": period,
                "location": location,
            })
            occupied_spans.add((start, end))

        for match in _NUMBER_PATTERN.finditer(line):
            append_claim(
                start=match.start(),
                end=match.end(),
                text=match.group(0),
                raw_value=float(match.group("number").replace(",", "")),
                raw_unit=match.group("unit"),
            )
        for match in _UNITLESS_RATIO_PATTERN.finditer(line):
            number_group = "number_before" if match.group("number_before") else "number_after"
            start, end = match.span(number_group)
            if any(not (end <= left or start >= right) for left, right in occupied_spans):
                continue
            label = str(match.group("label_before") or match.group("label_after") or "").upper()
            append_claim(
                start=start,
                end=end,
                text=line[start:end],
                raw_value=float(match.group(number_group)),
                raw_unit="",
                metric_override="icr" if label in {"ICR", "利息备付率"} else "dscr",
            )
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
