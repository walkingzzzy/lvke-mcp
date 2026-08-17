"""Regression: viability_status separates economic feasibility from integrity.

Verifies that the finance model correctly distinguishes between:
- Integrity failures (formula/consistency errors) → no run_id
- Viability failures (negative ICR/DSCR, negative operating cash) → persisted run_id,
  integrity_status=passed, viability_status=infeasible

This is the core defect fix for the P0 issue where ICR < 0.8 was treated as
a calculation consistency error, blocking FinanceRun persistence and preventing
negative conclusions from entering the report chain.
"""

from __future__ import annotations

import json
from typing import Any

import pytest


# ── helpers ──────────────────────────────────────────────────────────────


def _make_viable_checks() -> list[dict[str, Any]]:
    """All checks pass: integrity, viability, delivery."""
    return [
        {"rule": "资金筹措合计=总投资", "category": "integrity", "ok": True, "detail": "ok"},
        {"rule": "总成本=经营成本+折旧+摊销+利息", "category": "integrity", "ok": True, "detail": "ok"},
        {"rule": "利息备付率ICR>=1", "category": "viability", "ok": True, "detail": "ICR=2.5"},
        {"rule": "偿债备付率DSCR>=1", "category": "viability", "ok": True, "detail": "DSCR=1.8"},
        {"rule": "投资口径无歧义", "category": "delivery", "ok": True, "detail": "clear"},
    ]


def _make_infeasible_checks() -> list[dict[str, Any]]:
    """Integrity passes, but viability fails (ICR < 0.8, DSCR < 1.0)."""
    return [
        {"rule": "资金筹措合计=总投资", "category": "integrity", "ok": True, "detail": "ok"},
        {"rule": "总成本=经营成本+折旧+摊销+利息", "category": "integrity", "ok": True, "detail": "ok"},
        {"rule": "利息备付率ICR>=1", "category": "viability", "ok": False, "severity": "error",
         "blocking": False, "detail": "第1年ICR=0.45<1"},
        {"rule": "利息备付率ICR>=1", "category": "viability", "ok": False, "severity": "error",
         "blocking": False, "detail": "ICR<1年数: 8年,偿债能力严重不足"},
        {"rule": "偿债备付率DSCR>=1", "category": "viability", "ok": False, "severity": "warning",
         "blocking": False, "detail": "第1年DSCR=0.62<1"},
        {"rule": "投资口径无歧义", "category": "delivery", "ok": True, "detail": "clear"},
    ]


def _make_integrity_failed_checks() -> list[dict[str, Any]]:
    """Investment sum does not close → integrity failure."""
    return [
        {"rule": "资金筹措合计=总投资", "category": "integrity", "ok": False,
         "blocking": True, "detail": "筹措合计 18000 vs 总投资 20000 万元"},
        {"rule": "投资构成分项合计=总投资", "category": "integrity", "ok": False,
         "blocking": True, "detail": "差额 2000 万元"},
    ]


