"""Deterministic validation for the signed Sim-A formal-promotion lineage."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from lvke_mcp.runtime.storage import require_safe_id, sha256_json

SIM_A_FORMAL = "sim_a_formal"
SIM_A_ORIGIN = "sim_a_template"


class FormalLineageError(RuntimeError):
    """A formal object is missing, unsigned, mixed, or has been tampered with."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> None:
    raise FormalLineageError(code, message)


def _stores():
    # Lazy import avoids coupling the runtime package to server initialization.
    from lvke_mcp.servers.lvke_zero_material_delivery._service.base import (
        PROMOTION_STORE,
        TEMPLATE_PACK_STORE,
    )

    return TEMPLATE_PACK_STORE, PROMOTION_STORE


def _verified_artifact_record(
    workspace_id: str,
    object_id: str,
    store: Any,
    *,
    expected_type: str,
) -> dict[str, Any]:
    record = store.get(workspace_id, object_id)
    if not isinstance(record, dict):
        _fail("formal_lineage_object_not_found", f"{expected_type} 不存在或不属于当前工作区")
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("object_type") != expected_type:
        _fail("formal_lineage_object_invalid", f"{expected_type} payload 无效")
    identity = store.preview_identity(workspace_id, payload)
    if not hmac.compare_digest(str(record.get("object_id") or ""), identity["object_id"]):
        _fail("formal_lineage_identity_mismatch", f"{expected_type} ID 与 payload 不一致")
    if not hmac.compare_digest(str(record.get("content_hash") or ""), sha256_json(payload)):
        _fail("formal_lineage_content_hash_mismatch", f"{expected_type} 内容哈希不一致")
    if "basis" not in record:
        _fail("formal_lineage_unsigned_history", f"{expected_type} 缺少可复算 basis，属于历史未签名对象")
    if not hmac.compare_digest(str(record.get("basis_hash") or ""), sha256_json(record["basis"])):
        _fail("formal_lineage_basis_hash_mismatch", f"{expected_type} 依据哈希不一致")
    if str(record.get("workspace_id") or "") != workspace_id:
        _fail("formal_lineage_workspace_mismatch", f"{expected_type} 工作区不一致")
    return record


def expected_promoted_files(template_payload: dict[str, Any]) -> list[dict[str, str]]:
    """Build the exact deterministic SourceFile set represented by a TemplatePack."""

    rows: list[dict[str, str]] = []
    seen_file_ids: set[str] = set()
    files = template_payload.get("files")
    if not isinstance(files, list) or not files:
        _fail("template_pack_files_missing", "TemplatePack 未包含可晋升文件")
    for item in files:
        if not isinstance(item, dict):
            _fail("template_pack_file_invalid", "TemplatePack 文件条目无效")
        text = item.get("text")
        if not isinstance(text, str):
            _fail("template_pack_file_invalid", "TemplatePack 文件正文无效")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        content_hash = "sha256:" + digest
        if not hmac.compare_digest(str(item.get("sha256") or ""), content_hash):
            _fail("template_pack_file_hash_mismatch", "TemplatePack 文件哈希与正文不一致")
        file_id = f"src_{digest[:24]}"
        if file_id in seen_file_ids:
            _fail("template_pack_file_set_invalid", "TemplatePack 包含重复内容文件")
        seen_file_ids.add(file_id)
        rows.append(
            {
                "file_id": file_id,
                "content_hash": content_hash,
                "requirement_id": str(item.get("requirement_id") or ""),
                "kind": str(item.get("kind") or ""),
            }
        )
    return sorted(rows, key=lambda row: row["file_id"])


def validate_template_pack(workspace_id: str, template_pack_id: str) -> dict[str, Any]:
    workspace_id = require_safe_id(workspace_id, "workspace_id")
    template_pack_id = require_safe_id(template_pack_id, "template_pack_id")
    template_store, _promotion_store = _stores()
    record = _verified_artifact_record(
        workspace_id,
        template_pack_id,
        template_store,
        expected_type="TemplatePack",
    )
    payload = dict(record["payload"])
    if payload.get("evidence_policy") != SIM_A_FORMAL or payload.get("evidence_origin") != SIM_A_ORIGIN:
        _fail("template_pack_policy_invalid", "TemplatePack 不是规范 Sim-A 拟定模板包")
    expected = expected_promoted_files(payload)
    requirement_ids = sorted({row["requirement_id"] for row in expected if row["requirement_id"]})
    declared_requirements = sorted({str(item) for item in payload.get("requirement_ids") or [] if str(item)})
    if requirement_ids != declared_requirements:
        _fail("template_pack_requirement_set_mismatch", "TemplatePack 需求集合与文件集合不一致")
    return {"record": record, "payload": payload, "promoted_files": expected}


