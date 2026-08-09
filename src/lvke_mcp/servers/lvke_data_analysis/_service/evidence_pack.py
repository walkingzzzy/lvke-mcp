"""Build immutable evidence packs with formal/reconstructed/fixture/assumption tracks.

Minimum number of independent source families that must agree on the same
numeric value before a web-sourced field is auto-accepted for an
``estimate_preview`` run.  Two URLs from one registrable domain are one
family, so this genuinely means two independent origins — never one site
echoed twice.  Auto-acceptance NEVER upgrades formal-delivery eligibility;
that still requires the single human node at the thirteen-table review.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from lvke_mcp.adapters.data_analysis_repository import CANDIDATE_STORE, EVIDENCE_STORE
from lvke_mcp.runtime.evidence_qualification import (
    FORMAL_EVIDENCE,
    project_fact_may_be_certified,
)
from lvke_mcp.runtime.source_reconstruction import (
    SOURCE_RECONSTRUCTED,
    normalize_reconstruction,
    validate_reconstruction_records,
)
from lvke_mcp.runtime.storage import sha256_json

from .envelope import _missing
from .ingest import _documents_from_task

EVIDENCE_TRACKS = {"real", SOURCE_RECONSTRUCTED, "technical_fixture", "controlled_assumption"}
_SHA256_PATTERN = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_MIN_COROBORATING_FAMILIES = 2


def _validate_fixture_manifest(
    manifest: Any,
    selected: list[dict[str, Any]],
    fact_candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate a technical fixture without granting it formal standing."""

    if not isinstance(manifest, dict):
        return None, ["fixture_manifest_required"]
    required = (
        "fixture_id", "fixture_version", "project_type", "industry_code",
        "source_snapshot_ids", "content_hashes", "allowed_fields",
        "prohibited_extrapolations", "generated_at", "generator_version",
        "test_scope",
    )
    missing = [name for name in required if manifest.get(name) in (None, "", [])]
    if missing:
        return None, [f"fixture_manifest_field_required:{name}" for name in missing]
    try:
        datetime.fromisoformat(str(manifest["generated_at"]).replace("Z", "+00:00"))
    except ValueError:
        return None, ["fixture_manifest_generated_at_invalid"]
    source_ids = {str(item) for item in manifest.get("source_snapshot_ids") or [] if str(item)}
    selected_by_id = {
        str(item.get("source_id") or ""): item for item in selected
        if str(item.get("source_id") or "")
    }
    if source_ids != set(selected_by_id):
        return None, ["fixture_manifest_source_set_mismatch"]
    hashes = manifest.get("content_hashes") or {}
    if not isinstance(hashes, dict):
        return None, ["fixture_manifest_content_hashes_invalid"]
    for source_id, source in selected_by_id.items():
        expected = str(hashes.get(source_id) or "")
        actual = str(source.get("content_hash") or "")
        if not _SHA256_PATTERN.fullmatch(expected) or expected.removeprefix("sha256:") != actual.removeprefix("sha256:"):
            return None, [f"fixture_manifest_content_hash_mismatch:{source_id}"]
        if not source.get("locators"):
            return None, [f"fixture_source_locator_required:{source_id}"]
    allowed = {str(item) for item in manifest.get("allowed_fields") or [] if str(item)}
    candidate_fields = {
        str(item.get("field") or item.get("metric") or "")
        for item in fact_candidates if isinstance(item, dict)
    } - {""}
    if not candidate_fields.issubset(allowed):
        return None, ["fixture_candidate_field_not_allowed"]
    normalized = {
        **manifest,
        "source_snapshot_ids": sorted(source_ids),
        "content_hashes": {key: str(hashes[key]) for key in sorted(source_ids)},
        "allowed_fields": sorted(allowed),
        "prohibited_extrapolations": sorted({
            str(item) for item in manifest.get("prohibited_extrapolations") or [] if str(item)
        }),
        "test_scope": sorted({str(item) for item in manifest.get("test_scope") or [] if str(item)}),
    }
    return normalized, []


