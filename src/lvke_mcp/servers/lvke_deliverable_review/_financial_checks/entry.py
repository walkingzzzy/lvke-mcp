"""财务 run 审查入口；固定顺序聚合各规则组的 findings。"""

from __future__ import annotations

from typing import Any, Iterable

from lvke_mcp.servers.lvke_deliverable_review import rules

from .acquisition import (
    _acquisition_checks,
)

from .generic_debt import (
    _generic_debt_checks,
)

from .generic_statements import (
    _generic_depreciation_checks,
    _generic_period_checks,
    _generic_working_capital_checks,
)

from .generic_tax_source import (
    _generic_finance_source_checks,
    _generic_sensitivity_checks,
    _generic_tax_and_source_checks,
)


def review_finance_run(
    run: dict[str, Any],
    *,
    target_id: str,
    target_type: str,
    applicable_rules: Iterable[str],
    source_rule_rows: Iterable[dict[str, Any]],
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], set[str], dict[str, Any]]:
    """Run deterministic checks and report only rules whose inputs were evaluated."""

    applicable = set(applicable_rules)
    source_rules = {
        str(row.get("rule_id") or ""): row
        for row in source_rule_rows
        if str(row.get("rule_id") or "") in applicable
        and row.get("check_kind") == "deterministic"
    }
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    executed: set[str] = set()
    metrics: dict[str, Any] = {}
    if not run or not run.get("available"):
        return findings, ["bound_finance_run_unavailable"], executed, metrics

    is_acquisition = target_type.startswith("acquisition_") or bool(run.get("result")) and (
        str(run.get("model_version") or "").startswith("acquisition_model")
        or str((run.get("result") or {}).get("model_version") or "").startswith("acquisition_model")
    )
    if is_acquisition:
        rows, missing, done, check_metrics = _acquisition_checks(
            run, target_id, source_rules, standard_basis,
        )
        findings.extend(rows)
        incomplete.extend(missing)
        executed.update(done)
        metrics.update(check_metrics)
    else:
        for checker in (
            _generic_period_checks,
            _generic_depreciation_checks,
            _generic_debt_checks,
            _generic_working_capital_checks,
            _generic_sensitivity_checks,
            _generic_tax_and_source_checks,
            _generic_finance_source_checks,
        ):
            rows, missing, done, check_metrics = checker(
                run, target_id, source_rules, standard_basis,
            )
            findings.extend(rows)
            incomplete.extend(missing)
            executed.update(done)
            metrics.update(check_metrics)

    allowed = applicable | set(source_rules)
    findings = [
        row for row in findings
        if str(row.get("rule_id") or "") in allowed
    ]
    executed.intersection_update(allowed)
    incomplete = [
        reason for reason in incomplete
        if not reason.startswith("rule_input_unavailable:")
        or reason.removeprefix("rule_input_unavailable:") in allowed
    ]
    return findings, sorted(set(incomplete)), executed, metrics

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "Iterable",
    "_acquisition_checks",
    "_generic_debt_checks",
    "_generic_depreciation_checks",
    "_generic_finance_source_checks",
    "_generic_period_checks",
    "_generic_sensitivity_checks",
    "_generic_tax_and_source_checks",
    "_generic_working_capital_checks",
    "review_finance_run",
    "rules",
]
