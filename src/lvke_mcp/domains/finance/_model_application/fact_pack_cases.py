"""Fact Pack 三个用例：准备、确认与读取。"""

from __future__ import annotations

from typing import Any
import hashlib

from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
from lvke_mcp.adapters.finance_model_repository import FACT_PACK_STORE
from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    validate_formal_record,
    validate_promoted_source_file,
    validate_same_formal_lineage,
)
from lvke_mcp.runtime.responses import ok
from lvke_mcp.runtime.storage import sha256_json

from .base import (
    SERVER_NAME,
    _err_env,
    _ok_env,
    _workspace_id,
)


def _candidate_source_ids(candidate: dict[str, Any]) -> list[str]:
    return sorted({
        str(row.get("source_id") or row.get("source_snapshot_id") or row.get("file_id") or "")
        for row in candidate.get("evidence") or []
        if isinstance(row, dict)
        and str(row.get("source_id") or row.get("source_snapshot_id") or row.get("file_id") or "")
    })


def _sim_a_fact_pack_lineage(
    workspace_id: str,
    candidate: dict[str, Any],
    evidence_pack_ids: list[str],
) -> dict[str, Any]:
    if not evidence_pack_ids:
        raise FormalLineageError(
            "formal_evidence_pack_required",
            "sim_a_formal FinanceFactPack 必须绑定 EvidencePack",
        )
    evidence_records: list[dict[str, Any]] = []
    for evidence_pack_id in evidence_pack_ids:
        record = EVIDENCE_STORE.get(workspace_id, evidence_pack_id)
        if record is None:
            raise FormalLineageError(
                "formal_evidence_pack_not_found",
                f"EvidencePack 不存在或跨工作区: {evidence_pack_id}",
            )
        evidence_records.append(record)
    canonical = validate_same_formal_lineage(workspace_id, evidence_records)
    promoted_ids = {
        str(row.get("file_id") or "")
        for row in canonical["formal_promotion"]["promoted_files"]
    }
    source_ids = set(_candidate_source_ids(candidate))
    if not source_ids:
        raise FormalLineageError(
            "formal_fact_pack_source_required",
            "sim_a_formal FinanceFactPack 缺少 SourceFile 事实绑定",
        )
    if not source_ids <= promoted_ids:
        raise FormalLineageError(
            "formal_fact_pack_source_set_mismatch",
            "FinanceFactPack 引用了 promotion 之外的 SourceFile",
        )
    for source_id in sorted(source_ids):
        source_lineage = validate_promoted_source_file(workspace_id, source_id)
        if source_lineage != canonical:
            raise FormalLineageError(
                "formal_lineage_mixed_promotions",
                "FinanceFactPack SourceFile 与 EvidencePack 来自不同 promotion",
            )
    return canonical


