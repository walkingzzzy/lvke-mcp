"""Release preflight: split calculation / artifact / evidence / release gates.

Artifact failures must not mask calculation results, and calculation success
must not imply release readiness.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


GateStatus = str  # "pass" | "fail" | "blocked" | "skipped"


@dataclass
class GateResult:
    name: str
    status: GateStatus
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "pass"


@dataclass
class ReleasePreflightReport:
    calculation_gate: GateResult
    artifact_gate: GateResult
    evidence_gate: GateResult
    release_gate: GateResult
    formal_evidence: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def _gate(g: GateResult) -> dict[str, Any]:
            return {
                "status": g.status,
                "passed": g.passed,
                "failed": g.failed,
                "blockers": g.blockers,
            }

        release_ok = all(
            gate.status == "pass"
            for gate in (
                self.calculation_gate,
                self.artifact_gate,
                self.evidence_gate,
                self.release_gate,
            )
        )
        return {
            "calculation_gate": _gate(self.calculation_gate),
            "artifact_gate": _gate(self.artifact_gate),
            "evidence_gate": _gate(self.evidence_gate),
            "release_gate": _gate(self.release_gate),
            "release_ready": release_ok,
            "gates": [
                {"name": gate.name, **_gate(gate)}
                for gate in (
                    self.calculation_gate,
                    self.artifact_gate,
                    self.evidence_gate,
                    self.release_gate,
                )
            ],
            "formal_evidence": self.formal_evidence,
            "notes": self.notes,
        }


def _status_from_checks(passed: list[str], failed: list[str]) -> GateStatus:
    if failed:
        return "fail"
    if passed:
        return "pass"
    return "skipped"


def evaluate_calculation_gate(
    checks: Callable[[], tuple[list[str], list[str]]],
) -> GateResult:
    passed, failed = checks()
    return GateResult(
        name="calculation_gate",
        status=_status_from_checks(passed, failed),
        passed=passed,
        failed=failed,
    )


def evaluate_artifact_gate(
    *,
    required_paths: list[Path],
    zip_path: Path | None = None,
    word_manifest: Path | None = None,
    word_root: Path | None = None,
    require_checks: bool = False,
) -> GateResult:
    passed: list[str] = []
    failed: list[str] = []
    blockers: list[str] = []

    for path in required_paths:
        label = str(path)
        if path.is_file():
            passed.append(f"artifact exists: {label}")
        else:
            failed.append(f"artifact missing: {label}")

    if word_manifest is not None:
        if word_manifest.is_file():
            passed.append("Word conversion manifest readable")
            try:
                manifest = json.loads(word_manifest.read_text(encoding="utf-8"))
                records = manifest.get("records", [])
                if word_root is not None:
                    missing_docx = [
                        item.get("docx", "")
                        for item in records
                        if not (word_root / str(item.get("docx", ""))).exists()
                    ]
                    if missing_docx:
                        failed.append(
                            f"Word conversion DOCX missing ({len(missing_docx)} files)"
                        )
                    else:
                        passed.append("Word conversion DOCX paths exist")
            except (OSError, json.JSONDecodeError) as exc:
                failed.append(f"Word conversion manifest invalid: {exc}")
        else:
            failed.append("Word conversion manifest missing")

    if zip_path is not None:
        if not zip_path.is_file():
            failed.append("internal release ZIP missing")
        else:
            passed.append("internal release ZIP exists")
            try:
                with zipfile.ZipFile(zip_path) as archive:
                    names = archive.namelist()
                    passed.append(f"ZIP readable ({len(names)} entries)")
            except (OSError, zipfile.BadZipFile) as exc:
                failed.append(f"ZIP unreadable: {exc}")

    if failed:
        blockers.append("artifact_gate_incomplete")
    if require_checks and not passed and not failed:
        failed.append("formal artifact checks not configured")
        blockers.append("formal_artifacts_not_configured")
    status = _status_from_checks(passed, failed)
    if status == "skipped" and not required_paths and zip_path is None and word_manifest is None and not require_checks:
        status = "pass"
        passed.append("no artifact checks configured")
    return GateResult(
        name="artifact_gate",
        status=_status_from_checks(passed, failed),
        passed=passed,
        failed=failed,
        blockers=blockers,
    )


def evaluate_evidence_gate(
    *,
    evd_distribution: dict[str, int] | None = None,
    required_evd2_count: int = 24,
    sim_a_present: bool = False,
    hash_only_sources: list[str] | None = None,
) -> GateResult:
    passed: list[str] = []
    failed: list[str] = []
    blockers: list[str] = []

    if sim_a_present:
        failed.append("SIM-A materials present; formal candidate requires EVD-2 only")
        blockers.append("sim_a_not_formal")

    if hash_only_sources:
        failed.append(
            f"hash-only sources blocked ({len(hash_only_sources)}): "
            + ", ".join(hash_only_sources[:5])
        )
        blockers.append("hash_only_evidence")

    if evd_distribution is not None:
        evd2 = int(evd_distribution.get("EVD-2", 0))
        evd0 = int(evd_distribution.get("EVD-0", 0))
        evd1 = int(evd_distribution.get("EVD-1", 0))
        passed.append(f"EVD distribution recorded: 0={evd0} 1={evd1} 2={evd2}")
        if evd0 or evd1:
            failed.append("non-EVD-2 P0 items remain")
            blockers.append("formal_evidence_incomplete")
        if evd2 < required_evd2_count:
            failed.append(
                f"EVD-2 count {evd2} < required {required_evd2_count}"
            )
            blockers.append("p0_not_fully_formalized")

    status = _status_from_checks(passed, failed)
    if status == "skipped" and required_evd2_count:
        status = "blocked"
        blockers.append("evidence_distribution_missing")
    return GateResult(
        name="evidence_gate",
        status=status,
        passed=passed,
        failed=failed,
        blockers=blockers,
    )


def evaluate_release_gate(
    *,
    calculation: GateResult,
    artifact: GateResult,
    evidence: GateResult,
    build_metadata_complete: bool,
    metadata_matches_commit: bool = True,
) -> GateResult:
    passed: list[str] = []
    failed: list[str] = []
    blockers: list[str] = []

    if build_metadata_complete:
        passed.append("build_metadata_complete")
    else:
        failed.append("build_metadata_incomplete")
        blockers.append("build_metadata_incomplete")

    if metadata_matches_commit:
        passed.append("build_metadata_matches_commit")
    else:
        failed.append("stale_build_metadata")
        blockers.append("stale_build_metadata")

    for gate in (calculation, artifact, evidence):
        if gate.status == "pass":
            passed.append(f"{gate.name} pass")
        elif gate.status == "skipped":
            if gate.name == "artifact_gate" and not gate.failed:
                passed.append(f"{gate.name} skipped (no checks)")
            else:
                failed.append(f"{gate.name} skipped")
                blockers.append(f"{gate.name}_skipped")
        else:
            failed.append(f"{gate.name} fail")
            blockers.extend(gate.blockers or [f"{gate.name}_fail"])

    return GateResult(
        name="release_gate",
        status=_status_from_checks(passed, failed),
        passed=passed,
        failed=failed,
        blockers=blockers,
    )


def run_release_preflight(
    *,
    calculation_checks: Callable[[], tuple[list[str], list[str]]],
    required_artifacts: list[Path] | None = None,
    zip_path: Path | None = None,
    word_manifest: Path | None = None,
    word_root: Path | None = None,
    evd_distribution: dict[str, int] | None = None,
    sim_a_present: bool = False,
    hash_only_sources: list[str] | None = None,
    build_metadata_complete: bool = False,
    metadata_matches_commit: bool = True,
    formal_evidence: str = "",
    require_artifact_checks: bool = False,
) -> ReleasePreflightReport:
    calculation = evaluate_calculation_gate(calculation_checks)
    artifact = evaluate_artifact_gate(
        required_paths=list(required_artifacts or []),
        zip_path=zip_path,
        word_manifest=word_manifest,
        word_root=word_root,
        require_checks=require_artifact_checks,
    )
    evidence = evaluate_evidence_gate(
        evd_distribution=evd_distribution,
        sim_a_present=sim_a_present,
        hash_only_sources=hash_only_sources,
    )
    release = evaluate_release_gate(
        calculation=calculation,
        artifact=artifact,
        evidence=evidence,
        build_metadata_complete=build_metadata_complete,
        metadata_matches_commit=metadata_matches_commit,
    )
    return ReleasePreflightReport(
        calculation_gate=calculation,
        artifact_gate=artifact,
        evidence_gate=evidence,
        release_gate=release,
        formal_evidence=formal_evidence,
    )
