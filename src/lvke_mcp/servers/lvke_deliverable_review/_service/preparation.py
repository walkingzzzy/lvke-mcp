"""审查准备：强制 findings、preparation 基准与校验记录。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lvke_mcp.runtime.storage import require_safe_id, sha256_json
from lvke_mcp.runtime.evidence_qualification import (
    declared_evidence_policy,
    project_fact_may_be_certified,
)
from lvke_mcp.servers.lvke_deliverable_review import rules
from lvke_mcp.servers.lvke_deliverable_review.contracts import normalize_project_context, normalize_target

from .base import (
    PREPARATION_STORE,
    REPO_ROOT,
    _blocked,
    _ok,
    _write,
)

from .legacy_gate import (
    _legacy_gate_snapshot,
)

from .target_resolve import (
    _binding_snapshot,
    _resolve_target,
)


def _mandatory_findings(standards: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for package in standards.get("packages") or []:
        package_id = str(package.get("package_id") or "")
        required = list(package.get("review_findings_required") or [])
        for index, text in enumerate(required, start=1):
            if package_id == "PKG-STD-021":
                rule_id = "HM-HOTEL-004"
                role = "legal"
                category = "land_use_compliance"
            elif package_id == "PKG-STD-022":
                rule_id = "HM-MINE-003"
                role = "legal"
                category = "mineral_rights_and_land"
            else:
                rule_id = f"CORE.REQUIRED.{package_id}.{index}"
                role = "business"
                category = "required_manual_review"
            rows.append({
                "rule_id": rule_id,
                "instance_key": f"{package_id}:{index}",
                "package_id": package_id,
                "message": str(text),
                "category": category,
                "review_area": role,
            })
    return rows


def prepare(args: dict[str, Any]) -> dict[str, Any]:
    def execute(workspace_id: str) -> dict[str, Any]:
        target = normalize_target(args.get("target"))
        resolved, blockers = _resolve_target(
            workspace_id,
            target,
        )
        if blockers or resolved is None:
            return _blocked(blockers[0], "审查目标无法完整解析", blockers=blockers)
        component_types = [
            str(item.get("target_type") or "")
            for item in (resolved.get("snapshot") or {}).get("components") or []
            if isinstance(item, dict)
        ]
        project_context = normalize_project_context(
            args.get("project_context"),
            target_type=target["target_type"],
        )
        pack = rules.compose(
            target["target_type"], args.get("rule_pack_ids") or [],
            args.get("industry_overlays") or [], component_types=component_types,
            project_context=project_context,
        )
        standards = rules.standards_snapshot(
            REPO_ROOT,
            pack["standard_package_ids"],
            review_purpose=project_context["review_purpose"],
        )
        upstream_snapshot = _binding_snapshot(
            workspace_id,
            resolved["bindings"],
        )
        legacy_gate_snapshot = _legacy_gate_snapshot(
            workspace_id,
            resolved,
        )
        target_snapshot = resolved.get("snapshot") if isinstance(resolved.get("snapshot"), dict) else {}
        revision_record = target_snapshot.get("revision_record") if isinstance(target_snapshot.get("revision_record"), dict) else {}
        revision_payload = revision_record.get("payload") if isinstance(revision_record.get("payload"), dict) else {}
        upstream = revision_payload.get("upstream") if isinstance(revision_payload.get("upstream"), dict) else {}
        evidence_policy = declared_evidence_policy(upstream, default="candidate")
        evidence_metadata = {
            "evidence_policy": evidence_policy,
            "project_fact_certified": project_fact_may_be_certified(
                evidence_policy,
                own_qualification_passed=True,
                # parents= 会连父对象的 evidence_policy 一起复核，不只看它自报的
                # certified 布尔值。
                parents=[upstream],
            ),
            "reconstruction_records": list(upstream.get("reconstruction_records") or []),
            "reconstructed_source_ids": list(upstream.get("reconstructed_source_ids") or []),
            "unresolved_inputs": list(upstream.get("unresolved_inputs") or []),
            "release_limitations": list(upstream.get("release_limitations") or []),
        }
        mandatory_findings = _mandatory_findings(standards)
        basis = _preparation_basis({
            "target": {key: resolved[key] for key in ("target_type", "target_id", "target_sha256")},
            "bindings": resolved["bindings"], "upstream_snapshot": upstream_snapshot,
            "rule_pack": pack, "standards": standards,
            "legacy_gate_snapshot": legacy_gate_snapshot,
            "evidence_metadata": evidence_metadata,
            "project_context": project_context,
            "engine_version": rules.ENGINE_VERSION, "recalculation_environment_version": rules.RECALC_ENV_VERSION,
        })
        preparation_payload = {
            **basis,
            "target_spec": resolved["target_spec"],
            "target_snapshot": resolved["snapshot"],
            "mandatory_findings": mandatory_findings,
        }
        record = PREPARATION_STORE.put(
            workspace_id, preparation_payload,
            producer="lvke-deliverable-review.review_prepare", status="ok",
            source_ids=[target["target_id"], *[str(value) for value in resolved["bindings"].values() if isinstance(value, str)]],
            basis=basis, schema_version="deliverable_review_preparation.v1",
        )
        verified_record, integrity_reasons = _verified_preparation_record(
            workspace_id,
            str(record.get("object_id") or ""),

            expected_basis_hash=sha256_json(basis),
            expected_content_hash=sha256_json(preparation_payload),
        )
        if verified_record is None:
            return _blocked(
                "preparation_integrity_failed",
                "审查准备对象写入后完整性校验失败",
                integrity_reasons=integrity_reasons,
            )
        record = verified_record
        warnings = [f"标准包未完成：{item}" for item in standards["incomplete"]]
        warnings.extend(
            f"标准包仅支持框架性过程验收：{item}；不得声称已按方法书全文完成"
            for item in standards.get("framework_only") or []
        )
        return _ok(
            review_preparation_id=record["object_id"], 
            target=basis["target"], bindings=basis["bindings"],
            rule_pack=pack, standards=standards, review_scope=pack["applicable_rules"],
            project_context=project_context,
            selected_rule_packs=pack.get("selected_rule_packs") or [],
            excluded_rule_packs=pack.get("excluded_rule_packs") or [],
            excluded_rules=pack.get("excluded_rules") or [],
            legacy_gate_snapshot=legacy_gate_snapshot,
            mandatory_findings=mandatory_findings,
            resource_uris=[record["resource_uri"]], warnings=warnings, blockers=[],
            next_actions=["调用 review_start 创建不可变审查运行"],
        )
    return _write("review_prepare", args, execute)


_PREPARATION_BASIS_FIELDS = (
    "target",
    "bindings",
    "upstream_snapshot",
    "rule_pack",
    "standards",
    "project_context",
    "legacy_gate_snapshot",
    "evidence_metadata",
    "engine_version",
    "recalculation_environment_version",
)


def _preparation_basis(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(payload.get(key))
        for key in _PREPARATION_BASIS_FIELDS
    }


def _verified_preparation_record(
    workspace_id: str,
    preparation_id: str,
    *,
    expected_basis_hash: str = "",
    expected_content_hash: str = "",
) -> tuple[dict[str, Any] | None, list[str]]:
    """Read and verify a content-addressed review preparation record."""

    try:
        workspace_id = require_safe_id(workspace_id, "workspace_id")
        preparation_id = require_safe_id(
            preparation_id,
            "review_preparation_id",
        )
        record = PREPARATION_STORE.get(workspace_id, preparation_id)
    except (OSError, ValueError):
        return None, ["preparation_record_unavailable"]
    if not isinstance(record, dict):
        return None, ["preparation_record_unavailable"]
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None, ["preparation_payload_invalid"]
    content_hash = sha256_json(payload)
    basis_hash = sha256_json(_preparation_basis(payload))
    expected_object_id = (
        f"{PREPARATION_STORE.id_prefix}_"
        f"{content_hash.removeprefix('sha256:')[:24]}"
    )
    reasons: list[str] = []
    if expected_basis_hash and basis_hash != expected_basis_hash:
        reasons.append("preparation_basis_binding_mismatch")
    if expected_content_hash and content_hash != expected_content_hash:
        reasons.append("preparation_content_binding_mismatch")
    if record.get("content_hash") != content_hash:
        reasons.append("preparation_content_hash_mismatch")
    if record.get("basis_hash") != basis_hash:
        reasons.append("preparation_basis_hash_mismatch")
    if record.get("object_id") != expected_object_id:
        reasons.append("preparation_object_id_mismatch")
    if preparation_id != expected_object_id:
        reasons.append("preparation_reference_id_mismatch")
    if record.get("workspace_id") != workspace_id:
        reasons.append("preparation_workspace_mismatch")
    if record.get("resource_uri") != PREPARATION_STORE.uri(
        workspace_id,
        expected_object_id,
    ):
        reasons.append("preparation_resource_uri_mismatch")
    if record.get("producer") != "lvke-deliverable-review.review_prepare":
        reasons.append("preparation_producer_mismatch")
    if record.get("schema_version") != "deliverable_review_preparation.v1":
        reasons.append("preparation_schema_mismatch")
    if record.get("status") != "ok":
        reasons.append("preparation_status_invalid")
    return (
        deepcopy(record) if not reasons else None,
        sorted(set(reasons)),
    )


def _standard_basis(preparation_payload: dict[str, Any]) -> list[dict[str, Any]]:
    basis: list[dict[str, Any]] = []
    for row in ((preparation_payload.get("standards") or {}).get("packages") or []):
        artifacts = row.get("artifacts") or []
        if not artifacts:
            basis.append({
                "standard_package_id": row.get("package_id"), "title": row.get("title"),
                "content_hash": row.get("source_manifest_sha256"), "gate_status": row.get("gate_status"),
            })
            continue
        for artifact in artifacts:
            basis.append({
                "standard_package_id": row.get("package_id"), "title": row.get("title"),
                "standard_artifact_id": artifact.get("artifact_id"),
                "publisher": artifact.get("publisher"), "document_number": artifact.get("document_number"),
                "publication_date": artifact.get("publication_date"),
                "source_url": artifact.get("source_url") or artifact.get("official_page_url"),
                "content_hash": artifact.get("sha256"), "gate_status": row.get("gate_status"),
            })
    return basis


def _run_from_preparation(
    workspace_id: str,
    preparation_payload: dict[str, Any],
) -> dict[str, Any]:
    target = preparation_payload.get("target") or {}
    target_type = str(target.get("target_type") or "")
    snapshot = preparation_payload.get("target_snapshot") or {}
    if target_type == "finance_run":
        return snapshot if isinstance(snapshot, dict) else {}
    if target_type == "acquisition_run":
        run = snapshot if isinstance(snapshot, dict) else {}
        if run and not isinstance(run.get("spec"), dict):
            try:
                from lvke_mcp.domains.asset_acquisition.backend import get_spec

                spec_row = get_spec(
                    workspace_id,
                    str(run.get("spec_id") or ""),
                )
                run = {**run, "spec": deepcopy(spec_row.get("spec") or {})}
            except Exception:  # noqa: BLE001 - missing spec remains an explicit incomplete input
                pass
        return run
    run_id = str((preparation_payload.get("bindings") or {}).get("finance_run_id") or "")
    if not run_id:
        return {}
    if target_type == "acquisition_tables_package":
        from lvke_mcp.domains.asset_acquisition.backend import get_run, get_spec

        run = get_run(workspace_id, run_id)
        spec_row = (
            get_spec(
                workspace_id,
                str(run.get("spec_id") or ""),
            )
            if run
            else {}
        )
        return {**run, "spec": deepcopy(spec_row.get("spec") or {})} if run else {}
    from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

    run = get_workspace_finance_run(
        workspace_id,
        run_id=run_id,
        view="full",
    )
    if run.get("available") and str(run.get("run_id") or "") == run_id:
        return run
    try:
        from lvke_mcp.domains.asset_acquisition.backend import get_run, get_spec

        acquisition_run = get_run(
            workspace_id,
            run_id,
        )
        if acquisition_run.get("available"):
            spec_row = get_spec(
                workspace_id,
                str(acquisition_run.get("spec_id") or ""),
            )
            return {**acquisition_run, "spec": deepcopy(spec_row.get("spec") or {})}
    except Exception:  # noqa: BLE001 - caller records unavailable bound run
        pass
    return run


def _component_preparation(parent: dict[str, Any], component: dict[str, Any]) -> dict[str, Any]:
    child = deepcopy(parent)
    child["target"] = {
        key: component.get(key) for key in ("target_type", "target_id", "target_sha256")
    }
    child["target_spec"] = component.get("target_spec") or child["target"]
    child["target_snapshot"] = component.get("snapshot") or {}
    child["bindings"] = component.get("bindings") or {}
    return child
