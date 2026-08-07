"""输入、年度表、指标与勾稽页写入。"""

from __future__ import annotations

from typing import Any



def _input_rows(fin: dict[str, Any]) -> list[tuple[str, Any, str]]:
    schema = fin.get("schema") or {}
    norm = schema.get("normalized_inputs") or {}
    inv = fin.get("investment") or {}
    fund = fin.get("funding") or {}
    params = fin.get("params") or {}
    rows = [
        ("total_investment_wan", inv.get("total") or norm.get("total_investment_wan"), "万元"),
        ("construction_wan", inv.get("construction") or norm.get("construction_wan"), "万元"),
        ("interest_wan", inv.get("interest") or norm.get("interest_wan"), "万元"),
        ("working_capital_wan", inv.get("working_capital") or norm.get("working_capital_wan"), "万元"),
        ("equity_capital_wan", fund.get("capital"), "万元"),
        ("loan_wan", fund.get("loan"), "万元"),
        ("loan_rate", (fin.get("raw") or {}).get("loan_rate") or norm.get("loan_rate"), "小数"),
        ("calc_years", params.get("calc_years"), "年"),
        ("build_years", params.get("build_years") or params.get("build_period_years"), "年"),
        ("finance_schema_version", fin.get("finance_schema_version") or (schema.get("finance_schema_version")), ""),
    ]
    return [(k, v, u) for k, v, u in rows if v is not None and v != ""]


def _write_inputs(ws, fin, Font, PatternFill, Alignment, Border, Side):
    fill = PatternFill("solid", fgColor="DCEAF7")
    header_font = Font(bold=True)
    thin = Border(
        left=Side(style="thin", color="B0B0B0"),
        right=Side(style="thin", color="B0B0B0"),
        top=Side(style="thin", color="B0B0B0"),
        bottom=Side(style="thin", color="B0B0B0"),
    )
    ws.append(["key", "value", "unit", "role"])
    for cell in ws[1]:
        cell.font = header_font
    for key, val, unit in _input_rows(fin):
        ws.append([key, val, unit, "input"])
        for cell in ws[ws.max_row]:
            cell.fill = fill
            cell.border = thin
            cell.alignment = Alignment(horizontal="left")
    # named-ish map for formulas: column B is value; row index by key
    key_rows = {}
    for r in range(2, ws.max_row + 1):
        key_rows[str(ws.cell(r, 1).value)] = r
    return key_rows


def _write_year_table(ws, rows: list[dict], fields: list[str], header_font):
    if not rows:
        ws.append(["(empty)"])
        return
    headers = ["year"] + fields
    ws.append(headers)
    for cell in ws[1]:
        cell.font = header_font
    for row in rows:
        year = row.get("year") if row.get("year") is not None else row.get("period")
        ws.append([year] + [row.get(f) for f in fields])


def _write_indicators(ws, fin, key_rows, Font):
    inv = fin.get("investment") or {}
    fund = fin.get("funding") or {}
    ind = fin.get("indicators") or {}
    header_font = Font(bold=True)
    ws.append(["metric", "value", "unit", "source"])
    for cell in ws[1]:
        cell.font = header_font

    def add(name, value, unit, source="engine"):
        ws.append([name, value, unit, source])

    # Prefer formulas referencing Inputs sheet when keys exist
    if "total_investment_wan" in key_rows:
        r = key_rows["total_investment_wan"]
        add("total_investment", f"=Inputs!B{r}", "万元", "formula→Inputs")
    else:
        add("total_investment", inv.get("total"), "万元")
    if "equity_capital_wan" in key_rows:
        r = key_rows["equity_capital_wan"]
        add("equity_capital", f"=Inputs!B{r}", "万元", "formula→Inputs")
    else:
        add("equity_capital", fund.get("capital"), "万元")
    if "loan_wan" in key_rows:
        r = key_rows["loan_wan"]
        add("loan", f"=Inputs!B{r}", "万元", "formula→Inputs")
    else:
        add("loan", fund.get("loan"), "万元")

    add("project_irr_pct", ind.get("project_irr_pct"), "%")
    add("npv_wan", ind.get("npv_wan"), "万元")
    add("static_payback_years", ind.get("static_payback_years"), "年")
    add("dynamic_payback_years", ind.get("dynamic_payback_years"), "年")
    add("bep_pct", ind.get("bep_pct"), "%")

    # funding sum formula if components present
    # locate rows just written for equity/loan if formula
    # Optional check row: capital + loan (values may be formulas)
    # Find equity/loan row numbers on this sheet
    equity_row = loan_row = None
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, 1).value == "equity_capital":
            equity_row = r
        if ws.cell(r, 1).value == "loan":
            loan_row = r
    if equity_row and loan_row:
        add("funding_sum", f"=B{equity_row}+B{loan_row}", "万元", "formula")


def _write_checks(ws, fin, Font):
    header_font = Font(bold=True)
    ws.append(["rule", "ok", "detail", "blocking"])
    for cell in ws[1]:
        cell.font = header_font
    checks = []
    try:
        from lvke_mcp.domains.finance import finance_model as fm

        checks = fm.check_consistency(fin) or []
    except Exception:  # noqa: BLE001
        checks = []
    for c in checks:
        ws.append([
            c.get("rule"),
            bool(c.get("ok")),
            c.get("detail"),
            bool(c.get("blocking")),
        ])
