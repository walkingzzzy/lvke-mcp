"""项目上下文推断：贷款利率、年份序列、折旧年限/分类与成本项。"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from .base import (
    _norm,
    _to_float,
)

from .locate import (
    _find_row,
    _row_label,
    _rows,
    _series_for_row,
)

from .sheet_read import (
    _find_mapped_sheet,
)


def _extract_loan_rate(reference_pack: dict[str, Any]) -> Optional[float]:
    relevant = {
        "建设期贷款利息表", "还款付息测算表",
    }
    pattern = re.compile(r"年利率\s*([0-9]+(?:\.[0-9]+)?)\s*%")
    for sheet in (reference_pack.get("sheets") or {}).values():
        if sheet.get("business") not in relevant:
            continue
        for value in (sheet.get("values") or {}).values():
            if not isinstance(value, str):
                continue
            match = pattern.search(_norm(value))
            if match:
                return float(match.group(1)) / 100.0
        for item in (sheet.get("formulas") or {}).values():
            formula = str(item.get("formula") or "")
            for match in re.finditer(r"\*\s*(0\.\d+)", formula):
                rate = float(match.group(1))
                if 0 < rate < 0.2:
                    return rate
    return None


def _build_period_years(reference_pack: dict[str, Any]) -> int:
    project = (reference_pack.get("cashflows") or {}).get("project_after_tax") or []
    if project:
        return max(int(item.get("period") or 0) for item in project)
    return max(len(project), 1)


def _build_years(reference_pack: dict[str, Any]) -> int:
    revenue = (reference_pack.get("cashflows") or {}).get("project_revenue") or []
    positive = [int(item["period"]) for item in revenue if float(item.get("value") or 0.0) > 0]
    if positive:
        return max(min(positive) - 1, 1)
    cash = (reference_pack.get("cashflows") or {}).get("project_after_tax") or []
    first_positive = next(
        (index for index, item in enumerate(cash) if float(item.get("value") or 0.0) > 0),
        1,
    )
    return max(first_positive, 1)


def _extract_depreciation_years(reference_pack: dict[str, Any]) -> Optional[int]:
    """Read useful life from the depreciation appendix instead of deriving it.

    ``calculation period - construction period`` is not an accounting useful
    life and was the source of a material five-workbook divergence.
    """

    sheet = _find_mapped_sheet(reference_pack, "固定资产折旧费估算表")
    if not sheet:
        return None
    for row_index, row in sorted(_rows(sheet).items()):
        label = _norm(_row_label(row))
        if not any(token in label for token in ("折旧年限", "使用年限", "折旧期")):
            continue
        candidates = []
        for _col, (_cell, value) in sorted(row.items()):
            number = _to_float(value)
            if number is not None and 1 <= number <= 80:
                candidates.append(int(round(number)))
        if candidates:
            return candidates[-1]
    return None


def _extract_depreciation_classes(reference_pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract class-specific original value, useful life and implied salvage.

    Real feasibility workbooks often place ``折旧`` and ``年限`` on two header
    rows above a life column, then repeat ``类别 / 原值 / 当期折旧费 / 净值``
    blocks.  Keeping those classes avoids replacing 30/10/5-year assets with a
    synthetic project-period life.
    """

    sheet = _find_mapped_sheet(reference_pack, "固定资产折旧费估算表")
    if not sheet:
        return []
    rows = _rows(sheet)
    column_headers: dict[int, str] = defaultdict(str)
    for row_index, row in rows.items():
        if row_index > 8:
            continue
        for col, (_cell, raw) in row.items():
            if isinstance(raw, str):
                column_headers[col] += _norm(raw)
    life_columns = [
        col for col, text in column_headers.items()
        if "年限" in text and ("折旧" in text or "使用" in text)
    ]
    if not life_columns:
        return []

    descriptors = ("原值", "当期折旧费", "折旧费", "净值", "合计")
    total_columns = [
        col for row_index, row in rows.items() if row_index <= 8
        for col, (_cell, raw) in row.items()
        if isinstance(raw, str) and "合计" in _norm(raw)
    ]
    result: list[dict[str, Any]] = []
    for row_index, row in sorted(rows.items()):
        label = _norm(_row_label(row))
        if "原值" not in label or "合计" in label:
            continue
        name = ""
        for previous in range(row_index - 1, max(row_index - 4, 0), -1):
            prior = rows.get(previous) or {}
            candidate = next(
                (
                    str(raw).strip() for col, (_cell, raw) in sorted(prior.items())
                    if col <= 2 and isinstance(raw, str) and str(raw).strip()
                ),
                "",
            )
            if candidate and not any(token in _norm(candidate) for token in descriptors):
                name = candidate
                break
        if not name:
            continue
        original_cell = ""
        original: Optional[float] = None
        for col in total_columns:
            item = row.get(col)
            value = _to_float(item[1]) if item else None
            if value is not None:
                original = value
                original_cell = item[0]
                break
        if original is None:
            candidates = [
                (cell, value) for col, (cell, raw) in sorted(row.items())
                if col > 2 and col not in life_columns
                and (value := _to_float(raw)) is not None
            ]
            if candidates:
                original_cell, original = max(candidates, key=lambda item: abs(item[1]))
        if original is None or original <= 0:
            continue
        life_cell = ""
        life: Optional[int] = None
        for col in life_columns:
            item = row.get(col)
            value = _to_float(item[1]) if item else None
            if value is not None and 1 <= value <= 100:
                life = int(round(value))
                life_cell = item[0]
                break
        if life is None:
            continue
        annual_row_index, annual_label = _find_row(
            sheet, ["当期折旧费"], exclude=(),
        )
        # _find_row returns the first block; select this class's following row.
        if annual_row_index <= row_index:
            annual_row_index = next(
                (
                    candidate for candidate in range(row_index + 1, min(row_index + 4, max(rows) + 1))
                    if "当期折旧费" in _norm(_row_label(rows.get(candidate) or {}))
                ),
                0,
            )
        if annual_row_index and annual_row_index > row_index + 3:
            annual_row_index = 0
        annual_series = _series_for_row(sheet, annual_row_index) if annual_row_index else []
        annual_item = next(
            (item for item in annual_series if float(item.get("value") or 0.0) > 0),
            None,
        )
        annual = float(annual_item["value"]) if annual_item else None
        implied_salvage = 0.05
        if annual is not None and original > 0:
            candidate_salvage = 1.0 - annual * life / float(original)
            if -0.001 <= candidate_salvage <= 0.5:
                implied_salvage = round(max(candidate_salvage, 0.0), 6)
        result.append({
            "name": name,
            "original_value_wan": round(float(original), 6),
            "depreciation_years": life,
            "salvage_rate": implied_salvage,
            "vendor_annual_depreciation_wan": round(annual, 6) if annual is not None else None,
            "source_locators": {
                "original_value": original_cell,
                "depreciation_years": life_cell,
                "annual_depreciation": str((annual_item or {}).get("cell") or ""),
            },
        })
    return result


