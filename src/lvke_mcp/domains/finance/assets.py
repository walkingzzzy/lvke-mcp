"""Asset depreciation / amortization / terminal value (P1-2)."""

from __future__ import annotations

from typing import Any, Optional


def resolve_life_years(user_years: Optional[int], op_years: int) -> dict[str, Any]:
    """Do NOT silently extend life to operation period (fix F13-P1-02).

    - Missing input → default to op_years (explicit default, not a rewrite).
    - User years kept as-is even when shorter or longer than op_years.
    - When user years < op_years: depreciate only for user years then zero
      (caller uses life for annual charge and active flags).
    """
    op_years = max(int(op_years or 1), 1)
    if user_years is None or int(user_years) <= 0:
        return {
            "years": op_years,
            "source": "default_op_years",
            "rewritten": False,
            "user_years": None,
        }
    y = int(user_years)
    return {
        "years": y,
        "source": "user",
        "rewritten": False,
        "user_years": y,
        "shorter_than_op": y < op_years,
        "longer_than_op": y > op_years,
    }


def annual_straight_line(
    original: float,
    years: int,
    *,
    salvage_rate: float = 0.0,
) -> float:
    years = max(int(years or 1), 1)
    original = float(original or 0.0)
    salvage_rate = float(salvage_rate or 0.0)
    depreciable = original * (1.0 - salvage_rate)
    return round(depreciable / years, 2)


def annual_double_declining(
    original: float,
    years: int,
    *,
    salvage_rate: float = 0.0,
    year_index: int = 1,
) -> float:
    """Double-declining balance charge for one year, floored at salvage."""

    years = max(int(years or 1), 1)
    original = float(original or 0.0)
    salvage = original * float(salvage_rate or 0.0)
    book = original
    rate = 2.0 / years
    charge = 0.0
    for year in range(1, years + 1):
        remaining = max(book - salvage, 0.0)
        year_charge = remaining if year == years else min(book * rate, remaining)
        if year == max(int(year_index or 1), 1):
            charge = year_charge
            break
        book = max(book - year_charge, salvage)
    return round(charge, 2)


def depreciation_charge(
    original: float,
    years: int,
    *,
    salvage_rate: float = 0.0,
    method: str = "straight_line",
    year_index: int = 1,
    tax_method: str | None = None,
) -> dict[str, Any]:
    """Book depreciation with optional tax-book separation."""

    book_method = "double_declining" if str(method or "") == "double_declining" else "straight_line"
    tax_name = str(tax_method or book_method)
    if book_method == "double_declining":
        book = annual_double_declining(
            original, years, salvage_rate=salvage_rate, year_index=year_index,
        )
    else:
        book = annual_straight_line(original, years, salvage_rate=salvage_rate)
    if tax_name == "double_declining":
        tax = annual_double_declining(
            original, years, salvage_rate=salvage_rate, year_index=year_index,
        )
    else:
        tax = annual_straight_line(original, years, salvage_rate=salvage_rate)
    return {
        "book_depreciation_wan": book,
        "tax_depreciation_wan": tax,
        "temporary_difference_wan": round(book - tax, 2),
        "depreciation_method": book_method,
        "tax_depreciation_method": tax_name,
    }


def terminal_recovery(
    *,
    original: float,
    annual_dep: float,
    dep_years: int,
    op_years: int,
    salvage_rate: float,
    amort_original: float = 0.0,
    annual_amort: float = 0.0,
    amort_years: int = 0,
) -> dict[str, Any]:
    """期末回收 = 残值 + 未折完/未摊完账面净值（当寿命 > 计算期运营年数）。"""
    original = float(original or 0.0)
    op_years = max(int(op_years or 0), 0)
    dep_years = max(int(dep_years or 0), 0)
    salvage = round(original * float(salvage_rate or 0.0), 2)
    # Book value path: after min(op, dep_years) of charges
    charged_years = min(op_years, dep_years) if dep_years > 0 else 0
    accum_dep = round(float(annual_dep or 0.0) * charged_years, 2)
    # Cap accum at depreciable amount
    depreciable = round(original * (1.0 - float(salvage_rate or 0.0)), 2)
    if accum_dep > depreciable:
        accum_dep = depreciable
    book = round(original - accum_dep, 2)
    # Unrecovered beyond salvage when life > op
    unrecovered = round(float(annual_dep or 0.0) * max(dep_years - op_years, 0), 2)
    amort_unrec = 0.0
    if amort_original and amort_years:
        amort_unrec = round(float(annual_amort or 0.0) * max(int(amort_years) - op_years, 0), 2)
    # Prefer book-value recovery when life exceeds op (includes salvage embedded in book)
    if dep_years > op_years and op_years > 0:
        total = round(book + amort_unrec, 2)
        method = "book_value_plus_amort_unrecovered"
    else:
        total = round(salvage + unrecovered + amort_unrec, 2)
        method = "salvage_plus_unrecovered"
    return {
        "terminal_recovery": total,
        "salvage": salvage,
        "unrecovered_dep": unrecovered,
        "unrecovered_amort": amort_unrec,
        "book_value_end": book,
        "accum_dep": accum_dep,
        "method": method,
    }


