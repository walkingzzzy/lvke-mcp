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
from lvke_mcp.domains.finance.evidence_binding import bind_finance_spec_evidence
from lvke_mcp.domains.finance.generation_standard import coverage_snapshot
from lvke_mcp.domains.finance.run_service import get_workspace_finance_run


def _load_run_view(workspace_id: str, run_id: str, view: str = "summary") -> dict[str, Any]:
    """读取 run 的组装视图。

    字段分布在不同视图里：``summary`` 有 indicators / investment / funding /
    table_manifest，``checks`` 有已固化的 consistency，``full`` 才带
    input_revision。取哪个视图由调用点按需要的字段决定，不要假设 summary
    什么都有——那正是此前 consistency 与 finance_inputs 读成空的原因。
    """

    try:
        return get_workspace_finance_run(workspace_id, run_id=run_id, view=view) or {}
    except Exception:  # noqa: BLE001
        return {}


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
        technical = _error_result("technical", str(exc), exception_type=type(exc).__name__)
    dimensions["technical"] = technical
    all_blockers.extend(technical.get("blockers") or [])
    all_quality_issues.extend(technical.get("quality_issues") or [])
    all_warnings.extend(technical.get("warnings") or [])

    # ── 2. Standard: 2023 outline coverage snapshot ──────────────────────
    # finance_inputs / table_manifest 此前只认调用方显式传入，不传就全空——于是
    # 对一个字段齐全的 run 也会报 5 项要求全部 finance_paths_present=[]，读起来
    # 像"该 run 什么都没有"。run 里本就固化了这两份数据，缺省时回退到 run。
    if finance_inputs is None or table_manifest is None:
        summary_view = _load_run_view(workspace_id, run_id)
        if table_manifest is None:
            candidate_manifest = summary_view.get("table_manifest")
            table_manifest = candidate_manifest if isinstance(candidate_manifest, list) else None
        if finance_inputs is None:
            # input_revision 只在 full 视图；summary 只带 input_revision_id。
            full_view = _load_run_view(workspace_id, run_id, view="full")
            for key in ("input_revision", "finance_inputs"):
                candidate_inputs = full_view.get(key)
                if isinstance(candidate_inputs, dict) and candidate_inputs:
                    finance_inputs = candidate_inputs
                    break
    try:
        standard = _validate_standard(
            finance_inputs=finance_inputs,
            table_manifest=table_manifest,
            report_sections=report_sections,
        )
    except Exception as exc:  # noqa: BLE001
        standard = _error_result("standard", str(exc), exception_type=type(exc).__name__)
    dimensions["standard"] = standard
    all_blockers.extend(standard.get("blockers") or [])
    all_quality_issues.extend(standard.get("quality_issues") or [])
    all_warnings.extend(standard.get("warnings") or [])

    # ── 3. Evidence: spec evidence binding quality ───────────────────────
    if spec is not None:
        try:
            evidence = _validate_evidence(workspace_id, spec)
        except Exception as exc:  # noqa: BLE001
            evidence = _error_result("evidence", str(exc), exception_type=type(exc).__name__)
        dimensions["evidence"] = evidence
        all_blockers.extend(evidence.get("blockers") or [])
        all_quality_issues.extend(evidence.get("quality_issues") or [])
        all_warnings.extend(evidence.get("warnings") or [])

    # ── 4. Viability: IRR/NPV, DSCR, ICR, consistency checks ─────────────
    try:
        viability = _validate_viability(workspace_id, run_id)
    except Exception as exc:  # noqa: BLE001
        viability = _error_result("viability", str(exc), exception_type=type(exc).__name__)
    dimensions["viability"] = viability
    all_blockers.extend(viability.get("blockers") or [])
    all_quality_issues.extend(viability.get("quality_issues") or [])
    all_warnings.extend(viability.get("warnings") or [])

    # ── Aggregate ────────────────────────────────────────────────────────
    # 校验器自身异常单列 system_errors，不混进 blockers：blockers 是"这份交付有
    # 问题"，system_errors 是"这道校验没跑完"。两者混同会让调用方按业务方向排查
    # 一个代码缺陷。system_success 随之为 false，如实反映"结论不完整"。
    all_system_errors: list[str] = []
    for result in dimensions.values():
        all_system_errors.extend(result.get("system_errors") or [])
    incomplete_dimensions = sorted(
        name
        for name, result in dimensions.items()
        if result.get("conclusion_available") is False
    )
    overall_status = _compute_overall_status(dimensions, scope)
    return {
        "success": True,
        "transport_success": True,
        "system_success": not all_system_errors,
        "status": overall_status,
        "validation_scope": scope,
        "dimensions": dimensions,
        "blockers": sorted(set(all_blockers)),
        "quality_issues": sorted(set(all_quality_issues)),
        "warnings": sorted(set(all_warnings)),
        "system_errors": sorted(set(all_system_errors)),
        "incomplete_dimensions": incomplete_dimensions,
        "validation_complete": not incomplete_dimensions,
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
        # `tables_validate` 的 success=false 是**业务判定**（十三表未过正式门禁），
        # 它的 blockers 才是真结论。此前这里丢掉那些 blockers、换成
        # `technical_validation_error` + message（为空时是字面量 "unknown"），
        # 把"表校验发现 8 项问题"抹成"校验器出错"。现在原样透传。
        failed_blockers = list(result.get("blockers") or [])
        return {
            "valid": False,
            "status": "failed",
            "conclusion_available": True,
            "blockers": failed_blockers or ["finance_tables_validation_failed"],
            "quality_issues": list(result.get("quality_issues") or []) or failed_blockers,
            "warnings": list(result.get("warnings") or []),
            "details": {
                "message": str(result.get("message") or ""),
                "code": str(result.get("code") or ""),
                "technical_validation": result.get("technical_validation"),
                "formal_validation": result.get("formal_validation"),
                "technical_blockers": result.get("technical_blockers"),
                "workbook_semantic_blockers": result.get("workbook_semantic_blockers"),
                "run_consistency_ok": result.get("run_consistency_ok"),
            },
        }
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
    """Run viability assessment from run indicators and consistency checks.

    数据来源必须走 run 读取视图，不能拿持久化记录直接喂
    ``model_checks.check_consistency``：那个函数期望的是计算结果对象
    （``r["investment"]``、``r["funding"]``），而持久化 run 里是
    ``results``/``scenarios``，于是抛 ``KeyError('investment')``，被外层
    包成 ``viability_validation_error`` blocker。持久化 run 也没有
    ``indicators``（那是视图层组装的），照原样读全是 None。
    勾稽结果 run 里已固化，直接消费，不重算。
    """

    view = _load_run_view(workspace_id, run_id)
    if not view.get("available"):
        return {
            "valid": False,
            "status": "not_determinable",
            "conclusion_available": False,
            "blockers": [],
            "quality_issues": [],
            "warnings": [f"未找到可读的 FinanceRun: {run_id}"],
            "details": {
                "run_id": run_id,
                "reason": "finance_run_not_available",
                "note": "该维度未产出结论；请确认 run_id 与工作区是否一致。",
            },
        }

    indicators = dict(view.get("indicators") or {})
    # consistency 只在 checks 视图里，summary 没有。
    consistency_checks = list(
        _load_run_view(workspace_id, run_id, view="checks").get("consistency") or []
    )

    irr = indicators.get("project_irr_pct")
    npv = indicators.get("npv_wan", indicators.get("project_npv_wan"))
    payback = indicators.get(
        "static_payback_years", indicators.get("payback_years")
    )
    dscr = indicators.get("dscr_min", indicators.get("dscr"))
    icr = indicators.get("icr_min", indicators.get("icr"))

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
    if statuses <= {"passed", "ok"}:
        return "ok"
    # 校验器异常或维度未产出结论时，整体结论是"未验证完"，不能因为其他维度
    # 恰好报了 failed 就说成 partial —— partial 意味着"跑完了、有问题"。
    if {"error", "not_determinable"} & statuses:
        return "unverified"
    if "failed" in statuses:
        return "partial"
    return "unverified"


def _error_result(
    dimension: str,
    message: str,
    *,
    exception_type: str = "",
) -> dict[str, Any]:
    """校验器自身崩了的结果，与"校验发现问题"严格区分。

    此前这里把异常塞进 ``blockers``，调用方读到的是
    ``blockers: ["viability_validation_error"]`` —— 看起来像"生存能力校验没
    通过"，实际是校验器抛了 KeyError。两者处置完全不同：前者要改项目输入，
    后者要修代码。所以异常走 ``system_error``，并带上异常类型和"这不是业务
    结论"的显式声明；``status`` 保持 error，让上层能判定该维度未产出结论。
    """

    detail: dict[str, Any] = {
        "error": message or "unknown",
        "error_kind": "internal_validator_exception",
        "note": (
            f"{dimension} 维度校验器抛出异常，未产出业务结论。"
            "这不是该维度不合格，而是校验未执行完成；请按 exception_type 修复校验器。"
        ),
    }
    if exception_type:
        detail["exception_type"] = exception_type
    return {
        "valid": False,
        "status": "error",
        "conclusion_available": False,
        "blockers": [],
        "quality_issues": [],
        "system_errors": [f"{dimension}_validator_exception"],
        "warnings": [f"{dimension} 维度校验器异常，该维度未产出结论"],
        "details": detail,
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