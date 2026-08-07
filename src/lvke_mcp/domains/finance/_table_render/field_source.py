"""字段取值来源解析：确认事实域、输入修订、直填行与还款来源。"""

from __future__ import annotations

from typing import Any, Optional



def _get_field(row: dict[str, Any], key: str, row_map: Optional[dict] = None) -> Any:
    if row_map and key in row_map:
        for alt in row_map[key]:
            if alt in row and row[alt] is not None:
                return row[alt]
    # 常见别名
    aliases = {
        "begin": ("begin", "begin_balance"),
        "end": ("end", "end_balance"),
        "principal": ("principal", "repay_principal"),
        "interest": ("interest", "pay_interest"),
        "period": ("period", "year"),
        "year": ("year", "period"),
        "total_cost": ("total_cost", "total"),
        "rate": ("rate", "rate_pct"),
        "begin_balance": ("begin_balance", "begin"),
        "end_balance": ("end_balance", "end"),
        "draw": ("draw",),
    }
    if key in aliases:
        for alt in aliases[key]:
            if alt in row and row[alt] is not None:
                return row[alt]
    return row.get(key)


def _confirmed_fact_domains(fin: dict[str, Any]) -> dict[str, Any]:
    """Return fact domains only after explicit pack confirmation."""
    for source in (
        fin.get("input_revision"), fin.get("finance_inputs"), fin.get("raw"), fin,
    ):
        if not isinstance(source, dict):
            continue
        pack = source.get("finance_fact_pack") or source.get("fact_pack")
        if not isinstance(pack, dict):
            continue
        if pack.get("version") != "finance_fact_pack.v1":
            continue
        if str(pack.get("confirmation_status") or "").lower() != "confirmed":
            continue
        domains = pack.get("domains")
        if isinstance(domains, dict):
            return domains
    return {}


def _effective_input_revision(fin: dict[str, Any]) -> dict[str, Any]:
    """Return the single authoritative input source for all renderers.

    Presence of ``input_revision`` wins even when it is an empty mapping; the
    legacy ``finance_inputs`` snapshot is only a fallback for pre-revision runs.
    """
    value = fin.get("input_revision")
    if isinstance(value, dict) and "input_revision" in fin:
        return value
    legacy = fin.get("finance_inputs")
    return legacy if isinstance(legacy, dict) else {}


def _approved_direct_rows(fin: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Read a direct fact list only when that list has its own approval marker."""
    fin_in = _effective_input_revision(fin)
    if not isinstance(fin_in, dict):
        return []
    status = str(
        fin_in.get(f"{key}_confirmation_status")
        or fin_in.get(f"{key}_review_status")
        or ""
    ).lower()
    if status not in {"confirmed", "reviewed", "verified", "approved"}:
        return []
    rows = fin_in.get(key)
    return [dict(row) for row in rows or [] if isinstance(row, dict)] if isinstance(rows, list) else []


def _repay_source_facts(fin: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    domains = _confirmed_fact_domains(fin)
    debt_schedule = domains.get("debt_schedule") if isinstance(domains, dict) else None
    if isinstance(debt_schedule, dict):
        rows = debt_schedule.get("debt_repay_sources") or debt_schedule.get("repay_sources")
        if isinstance(rows, list) and rows:
            return [dict(row) for row in rows if isinstance(row, dict)], "confirmed_fact_pack.debt_schedule"
    direct = _approved_direct_rows(fin, "debt_repay_sources")
    if direct:
        return direct, "approved_input.debt_repay_sources"
    return [], ""


def _source_kind(name: Any) -> str:
    text = str(name or "").strip().lower()
    if any(token in text for token in ("利润", "profit", "distributable")):
        return "profit"
    if any(token in text for token in ("折旧", "depreciation", "dep")):
        return "depreciation"
    if any(token in text for token in ("摊销", "amortization", "amort")):
        return "amortization"
    return ""


def _source_value(
    facts: list[dict[str, Any]],
    *,
    kind: str,
    year_index: int,
    base: float,
) -> Optional[float]:
    values: list[float] = []
    for fact in facts:
        if _source_kind(fact.get("name") or fact.get("source") or fact.get("category")) != kind:
            continue
        fact_year = fact.get("year")
        if fact_year not in (None, ""):
            try:
                if int(fact_year) != year_index + 1:
                    continue
            except (TypeError, ValueError):
                continue
        schedule = fact.get("annual_schedule_wan") or fact.get("schedule_wan")
        if isinstance(schedule, list) and schedule:
            if year_index < 0 or year_index >= len(schedule):
                values.append(0.0)
                continue
            try:
                values.append(float(schedule[year_index] or 0.0))
            except (TypeError, ValueError):
                pass
            continue
        amount = fact.get("annual_wan")
        if amount is None:
            amount = fact.get("amount_wan")
        if amount is not None:
            try:
                values.append(float(amount))
            except (TypeError, ValueError):
                pass
            continue
        share = fact.get("share")
        if share is not None:
            try:
                ratio = float(share)
                ratio = ratio / 100.0 if abs(ratio) > 1.0 else ratio
                # share is a claim on capacity, not a hard allocation that can
                # zero-out when base is temporarily 0. Keep a non-null series so
                # structure checks can still see confirmed sources; coverage uses
                # max(base*share, debt need) at render time when possible.
                values.append(base * ratio)
            except (TypeError, ValueError):
                pass
    if not values:
        return None
    return round(sum(values), 2)
