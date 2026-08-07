"""指标与敏感性摘要提取、试算利率复核。"""

from __future__ import annotations

import re
from typing import Any, Optional

from .base import (
    _norm,
    _to_float,
)

from .locate import (
    _row_label,
    _rows,
)

from .sheet_read import (
    _find_mapped_sheet,
    _header_text,
)


def _review_trial_rates(review_sheet: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not review_sheet:
        return {}
    rows = _rows(review_sheet)
    formulas = review_sheet.get("formulas") or {}
    result: dict[str, dict[str, Any]] = {}
    capital_marker = 0
    trial_index = 0
    for row_index, row in sorted(rows.items()):
        if "资本金内部收益率" in _norm(_row_label(row)):
            capital_marker = row_index
        if "试算使净现值之和为0" not in _norm(_row_label(row)):
            continue
        candidate = None
        for col, (cell, raw) in sorted(row.items()):
            if col < 3:
                continue
            value = _to_float(raw)
            if value is not None:
                candidate = (cell, value)
                break
        if not candidate:
            continue
        trial_index += 1
        next_label = _norm(_row_label(rows.get(row_index + 1) or {}))
        # Several vendor templates omit the capital-IRR section title entirely;
        # their third trial block is nevertheless the capital cash-flow trial.
        if (capital_marker and row_index > capital_marker) or trial_index >= 3:
            key = "capital_after_tax"
        elif "税前" in next_label:
            key = "project_pre_tax"
        else:
            key = "project_after_tax"
        result[key] = {
            "rate": candidate[1],
            "rate_pct": candidate[1] * 100.0 if abs(candidate[1]) <= 1.0 else candidate[1],
            "cell": candidate[0],
            "hardcoded": candidate[0] not in formulas,
        }
    return result


def _extract_indicator_summary(reference_pack: dict[str, Any]) -> dict[str, Any]:
    """Structure the vendor's key-indicator sheet with cell-level provenance.

    The summary is a read-only reference surface.  Its values never replace an
    approved engine run, but they are consumed by reference-track comparison and
    reverse-reconciliation checks instead of being left as an unmapped worksheet.
    """

    sheet = _find_mapped_sheet(reference_pack, "主要技术经济指标汇总表")
    if not sheet:
        return {"available": False, "rows": [], "normalized": {}, "warnings": ["未找到指标汇总表"]}
    sheet_name = str((sheet.get("mapping") or {}).get("vendor_sheet_actual") or "")
    rows = _rows(sheet)
    header_row = 0
    for row_index, cells in sorted(rows.items()):
        text = _norm(" ".join(str(value) for _, value in cells.values() if value is not None))
        if "指标名称" in text and ("数据" in text or "数值" in text):
            header_row = row_index
            break
    if not header_row:
        return {
            "available": False,
            "sheet_name": sheet_name,
            "rows": [],
            "normalized": {},
            "warnings": ["指标汇总表缺少可识别表头"],
        }

    structured: list[dict[str, Any]] = []
    normalized: dict[str, dict[str, Any]] = {}

    def bind(key: str, value: Any, locator: str, label: str, unit: str) -> None:
        number = _to_float(value)
        if number is None or key in normalized:
            return
        normalized[key] = {
            "value": number,
            "locator": locator,
            "label": label,
            "unit": unit,
        }

    for row_index, cells in sorted(rows.items()):
        if row_index <= header_row:
            continue
        label_cell, label_raw = cells.get(2, ("", None))
        label = str(label_raw or "").strip()
        if not label:
            continue
        unit_cell, unit_raw = cells.get(3, ("", None))
        unit = str(unit_raw or "").strip()
        seq_cell, seq_raw = cells.get(1, ("", None))
        remark_cell, remark_raw = cells.get(6, ("", None))
        values = []
        for col_index, scope in ((4, "primary"), (5, "secondary")):
            cell, raw = cells.get(col_index, ("", None))
            if _to_float(raw) is None:
                continue
            values.append({
                "scope": scope,
                "value": _to_float(raw),
                "cell": cell,
                "locator": f"{sheet_name}!{cell}",
            })
        structured.append({
            "row": row_index,
            "seq": seq_raw,
            "indicator": label,
            "unit": unit,
            "values": values,
            "remark": remark_raw,
            "locators": {
                "seq": f"{sheet_name}!{seq_cell}" if seq_cell else "",
                "indicator": f"{sheet_name}!{label_cell}" if label_cell else "",
                "unit": f"{sheet_name}!{unit_cell}" if unit_cell else "",
                "remark": f"{sheet_name}!{remark_cell}" if remark_cell else "",
            },
        })
        primary = values[0] if values else None
        secondary = values[1] if len(values) > 1 else None
        text = _norm(label)
        if primary:
            if "项目总投资" in text and "资金来源" not in text:
                bind("total_investment", primary["value"], primary["locator"], label, unit)
            elif "固定资产投资" in text and "资金来源" not in text:
                bind("construction_investment", primary["value"], primary["locator"], label, unit)
            elif "流动资金" in text and ("新增" in text or "铺底" in text):
                bind("working_capital", primary["value"], primary["locator"], label, unit)
            elif "申请贷款" in text or "银行长期贷款" in text:
                bind("loan", primary["value"], primary["locator"], label, unit)
            elif ("自筹资金" in text or "企业自筹" in text) and "流动资金" not in text:
                bind("equity_capital", primary["value"], primary["locator"], label, unit)
            elif text == "建设期":
                bind("build_period_years", primary["value"], primary["locator"], label, unit)
            elif text in {"经营收入", "销售收入(不含税)", "营业收入"}:
                bind("revenue", primary["value"], primary["locator"], label, unit)
            elif "资本金内部收益率" in text:
                bind("capital_irr_pct", primary["value"], primary["locator"], label, unit)
            elif "项目财务内部收益率" in text:
                bind("project_irr_pre_tax_pct", primary["value"], primary["locator"], label, unit)
                if secondary:
                    bind("project_irr_pct", secondary["value"], secondary["locator"], label, unit)
            elif "项目财务净现值" in text:
                bind("npv_pre_tax_wan", primary["value"], primary["locator"], label, unit)
                if secondary:
                    bind("npv_wan", secondary["value"], secondary["locator"], label, unit)
            elif "项目投资回收期" in text:
                bind("payback_pre_tax_years", primary["value"], primary["locator"], label, unit)
                if secondary:
                    bind("static_payback_years", secondary["value"], secondary["locator"], label, unit)

    return {
        "available": bool(structured),
        "sheet_name": sheet_name,
        "header_row": header_row,
        "rows": structured,
        "normalized": normalized,
        "warnings": [],
    }


def _sensitivity_factor_code(label: str) -> str:
    text = _norm(label)
    if "建设投资" in text or "固定资产投资" in text:
        return "construction_investment"
    if "服务价格" in text:
        return "service_price"
    if "产品价格" in text or "销售价格" in text:
        return "product_price"
    if "直接成本" in text:
        return "direct_cost"
    if "运营负荷" in text or "生产负荷" in text or "经营负荷" in text:
        return "operating_load"
    return re.sub(r"[^0-9A-Za-z_]+", "_", text).strip("_").lower() or "unknown"


def _extract_sensitivity_summary(
    reference_pack: dict[str, Any],
    indicator_summary: dict[str, Any],
) -> dict[str, Any]:
    """Extract the real factory/cemetery sensitivity overview and provenance."""

    overview: Optional[dict[str, Any]] = None
    sheet_name = ""
    supporting: dict[str, str] = {}
    for name, candidate in (reference_pack.get("sheets") or {}).items():
        if candidate.get("business") != "单因素敏感性分析表":
            continue
        header = _norm(_header_text(candidate, max_rows=2))
        if "不确定性因数" in header and "内部收益率" in header:
            overview = candidate
            sheet_name = str(name)
            continue
        code = _sensitivity_factor_code(str(name))
        if code != "unknown":
            supporting.setdefault(code, str(name))
    if not overview:
        return {"available": False, "factors": [], "warnings": ["未找到敏感性汇总页"]}

    factors: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    base: dict[str, Any] = {}
    for row_index, cells in sorted(_rows(overview).items()):
        factor_cell, factor_raw = cells.get(1, ("", None))
        factor_label = str(factor_raw or "").strip()
        change_cell, change_raw = cells.get(2, ("", None))
        irr_cell, irr_raw = cells.get(3, ("", None))
        coefficient_cell, coefficient_raw = cells.get(4, ("", None))
        critical_cell, critical_raw = cells.get(5, ("", None))
        irr = _to_float(irr_raw)
        if factor_label and "基本方案" in _norm(factor_label) and irr is not None:
            base = {
                "irr_pct": irr,
                "row": row_index,
                "factor_locator": f"{sheet_name}!{factor_cell}",
                "irr_locator": f"{sheet_name}!{irr_cell}",
            }
            current = None
            continue
        change = _to_float(change_raw)
        if factor_label and change is not None and irr is not None:
            code = _sensitivity_factor_code(factor_label)
            current = {
                "factor": factor_label,
                "factor_code": code,
                "factor_locator": f"{sheet_name}!{factor_cell}",
                "supporting_sheet": supporting.get(code, ""),
                "critical_point_pct": None,
                "critical_point_locator": "",
                "scenarios": [],
            }
            factors.append(current)
        if current is None or change is None or irr is None:
            continue
        critical = _to_float(critical_raw)
        if critical is not None:
            current["critical_point_pct"] = critical
            current["critical_point_locator"] = f"{sheet_name}!{critical_cell}"
        current["scenarios"].append({
            "change_ratio": change,
            "change_pct": change * 100.0 if abs(change) <= 1.0 else change,
            "irr_pct": irr,
            "sensitivity_coefficient": _to_float(coefficient_raw),
            "row": row_index,
            "locators": {
                "change": f"{sheet_name}!{change_cell}",
                "irr": f"{sheet_name}!{irr_cell}",
                "coefficient": f"{sheet_name}!{coefficient_cell}" if coefficient_cell else "",
            },
        })

    expected = ((indicator_summary.get("normalized") or {}).get("project_irr_pre_tax_pct") or {})
    expected_value = _to_float(expected.get("value"))
    base_value = _to_float(base.get("irr_pct"))
    reconciliation = {
        "summary_value": expected_value,
        "summary_locator": expected.get("locator") or "",
        "sensitivity_value": base_value,
        "sensitivity_locator": base.get("irr_locator") or "",
        "tolerance_percentage_points": 0.01,
        "delta_percentage_points": (
            abs(base_value - expected_value)
            if base_value is not None and expected_value is not None else None
        ),
    }
    reconciliation["within_tolerance"] = (
        reconciliation["delta_percentage_points"] is not None
        and reconciliation["delta_percentage_points"] <= 0.01
    )
    warnings = []
    if not base:
        warnings.append("敏感性汇总页缺少基本方案IRR")
    if factors and any(len(item.get("scenarios") or []) < 2 for item in factors):
        warnings.append("至少一个敏感性因素缺少完整变动档位")
    if expected_value is not None and not reconciliation["within_tolerance"]:
        warnings.append("敏感性基准IRR与主要指标汇总表不一致")
    return {
        "available": bool(base and factors),
        "sheet_name": sheet_name,
        "base": base,
        "factors": factors,
        "supporting_sheets": supporting,
        "base_reconciliation": reconciliation,
        "warnings": warnings,
    }
