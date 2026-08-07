"""Compare exact benchmark dimensions without cross-basis deviation inference."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from lvke_mcp.adapters.data_analysis_repository import BENCHMARK_COMPARISON_STORE

from .envelope import _missing


def _benchmark_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def compare_benchmark(
    workspace_id: str,
    subject: dict[str, Any],
    benchmarks: list[dict[str, Any]],
    *,
    attention_threshold_pct: float = 15,
    material_threshold_pct: float = 30,
) -> dict[str, Any]:
    """Compare exact benchmark dimensions and never infer a cross-basis deviation."""

    attention = _benchmark_decimal(attention_threshold_pct)
    material = _benchmark_decimal(material_threshold_pct)
    if (
        attention is None
        or material is None
        or attention < 0
        or material <= attention
        or material > 1000
    ):
        return _missing(
            "benchmark_thresholds_invalid",
            "偏差阈值必须满足 0 <= attention < material <= 1000",
        )
    required_dimensions = ("metric", "unit", "period", "region", "scope", "tax_basis")
    missing_subject = [
        field for field in (*required_dimensions, "value")
        if subject.get(field) in (None, "")
    ]
    subject_value = _benchmark_decimal(subject.get("value"))
    if missing_subject or subject_value is None:
        return _missing(
            "benchmark_subject_invalid",
            "待比较对象缺少数值或完整期间、地区、范围、单位和税基口径",
        )
    comparable: list[dict[str, Any]] = []
    unable: list[dict[str, Any]] = []
    for index, benchmark in enumerate(benchmarks):
        benchmark_id = str(benchmark.get("benchmark_id") or f"benchmark_{index + 1:03d}")
        value = _benchmark_decimal(benchmark.get("value"))
        mismatch_fields = [
            field
            for field in required_dimensions
            if str(subject.get(field) or "").strip().casefold()
            != str(benchmark.get(field) or "").strip().casefold()
        ]
        if value is None:
            mismatch_fields.append("value")
        if mismatch_fields:
            unable.append(
                {
                    "benchmark_id": benchmark_id,
                    "reason": "benchmark_basis_incompatible",
                    "mismatch_fields": sorted(set(mismatch_fields)),
                    "deviation_pct": None,
                }
            )
            continue
        if value == 0:
            unable.append(
                {
                    "benchmark_id": benchmark_id,
                    "reason": "benchmark_zero_base",
                    "mismatch_fields": [],
                    "deviation_pct": None,
                }
            )
            continue
        deviation = ((subject_value - value) / abs(value) * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        absolute_deviation = abs(deviation)
        if absolute_deviation >= material:
            severity = "material"
        elif absolute_deviation >= attention:
            severity = "attention"
        else:
            severity = "within_threshold"
        comparable.append(
            {
                "benchmark_id": benchmark_id,
                "benchmark_value": float(value),
                "subject_value": float(subject_value),
                "deviation_pct": float(deviation),
                "absolute_deviation_pct": float(absolute_deviation),
                "severity": severity,
                "compatible": True,
                "source_id": benchmark.get("source_id"),
                "locator": benchmark.get("locator"),
            }
        )
    status = "partial" if unable else "ok"
    payload = {
        "object_type": "BenchmarkComparison",
        "subject": subject,
        "benchmarks": benchmarks,
        "thresholds": {
            "attention_pct": float(attention),
            "material_pct": float(material),
        },
        "comparable_results": comparable,
        "unable_to_compare": unable,
        "aggregation": "none",
        "comparison_boundary": "只有 metric、period、region、scope、unit 和 tax_basis 完全一致时才计算偏差；不执行模糊换算或跨口径推断。",
    }
    source_ids = [str(subject.get("source_id") or "")]
    source_ids.extend(
        str(item.get("source_id") or "")
        for item in benchmarks
        if isinstance(item, dict)
    )
    record = BENCHMARK_COMPARISON_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_compare_benchmark",
        status=status,
        source_ids=[item for item in source_ids if item],
        basis={
            "subject": subject,
            "benchmarks": benchmarks,
            "thresholds": payload["thresholds"],
        },
    )
    return {
        "success": status == "ok",
        "business_success": status == "ok",
        "system_success": True,
        "transport_success": True,
        "status": status,
        "benchmark_comparison_id": record["object_id"],
        "comparable_results": comparable,
        "unable_to_compare": unable,
        "aggregation": "none",
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "resource_uris": [record["resource_uri"]],
        "warnings": (
            ["部分 benchmark 的期间、地区、范围、单位或税基不兼容，未计算偏差"]
            if unable
            else []
        ),
        "blockers": [],
        "next_actions": ["对 unable_to_compare 项补充同口径 benchmark 或显式归一化后重试"],
    }