def _build_evidence_resolver(candidate: dict[str, Any]):
    """Select the evidence resolver a candidate's policy requires.

    prepare 此前无条件使用默认权威解析器，只有 confirm 才在
    source_reconstructed 下切换到重建解析器，导致同一份 candidate 在两个
    阶段走不同解析路径、匹配叶子数不一致（prepare 为 0、confirm 为 1）。
    两处统一调用本函数，保证 prepare 的预览与 confirm 的判定同源。
    """
    policy = str(candidate.get("evidence_policy") or "formal_evidence")
    if policy == "sim_a_formal":
        def resolve_sim_a_formal(
            requested_workspace_id: str,
            *,
            source_id: str,
            evidence_id: str = "",
            locator: str = "",
        ) -> dict[str, Any]:
            from lvke_mcp.adapters import source_files_repository as source_files

            try:
                _state, record = source_files._require_source_record(  # noqa: SLF001
                    requested_workspace_id, source_id,
                )
            except source_files.SourceFileError:
                return {
                    "source_id": source_id,
                    "locator": locator,
                    "evidence_id": evidence_id or locator,
                    "authoritative": True,
                    "binding_ok": False,
                    "allow_claimed_value": False,
                    "issues": ["拟定稿 SourceFile 不存在"],
                }
            digest = str((record or {}).get("sha256") or "")
            exists = bool(digest or (record or {}).get("file_id"))
            if not exists:
                return {
                    "source_id": source_id,
                    "locator": locator,
                    "evidence_id": evidence_id or locator,
                    "authoritative": True,
                    "binding_ok": False,
                    "allow_claimed_value": False,
                    "issues": ["拟定稿 SourceFile 不存在"],
                }
            return {
                "source_id": source_id,
                "file_id": source_id,
                "evidence_id": evidence_id or locator,
                "locator": locator,
                "kind": "document",
                "evidence_grade": "B",
                "review_status": "approved",
                "validation_status": "passed",
                "source_sha256": digest,
                "authoritative": True,
                "binding_ok": True,
                "allow_claimed_value": True,
                "issues": [],
            }

        return resolve_sim_a_formal
    if policy != "source_reconstructed":
        return None
    records = [
        dict(row) for row in candidate.get("reconstruction_records") or []
        if isinstance(row, dict)
    ]
    by_binding: dict[tuple[str, str], dict[str, Any]] = {}
    for row in records:
        locator = str(row.get("locator") or "")
        source_uri = str(row.get("source_uri") or "")
        tail = source_uri.rsplit("/", 1)[-1]
        by_binding[(tail, locator)] = row

    def resolve_reconstruction(
        requested_workspace_id: str,
        *,
        source_id: str,
        evidence_id: str = "",
        locator: str = "",
    ) -> dict[str, Any]:
        del evidence_id
        from lvke_mcp.adapters.source_files_repository import (
            resolve_reconstructed_evidence_binding,
        )

        reconstruction = by_binding.get((str(source_id), str(locator)))
        if reconstruction is None:
            return {
                "source_id": source_id,
                "locator": locator,
                "evidence_grade": "B",
                "review_status": "missing",
                "authoritative": True,
                "binding_ok": False,
                "issues": ["来源重建记录不存在或 locator 不匹配"],
            }
        return resolve_reconstructed_evidence_binding(
            requested_workspace_id,
            source_id=source_id,
            locator=locator,
            reconstruction_record=reconstruction,
        )

    return resolve_reconstruction


def prepare_fact_pack(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_id(args)
    candidate = args.get("fact_pack") if isinstance(args.get("fact_pack"), dict) else None
    key = str(args.get("idempotency_key") or "").strip()
    if not workspace_id or candidate is None or not key:
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "workspace_id、fact_pack 与 idempotency_key 必填",
            status="blocked",
        )
    from lvke_mcp.domains.finance.fact_pack import build_fact_pack_snapshot
    from lvke_mcp.runtime.source_reconstruction import validate_reconstruction_records

    policy = str(candidate.get("evidence_policy") or "formal_evidence")
    evidence_pack_ids = sorted({str(item) for item in args.get("evidence_pack_ids") or [] if str(item)})
    canonical_lineage: dict[str, Any] = {}
    quality_issues: list[str] = []
    if policy == "sim_a_formal":
        try:
            canonical_lineage = _sim_a_fact_pack_lineage(
                workspace_id,
                candidate,
                evidence_pack_ids,
            )
        except FormalLineageError as exc:
            quality_issues.append(exc.code)
    if policy == "source_reconstructed":
        errors = validate_reconstruction_records(candidate.get("reconstruction_records"))
        if errors:
            return _ok_env(
                {"available": False, "field_errors": errors},
                source=f"{SERVER_NAME}.finance_prepare_fact_pack",
                status="missing_inputs",
                blockers=[],
                quality_issues=[
                    *quality_issues,
                    *sorted({str(row.get("code") or "reconstruction_invalid") for row in errors}),
                ],
                fact_pack_id=None,
                confirmation_status="draft",
            )
    fingerprint = sha256_json(candidate)
    key_hash = "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()
    for record in FACT_PACK_STORE.list(workspace_id):
        payload = record.get("payload") or {}
        if payload.get("prepare_idempotency_key_hash") != key_hash:
            continue
        if payload.get("candidate_fingerprint") != fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已用于不同 Fact Pack 候选",
                status="blocked",
            )
        return _fact_pack_result(record, replayed=True)
    snapshot = build_fact_pack_snapshot(
        candidate,
        workspace_id=workspace_id,
        confirm=False,
        evidence_resolver=_build_evidence_resolver(candidate),
    )
    record = FACT_PACK_STORE.put(
        workspace_id,
        {
            **canonical_lineage,
            "object_type": "FinanceFactPack",
            "confirmation_status": "draft",
            "fact_pack": snapshot,
            "raw_candidate": candidate,
            "candidate_fingerprint": fingerprint,
            "prepare_idempotency_key_hash": key_hash,
            "evidence_policy": policy,
            "evidence_pack_ids": evidence_pack_ids,
            "project_fact_certified": bool(canonical_lineage) if policy == "sim_a_formal" else bool(snapshot.get("project_fact_certified")),
            "reconstruction_records": list(snapshot.get("reconstruction_records") or []),
            "reconstructed_source_ids": list(snapshot.get("reconstructed_source_ids") or []),
            "unresolved_inputs": list(snapshot.get("unresolved_inputs") or []),
            "release_limitations": list(snapshot.get("release_limitations") or []),
            "quality_issues": quality_issues,
        },
        producer=f"{SERVER_NAME}.finance_prepare_fact_pack",
        status="draft",
        source_ids=evidence_pack_ids,
        basis={
            "candidate_fingerprint": fingerprint,
            "evidence_pack_ids": evidence_pack_ids,
            "formal_promotion": canonical_lineage.get("formal_promotion"),
        },
    )
    return _fact_pack_result(record, replayed=False)