def _cost_items(reference_pack: dict[str, Any]) -> dict[str, float]:
    sheet = _find_mapped_sheet(reference_pack, "总成本费用估算表")
    if not sheet:
        values = [
            float(item["value"])
            for item in (reference_pack.get("cashflows") or {}).get("project_operating_cost") or []
            if float(item.get("value") or 0.0) > 0
        ]
        return {"经营成本": max(values)} if values else {}
    aliases = (
        "外购原材料", "燃料", "动力", "工资", "福利", "修理费",
        "营业费用", "销售费用", "其他费用", "经营成本",
    )
    result: dict[str, float] = {}
    for row_index, row in sorted(_rows(sheet).items()):
        label = _row_label(row)
        normalized = _norm(label)
        if not any(alias in normalized for alias in aliases):
            continue
        # 摊销/折旧非现金经营成本；不得并入 cost_items 达产现金口径（B-2 房地产簿误吸）
        if any(token in normalized for token in ("摊销", "折旧", "摊提")):
            continue
        if "经营成本" in normalized and result:
            continue
        series = _series_for_row(sheet, row_index)
        positives = [float(item["value"]) for item in series if float(item["value"]) > 0]
        if not positives:
            continue
        name = next(
            (str(value).strip() for col, (_, value) in sorted(row.items()) if col <= 4 and isinstance(value, str)),
            label,
        )
        result[name] = max(positives)
    # 经营成本合计行与明细（如销售费用）同值时去重，避免 period_opex 双倍（B-2 房地产簿）
    if result:
        detail_sum = sum(
            float(v) for k, v in result.items() if "经营成本" not in _norm(k)
        )
        for key in list(result):
            if "经营成本" not in _norm(key):
                continue
            total = float(result[key] or 0.0)
            if detail_sum > 0 and abs(total - detail_sum) <= max(0.02, abs(total) * 1e-6):
                result.pop(key, None)
            elif detail_sum > 0 and total > 0:
                # 有明细时优先明细，丢弃合计行
                result.pop(key, None)
    if not result:
        values = [
            float(item["value"])
            for item in (reference_pack.get("cashflows") or {}).get("project_operating_cost") or []
            if float(item.get("value") or 0.0) > 0
        ]
        if values:
            result["经营成本"] = max(values)
    return result


def infer_vendor_project_context(reference_pack: dict[str, Any]) -> dict[str, Any]:
    name = str((reference_pack.get("source") or {}).get("workbook_name") or "")
    normalized = _norm(name)
    revenue_sheet = _find_mapped_sheet(
        reference_pack, "营业收入、税金及附加和增值税估算表"
    )
    revenue_labels = [
        _norm(_row_label(row)) for row in _rows(revenue_sheet).values()
    ] if revenue_sheet else []
    has_product_detail = (
        sum(1 for label in revenue_labels if "单价" in label) >= 1
        and sum(1 for label in revenue_labels if "数量" in label) >= 1
    )
    if "房地产" in normalized or any(
        sheet.get("business") == "营业收入、税金及附加和增值税估算表"
        and "不动产" in _norm(" ".join(map(str, (sheet.get("values") or {}).values())))
        for sheet in (reference_pack.get("sheets") or {}).values()
    ):
        industry = "房地产"
        revenue_model = "property_sales"
    elif "产品出售" in normalized:
        industry = "制造业"
        revenue_model = "product_sales"
    elif "厂房出租" in normalized:
        industry = "产业园区租赁"
        revenue_model = "lease_portfolio"
    elif "墓地" in normalized:
        industry = "殡葬服务"
        revenue_model = "inventory_sales"
    elif any(keyword in normalized for keyword in ("石灰岩", "骨料", "机制砂")) or has_product_detail:
        industry = "建材深加工"
        revenue_model = "product_sales"
    else:
        industry = ""
        revenue_model = "flat"
    return {
        "invest_type": "enterprise",
        "industry": industry,
        "build_period_months": _build_years(reference_pack) * 12,
        "revenue_model": revenue_model,
        "source": "vendor_reference_C",
    }
