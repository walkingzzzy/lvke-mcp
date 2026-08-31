"""FinanceSpec 用例：准备、确认与校验；含候选输入归一化与收入完整性。"""

from __future__ import annotations

from typing import Any
import hashlib
import json

from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
from lvke_mcp.adapters.finance_model_repository import FACT_PACK_STORE, IDEMPOTENCY_STORE, SPEC_STORE
from lvke_mcp.domains.finance.generation_standard import (
    coverage_snapshot,
    generation_baseline,
    stamp_finance_spec,
)
from lvke_mcp.domains.finance.parameter_resolver import (
    canonicalize_finance_inputs,
    finance_input_schema,
)
from lvke_mcp.domains.finance.rail_validation import (
    rail_transit_missing_inputs as _rail_transit_missing_inputs,
    revenue_input_complete as _revenue_input_complete,
)
from lvke_mcp.runtime.evidence_qualification import project_fact_may_be_certified
from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    validate_formal_record,
    validate_same_formal_lineage,
)
from lvke_mcp.runtime.responses import ok
from lvke_mcp.runtime.storage import sha256_json

from .base import (
    SERVER_NAME,
    _active_idempotency_record,
    _err_env,
    _exception_env,
    _expires_at,
    _ok_env,
    _str_list,
    _unique_strings,
    _workspace_id,
)


