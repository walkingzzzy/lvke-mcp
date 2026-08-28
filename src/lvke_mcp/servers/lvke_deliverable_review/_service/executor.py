"""规则执行器与审查运行：保持 finding ID、顺序、severity 与 blocker 聚合不变。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from lvke_mcp.runtime.storage import sha256_json, utc_now
from lvke_mcp.servers.lvke_deliverable_review import financial_checks, report_checks, rules
from lvke_mcp.servers.lvke_deliverable_review.contracts import SEVERITY_ORDER, verdict_for
from lvke_mcp.servers.lvke_deliverable_review.store import STORE

from .finding_rules import (
    _acquisition_input_findings,
    _existing_issue_findings,
    _finance_recalculation_findings,
    _hotel_acquisition_run_findings,
    _professional_rule_finding,
    _project_metadata_findings,
    _report_content,
    _report_findings,
    _required_finding_rows,
    _summarize_track_coverage,
)

from .preparation import (
    _component_preparation,
    _preparation_basis,
    _run_from_preparation,
    _standard_basis,
)


def _execute_rules(
    workspace_id: str,
    preparation_payload: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    target = preparation_payload.get("target") or {}
    target_type = str(target.get("target_type") or "")
    pack = preparation_payload.get("rule_pack") or {}
    standard_basis = _standard_basis(preparation_payload)
    findings: list[dict[str, Any]] = _required_finding_rows(preparation_payload, standard_basis)
    incomplete = [f"standard_package_incomplete:{item}" for item in ((preparation_payload.get("standards") or {}).get("incomplete") or [])]
    metrics: dict[str, Any] = {}
    executed_rules: set[str] = {"CORE.TARGET.RESOLVED", "CORE.UPSTREAM.COMPLETE", "CORE.STANDARDS.LOCKED"}
    executed_rules.update(str(row.get("rule_id") or "") for row in findings)
    manual_routed_rules: list[str] = []
    applicable_rules = set(pack.get("applicable_rules") or [])
    reviewed_finance_runs: set[str] = set()
    professional_target_types = {target_type}
    if target_type == "combined_deliverable":
        professional_target_types.update(
            str(component.get("target_type") or "")
            for component in (
                (preparation_payload.get("target_snapshot") or {}).get("components")
                or []
            )
        )
    evidence_track = str(
        (preparation_payload.get("project_context") or {}).get("evidence_track") or "real"
    )
    for source_rule in pack.get("rule_sources") or []:
        if source_rule.get("check_kind") != "professional":
            continue
        if not professional_target_types.intersection(
            set(source_rule.get("target_kinds") or [])
        ):
            continue
        rule_id = str(source_rule.get("rule_id") or "")
        executed_rules.add(rule_id)
        manual_routed_rules.append(rule_id)
        # 拟定正式轨已由确定性勾稽/九章检查覆盖；不再生成无法豁免、
        # 复测必复现的「待专业核验」pending。原件轨仍要求人工专业核验。
        if evidence_track == "sim_a_formal":
            continue
        findings.append(_professional_rule_finding(
            preparation_payload,
            source_rule,
            standard_basis,
        ))

    def review_one(child: dict[str, Any]) -> None:
        nonlocal findings, incomplete
        child_type = str((child.get("target") or {}).get("target_type") or "")
        run = _run_from_preparation(
            workspace_id,
            child,
        )
        run_key = str(run.get("run_id") or run.get("id") or "") if run else ""
        review_finance_run = bool(run) and (
            not run_key or run_key not in reviewed_finance_runs
        )
        finance_targets = {
            "finance_run", "finance_tables_package", "acquisition_run",
            "acquisition_tables_package",
        }
        if child_type in finance_targets and review_finance_run:
            findings.extend(_existing_issue_findings(run, standard_basis))
            if run.get("available"):
                executed_rules.add("FIN.EXISTING.CHECKS")
            is_acquisition = child_type.startswith("acquisition_")
            if not is_acquisition:
                recalculated, missing, finance_metrics = _finance_recalculation_findings(
                    run, standard_basis,
                )
                findings.extend(recalculated)
                incomplete.extend(missing)
                metrics.setdefault("finance", []).append(finance_metrics)
                recalculation_rules = set(finance_metrics.get("finance_recalculations") or [])
                if "total_investment" in recalculation_rules:
                    executed_rules.add("FIN.INVESTMENT.BALANCE")
                if "funding_balance" in recalculation_rules:
                    executed_rules.add("FIN.FUNDING.BALANCE")
        if review_finance_run and (
            child_type in finance_targets
            or child_type == "report_revision"
        ):
            deterministic_rows, deterministic_missing, deterministic_executed, deterministic_metrics = (
                financial_checks.review_finance_run(
                    run,
                    target_id=str((child.get("target") or {}).get("target_id") or ""),
                    target_type=child_type,
                    applicable_rules=applicable_rules,
                    source_rule_rows=pack.get("rule_sources") or [],
                    standard_basis=standard_basis,
                )
            )
            findings.extend(deterministic_rows)
            incomplete.extend(deterministic_missing)
            executed_rules.update(deterministic_executed)
            metrics.setdefault("deterministic_finance", []).append(deterministic_metrics)
        if review_finance_run and run_key:
            reviewed_finance_runs.add(run_key)
        if "PROJECT.METADATA.COMPLETE" in applicable_rules:
            findings.extend(_project_metadata_findings(child, run, standard_basis))
            executed_rules.add("PROJECT.METADATA.COMPLETE")
        if child_type in {"acquisition_run", "acquisition_tables_package"} and "ACQ.TRANSACTION.INPUTS" in applicable_rules:
            findings.extend(_acquisition_input_findings(
                run, str((child.get("target") or {}).get("target_id") or ""), standard_basis,
            ))
            executed_rules.add("ACQ.TRANSACTION.INPUTS")
        if child_type in {"acquisition_run", "acquisition_tables_package"} and {
            "HOTEL.RIGHTS.LICENSES", "HOTEL.OPERATING_MODEL",
        }.intersection(applicable_rules):
            hotel_rows, hotel_executed = _hotel_acquisition_run_findings(
                run,
                str((child.get("target") or {}).get("target_id") or ""),
                standard_basis,
            )
            findings.extend(hotel_rows)
            executed_rules.update(hotel_executed)
        if child_type in {"finance_xlsx", "finance_xlsx_source"}:
            path = Path(str((child.get("target_snapshot") or {}).get("path") or ""))
            scanned, missing, xlsx_metrics = rules.scan_xlsx(path, deep=mode == "deep")
            for item in scanned:
                item["standard_basis"] = item.get("standard_basis") or standard_basis
                item["coverage_rule_id"] = "FIN.XLSX.INTEGRITY"
            findings.extend(scanned)
            incomplete.extend(missing)
            executed_rules.add("FIN.XLSX.INTEGRITY")
            if mode == "deep":
                recalculated, recalc_missing, recalc_metrics = rules.recalculate_xlsx(path)
                for item in recalculated:
                    item["standard_basis"] = item.get("standard_basis") or standard_basis
                    item["coverage_rule_id"] = "FIN.XLSX.RECALC"
                findings.extend(recalculated)
                incomplete.extend(recalc_missing)
                xlsx_metrics["recalculation"] = recalc_metrics
                if recalc_metrics.get("available") and not recalc_missing:
                    executed_rules.add("FIN.XLSX.RECALC")
            else:
                incomplete.append("deep_recalculation_not_executed")
            metrics.setdefault("xlsx", []).append(xlsx_metrics)
        if child_type in {"report_revision", "report_artifact"}:
            report_rows, missing, report_metrics = _report_findings(
                workspace_id, child, run, standard_basis,
            )
            findings.extend(report_rows)
            incomplete.extend(missing)
            metrics.setdefault("report", []).append(report_metrics)
            executed_rules.update(report_metrics.get("executed_rules") or [])
            executed_rules.update({"REPORT.PLACEHOLDER", "REPORT.DUPLICATE.PARAGRAPH"})
            if child_type == "report_revision":
                executed_rules.add("REPORT.EXISTING.VALIDATION")

    if target_type == "combined_deliverable":
        components = (preparation_payload.get("target_snapshot") or {}).get("components") or []
        finance_present = False
        report_present = False
        combined_reports: list[dict[str, Any]] = []
        combined_runs: list[dict[str, Any]] = []
        for component in components:
            component_type = str(component.get("target_type") or "")
            finance_present = finance_present or component_type in {
                "finance_run", "finance_tables_package", "finance_xlsx", "finance_xlsx_source",
                "acquisition_run", "acquisition_tables_package",
            }
            report_present = report_present or component_type in {"report_revision", "report_artifact"}
            child = _component_preparation(preparation_payload, component)
            if component_type in {"report_revision", "report_artifact"}:
                content = _report_content(workspace_id, child)
                if content:
                    combined_reports.append({
                        "target_id": (child.get("target") or {}).get("target_id"),
                        "content": content,
                    })
            elif component_type in {
                "finance_run", "finance_tables_package", "acquisition_run",
                "acquisition_tables_package",
            }:
                run = _run_from_preparation(
                    workspace_id,
                    child,
                )
                run_id = str(run.get("run_id") or run.get("id") or "") if run else ""
                if run and not any(
                    str(item.get("run_id") or item.get("id") or "") == run_id
                    for item in combined_runs
                ):
                    combined_runs.append(run)
            review_one(child)
        if not finance_present:
            incomplete.append("combined_finance_component_missing")
        if not report_present:
            incomplete.append("combined_report_component_missing")
        combined_rows, combined_missing, combined_metrics, combined_executed = (
            report_checks.review_combined(
                report_contents=combined_reports,
                finance_runs=combined_runs,
                target_id=str(target.get("target_id") or ""),
                standard_basis=standard_basis,
            )
        )
        findings.extend(combined_rows)
        incomplete.extend(combined_missing)
        metrics["combined"] = combined_metrics
        executed_rules.update(combined_executed)
        executed_rules.update({"COMBINED.BINDINGS.COMPLETE", "COMBINED.UPSTREAM.VERDICTS"})
    else:
        review_one(preparation_payload)

    unique: dict[str, dict[str, Any]] = {}
    for item in findings:
        item["rule_pack_id"] = pack.get("rule_pack_id")
        item["rule_pack_version"] = pack.get("version")
        unique[str(item["finding_id"])] = item
    findings = sorted(unique.values(), key=lambda row: (SEVERITY_ORDER.get(str(row.get("severity")), 9), str(row.get("finding_id"))))
    incomplete = sorted(set(incomplete))
    applicable = list(pack.get("applicable_rules") or [])
    if target_type == "combined_deliverable":
        component_types = {
            str(component.get("target_type") or "")
            for component in ((preparation_payload.get("target_snapshot") or {}).get("components") or [])
        }
        if not component_types.intersection({"finance_xlsx", "finance_xlsx_source"}):
            applicable = [rule_id for rule_id in applicable if not rule_id.startswith("FIN.XLSX.")]
        if not component_types.intersection({
            "finance_run", "finance_tables_package", "acquisition_run", "acquisition_tables_package",
        }):
            applicable = [
                rule_id for rule_id in applicable
                if rule_id not in {"FIN.EXISTING.CHECKS", "FIN.INVESTMENT.BALANCE", "FIN.FUNDING.BALANCE"}
            ]
        if "report_revision" not in component_types:
            applicable = [rule_id for rule_id in applicable if rule_id != "REPORT.EXISTING.VALIDATION"]
        source_targets = {
            str(row.get("rule_id") or ""): set(row.get("target_kinds") or [])
            for row in (pack.get("rule_sources") or [])
        }
        applicable = [
            rule_id for rule_id in applicable
            if rule_id not in source_targets
            or bool(source_targets[rule_id].intersection(component_types | {"combined_deliverable"}))
        ]
    coverage = {
        "applicable_rule_count": len(applicable), "executed_rule_count": len(set(applicable) & executed_rules),
        "coverage_ratio": round(len(set(applicable) & executed_rules) / len(applicable), 6) if applicable else 1.0,
        "applicable_rules": applicable, "executed_rules": sorted(executed_rules),
        "not_executed_rules": sorted(set(applicable) - executed_rules), "metrics": metrics,
        "manual_routed_rules": sorted(set(manual_routed_rules)),
        "deterministic_rule_count": sum(
            1 for row in (pack.get("rule_sources") or []) if row.get("check_kind") == "deterministic"
        ),
        "professional_rule_count": len(set(manual_routed_rules)),
        "rule_source_evidence": {
            str(row.get("rule_id") or ""): deepcopy(row.get("standard") or {})
            for row in (pack.get("rule_sources") or [])
        },
    }
    if coverage["not_executed_rules"]:
        incomplete.extend(f"rule_not_executed:{rule_id}" for rule_id in coverage["not_executed_rules"])
        incomplete = sorted(set(incomplete))
    coverage.update(_summarize_track_coverage(
        metrics,
        incomplete,
        findings,
        evidence_track=str(
            (preparation_payload.get("project_context") or {}).get("evidence_track")
            or "real"
        ),
    ))
    return {
        "findings": findings, "incomplete_reasons": incomplete, "coverage": coverage,
        "overall_verdict": verdict_for(findings, incomplete),
    }


def _preparation_execution_integrity_reasons(
    events: list[dict[str, Any]],
    preparation_payload: dict[str, Any],
) -> list[str]:
    if not events or events[0].get("event_type") != "review_created":
        return ["review_created_event_missing"]
    created = events[0].get("payload") or {}
    if not isinstance(created, dict):
        return ["review_created_payload_invalid"]
    reasons: list[str] = []
    expected_basis_hash = str(created.get("preparation_basis_hash") or "")
    expected_content_hash = str(
        created.get("preparation_content_hash") or ""
    )
    if not expected_basis_hash:
        reasons.append("preparation_basis_binding_missing")
    elif sha256_json(
        _preparation_basis(preparation_payload)
    ) != expected_basis_hash:
        reasons.append("preparation_basis_binding_mismatch")
    if (
        expected_content_hash
        and sha256_json(preparation_payload) != expected_content_hash
    ):
        reasons.append("preparation_content_binding_mismatch")
    for field in (
        "target",
        "target_spec",
        "bindings",
        "upstream_snapshot",
        "rule_pack",
        "standards",
        "legacy_gate_snapshot",
        "engine_version",
        "recalculation_environment_version",
    ):
        if deepcopy(created.get(field)) != deepcopy(
            preparation_payload.get(field)
        ):
            reasons.append(f"preparation_review_binding_mismatch:{field}")
    return sorted(set(reasons))


def _run_review(
    workspace_id: str,
    review_id: str,
    preparation_payload: dict[str, Any] | None,
    mode: str,
    preparation_integrity_reasons: list[str] | None = None,
) -> None:
    with STORE.mutation_guard(workspace_id, "review_engine_execute", review_id):
        events = STORE.events(workspace_id, review_id)
        if any(
            event.get("event_type") in {"review_completed", "review_failed"}
            for event in events
        ):
            return
        chain_ok, chain_reasons = STORE.verify_event_chain(
            workspace_id,
            review_id,
        )
        integrity_reasons = list(preparation_integrity_reasons or [])
        if not chain_ok:
            integrity_reasons.extend(chain_reasons or ["review_event_chain_invalid"])
        if isinstance(preparation_payload, dict):
            integrity_reasons.extend(
                _preparation_execution_integrity_reasons(
                    events,
                    preparation_payload,
                )
            )
        running_events = [
            event for event in events
            if event.get("event_type") == "review_running"
        ]
        resumed = bool(running_events)
        first_running_payload = (
            (running_events[0].get("payload") or {}) if running_events else {}
        )
        review_as_of = str(
            first_running_payload.get("review_as_of")
            or first_running_payload.get("started_at")
            or utc_now()
        )
        STORE.append(
            workspace_id,
            review_id,
            "review_running",
            {
                "started_at": utc_now(),
                "review_as_of": review_as_of,
                "resumed": resumed,
            },
        )
        if preparation_payload is None or integrity_reasons:
            incomplete_reason = (
                "review_preparation_integrity_failed"
                if integrity_reasons
                else "review_preparation_unavailable"
            )
            STORE.append(
                workspace_id,
                review_id,
                "review_failed",
                {
                    "completed_at": utc_now(),
                    "incomplete_reason": incomplete_reason,
                    "integrity_reasons": sorted(set(integrity_reasons)),
                },
            )
            return
        try:
            execution_payload = {
                **preparation_payload,
                "review_as_of": review_as_of,
            }
            result = _execute_rules(
                workspace_id,
                execution_payload,
                mode,
            )
        except Exception:  # noqa: BLE001 - fail closed and avoid leaking exception text
            STORE.append(
                workspace_id, review_id, "review_failed",
                {"completed_at": utc_now(), "incomplete_reason": "review_engine_failed"},
            )
        else:
            # Keep terminal persistence outside the engine exception handler.
            # If the event is durably written and the caller then crashes, a
            # retry observes the terminal event instead of appending a false
            # review_failed event after review_completed.
            STORE.append(
                workspace_id, review_id, "review_completed",
                {**result, "completed_at": utc_now()},
            )
