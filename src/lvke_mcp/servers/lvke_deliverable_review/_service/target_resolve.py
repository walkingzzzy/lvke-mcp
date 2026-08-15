"""跨域目标解析与上游绑定投影：工件文件、报告修订、收购 run 与绑定清单。"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.servers.lvke_deliverable_review.contracts import normalize_target

from .base import (
    _REPORT_ARTIFACT_DOMAINS,
    _parse_timestamp,
    _safe_file,
)


def _immutable_artifact_files(raw_files: Any) -> list[dict[str, Any]]:
    """Keep only immutable manifest fields used to bind a reviewed artifact."""

    files: list[dict[str, Any]] = []
    for raw in raw_files or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("filename") or "")
        files.append({
            "name": name,
            "role": str(raw.get("role") or ""),
            "media_type": str(raw.get("media_type") or ""),
            "size_bytes": raw.get("size_bytes"),
            "sha256": str(raw.get("sha256") or ""),
        })
    return sorted(files, key=lambda row: (row["name"], row["role"], row["sha256"]))


def _generic_artifact_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_family": "generic",
        "artifact_id": str(record.get("artifact_id") or ""),
        "kind": str(record.get("kind") or ""),
        "template_version": str(record.get("template_version") or ""),
        "basis_fingerprint": str(record.get("basis_fingerprint") or ""),
        "report_revision_id": str(record.get("report_revision_id") or ""),
        "document_revision_id": str(record.get("document_revision_id") or ""),
        "finance_run_id": str(record.get("finance_run_id") or record.get("run_id") or ""),
        "spec_hash": str(record.get("spec_hash") or ""),
        "manifest_hash": str(record.get("manifest_hash") or ""),
        "index_hash": str(record.get("index_hash") or ""),
        "files": _immutable_artifact_files(record.get("files")),
    }


def _acquisition_artifact_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_family": "asset_acquisition",
        "artifact_id": str(record.get("artifact_id") or ""),
        "type": str(record.get("type") or "asset_acquisition"),
        "run_id": str(record.get("run_id") or ""),
        "spec_hash": str(record.get("spec_hash") or ""),
        "fact_revision": str(record.get("fact_revision") or ""),
        "spec_snapshot_hash": str(record.get("spec_snapshot_hash") or ""),
        "evidence_binding_version": str(record.get("evidence_binding_version") or ""),
        "evidence_binding_hash": str(record.get("evidence_binding_hash") or ""),
        "template_version": str(record.get("template_version") or ""),
        "report_data_hash": str(record.get("report_data_hash") or ""),
        "numeric_consistency": str(record.get("numeric_consistency") or ""),
        "files": _immutable_artifact_files(record.get("files")),
    }


def _string_ids(*values: Any) -> list[str]:
    return sorted({
        str(item).strip()
        for value in values
        for item in (value if isinstance(value, (list, tuple, set)) else [])
        if str(item).strip()
    })


def _acquisition_scenario_matrix_ids(workspace_id: str, run_id: str) -> list[str]:
    if not run_id.startswith("acqrun_"):
        return []
    try:
        from lvke_mcp.domains.asset_acquisition.backend import list_scenario_matrices

        return [
            str(item.get("matrix_id") or "")
            for item in list_scenario_matrices(workspace_id, run_id)
            if item.get("matrix_id")
        ]
    except (OSError, ValueError):
        return []


def _artifact_upstream_bindings(record: dict[str, Any]) -> dict[str, Any]:
    containers = [
        record,
        record.get("bindings") if isinstance(record.get("bindings"), dict) else {},
        record.get("upstream") if isinstance(record.get("upstream"), dict) else {},
        record.get("basis") if isinstance(record.get("basis"), dict) else {},
    ]
    evidence_ids = _string_ids(
        *(container.get("evidence_pack_ids") for container in containers)
    )
    research_ids = _string_ids(
        *(container.get("research_package_ids") for container in containers),
        *(container.get("research_pack_ids") for container in containers),
    )
    bindings: dict[str, Any] = {
        "evidence_pack_ids": evidence_ids,
        "research_package_ids": research_ids,
    }
    for key in ("finance_run_id", "finance_tables_package_id", "report_revision_id"):
        value = next(
            (
                str(container.get(key) or "").strip()
                for container in containers
                if str(container.get(key) or "").strip()
            ),
            "",
        )
        if value:
            bindings[key] = value
    return bindings


def _linked_generic_report_revision(
    workspace_id: str,
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    native_revision_id = str(artifact.get("document_revision_id") or "")
    if not native_revision_id:
        return {}, {}, ["report_artifact_revision_missing"]
    try:
        from lvke_mcp.adapters.report_repository import REVISION_STORE

        exact_revision_id = str(artifact.get("report_revision_id") or "")
        if exact_revision_id:
            exact = REVISION_STORE.get(workspace_id, exact_revision_id)
            if exact is None:
                return {}, {}, ["report_artifact_revision_not_found"]
            exact_native = str((exact.get("payload") or {}).get("native_revision_id") or "")
            if exact_native != native_revision_id:
                return {}, {}, ["report_artifact_revision_mismatch"]
            records = [exact]
        else:
            records = REVISION_STORE.list(workspace_id)
    except (OSError, ValueError):
        return {}, {}, ["report_artifact_revision_unavailable"]
    artifact_created_at = _parse_timestamp(artifact.get("created_at"))
    artifact_run_id = str(
        artifact.get("finance_run_id") or artifact.get("run_id") or ""
    )
    candidates: list[dict[str, Any]] = []
    for record in records:
        payload = record.get("payload") or {}
        upstream = payload.get("upstream") or {}
        if str(payload.get("native_revision_id") or "") != native_revision_id:
            continue
        upstream_run_id = str(upstream.get("run_id") or "")
        if artifact_run_id and upstream_run_id and upstream_run_id != artifact_run_id:
            continue
        created_at = _parse_timestamp(record.get("created_at"))
        if artifact_created_at and created_at and created_at > artifact_created_at:
            continue
        candidates.append(record)
    if not candidates:
        return {}, {}, ["report_artifact_revision_not_found"]
    if not str(artifact.get("report_revision_id") or "") and len(candidates) > 1:
        return {}, {}, ["report_artifact_revision_ambiguous"]
    candidates.sort(
        key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("object_id") or ""),
        ),
        reverse=True,
    )
    revision = candidates[0]
    payload = revision.get("payload") or {}
    upstream = payload.get("upstream") or {}
    bindings = _artifact_upstream_bindings({
        "report_revision_id": revision.get("object_id"),
        "upstream": upstream,
    })
    snapshot = {
        "report_revision_id": str(revision.get("object_id") or ""),
        "native_revision_id": native_revision_id,
        "content_hash": str(revision.get("content_hash") or ""),
        "basis_hash": str(revision.get("basis_hash") or ""),
    }
    return snapshot, bindings, []


def _resolve_report_artifact(
    workspace_id: str,
    artifact_id: str,
    *,
    artifact_domain: str,
) -> tuple[dict[str, Any] | None, dict[str, Any], list[str]]:
    """Resolve one explicitly selected artifact domain to an immutable target."""

    if artifact_domain == "generic_feasibility":
        try:
            from lvke_mcp.domains.reports import artifacts as deliverable_artifacts

            generic = deliverable_artifacts.get_artifact(
                workspace_id, artifact_id,
            )
        except Exception:  # noqa: BLE001 - target is unavailable or invalid
            generic = {}
        if not generic:
            return None, {}, ["report_artifact_not_found"]
        if (
            generic.get("status") not in {"succeeded", "released"}
            or generic.get("current") is not True
            or generic.get("integrity_status") != "passed"
        ):
            return None, {}, ["report_artifact_not_current"]
        snapshot = _generic_artifact_snapshot(generic)
        revision_snapshot, revision_bindings, revision_blockers = _linked_generic_report_revision(
            workspace_id,
            generic,
        )
        if revision_blockers:
            return None, {}, revision_blockers
        bindings = _artifact_upstream_bindings(generic)
        for key in ("evidence_pack_ids", "research_package_ids"):
            bindings[key] = _string_ids(bindings.get(key), revision_bindings.get(key))
        for key in ("finance_tables_package_id", "report_revision_id"):
            if revision_bindings.get(key):
                bindings[key] = revision_bindings[key]
        bindings["finance_run_id"] = str(
            generic.get("finance_run_id") or generic.get("run_id") or ""
        )
        bindings["scenario_matrix_ids"] = _acquisition_scenario_matrix_ids(
            workspace_id,
            bindings["finance_run_id"],
        )
        snapshot["upstream_bindings"] = deepcopy(bindings)
        if revision_snapshot:
            snapshot["report_revision"] = revision_snapshot
        return snapshot, bindings, []
    if artifact_domain == "asset_acquisition":
        try:
            from lvke_mcp.domains.asset_acquisition import backend as acquisition_service

            acquisition = acquisition_service.get_artifact(
                workspace_id,
                artifact_id,
            )
        except Exception:  # noqa: BLE001 - acquisition storage may be unavailable
            acquisition = {}
        if not acquisition:
            return None, {}, ["report_artifact_not_found"]
        if (
            acquisition.get("status") != "succeeded"
            or acquisition.get("ok") is not True
            or acquisition.get("integrity_status") != "passed"
        ):
            return None, {}, ["report_artifact_not_current"]
        snapshot = _acquisition_artifact_snapshot(acquisition)
        bindings = _artifact_upstream_bindings(acquisition)
        bindings["finance_run_id"] = str(acquisition.get("run_id") or "")
        bindings["scenario_matrix_ids"] = _acquisition_scenario_matrix_ids(
            workspace_id,
            bindings["finance_run_id"],
        )
        snapshot["upstream_bindings"] = deepcopy(bindings)
        return snapshot, bindings, []
    return None, {}, ["report_artifact_domain_invalid"]


def _resolve_report_artifact_auto(
    workspace_id: str,
    artifact_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any], str, list[str]]:
    matches: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    resolution_blockers: list[str] = []
    for domain in sorted(_REPORT_ARTIFACT_DOMAINS):
        payload, bindings, blockers = _resolve_report_artifact(
            workspace_id,
            artifact_id,
            artifact_domain=domain,
        )
        if payload is not None and not blockers:
            matches.append((domain, payload, bindings))
        resolution_blockers.extend(
            blocker for blocker in blockers
            if blocker not in {"report_artifact_not_found"}
        )
    if len(matches) == 1:
        domain, payload, bindings = matches[0]
        return payload, bindings, domain, []
    if len(matches) > 1:
        return None, {}, "", ["report_artifact_domain_ambiguous"]
    if resolution_blockers:
        return None, {}, "", sorted(set(resolution_blockers))
    return None, {}, "", ["report_artifact_not_found"]


def _combined_bindings_manifest(
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    """Preserve every component binding while retaining scalar compatibility.

    A combined delivery may legitimately contain more than one run, table
    package, evidence pack or report artifact.  A plain ``dict.update`` loses
    earlier component bindings and makes the preparation's upstream snapshot
    incomplete.  The component manifest is authoritative; the top-level
    aggregate remains for existing single-run consumers.
    """

    aggregate: dict[str, Any] = {}
    component_bindings: list[dict[str, Any]] = []
    conflicts: dict[str, list[Any]] = {}
    for component in components:
        raw_bindings = deepcopy(component.get("bindings") or {})
        component_bindings.append({
            "target_type": str(component.get("target_type") or ""),
            "target_id": str(component.get("target_id") or ""),
            "target_sha256": str(component.get("target_sha256") or ""),
            "bindings": raw_bindings,
        })
        for key, raw_value in raw_bindings.items():
            if raw_value in (None, "", [], {}):
                continue
            if isinstance(raw_value, list):
                current = aggregate.setdefault(key, [])
                if not isinstance(current, list):
                    conflicts.setdefault(key, [deepcopy(current)])
                    values = conflicts[key]
                else:
                    values = current
                for value in raw_value:
                    if value not in values:
                        values.append(deepcopy(value))
                continue
            if key not in aggregate:
                aggregate[key] = deepcopy(raw_value)
                continue
            if aggregate[key] != raw_value:
                values = conflicts.setdefault(key, [deepcopy(aggregate[key])])
                if raw_value not in values:
                    values.append(deepcopy(raw_value))
    aggregate["component_bindings"] = component_bindings
    if conflicts:
        aggregate["component_binding_conflicts"] = conflicts
    return aggregate


def _combined_lineage_blockers(components: list[dict[str, Any]]) -> list[str]:
    """Reject only conflicts that make one combined deliverable incoherent."""

    direct_run_ids = {
        str(component.get("target_id") or "")
        for component in components
        if component.get("target_type") in {"finance_run", "acquisition_run"}
    }
    direct_revision_ids = {
        str(component.get("target_id") or "")
        for component in components
        if component.get("target_type") == "report_revision"
    }
    report_bound_runs: set[str] = set()
    artifact_bound_revisions: set[str] = set()
    blockers: list[str] = []
    for component in components:
        target_type = str(component.get("target_type") or "")
        bindings = component.get("bindings") or {}
        bound_run_id = str(bindings.get("finance_run_id") or "")
        if target_type in {"report_revision", "report_artifact"} and bound_run_id:
            report_bound_runs.add(bound_run_id)
            if direct_run_ids and bound_run_id not in direct_run_ids:
                blockers.append("combined_report_finance_run_mismatch")
        if target_type == "report_artifact":
            bound_revision_id = str(bindings.get("report_revision_id") or "")
            if bound_revision_id:
                artifact_bound_revisions.add(bound_revision_id)
                if direct_revision_ids and bound_revision_id not in direct_revision_ids:
                    blockers.append("combined_report_artifact_revision_mismatch")
        if target_type in {"finance_tables_package", "acquisition_tables_package"}:
            if direct_run_ids and bound_run_id and bound_run_id not in direct_run_ids:
                blockers.append("combined_table_package_run_mismatch")
    if len(report_bound_runs) > 1:
        blockers.append("combined_report_finance_binding_conflict")
    if direct_revision_ids and artifact_bound_revisions - direct_revision_ids:
        blockers.append("combined_report_revision_binding_conflict")
    return sorted(set(blockers))


def _resolve_target(
    workspace_id: str,
    target: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    target = deepcopy(target)
    target_type = target["target_type"]
    target_id = target["target_id"]
    blockers: list[str] = []
    payload: Any = None
    bindings: dict[str, Any] = {}
    resource_uri = ""
    if target_type == "finance_run":
        from lvke_mcp.domains.finance.run_service import get_workspace_finance_run
        payload = get_workspace_finance_run(
            workspace_id,
            run_id=target_id,
            view="full",
        )
        if not payload.get("available") or str(payload.get("run_id") or "") != target_id:
            blockers.append("finance_run_not_found")
        bindings["finance_run_id"] = target_id
    elif target_type == "finance_tables_package":
        from lvke_mcp.adapters.finance_tables_repository import PACKAGE_STORE
        record = PACKAGE_STORE.get(
            workspace_id,
            target_id,
        )
        payload = record
        if record is None:
            blockers.append("finance_tables_package_not_found")
        else:
            bindings["finance_run_id"] = str((record.get("payload") or {}).get("run_id") or "")
            resource_uri = str(record.get("resource_uri") or "")
    elif target_type == "finance_xlsx":
        path = _safe_file(workspace_id, str(target.get("file_path") or target_id))
        if path is None or path.suffix.lower() not in {".xlsx", ".xlsm"}:
            blockers.append("finance_xlsx_not_found_or_outside_workspace")
        else:
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            payload = {"path": str(path), "size": path.stat().st_size, "sha256": digest}
    elif target_type == "finance_xlsx_source":
        from lvke_mcp.adapters.source_files_repository import resolve_source_workbook_for_review
        source_file_id = str(target.get("source_file_id") or "").strip()
        if not source_file_id:
            blockers.append("finance_xlsx_source_file_id_required")
        else:
            resolution = resolve_source_workbook_for_review(
                workspace_id,
                source_file_id,
            )
            if not resolution.get("ok"):
                blockers.append(str(resolution.get("code") or "source_workbook_not_found"))
            else:
                payload = {
                    "path": str(resolution.get("path") or ""),
                    "size": int(resolution.get("size") or 0),
                    "sha256": str(resolution.get("sha256") or ""),
                    "source_file_id": str(resolution.get("source_file_id") or source_file_id),
                    "original_filename": str(resolution.get("original_filename") or ""),
                }
                bindings["source_file_id"] = str(resolution.get("source_file_id") or source_file_id)
    elif target_type == "acquisition_run":
        from lvke_mcp.domains.asset_acquisition.backend import (
            get_run,
            get_scenario_matrix,
            list_scenario_matrices,
        )
        payload = get_run(
            workspace_id,
            target_id,
        )
        if not payload.get("available") or str(payload.get("run_id") or "") != target_id:
            blockers.append("acquisition_run_not_found")
        else:
            matrix_summaries = list_scenario_matrices(workspace_id, target_id)
            matrices = [
                get_scenario_matrix(
                    workspace_id,
                    target_id,
                    str(item.get("matrix_id") or ""),
                )
                for item in matrix_summaries
                if item.get("matrix_id")
            ]
            payload = {**payload, "scenario_matrices": [item for item in matrices if item]}
            bindings["scenario_matrix_ids"] = [
                str(item.get("matrix_id") or "") for item in matrices if item.get("matrix_id")
            ]
        bindings["finance_run_id"] = target_id
    elif target_type == "acquisition_tables_package":
        from lvke_mcp.domains.asset_acquisition.tables import get_package_record
        record = get_package_record(workspace_id, target_id)
        payload = record
        if record is None:
            blockers.append("acquisition_tables_package_not_found")
        else:
            acquisition_run_id = str((record.get("payload") or {}).get("run_id") or "")
            bindings["finance_run_id"] = acquisition_run_id
            if acquisition_run_id:
                bindings["scenario_matrix_ids"] = _acquisition_scenario_matrix_ids(
                    workspace_id,
                    acquisition_run_id,
                )
            resource_uri = str(record.get("resource_uri") or "")
    elif target_type == "report_revision":
        from lvke_mcp.adapters.report_repository import REVISION_STORE
        record = REVISION_STORE.get(
            workspace_id,
            target_id,
        )
        payload = record
        if record is None:
            blockers.append("report_revision_not_found")
        else:
            record_payload = record.get("payload") or {}
            native_revision_id = str(record_payload.get("native_revision_id") or "")
            document = record_payload.get("document_snapshot")
            if not isinstance(document, dict):
                try:
                    from lvke_mcp.domains.reports.doc_service import read_document

                    document = read_document(
                        workspace_id,
                        revision_id=native_revision_id,
                    )
                except Exception:  # noqa: BLE001
                    blockers.append("report_native_revision_not_found")
                    document = {}
            payload = {"revision_record": record, "document": document}
            upstream = record_payload.get("upstream") or {}
            bindings = {
                "report_revision_id": target_id,
                "finance_run_id": str(upstream.get("run_id") or ""),
                "finance_tables_package_id": str(upstream.get("finance_tables_package_id") or ""),
                "evidence_pack_ids": list(upstream.get("evidence_pack_ids") or []),
                "research_package_ids": list(upstream.get("research_package_ids") or []),
            }
            bindings["scenario_matrix_ids"] = _acquisition_scenario_matrix_ids(
                workspace_id,
                bindings["finance_run_id"],
            )
            resource_uri = str(record.get("resource_uri") or "")
    elif target_type == "report_artifact":
        artifact_domain = str(target.get("artifact_domain") or "").strip()
        if not artifact_domain:
            payload, bindings, artifact_domain, artifact_blockers = (
                _resolve_report_artifact_auto(workspace_id, target_id)
            )
            blockers.extend(artifact_blockers)
            if artifact_domain:
                target["artifact_domain"] = artifact_domain
        elif artifact_domain not in _REPORT_ARTIFACT_DOMAINS:
            blockers.append("report_artifact_domain_invalid")
        else:
            target["artifact_domain"] = artifact_domain
            payload, bindings, artifact_blockers = _resolve_report_artifact(
                workspace_id,
                target_id,
                artifact_domain=artifact_domain,
            )
            if payload is None and artifact_blockers == ["report_artifact_not_found"]:
                other_domains = _REPORT_ARTIFACT_DOMAINS - {artifact_domain}
                mismatched = any(
                    _resolve_report_artifact(
                        workspace_id,
                        target_id,
                        artifact_domain=other_domain,
                    )[0] is not None
                    for other_domain in other_domains
                )
                blockers.append(
                    "report_artifact_domain_mismatch"
                    if mismatched else "report_artifact_not_found"
                )
            else:
                blockers.extend(artifact_blockers)
    elif target_type == "combined_deliverable":
        component_targets = target.get("components") or []
        if not isinstance(component_targets, list) or len(component_targets) < 2:
            blockers.append("combined_components_required")
            payload = {"components": []}
        else:
            components = []
            for raw_component in component_targets:
                try:
                    component = normalize_target(raw_component)
                except ValueError as exc:
                    blockers.append(f"combined_component_invalid:{exc}")
                    continue
                if component["target_type"] == "combined_deliverable":
                    blockers.append("combined_component_recursive")
                    continue
                resolved, component_blockers = _resolve_target(
                    workspace_id,
                    component,
                )
                blockers.extend(f"{component['target_id']}:{item}" for item in component_blockers)
                if resolved:
                    components.append(resolved)
            payload = {"components": components}
            bindings = _combined_bindings_manifest(components)
            blockers.extend(_combined_lineage_blockers(components))
    if payload is None:
        payload = {}
    target_hash = (
        str(payload.get("sha256") or "")
        if target_type in {"finance_xlsx", "finance_xlsx_source"} and isinstance(payload, dict)
        else sha256_json(payload)
    )
    return ({
        "target_type": target_type, "target_id": target_id, "target_sha256": target_hash,
        "snapshot": payload, "bindings": bindings, "resource_uri": resource_uri,
        "target_spec": target,
    } if not blockers else None), blockers


def _acquisition_run_snapshot(run: dict[str, Any]) -> dict[str, Any]:
    """Project immutable acquisition inputs and deterministic validation state."""

    retained = (
        "run_id", "status", "available", "validation_status", "consistency_ok",
        "formal_spec_valid", "formal_spec_errors", "spec_id", "spec_hash",
        "input_hash", "spec_snapshot_hash", "evidence_binding_version",
        "evidence_binding_hash", "evidence_status", "evidence_formal_ok",
        "model_version", "result", "issues", "scenario_matrices", "created_at",
    )
    return {
        key: deepcopy(run.get(key))
        for key in retained
        if key in run
    }


def _binding_snapshot(
    workspace_id: str,
    bindings: dict[str, Any],
) -> dict[str, Any]:
    """Resolve immutable upstream IDs to content hashes for invalidation checks."""

    snapshot: dict[str, Any] = {}
    finance_run_id = str(bindings.get("finance_run_id") or "")
    if finance_run_id:
        run: dict[str, Any] = {}
        acquisition_run = False
        try:
            from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

            run = get_workspace_finance_run(
                workspace_id,
                run_id=finance_run_id,
                view="full",
            )
        except Exception:  # noqa: BLE001 - acquisition IDs use a separate service
            pass
        if not run.get("available"):
            try:
                from lvke_mcp.domains.asset_acquisition.backend import get_run

                run = get_run(
                    workspace_id,
                    finance_run_id,
                )
                acquisition_run = True
            except Exception:  # noqa: BLE001
                run = {}
        run_snapshot = _acquisition_run_snapshot(run) if acquisition_run else run
        snapshot["finance_run"] = {
            "id": finance_run_id,
            "content_hash": (
                sha256_json(run_snapshot)
                if run and run.get("available") else None
            ),
        }

    table_id = str(bindings.get("finance_tables_package_id") or "")
    if table_id:
        record = None
        try:
            from lvke_mcp.adapters.finance_tables_repository import PACKAGE_STORE

            record = PACKAGE_STORE.get(
                workspace_id,
                table_id,
            )
        except (ValueError, OSError):
            record = None
        if record is None:
            try:
                from lvke_mcp.domains.asset_acquisition.tables import get_package_record

                record = get_package_record(workspace_id, table_id)
            except (ValueError, OSError):
                record = None
        snapshot["finance_tables_package"] = {
            "id": table_id,
            "content_hash": (record or {}).get("content_hash"),
            "basis_hash": (record or {}).get("basis_hash"),
        }

    scenario_rows: list[dict[str, Any]] = []
    for matrix_id in bindings.get("scenario_matrix_ids") or []:
        try:
            from lvke_mcp.domains.asset_acquisition.backend import get_scenario_matrix

            matrix = get_scenario_matrix(workspace_id, finance_run_id, str(matrix_id))
        except (OSError, ValueError):
            matrix = {}
        scenario_rows.append({
            "id": str(matrix_id),
            "content_hash": sha256_json(matrix) if matrix else None,
            "matrix_hash": (matrix or {}).get("matrix_hash"),
        })
    if scenario_rows:
        snapshot["scenario_matrices"] = scenario_rows

    report_revision_id = str(bindings.get("report_revision_id") or "")
    if report_revision_id:
        try:
            from lvke_mcp.adapters.report_repository import REVISION_STORE

            record = REVISION_STORE.get(
                workspace_id,
                report_revision_id,
            )
        except (OSError, ValueError):
            record = None
        snapshot["report_revision"] = {
            "id": report_revision_id,
            "content_hash": (record or {}).get("content_hash"),
            "basis_hash": (record or {}).get("basis_hash"),
        }

    try:
        from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
    except Exception:  # noqa: BLE001
        EVIDENCE_STORE = None  # type: ignore[assignment]
    evidence_rows = []
    for object_id in bindings.get("evidence_pack_ids") or []:
        record = (
            EVIDENCE_STORE.get(
                workspace_id,
                str(object_id),
            )
            if EVIDENCE_STORE
            else None
        )
        evidence_rows.append({
            "id": str(object_id), "content_hash": (record or {}).get("content_hash"),
            "basis_hash": (record or {}).get("basis_hash"),
        })
    if evidence_rows:
        snapshot["evidence_packs"] = evidence_rows

    try:
        from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_STORE
    except Exception:  # noqa: BLE001
        RESEARCH_STORE = None  # type: ignore[assignment]
    research_rows = []
    for object_id in bindings.get("research_package_ids") or []:
        record = (
            RESEARCH_STORE.get(
                workspace_id,
                str(object_id),
            )
            if RESEARCH_STORE
            else None
        )
        research_rows.append({
            "id": str(object_id), "content_hash": (record or {}).get("content_hash"),
            "basis_hash": (record or {}).get("basis_hash"), "status": (record or {}).get("status"),
        })
    if research_rows:
        snapshot["research_packages"] = research_rows
    component_snapshots: list[dict[str, Any]] = []
    for component in bindings.get("component_bindings") or []:
        if not isinstance(component, dict):
            continue
        component_snapshots.append({
            "target_type": str(component.get("target_type") or ""),
            "target_id": str(component.get("target_id") or ""),
            "target_sha256": str(component.get("target_sha256") or ""),
            "upstream": _binding_snapshot(
                workspace_id,
                component.get("bindings")
                if isinstance(component.get("bindings"), dict)
                else {},
            ),
        })
    if component_snapshots:
        snapshot["components"] = component_snapshots
    return snapshot
