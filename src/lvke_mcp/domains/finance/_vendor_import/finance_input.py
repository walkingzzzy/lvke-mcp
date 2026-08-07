"""从甲方表构造财务输入与 spec：资金计划、建设明细、营运资金、人员与产品线。"""

from __future__ import annotations

from typing import Any, Optional

from .base import (
    _norm,
    _to_float,
)

from .locate import (
    _find_row,
    _row_label,
    _rows,
    _series_by_label,
    _series_for_row,
    _series_values,
    _total_by_label,
    _total_for_row,
)

from .project_context import (
    _build_period_years,
    _cost_items,
    _extract_depreciation_classes,
    _extract_depreciation_years,
    _extract_loan_rate,
    infer_vendor_project_context,
)

from .sheet_read import (
    _find_mapped_sheet,
)


def _funding_year_schedules(reference_pack: dict[str, Any]) -> dict[str, list[float]]:
    """Read construction/loan/equity year plans from 投资使用计划与资金筹措表.

    These are *input* parameters (how much is spent/borrowed each year), not
    engine outputs. Used to replace uniform build-period averaging (B-2).
    """
    funding = _find_mapped_sheet(reference_pack, "投资使用计划与资金筹措表")
    if not funding:
        return {}
    construction = _series_values(
        _series_by_label(funding, ["建设投资"], exclude=["总投资", "使用计划"])
    )
    idc = _series_values(
        _series_by_label(funding, ["建设期", "利息"], exclude=["合计表"])
    )
    if not idc:
        idc = _series_values(_series_by_label(funding, ["建设期贷款利息"]))
    loan_draw = _series_values(
        _series_by_label(funding, ["申请银行长期借款"])
    )
    if not loan_draw:
        loan_draw = _series_values(
            _series_by_label(funding, ["银行长期借款"], exclude=["短期"])
        )
    equity = _series_values(
        _series_by_label(funding, ["项目资本金"], exclude=["用于"])
    )
    wc = _series_values(
        _series_by_label(funding, ["新增铺底流动资金"])
    )
    if not wc:
        wc = _series_values(_series_by_label(funding, ["新增流动资金"]))
    if not wc:
        wc = _series_values(_series_by_label(funding, ["流动资金"], exclude=["用于", "借款"]))

    # Build-year cash outlay: choose construction-only vs construction+IDC by
    # matching the vendor project CF build-year negatives when available.
    outlay: list[float] = []
    cand_c: list[float] = list(construction)
    cand_ci: list[float] = []
    if construction or idc:
        n = max(len(construction), len(idc))
        for i in range(n):
            c = construction[i] if i < len(construction) else 0.0
            d = idc[i] if i < len(idc) else 0.0
            cand_ci.append(round(float(c) + float(d), 6))
        while cand_ci and cand_ci[-1] == 0.0:
            cand_ci.pop()
        while cand_c and cand_c[-1] == 0.0:
            cand_c.pop()

    project_after = _series_values(
        (reference_pack.get("cashflows") or {}).get("project_after_tax")
    )
    # Build years are leading non-positive project CF years.
    build_ref: list[float] = []
    for value in project_after:
        if value <= 0:
            build_ref.append(round(abs(float(value)), 6))
        else:
            break

    def _l1(a: list[float], b: list[float]) -> float:
        n = max(len(a), len(b))
        s = 0.0
        for i in range(n):
            av = a[i] if i < len(a) else 0.0
            bv = b[i] if i < len(b) else 0.0
            s += abs(av - bv)
        return s

    if build_ref and (cand_c or cand_ci):
        err_c = _l1(cand_c, build_ref) if cand_c else float("inf")
        err_ci = _l1(cand_ci, build_ref) if cand_ci else float("inf")
        outlay = cand_ci if err_ci <= err_c else cand_c
    elif cand_ci:
        outlay = cand_ci
    elif cand_c:
        outlay = cand_c
    else:
        total_plan = _series_values(
            _series_by_label(funding, ["总投资"], exclude=["使用计划", "资金筹措表"])
        )
        if total_plan:
            outlay = list(total_plan)
            if wc and outlay and abs(outlay[-1] - wc[-1]) <= max(0.02, abs(outlay[-1]) * 1e-6):
                outlay = outlay[:-1]

    result: dict[str, list[float]] = {}
    if outlay and any(v > 0 for v in outlay):
        result["construction_outlay_by_year"] = outlay
    if construction and any(v > 0 for v in construction):
        result["construction_invest_by_year"] = construction
    if idc and any(v > 0 for v in idc):
        result["idc_by_year"] = idc
    if loan_draw and any(v > 0 for v in loan_draw):
        result["loan_draw_by_year"] = loan_draw
    if equity and any(v > 0 for v in equity):
        result["equity_inject_by_year"] = equity
    if wc and any(v > 0 for v in wc):
        result["working_capital_by_year"] = wc
    return result


