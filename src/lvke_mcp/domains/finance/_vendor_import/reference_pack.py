"""参考数据提取与只读参考档构造。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

from .base import (
    MAX_COLS,
    MAX_ROWS,
    REFERENCE_PACK_VERSION,
    VendorImportError,
    _norm,
    _to_float,
)

from .extract_summary import (
    _extract_indicator_summary,
    _extract_sensitivity_summary,
    _review_trial_rates,
)

from .locate import (
    _find_row,
    _first_numeric_on_row,
    _row_label,
    _rows,
    _series_by_label,
    _total_by_label,
)

from .sheet_read import (
    _find_mapped_sheet,
    _infer_business,
    _read_value_sheets,
    _sheet_is_nonempty,
)


def _extract_reference_data(reference_pack: dict[str, Any]) -> None:
    indicator_summary = _extract_indicator_summary(reference_pack)
    reference_pack["indicator_summary"] = indicator_summary
    reference_pack["sensitivity_summary"] = _extract_sensitivity_summary(
        reference_pack, indicator_summary
    )
    funding = _find_mapped_sheet(reference_pack, "投资使用计划与资金筹措表")
    project_cf = _find_mapped_sheet(reference_pack, "项目投资现金流量表")
    capital_cf = _find_mapped_sheet(reference_pack, "项目资本金流量表")
    debt = _find_mapped_sheet(reference_pack, "还款付息测算表")
    review = _find_mapped_sheet(reference_pack, "投资估算复核表")

    project_pre = _series_by_label(
        project_cf, ["所得税前净现金流量"], exclude=["累计"]
    )
    project_after = _series_by_label(
        project_cf, ["所得税后净现金流量"], exclude=["累计"]
    )
    capital_after = _series_by_label(
        capital_cf, ["所得税后净现金流量"], exclude=["累计"]
    )
    project_revenue = _series_by_label(project_cf, ["营业收入"])
    project_cost = _series_by_label(project_cf, ["经营成本"])
    icr = _series_by_label(debt, ["利息备付率"])
    dscr = _series_by_label(debt, ["偿债备付率"])
    principal = _series_by_label(debt, ["其中:还本", "还本"], exclude=["还本付息", "当期还本付息"])

    cashflows = {
        "project_pre_tax": project_pre,
        "project_after_tax": project_after,
        "capital_after_tax": capital_after,
        "project_revenue": project_revenue,
        "project_operating_cost": project_cost,
        "debt_icr": icr,
        "debt_dscr": dscr,
        "debt_principal": principal,
    }
    reference_pack["cashflows"] = cashflows

    total = _total_by_label(funding, ["总投资"], exclude=["使用计划", "资金筹措表"])
    interest = _total_by_label(funding, ["建设期", "利息"])
    working = _total_by_label(funding, ["流动资金"])
    capital = _total_by_label(funding, ["项目资本金"])
    debt_funding_total = _total_by_label(funding, ["债务资金"])
    long_term_loan = _total_by_label(funding, ["银行长期借款"])
    short_term_loan_schedule = _series_by_label(funding, ["银行短期借款"])
    short_term_loan = next(
        (float(item["value"]) for item in short_term_loan_schedule if float(item.get("value") or 0.0) > 0),
        None,
    )
    loan = debt_funding_total if debt_funding_total is not None else long_term_loan
    loan_selection = "debt_funding_total"
    # Some vendor sheets put revolving working-capital borrowing below the
    # project-funding total.  In that layout the row labelled “债务资金” can be
    # larger than total investment minus equity, while the explicitly labelled
    # long-term bank loan reconciles exactly.  Keep every source value, but use
    # the evidenced long-term principal for the deterministic project model.
    if (
        total is not None and capital is not None and long_term_loan is not None
        and abs((float(capital) + float(long_term_loan)) - float(total))
        <= max(abs(float(total)) * 1e-6, 0.01)
        and (
            debt_funding_total is None
            or abs((float(capital) + float(debt_funding_total)) - float(total))
            > max(abs(float(total)) * 1e-6, 0.01)
        )
    ):
        loan = long_term_loan
        loan_selection = "long_term_loan_reconciled_to_total"

    trials = _review_trial_rates(review)
    benchmark = None
    if review:
        row_index, _ = _find_row(review, ["根据行业确定", "收益率"])
        benchmark = _first_numeric_on_row(review, row_index)
    vendor_indicators: dict[str, Any] = {
        "total_investment": total,
        "interest_during_construction": interest or 0.0,
        "working_capital": working or 0.0,
        "equity_capital": capital,
        "loan": loan,
        "debt_funding_total": debt_funding_total,
        "long_term_loan": long_term_loan,
        "short_term_working_capital_borrowing": short_term_loan,
        "short_term_working_capital_borrowing_schedule": short_term_loan_schedule,
        "loan_selection": loan_selection,
        "benchmark_rate_pct": benchmark * 100.0 if benchmark is not None and abs(benchmark) <= 1 else benchmark,
        "trial_rates": trials,
    }
    summary_values = indicator_summary.get("normalized") or {}

    def summary_number(key: str) -> Optional[float]:
        return _to_float((summary_values.get(key) or {}).get("value"))

    # A summary sheet is a reference/reconciliation source, never model truth.
    # It may fill a missing vendor-reference field but cannot silently overwrite
    # a value already extracted from the underlying financing/cash-flow sheets.
    for key in (
        "total_investment", "construction_investment", "working_capital",
        "equity_capital", "loan", "project_irr_pre_tax_pct",
        "project_irr_pct", "capital_irr_pct", "npv_wan",
        "static_payback_years", "build_period_years", "revenue",
    ):
        value = summary_number(key)
        if value is not None and vendor_indicators.get(key) is None:
            vendor_indicators[key] = value
    if total is not None:
        vendor_indicators["construction_investment"] = round(
            total - float(interest or 0.0) - float(working or 0.0), 6
        )
    if trials.get("project_pre_tax"):
        vendor_indicators["project_irr_pre_tax_pct"] = trials["project_pre_tax"]["rate_pct"]
    if trials.get("project_after_tax"):
        vendor_indicators["project_irr_pct"] = trials["project_after_tax"]["rate_pct"]
    if trials.get("capital_after_tax"):
        vendor_indicators["capital_irr_pct"] = trials["capital_after_tax"]["rate_pct"]

    if project_after and benchmark is not None:
        try:
            from lvke_mcp.domains.finance.calculations import npv
            from lvke_mcp.domains.finance.reference_track import payback_from_period_rows

            values = [float(item["value"]) for item in project_after]
            vendor_indicators["npv_wan"] = npv([0.0, *values], float(benchmark))
            payback = payback_from_period_rows(project_after, rate=float(benchmark))
            vendor_indicators["static_payback_years"] = payback["static_years"]
            vendor_indicators["dynamic_payback_years"] = payback["dynamic_years"]
        except Exception:  # noqa: BLE001
            pass

    comparable_keys = (
        "total_investment", "construction_investment", "working_capital",
        "interest_during_construction", "equity_capital", "loan",
        "project_irr_pct", "capital_irr_pct", "npv_wan", "static_payback_years",
    )
    reference_pack["vendor_indicators"] = vendor_indicators
    reference_pack["indicators"] = {
        key: vendor_indicators[key]
        for key in comparable_keys
        if vendor_indicators.get(key) is not None
    }
    reconciliations = []
    for key in (
        "total_investment", "construction_investment", "working_capital",
        "equity_capital", "loan", "project_irr_pct", "capital_irr_pct",
        "npv_wan", "static_payback_years",
    ):
        summary_item = summary_values.get(key) or {}
        summary_value = _to_float(summary_item.get("value"))
        extracted_value = _to_float(vendor_indicators.get(key))
        if summary_value is None or extracted_value is None:
            continue
        tolerance = 0.01 if "irr" in key else max(abs(summary_value) * 1e-6, 0.01)
        delta = abs(summary_value - extracted_value)
        reconciliations.append({
            "field": key,
            "summary_value": summary_value,
            "summary_locator": summary_item.get("locator") or "",
            "underlying_value": extracted_value,
            "delta": delta,
            "tolerance": tolerance,
            "within_tolerance": delta <= tolerance,
        })
    reference_pack["summary_reconciliation"] = reconciliations
    mismatched_summary = [row for row in reconciliations if not row["within_tolerance"]]
    if mismatched_summary:
        reference_pack.setdefault("warnings", []).append(
            f"主要指标汇总表存在 {len(mismatched_summary)} 项反向勾稽差异"
        )

    periods: dict[str, float] = {}
    for index, item in enumerate(project_after):
        periods[f"project_cashflow.net_cashflow.Y{index}"] = float(item["value"])
    for index, item in enumerate(capital_after):
        periods[f"capital_cashflow.net_cashflow.Y{index}"] = float(item["value"])
    for index, item in enumerate(icr, 1):
        periods[f"debt_service.icr.Y{index}"] = float(item["value"])
    for index, item in enumerate(dscr, 1):
        periods[f"debt_service.dscr.Y{index}"] = float(item["value"])
    reference_pack["periods"] = periods


def build_reference_pack(xlsx_path: str | Path) -> dict[str, Any]:
    """Read one vendor xlsx into an immutable, JSON-serializable reference pack."""
    path = Path(xlsx_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise VendorImportError(f"不支持的甲方工作簿类型：{path.suffix}")

    try:
        from lvke_mcp.adapters.spreadsheets.formulas import (
            FormulaBackend,
            FormulaBackendUnavailable,
        )
        from lvke_mcp.domains.templates.catalog import map_vendor_sheet
    except Exception as exc:  # noqa: BLE001
        raise VendorImportError(f"缺少 Excel/附表桥接模块：{exc}") from exc

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    formula_status = "available"
    formula_warning = ""
    formula_sheets: dict[str, dict[str, Any]] = {}
    cross_sheet = {"cross_sheet_refs": {}, "matrix": {}, "total": 0}
    sheet_names: list[str] = []
    backend = None
    try:
        backend = FormulaBackend(str(path))
        sheet_names = backend.sheet_names()
        for name in sheet_names:
            raw = backend.read_formulas(name, max_rows=MAX_ROWS, max_cols=MAX_COLS)
            formula_sheets[name] = {
                item["cell"]: {
                    "formula": item.get("formula") or "",
                    "cached_value": item.get("cached_value"),
                    "references": list(item.get("references") or []),
                }
                for item in raw.get("cells") or []
            }
        cross_sheet = backend.cross_sheet_refs()
    except FormulaBackendUnavailable as exc:
        formula_status = "unavailable"
        formula_warning = str(exc)
        try:
            from lvke_mcp.adapters.spreadsheets.reader import pick_backend

            sheet_names = pick_backend().list_sheets(path)
        except Exception as read_exc:  # noqa: BLE001
            raise VendorImportError(str(read_exc)) from read_exc
    except Exception as exc:  # noqa: BLE001
        raise VendorImportError(f"公式解析失败：{type(exc).__name__}: {exc}") from exc
    finally:
        if backend is not None:
            backend.close()

    value_sheets, value_backend = _read_value_sheets(path, sheet_names)
    sheet_map: dict[str, Any] = {}
    sheets: dict[str, Any] = {}
    for name in sheet_names:
        sheet = dict(value_sheets.get(name) or {"values": {}, "max_row": 0, "max_col": 0})
        sheet["formulas"] = formula_sheets.get(name) or {}
        sheet["formula_count"] = len(sheet["formulas"])
        business = _infer_business(name, sheet)
        by_business = map_vendor_sheet(business=business) if business else None
        by_name = map_vendor_sheet(sheet_name=name.strip())
        conflicts = []
        if by_business and by_name and by_business.get("business") != by_name.get("business"):
            conflicts.append(
                "业务语义候选与裸工作表名登记冲突："
                f"{by_business.get('business')} != {by_name.get('business')}"
            )
        mapped = by_business or by_name
        mapping = dict(mapped or {})
        resolved_business = business or mapping.get("business") or ""
        formula_cross_refs = sum(
            1
            for item in (sheet.get("formulas") or {}).values()
            for reference in (item.get("references") or [])
            if "!" in str(reference)
        )
        key_rows = []
        for row in _rows(sheet).values():
            label = _row_label(row)
            normalized = _norm(label)
            if any(
                token in normalized
                for token in (
                    "总投资", "营业收入", "净现金流量", "内部收益率",
                    "还本付息", "不确定性因数", "指标名称",
                )
            ):
                key_rows.append(label)
            if len(key_rows) >= 8:
                break
        rule_hits = []
        if business:
            rule_hits.append("sheet_name+header_semantics")
        if by_business:
            rule_hits.append("canonical_business_dictionary")
        if by_name:
            rule_hits.append("registered_vendor_sheet_name")
        if key_rows:
            rule_hits.append("key_business_rows")
        if formula_cross_refs:
            rule_hits.append("cross_sheet_formula_references")
        non_empty = _sheet_is_nonempty(sheet)
        confidence = 0.0
        if mapped and resolved_business:
            confidence = 0.97 if by_business and by_name and not conflicts else 0.90 if by_business else 0.72
            if key_rows:
                confidence = min(0.99, confidence + 0.02)
            if formula_cross_refs:
                confidence = min(0.99, confidence + 0.01)
            if conflicts:
                confidence = min(confidence, 0.49)
        mapping.update({
            "vendor_sheet_actual": name,
            "business": resolved_business,
            "candidate_business": resolved_business,
            "mapped": bool(mapped and not conflicts),
            "mapping_rule": "vendor_business_semantics.v2" if mapped else "unmapped",
            "confidence": confidence,
            "rule_hits": rule_hits,
            "key_row_signals": key_rows,
            "cross_sheet_formula_reference_count": formula_cross_refs,
            "conflict_reasons": conflicts,
            "non_empty": non_empty,
            # Mechanical inference is only a candidate.  Every non-empty sheet
            # must receive an append-only human mapped/ignored decision before
            # the bound reference track can be approved.
            "decision_status": "pending" if non_empty else "not_required",
            "review_required": non_empty,
        })
        sheet["business"] = mapping["business"]
        sheet["mapping"] = mapping
        sheets[name] = sheet
        sheet_map[name] = mapping

    dependency_graph: dict[str, list[str]] = {}
    for name, sheet in sheets.items():
        for cell, item in (sheet.get("formulas") or {}).items():
            qualified = []
            for reference in item.get("references") or []:
                target = str(reference).replace("$", "")
                qualified.append(target if "!" in target else f"{name}!{target}")
            dependency_graph[f"{name}!{cell}"] = qualified

    reference_pack: dict[str, Any] = {
        "version": REFERENCE_PACK_VERSION,
        "source_type": "vendor_reference",
        "reliability_grade": "C",
        "read_only": True,
        "source": {
            "path": str(path),
            "workbook_name": path.name,
            "workbook_sha256": digest,
        },
        "formula_status": formula_status,
        "formula_warning": formula_warning,
        "value_backend": value_backend,
        "sheet_map": sheet_map,
        "sheets": sheets,
        "formulas": {name: sheet.get("formulas") or {} for name, sheet in sheets.items()},
        "dependency_graph": dependency_graph,
        "cross_sheet_refs": cross_sheet,
        "warnings": [formula_warning] if formula_warning else [],
    }
    unmapped = [name for name, mapping in sheet_map.items() if not mapping.get("mapped")]
    if unmapped:
        reference_pack["warnings"].append(f"未映射工作表：{', '.join(unmapped)}")
    pending_decisions = [
        name for name, mapping in sheet_map.items()
        if mapping.get("non_empty") and mapping.get("decision_status") == "pending"
    ]
    if pending_decisions:
        reference_pack["warnings"].append(
            f"{len(pending_decisions)} 张非空工作表待人工映射/忽略裁决"
        )
    _extract_reference_data(reference_pack)
    return reference_pack