def yearly_dep_schedule(
    *,
    original: float,
    annual: float,
    years: int,
    op_years: int,
    salvage_rate: float,
) -> list[dict[str, Any]]:
    """Yearly depreciation rows; charge stops after life years (no silent extend)."""
    rows = []
    accum = 0.0
    depreciable = round(float(original or 0.0) * (1.0 - float(salvage_rate or 0.0)), 2)
    for y in range(1, max(int(op_years), 1) + 1):
        if y <= max(int(years), 0) and accum < depreciable - 1e-9:
            charge = min(float(annual or 0.0), round(depreciable - accum, 2))
        else:
            charge = 0.0
        accum = round(accum + charge, 2)
        end_book = round(float(original or 0.0) - accum, 2)
        rows.append(
            {
                "year": y,
                "original_value": round(float(original or 0.0), 2),
                "salvage_rate": float(salvage_rate or 0.0),
                "dep_years": int(years),
                "depreciation": round(charge, 2),
                "accum_dep": accum,
                "book_value": end_book,
            }
        )
    return rows


def classified_depreciation_schedule(
    classes: list[dict[str, Any]],
    *,
    op_years: int,
) -> dict[str, Any]:
    """Aggregate straight-line schedules for heterogeneous fixed-asset classes.

    Vendor workbooks commonly carry buildings, machinery and vehicles with
    different useful lives.  Collapsing them into one synthetic life changes
    depreciation, tax and terminal book value.  This helper keeps every class
    independent and returns a deterministic aggregate plus the class detail.
    """

    years = max(int(op_years or 0), 1)
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(classes or []):
        if not isinstance(item, dict):
            continue
        try:
            original = float(item.get("original_value_wan") or item.get("original_value") or 0.0)
            life = int(float(item.get("depreciation_years") or item.get("years") or 0))
            salvage_rate = float(item.get("salvage_rate") or 0.0)
        except (TypeError, ValueError):
            continue
        if original <= 0 or life <= 0 or not 0.0 <= salvage_rate < 1.0:
            continue
        method = str(item.get("depreciation_method") or item.get("method") or "straight_line")
        tax_method = str(item.get("tax_depreciation_method") or item.get("tax_method") or method)
        first = depreciation_charge(
            original,
            life,
            salvage_rate=salvage_rate,
            method=method,
            year_index=1,
            tax_method=tax_method,
        )
        normalized.append({
            **item,
            "name": str(item.get("name") or f"资产类别{index + 1}"),
            "original_value_wan": round(original, 6),
            "depreciation_years": life,
            "salvage_rate": salvage_rate,
            "depreciation_method": method,
            "tax_depreciation_method": tax_method,
            "annual_depreciation_wan": first["book_depreciation_wan"],
            "annual_tax_depreciation_wan": first["tax_depreciation_wan"],
        })

    rows: list[dict[str, Any]] = []
    cumulative = 0.0
    for year in range(1, years + 1):
        breakdown = []
        charge = 0.0
        tax_charge = 0.0
        for item in normalized:
            life = int(item["depreciation_years"])
            original = float(item["original_value_wan"])
            method = str(item.get("depreciation_method") or "straight_line")
            tax_method = str(item.get("tax_depreciation_method") or method)
            split = depreciation_charge(
                original,
                life,
                salvage_rate=float(item["salvage_rate"]),
                method=method,
                year_index=year,
                tax_method=tax_method,
            ) if year <= life else {
                "book_depreciation_wan": 0.0,
                "tax_depreciation_wan": 0.0,
                "temporary_difference_wan": 0.0,
            }
            class_charge = round(float(split.get("book_depreciation_wan") or 0.0), 2)
            class_tax = round(float(split.get("tax_depreciation_wan") or 0.0), 2)
            charge = round(charge + class_charge, 2)
            tax_charge = round(tax_charge + class_tax, 2)
            breakdown.append({
                "name": item["name"],
                "original_value_wan": round(original, 2),
                "depreciation_years": life,
                "salvage_rate": float(item["salvage_rate"]),
                "depreciation": class_charge,
                "tax_depreciation": class_tax,
                "temporary_difference": round(class_charge - class_tax, 2),
            })
        cumulative = round(cumulative + charge, 2)
        rows.append({
            "year": year,
            "depreciation": charge,
            "tax_depreciation": tax_charge,
            "temporary_difference": round(charge - tax_charge, 2),
            "cumulative_depreciation": cumulative,
            "classes": breakdown,
        })

    original_total = round(sum(float(item["original_value_wan"]) for item in normalized), 2)
    salvage_total = round(sum(
        float(item["original_value_wan"]) * float(item["salvage_rate"])
        for item in normalized
    ), 2)
    terminal_book_value = round(max(original_total - cumulative, salvage_total), 2)
    return {
        "classes": normalized,
        "rows": rows,
        "original_value_wan": original_total,
        "salvage_value_wan": salvage_total,
        "weighted_salvage_rate": (
            round(salvage_total / original_total, 10) if original_total > 0 else 0.0
        ),
        "max_life_years": max(
            (int(item["depreciation_years"]) for item in normalized), default=0
        ),
        "annual_average_wan": round(
            sum(float(row["depreciation"]) for row in rows) / years, 2
        ),
        "terminal_book_value_wan": terminal_book_value,
    }


