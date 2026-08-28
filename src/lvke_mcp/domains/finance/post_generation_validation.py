"""后置校验编排器：聚合四维校验（技术/标准/证据/生存能力），
输出结构化 ValidationReport，不阻断生成。

所有校验函数在此编排而非重新实现——消费现有:
- technical:  tables_application.delivery_assessment / validate_tables
- standard:   generation_standard.coverage_snapshot
- evidence:   evidence_binding.bind_finance_spec_evidence
- viability:  checks.check_consistency + run indicators
"""

from __future__ import annotations

from typing import Any

from lvke_mcp.domains.finance import tables_application
from lvke_mcp.domains.finance._finance_model import checks as model_checks
from lvke_mcp.domains.finance.evidence_binding import bind_finance_spec_evidence
from lvke_mcp.domains.finance.generation_standard import coverage_snapshot
from lvke_mcp.domains.finance.run_store import load_run as _load_run


def validate_post_generation(
    workspace_id: str,
    run_id: str,
    *,
    spec: dict[str, Any] | None = None,
    validation_scope: str = "technical",
    finance_inputs: dict[str, Any] | None = None,
    table_manifest: list[dict[str, Any]] | None = None,
    report_sections: list[str] | None = None,
) -> dict[str, Any]:
    """Run all four post-generation validation dimensions and produce a
    structured ValidationReport.

    Never blocks generation — always returns results regardless of quality.
    Accepts optional pre-computed inputs to avoid redundant re-renders when
    the caller already has them (e.g. from a recent render/export).
    """

    scope = str(validation_scope or "technical").strip().lower()
    if scope not in {"technical", "formal"}:
        return _failure("validation_scope_invalid", "scope must be technical or formal")

    dimensions: dict[str, dict[str, Any]] = {}
    all_blockers: list[str] = []
    all_quality_issues: list[str] = []
    all_warnings: list[str] = []

    # ── 1. Technical: table structure, articulation, semantic checks ──────
    try:
        technical = _validate_technical(workspace_id, run_id, scope)
    except Exception as exc:  # noqa: BLE001
        technical = _error_result("technical", str(exc))
    dimensions["technical"] = technical
    all_blockers.extend(technical.get("blockers") or [])
    all_quality_issues.extend(technical.get("quality_issues") or [])
    all_warnings.extend(technical.get("warnings") or [])

    # ── 2. Standard: 2023 outline coverage snapshot ──────────────────────
    try:
        standard = _validate_standard(
            finance_inputs=finance_inputs,
            table_manifest=table_manifest,
            report_sections=report_sections,
        )
    except Exception as exc:  # noqa: BLE001
        standard = _error_result("standard", str(exc))
    dimensions["standard"] = standard
    all_blockers.extend(standard.get("blockers") or [])
    all_quality_issues.extend(standard.get("quality_issues") or [])
    all_warnings.extend(standard.get("warnings") or [])

    # ── 3. Evidence: spec evidence binding quality ───────────────────────
    if spec is not None:
        try:
            evidence = _validate_evidence(workspace_id, spec)
        except Exception as exc:  # noqa: BLE001
            evidence = _error_result("evidence", str(exc))
        dimensions["evidence"] = evidence
        all_blockers.extend(evidence.get("blockers") or [])
        all_quality_issues.extend(evidence.get("quality_issues") or [])
        all_warnings.extend(evidence.get("warnings") or [])

    # ── 4. Viability: IRR/NPV, DSCR, ICR, consistency checks ─────────────
    try:
        viability = _validate_viability(workspace_id, run_id)
    except Exception as exc:  # noqa: BLE001
        viability = _error_result("viability", str(exc))
    dimensions["viability"] = viability
    all_blockers.extend(viability.get("blockers") or [])
    all_quality_issues.extend(viability.get("quality_issues") or [])
    all_warnings.extend(viability.get("warnings") or [])

    # ── Aggregate ────────────────────────────────────────────────────────
    overall_status = _compute_overall_status(dimensions, scope)
    return {
        "success": True,
        "transport_success": True,
        "status": overall_status,
        "validation_scope": scope,
        "dimensions": dimensions,
        "blockers": sorted(set(all_blockers)),
        "quality_issues": sorted(set(all_quality_issues)),
        "warnings": sorted(set(all_warnings)),
        "overall_status": overall_status,
        "generated_against_standard": True,
        "validation_stage": "post_generation",
        "dimension_count": len(dimensions),
        "dimension_names": sorted(dimensions.keys()),
    }


# ── dimension helpers ────────────────────────────────────────────────────────


def _validate_technical(
    workspace_id: str,
    run_id: str,
    scope: str,
) -> dict[str, Any]:
    """Run table structure, articulation, and semantic checks."""
    result = tables_application.validate_tables(
        workspace_id,
        run_id,
        validation_scope=scope,
    )
    if not result.get("success"):
        return _error_result("technical", str(result.get("message") or "unknown"))
    blockers = list(result.get("blockers") or [])
    quality_issues = list(result.get("quality_issues") or [])
    warnings = list(result.get("warnings") or [])
    valid = not blockers
    return {
        "valid": valid,
        "status": "passed" if valid else "failed",
        "blockers": blockers,
        "quality_issues": quality_issues,
        "warnings": warnings,
        "details": {
            "technical_validation": result.get("technical_validation"),
            "formal_validation": result.get("formal_validation"),
            "technical_blockers": result.get("technical_blockers"),
            "workbook_semantic_blockers": result.get("workbook_semantic_blockers"),
            "run_consistency_ok": result.get("run_consistency_ok"),
        },
    }