def _missing_pack_fields(
    expected_fields: list[str],
    fact_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """列出期望字段中没有带值候选事实支撑的缺口；无缺口返回空数组，不编数补齐。"""
    missing: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field in expected_fields:
        name = str(field or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        supported = False
        attempted = False
        for candidate in fact_candidates:
            if not isinstance(candidate, dict):
                continue
            label = str(candidate.get("metric") or candidate.get("field") or "").strip()
            if label.lower() != name.lower():
                continue
            attempted = True
            if candidate.get("value") is not None:
                supported = True
                break
        if not supported:
            missing.append(
                {
                    "field": name,
                    "reason": "candidate_without_value" if attempted else "no_fact_candidate",
                }
            )
    return missing


def _registrable_domain(url: str) -> str:
    """Registrable domain (eTLD+1) with a compact Chinese public-suffix set.

    ``a.gov.cn`` and ``b.gov.cn`` are different families; ``news.sina.com.cn``
    and ``finance.sina.com.cn`` collapse to ``sina.com.cn``.  A deliberately
    small implementation — corroboration counting only needs to avoid treating
    two hosts under one publisher as independent origins.
    """

    from urllib.parse import urlparse

    host = urlparse(url).netloc.lower()
    if not host:
        host = str(url or "").strip().lower().split("/")[0]
    host = host.split("@")[-1].split(":")[0]
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    multi_part_suffixes = {
        ("com", "cn"), ("gov", "cn"), ("org", "cn"), ("net", "cn"),
        ("edu", "cn"), ("ac", "cn"),
    }
    if tuple(labels[-2:]) in multi_part_suffixes:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _source_family(source: dict[str, Any]) -> str:
    """Registrable-domain family for a source; controlled files stand alone.

    A controlled upload has no public domain, so each is its own family keyed
    by ``source_id`` — a single uploaded file can never self-corroborate.
    """

    url = str(source.get("url") or "")
    domain = _registrable_domain(url) if url else ""
    return domain or f"file:{source.get('source_id')}"


def _adjudicate_estimate_fields(
    selected: list[dict[str, Any]],
    fact_candidates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Auto-accept a field only when ≥N independent families agree on one value.

    Returns ``field -> {value, unit, families, source_ids, assurance}`` for the
    fields that clear the corroboration bar.  Conflicts (families disagree) and
    thin evidence (one family) are deliberately left out so the caller records
    them as ``missing`` rather than guessing.  The output is always
    ``estimate_preview`` grade — this path exists so upstream stays fully
    automatic for rough sizing, not so it can bypass the human delivery gate.
    """

    family_by_source = {
        str(doc.get("source_id")): _source_family(doc) for doc in selected
    }
    selected_ids = set(family_by_source)
    # field -> value_key -> {families:set, source_ids:set, unit}
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for candidate in fact_candidates:
        if not isinstance(candidate, dict):
            continue
        numeric = candidate.get("numeric_value")
        if not isinstance(numeric, (int, float)) or isinstance(numeric, bool):
            continue  # only gate-approved numbers can auto-corroborate
        source_id = str(candidate.get("source_id") or "")
        if source_id not in selected_ids:
            continue
        field = str(candidate.get("field") or candidate.get("metric") or "").strip()
        if not field:
            continue
        # Bucket by the exact numeric value + unit so different units are never
        # silently merged into one "agreement".
        unit = str(candidate.get("expected_unit") or "")
        value_key = f"{numeric}|{unit}"
        bucket = grouped.setdefault(field, {}).setdefault(
            value_key, {"value": numeric, "unit": unit, "families": set(), "source_ids": set()}
        )
        bucket["families"].add(family_by_source[source_id])
        bucket["source_ids"].add(source_id)
    accepted: dict[str, dict[str, Any]] = {}
    for field, buckets in grouped.items():
        # A field is auto-accepted only when exactly one value bucket clears the
        # family bar.  If two different values each reach it, that is a conflict,
        # not corroboration — leave it for the caller to record as missing.
        clearing = [
            b for b in buckets.values()
            if len(b["families"]) >= _MIN_COROBORATING_FAMILIES
        ]
        if len(clearing) != 1:
            continue
        winner = clearing[0]
        accepted[field] = {
            "value": winner["value"],
            "unit": winner["unit"],
            "families": sorted(winner["families"]),
            "source_ids": sorted(winner["source_ids"]),
            "assurance": "estimate_preview",
        }
    return accepted


def build_evidence_pack(
    workspace_id: str,
    task_id: str,
    selected_source_ids: list[str] | None,
    fact_candidates: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    expected_fields: list[str] | None = None,
    candidate_set_id: str = "",
    selected_candidate_ids: list[str] | None = None,
    evidence_track: str = "real",
    fixture_manifest: dict[str, Any] | None = None,
    reconstruction_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence_track = str(evidence_track or "real").strip()
    if evidence_track not in EVIDENCE_TRACKS:
        return _missing("evidence_track_invalid", "evidence_track 必须为 real、technical_fixture 或 controlled_assumption")
    if evidence_track != "technical_fixture" and fixture_manifest:
        return _missing("fixture_manifest_not_applicable", "fixture_manifest 仅允许用于 technical_fixture 轨")
    normalized_reconstructions = [normalize_reconstruction(item) for item in (reconstruction_records or [])]
    reconstruction_errors = validate_reconstruction_records(reconstruction_records) if evidence_track == SOURCE_RECONSTRUCTED else []
    if evidence_track == SOURCE_RECONSTRUCTED and reconstruction_errors:
        return _missing("source_reconstruction_invalid", "source_reconstructed 必须提供完整来源重建记录", field_errors=reconstruction_errors)
    if evidence_track != SOURCE_RECONSTRUCTED and reconstruction_records:
        return _missing("reconstruction_records_not_applicable", "reconstruction_records 仅允许用于 source_reconstructed 轨")
    documents = _documents_from_task(
        workspace_id,
        task_id,
    )
    if not documents:
        return _missing("analysis_task_not_found", "没有可固化的分析任务")
    if selected_source_ids == []:
        return _missing("no_selected_sources", "selected_source_ids 显式为空，不会自动回退到全部来源")
    selected = (
        documents
        if selected_source_ids is None
        else [doc for doc in documents if doc.get("source_id") in selected_source_ids]
    )
    if not selected:
        return _missing("no_selected_sources", "未选择有效来源")
    # Formal evidence candidates must be selected from an immutable candidate
    # set produced by this service.  Caller-authored candidate objects remain a
    # compatibility surface for estimate_preview only and can never acquire
    # formal evidence standing merely by being copied into a pack.
    candidate_set = None
    server_signed_candidates = False
    if candidate_set_id:
        candidate_set = CANDIDATE_STORE.get(
            workspace_id,
            candidate_set_id,
        )
        candidate_payload = (candidate_set or {}).get("payload") or {}
        if (
            candidate_set is None
            or str(candidate_payload.get("analysis_task_id") or "") != task_id
        ):
            return _missing("candidate_set_not_found", "未找到属于该分析任务的候选事实集")
        available = {
            str(item.get("candidate_id") or ""): item
            for item in candidate_payload.get("fact_candidates") or []
            if isinstance(item, dict) and item.get("candidate_id")
        }
        requested_ids = [str(item) for item in (selected_candidate_ids or []) if str(item)]
        if requested_ids:
            unknown = sorted(set(requested_ids) - set(available))
            if unknown:
                return _missing("candidate_not_found", "候选事实不存在或不属于指定候选集")
            fact_candidates = [dict(available[item]) for item in requested_ids]
        else:
            fact_candidates = [dict(item) for item in available.values()]
        server_signed_candidates = True
    elif selected_candidate_ids:
        return _missing("candidate_set_required", "selected_candidate_ids 必须与 candidate_set_id 一起使用")

    selected_ids = {str(doc.get("source_id") or "") for doc in selected}
    if server_signed_candidates and any(
        str(item.get("source_id") or "") not in selected_ids
        for item in fact_candidates
        if isinstance(item, dict)
    ):
        return _missing("candidate_source_not_selected", "候选事实来源不在选定来源集合中")

    limits = []
    for doc in selected:
        if evidence_track == "real" and doc.get("source_type") == "web_snapshot":
            limits.append(f"{doc.get('source_id')}: 公开网络候选，未自动升级为正式财务输入")
        if (
            evidence_track == "real"
            and doc.get("source_type") == "controlled_file"
            and not doc.get("formal_use_allowed")
        ):
            limits.append(f"{doc.get('source_id')}: 受控文件尚未具备正式使用资格")
    missing_fields = _missing_pack_fields(expected_fields or [], fact_candidates)
    # Estimate-grade auto-acceptance: fields where ≥N independent source families
    # agree on one gate-approved number.  This keeps upstream fully automatic for
    # rough sizing; it never grants formal delivery (that stays at the downstream
    # thirteen-table human node).  Fields that do not clear the bar are not filled.
    auto_accepted = _adjudicate_estimate_fields(selected, fact_candidates)
    formal_sources_ok = all(bool(doc.get("formal_use_allowed")) for doc in selected)
    formal_evidence_candidate = bool(
        evidence_track == "real"
        and
        server_signed_candidates
        and formal_sources_ok
        and not conflicts
        and not missing_fields
        and fact_candidates
        and all(
            isinstance(item, dict)
            and item.get("candidate_id")
            and item.get("source_id")
            and isinstance(item.get("locator"), dict)
            and item.get("formal_use_allowed") is True
            for item in fact_candidates
        )
    )
    source_reconstructed_candidate = bool(
        evidence_track == SOURCE_RECONSTRUCTED
        and normalized_reconstructions
        and not conflicts
        and not missing_fields
        and fact_candidates
    )
    normalized_fixture_manifest = None
    fixture_errors: list[str] = []
    technical_fixture_candidate = False
    if evidence_track == "technical_fixture":
        normalized_fixture_manifest, fixture_errors = _validate_fixture_manifest(
            fixture_manifest,
            selected,
            fact_candidates,
        )
        technical_fixture_candidate = bool(
            normalized_fixture_manifest
            and server_signed_candidates
            and not conflicts
            and not missing_fields
            and fact_candidates
            and all(
                isinstance(item, dict)
                and item.get("candidate_id")
                and item.get("source_id")
                and isinstance(item.get("locator"), dict)
                and item.get("locator")
                for item in fact_candidates
            )
        )
        if fixture_errors:
            limits.extend(fixture_errors)
    elif evidence_track == "controlled_assumption":
        limits.append("controlled_assumption: 受控假设只能用于 estimate_preview")
    reconstruction_by_source = {
        str(item.get("source_uri") or "").rsplit("/", 1)[-1]: item
        for item in normalized_reconstructions
    }
    candidate_locators_by_source: dict[str, list[Any]] = {}
    for candidate in fact_candidates:
        if not isinstance(candidate, dict) or not candidate.get("locator"):
            continue
        candidate_locators_by_source.setdefault(
            str(candidate.get("source_id") or ""),
            [],
        ).append(candidate["locator"])
    source_rows = []
    for doc in selected:
        row = {
            key: doc.get(key)
            for key in (
                "source_id", "source_type", "title", "url", "content_hash",
                "fetched_at", "status", "formal_use_allowed",
                "formal_use_decision", "ocr_formal_use_decision",
                "unresolved_low_confidence_locator_count", "locators",
                "content_origin", "provider", "provider_tool",
                "evidence_policy", "project_fact_certified",
            )
        }
        reconstruction = reconstruction_by_source.get(str(doc.get("source_id") or ""))
        if (
            evidence_track == SOURCE_RECONSTRUCTED
            and not row.get("locators")
        ):
            candidate_locators = candidate_locators_by_source.get(
                str(doc.get("source_id") or ""),
                [],
            )
            if candidate_locators:
                row["locators"] = candidate_locators
            elif reconstruction is not None and reconstruction.get("locator"):
                row["locators"] = [str(reconstruction["locator"])]
        source_rows.append(row)
    evidence_policy = (
        FORMAL_EVIDENCE
        if formal_evidence_candidate
        else SOURCE_RECONSTRUCTED
        if evidence_track == SOURCE_RECONSTRUCTED
        else evidence_track
    )
    project_fact_certified = project_fact_may_be_certified(
        evidence_policy,
        own_qualification_passed=formal_evidence_candidate,
    )
    payload = {
        "analysis_task_id": task_id,
        "evidence_track": evidence_track,
        "technical_fixture_candidate": technical_fixture_candidate,
        "fixture_manifest": normalized_fixture_manifest,
        "candidate_set_id": candidate_set_id or None,
        "server_signed_candidates": server_signed_candidates,
        "formal_evidence_candidate": formal_evidence_candidate,
        "source_reconstructed_candidate": source_reconstructed_candidate,
        "reconstruction_records": normalized_reconstructions,
        "evidence_policy": evidence_policy,
        "project_fact_certified": project_fact_certified,
        "reconstructed_source_ids": [str(item.get("reconstruction_id") or "") for item in normalized_reconstructions if item.get("reconstruction_id")],
        "unresolved_inputs": list(missing_fields),
        "release_limitations": sorted(set([*limits, *[str(value) for item in normalized_reconstructions for value in (item.get("limitations") or [])]])),
        "sources": source_rows,
        "fact_candidates": fact_candidates,
        "auto_accepted_estimate_fields": auto_accepted,
        "missing_fields": missing_fields,
        "conflicts": conflicts,
        "limitations": limits,
        "finance_boundary": "证据包不等于已确认 FinanceSpec；source_reconstructed 只表示基于现有项目资料重建，必须保留限制且不能认证项目事实；technical_fixture 仅验证技术链；controlled_assumption 只允许 estimate_preview。",
    }
    status_value = "partial" if conflicts or missing_fields else "ok"
    record = EVIDENCE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-analysis.analysis_build_evidence_pack",
        status=status_value,
        source_ids=[str(doc.get("source_id")) for doc in selected],
        basis=[{"source_id": doc.get("source_id"), "content_hash": doc.get("content_hash")} for doc in selected],
    )
    warnings = [*limits]
    if missing_fields:
        warnings.append("期望字段存在证据缺口，evidence pack 记为 partial")
    partial_reasons = [
        *[f"limitation:{item}" for item in limits],
        *[f"conflict:{item.get('field') or item.get('metric') or 'unknown'}" for item in conflicts],
        *[f"missing_field:{item.get('field') or 'unknown'}" for item in missing_fields],
    ]
    return {
        "success": status_value == "ok",
        "business_success": status_value == "ok",
        "system_success": True,
        "transport_success": True,
        "status": status_value,
        "data_completeness": "complete" if status_value == "ok" else "partial",
        "partial_reasons": partial_reasons,
        "evidence_pack_id": record["object_id"],
        "basis_hash": record["basis_hash"],
        "source_count": len(selected),
        "limitations": limits,
        "missing_fields": missing_fields,
        "auto_accepted_estimate_fields": auto_accepted,
        "formal_evidence_candidate": formal_evidence_candidate,
        "source_reconstructed_candidate": source_reconstructed_candidate,
        "reconstruction_records": normalized_reconstructions,
        "evidence_policy": evidence_policy,
        "project_fact_certified": project_fact_certified,
        "reconstructed_source_ids": [str(item.get("reconstruction_id") or "") for item in normalized_reconstructions if item.get("reconstruction_id")],
        "unresolved_inputs": list(missing_fields),
        "release_limitations": sorted(set([*limits, *[str(value) for item in normalized_reconstructions for value in (item.get("limitations") or [])]])),
        "technical_fixture_candidate": technical_fixture_candidate,
        "evidence_track": evidence_track,
        "fixture_manifest_hash": (
            sha256_json(normalized_fixture_manifest) if normalized_fixture_manifest else None
        ),
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": [],
        "next_actions": ["auto_accepted_estimate_fields 可直接喂 estimate_preview 匡算；正式交付仍走十三表人工节点"],
    }