def prepare_spec(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_id(args)
    if not workspace_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    fact_pack_id = str(args.get("fact_pack_id") or "").strip()
    fact_pack_record: dict[str, Any] | None = None
    fact_pack_payload: dict[str, Any] = {}
    fact_pack: dict[str, Any] = {}
    if fact_pack_id:
        fact_pack_record = FACT_PACK_STORE.get(workspace_id, fact_pack_id)
        if fact_pack_record is None:
            return _err_env(
                f"{SERVER_NAME}.fact_pack_not_found",
                f"未找到同工作区 Finance Fact Pack：{fact_pack_id}",
                status="blocked",
                blockers=["fact_pack_not_found"],
            )
        fact_pack_payload = (
            fact_pack_record.get("payload")
            if isinstance(fact_pack_record.get("payload"), dict)
            else {}
        )
        fact_pack = (
            fact_pack_payload.get("fact_pack")
            if isinstance(fact_pack_payload.get("fact_pack"), dict)
            else {}
        )
        from lvke_mcp.domains.finance.fact_pack import verify_fact_pack_seal

        seal = verify_fact_pack_seal(fact_pack, workspace_id=workspace_id)
        fact_pack_errors = list(seal.get("issues") or [])
        if fact_pack_payload.get("confirmation_status") != "confirmed":
            fact_pack_errors.append("finance_fact_pack 未 confirmed")
        if fact_pack.get("delivery_grade_ceiling") != "formal_candidate":
            fact_pack_errors.append("finance_fact_pack 未达到 formal_candidate")
        if not bool((fact_pack.get("depth_assessment") or {}).get("ok")):
            fact_pack_errors.append("finance_fact_pack 深度评估未通过")
        if fact_pack_errors:
            return _ok_env(
                {
                    "available": False,
                    "fact_pack_id": fact_pack_id,
                    "fact_pack_errors": list(dict.fromkeys(fact_pack_errors)),
                },
                source=f"{SERVER_NAME}.finance_prepare_spec",
                status="blocked",
                blockers=["confirmed_formal_candidate_fact_pack_required"],
                next_actions=["确认同工作区 Fact Pack 并达到 formal_candidate 后重试"],
                fact_pack_id=fact_pack_id,
                fact_pack_hash=fact_pack.get("fact_pack_hash"),
            )
    evidence_ids = _str_list(args.get("evidence_pack_ids"))
    evidence_records = []
    for evidence_id in evidence_ids:
        record = EVIDENCE_STORE.get(workspace_id, evidence_id)
        if record is None:
            return _err_env(
                f"{SERVER_NAME}.evidence_pack_not_found",
                f"未找到 evidence pack：{evidence_id}",
                status="blocked",
                blockers=[f"evidence_pack_not_found:{evidence_id}"],
            )
        evidence_records.append(record)
    formal_parents = [
        record
        for record in [*evidence_records, *([fact_pack_record] if fact_pack_record else [])]
        if str(((record.get("payload") or {}).get("evidence_policy") or "")) == "sim_a_formal"
    ]
    canonical_lineage: dict[str, Any] = {}
    if formal_parents:
        all_parents = [*evidence_records, *([fact_pack_record] if fact_pack_record else [])]
        if len(formal_parents) != len(all_parents):
            return _err_env(
                f"{SERVER_NAME}.formal_lineage_mixed_policy",
                "FinanceSpec 不允许混合 SIM-A promotion 与其他证据父对象",
                status="blocked",
                blockers=["formal_lineage_mixed_policy"],
            )
        try:
            canonical_lineage = validate_same_formal_lineage(
                workspace_id,
                all_parents,
            )
        except FormalLineageError as exc:
            return _err_env(
                f"{SERVER_NAME}.{exc.code}",
                exc.message,
                status="blocked",
                blockers=[exc.code],
            )
    try:
        from lvke_mcp.domains.finance import run_service

        supplied_spec = args.get("spec") if isinstance(args.get("spec"), dict) else None
        if supplied_spec is not None:
            supplied_spec = dict(supplied_spec)
            revenue = supplied_spec.get("revenue")
            if isinstance(revenue, dict) and str(revenue.get("model") or "") == "tourism":
                from lvke_mcp.domains.finance.revenue_models import normalize_tourism_revenue

                normalized_revenue, revenue_errors = normalize_tourism_revenue(revenue)
                if revenue_errors:
                    return _ok_env(
                        {"available": False, "missing_inputs": []},
                        source=f"{SERVER_NAME}.finance_prepare_spec",
                        status="blocked",
                        blockers=["revenue_component_conflict"],
                        field_errors=revenue_errors,
                        next_actions=["修正文旅收入组件与兼容别名冲突后重试"],
                    )
                supplied_spec["revenue"] = normalized_revenue
        data = run_service.prepare_workspace_finance_spec(
            workspace_id,
            strategy=str(args.get("strategy") or "propose_from_project"),
            force_refresh=bool(args.get("force_refresh") or False),
            force_flat=bool(args.get("force_flat") or False),
        )
        if supplied_spec is not None:
            supplied_spec = stamp_finance_spec(
                supplied_spec,
                invest_type=str(data.get("invest_type") or ""),
            )
            data["spec"] = supplied_spec
            data["spec_hash"] = run_service.compute_spec_hash(supplied_spec)
            data["force_flat"] = False
        normalized_inputs, adoption, rejected = _canonical_candidate_inputs(
            supplied_spec,
            args.get("input_revision") if isinstance(args.get("input_revision"), dict) else None,
            data.get("input_revision") if isinstance(data.get("input_revision"), dict) else {},
        )
        if rejected:
            return _ok_env(
                {
                    "available": False,
                    "missing_inputs": [],
                    "input_rejections": rejected,
                    "input_adoption_ledger": adoption,
                },
                source=f"{SERVER_NAME}.finance_prepare_spec",
                status="blocked",
                blockers=["candidate_input_invalid"],
                field_errors=[
                    {
                        "path": str(item.get("path") or f"/input_revision/{item.get('input') or 'unknown'}"),
                        "code": str(item.get("reason") or "candidate_input_invalid"),
                        "input": item.get("input"),
                        **({"conflicts_with": item.get("conflicts_with")} if item.get("conflicts_with") else {}),
                    }
                    for item in rejected
                ],
                next_actions=["修正未知、冲突或非法的 input_revision 字段后重试"],
            )
        if fact_pack:
            normalized_inputs["finance_fact_pack"] = json.loads(
                json.dumps(fact_pack, ensure_ascii=False)
            )
            adoption.append({
                "input": "fact_pack_id",
                "effective": "finance_fact_pack",
                "raw_value": fact_pack_id,
                "effective_value": fact_pack.get("fact_pack_hash"),
                "status": "resolved_confirmed_object",
                "source": "finance_fact_pack_store",
            })
            from lvke_mcp.domains.finance.fact_pack import project_confirmed_fact_pack

            projected_inputs, projected_spec, projection = project_confirmed_fact_pack(
                normalized_inputs,
                data.get("spec") if isinstance(data.get("spec"), dict) else None,
                expected_workspace_id=workspace_id,
                expected_build_years=max(
                    1,
                    -(
                        -int(
                            normalized_inputs.get("build_period_months")
                            or data.get("build_period_months")
                            or 12
                        )
                        // 12
                    ),
                ),
                expected_calc_years=int(
                    normalized_inputs.get("calc_period_years") or 12
                ),
            )
            if not projection.get("applied"):
                return _ok_env(
                    {
                        "available": False,
                        "fact_pack_id": fact_pack_id,
                        "fact_pack_errors": list(projection.get("issues") or []),
                    },
                    source=f"{SERVER_NAME}.finance_prepare_spec",
                    status="blocked",
                    blockers=["confirmed_fact_pack_projection_failed"],
                    next_actions=["重建并重新确认同工作区 FinanceFactPack 后重试"],
                    fact_pack_id=fact_pack_id,
                    fact_pack_hash=fact_pack.get("fact_pack_hash"),
                )
            normalized_inputs = projected_inputs
            data["spec"] = projected_spec
            data["spec_hash"] = run_service.compute_spec_hash(projected_spec)
            data["fact_pack_projection"] = projection
        data["input_revision"] = normalized_inputs
        from lvke_mcp.domains.finance.working_capital import apply_operating_turnover_to_inputs

        injected_turnover = apply_operating_turnover_to_inputs(normalized_inputs)
        if injected_turnover:
            data.setdefault("assumptions_to_confirm", [])
            if isinstance(data["assumptions_to_confirm"], list):
                data["assumptions_to_confirm"].append(
                    "经营项目缺周转天数时按行业缺省天数注入 wc_turnover；正式发布前须替换为已确认驱动"
                )
        data["input_adoption_ledger"] = adoption
        data["input_hash"] = run_service.compute_input_hash(
            normalized_inputs,
            invest_type=str(data.get("invest_type") or normalized_inputs.get("invest_type") or ""),
            build_period_months=data.get("build_period_months") or normalized_inputs.get("build_period_months"),
            industry=str(data.get("industry") or normalized_inputs.get("industry") or ""),
        )
        effective_invest_type = str(
            data.get("invest_type") or normalized_inputs.get("invest_type") or ""
        )
        data["generation_basis"] = generation_baseline(
            invest_type=effective_invest_type,
        )
        data["generation_standard"] = data["generation_basis"]["standard_id"]
        data["standard_version"] = data["generation_basis"]["standard_version"]
        data["standard_source_hash"] = data["generation_basis"]["source_hash"]
        data["standard_coverage_snapshot"] = coverage_snapshot(
            finance_inputs=normalized_inputs,
            invest_type=effective_invest_type,
        )
        missing = [] if normalized_inputs.get("total_investment_wan") else ["total_investment_wan"]
        spec = data.get("spec") if isinstance(data.get("spec"), dict) else None
        if not _revenue_input_complete(spec or supplied_spec, normalized_inputs):
            missing.append("annual_revenue_wan_or_revenue_driver")
        missing.extend(
            _rail_transit_missing_inputs(
                spec or supplied_spec,
                normalized_inputs,
                build_period_months=(
                    normalized_inputs.get("build_period_months")
                    or data.get("build_period_months")
                ),
            )
        )
        missing = list(dict.fromkeys(missing))
        if spec is None:
            missing.append("finance_spec")
        data["available"] = spec is not None
        data["missing_inputs"] = list(missing)
        data["quality_issues"] = [f"missing_input:{item}" for item in missing]
        evidence_binding_hash = sha256_json(
            {
                "evidence_pack_ids": evidence_ids,
                "evidence_basis_hashes": [record.get("basis_hash") for record in evidence_records],
                "fact_pack_id": fact_pack_id or None,
                "fact_pack_basis_hash": (
                    fact_pack_record.get("basis_hash") if fact_pack_record else None
                ),
                "fact_pack_content_hash": (
                    fact_pack_record.get("content_hash") if fact_pack_record else None
                ),
                "fact_pack_hash": fact_pack.get("fact_pack_hash") or None,
            }
        )
        spec_record = None
        reused_fact_pack_hash: str | None = None
        reused_record = (
            data.get("confirmed_spec_record")
            if isinstance(data.get("confirmed_spec_record"), dict)
            else None
        )
        can_reuse_confirmed = bool(
            reused_record
            and supplied_spec is None
            and not isinstance(args.get("input_revision"), dict)
            and not evidence_ids
            and not fact_pack_id
            and str((reused_record.get("payload") or {}).get("spec_hash") or "")
            == str(data.get("spec_hash") or "")
            and str((reused_record.get("payload") or {}).get("input_hash") or "")
            == str(data.get("input_hash") or "")
        )
        if can_reuse_confirmed and str((reused_record.get("payload") or {}).get("evidence_policy") or "") == "sim_a_formal":
            try:
                reused_lineage = validate_formal_record(workspace_id, reused_record)
            except FormalLineageError:
                can_reuse_confirmed = False
            else:
                canonical_lineage = reused_lineage
        if spec is not None:
            reconstruction_records = [
                item
                for record in evidence_records
                for item in ((record.get("payload") or {}).get("reconstruction_records") or [])
                if isinstance(item, dict)
            ]
            evidence_policies = {
                str((record.get("payload") or {}).get("evidence_policy") or "")
                for record in evidence_records
                if str((record.get("payload") or {}).get("evidence_policy") or "")
            }
            fact_pack_policy = str(fact_pack_payload.get("evidence_policy") or "")
            if fact_pack_policy:
                evidence_policies.add(fact_pack_policy)
            if "source_reconstructed" in evidence_policies:
                evidence_policy = "source_reconstructed"
            elif canonical_lineage:
                evidence_policy = "sim_a_formal"
            else:
                evidence_policy = sorted(evidence_policies)[0] if evidence_policies else "formal_evidence"
            reconstruction_records.extend(
                item
                for item in (fact_pack_payload.get("reconstruction_records") or [])
                if isinstance(item, dict)
            )
            unique_reconstruction_records = {
                sha256_json(item): item for item in reconstruction_records
            }
            reconstruction_records = list(unique_reconstruction_records.values())
            reconstructed_source_ids = list(dict.fromkeys([
                *[
                    str(item.get("reconstruction_id") or item.get("source_id") or "")
                    for item in reconstruction_records
                ],
                *_str_list(fact_pack_payload.get("reconstructed_source_ids")),
            ]))
            reconstructed_source_ids = [item for item in reconstructed_source_ids if item]
            unresolved_inputs = list(dict.fromkeys([
                *_str_list(args.get("unresolved_inputs")),
                *_str_list(fact_pack_payload.get("unresolved_inputs")),
            ]))
            release_limitations = list(dict.fromkeys([
                *_str_list(args.get("release_limitations")),
                *_str_list(fact_pack_payload.get("release_limitations")),
            ]))
            parent_object_ids = [*evidence_ids, *([fact_pack_id] if fact_pack_id else [])]
            if can_reuse_confirmed:
                spec_record = reused_record
                reused_payload = reused_record.get("payload") or {}
                evidence_binding_hash = str(
                    reused_payload.get("evidence_binding_hash")
                    or evidence_binding_hash
                )
                fact_pack_id = str(reused_payload.get("fact_pack_id") or "")
                reused_fact_pack_hash = str(
                    reused_payload.get("fact_pack_hash") or ""
                ) or None
            else:
                spec_record = SPEC_STORE.put(
                    workspace_id,
                    {
                    **canonical_lineage,
                    "spec": spec,
                    "spec_hash": data.get("spec_hash"),
                    "generation_basis": data.get("generation_basis"),
                    "generation_standard": data.get("generation_standard"),
                    "standard_version": data.get("standard_version"),
                    "standard_source_hash": data.get("standard_source_hash"),
                    "standard_coverage_snapshot": data.get("standard_coverage_snapshot"),
                    "input_revision": normalized_inputs,
                    "input_hash": data.get("input_hash"),
                    "input_revision_id": data.get("input_revision_id"),
                    "confirmation_status": "candidate",
                    "evidence_pack_ids": evidence_ids,
                    "fact_pack_id": fact_pack_id or None,
                    "fact_pack_hash": fact_pack.get("fact_pack_hash") or None,
                    "fact_pack_content_hash": (
                        fact_pack_record.get("content_hash") if fact_pack_record else None
                    ),
                    "fact_pack_basis_hash": (
                        fact_pack_record.get("basis_hash") if fact_pack_record else None
                    ),
                    "evidence_binding_hash": evidence_binding_hash,
                    "evidence_policy": evidence_policy,
                    # 缺省必须 fail-closed。旧写法 get(..., True) 在 FactPack 未表态
                    # 时给出 True，且只挡 source_reconstructed 一种非正式资格。
                    "project_fact_certified": project_fact_may_be_certified(
                        evidence_policy,
                        own_qualification_passed=bool(canonical_lineage) if evidence_policy == "sim_a_formal" else True,
                        parents=[fact_pack_payload, *evidence_records],
                    ),
                    "reconstruction_records": reconstruction_records,
                    "reconstructed_source_ids": reconstructed_source_ids,
                    "unresolved_inputs": unresolved_inputs,
                    "release_limitations": release_limitations,
                    "parent_object_ids": parent_object_ids,
                },
                producer=f"{SERVER_NAME}.finance_prepare_spec",
                status="partial" if missing else "ok",
                source_ids=parent_object_ids,
                basis={
                    "spec_hash": data.get("spec_hash"),
                    "input_hash": data.get("input_hash"),
                    "evidence_binding_hash": evidence_binding_hash,
                    "fact_pack_basis_hash": (
                        fact_pack_record.get("basis_hash") if fact_pack_record else None
                    ),
                    "fact_pack_hash": fact_pack.get("fact_pack_hash") or None,
                    "formal_promotion": canonical_lineage.get("formal_promotion"),
                },
                )
            data["spec_id"] = spec_record["object_id"]
            data["reused_confirmed"] = can_reuse_confirmed
            data["evidence_binding_hash"] = evidence_binding_hash
            data["fact_pack_id"] = fact_pack_id or None
            data["fact_pack_hash"] = (
                reused_fact_pack_hash
                if can_reuse_confirmed
                else fact_pack.get("fact_pack_hash") or None
            )
        # 没能固化出任何 spec 记录时不能报成功：调用方拿到 success=true 却
        # 拿不到 spec_id，只会在下一步 finance_run_model 才发现无从下手。
        # 例如 reuse_confirmed 遇到 hash 非法或 lineage 断裂的 confirmed spec，
        # 拒绝复用是对的，但必须如实说"没给出 spec"。
        spec_unavailable = spec_record is None
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_prepare_spec",
            status="blocked" if spec_unavailable else ("partial" if missing else "ok"),
            warnings=[
                *_str_list(data.get("warnings")),
                *(f"质量提示：缺少关键输入 {item}" for item in missing),
            ],
            blockers=(
                ["finance_spec_unavailable"] if spec_unavailable else []
            ),
            next_actions=(
                [
                    "无可复用的 confirmed Spec（hash 非法或 lineage 断裂）；"
                    "改用 strategy=propose_from_project 重新准备候选 Spec",
                ]
                if spec_unavailable
                else ["候选 Spec 已固化；可直接运行模型，补充输入可提高置信度"]
                if missing
                else (
                    ["已复用 confirmed Spec，可直接用 spec_id 调用 finance_run_model"]
                    if can_reuse_confirmed
                    else ["可确认候选 Spec，也可直接调用 finance_run_model"]
                )
            ),
            resource_uris=[spec_record["resource_uri"]] if spec_record else [],
            spec_id=spec_record["object_id"] if spec_record else None,
            spec_hash=data.get("spec_hash"),
            evidence_binding_hash=evidence_binding_hash,
            fact_pack_id=fact_pack_id or None,
            fact_pack_hash=(
                reused_fact_pack_hash
                if can_reuse_confirmed
                else fact_pack.get("fact_pack_hash") or None
            ),
            missing_inputs=missing,
            assumptions_to_confirm=_str_list(data.get("assumptions_to_confirm")),
            input_hash=data.get("input_hash"),
            input_revision_id=data.get("input_revision_id"),
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_prepare_spec failed",
            f"{SERVER_NAME}.prepare_failed",
            "准备 FinanceSpec 失败",
        )


