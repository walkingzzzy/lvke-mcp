"""Source-reconciliation, peer and segment observation comparison."""

from __future__ import annotations

from typing import Any

from lvke_mcp.adapters.data_analysis_repository import NORMALIZED_COMPARE_STORE

from .envelope import _missing
from .period_norm import normalize_financial_period
from .unit_rules import CONTROLLED_UNIT_RULES


def _missing_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对每个 metric×来源组合列出缺失观察值；缺口如实结构化，不静默留空。"""
    metric_labels: dict[str, str] = {}
    sources: list[str] = []
    covered: set[tuple[str, str]] = set()
    without_value: set[tuple[str, str]] = set()
    for item in observations:
        source_id = str(item.get("source_id") or "").strip()
        metric = str(item.get("metric") or "").strip()
        if source_id and source_id not in sources:
            sources.append(source_id)
        if metric:
            metric_labels.setdefault(metric.lower(), metric)
        if not source_id or not metric:
            continue
        pair = (source_id, metric.lower())
        if item.get("value") is None:
            without_value.add(pair)
        else:
            covered.add(pair)
    missing: list[dict[str, Any]] = []
    for metric_key, metric_label in metric_labels.items():
        for source_id in sources:
            pair = (source_id, metric_key)
            if pair in covered:
                continue
            missing.append(
                {
                    "source_id": source_id,
                    "metric": metric_label,
                    "reason": "value_missing" if pair in without_value else "no_observation",
                }
            )
    return missing


def _period_mismatches(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in observations:
        metric = str(item.get("metric") or "").strip()
        if not metric or item.get("value") is None:
            continue
        period = normalize_financial_period(item.get("period", item.get("as_of")))
        key = (
            metric.lower(),
            str(item.get("unit") or "").strip().lower(),
            str(item.get("scope") or "").strip().lower(),
        )
        groups.setdefault(key, []).append({**item, "period_metadata": period})
    mismatches: list[dict[str, Any]] = []
    for key, items in groups.items():
        period_types = sorted({str(item["period_metadata"]["period_type"]) for item in items})
        if len(period_types) > 1:
            mismatches.append({
                "comparison_key": "|".join(key),
                "period_types": period_types,
                "periods": sorted({str(item["period_metadata"]["normalized"]) for item in items}),
                "reason": "financial_period_granularity_mismatch",
            })
    return mismatches


def _comparison_key(item: dict[str, Any], *, include_entity: bool, include_dimension: bool) -> str:
    period = normalize_financial_period(item.get("period", item.get("as_of")))
    parts = [
        str(item.get("metric") or "").strip().lower(),
        str(item.get("unit") or "").strip().lower(),
        str(period.get("normalized") or ""),
        str(item.get("scope") or "").strip().lower(),
    ]
    if include_entity:
        parts.append(str(item.get("entity") or "").strip().lower())
    if include_dimension:
        parts.append(str(item.get("dimension") or item.get("segment") or "").strip().lower())
    return "|".join(parts)


def _peer_comparison(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for item in observations:
        rows.setdefault(_comparison_key(item, include_entity=True, include_dimension=False), []).append(item)
    peers: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for key, items in rows.items():
        values = {str(item.get("value")) for item in items}
        row = {"comparison_key": key, "entity": items[0].get("entity"), "observations": items}
        if len(values) > 1:
            conflicts.append({**row, "values": sorted(values), "reason": "duplicate_peer_observation_conflict"})
        else:
            peers.append({**row, "value": items[0].get("value"), "unit": items[0].get("unit")})
    return peers, conflicts


def _segment_comparison(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    duplicate_groups: dict[str, list[dict[str, Any]]] = {}
    for item in observations:
        duplicate_groups.setdefault(_comparison_key(item, include_entity=True, include_dimension=True), []).append(item)
    valid_rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for key, items in duplicate_groups.items():
        values = {str(item.get("value")) for item in items}
        if len(values) > 1:
            conflicts.append({
                "comparison_key": key,
                "observations": items,
                "values": sorted(values),
                "reason": "duplicate_segment_observation_conflict",
            })
        else:
            valid_rows.append(items[0])
    summaries: dict[str, list[dict[str, Any]]] = {}
    for item in valid_rows:
        key = _comparison_key(item, include_entity=True, include_dimension=False)
        summaries.setdefault(key, []).append(item)
    result: list[dict[str, Any]] = []
    for key, items in summaries.items():
        numeric = [item for item in items if isinstance(item.get("value"), (int, float)) and not isinstance(item.get("value"), bool)]
        total = sum(float(item["value"]) for item in numeric)
        segments = []
        for item in items:
            value = item.get("value")
            share = (float(value) / total) if total and isinstance(value, (int, float)) and not isinstance(value, bool) else None
            segments.append({**item, "revenue_share": share})
        result.append({
            "comparison_key": key,
            "entity": items[0].get("entity"),
            "metric": items[0].get("metric"),
            "period": normalize_financial_period(items[0].get("period", items[0].get("as_of")))["normalized"],
            "unit": items[0].get("unit"),
            "segment_total": total,
            "segments": segments,
        })
    return result, conflicts


def compare(
    observations: list[dict[str, Any]],
    comparison_mode: str = "source_reconciliation",
) -> dict[str, Any]:
    if comparison_mode not in {"source_reconciliation", "peer", "segment"}:
        return _missing("unsupported_comparison_mode", "comparison_mode 必须为 source_reconciliation、peer 或 segment")
    groups: dict[str, list[dict[str, Any]]] = {}
    unable = []
    for item in observations:
        metric = str(item.get("metric") or "").strip()
        if not metric or item.get("value") is None:
            unable.append({**item, "reason": "missing_metric_or_value"})
            continue
        key = _comparison_key(item, include_entity=False, include_dimension=False)
        groups.setdefault(key, []).append(item)
    consistent, conflicts = [], []
    peer_rows: list[dict[str, Any]] = []
    segment_summaries: list[dict[str, Any]] = []
    if comparison_mode == "peer":
        peer_rows, conflicts = _peer_comparison([item for items in groups.values() for item in items])
    elif comparison_mode == "segment":
        segment_summaries, conflicts = _segment_comparison([item for items in groups.values() for item in items])
    else:
        for key, items in groups.items():
            values = {str(item.get("value")) for item in items}
            target = consistent if len(values) == 1 else conflicts
            target.append({"comparison_key": key, "observations": items, "values": sorted(values)})
    missing = _missing_observations(observations)
    period_mismatches = _period_mismatches(observations)
    warnings = []
    if conflicts or unable:
        warnings.append("存在冲突或不可比较项，未进行静默合并")
    if missing:
        warnings.append("部分来源缺少指标观察值，比较覆盖不完整")
    if period_mismatches:
        warnings.append("存在年度、季度或月度期间粒度不一致，不得直接比较")
    return {
        "success": True,
        "status": "partial" if conflicts or unable or missing or period_mismatches else "ok",
        "comparison_mode": comparison_mode,
        "consistent": consistent,
        "conflicts": conflicts,
        "peer_rows": peer_rows,
        "segment_summaries": segment_summaries,
        "period_mismatches": period_mismatches,
        "missing": missing,
        "unable_to_compare": unable,
        "resource_uris": [],
        "warnings": warnings,
        "blockers": [],
        "next_actions": ["由用户或受控依据确认冲突口径后再进入财务输入"],
    }


def normalize_compare(
    workspace_id: str,
    observations: list[dict[str, Any]],
    conversion_rules: list[dict[str, Any]] | None = None,
    *,
    use_controlled_unit_dictionary: bool = False,
    comparison_mode: str = "source_reconciliation",
) -> dict[str, Any]:
    """Normalize exact units and expose a comparison without fuzzy inference."""

    conversion_rules = list(conversion_rules or [])
    normalized: list[dict[str, Any]] = []
    unprocessed: list[dict[str, Any]] = []
    applied_rules: list[dict[str, Any]] = []
    for observation in observations:
        metric = str(observation.get("metric") or "").strip()
        unit = str(observation.get("unit") or "").strip()
        value = observation.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            unprocessed.append({**observation, "reason": "non_numeric_value"})
            continue
        matched: dict[str, Any] | None = None
        for rule in conversion_rules:
            if (
                metric.lower() == str(rule.get("metric") or "").strip().lower()
                and unit.lower() == str(rule.get("source_unit") or "").strip().lower()
            ):
                matched = rule
                break
        if matched is None and use_controlled_unit_dictionary:
            controlled = CONTROLLED_UNIT_RULES.get(unit)
            if controlled is None:
                controlled = next(
                    (rule for source, rule in CONTROLLED_UNIT_RULES.items() if source.lower() == unit.lower()),
                    None,
                )
            if controlled is not None:
                target_unit, factor, basis = controlled
                matched = {
                    "metric": metric,
                    "source_unit": unit,
                    "target_unit": target_unit,
                    "factor": factor,
                    "conversion_basis": basis,
                    "rule_source": "controlled_unit_dictionary",
                }
        if matched is None:
            unprocessed.append({**observation, "reason": "no_explicit_conversion_rule"})
            continue
        normalized_value = value * float(matched["factor"])
        normalized_observation = {
                **observation,
                "original_value": value,
                "original_unit": unit,
                "value": normalized_value,
                "unit": str(matched["target_unit"]),
                "conversion_rule": {
                    "metric": str(matched["metric"]),
                    "source_unit": str(matched["source_unit"]),
                    "target_unit": str(matched["target_unit"]),
                    "factor": float(matched["factor"]),
                    "conversion_basis": str(matched["conversion_basis"]),
                    "rule_source": str(matched.get("rule_source") or "caller_declared"),
                },
            }
        period = normalize_financial_period(
            observation.get("period", observation.get("as_of"))
        )
        normalized_observation["period"] = period["normalized"]
        normalized_observation["period_metadata"] = period
        normalized.append(normalized_observation)
        if matched not in applied_rules:
            applied_rules.append(matched)
    comparison = compare(normalized, comparison_mode=comparison_mode) if normalized else {
        "consistent": [], "conflicts": [], "missing": [], "unable_to_compare": [], "warnings": []
    }
    status_value = "partial" if unprocessed or comparison.get("status") == "partial" else "ok"
    payload = {
        "observations": observations,
        "conversion_rules": conversion_rules,
        "use_controlled_unit_dictionary": use_controlled_unit_dictionary,
        "comparison_mode": comparison_mode,
        "normalized_observations": normalized,
        "unprocessed": unprocessed,
        "comparison": comparison,
        "normalization_boundary": "仅按调用方显式规则或明确启用的受控精确单位字典换算；不会推断单位、选择正确值或写入 FinanceSpec。",
    }
    record = NORMALIZED_COMPARE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_normalize_compare",
        status=status_value,
        source_ids=[str(item.get("source_id")) for item in observations if str(item.get("source_id") or "")],
        basis={
            "observations": observations,
            "conversion_rules": conversion_rules,
            "use_controlled_unit_dictionary": use_controlled_unit_dictionary,
            "comparison_mode": comparison_mode,
        },
    )
    warnings = list(comparison.get("warnings") or [])
    if unprocessed:
        warnings.append("部分观察值没有精确匹配的显式换算规则，未做单位转换")
    return {
        "success": True,
        "status": status_value,
        "comparison_id": record["object_id"],
        "normalized_observations": normalized,
        "unprocessed": unprocessed,
        "comparison": comparison,
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": [],
        "next_actions": ["核对 conversion_basis、时点和范围；冲突或缺口不得自动写入 FinanceSpec"],
    }