def renewal_capex_schedule(
    plan: list[dict[str, Any]],
    *,
    op_years: int,
) -> dict[str, Any]:
    """Build cash, depreciation and terminal book-value schedules for renewals."""

    years = max(int(op_years or 0), 1)
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(plan or []):
        if not isinstance(item, dict):
            continue
        try:
            in_service_year = int(item.get("year") or 0)
            amount = float(item.get("amount_wan") or 0.0)
            life = int(item.get("depreciation_years") or 0)
            salvage_rate = float(item.get("salvage_rate") or 0.0)
        except (TypeError, ValueError):
            continue
        if (
            not 1 <= in_service_year <= years
            or amount <= 0
            or life <= 0
            or not 0 <= salvage_rate < 1
        ):
            continue
        normalized.append({
            **item,
            "name": str(item.get("name") or item.get("asset_class") or f"更新资产{index + 1}"),
            "year": in_service_year,
            "amount_wan": round(amount, 2),
            "depreciation_years": life,
            "salvage_rate": salvage_rate,
            "annual_depreciation_wan": annual_straight_line(
                amount, life, salvage_rate=salvage_rate,
            ),
        })

    capex_by_year = [0.0] * years
    depreciation_by_year = [0.0] * years
    cumulative_capex = [0.0] * years
    terminal_book_value = 0.0
    for item in normalized:
        start = int(item["year"])
        amount = float(item["amount_wan"])
        life = int(item["depreciation_years"])
        annual = float(item["annual_depreciation_wan"])
        salvage = round(amount * float(item["salvage_rate"]), 2)
        capex_by_year[start - 1] = round(capex_by_year[start - 1] + amount, 2)
        charges = 0.0
        for year_index in range(start - 1, years):
            age = year_index - (start - 1) + 1
            charge = annual if age <= life else 0.0
            depreciable_left = round(amount - salvage - charges, 2)
            charge = round(min(charge, max(depreciable_left, 0.0)), 2)
            depreciation_by_year[year_index] = round(
                depreciation_by_year[year_index] + charge, 2,
            )
            charges = round(charges + charge, 2)
        terminal_book_value = round(
            terminal_book_value + max(amount - charges, salvage), 2,
        )
    running = 0.0
    for index, amount in enumerate(capex_by_year):
        running = round(running + amount, 2)
        cumulative_capex[index] = running
    return {
        "items": normalized,
        "capex_by_year": capex_by_year,
        "depreciation_by_year": depreciation_by_year,
        "cumulative_capex_by_year": cumulative_capex,
        "total_capex_wan": round(sum(capex_by_year), 2),
        "terminal_book_value_wan": terminal_book_value,
    }