def build_promotion_payload(
    template_payload: dict[str, Any],
    *,
    template_pack_id: str,
    responsible_party: str,
    confirmation_note: str,
    promoted_files: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "object_type": "FormalPromotion",
        "template_pack_id": template_pack_id,
        "template_pack_hash": sha256_json(template_payload),
        "assumption_package_id": template_payload.get("assumption_package_id"),
        "delivery_run_id": template_payload.get("delivery_run_id"),
        "intent_id": template_payload.get("intent_id"),
        "responsible_party": responsible_party,
        "confirmation_note": confirmation_note,
        "evidence_policy": SIM_A_FORMAL,
        "evidence_origin": SIM_A_ORIGIN,
        "promoted_files": sorted(promoted_files, key=lambda row: row["file_id"]),
        "file_ids": sorted(row["file_id"] for row in promoted_files),
        "requirement_ids": list(template_payload.get("requirement_ids") or []),
        # 显式记录所用报告配置的身份。``template_pack_hash`` 已经**间接**保护了它
        # （改配置 hash 会让 pack hash 变化），但间接保护回答不了"这个 promotion
        # 用的哪份配置"——审计得反查 TemplatePack 才知道。正式对象必须能自证。
        "report_profile": _report_profile_identity(template_payload),
        "release_not_invoked": True,
    }


#: promotion 里保留的配置身份字段。刻意只留身份与版本，不整份拷进来：
#: 配置正文属于 TemplatePack，promotion 只需要能指名它。
_PROFILE_IDENTITY_FIELDS = (
    "profile_id",
    "template_set_id",
    "profile_version",
    "profile_content_hash",
    "profile_manifest_hash",
)


def _report_profile_identity(template_payload: dict[str, Any]) -> dict[str, str]:
    """Project the frozen report-profile identity carried by a TemplatePack."""

    profile = template_payload.get("report_profile")
    if not isinstance(profile, dict):
        return {}
    return {
        field: str(profile.get(field) or "")
        for field in _PROFILE_IDENTITY_FIELDS
        if profile.get(field)
    }


def _source_file_record(workspace_id: str, file_id: str) -> dict[str, Any]:
    from lvke_mcp.adapters import source_files_repository as source_files

    state = source_files._load_state(workspace_id)  # noqa: SLF001
    record = (state.get("files") or {}).get(file_id)
    if not isinstance(record, dict) or str(record.get("workspace_id") or "") != workspace_id:
        _fail("formal_source_not_found", f"正式 SourceFile 不存在或不属于当前工作区: {file_id}")
    source_path = Path(str(record.get("path") or ""))
    if not source_path.is_file():
        _fail("formal_source_content_missing", f"正式 SourceFile 内容缺失: {file_id}")
    data = source_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if (
        file_id != f"src_{digest[:24]}"
        or str(record.get("sha256") or "").removeprefix("sha256:") != digest
        or int(record.get("size_bytes") or -1) != len(data)
    ):
        _fail("formal_source_hash_mismatch", f"正式 SourceFile 内容已变化: {file_id}")
    return record


def validate_promoted_source_file(
    workspace_id: str,
    file_id: str,
) -> dict[str, Any]:
    """Validate one SourceFile and return its complete canonical promotion."""

    workspace_id = require_safe_id(workspace_id, "workspace_id")
    file_id = require_safe_id(file_id, "file_id")
    source = _source_file_record(workspace_id, file_id)
    if source.get("evidence_policy") != SIM_A_FORMAL:
        _fail("formal_source_policy_required", "SourceFile 不是 sim_a_formal 正式来源")
    promotion_id = str(source.get("formal_promotion_id") or "")
    if not promotion_id:
        _fail("formal_lineage_unsigned_history", "正式 SourceFile 缺少 promotion_id")
    canonical = validate_formal_promotion(workspace_id, promotion_id)
    descriptors = {
        str(row.get("file_id") or ""): row
        for row in canonical["formal_promotion"]["promoted_files"]
    }
    if file_id not in descriptors:
        _fail("formal_source_binding_mismatch", "SourceFile 不属于其声明的 promotion 文件集合")
    return canonical