def confirm_fact_pack(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_id(args)
    fact_pack_id = str(args.get("fact_pack_id") or "").strip()
    key = str(args.get("idempotency_key") or "").strip()
    if not workspace_id or not fact_pack_id or not key:
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "workspace_id、fact_pack_id 与 idempotency_key 必填",
            status="blocked",
        )
    source = FACT_PACK_STORE.get(workspace_id, fact_pack_id)
    if source is None:
        return _err_env(f"{SERVER_NAME}.fact_pack_not_found", "Fact Pack 不存在", status="blocked")
    source_payload = source.get("payload") or {}
    if source_payload.get("confirmation_status") == "confirmed":
        return _fact_pack_result(source, replayed=True)
    candidate = source_payload.get("raw_candidate")
    if not isinstance(candidate, dict):
        return _err_env(f"{SERVER_NAME}.fact_pack_invalid", "Fact Pack 候选快照无效", status="blocked")
    key_hash = "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()
    fingerprint = sha256_json({"fact_pack_id": fact_pack_id, "basis_hash": source.get("basis_hash")})
    for record in FACT_PACK_STORE.list(workspace_id):
        payload = record.get("payload") or {}
        if payload.get("confirm_idempotency_key_hash") != key_hash:
            continue
        if payload.get("confirmation_fingerprint") != fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已用于不同 Fact Pack 确认请求",
                status="blocked",
            )
        return _fact_pack_result(record, replayed=True)
    policy = str(candidate.get("evidence_policy") or "formal_evidence")
    evidence_pack_ids = [str(item) for item in source_payload.get("evidence_pack_ids") or [] if str(item)]
    canonical_lineage: dict[str, Any] = {}
    quality_issues: list[str] = []
    if policy == "sim_a_formal":
        try:
            validate_formal_record(workspace_id, source)
            canonical_lineage = _sim_a_fact_pack_lineage(
                workspace_id,
                candidate,
                evidence_pack_ids,
            )
        except FormalLineageError as exc:
            quality_issues.append(exc.code)
    # 与 prepare 共用同一解析器构造，避免两阶段解析路径分叉。
    resolver = _build_evidence_resolver(candidate)
    from lvke_mcp.domains.finance.fact_pack import build_fact_pack_snapshot

    confirmed = build_fact_pack_snapshot(
        candidate,
        workspace_id=workspace_id,
        confirm=True,
        evidence_resolver=resolver,
    )
    if confirmed.get("delivery_grade_ceiling") != "formal_candidate":
        quality_issues.extend(
            [
                "fact_pack_not_formal_candidate",
                *[str(item) for item in confirmed.get("missing") or []],
            ]
        )
    record = FACT_PACK_STORE.put(
        workspace_id,
        {
            **canonical_lineage,
            "object_type": "FinanceFactPack",
            "confirmation_status": "confirmed",
            "parent_fact_pack_id": fact_pack_id,
            "parent_object_ids": [fact_pack_id],
            "fact_pack": confirmed,
            "raw_candidate": candidate,
            "candidate_fingerprint": source_payload.get("candidate_fingerprint"),
            "confirmation_fingerprint": fingerprint,
            "confirm_idempotency_key_hash": key_hash,
            "evidence_policy": policy,
            "evidence_pack_ids": evidence_pack_ids,
            "project_fact_certified": bool(canonical_lineage) if policy == "sim_a_formal" else bool(confirmed.get("project_fact_certified")),
            "reconstruction_records": list(confirmed.get("reconstruction_records") or []),
            "reconstructed_source_ids": list(confirmed.get("reconstructed_source_ids") or []),
            "unresolved_inputs": list(confirmed.get("unresolved_inputs") or []),
            "release_limitations": list(confirmed.get("release_limitations") or []),
            "quality_issues": quality_issues,
        },
        producer=f"{SERVER_NAME}.finance_confirm_fact_pack",
        status="confirmed",
        source_ids=[fact_pack_id, *evidence_pack_ids],
        basis={
            "parent_basis_hash": source.get("basis_hash"),
            "fact_pack_hash": confirmed.get("fact_pack_hash"),
            "evidence_pack_ids": evidence_pack_ids,
            "formal_promotion": canonical_lineage.get("formal_promotion"),
        },
    )
    return _fact_pack_result(record, replayed=False)