def confirm_spec(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_id(args)
    spec_id = str(args.get("spec_id") or "").strip()
    if not workspace_id or not spec_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 与 spec_id 必填")
    source = SPEC_STORE.get(workspace_id, spec_id)
    if source is None:
        return _err_env(f"{SERVER_NAME}.spec_not_found", "未找到候选 FinanceSpec", status="blocked")
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    canonical_lineage: dict[str, Any] = {}
    if payload.get("evidence_policy") == "sim_a_formal":
        parent_records: list[dict[str, Any]] = []
        for evidence_id in _str_list(payload.get("evidence_pack_ids")):
            record = EVIDENCE_STORE.get(workspace_id, evidence_id)
            if record is None:
                return _err_env(
                    f"{SERVER_NAME}.evidence_pack_not_found",
                    "确认 FinanceSpec 时正式 EvidencePack 不存在或跨工作区",
                    status="blocked",
                )
            parent_records.append(record)
        fact_pack_id = str(payload.get("fact_pack_id") or "")
        if fact_pack_id:
            fact_record = FACT_PACK_STORE.get(workspace_id, fact_pack_id)
            if fact_record is None:
                return _err_env(
                    f"{SERVER_NAME}.fact_pack_not_found",
                    "确认 FinanceSpec 时正式 FactPack 不存在或跨工作区",
                    status="blocked",
                )
            parent_records.append(fact_record)
        try:
            source_lineage = validate_formal_record(workspace_id, source)
            canonical_lineage = validate_same_formal_lineage(workspace_id, parent_records)
            if canonical_lineage != source_lineage:
                raise FormalLineageError(
                    "formal_lineage_metadata_mismatch",
                    "FinanceSpec 与其正式父对象 promotion 不一致",
                )
        except FormalLineageError as exc:
            return _err_env(
                f"{SERVER_NAME}.{exc.code}",
                exc.message,
                status="blocked",
                blockers=[exc.code],
            )
    spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else None
    if spec is None:
        return _err_env(f"{SERVER_NAME}.spec_invalid", "候选 FinanceSpec 快照无效", status="blocked")
    input_revision = payload.get("input_revision") if isinstance(payload.get("input_revision"), dict) else {}
    missing = [] if input_revision.get("total_investment_wan") else ["total_investment_wan"]
    if not _revenue_input_complete(spec, input_revision):
        missing.append("annual_revenue_wan_or_revenue_driver")
    missing.extend(
        _rail_transit_missing_inputs(
            spec,
            input_revision,
            build_period_months=input_revision.get("build_period_months"),
        )
    )
    missing = list(dict.fromkeys(missing))
    from lvke_mcp.domains.finance.spec import mark_spec_confirmed, validate_for_formal

    confirmed = mark_spec_confirmed(spec)
    formal_ok, formal_errors = validate_for_formal(confirmed)
    quality_issues = [
        *(f"missing_input:{item}" for item in missing),
        *[str(item) for item in formal_errors],
    ]
    key = str(args.get("idempotency_key") or "").strip()
    if not key:
        return _err_env(
            f"{SERVER_NAME}.idempotency_key_required",
            "finance_confirm_spec 写操作必须提供 idempotency_key",
            status="blocked",
        )
    note = str(args.get("note") or "")
    fingerprint = sha256_json(
        {"spec_id": spec_id, "spec_content_hash": source.get("content_hash"), "note": note}
    )
    key_hash = "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()
    prior = _active_idempotency_record(workspace_id, key_hash, "finance_confirm_spec")
    if prior is not None:
        saved = prior.get("payload") or {}
        if saved.get("content_fingerprint") != fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已绑定不同 FinanceSpec 确认请求",
                status="blocked",
                replayed=False,
                reused=False,
            )
        replay = json.loads(json.dumps(saved.get("result") or {}))
        replay.update({"replayed": True, "reused": True})
        return replay
    from lvke_mcp.domains.finance.run_service import compute_spec_hash

    record = SPEC_STORE.put(
        workspace_id,
        {
            **payload,
            **canonical_lineage,
            "spec": confirmed,
            "spec_hash": compute_spec_hash(confirmed),
            "confirmation_status": "confirmed",
            "quality_issues": quality_issues,
            "missing_inputs": missing,
            "formal_quality_valid": bool(formal_ok and not missing),
            "parent_spec_id": spec_id,
            "confirmation": {"note": note},
            "parent_object_ids": list(dict.fromkeys([
                spec_id,
                *_str_list(payload.get("evidence_pack_ids")),
                *([str(payload.get("fact_pack_id"))] if payload.get("fact_pack_id") else []),
            ])),
        },
        producer=f"{SERVER_NAME}.finance_confirm_spec",
        status="partial" if quality_issues else "ok",
        source_ids=[
            spec_id,
            *_str_list(payload.get("evidence_pack_ids")),
            *([str(payload.get("fact_pack_id"))] if payload.get("fact_pack_id") else []),
        ],
        basis={
            "parent_spec_id": spec_id,
            "spec_hash": compute_spec_hash(confirmed),
            "fact_pack_basis_hash": payload.get("fact_pack_basis_hash"),
            "fact_pack_hash": payload.get("fact_pack_hash"),
            "note": note,
            "formal_promotion": canonical_lineage.get("formal_promotion"),
        },
    )
    expires_at = _expires_at()
    result = _ok_env(
        {
            "spec_id": record["object_id"],
            "parent_spec_id": spec_id,
            "spec_hash": record["payload"]["spec_hash"],
            "quality_issues": quality_issues,
            "missing_inputs": missing,
            "formal_quality_valid": bool(formal_ok and not missing),
        },
        source=f"{SERVER_NAME}.finance_confirm_spec",
        status="partial" if quality_issues else "ok",
        resource_uris=[record["resource_uri"]],
        warnings=[f"质量提示：{item}" for item in quality_issues],
        blockers=[],
        next_actions=["Spec 已确认；可直接调用 finance_run_model，质量问题不阻断运行"],
        spec_id=record["object_id"],
        spec_hash=record["payload"]["spec_hash"],
        content_fingerprint=fingerprint,
        replayed=False,
        reused=False,
        idempotency_expires_at=expires_at,
    )
    IDEMPOTENCY_STORE.put(
        workspace_id,
        {"operation": "finance_confirm_spec", "key_hash": key_hash, "content_fingerprint": fingerprint, "expires_at": expires_at, "result": result},
        producer=f"{SERVER_NAME}.finance_confirm_spec",
        source_ids=[record["object_id"]],
        basis={"operation": "finance_confirm_spec", "key_hash": key_hash, "content_fingerprint": fingerprint},
    )
    return result


