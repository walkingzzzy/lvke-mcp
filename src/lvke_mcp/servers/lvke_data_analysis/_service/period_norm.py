"""Normalize financial period labels to a canonical structured form."""

from __future__ import annotations

import re
from typing import Any


def normalize_financial_period(value: Any) -> dict[str, Any]:
    """Normalize common annual/quarterly/monthly labels without changing granularity."""

    raw = str(value or "").strip().upper()
    annual = re.fullmatch(r"(?:FY)?(\d{4})([AE])?", raw)
    if annual:
        year = int(annual.group(1))
        suffix = annual.group(2) or ""
        return {
            "raw": str(value or ""), "normalized": str(year), "period_type": "annual",
            "year": year, "quarter": None, "month": None,
            "actual_estimate": "actual" if suffix == "A" else ("estimate" if suffix == "E" else "unspecified"),
            "sort_key": year * 100,
        }
    quarter = re.fullmatch(r"(\d{4})[- ]?Q([1-4])([AE])?", raw)
    if quarter:
        year, number = int(quarter.group(1)), int(quarter.group(2))
        suffix = quarter.group(3) or ""
        return {
            "raw": str(value or ""), "normalized": f"{year}-Q{number}", "period_type": "quarterly",
            "year": year, "quarter": number, "month": None,
            "actual_estimate": "actual" if suffix == "A" else ("estimate" if suffix == "E" else "unspecified"),
            "sort_key": year * 100 + number * 3,
        }
    month = re.fullmatch(r"(\d{4})[-/](0[1-9]|1[0-2])", raw)
    if month:
        year, number = int(month.group(1)), int(month.group(2))
        return {
            "raw": str(value or ""), "normalized": f"{year}-{number:02d}", "period_type": "monthly",
            "year": year, "quarter": (number - 1) // 3 + 1, "month": number,
            "actual_estimate": "unspecified", "sort_key": year * 100 + number,
        }
    return {
        "raw": str(value or ""), "normalized": str(value or ""), "period_type": "unknown",
        "year": None, "quarter": None, "month": None,
        "actual_estimate": "unspecified", "sort_key": None,
    }