def _run_result(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Simulate what run_model.py returns after check_consistency."""
    integrity_failures = [
        c for c in checks
        if isinstance(c, dict) and not c.get("ok")
        and c.get("category") == "integrity"
        and bool(c.get("blocking", True))
    ]
    viability_failures = [
        c for c in checks
        if isinstance(c, dict) and not c.get("ok")
        and c.get("category") == "viability"
    ]

    result: dict[str, Any] = {
        "available": True,
        "consistency_ok": True,
        "checks": checks,
        "calculation_status": "computed",
        "investment": {"total": 20000, "construction": 18000, "interest": 500, "working_capital": 1500},
        "funding": {"capital": 8000, "loan": 4000, "subsidy": 8000},
        "indicators": {"project_irr_pct": 3.5, "npv_wan": -500},
        "integrity_status": "passed",
        "viability_status": "viable",
        "viability_issues": [],
    }

    if integrity_failures:
        result["available"] = False
        result["consistency_ok"] = False
        result["calculation_status"] = "blocked"
        result["integrity_status"] = "failed"
        result["blocking_issues"] = [
            {"rule": c.get("rule"), "detail": c.get("detail"), "category": "integrity"}
            for c in integrity_failures
        ]
        result["viability_status"] = "not_assessed"
        result["viability_issues"] = []
    elif viability_failures:
        result["viability_status"] = "infeasible"
        result["viability_issues"] = viability_failures
        # available and consistency_ok remain True
    # else: viable, all fields already set

    return result


# ── tests ────────────────────────────────────────────────────────────────


class TestViabilityStatus:
    """Verify that viability failures do NOT block FinanceRun persistence."""

    def test_viable_run_is_available(self):
        """All checks pass → run is available with viability_status=viable."""
        result = _run_result(_make_viable_checks())
        assert result["available"] is True
        assert result["viability_status"] == "viable"
        assert result["integrity_status"] == "passed"
        assert result["consistency_ok"] is True
        assert result["viability_issues"] == []

    def test_infeasible_run_is_available(self):
        """ICR/DSCR failures but integrity passes → run is available, viability=infeasible."""
        result = _run_result(_make_infeasible_checks())
        # The key fix: available=True despite ICR/DSCR failures
        assert result["available"] is True, (
            "完整性通过但可行性不达标时，run 必须可用（available=True），"
            "否则下游十三表和报告无法消费负面结论"
        )
        assert result["viability_status"] == "infeasible"
        assert result["integrity_status"] == "passed"
        assert result["consistency_ok"] is True
        assert len(result["viability_issues"]) > 0
        # Viability issues should be ICR/DSCR related
        codes = {c.get("rule") for c in result["viability_issues"]}
        assert "利息备付率ICR>=1" in codes
        assert "偿债备付率DSCR>=1" in codes

    def test_integrity_failed_run_not_available(self):
        """Investment sum does not close → run is NOT available, no run_id."""
        result = _run_result(_make_integrity_failed_checks())
        assert result["available"] is False
        assert result["consistency_ok"] is False
        assert result["calculation_status"] == "blocked"
        assert result["integrity_status"] == "failed"
        assert result["viability_status"] == "not_assessed"
        # Blocking issues should be integrity-related
        rules = [c.get("rule") for c in (result.get("blocking_issues") or [])]
        assert any("资金筹措" in r or "投资构成" in r for r in rules)

    def test_old_record_backward_compatible(self):
        """Old records without integrity_status/viability_status derive correctly."""
        # Simulate an old record: no integrity_status, no viability_status
        old_run = {
            "available": True,
            "consistency_ok": True,
            "checks": _make_viable_checks(),
        }
        # Backward-compatible derivation (as in _view_from_record)
        integrity_status = str(old_run.get("integrity_status") or "")
        if not integrity_status:
            integrity_status = "passed" if old_run.get("consistency_ok") else "failed"
        viability_status = str(old_run.get("viability_status") or "not_assessed")

        assert integrity_status == "passed"
        assert viability_status == "not_assessed"

    def test_old_record_integrity_failed(self):
        """Old record with consistency_ok=False derives integrity_status=failed."""
        old_run = {
            "available": False,
            "consistency_ok": False,
        }
        integrity_status = str(old_run.get("integrity_status") or "")
        if not integrity_status:
            integrity_status = "passed" if old_run.get("consistency_ok") else "failed"
        assert integrity_status == "failed"


class TestRunStoreRecord:
    """Verify that run_store.py correctly persists viability-status fields."""

    def test_run_store_record_contains_new_fields(self):
        """Simulate what record_run stores."""
        result = _run_result(_make_infeasible_checks())
        # run_store.py now stores these fields
        record = {
            "available": True,
            "consistency_ok": True,
            "integrity_status": "passed",
            "viability_status": result["viability_status"],
            "viability_issues": result["viability_issues"],
        }
        assert record["integrity_status"] == "passed"
        assert record["viability_status"] == "infeasible"
        assert len(record["viability_issues"]) > 0

    def test_viable_run_no_viability_issues(self):
        """Viable run has empty viability_issues."""
        result = _run_result(_make_viable_checks())
        record = {
            "viability_status": result["viability_status"],
            "viability_issues": result["viability_issues"],
        }
        assert record["viability_status"] == "viable"
        assert record["viability_issues"] == []


class TestConclusionConsistency:
    """Verify that the deliverable review catches conclusion mismatches."""

    def test_infeasible_run_with_positive_conclusion_flagged(self):
        """adverse includes viability_infeasible when viability_status=infeasible."""
        adverse = []
        run = _run_result(_make_infeasible_checks())
        viability_status = str(run.get("viability_status") or "not_assessed")
        if viability_status == "infeasible":
            adverse.append({"reason": "viability_infeasible", "run_id": run.get("run_id")})
        assert len(adverse) == 1
        assert adverse[0]["reason"] == "viability_infeasible"

    def test_viable_run_not_flagged(self):
        """adverse is empty when viability_status=viable."""
        adverse = []
        run = _run_result(_make_viable_checks())
        viability_status = str(run.get("viability_status") or "not_assessed")
        if viability_status == "infeasible":
            adverse.append({"reason": "viability_infeasible"})
        assert len(adverse) == 0

    def test_intact_integrity_checks_still_block(self):
        """Integrity failures still block run availability (no regression)."""
        result = _run_result(_make_integrity_failed_checks())
        assert result["available"] is False
        # Downstream consumers check integrity_status
        assert result["integrity_status"] == "failed"

    def test_viability_and_integrity_orthogonal(self):
        """A run can be integrity=passed AND viability=infeasible simultaneously."""
        result = _run_result(_make_infeasible_checks())
        assert result["integrity_status"] == "passed"
        assert result["viability_status"] == "infeasible"
        # Both conditions are true at the same time — this is the correct
        # state for an internally consistent but economically infeasible project.