def validate_spec(args: dict[str, Any]) -> dict[str, Any]:
    spec = args.get("spec")
    if not isinstance(spec, dict):
        return _err_env(f"{SERVER_NAME}.invalid_argument", "spec 必填且必须是对象")
    try:
        from lvke_mcp.domains.finance.spec import validate, validate_for_formal

        structural_ok, errors = validate(spec)
        formal = bool(args.get("for_formal", False))
        formal_ok, formal_errors = validate_for_formal(spec) if formal else (structural_ok, [])
        errors = _unique_strings(errors)
        formal_errors = _unique_strings(formal_errors)
        valid = bool(structural_ok and (formal_ok if formal else True))
        missing = [
            item
            for item in [*errors, *formal_errors]
            if any(word in item for word in ("缺", "missing", "尚未确认"))
        ]
        quality_issues = _unique_strings([*errors, *formal_errors])
        status = "partial" if quality_issues else "ok"
        return _ok_env(
            {
                "valid": True,
                "quality_valid": valid,
                "structural_valid": structural_ok,
                "formal_valid": formal_ok if formal else None,
                "errors": errors,
                "formal_errors": formal_errors,
                "quality_issues": quality_issues,
                "missing_inputs": missing,
                "note": "校验问题仅描述输入质量；运行阶段会采用可追溯默认假设继续计算。",
            },
            source=f"{SERVER_NAME}.finance_validate_spec",
            status=status,
            warnings=[f"质量提示：{item}" for item in quality_issues],
            blockers=[],
            next_actions=["可直接运行模型；修正 quality_issues 可提高结果置信度"],
            valid=True,
            quality_valid=valid,
            missing_inputs=_str_list(missing),
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_validate_spec failed",
            f"{SERVER_NAME}.validate_failed",
            "校验 FinanceSpec 失败",
        )


