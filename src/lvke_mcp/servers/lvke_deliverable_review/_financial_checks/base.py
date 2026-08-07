"""规则注册表、数值/容差原语、来源基准与 finding 构造。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from lvke_mcp.servers.lvke_deliverable_review import rules


BUILTIN_RULES = {
    "FIN.DEPRECIATION.RECALC",
    "FIN.TAX.RECALC",
    "FIN.DEBT.ROLLFORWARD",
    "FIN.DEBT.COVERAGE",
    "FIN.WORKING_CAPITAL.DRIVER",
    "FIN.PERIOD.RECONCILIATION",
    "FIN.SENSITIVITY.RERUN",
}


def _minimum_capital_pct(run: dict[str, Any]) -> tuple[float | None, str]:
    explicit = _number(
        (run.get("funding") or {}).get("minimum_capital_pct")
        or (run.get("spec") or {}).get("minimum_capital_pct")
        or (run.get("raw") or {}).get("minimum_capital_pct")
    )
    if explicit is not None:
        return (explicit * 100.0 if 0 < explicit <= 1 else explicit), "explicit"
    industry = str(run.get("industry") or (run.get("spec") or {}).get("industry") or "")
    try:
        import yaml

        path = Path(__file__).resolve().parents[2] / "config" / "finance_params.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, TypeError):
        return None, ""
    current = document.get("min_capital_ratio_2019") or {}
    legacy = document.get("min_capital_ratio") or {}
    candidates = [
        (str(key), _number(value))
        for mapping in (current, legacy)
        for key, value in mapping.items()
        if str(key) and str(key) in industry and _number(value) is not None
    ]
    if candidates:
        key, ratio = max(candidates, key=lambda item: len(item[0]))
        return float(ratio) * 100.0, f"config:{key}"
    default = _number(legacy.get("一般工业"))
    if default is not None and industry:
        return default * 100.0, "config:一般工业"
    return None, ""


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tolerance(*values: float | None) -> float:
    scale = max((abs(value) for value in values if value is not None), default=0.0)
    return max(0.01, scale * 1e-7)


def _different(actual: float | None, expected: float | None) -> bool:
    return actual is None or expected is None or abs(actual - expected) > _tolerance(actual, expected)


def _source_basis(
    source_rule: dict[str, Any] | None,
    standard_basis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not source_rule:
        return deepcopy(standard_basis)
    standard = source_rule.get("standard") or {}
    package_id = str(standard.get("package_id") or "")
    artifact_id = str(standard.get("artifact_id") or "")
    matching = next(
        (
            row for row in standard_basis
            if str(row.get("standard_package_id") or "") == package_id
            and str(row.get("artifact_id") or "") == artifact_id
        ),
        {},
    )
    return [{
        **deepcopy(matching),
        "standard_package_id": package_id,
        "artifact_id": artifact_id,
        "content_hash": standard.get("sha256") or matching.get("content_hash"),
        "locator": standard.get("locator"),
        "quote": standard.get("quote"),
    }]


def _finding(
    rule_id: str,
    severity: str,
    message: str,
    *,
    category: str,
    target_id: str,
    location: dict[str, Any],
    standard_basis: list[dict[str, Any]],
    source_rules: dict[str, dict[str, Any]],
    expected: Any = None,
    actual: Any = None,
    difference: Any = None,
    tolerance: Any = None,
    remediation: str = "修正结构化财务输入并生成新 run 后复测",
) -> dict[str, Any]:
    source_rule = source_rules.get(rule_id)
    item = rules.finding(
        rule_id,
        severity,
        message,
        category=category,
        blocking=bool((source_rule or {}).get("blocking", severity in {"P0", "P1"})),
        expected=expected,
        actual=actual,
        difference=difference,
        tolerance=tolerance,
        target_location={"run_id": target_id, **location},
        standard_basis=_source_basis(source_rule, standard_basis),
        review_area="finance",
        remediation=remediation,
    )
    return item