def get_fact_pack(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_id(args)
    fact_pack_id = str(args.get("fact_pack_id") or "").strip()
    if not workspace_id or not fact_pack_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 与 fact_pack_id 必填")
    record = FACT_PACK_STORE.get(workspace_id, fact_pack_id)
    if record is None:
        return _err_env(f"{SERVER_NAME}.fact_pack_not_found", "Fact Pack 不存在", status="blocked")
    return _fact_pack_result(record, replayed=False)


def _fact_pack_result(record: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    payload = record.get("payload") or {}
    pack = payload.get("fact_pack") or {}
    quality_issues = [
        str(item) for item in payload.get("quality_issues") or [] if str(item)
    ]
    status = "partial" if quality_issues or payload.get("confirmation_status") != "confirmed" else "ok"
    return _ok_env(
        {
            "fact_pack_id": record.get("object_id"),
            "fact_pack": pack,
            "content_hash": record.get("content_hash"),
            "basis_hash": record.get("basis_hash"),
        },
        source=f"{SERVER_NAME}.finance_get_fact_pack",
        status=status,
        resource_uris=[str(record.get("resource_uri") or "")],
        warnings=[] if status == "ok" else ["Fact Pack 尚未确认"],
        blockers=[],
        quality_issues=quality_issues,
        next_actions=[] if status == "ok" else ["质量提示已保留，可继续使用 Fact Pack"],
        fact_pack_id=record.get("object_id"),
        confirmation_status=str(payload.get("confirmation_status") or "draft"),
        delivery_grade_ceiling=str(pack.get("delivery_grade_ceiling") or "summary"),
        fact_pack_hash=pack.get("fact_pack_hash"),
        depth_assessment=pack.get("depth_assessment") or {},
        binding_assessment=pack.get("binding_assessment") or {},
        replayed=replayed,
    )

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "EVIDENCE_STORE",
    "FACT_PACK_STORE",
    "FormalLineageError",
    "SERVER_NAME",
    "_build_evidence_resolver",
    "_candidate_source_ids",
    "_err_env",
    "_fact_pack_result",
    "_ok_env",
    "_sim_a_fact_pack_lineage",
    "_workspace_id",
    "confirm_fact_pack",
    "get_fact_pack",
    "hashlib",
    "ok",
    "prepare_fact_pack",
    "sha256_json",
    "validate_formal_record",
    "validate_promoted_source_file",
    "validate_same_formal_lineage",
]