def validate_immutable_record(
    workspace_id: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Recompute the common immutable-record envelope used by downstream stores."""

    workspace_id = require_safe_id(workspace_id, "workspace_id")
    if not isinstance(record, dict):
        _fail("formal_lineage_object_invalid", "正式父对象记录无效")
    if str(record.get("workspace_id") or "") != workspace_id:
        _fail("formal_lineage_workspace_mismatch", "正式父对象工作区不一致")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        _fail("formal_lineage_object_invalid", "正式父对象 payload 无效")
    content_hash = sha256_json(payload)
    if not hmac.compare_digest(str(record.get("content_hash") or ""), content_hash):
        _fail("formal_lineage_content_hash_mismatch", "正式父对象内容哈希不一致")
    object_id = str(record.get("object_id") or "")
    if not object_id.endswith("_" + content_hash.removeprefix("sha256:")[:24]):
        _fail("formal_lineage_identity_mismatch", "正式父对象 ID 与 payload 不一致")
    if "basis" not in record:
        _fail("formal_lineage_unsigned_history", "正式父对象缺少可复算 basis，历史对象失败关闭")
    if not hmac.compare_digest(str(record.get("basis_hash") or ""), sha256_json(record["basis"])):
        _fail("formal_lineage_basis_hash_mismatch", "正式父对象依据哈希不一致")
    return record


def validate_formal_promotion(
    workspace_id: str,
    promotion_id: str,
    *,
    expected_file_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate the complete promotion and return canonical propagation metadata."""

    workspace_id = require_safe_id(workspace_id, "workspace_id")
    promotion_id = require_safe_id(promotion_id, "promotion_id")
    _template_store, promotion_store = _stores()
    record = _verified_artifact_record(
        workspace_id,
        promotion_id,
        promotion_store,
        expected_type="FormalPromotion",
    )
    payload = dict(record["payload"])
    template_pack_id = str(payload.get("template_pack_id") or "")
    template = validate_template_pack(workspace_id, template_pack_id)
    if not hmac.compare_digest(
        str(payload.get("template_pack_hash") or ""),
        sha256_json(template["payload"]),
    ):
        _fail("formal_promotion_template_hash_mismatch", "FormalPromotion 与 TemplatePack 哈希不一致")
    promoted_files = template["promoted_files"]
    declared = payload.get("promoted_files")
    if not isinstance(declared, list) or sorted(declared, key=lambda row: str(row.get("file_id") or "")) != promoted_files:
        _fail("formal_promotion_file_set_mismatch", "FormalPromotion 文件集合与 TemplatePack 不精确相等")
    canonical_payload = build_promotion_payload(
        template["payload"],
        template_pack_id=template_pack_id,
        responsible_party=str(payload.get("responsible_party") or ""),
        confirmation_note=str(payload.get("confirmation_note") or ""),
        promoted_files=promoted_files,
    )
    if canonical_payload != payload:
        _fail("formal_promotion_payload_mismatch", "FormalPromotion 不是规范化 promotion payload")
    expected_set = {row["file_id"] for row in promoted_files}
    if expected_file_ids is not None and {str(item) for item in expected_file_ids} != expected_set:
        _fail("formal_promotion_file_set_mismatch", "调用对象绑定的 SourceFile 集合与 promotion 不精确相等")
    for descriptor in promoted_files:
        source = _source_file_record(workspace_id, descriptor["file_id"])
        source_hash = "sha256:" + str(source.get("sha256") or "").removeprefix("sha256:")
        if source_hash != descriptor["content_hash"]:
            _fail("formal_source_hash_mismatch", "SourceFile 哈希与 promotion 不一致")
        if (
            source.get("evidence_policy") != SIM_A_FORMAL
            or source.get("evidence_origin") != SIM_A_ORIGIN
            or source.get("project_fact_certified") is not True
            or source.get("formal_promotion_id") != promotion_id
            or source.get("template_pack_id") != template_pack_id
            or source.get("requirement_id") != descriptor["requirement_id"]
            or source.get("kind") != descriptor["kind"]
        ):
            _fail("formal_source_binding_mismatch", "SourceFile 缺少规范 promotion 父级绑定")
    return {
        "evidence_policy": SIM_A_FORMAL,
        "evidence_origin": SIM_A_ORIGIN,
        "project_fact_certified": True,
        "formal_promotion": {
            "promotion_id": promotion_id,
            "template_pack_id": template_pack_id,
            "promotion_hash": str(record.get("content_hash") or ""),
            "promoted_files": promoted_files,
        },
    }


def promotion_id_from(value: dict[str, Any]) -> str:
    nested = value.get("formal_promotion")
    if isinstance(nested, dict) and nested.get("promotion_id"):
        return str(nested["promotion_id"])
    return str(value.get("promotion_id") or value.get("formal_promotion_id") or "")


def validate_object_formal_lineage(
    workspace_id: str,
    value: dict[str, Any],
    *,
    expected_file_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Fail closed for any Sim-A object lacking a verifiable promotion parent."""

    payload = value.get("payload") if isinstance(value.get("payload"), dict) else value
    if str(payload.get("evidence_policy") or payload.get("evidence_track") or "") != SIM_A_FORMAL:
        _fail("formal_lineage_policy_required", "对象不是 sim_a_formal 正式策略")
    promotion_id = promotion_id_from(payload)
    if not promotion_id:
        _fail("formal_lineage_unsigned_history", "sim_a_formal 对象缺少 promotion_id，历史对象失败关闭")
    canonical = validate_formal_promotion(
        workspace_id,
        promotion_id,
        expected_file_ids=expected_file_ids,
    )
    stored = {
        field: payload.get(field)
        for field in (
            "evidence_policy",
            "evidence_origin",
            "project_fact_certified",
            "formal_promotion",
        )
    }
    if stored != canonical:
        _fail("formal_lineage_metadata_mismatch", "对象持久化的正式谱系元数据与规范值不一致")
    return canonical


def validate_formal_record(
    workspace_id: str,
    record: dict[str, Any],
    *,
    expected_file_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate an immutable downstream record and its canonical promotion."""

    verified = validate_immutable_record(workspace_id, record)
    return validate_object_formal_lineage(
        workspace_id,
        verified,
        expected_file_ids=expected_file_ids,
    )


def validate_same_formal_lineage(
    workspace_id: str,
    parents: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Require every immutable parent to carry the same verified promotion."""

    if not parents:
        _fail("formal_lineage_parent_required", "sim_a_formal 对象缺少正式父对象")
    canonical_rows = [validate_formal_record(workspace_id, parent) for parent in parents]
    promotion_ids = {
        str(row["formal_promotion"].get("promotion_id") or "")
        for row in canonical_rows
    }
    if len(promotion_ids) != 1:
        _fail("formal_lineage_mixed_promotions", "正式父对象来自不同 promotion")
    canonical = canonical_rows[0]
    if any(row != canonical for row in canonical_rows[1:]):
        _fail("formal_lineage_metadata_mismatch", "正式父对象的规范 promotion 元数据不一致")
    return canonical


def validate_research_package(
    workspace_id: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Validate a formal ResearchPackage and its immutable evidence parents."""

    validate_immutable_record(workspace_id, record)
    payload = record.get("payload")
    if not isinstance(payload, dict):
        _fail("formal_research_package_invalid", "正式 ResearchPackage payload 无效")
    status = str(payload.get("status") or record.get("status") or "")
    promotion_id = promotion_id_from(payload)
    if not promotion_id:
        _fail("formal_lineage_unsigned_history", "正式 ResearchPackage 缺少 promotion_id")
    canonical = validate_formal_promotion(workspace_id, promotion_id)
    stored = {
        field: payload.get(field)
        for field in (
            "evidence_policy",
            "evidence_origin",
            "formal_promotion",
        )
    }
    expected = {
        field: canonical[field]
        for field in (
            "evidence_policy",
            "evidence_origin",
            "formal_promotion",
        )
    }
    expected_certified = status == "completed"
    if stored != expected or payload.get("project_fact_certified") is not expected_certified:
        _fail(
            "formal_lineage_metadata_mismatch",
            "ResearchPackage 持久化的正式谱系或质量认证状态不规范",
        )
    artifacts = payload.get("agent_artifacts")
    evidence = artifacts.get("evidence") if isinstance(artifacts, dict) else None
    if not isinstance(evidence, dict):
        _fail("formal_research_parent_missing", "正式 ResearchPackage 缺少证据父级绑定")
    evidence_pack_ids = [
        str(item) for item in evidence.get("evidence_pack_ids") or [] if str(item)
    ]
    source_snapshot_ids = [
        str(item) for item in evidence.get("source_snapshot_ids") or [] if str(item)
    ]
    if not evidence_pack_ids or source_snapshot_ids:
        _fail(
            "formal_research_parent_invalid",
            "正式 ResearchPackage 必须仅由同一 promotion 的 EvidencePack 推导",
        )

    from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
    from lvke_mcp.adapters.research_repository import PACKAGE_STORE, QUALITY_REVIEW_STORE

    evidence_records = [EVIDENCE_STORE.get(workspace_id, item) for item in evidence_pack_ids]
    if any(not isinstance(item, dict) for item in evidence_records):
        _fail(
            "formal_evidence_pack_not_found",
            "ResearchPackage 绑定的 EvidencePack 不存在或不属于当前工作区",
        )
    parent_canonical = validate_same_formal_lineage(
        workspace_id,
        [item for item in evidence_records if isinstance(item, dict)],
    )
    if parent_canonical != canonical:
        _fail("formal_lineage_mixed_promotions", "ResearchPackage 与 EvidencePack promotion 不一致")

    task_id = str(payload.get("task_id") or "")
    if status == "partial":
        basis = record.get("basis")
        if not isinstance(basis, dict):
            _fail("formal_research_basis_mismatch", "正式 ResearchPackage 提交依据无效")
        artifact_sources = artifacts.get("sources") if isinstance(artifacts, dict) else None
        if (
            str(basis.get("task_id") or "") != task_id
            or [str(item) for item in basis.get("evidence_pack_ids") or []] != evidence_pack_ids
            or list(basis.get("source_snapshot_ids") or [])
            or basis.get("citations") != artifact_sources
            or payload.get("upstream_project_fact_certified") is not True
            or set(str(item) for item in record.get("source_ids") or [])
            != {task_id, *evidence_pack_ids}
        ):
            _fail(
                "formal_research_basis_mismatch",
                "正式 ResearchPackage 的提交依据与 EvidencePack/引用绑定不一致",
            )
        return canonical

    if status != "completed":
        _fail("formal_research_status_invalid", "正式 ResearchPackage 状态不可用于下游")
    basis = record.get("basis")
    expected_basis_fields = {
        "parent_package_id",
        "parent_content_hash",
        "parent_basis_hash",
        "quality_review_id",
        "quality_review_content_hash",
        "quality_review_basis_hash",
    }
    if not isinstance(basis, dict) or set(basis) != expected_basis_fields:
        _fail("formal_research_basis_mismatch", "已确认 ResearchPackage 缺少规范父级依据")
    parent_id = str(basis.get("parent_package_id") or "")
    review_id = str(basis.get("quality_review_id") or "")
    if (
        not parent_id
        or not review_id
        or str(payload.get("quality_review_id") or "") != review_id
    ):
        _fail("formal_research_parent_missing", "已确认 ResearchPackage 缺少父包或质量审查")
    parent = PACKAGE_STORE.get(workspace_id, parent_id)
    review = QUALITY_REVIEW_STORE.get(workspace_id, review_id)
    if not isinstance(parent, dict) or not isinstance(review, dict):
        _fail("formal_research_parent_not_found", "ResearchPackage 父包或质量审查不存在")
    if basis != {
        "parent_package_id": parent_id,
        "parent_content_hash": parent.get("content_hash"),
        "parent_basis_hash": parent.get("basis_hash"),
        "quality_review_id": review_id,
        "quality_review_content_hash": review.get("content_hash"),
        "quality_review_basis_hash": review.get("basis_hash"),
    }:
        _fail("formal_research_basis_mismatch", "已确认 ResearchPackage 父级哈希绑定不一致")
    if str((parent.get("payload") or {}).get("status") or "") != "partial":
        _fail("formal_research_parent_mismatch", "已确认 ResearchPackage 的父包不是 partial 提交")
    if validate_research_package(workspace_id, parent) != canonical:
        _fail("formal_lineage_mixed_promotions", "ResearchPackage 父子 promotion 不一致")
    validate_immutable_record(workspace_id, review)
    review_payload = review.get("payload") if isinstance(review.get("payload"), dict) else {}
    review_canonical = validate_object_formal_lineage(workspace_id, review_payload)
    if (
        review_canonical != canonical
        or str(review_payload.get("research_package_id") or "") != parent_id
        or str(review_payload.get("task_id") or "") != task_id
        or str(review_payload.get("status") or "")
        != str(payload.get("quality_review_status") or "")
        or review_payload.get("quality") != payload.get("quality")
        or set(str(item) for item in review.get("source_ids") or []) != {parent_id}
        or set(str(item) for item in record.get("source_ids") or [])
        != {parent_id, review_id, task_id}
    ):
        _fail(
            "formal_research_parent_mismatch",
            "已确认 ResearchPackage 与父包或 QualityReview 绑定不一致",
        )
    parent_evidence = ((parent.get("payload") or {}).get("agent_artifacts") or {}).get("evidence")
    if parent_evidence != evidence:
        _fail("formal_research_parent_mismatch", "已确认 ResearchPackage 改变了证据父级集合")
    return canonical


def validate_finance_run(
    workspace_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Validate a formal FinanceRun snapshot and its exact Spec/BoE parents."""

    workspace_id = require_safe_id(workspace_id, "workspace_id")
    run_id = require_safe_id(run_id, "run_id")
    from lvke_mcp.adapters.finance_model_repository import BASIS_OF_ESTIMATE_STORE, SPEC_STORE
    from lvke_mcp.domains.finance import run_store

    record = run_store.load_run_record(workspace_id, run_id)
    if not record or str(record.get("workspace_id") or "") != workspace_id:
        _fail("formal_finance_run_not_found", "FinanceRun 不存在或不属于当前工作区")
    snapshot = record.get("_result_snapshot")
    if not isinstance(snapshot, dict):
        _fail("formal_lineage_unsigned_history", "正式 FinanceRun 缺少不可变结果快照")
    snapshot_hash = run_store.finance_run_snapshot_hash(snapshot)
    # 「没有 content_hash」与「content_hash 不一致」是两种完全不同的处置，必须分码。
    # content_hash 只在 evidence_policy == "sim_a_formal" 时写入
    # （run_store.py 的 formal_run 分支），普通 run 从不写。此前两种情况共用
    # 一个"快照哈希不一致"，于是「这个 run 还没晋升成正式对象」被报成篡改告警：
    # 排查方向被系统性带偏（会去查序列化/哈希算法），而真实处置是先晋升或改用
    # external 模式做专项审查。
    stored_content_hash = str(record.get("content_hash") or "")
    if not stored_content_hash:
        _fail(
            "formal_finance_run_not_promoted",
            "FinanceRun 不是 sim_a_formal 正式对象（无 content_hash），"
            "内部套件只受理已晋升的正式谱系；"
            "请先完成正式晋升，或改用 review_mode=external 做专项审查",
        )
    if not hmac.compare_digest(stored_content_hash, snapshot_hash):
        _fail("formal_finance_run_content_hash_mismatch", "FinanceRun 快照哈希不一致")
    if run_id != f"run_{snapshot_hash.removeprefix('sha256:')[:24]}":
        _fail("formal_finance_run_identity_mismatch", "FinanceRun ID 与结果快照不一致")
    if "basis" not in record:
        _fail("formal_lineage_unsigned_history", "正式 FinanceRun 缺少可复算 basis")
    if not hmac.compare_digest(str(record.get("basis_hash") or ""), sha256_json(record["basis"])):
        _fail("formal_finance_run_basis_hash_mismatch", "FinanceRun 依据哈希不一致")

    canonical = validate_object_formal_lineage(workspace_id, snapshot)
    spec_id = str(snapshot.get("spec_id") or "")
    boe_id = str(snapshot.get("basis_of_estimate_id") or "")
    if not spec_id or not boe_id:
        _fail("formal_finance_run_parent_missing", "正式 FinanceRun 缺少 FinanceSpec 或 BoE 父对象")
    spec_record = SPEC_STORE.get(workspace_id, spec_id)
    boe_record = BASIS_OF_ESTIMATE_STORE.get(workspace_id, boe_id)
    parent_canonical = validate_same_formal_lineage(
        workspace_id,
        [spec_record or {}, boe_record or {}],
    )
    if parent_canonical != canonical:
        _fail("formal_lineage_metadata_mismatch", "FinanceRun 与 Spec/BoE promotion 不一致")
    spec_payload = (spec_record or {}).get("payload") or {}
    boe_payload = (boe_record or {}).get("payload") or {}
    if str(boe_payload.get("spec_id") or "") != spec_id:
        _fail("formal_finance_run_parent_mismatch", "BoE 未绑定 FinanceRun 的 FinanceSpec")
    parent_spec = spec_payload.get("spec")
    if not isinstance(parent_spec, dict):
        _fail("formal_finance_run_parent_mismatch", "FinanceSpec 缺少可复算的规范 spec")
    expected_spec_hash = "sha256:" + hashlib.sha256(
        json.dumps(
            parent_spec,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        str(spec_payload.get("spec_hash") or "") != expected_spec_hash
        or str(((spec_record or {}).get("basis") or {}).get("spec_hash") or "")
        != expected_spec_hash
        or str(boe_payload.get("spec_hash") or "") != expected_spec_hash
        or str(((boe_record or {}).get("basis") or {}).get("spec_basis_hash") or "")
        != str((spec_record or {}).get("basis_hash") or "")
    ):
        _fail("formal_finance_run_parent_mismatch", "FinanceSpec 或 BoE 的 spec 哈希绑定不一致")
    run_spec = snapshot.get("spec")
    run_spec_hash = (
        "sha256:" + hashlib.sha256(
            json.dumps(
                run_spec,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if isinstance(run_spec, dict)
        else ""
    )
    if run_spec_hash != expected_spec_hash:
        _fail("formal_finance_run_binding_mismatch", "FinanceRun 有效 spec 与父 FinanceSpec 不一致")
    expected_pairs = {
        "spec_id": spec_id,
        "spec_hash": expected_spec_hash,
        "basis_of_estimate_id": boe_id,
        "basis_of_estimate_hash": str((boe_record or {}).get("basis_hash") or ""),
        "evidence_policy": canonical["evidence_policy"],
        "evidence_origin": canonical["evidence_origin"],
        "project_fact_certified": canonical["project_fact_certified"],
        "formal_promotion": canonical["formal_promotion"],
    }
    for field, expected in expected_pairs.items():
        if snapshot.get(field) != expected:
            _fail("formal_finance_run_binding_mismatch", f"FinanceRun 字段 {field} 与父级谱系不一致")
        if field in record and record.get(field) != expected:
            _fail("formal_finance_run_binding_mismatch", f"FinanceRun 封装字段 {field} 与快照不一致")
    expected_basis = {
        "workspace_id": workspace_id,
        "spec_id": spec_id,
        "spec_hash": expected_pairs["spec_hash"],
        "basis_of_estimate_id": boe_id,
        "basis_of_estimate_hash": expected_pairs["basis_of_estimate_hash"],
        "input_hash": str(snapshot.get("input_hash") or ""),
        "table_bundle_hash": str(snapshot.get("table_bundle_hash") or ""),
        "result_snapshot_hash": snapshot_hash,
        "formal_promotion": canonical["formal_promotion"],
    }
    if record.get("basis") != expected_basis:
        _fail("formal_finance_run_basis_mismatch", "FinanceRun basis 与规范父级绑定不一致")
    return canonical


def validate_finance_tables_package(
    workspace_id: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Validate an immutable formal tables package and its FinanceRun parent."""

    validate_immutable_record(workspace_id, record)
    payload = record.get("payload") or {}
    run_id = str(payload.get("run_id") or "")
    canonical = validate_finance_run(workspace_id, run_id)
    stored = validate_object_formal_lineage(workspace_id, payload)
    if stored != canonical:
        _fail("formal_lineage_metadata_mismatch", "FinanceTablesPackage 与 FinanceRun promotion 不一致")
    for field in ("evidence_policy", "evidence_origin", "project_fact_certified", "formal_promotion"):
        if payload.get(field) != canonical[field]:
            _fail("formal_tables_package_binding_mismatch", f"FinanceTablesPackage 字段 {field} 不规范")
    return canonical
