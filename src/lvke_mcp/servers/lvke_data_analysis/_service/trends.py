"""Research-ready growth and common-size trend calculation by exact period."""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters.data_analysis_repository import FINANCIAL_TREND_STORE

from .envelope import _missing
from .period_norm import normalize_financial_period


def _trend_record(item: dict[str, Any], method: str, value: float, base: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity": item.get("entity"), "metric": item.get("metric"), "period": item.get("period"),
        "unit": item.get("unit"), "method": method, "result": value,
        "base_period": base.get("period"), "base_value": base.get("value"), "current_value": item.get("value"),
    }


def _append_growth_result(
    results: list[dict[str, Any]], issues: list[dict[str, Any]], item: dict[str, Any],
    prior: dict[str, Any] | None, method: str,
) -> None:
    if prior is None:
        issues.append({"method": method, "entity": item.get("entity"), "metric": item.get("metric"), "period": item.get("period"), "reason": "missing_comparison_period"})
        return
    base = float(prior["value"])
    if base == 0:
        issues.append({"method": method, "entity": item.get("entity"), "metric": item.get("metric"), "period": item.get("period"), "reason": "zero_comparison_base"})
        return
    results.append(_trend_record(item, method, (float(item["value"]) - base) / abs(base), prior))


def _elapsed_years(first: dict[str, Any], last: dict[str, Any]) -> float:
    first_month = _period_month_index(first)
    last_month = _period_month_index(last)
    return (last_month - first_month) / 12.0


def _period_month_index(period: dict[str, Any]) -> int:
    year = int(period["year"])
    if period["period_type"] == "annual":
        month = 12
    elif period["period_type"] == "quarterly":
        month = int(period["quarter"]) * 3
    else:
        month = int(period["month"])
    return year * 12 + month


def _append_common_size(
    results: list[dict[str, Any]], issues: list[dict[str, Any]], rows: list[dict[str, Any]],
    bases: dict[str, str],
) -> None:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for item in rows:
        key = (
            str(item.get("entity") or "").lower(), str(item.get("period") or ""),
            str(item.get("unit") or "").lower(), str(item.get("statement") or "").lower(),
        )
        grouped.setdefault(key, []).append(item)
    defaults = {"income_statement": "revenue", "balance_sheet": "total_assets", "cash_flow_statement": "operating_cash_flow"}
    for key, items in grouped.items():
        statement = key[3]
        base_metric = bases.get(statement) or defaults.get(statement)
        base = next((item for item in items if item.get("is_common_size_base") is True), None)
        if base is None and base_metric:
            base = next((item for item in items if str(item.get("metric") or "").lower() == base_metric), None)
        if base is None or float(base.get("value") or 0) == 0:
            issues.append({"method": "common_size", "group_key": "|".join(key), "reason": "missing_or_zero_common_size_base"})
            continue
        denominator = float(base["value"])
        for item in items:
            results.append(_trend_record(item, "common_size", float(item["value"]) / denominator, base))


def financial_trends(
    workspace_id: str,
    observations: list[dict[str, Any]],
    methods: list[str] | None = None,
    common_size_bases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Calculate research-ready growth and common-size metrics by exact period."""

    requested = list(dict.fromkeys(methods or ["yoy", "qoq", "cagr", "common_size"]))
    unsupported = [item for item in requested if item not in {"yoy", "qoq", "cagr", "common_size"}]
    if unsupported:
        return _missing("unsupported_trend_method", f"不支持的趋势方法：{', '.join(unsupported)}")
    bases = {str(key).lower(): str(value).lower() for key, value in (common_size_bases or {}).items()}
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in observations:
        value = item.get("value")
        period = normalize_financial_period(item.get("period", item.get("as_of")))
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            rejected.append({**item, "reason": "non_numeric_value"})
            continue
        if period["period_type"] == "unknown":
            rejected.append({**item, "reason": "unknown_financial_period"})
            continue
        rows.append({**item, "period": period["normalized"], "period_metadata": period})
    series: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for item in rows:
        key = (
            str(item.get("entity") or "").lower(),
            str(item.get("metric") or "").lower(),
            str(item.get("unit") or "").lower(),
            str(item.get("scope") or "").lower(),
            str(item["period_metadata"]["period_type"]),
        )
        series.setdefault(key, []).append(item)
    results: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for key, items in series.items():
        ordered = sorted(items, key=lambda item: int(item["period_metadata"]["sort_key"]))
        indexed = {int(item["period_metadata"]["sort_key"]): item for item in ordered}
        for item in ordered:
            meta = item["period_metadata"]
            if "yoy" in requested:
                delta = 100 if meta["period_type"] == "annual" else 100
                prior = indexed.get(int(meta["sort_key"]) - delta)
                _append_growth_result(results, issues, item, prior, "yoy")
            if "qoq" in requested and meta["period_type"] == "quarterly":
                year, quarter = int(meta["year"]), int(meta["quarter"])
                previous_key = (year - 1) * 100 + 12 if quarter == 1 else year * 100 + (quarter - 1) * 3
                _append_growth_result(results, issues, item, indexed.get(previous_key), "qoq")
        if "cagr" in requested and len(ordered) >= 2:
            first, last = ordered[0], ordered[-1]
            years = _elapsed_years(first["period_metadata"], last["period_metadata"])
            first_value, last_value = float(first["value"]), float(last["value"])
            if years <= 0 or first_value <= 0 or last_value < 0:
                issues.append({"method": "cagr", "series_key": "|".join(key), "reason": "invalid_cagr_base_or_span"})
            else:
                results.append(_trend_record(last, "cagr", (last_value / first_value) ** (1.0 / years) - 1.0, first))
    if "common_size" in requested:
        _append_common_size(results, issues, rows, bases)
    payload = {
        "observations": observations,
        "methods": requested,
        "common_size_bases": common_size_bases or {},
        "results": results,
        "rejected": rejected,
        "issues": issues,
    }
    status_value = "partial" if rejected or issues else "ok"
    record = FINANCIAL_TREND_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_financial_trends",
        status=status_value,
        source_ids=[str(item.get("source_id")) for item in rows if str(item.get("source_id") or "")],
        basis={"methods": requested, "common_size_bases": common_size_bases or {}, "observations": observations},
    )
    partial_reasons = sorted({
        *[str(item.get("reason") or "rejected_observation") for item in rejected],
        *[str(item.get("reason") or "trend_issue") for item in issues],
    })
    return {
        "success": status_value == "ok",
        "business_success": status_value == "ok",
        "system_success": True,
        "transport_success": True,
        "status": status_value,
        "data_completeness": "complete" if status_value == "ok" else "partial",
        "partial_reasons": partial_reasons,
        "financial_trend_id": record["object_id"],
        "results": results,
        "rejected": rejected,
        "issues": issues,
        "resource_uris": [record["resource_uri"]],
        "warnings": (["部分趋势因期间、缺失值、零基期或口径不足无法计算"] if rejected or issues else []),
        "blockers": [],
        "next_actions": ["核对期间粒度、单位和共同比基准后再用于研报"],
    }
