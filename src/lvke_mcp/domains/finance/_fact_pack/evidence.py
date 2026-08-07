"""逐域证据支撑判定。"""

from __future__ import annotations

from typing import Any

from .depth import (
    _domain_fact_leaves,
)


def _values_close(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) is bool(right)
    try:
        left_n = float(left)
        right_n = float(right)
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()
    scale = max(abs(left_n), abs(right_n), 1.0)
    return abs(left_n - right_n) <= max(0.01, scale * 0.005)


def _evidence_supports_domain(
    domain: str,
    domain_value: Any,
    evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Require reviewed evidence to bind EVERY numeric fact leaf.

    Rules (fail-closed for formal):
    - each fact leaf must be bound by an approved evidence row, preferably by
      matching fact_path; otherwise by reviewed_value == leaf value (+unit/period).
    - every reviewed evidence value must match some leaf (no orphan numbers).
    - 100% leaf coverage required; partial coverage only supports reference.
    """
    leaves = _domain_fact_leaves(domain, domain_value)
    if not evidence_rows:
        return {
            "ok": False,
            "leaf_count": len(leaves),
            "matched_leaves": 0,
            "unmatched_leaves": [leaf["fact_path"] for leaf in leaves],
            "missing_fact_paths": [leaf["fact_path"] for leaf in leaves],
            "detail": "missing evidence rows",
        }
    if not all(bool(row.get("binding_ok")) for row in evidence_rows):
        return {
            "ok": False,
            "leaf_count": len(leaves),
            "matched_leaves": 0,
            "unmatched_leaves": [leaf["fact_path"] for leaf in leaves],
            "missing_fact_paths": [leaf["fact_path"] for leaf in leaves],
            "detail": "binding_ok incomplete",
        }
    if not leaves:
        return {
            "ok": True,
            "leaf_count": 0,
            "matched_leaves": 0,
            "unmatched_leaves": [],
            "missing_fact_paths": [],
            "detail": "no numeric leaves; binding presence only",
        }

    # Index evidence by fact_path and by numeric value.
    evidence_by_path: dict[str, dict[str, Any]] = {}
    evidence_values: list[tuple[Any, dict[str, Any]]] = []
    orphan_values: list[Any] = []
    for row in evidence_rows:
        fact_path = str(row.get("fact_path") or "").strip()
        raw = None
        for key in (
            "reviewed_value", "value", "amount", "amount_wan", "numeric_value", "cell_value",
        ):
            if row.get(key) not in (None, ""):
                raw = row.get(key)
                break
        number = raw if raw not in (None, "") else None
        if fact_path:
            evidence_by_path[fact_path] = row
        if number is not None:
            evidence_values.append((number, row))

    leaf_by_path = {str(leaf["fact_path"]): leaf for leaf in leaves}
    matched_paths: list[str] = []
    unmatched_paths: list[str] = []
    value_mismatches: list[dict[str, Any]] = []
    unit_period_mismatches: list[dict[str, Any]] = []
    used_value_indices: set[int] = set()
    for leaf in leaves:
        path = leaf["fact_path"]
        target = leaf["value"]
        # Prefer fact_path binding with value confirmation.
        row = evidence_by_path.get(path)
        bound = False
        if row is not None:
            bound_val: Any = None
            for key in ("reviewed_value", "value", "amount", "amount_wan", "numeric_value"):
                if row.get(key) not in (None, ""):
                    bound_val = row.get(key)
                    break
            # Formal requires exact fact_path identity, value, unit and period.
            bound = bound_val is not None and _values_close(bound_val, target)
            expected_unit = leaf.get("unit")
            expected_period = leaf.get("period")
            actual_unit = row.get("unit")
            actual_period = row.get("period") or row.get("year")
            if bound and expected_unit not in (None, "") and str(actual_unit or "") != str(expected_unit):
                unit_period_mismatches.append({"fact_path": path, "expected_unit": expected_unit, "actual_unit": actual_unit})
                bound = False
            if bound and expected_period not in (None, "") and str(actual_period or "") != str(expected_period):
                unit_period_mismatches.append({"fact_path": path, "expected_period": expected_period, "actual_period": actual_period})
                bound = False
            if bound_val is not None and not _values_close(bound_val, target):
                value_mismatches.append({"fact_path": path, "expected": target, "actual": bound_val})
            if bound:
                for idx, (_candidate, candidate_row) in enumerate(evidence_values):
                    if candidate_row is row:
                        used_value_indices.add(idx)
                        break
        if bound:
            matched_paths.append(path)
        else:
            unmatched_paths.append(path)

    # Orphan reviewed values that matched no leaf at all.
    for idx, (candidate, _r) in enumerate(evidence_values):
        if not any(_values_close(candidate, leaf["value"]) for leaf in leaves):
            orphan_values.append(candidate)

    orphan_paths = [
        str(row.get("fact_path") or "") for row in evidence_rows
        if str(row.get("fact_path") or "").strip() and str(row.get("fact_path") or "") not in leaf_by_path
    ]
    fully_covered = not unmatched_paths and not orphan_values and not unit_period_mismatches and not orphan_paths
    # 100% leaf coverage → formal-eligible. Partial → reference only.
    ok = fully_covered
    return {
        "ok": ok,
        "leaf_count": len(leaves),
        "matched_leaves": len(matched_paths),
        "unmatched_leaves": unmatched_paths,
        "missing_fact_paths": unmatched_paths,
        "orphan_evidence_values": orphan_values,
        "orphan_fact_paths": orphan_paths,
        "value_mismatches": value_mismatches,
        "unit_period_mismatches": unit_period_mismatches,
        "coverage": round(len(matched_paths) / len(leaves), 4) if leaves else 1.0,
        "detail": (
            "all fact leaves bound to approved evidence"
            if ok else
            f"unbound leaves={unmatched_paths[:4]}; orphans={orphan_values[:4]}"
        ),
    }
