"""证据目录与 claim 证据匹配：各证据轨候选、指标、口径与位置。"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable


from .normalize import (
    _canonical_unit,
    _canonical_value,
    _number,
    _semantic,
    _within_tolerance,
)


def _source_timestamp(source: dict[str, Any]) -> str:
    for key in ("fetched_at", "retrieved_at", "captured_at", "created_at"):
        if source.get(key):
            return str(source[key])
    for locator in source.get("locators") or []:
        if not isinstance(locator, dict):
            continue
        for key in ("fetched_at", "retrieved_at", "captured_at", "created_at"):
            if locator.get(key):
                return str(locator[key])
    return ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _evidence_catalog(packs: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for pack in packs:
        payload = pack.get("payload") or {}
        pack_id = str(pack.get("object_id") or pack.get("evidence_pack_id") or "")
        pack_candidate_set_id = str(payload.get("candidate_set_id") or "")
        pack_server_signed = payload.get("server_signed_candidates") is True
        pack_formal = payload.get("formal_evidence_candidate") is True
        pack_track = str(payload.get("evidence_policy") or payload.get("evidence_track") or "real")
        pack_fixture = payload.get("technical_fixture_candidate") is True
        pack_reconstructed = payload.get("source_reconstructed_candidate") is True
        fixture_manifest = payload.get("fixture_manifest") or {}
        by_source = {
            str(source.get("source_id") or ""): source
            for source in (payload.get("sources") or [])
            if isinstance(source, dict)
        }
        for source in by_source.values():
            sources.append({
                **deepcopy(source),
                "evidence_pack_id": pack_id,
                "_pack_candidate_set_id": pack_candidate_set_id,
                "_pack_server_signed_candidates": pack_server_signed,
                "_pack_formal_evidence_candidate": pack_formal,
                "_pack_evidence_track": pack_track,
                "_pack_technical_fixture_candidate": pack_fixture,
                "_pack_source_reconstructed_candidate": pack_reconstructed,
                "_pack_fixture_manifest": deepcopy(fixture_manifest),
            })
        for raw in payload.get("fact_candidates") or []:
            if not isinstance(raw, dict):
                continue
            source = by_source.get(str(raw.get("source_id") or "")) or {}
            candidates.append({
                **deepcopy(raw),
                "evidence_policy": raw.get("evidence_policy") or source.get("evidence_policy") or payload.get("evidence_policy"),
                "evidence_pack_id": pack_id,
                "source": deepcopy(source),
                "_pack_candidate_set_id": pack_candidate_set_id,
                "_pack_server_signed_candidates": pack_server_signed,
                "_pack_formal_evidence_candidate": pack_formal,
                "_pack_evidence_track": pack_track,
                "_pack_technical_fixture_candidate": pack_fixture,
                "_pack_source_reconstructed_candidate": pack_reconstructed,
                "_pack_fixture_manifest": deepcopy(fixture_manifest),
            })
    return candidates, sources


def _sim_a_formal_candidate(candidate: dict[str, Any]) -> bool:
    source = candidate.get("source") or {}
    content_hash = str(source.get("content_hash") or source.get("sha256") or "")
    policy = str(
        candidate.get("evidence_policy")
        or source.get("evidence_policy")
        or candidate.get("_pack_evidence_track")
        or ""
    )
    return bool(
        policy == "sim_a_formal"
        and re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", content_hash)
        and (
            isinstance(candidate.get("locator"), dict)
            or candidate.get("locator")
            or source.get("locators")
        )
    )


def _formal_evidence_candidate(candidate: dict[str, Any]) -> bool:
    source = candidate.get("source") or {}
    content_hash = str(source.get("content_hash") or "")
    return bool(
        candidate.get("_pack_candidate_set_id")
        and candidate.get("_pack_server_signed_candidates") is True
        and candidate.get("_pack_formal_evidence_candidate") is True
        and candidate.get("formal_use_allowed") is True
        and source.get("formal_use_allowed") is True
        and re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", content_hash)
        and isinstance(candidate.get("locator"), dict)
        and candidate.get("locator")
    )


def _formal_evidence_source(source: dict[str, Any]) -> bool:
    content_hash = str(source.get("content_hash") or "")
    locators = source.get("locators") or []
    return bool(
        source.get("_pack_candidate_set_id")
        and source.get("_pack_server_signed_candidates") is True
        and source.get("_pack_formal_evidence_candidate") is True
        and source.get("formal_use_allowed") is True
        and re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", content_hash)
        and isinstance(locators, list)
        and locators
    )


def _technical_fixture_candidate(candidate: dict[str, Any]) -> bool:
    source = candidate.get("source") or {}
    manifest = candidate.get("_pack_fixture_manifest") or {}
    content_hash = str(source.get("content_hash") or "")
    source_id = str(candidate.get("source_id") or "")
    manifest_hashes = manifest.get("content_hashes") or {}
    return bool(
        candidate.get("_pack_evidence_track") == "technical_fixture"
        and candidate.get("_pack_server_signed_candidates") is True
        and candidate.get("_pack_technical_fixture_candidate") is True
        and candidate.get("_pack_formal_evidence_candidate") is not True
        and source_id in set(manifest.get("source_snapshot_ids") or [])
        and str(manifest_hashes.get(source_id) or "").removeprefix("sha256:")
        == content_hash.removeprefix("sha256:")
        and re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", content_hash)
        and isinstance(candidate.get("locator"), dict)
        and candidate.get("locator")
    )


def _source_reconstructed_candidate(candidate: dict[str, Any]) -> bool:
    """Qualify a source-reconstructed fact for process acceptance only."""

    source = candidate.get("source") or {}
    content_hash = str(source.get("content_hash") or "")
    return bool(
        candidate.get("_pack_evidence_track") == "source_reconstructed"
        and candidate.get("_pack_source_reconstructed_candidate") is True
        and re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", content_hash)
        and isinstance(candidate.get("locator"), dict)
        and candidate.get("locator")
    )


def _candidate_metric(candidate: dict[str, Any]) -> str:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("field", "metric", "matched_alias", "excerpt")
    )
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", text.lower())
    if re.search(r"(?:room.*count|count.*room|hotel.*room|guest.*room)|(?:客房|房间)(?:数|数量|总数)", normalized):
        return "room_count"
    if re.search(r"(?:market|transport|service).*(?:radius|distance)|(?:radius|distance).*(?:market|transport|service)|市场半径|运输半径|辐射半径|经营范围", normalized):
        return "market_radius"
    if re.search(r"(?:asset|land|building|construction|property).*(?:area|scale)|(?:area|scale).*(?:asset|land|building|construction|property)|资产边界面积|土地面积|建筑面积|建设规模", normalized):
        return "area"
    return _semantic(text)


def _candidate_scope(candidate: dict[str, Any], metric: str) -> str:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("field", "metric", "matched_alias")
    ).lower()
    if metric == "room_count":
        return "total"
    if metric != "area":
        return ""
    if "asset_boundary" in text or "资产边界" in text or "收购范围" in text:
        return "asset_boundary"
    if "land" in text or "土地" in text or "占地" in text or "用地" in text:
        return "land"
    if "construction" in text or "施工" in text or "建设规模" in text:
        return "construction"
    if "building" in text or "property" in text or "建筑" in text or "房屋" in text:
        return "building"
    return "area"


def _candidate_location(candidate: dict[str, Any], *, target_id: str) -> dict[str, Any]:
    source = candidate.get("source") or {}
    return {
        "target_id": target_id,
        "evidence_pack_id": candidate.get("evidence_pack_id"),
        "source_id": candidate.get("source_id"),
        "candidate_id": candidate.get("candidate_id"),
        "content_hash": source.get("content_hash"),
        "locator": deepcopy(candidate.get("locator") or {}),
        "text_anchor": str(
            candidate.get("excerpt") or candidate.get("original_value") or ""
        )[:160],
    }


def _evidence_claims(
    candidates: list[dict[str, Any]], *, target_id: str,
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for candidate in candidates:
        if not (
            _formal_evidence_candidate(candidate)
            or _source_reconstructed_candidate(candidate)
            or _sim_a_formal_candidate(candidate)
        ):
            continue
        numeric = _number(candidate.get("numeric_value"))
        metric = _candidate_metric(candidate)
        raw_unit = str(candidate.get("expected_unit") or "")
        if numeric is None or not metric or not raw_unit:
            continue
        unit = _canonical_unit(raw_unit)
        value = _canonical_value(numeric, raw_unit)
        claims.append({
            "claim_id": str(candidate.get("candidate_id") or ""),
            "text": str(candidate.get("excerpt") or candidate.get("original_value") or ""),
            "context": str(candidate.get("excerpt") or candidate.get("original_value") or ""),
            "claim_type": "operating",
            "metric": metric,
            "value": value,
            "unit": unit,
            "raw_value": numeric,
            "raw_unit": raw_unit,
            "period": "",
            "location": _candidate_location(candidate, target_id=target_id),
            "source_kind": "evidence",
            "source_id": str(candidate.get("source_id") or ""),
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "evidence_scope": _candidate_scope(candidate, metric),
        })
    return claims


def _candidate_matches_claim(
    candidate: dict[str, Any], claim: dict[str, Any], *, evidence_track: str = "real",
) -> bool:
    numeric = _number(candidate.get("numeric_value"))
    qualified = (
        _technical_fixture_candidate(candidate)
        if evidence_track == "technical_fixture"
        else _source_reconstructed_candidate(candidate)
        if evidence_track == "source_reconstructed"
        else _sim_a_formal_candidate(candidate)
        if evidence_track == "sim_a_formal"
        else _formal_evidence_candidate(candidate)
    )
    if numeric is None or not qualified:
        return False
    candidate_unit = _canonical_unit(str(candidate.get("expected_unit") or ""))
    claim_unit = str(claim.get("unit") or "")
    if candidate_unit and candidate_unit != claim_unit:
        return False
    candidate_value = _canonical_value(numeric, str(candidate.get("expected_unit") or ""))
    candidate_metric = _candidate_metric(candidate)
    claim_metric = str(claim.get("metric") or "")
    if claim_metric and candidate_metric and claim_metric != candidate_metric:
        return False
    return _within_tolerance(float(claim["value"]), candidate_value, metric=claim_metric)


def _claim_evidence(
    claim: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    exclude_candidate_id: str = "",
    exclude_source_id: str = "",
    evidence_track: str = "real",
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        if exclude_candidate_id and str(candidate.get("candidate_id") or "") == exclude_candidate_id:
            continue
        if exclude_source_id and str(candidate.get("source_id") or "") == exclude_source_id:
            continue
        if not _candidate_matches_claim(candidate, claim, evidence_track=evidence_track):
            continue
        source = candidate.get("source") or {}
        output.append({
            "evidence_pack_id": candidate.get("evidence_pack_id"),
            "source_id": candidate.get("source_id"),
            "url": source.get("url"),
            "locator": deepcopy(candidate.get("locator")),
            "content_hash": source.get("content_hash"),
            "fetched_at": _source_timestamp(source) or None,
            "candidate_id": candidate.get("candidate_id"),
        })
    return output
