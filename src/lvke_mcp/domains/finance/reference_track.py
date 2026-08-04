"""Read-only replay and corrected-track amount bridge for vendor workbooks."""

from __future__ import annotations

from typing import Any

from lvke_mcp.servers.finance_calc.calculations import irr, npv

IRR_TOLERANCE_PERCENTAGE_POINTS = 0.01
AMOUNT_TOLERANCE_WAN = 0.01


def _sheet_name(reference_pack: dict[str, Any], business: str) -> str:
    for name, sheet in (reference_pack.get("sheets") or {}).items():
        if sheet.get("business") == business:
            return str(name)
    return ""


def payback_from_period_rows(rows: list[dict[str, Any]], *, rate: float = 0.0) -> dict[str, float | None]:
    """Interpolate payback using the vendor's explicit 1-based period labels."""

    cumulative = 0.0
    discounted = 0.0
    previous_period = 0.0
    static_result: float | None = None
    dynamic_result: float | None = None
    for index, row in enumerate(rows):
        period = float(row.get("period") or index + 1)
        value = float(row.get("value") or 0.0)
        before = cumulative
        cumulative += value
        if static_result is None and cumulative >= 0 and before < 0 and value > 0:
            static_result = previous_period + (-before / value) * (period - previous_period)
        discounted_value = value / ((1.0 + rate) ** period)
        discounted_before = discounted
        discounted += discounted_value
        if dynamic_result is None and discounted >= 0 and discounted_before < 0 and discounted_value > 0:
            dynamic_result = previous_period + (-discounted_before / discounted_value) * (period - previous_period)
        previous_period = period
    return {"static_years": static_result, "dynamic_years": dynamic_result}


def replay_reference_track(
    reference_pack: dict[str, Any], *, irr_tolerance_pp: float = IRR_TOLERANCE_PERCENTAGE_POINTS,
) -> dict[str, Any]:
    """Recalculate vendor cashflows without changing any vendor value."""

    indicators = reference_pack.get("vendor_indicators") or {}
    trials = indicators.get("trial_rates") or {}
    benchmark_pct = indicators.get("benchmark_rate_pct")
    benchmark = float(benchmark_pct or 0.0) / 100.0
    cashflow_sheet = _sheet_name(reference_pack, "项目投资现金流量表")
    capital_sheet = _sheet_name(reference_pack, "项目资本金流量表")
    rows: dict[str, dict[str, Any]] = {}
    for key, label, sheet in (
        ("project_pre_tax", "项目税前", cashflow_sheet),
        ("project_after_tax", "项目税后", cashflow_sheet),
        ("capital_after_tax", "资本金税后", capital_sheet),
    ):
        source = list((reference_pack.get("cashflows") or {}).get(key) or [])
        values = [float(item.get("value") or 0.0) for item in source]
        stated = (trials.get(key) or {}).get("rate_pct")
        solved = None
        solve_error = ""
        try:
            solved = float(irr([0.0, *values]) * 100.0)
        except Exception as exc:  # noqa: BLE001
            solve_error = f"{type(exc).__name__}: {exc}"
        delta = solved - float(stated) if solved is not None and stated is not None else None
        locators = [
            {"period": int(item.get("period") or index + 1), "cell": item.get("cell"),
             "locator": f"{sheet}!{item.get('cell')}", "value": float(item.get("value") or 0.0)}
            for index, item in enumerate(source)
        ]
        rows[key] = {
            "label": label, "cashflows_wan": values, "source_locators": locators,
            "stated_irr_pct": stated, "solved_irr_pct": solved,
            "irr_delta_percentage_points": delta,
            "irr_within_tolerance": delta is not None and abs(delta) <= irr_tolerance_pp,
            "solve_error": solve_error,
        }
    project = rows["project_after_tax"]
    project_values = [0.0, *project["cashflows_wan"]]
    if project["cashflows_wan"]:
        project["npv_wan"] = npv(project_values, benchmark)
        # Use explicit vendor period labels.  A synthetic t=0 zero incorrectly
        # returns zero; treating the first row as t=0 undercounts by one year.
        payback = payback_from_period_rows(
            list((reference_pack.get("cashflows") or {}).get("project_after_tax") or []),
            rate=benchmark,
        )
        project["static_payback_years"] = payback["static_years"]
        project["dynamic_payback_years"] = payback["dynamic_years"]
    required = [rows["project_after_tax"], rows["project_pre_tax"], rows["capital_after_tax"]]
    declared = [row for row in required if row["stated_irr_pct"] is not None]
    verifiable = [
        row for row in declared
        if row["cashflows_wan"] and row["solved_irr_pct"] is not None and not row["solve_error"]
    ]
    # ``all([])`` used to mark a workbook with no declared reference IRR as
    # passed.  Formal reference approval requires at least one independently
    # solvable, explicitly stated track; every stated track must be verifiable
    # and within the fixed tolerance.
    passed = bool(declared) and len(verifiable) == len(declared) and all(
        row["irr_within_tolerance"] for row in declared
    )
    return {
        "version": "vendor_reference_replay.v1", "read_only": True,
        "workbook_sha256": (reference_pack.get("source") or {}).get("workbook_sha256"),
        "benchmark_rate_pct": benchmark_pct, "irr_tolerance_percentage_points": irr_tolerance_pp,
        "tracks": rows,
        "declared_track_count": len(declared),
        "verifiable_track_count": len(verifiable),
        "passed": passed,
    }


def build_corrected_track_bridge(
    engine_run: dict[str, Any], reference_pack: dict[str, Any], *, amount_tolerance_wan: float = AMOUNT_TOLERANCE_WAN,
) -> dict[str, Any]:
    """Build a period-by-period immutable amount bridge requiring adjudication."""

    reference_rows = list((reference_pack.get("cashflows") or {}).get("project_after_tax") or [])
    engine_rows = list(((engine_run.get("annual") or {}).get("project_cashflow") or []))
    engine_values = []
    for row in engine_rows:
        value = row.get("net_cashflow")
        if value is None:
            value = row.get("after_tax_net_cashflow")
        engine_values.append(float(value or 0.0))
    reference_values = [float(row.get("value") or 0.0) for row in reference_rows]
    length = max(len(reference_values), len(engine_values))
    sheet = _sheet_name(reference_pack, "项目投资现金流量表")
    bridge = []
    for index in range(length):
        reference_value = reference_values[index] if index < len(reference_values) else 0.0
        engine_value = engine_values[index] if index < len(engine_values) else 0.0
        delta = engine_value - reference_value
        source = reference_rows[index] if index < len(reference_rows) else {}
        bridge.append({
            "period": index + 1,
            "reference_value_wan": reference_value,
            "corrected_value_wan": engine_value,
            "delta_wan": delta,
            "source_locator": f"{sheet}!{source.get('cell')}" if source.get("cell") else "",
            "formula": "corrected_track - vendor_reference_track",
            "within_tolerance": abs(delta) <= amount_tolerance_wan,
            "decision_status": "not_required" if abs(delta) <= amount_tolerance_wan else "pending",
            "evidence_status": "linked" if source.get("cell") else "missing",
        })
    blocking = [row for row in bridge if row["decision_status"] == "pending"]
    return {
        "version": "corrected_track_bridge.v1",
        "amount_tolerance_wan": amount_tolerance_wan,
        "rows": bridge,
        "blocking_count": len(blocking),
        "approved": not blocking,
    }