def _canonical_candidate_inputs(
    supplied_spec: dict[str, Any] | None,
    explicit_revision: dict[str, Any] | None,
    workspace_revision: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    spec_inputs: dict[str, Any] = {}
    if isinstance(supplied_spec, dict):
        input_keys = set(finance_input_schema().get("properties") or {})
        nested = supplied_spec.get("finance_inputs")
        if isinstance(nested, dict):
            spec_inputs.update(nested)
        # FinanceSpec 允许把成本与税率写成业务分组（`cost.cost_items`、
        # `tax.vat_rate`），validate_spec 也接受这种形态。但计算层只认扁平的
        # 顶层键，分组不提升就会被静默丢弃：`cost.cost_items` 五项明细齐全却
        # 读不到，模型退回"总成本费用率 75%"估算，产出与输入无关的成本。
        #
        # `revenue` 组**仅在 model=="flat" 时**提升，且只提升白名单交集键。
        # flat 模型的驱动就是 `annual_revenue_wan` 本身，不提升它就被静默丢弃、
        # 达产营收退回"投资额×30%"派生基线：实测 97,680 变 20,520，利润总额由
        # +40,614.71 翻成 −35,631、NPV 由 +164,301.87 翻成 −246,330.44、IRR 无解，
        # 而 consistency_ok 仍为 true。
        #
        # 其他模型（tourism / product_sales / property_sales / rail_transit）另有
        # 自己的量价驱动，其 `revenue.annual_revenue_wan` 只是**由那些驱动折算出的
        # 回显值**，与调用方显式 input_revision 常有正常的舍入差（实测零材料
        # tourism 链 12000.0 vs 12000.3）。把回显值当独立输入提升会把这种舍入差
        # 判成 candidate_input_conflict，整条链 fail-closed —— 那是误杀，不是把关。
        revenue_group = supplied_spec.get("revenue")
        revenue_model = (
            str(revenue_group.get("model") or "") if isinstance(revenue_group, dict) else ""
        )
        promoted_groups = ("cost", "tax", "revenue") if revenue_model == "flat" else ("cost", "tax")
        for group in promoted_groups:
            group_values = supplied_spec.get(group)
            if not isinstance(group_values, dict):
                continue
            for key, value in group_values.items():
                if key not in input_keys:
                    continue
                if key in spec_inputs and spec_inputs[key] != value:
                    return {}, [], [{
                        "input": key,
                        "reason": "candidate_input_conflict",
                        "path": f"/spec/{group}/{key}",
                        "conflicts_with": f"/spec/finance_inputs/{key}",
                    }]
                spec_inputs[key] = value
        for key in input_keys:
            if key in supplied_spec:
                if key in spec_inputs and spec_inputs[key] != supplied_spec[key]:
                    return {}, [], [{
                        "input": key,
                        "reason": "candidate_input_conflict",
                        "path": f"/spec/{key}",
                        "conflicts_with": f"/spec/finance_inputs/{key}",
                    }]
                spec_inputs[key] = supplied_spec[key]
    merged = dict(workspace_revision or {})
    adoption: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    normalized_sources = []
    for source_name, values in (
        ("candidate_spec", spec_inputs),
        ("explicit_input_revision", explicit_revision or {}),
    ):
        normalized, ledger, errors = canonicalize_finance_inputs(values)
        adoption.extend({**item, "source": source_name} for item in ledger)
        rejected.extend({**item, "source": source_name} for item in errors)
        normalized_sources.append(normalized)
    for key in sorted(set(normalized_sources[0]) & set(normalized_sources[1])):
        if normalized_sources[0][key] != normalized_sources[1][key]:
            rejected.append({
                "input": key,
                "reason": "candidate_input_conflict",
                "source": "candidate_spec_vs_explicit_input_revision",
                "path": f"/input_revision/{key}",
                "conflicts_with": f"/spec/finance_inputs/{key}",
            })
    for normalized in normalized_sources:
        merged.update(normalized)
    normalized, ledger, errors = canonicalize_finance_inputs(merged)
    adoption.extend({**item, "source": "effective"} for item in ledger)
    rejected.extend({**item, "source": "effective"} for item in errors)
    return normalized, adoption, rejected

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "EVIDENCE_STORE",
    "FACT_PACK_STORE",
    "FormalLineageError",
    "IDEMPOTENCY_STORE",
    "SERVER_NAME",
    "SPEC_STORE",
    "_active_idempotency_record",
    "_canonical_candidate_inputs",
    "_err_env",
    "_exception_env",
    "_expires_at",
    "_ok_env",
    "_rail_transit_missing_inputs",
    "_revenue_input_complete",
    "_str_list",
    "_unique_strings",
    "_workspace_id",
    "canonicalize_finance_inputs",
    "confirm_spec",
    "coverage_snapshot",
    "finance_input_schema",
    "generation_baseline",
    "hashlib",
    "json",
    "ok",
    "prepare_spec",
    "project_fact_may_be_certified",
    "sha256_json",
    "stamp_finance_spec",
    "validate_formal_record",
    "validate_same_formal_lineage",
    "validate_spec",
]