def _construction_items_from_investment_sheet(reference_pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract qty×indicator engineering lines from 固定资产投资估算表 (input side).

    Only rows with unit + positive quantity + positive indicator are kept so
    formal 附表1 can show reference-grade quantity/indicator without inventing
    reverse-engineered quantities (B-4).
    """
    sheet = _find_mapped_sheet(reference_pack, "固定资产投资估算表")
    if not sheet:
        return []
    other_section_row, _ = _find_row(sheet, ["工程建设其他费用"])
    items: list[dict[str, Any]] = []
    for row_index, row in sorted(_rows(sheet).items()):
        if other_section_row and row_index >= other_section_row:
            break
        label = _row_label(row)
        if not label:
            continue
        normalized = _norm(label)
        # skip pure section headers without qty
        unit = None
        quantity = None
        indicator = None
        # Total columns vary across vendor templates.  Resolve the header-backed
        # total instead of assuming I/J; direct-amount rows may legitimately
        # omit a unit indicator (for example, an installation-material lump sum).
        amount = _total_for_row(sheet, row_index)
        for col, cell in sorted(row.items()):
            if col == 3 and isinstance(cell[1], str) and str(cell[1]).strip():
                unit = str(cell[1]).strip().replace("\n", "")
            if col == 4:
                quantity = _to_float(cell[1])
            if col == 5:
                indicator = _to_float(cell[1])
        sequence = row.get(1, ("", None))[1]
        sequence_text = str(sequence or "").strip()
        if (
            (indicator is None or indicator <= 0)
            and sequence_text
            and "." not in sequence_text
        ):
            # Integer-level rows are section subtotals (for example
            # “1 建筑工程”); their children carry the actual input lines.
            continue
        if quantity is None or quantity <= 0:
            continue
        if not unit:
            continue
        if (indicator is None or indicator <= 0) and (amount is None or amount <= 0):
            continue
        # category heuristic
        category = "other"
        if any(k in normalized for k in ("车间", "楼", "门卫", "道路", "绿化", "广场", "建筑", "土建")):
            category = "civil"
        elif any(k in normalized for k in ("设备", "电梯", "吊车", "光伏", "家具", "充电")):
            category = "equipment"
        elif any(k in normalized for k in ("安装", "给排水", "供配电", "弱电", "消防", "通风", "安防")):
            category = "installation"
        name = next(
            (
                str(cell[1]).strip()
                for col, cell in sorted(row.items())
                if col == 2 and isinstance(cell[1], str) and str(cell[1]).strip()
            ),
            label,
        )
        items.append({
            "name": name,
            "unit": unit,
            "quantity": round(float(quantity), 6),
            **(
                {"indicator_yuan": round(float(indicator), 6)}
                if indicator is not None and indicator > 0
                else {}
            ),
            "amount_wan": (
                round(float(amount), 6)
                if amount is not None and amount > 0
                else round(float(quantity) * float(indicator) / 10000.0, 6)
            ),
            "category": category,
            "source_row": row_index,
        })
    return items


def _construction_detail_from_items(items: list[dict[str, Any]]) -> dict[str, float]:
    """Map qty×indicator items into engine construction_detail three buckets.

    Engine / quality_audit expect civil+equipment+installation (=建设投资工程费用三段).
    Uncategorized amounts fold into civil rather than inventing a fourth key.
    """
    civil = equipment = installation = 0.0
    for item in items:
        amount = float(item.get("amount_wan") or 0.0)
        cat = str(item.get("category") or "other")
        if cat == "equipment":
            equipment += amount
        elif cat == "installation":
            installation += amount
        else:
            civil += amount
    return {
        "civil_wan": round(civil, 6),
        "equipment_wan": round(equipment, 6),
        "installation_wan": round(installation, 6),
    }


def _wc_turnover_from_sheet(reference_pack: dict[str, Any]) -> dict[str, Any] | None:
    """Extract min turnover days from 流动资金估算表 (input parameters).

    保留存货子树（原材料/燃料/在产品/产成品），供 reference 结构门禁使用；
    同时提供兼容字段 inventory（优先原材料天数）给引擎既有周转法。
    """
    sheet = _find_mapped_sheet(reference_pack, "流动资金估算表")
    if not sheet:
        return None
    mapping = {
        "应收账款": "receivable",
        "存货": "inventory",
        "原材料": "raw",
        "燃料": "fuel",
        "动力": "fuel",
        "在产品": "wip",
        "产成品": "finished",
        "现金": "cash",
        "应付账款": "payable",
    }
    out: dict[str, Any] = {}
    inv_detail: dict[str, float] = {}
    for row_index, row in sorted(_rows(sheet).items()):
        label = _row_label(row)
        normalized = _norm(label)
        for zh, key in mapping.items():
            if zh not in normalized:
                continue
            days = None
            for col, cell in sorted(row.items()):
                if col == 3:
                    days = _to_float(cell[1])
                    break
            if days is None or days <= 0:
                continue
            days_f = round(float(days), 6)
            series = _series_for_row(sheet, row_index)
            mature = max(
                (float(item.get("value") or 0.0) for item in series),
                default=0.0,
            )
            component: Any = days_f
            if mature > 0:
                mature_item = max(
                    series,
                    key=lambda item: float(item.get("value") or 0.0),
                )
                component = {
                    "days": days_f,
                    "annual_base_wan": round(mature * 360.0 / days_f, 6),
                    "base_source": (
                        f"{sheet.get('name') or '流动资金估算表'}!"
                        f"{mature_item.get('cell') or ''}"
                    ).rstrip("!"),
                }
            if key in {"raw", "fuel", "wip", "finished"}:
                inv_detail.setdefault(key, component)
            elif key not in out:
                out[key] = component
    if inv_detail:
        out["inventory_detail"] = inv_detail
        # 兼容引擎：inventory 天数优先用原材料，否则用已有 inventory
        if "inventory" not in out:
            first = inv_detail.get("raw") or inv_detail.get("finished") or next(iter(inv_detail.values()))
            out["inventory"] = (
                first.get("days") if isinstance(first, dict) else first
            )
    return out or None


def _full_working_capital_profile(reference_pack: dict[str, Any]) -> dict[str, Any]:
    """Return full turnover working capital, distinct from initial/铺底 funding."""

    sheet = _find_mapped_sheet(reference_pack, "流动资金估算表")
    if not sheet:
        return {}
    total_series: list[dict[str, Any]] = []
    increase_series: list[dict[str, Any]] = []
    excluded = tuple(_norm(value) for value in ("铺底", "当期", "来源", "借款", "自筹"))
    for row_index, row in sorted(_rows(sheet).items()):
        normalized = _norm(_row_label(row))
        series = _series_for_row(sheet, row_index)
        if not series:
            continue
        if "流动资金" in normalized and "当期增加额" in normalized:
            increase_series = series
        elif not total_series and "流动资金" in normalized and not any(
            token in normalized for token in excluded
        ):
            total_series = series
    positive = [
        item for item in total_series if float(item.get("value") or 0.0) > 0
    ]
    if not positive:
        return {}
    peak_item = max(positive, key=lambda item: float(item.get("value") or 0.0))
    return {
        "total_wan": round(float(peak_item.get("value") or 0.0), 6),
        "total_locator": (
            f"{sheet.get('name') or '流动资金估算表'}!"
            f"{peak_item.get('cell') or ''}"
        ).rstrip("!"),
        "increase_by_year": _series_values(increase_series),
    }


def _investment_segment_totals(reference_pack: dict[str, Any]) -> dict[str, float]:
    sheet = _find_mapped_sheet(reference_pack, "固定资产投资估算表")
    if not sheet:
        return {}
    values = {
        "engineering_wan": _total_by_label(
            sheet,
            ["工程费用"],
            exclude=["工程建设其他费用"],
        ),
        "other_wan": _total_by_label(sheet, ["工程建设其他费用"]),
        "reserve_wan": _total_by_label(sheet, ["预备费"]),
    }
    return {
        key: round(float(value), 6)
        for key, value in values.items()
        if value is not None and value >= 0
    }


def _staff_detail_from_wage_sheet(reference_pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract labor headcount × average wage from 工资及福利费估算表."""
    sheet = (
        _find_mapped_sheet(reference_pack, "工资及福利费估算表")
        or _find_mapped_sheet(reference_pack, "工资及附加估算表")
    )
    if not sheet:
        return []
    rows = _rows(sheet)
    headcount = None
    avg_wage = None
    for _idx, row in sorted(rows.items()):
        label = _norm(_row_label(row))
        if "人数" in label or "劳动定员" in label:
            for col, cell in sorted(row.items()):
                if col >= 4:
                    val = _to_float(cell[1])
                    if val is not None and val > 0:
                        headcount = val
                        break
        if "人均" in label and "工资" in label:
            for col, cell in sorted(row.items()):
                if col >= 4:
                    val = _to_float(cell[1])
                    if val is not None and val > 0:
                        avg_wage = val
                        break
    if headcount and avg_wage:
        return [{
            "category": "劳动定员",
            "headcount": int(round(headcount)),
            "avg_wage_yuan": round(float(avg_wage), 2),
        }]
    return []


def build_finance_input_from_vendor(reference_pack: dict[str, Any]) -> dict[str, Any]:
    """Build deterministic engine inputs from vendor *input parameters*, not outputs."""
    indicators = reference_pack.get("vendor_indicators") or {}
    vendor_total = _to_float(indicators.get("total_investment"))
    if vendor_total is None or vendor_total <= 0:
        return {
            "is_operating": True,
            "input_sources": {},
            "_missing_inputs": ["total_investment_wan"],
        }
    interest = max(_to_float(indicators.get("interest_during_construction")) or 0.0, 0.0)
    initial_working = max(_to_float(indicators.get("working_capital")) or 0.0, 0.0)
    construction = round(vendor_total - interest - initial_working, 6)
    full_wc_profile = _full_working_capital_profile(reference_pack)
    working = max(
        _to_float(full_wc_profile.get("total_wan")) or initial_working,
        0.0,
    )
    total = round(construction + interest + working, 6)
    capital = _to_float(indicators.get("equity_capital"))
    loan = _to_float(indicators.get("loan"))
    short_working_loan = max(
        (
            float(item.get("value") or 0.0)
            for item in indicators.get("short_term_working_capital_borrowing_schedule") or []
            if isinstance(item, dict)
        ),
        default=0.0,
    )
    if working > initial_working and short_working_loan > 0:
        loan = round(float(loan or 0.0) + short_working_loan, 6)
    revenue_series = (reference_pack.get("cashflows") or {}).get("project_revenue") or []
    revenue_values = [float(item["value"]) for item in revenue_series if float(item.get("value") or 0.0) > 0]
    annual_revenue = max(revenue_values) if revenue_values else None
    principal = (reference_pack.get("cashflows") or {}).get("debt_principal") or []
    loan_years = sum(1 for item in principal if float(item.get("value") or 0.0) > 0)
    # Prefer full 还本 series including zeros only if positive exists
    _principal_vals = [
        round(float(item.get("value") or 0.0), 6)
        for item in principal
        if isinstance(item, dict)
    ]
    context = infer_vendor_project_context(reference_pack)
    source_ref = str((reference_pack.get("source") or {}).get("path") or "")
    source_fields = (
        "total_investment_wan", "invest_breakdown", "capital_own_wan", "loan_wan",
        "loan_rate", "loan_years", "calc_period_years", "annual_revenue_wan",
        "cost_items", "operating_cost_by_year",
        "construction_outlay_by_year", "loan_draw_by_year",
    )
    input_sources = {
        field: {
            "note": "从甲方计算表输入参数抽取；仅C级参考，输出由我方引擎重算",
            "source_ref": source_ref,
            "evidence_level": "C",
        }
        for field in source_fields
    }
    # 逐年经营成本（附表成本/现金流经营成本行）：属输入侧序列，供引擎按年使用，
    # 禁止把达产峰值按营收比例回推（B-2 amount-bridge 主因之一）。
    opex_series_raw = (reference_pack.get("cashflows") or {}).get("project_operating_cost") or []
    operating_cost_by_year: list[float] = []
    for item in opex_series_raw:
        if not isinstance(item, dict):
            continue
        value = _to_float(item.get("value"))
        if value is None:
            continue
        operating_cost_by_year.append(round(float(value), 6))
    result: dict[str, Any] = {
        "total_investment_wan": round(total, 6),
        "invest_breakdown": {
            "construction_wan": construction,
            "interest_wan": round(interest, 6),
            "working_capital_wan": round(working, 6),
        },
        "is_operating": bool(revenue_values),
        "annual_revenue_wan": round(annual_revenue, 6) if annual_revenue is not None else None,
        "loan_rate": _extract_loan_rate(reference_pack),
        "loan_years": max(loan_years, 1),
        "calc_period_years": max(_build_period_years(reference_pack), 1),
        "build_period_months": context["build_period_months"],
        "cost_items": _cost_items(reference_pack),
        "input_sources": input_sources,
    }
    if operating_cost_by_year and any(v > 0 for v in operating_cost_by_year):
        result["operating_cost_by_year"] = operating_cost_by_year
        result["input_sources"]["operating_cost_by_year"] = {
            "note": "从甲方表经营成本逐年序列抽取（输入参数）；引擎按年使用，不抄净现金流输出",
            "source_ref": source_ref,
            "evidence_level": "C",
        }
    # S2 T-debt: 还本/付息序列作为输入（来自还款付息表行，非输出指标抄写）
    if _principal_vals and any(v > 0 for v in _principal_vals):
        if working > initial_working and short_working_loan > 0:
            _principal_vals[-1] = round(
                _principal_vals[-1] + short_working_loan,
                6,
            )
        result["loan_principal_by_year"] = _principal_vals
        result["loan_repay_method"] = "principal_schedule"
        result["input_sources"]["loan_principal_by_year"] = {
            "note": "从还款付息表「还本」行抽取；引擎按序列还本并重算余额/利息备付",
            "source_ref": source_ref,
            "evidence_level": "C",
        }
    debt_sheet = _find_mapped_sheet(reference_pack, "还款付息测算表")
    if debt_sheet is None:
        for _n, _sh in (reference_pack.get("sheets") or {}).items():
            if "还本付息" in str(_n) or "还款付息" in str(_n):
                debt_sheet = _sh
                break
    if debt_sheet is not None:
        _interest_pay = _series_by_label(debt_sheet, ["付息"], exclude=["还本付息"])
        if not _interest_pay:
            _interest_pay = _series_by_label(debt_sheet, ["其中:付息", "支付利息"])
        _int_vals = [
            round(float(item.get("value") or 0.0), 6)
            for item in _interest_pay
            if isinstance(item, dict)
        ]
        if (
            _int_vals
            and any(v > 0 for v in _int_vals)
            and not (working > initial_working and short_working_loan > 0)
        ):
            result["loan_interest_by_year"] = _int_vals
            result["input_sources"]["loan_interest_by_year"] = {
                "note": "从还款付息表「付息」行抽取（输入）；缺省时引擎按余额×利率重算",
                "source_ref": source_ref,
                "evidence_level": "C",
            }
        # improve principal extract with 其中:还本 if debt_principal empty
        if not result.get("loan_principal_by_year"):
            _prin2 = _series_by_label(debt_sheet, ["其中:还本", "还本"], exclude=["还本付息", "当期还本付息"])
            _pv = [round(float(x.get("value") or 0.0), 6) for x in _prin2 if isinstance(x, dict)]
            if _pv and any(v > 0 for v in _pv):
                result["loan_principal_by_year"] = _pv
                result["loan_repay_method"] = "principal_schedule"
                result["loan_years"] = max(sum(1 for v in _pv if v > 0), 1)
    # B-2: construction / loan draw schedules from 资金筹措表 (input phasing)
    for key, series in _funding_year_schedules(reference_pack).items():
        result[key] = series
        result["input_sources"][key] = {
            "note": "从投资使用计划与资金筹措表抽取的分年投入/提款（输入参数，非输出指标）",
            "source_ref": source_ref,
            "evidence_level": "C",
        }
    if full_wc_profile.get("increase_by_year"):
        result["working_capital_by_year"] = list(
            full_wc_profile["increase_by_year"]
        )
        result["input_sources"]["working_capital_by_year"] = {
            "note": "从流动资金估算表的全额流动资金当期增加额抽取；不使用铺底资金替代全额流资",
            "source_ref": source_ref,
            "evidence_level": "C",
        }
    # B-2 terminal recovery inputs from 项目投资现金流量表 (row present ⇒ authoritative)
    project_cf = _find_mapped_sheet(reference_pack, "项目投资现金流量表")
    if project_cf:
        fa_rec = _series_values(
            _series_by_label(project_cf, ["回收固定资产余值"])
        )
        wc_rec = _series_values(
            _series_by_label(
                project_cf, ["回收", "流动资金"], exclude=["固定资产"]
            )
        )
        # Row exists with no positives → explicit 0 (common vendor simplification)
        fa_row, _ = _find_row(project_cf, ["回收固定资产余值"])
        wc_row, _ = _find_row(project_cf, ["回收", "流动资金"], exclude=["固定资产"])
        if fa_row:
            peak_fa = max(fa_rec) if fa_rec else 0.0
            result["terminal_fixed_asset_recover_wan"] = round(float(peak_fa or 0.0), 6)
            result["input_sources"]["terminal_fixed_asset_recover_wan"] = {
                "note": "从项目投资现金流量表「回收固定资产余值」抽取；无正值则按 0 回收",
                "source_ref": source_ref,
                "evidence_level": "C",
            }
        if wc_row:
            peak_wc = max(wc_rec) if wc_rec else 0.0
            result["terminal_working_capital_recover_wan"] = round(
                float(working if working > initial_working else peak_wc or 0.0),
                6,
            )
            result["input_sources"]["terminal_working_capital_recover_wan"] = {
                "note": "从项目投资现金流量表「回收流动资金」抽取；无正值则按 0 回收",
                "source_ref": source_ref,
                "evidence_level": "C",
            }
    depreciation_classes = _extract_depreciation_classes(reference_pack)
    depreciation_years = _extract_depreciation_years(reference_pack)
    if depreciation_classes:
        result["depreciation_classes"] = depreciation_classes
        result["input_sources"]["depreciation_classes"] = {
            "note": "从固定资产折旧费估算表逐类抽取原值、折旧年限、残值率和来源单元格",
            "source_ref": source_ref,
            "evidence_level": "C",
        }
    elif depreciation_years is not None:
        result["depreciation_years"] = depreciation_years
        result["input_sources"]["depreciation_years"] = {
            "note": "从固定资产折旧费估算表的折旧/使用年限字段提取",
            "source_ref": source_ref,
            "evidence_level": "C",
        }
    else:
        result.setdefault("_missing_inputs", []).append("depreciation_years")
    if capital is not None:
        result["capital_own_wan"] = round(capital, 6)
    if loan is not None:
        result["loan_wan"] = round(loan, 6)
    if result.get("loan_rate") is None:
        result.pop("loan_rate", None)
    if result.get("annual_revenue_wan") is None:
        result.pop("annual_revenue_wan", None)
    wc_turn = _wc_turnover_from_sheet(reference_pack)
    if wc_turn:
        result["wc_turnover"] = wc_turn
        result["input_sources"]["wc_turnover"] = {
            "note": "从流动资金估算表最低周转天数抽取（含存货分项，输入参数）",
            "source_ref": source_ref,
            "evidence_level": "C",
        }
    construction_items = _construction_items_from_investment_sheet(reference_pack)
    investment_segments = _investment_segment_totals(reference_pack)
    if construction_items:
        bd = dict(result.get("invest_breakdown") or {})
        if investment_segments.get("other_wan") is not None:
            bd["other_wan"] = investment_segments["other_wan"]
        if investment_segments.get("reserve_wan") is not None:
            bd["reserve_wan"] = investment_segments["reserve_wan"]
        bd["construction_items"] = construction_items
        detail = _construction_detail_from_items(construction_items)
        detail_sum = sum(float(detail.get(k) or 0.0) for k in (
            "civil_wan", "equipment_wan", "installation_wan",
        ))
        construction_total = float((bd.get("construction_wan") or construction or 0.0))
        engineering_target = float(
            investment_segments.get("engineering_wan")
            or max(
                construction_total
                - float(bd.get("other_wan") or 0.0)
                - float(bd.get("reserve_wan") or 0.0),
                0.0,
            )
        )
        # Only reconcile against the evidenced engineering subtotal.  Other
        # costs and contingency remain separate segments and are never folded
        # into civil works.
        if engineering_target > 0 and detail_sum > 0:
            residual = round(engineering_target - detail_sum, 6)
            if residual > 0:
                detail["civil_wan"] = round(float(detail.get("civil_wan") or 0.0) + residual, 6)
                detail_sum = engineering_target
            if detail_sum + 1.0 >= engineering_target * 0.95:
                if detail_sum > engineering_target + 1.0:
                    scale = engineering_target / detail_sum
                    for k in ("civil_wan", "equipment_wan", "installation_wan"):
                        detail[k] = round(float(detail.get(k) or 0.0) * scale, 6)
                bd["construction_detail"] = detail
                result["construction_detail"] = detail
        result["invest_breakdown"] = bd
        result["input_sources"]["construction_items"] = {
            "note": "从固定资产投资估算表抽取工程量×估算指标明细（输入参数）",
            "source_ref": source_ref,
            "evidence_level": "C",
            "detail_sum_wan": round(sum(float(detail.get(k) or 0.0) for k in ("civil_wan","equipment_wan","installation_wan")), 6),
            "construction_wan": construction_total,
            "engineering_target_wan": engineering_target,
            "detail_attached": "construction_detail" in bd,
        }
    staff_detail = _staff_detail_from_wage_sheet(reference_pack)
    if staff_detail:
        result["staff_detail"] = staff_detail
        result["input_sources"]["staff_detail"] = {
            "note": "从工资及福利费估算表抽取劳动定员×人均年工资",
            "source_ref": source_ref,
            "evidence_level": "C",
        }
    wage = next(
        (value for key, value in (result.get("cost_items") or {}).items() if "工资" in key),
        None,
    )
    if wage is not None:
        result["wage_wan"] = wage
    return result


def _product_lines_from_revenue_sheet(reference_pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract named price/quantity/ramp lines from a real product revenue table."""

    sheet = _find_mapped_sheet(
        reference_pack, "营业收入、税金及附加和增值税估算表"
    )
    if not sheet:
        return []
    rows = _rows(sheet)
    products: list[dict[str, Any]] = []
    for row_index, row in sorted(rows.items()):
        price_row = rows.get(row_index + 1) or {}
        quantity_row = rows.get(row_index + 2) or {}
        if "单价" not in _norm(_row_label(price_row)) or "数量" not in _norm(_row_label(quantity_row)):
            continue
        name = next(
            (str(raw).strip() for col, (_cell, raw) in sorted(row.items()) if col == 2 and str(raw).strip()),
            "",
        )
        if not name:
            continue
        price_values = [
            float(value) for col, (_cell, raw) in sorted(price_row.items())
            if col >= 5 and (value := _to_float(raw)) is not None and value > 0
        ]
        quantities = [
            float(value) for col, (_cell, raw) in sorted(quantity_row.items())
            if col >= 5 and (value := _to_float(raw)) is not None and value >= 0
        ]
        if not price_values or not quantities or max(quantities) <= 0:
            continue
        capacity = max(quantities)
        unit = next(
            (str(raw).strip() for col, (_cell, raw) in sorted(quantity_row.items()) if col == 3 and str(raw).strip()),
            "项",
        )
        products.append({
            "name": name,
            "unit": unit,
            "price_per_unit": round(max(price_values), 6),
            "price_unit": "yuan",
            "capacity": round(capacity, 6),
            "ramp": [round(value / capacity, 8) for value in quantities],
            "var_cost_rate": 0.0,
        })
    return products


def build_vendor_finance_spec(
    reference_pack: dict[str, Any],
    finance_input: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Select the required revenue model while keeping vendor evidence at grade C."""
    inputs = finance_input or build_finance_input_from_vendor(reference_pack)
    context = infer_vendor_project_context(reference_pack)
    revenue_series = [
        float(item["value"])
        for item in (reference_pack.get("cashflows") or {}).get("project_revenue") or []
        if float(item.get("value") or 0.0) > 0
    ]
    peak = max(revenue_series) if revenue_series else float(inputs.get("annual_revenue_wan") or 0.0)
    model = context["revenue_model"]
    if model == "product_sales" and peak > 0:
        product_lines = _product_lines_from_revenue_sheet(reference_pack)
        ramp = [round(value / peak, 8) for value in revenue_series]
        revenue = {
            "model": "product_sales",
            "annual_revenue_wan": peak,
            "products": product_lines or [{
                "name": "甲方表综合产品组合",
                "unit": "项",
                "price_per_unit": round(peak * 10000.0, 6),
                "price_unit": "yuan",
                "capacity": 1.0,
                "ramp": ramp,
                "var_cost_rate": 0.0,
            }],
        }
    elif model == "property_sales" and revenue_series:
        total_revenue = sum(revenue_series)
        revenue_sheet = _find_mapped_sheet(
            reference_pack, "营业收入、税金及附加和增值税估算表"
        )
        area = 0.0
        if revenue_sheet:
            for row_index, row in _rows(revenue_sheet).items():
                if "销售面积" not in _norm(_row_label(row)):
                    continue
                value = _total_for_row(revenue_sheet, row_index)
                if value and value > 0:
                    area += value
        if area <= 0:
            area = 1.0
        revenue = {
            "model": "property_sales",
            "annual_revenue_wan": peak,
            "saleable_area": round(area, 6),
            "price_per_sqm": round(total_revenue * 10000.0 / area, 6),
            "absorption": [round(value / total_revenue, 8) for value in revenue_series],
        }
    elif model in {"lease_portfolio", "inventory_sales"} and revenue_series:
        revenue = {
            "model": model,
            "annual_revenue_wan": peak,
            "annual_schedule_wan": [round(value, 6) for value in revenue_series],
            "inventory_total": round(sum(revenue_series), 6) if model == "inventory_sales" else 0.0,
            "sales_schedule": (
                [round(value / sum(revenue_series), 8) for value in revenue_series]
                if model == "inventory_sales" and sum(revenue_series) > 0 else []
            ),
        }
    else:
        revenue = {"model": "flat", "annual_revenue_wan": peak}
    return {
        "version": "finance_spec.v2",
        "confirmation_status": "candidate",
        "industry": context["industry"],
        "invest_type": context["invest_type"],
        "source_hint": "vendor_reference_C",
        "revenue": revenue,
        "cost": {},
        "tax": {},
        "assumptions": [
            "甲方工作簿仅作C级输入与公式参考；所有指标由本系统确定性引擎重算",
        ],
    }
