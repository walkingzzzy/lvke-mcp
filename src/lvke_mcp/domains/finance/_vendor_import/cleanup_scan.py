"""僵尸公式与手工常量扫描：常量算式频次、孤立常量公式与标签定位。"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

from .base import (
    IRR_RESIDUAL_TOL_WAN,
    _norm,
)

from .locate import (
    _rows,
)

from .sheet_read import (
    _find_mapped_sheet,
)


_CONST_ARITH = re.compile(
    r"^\s*[+-]?\d+(?:\.\d+)?\s*[+\-*/]\s*[+-]?\d+(?:\.\d+)?\s*$"
)


def _constant_formula_frequency(packs: Iterable[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for pack in packs:
        signatures = set()
        for formulas in (pack.get("formulas") or {}).values():
            for item in (formulas or {}).values():
                formula = re.sub(r"\s+", "", str(item.get("formula") or "").lstrip("="))
                if _CONST_ARITH.fullmatch(formula):
                    signatures.add(formula)
        counter.update(signatures)
    return counter


def _orphan_constant_formulas(reference_pack: dict[str, Any]) -> list[dict[str, Any]]:
    dependents: dict[str, set[str]] = defaultdict(set)
    for sheet_name, formulas in (reference_pack.get("formulas") or {}).items():
        for cell, item in (formulas or {}).items():
            dependent_id = f"{sheet_name}!{cell}"
            for reference in item.get("references") or []:
                target = str(reference).replace("$", "").split(":", 1)[0]
                if "!" not in target:
                    target = f"{sheet_name}!{target}"
                dependents[target].add(dependent_id)
    result = []
    for sheet_name, formulas in (reference_pack.get("formulas") or {}).items():
        for cell, item in (formulas or {}).items():
            formula = re.sub(r"\s+", "", str(item.get("formula") or "").lstrip("="))
            if not _CONST_ARITH.fullmatch(formula):
                continue
            locator = f"{sheet_name}!{cell}"
            direct = dependents.get(locator) or set()
            # A copied scratch block often has one terminal dependent (for
            # example J16 constant subtraction -> K16 ratio).  Treat the whole
            # disconnected two-cell island as orphan, but retain parameters
            # such as 3/100 that feed a real formula chain or multiple sheets.
            if len(direct) > 1 or any(dependents.get(item_id) for item_id in direct):
                continue
            result.append({"locator": locator, "formula": formula, "cached_value": item.get("cached_value")})
    return result


def _label_locators(sheet: Optional[dict[str, Any]], keyword: str) -> list[str]:
    if not sheet:
        return []
    result = []
    for row in _rows(sheet).values():
        for col, (cell, value) in row.items():
            if col <= 4 and isinstance(value, str) and _norm(keyword) in _norm(value):
                result.append(cell)
    return result


def detect_cleanup_issues(
    reference_pack: dict[str, Any],
    *,
    cohort_reference_packs: Optional[list[dict[str, Any]]] = None,
    irr_residual_tolerance_wan: float = IRR_RESIDUAL_TOL_WAN,
) -> list[dict[str, Any]]:
    """Detect F1/F2/F3 without modifying any vendor value."""
    findings: list[dict[str, Any]] = []
    project_cf = _find_mapped_sheet(reference_pack, "项目投资现金流量表")
    investment_labels = []
    financing_labels = []
    for keyword in ("项目资本金", "固定资产投资", "建设投资"):
        investment_labels.extend(_label_locators(project_cf, keyword))
    for keyword in ("借款本金偿还", "借款利息支付"):
        financing_labels.extend(_label_locators(project_cf, keyword))
    if investment_labels and financing_labels:
        sheet_name = str((project_cf or {}).get("mapping", {}).get("vendor_sheet_actual") or "")
        findings.append({
            "code": "F1",
            "type": "project_cashflow_financing_duplication",
            "severity": "high",
            "blocking": False,
            "locator": ", ".join(f"{sheet_name}!{cell}" for cell in financing_labels),
            "vendor_value": "项目投资现金流同时列示投资支出与还本付息",
            "engine_suggestion": "全投资口径移除借款本金偿还和利息支付；融资项仅进入资本金现金流",
            "detail": "附表9混入融资现金流，可能造成投资本金重复计列并压低项目IRR",
        })

    trials = (reference_pack.get("vendor_indicators") or {}).get("trial_rates") or {}
    cashflows = reference_pack.get("cashflows") or {}
    try:
        from lvke_mcp.domains.finance.calculations import irr, npv
    except Exception:  # noqa: BLE001
        irr = npv = None  # type: ignore
    for key, label in (
        ("project_pre_tax", "项目税前IRR"),
        ("project_after_tax", "项目税后IRR"),
        ("capital_after_tax", "资本金税后IRR"),
    ):
        trial = trials.get(key) or {}
        series = cashflows.get(key) or []
        if not trial or not series or not trial.get("hardcoded"):
            continue
        values = [float(item["value"]) for item in series]
        rate = float(trial.get("rate") or 0.0)
        residual = None
        solved_pct = None
        solve_error = ""
        if npv is not None:
            try:
                residual = float(npv([0.0, *values], rate))
            except Exception as exc:  # noqa: BLE001
                solve_error = f"NPV复算失败：{type(exc).__name__}: {exc}"
        if irr is not None:
            try:
                solved_pct = float(irr([0.0, *values], guess=rate) * 100.0)
            except Exception as exc:  # noqa: BLE001
                solve_error = f"IRR求解失败：{type(exc).__name__}: {exc}"
        residual_exceeded = residual is None or abs(residual) > float(irr_residual_tolerance_wan)
        negative_irr = (
            float(trial.get("rate_pct") or 0.0) < 0
            or (solved_pct is not None and solved_pct < 0)
        )
        findings.append({
            "code": "F2",
            "type": "hardcoded_irr_trial",
            "severity": "high" if residual_exceeded else "medium",
            "blocking": negative_irr,
            "locator": f"{_find_mapped_sheet(reference_pack, '投资估算复核表').get('mapping', {}).get('vendor_sheet_actual', '投资复核')}!{trial.get('cell')}",
            "vendor_value": float(trial.get("rate_pct") or 0.0),
            "npv_residual_wan": residual,
            "tolerance_wan": float(irr_residual_tolerance_wan),
            "engine_suggestion": solved_pct,
            "negative_irr": negative_irr,
            "detail": (
                f"{label}为硬编码试算值，代回NPV残差="
                f"{residual if residual is not None else '不可得'}万元；应使用确定性IRR求解"
                + (f"；{solve_error}" if solve_error else "")
            ),
        })

    cohort = [reference_pack, *(cohort_reference_packs or [])]
    frequency = _constant_formula_frequency(cohort)
    for item in _orphan_constant_formulas(reference_pack):
        count = int(frequency.get(item["formula"], 1))
        findings.append({
            "code": "F3",
            "type": "orphan_constant_formula",
            "severity": "high" if count >= 2 else "medium",
            "blocking": False,
            "locator": item["locator"],
            "vendor_value": f"={item['formula']}",
            "cached_value": item.get("cached_value"),
            "cohort_occurrences": count,
            "engine_suggestion": "删除无下游引用的常量试算式；如属有效参数，应改为有来源的独立输入",
            "detail": (
                "发现纯常量运算且无下游引用的孤儿公式"
                + (f"，在{count}套参考表重复出现" if count >= 2 else "")
            ),
        })
    return findings