def _validate_standard(
    finance_inputs: dict[str, Any] | None = None,
    table_manifest: list[dict[str, Any]] | None = None,
    report_sections: list[str] | None = None,
) -> dict[str, Any]:
    """Run 2023 outline coverage snapshot."""
    snapshot = coverage_snapshot(
        finance_inputs=finance_inputs,
        table_manifest=table_manifest,
        report_sections=report_sections,
    )
    status = str(snapshot.get("status") or "unverified")
    requirements = list(snapshot.get("requirements") or [])
    missing = [
        req
        for req in requirements
        if req.get("status") in ("partial", "unverified")
    ]
    blockers = [
        f"standard_coverage_missing:{req['requirement_id']}"
        for req in missing
    ] if snapshot.get("validation_stage") == "post_generation" else []
    return {
        "valid": status == "conformant",
        "status": status,
        "blockers": blockers,
        "quality_issues": blockers,
        "warnings": [],
        "details": {
            "standard_id": snapshot.get("standard_id"),
            "standard_version": snapshot.get("standard_version"),
            "source_hash": snapshot.get("source_hash"),
            "mapping_hash": snapshot.get("mapping_hash"),
            "profile": snapshot.get("profile"),
            "project_type": snapshot.get("project_type"),
            "generated_against_standard": snapshot.get("generated_against_standard"),
            "requirements": requirements,
            "requirement_count": len(requirements),
            "missing_count": len(missing),
        },
    }


def _validate_evidence(
    workspace_id: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Run spec evidence binding quality check."""
    binding = bind_finance_spec_evidence(workspace_id, spec)
    bindings = list(binding.get("bindings") or [])
    missing = list(binding.get("missing") or [])
    pending = list(binding.get("pending") or [])
    invalid = list(binding.get("invalid") or [])
    ok = bool(binding.get("ok"))
    formal_ok = bool(binding.get("formal_ok"))
    blockers = [
        *(f"evidence_missing:{item.get('source_path', '?')}" for item in missing),
        *(f"evidence_invalid:{item.get('source_path', '?')}" for item in invalid),
    ]
    return {
        "valid": ok and formal_ok,
        "status": "passed" if ok and formal_ok else (
            "partial" if ok else "failed"
        ),
        "blockers": blockers,
        "quality_issues": [
            *blockers,
            *(f"evidence_pending:{item.get('source_path', '?')}" for item in pending),
        ],
        "warnings": [],
        "details": {
            "binding_hash": binding.get("binding_hash"),
            "ok": ok,
            "formal_ok": formal_ok,
            "binding_count": len(bindings),
            "missing_count": len(missing),
            "pending_count": len(pending),
            "invalid_count": len(invalid),
            "bindings": bindings,
            "missing": missing,
            "pending": pending,
            "invalid": invalid,
        },
    }


def _validate_viability(
    workspace_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Run viability assessment from run indicators and consistency checks."""
    run = _load_run(workspace_id, run_id)
    if not run:
        return _error_result("viability", "run not found")

    indicators = dict(run.get("indicators") or {})
    annual = dict(run.get("annual") or {})
    consistency_checks = model_checks.check_consistency(run)

    irr = indicators.get("project_irr_pct")
    npv = indicators.get("project_npv_wan")
    payback = indicators.get("payback_years")
    dscr = indicators.get("dscr")
    icr = indicators.get("icr")

    blockers: list[str] = []
    quality_issues: list[str] = []
    warnings: list[str] = []

    # Economic viability — informational only, never blocks
    if irr is not None:
        quality_issues.append(
            f"project_irr:{irr}"
        )
    if npv is not None:
        quality_issues.append(
            f"project_npv_wan:{npv}"
        )
    # Consistency check failures become quality issues
    for check in consistency_checks:
        if not check.get("ok"):
            quality_issues.append(
                f"consistency:{check.get('rule', '?')}"
            )
    # Debt service
    if dscr is not None and dscr < 1.0:
        warnings.append(f"dscr_below_1:{dscr}")
    if icr is not None and icr < 1.0:
        warnings.append(f"icr_below_1:{icr}")

    return {
        "valid": True,  # viability never "fails" — it's an assessment
        "status": "ok",
        "blockers": blockers,
        "quality_issues": quality_issues,
        "warnings": warnings,
        "details": {
            "project_irr_pct": irr,
            "project_npv_wan": npv,
            "payback_years": payback,
            "dscr": dscr,
            "icr": icr,
            "consistency_checks": consistency_checks,
            "consistency_check_count": len(consistency_checks),
            "consistency_failures": [
                c for c in consistency_checks if not c.get("ok")
            ],
        },
    }


# ── helpers ──────────────────────────────────────────────────────────────────


def _compute_overall_status(
    dimensions: dict[str, dict[str, Any]],
    scope: str,
) -> str:
    """Compute overall status from all dimension results.

    - 'ok': all dimensions valid
    - 'partial': at least one dimension has issues but all ran
    - 'unverified': no dimensions ran
    """
    if not dimensions:
        return "unverified"
    statuses = {d.get("status") for d in dimensions.values()}
    if statuses == {"passed"} or statuses == {"ok"} or statuses == {"passed", "ok"}:
        return "ok"
    if "failed" in statuses:
        return "partial"
    return "unverified"


def _error_result(
    dimension: str,
    message: str,
) -> dict[str, Any]:
    return {
        "valid": False,
        "status": "error",
        "blockers": [f"{dimension}_validation_error"],
        "quality_issues": [f"{dimension}_validation_error"],
        "warnings": [],
        "details": {"error": message},
    }


def _failure(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "transport_success": False,
        "status": "blocked",
        "code": code,
        "message": message,
        "blockers": [code],
        "quality_issues": [code],
        "warnings": [],
        "dimensions": {},
        "overall_status": "blocked",
    }