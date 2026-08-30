"""BoE、资产负债、Monte Carlo 与分析 Resource 工具；含 get_analysis 聚合入口。"""

from __future__ import annotations

import hashlib
from typing import Any

from mcp import types

from lvke_mcp.runtime.evidence_qualification import (
    FORMAL_EVIDENCE,
    SIM_A_FORMAL,
    project_fact_may_be_certified,
)
from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    validate_formal_record,
    validate_same_formal_lineage,
)
from lvke_mcp.runtime.storage import (
    JSONArtifactStore,
    paginate_resource_entries,
    sha256_json,
)
from lvke_mcp.adapters.finance_model_repository import BALANCE_SHEET_STORE, BASIS_OF_ESTIMATE_STORE, FACT_PACK_STORE, MONTE_CARLO_STORE, SPEC_STORE
from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.runtime.responses import ok
from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
from lvke_mcp.runtime.source_reconstruction import reconstruction_errors

from .envelope import (
    _err_env,
    _exception_env,
    _ok_env,
    _str_list,
    _unique_strings,
    _ws,
)

from .schemas import (
    SERVER_NAME,
    _output_schema,
)


def _load_consistent_run(workspace_id: str, run_id: str) -> dict | None:
    from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

    run = get_workspace_finance_run(
        workspace_id,
        run_id=run_id,
        view="full",
    )
    if not run.get("available") or run.get("consistency_ok") is not True:
        return None
    return run


def _planning_record(
    workspace_id: str,
    object_id: str,
) -> dict[str, Any] | None:
    from lvke_mcp.adapters.project_planning_repository import get_record

    return get_record(workspace_id, object_id)


def _required_boe_pointers(spec_payload: dict[str, Any]) -> list[str]:
    input_revision = spec_payload.get("input_revision")
    input_revision = input_revision if isinstance(input_revision, dict) else {}
    required = ["/input_revision/total_investment_wan", "/spec/revenue"]
    for field in (
        "annual_operating_cost_wan",
        "invest_breakdown",
        "wc_turnover",
        "labor_plan",
        "fixed_asset_categories",
        "taxes",
    ):
        if input_revision.get(field) not in (None, "", [], {}):
            required.append(f"/input_revision/{field}")
    return required


def _latest_formal_boe(
    workspace_id: str,
    spec_id: str,
) -> dict[str, Any] | None:
    matches = [
        record
        for record in BASIS_OF_ESTIMATE_STORE.list(workspace_id)
        if (record.get("payload") or {}).get("spec_id") == spec_id
        and bool((record.get("payload") or {}).get("formal_ready"))
    ]
    return max(matches, key=lambda record: str(record.get("created_at") or ""), default=None)


def _tool_build_basis_of_estimate(args: dict) -> dict:
    wsid = _ws(args)
    spec_id = str(args.get("spec_id") or "").strip()
    idempotency_key = str(args.get("idempotency_key") or "").strip()
    planning_ids = _unique_strings(args.get("planning_object_ids"))
    evidence_ids = _unique_strings(args.get("evidence_pack_ids"))
    entries = args.get("entries") if isinstance(args.get("entries"), list) else []
    if not wsid or not spec_id or not idempotency_key or not entries:
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "workspace_id、spec_id、entries 与 idempotency_key 必填",
            status="blocked",
        )
    spec_record = SPEC_STORE.get(wsid, spec_id)
    spec_payload = spec_record.get("payload") if isinstance((spec_record or {}).get("payload"), dict) else {}
    if spec_record is None or spec_payload.get("confirmation_status") != "confirmed":
        return _err_env(
            f"{SERVER_NAME}.confirmed_spec_required",
            "Basis of Estimate 只能绑定同作用域已确认 FinanceSpec",
            status="blocked",
        )
    sim_a_formal = spec_payload.get("evidence_policy") == SIM_A_FORMAL
    bound_evidence_ids = set(_str_list(spec_payload.get("evidence_pack_ids")))
    if (
        (sim_a_formal and set(evidence_ids) != bound_evidence_ids)
        or (not sim_a_formal and not set(evidence_ids) <= bound_evidence_ids)
    ):
        return _err_env(
            f"{SERVER_NAME}.evidence_basis_mismatch",
            "BoE EvidencePack 必须已绑定到 FinanceSpec",
            status="blocked",
        )
    evidence_records = []
    for evidence_id in evidence_ids:
        record = EVIDENCE_STORE.get(wsid, evidence_id)
        if record is None:
            return _err_env(
                f"{SERVER_NAME}.evidence_pack_not_found",
                "BoE 引用的 EvidencePack 不存在或跨越作用域",
                status="blocked",
            )
        evidence_records.append(record)
    formal_lineage: dict[str, Any] = {}
    if sim_a_formal:
        formal_parents: list[dict[str, Any]] = [spec_record, *evidence_records]
        fact_pack_id = str(spec_payload.get("fact_pack_id") or "")
        if fact_pack_id:
            fact_pack_record = FACT_PACK_STORE.get(wsid, fact_pack_id)
            if fact_pack_record is None:
                return _err_env(
                    f"{SERVER_NAME}.fact_pack_not_found",
                    "BoE 绑定的正式 FactPack 不存在或跨工作区",
                    status="blocked",
                )
            formal_parents.append(fact_pack_record)
        try:
            formal_lineage = validate_same_formal_lineage(wsid, formal_parents)
        except FormalLineageError as exc:
            return _err_env(
                f"{SERVER_NAME}.{exc.code}",
                exc.message,
                status="blocked",
            )
    planning_records = []
    for object_id in planning_ids:
        record = _planning_record(wsid, object_id)
        payload = record.get("payload") if isinstance((record or {}).get("payload"), dict) else {}
        if record is None or payload.get("status") != "confirmed":
            return _err_env(
                f"{SERVER_NAME}.confirmed_planning_object_required",
                f"BoE planning basis 必须是同作用域 confirmed 对象：{object_id}",
                status="blocked",
            )
        planning_records.append(record)
    source_records = {
        record["object_id"]: record
        for record in [*planning_records, *evidence_records]
    }
    allowed_sources = set(source_records)
    field_errors = []
    pointers: set[str] = set()
    for index, entry in enumerate(entries):
        pointer = str(entry.get("target_pointer") or "")
        if pointer in pointers:
            field_errors.append({
                "path": f"/entries/{index}/target_pointer",
                "code": "duplicate_target_pointer",
            })
        pointers.add(pointer)
        source_object_id = str(entry.get("source_object_id") or "")
        if source_object_id not in allowed_sources:
            field_errors.append({
                "path": f"/entries/{index}/source_object_id",
                "code": "source_object_not_bound",
            })
        else:
            source_payload = source_records[source_object_id].get("payload") or {}
            source_track = str(source_payload.get("evidence_track") or "")
            declared_eligibility = str(entry.get("evidence_eligibility") or "")
            if sim_a_formal:
                try:
                    source_lineage = validate_formal_record(
                        wsid,
                        source_records[source_object_id],
                    )
                except FormalLineageError as exc:
                    field_errors.append({
                        "path": f"/entries/{index}/source_object_id",
                        "code": exc.code,
                    })
                else:
                    if source_lineage != formal_lineage:
                        field_errors.append({
                            "path": f"/entries/{index}/source_object_id",
                            "code": "formal_lineage_mixed_promotions",
                        })
                if declared_eligibility != SIM_A_FORMAL:
                    field_errors.append({
                        "path": f"/entries/{index}/evidence_eligibility",
                        "code": "formal_eligibility_must_be_server_derived",
                    })
                if str(entry.get("content_hash") or "") != str(source_records[source_object_id].get("content_hash") or ""):
                    field_errors.append({
                        "path": f"/entries/{index}/content_hash",
                        "code": "source_content_hash_mismatch",
                    })
            eligible_tracks = {
                "formal_evidence": {"real", "formal_evidence"},
                "source_reconstructed": {"source_reconstructed"},
                "technical_fixture": {"technical_fixture"},
                "controlled_assumption": {"controlled_assumption"},
                "sim_a_formal": {"sim_a_formal"},
            }
            if source_track not in eligible_tracks.get(declared_eligibility, set()):
                field_errors.append({
                    "path": f"/entries/{index}/evidence_eligibility",
                    "code": "evidence_eligibility_mismatch",
                })
            if declared_eligibility == "source_reconstructed":
                reconstruction = entry.get("reconstruction") or entry.get("reconstruction_record")
                errors = reconstruction_errors(reconstruction)
                field_errors.extend({
                    "path": f"/entries/{index}/reconstruction/{code.split('_required')[0] if code.endswith('_required') else 'record'}",
                    "code": code,
                } for code in errors)
        if not all(entry.get(field) for field in (
            "target_pointer", "unit", "period", "source_type", "source_object_id",
            "method", "selection_reason", "locator", "content_hash", "evidence_eligibility"
        )):
            field_errors.append({"path": f"/entries/{index}", "code": "boe_entry_incomplete"})
    required_pointers = _required_boe_pointers(spec_payload)
    missing_pointers = sorted(set(required_pointers) - pointers)
    field_errors.extend({"path": pointer, "code": "major_input_basis_missing"} for pointer in missing_pointers)
    if field_errors:
        return _ok_env(
            {"available": False, "field_errors": field_errors},
            source=f"{SERVER_NAME}.finance_build_basis_of_estimate",
            status="missing_inputs",
            blockers=sorted({str(item["code"]) for item in field_errors}),
            next_actions=["为每个重大 FinanceSpec 输入补充已绑定对象、locator、hash 和选择理由"],
            basis_of_estimate_id=None,
            spec_id=spec_id,
            technical_ready=False,
            formal_ready=False,
        )
    content_fingerprint = sha256_json({
        "spec_id": spec_id,
        "spec_basis_hash": spec_record["basis_hash"],
        "fact_pack_id": spec_payload.get("fact_pack_id"),
        "fact_pack_basis_hash": spec_payload.get("fact_pack_basis_hash"),
        "planning_object_ids": planning_ids,
        "evidence_pack_ids": evidence_ids,
        "entries": entries,
    })
    key_hash = "sha256:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    prior = next((
        record
        for record in BASIS_OF_ESTIMATE_STORE.list(wsid)
        if (record.get("payload") or {}).get("idempotency_key_hash") == key_hash
    ), None)
    if prior is not None:
        prior_payload = prior.get("payload") or {}
        if prior_payload.get("content_fingerprint") != content_fingerprint:
            return _err_env(
                f"{SERVER_NAME}.idempotency_conflict",
                "同一 idempotency_key 已用于不同 BoE 请求",
                status="blocked",
            )
        return _ok_env(
            prior,
            source=f"{SERVER_NAME}.finance_build_basis_of_estimate",
            status="ok" if prior_payload.get("formal_ready") else "partial",
            resource_uris=[prior["resource_uri"]],
            basis_of_estimate_id=prior["object_id"],
            spec_id=spec_id,
            technical_ready=bool(prior_payload.get("technical_ready")),
            formal_ready=bool(prior_payload.get("formal_ready")),
            replayed=True,
        )
    technical_ready = all(
        entry.get("evidence_eligibility") in {"formal_evidence", "source_reconstructed", "technical_fixture", "sim_a_formal"}
        for entry in entries
    )
    formal_ready = all(
        entry.get("evidence_eligibility") in {"formal_evidence", "source_reconstructed", "sim_a_formal"}
        for entry in entries
    )
    reconstructed = any(entry.get("evidence_eligibility") == "source_reconstructed" for entry in entries)
    reconstruction_records = [
        record
        for entry in entries
        for record in [entry.get("reconstruction") or entry.get("reconstruction_record")]
        if isinstance(record, dict)
    ]
    entry_policies = [str(entry.get("evidence_eligibility") or "") for entry in entries]
    all_certifying = bool(entries) and all(
        policy in {FORMAL_EVIDENCE, SIM_A_FORMAL} for policy in entry_policies
    )
    sim_a_entries = any(policy == SIM_A_FORMAL for policy in entry_policies)
    boe_policy = (
        "source_reconstructed"
        if reconstructed
        else SIM_A_FORMAL
        if sim_a_entries and all_certifying
        else FORMAL_EVIDENCE
    )
    payload = {
        **formal_lineage,
        "object_type": "BasisOfEstimate",
        "spec_id": spec_id,
        "spec_hash": spec_payload.get("spec_hash"),
        "entries": entries,
        "required_major_input_pointers": required_pointers,
        "planning_object_ids": planning_ids,
        "evidence_pack_ids": evidence_ids,
        "technical_ready": technical_ready,
        "formal_ready": formal_ready,
        "evidence_policy": SIM_A_FORMAL if formal_lineage else boe_policy,
        "evidence_origin": formal_lineage.get("evidence_origin") if formal_lineage else spec_payload.get("evidence_origin"),
        "project_fact_certified": project_fact_may_be_certified(
            SIM_A_FORMAL if formal_lineage else boe_policy,
            own_qualification_passed=bool(formal_lineage) if sim_a_formal else bool(formal_ready and all_certifying),
            parents=[
                {
                    "evidence_policy": str(entry.get("evidence_eligibility") or ""),
                    "project_fact_certified": str(entry.get("evidence_eligibility") or "")
                    in {FORMAL_EVIDENCE, SIM_A_FORMAL},
                }
                for entry in entries
            ],
        ),
        "reconstruction_records": reconstruction_records,
        "reconstructed_source_ids": [str(item.get("reconstruction_id") or "") for item in reconstruction_records if item.get("reconstruction_id")],
        "unresolved_inputs": list(args.get("unresolved_inputs") or spec_payload.get("unresolved_inputs") or []),
        "release_limitations": list(args.get("release_limitations") or spec_payload.get("release_limitations") or []),
        "evidence_eligibility": (
            "source_reconstructed"
            if reconstructed
            else SIM_A_FORMAL
            if boe_policy == SIM_A_FORMAL
            else "formal_evidence"
            if formal_ready
            else "technical_fixture"
            if technical_ready
            else "estimate_only"
        ),
        "idempotency_key_hash": key_hash,
        "content_fingerprint": content_fingerprint,
        "fact_pack_id": spec_payload.get("fact_pack_id"),
        "fact_pack_hash": spec_payload.get("fact_pack_hash"),
        "fact_pack_basis_hash": spec_payload.get("fact_pack_basis_hash"),
        "parent_object_ids": [
            spec_id,
            *planning_ids,
            *evidence_ids,
            *([str(spec_payload.get("fact_pack_id"))] if spec_payload.get("fact_pack_id") else []),
        ],
    }
    record = BASIS_OF_ESTIMATE_STORE.put(
        wsid,
        payload,
        producer=f"{SERVER_NAME}.finance_build_basis_of_estimate",
        status="ok" if formal_ready else "partial",
        source_ids=payload["parent_object_ids"],
        basis={
            "spec_basis_hash": spec_record["basis_hash"],
            "planning_basis_hashes": [record["basis_hash"] for record in planning_records],
            "evidence_basis_hashes": [record["basis_hash"] for record in evidence_records],
            "fact_pack_basis_hash": spec_payload.get("fact_pack_basis_hash"),
            "fact_pack_hash": spec_payload.get("fact_pack_hash"),
            "content_fingerprint": content_fingerprint,
            "formal_promotion": formal_lineage.get("formal_promotion"),
        },
    )
    return _ok_env(
        record,
        source=f"{SERVER_NAME}.finance_build_basis_of_estimate",
        status="ok" if formal_ready else "partial",
        resource_uris=[record["resource_uri"]],
        warnings=(
            []
            if formal_ready and not reconstructed
            else ["本 BoE 使用 source_reconstructed，仅代表流程验收，不认证项目原始事实"]
            if formal_ready and reconstructed
            else ["技术夹具 BoE 只能验证技术链，不得触发正式候选或正式发布"]
            if technical_ready
            else ["BoE 含 controlled_assumption，仅可用于 estimate preview"]
        ),
        next_actions=(
            ["将 basis_of_estimate_id 与 spec_id 一起用于 review_candidate 财务运行"]
            if formal_ready
            else ["仅在 estimate_preview 中绑定该 BoE；正式候选需换用 formal_evidence"]
        ),
        basis_of_estimate_id=record["object_id"],
        spec_id=spec_id,
        technical_ready=technical_ready,
        formal_ready=formal_ready,
        replayed=False,
    )


def _tool_get_basis_of_estimate(args: dict) -> dict:
    return _tool_get_analysis(
        args,
        store=BASIS_OF_ESTIMATE_STORE,
        id_field="basis_of_estimate_id",
        source=f"{SERVER_NAME}.finance_get_basis_of_estimate",
    )


def _tool_build_balance_sheet(args: dict) -> dict:
    wsid = _ws(args)
    run_id = str(args.get("run_id") or "").strip()
    if not wsid or not run_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 与 run_id 必填")
    try:
        run = _load_consistent_run(wsid, run_id)
        if run is None:
            return _err_env(
                f"{SERVER_NAME}.base_run_unavailable",
                "基准 FinanceRun 不存在或未通过勾稽",
                status="blocked",
                next_actions=["选择同工作区且 consistency_ok=true 的 FinanceRun"],
            )
        from lvke_mcp.domains.finance.advanced_analysis import build_balance_sheet_schedule

        schedule = build_balance_sheet_schedule(run)
        if not schedule.get("available"):
            return _ok_env(
                schedule,
                source=f"{SERVER_NAME}.finance_build_balance_sheet",
                status="missing_inputs",
                blockers=["annual.financial_plan 缺失"],
                next_actions=["重新生成包含财务计划现金流的 FinanceRun"],
                balance_sheet_id=None,
                run_id=run_id,
                formal_ready=False,
            )
        payload = {
            **schedule,
            "workspace_id": wsid,
            "run_id": run_id,
            "run_input_hash": run.get("input_hash"),
            "run_spec_hash": run.get("spec_hash"),
            "run_model_version": run.get("model_version"),
        }
        record = BALANCE_SHEET_STORE.put(
            wsid,
            payload,
            producer=f"{SERVER_NAME}.finance_build_balance_sheet",
            status="ok" if schedule.get("formal_ready") else "partial",
            source_ids=[run_id],
            basis={
                "run_id": run_id,
                "input_hash": run.get("input_hash"),
                "spec_hash": run.get("spec_hash"),
                "model_version": run.get("model_version"),
            },
        )
        status = "ok" if schedule.get("formal_ready") else "partial"
        return _ok_env(
            record,
            source=f"{SERVER_NAME}.finance_build_balance_sheet",
            status=status,
            resource_uris=[record["resource_uri"]],
            warnings=([] if status == "ok" else ["资产负债表权益组成与计算残差尚未勾稽"]),
            blockers=[],
            next_actions=([] if status == "ok" else ["核对资本金、利润分配与终值回收口径"]),
            balance_sheet_id=record["object_id"],
            run_id=run_id,
            formal_ready=bool(schedule.get("formal_ready")),
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_build_balance_sheet failed",
            f"{SERVER_NAME}.balance_sheet_failed",
            "生成资产负债表失败",
        )


def _tool_get_analysis(args: dict, *, store: JSONArtifactStore, id_field: str, source: str) -> dict:
    wsid = _ws(args)
    object_id = str(args.get(id_field) or "").strip()
    if not wsid or not object_id:
        return _err_env(f"{SERVER_NAME}.invalid_argument", f"workspace_id 与 {id_field} 必填")
    record = store.get(wsid, object_id)
    if record is None:
        return _err_env(
            f"{SERVER_NAME}.analysis_not_found",
            "未找到同工作区下的高级分析对象",
            status="blocked",
        )
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    readiness = {
        "formal_ready": bool(payload.get("formal_ready", record.get("status") == "ok")),
    }
    if id_field == "basis_of_estimate_id":
        readiness["technical_ready"] = bool(
            payload.get("technical_ready", payload.get("formal_ready"))
        )
    return _ok_env(
        record,
        source=source,
        status="ok" if record.get("status") == "ok" else "partial",
        resource_uris=[record["resource_uri"]],
        warnings=([] if record.get("status") == "ok" else ["分析对象存在未完成勾稽"]),
        **{
            id_field: record["object_id"],
            "run_id": payload.get("run_id"),
            **readiness,
        },
    )


def _tool_get_balance_sheet(args: dict) -> dict:
    return _tool_get_analysis(
        args,
        store=BALANCE_SHEET_STORE,
        id_field="balance_sheet_id",
        source=f"{SERVER_NAME}.finance_get_balance_sheet",
    )


def _tool_run_monte_carlo(args: dict) -> dict:
    wsid = _ws(args)
    run_id = str(args.get("run_id") or "").strip()
    distributions = args.get("distributions")
    sample_count = args.get("sample_count", 1000)
    seed = args.get("seed", 0)
    if not wsid or not run_id or not isinstance(distributions, list):
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "workspace_id、run_id 与 distributions 必填",
        )
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or not 10 <= sample_count <= 10_000:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "sample_count 必须为 10..10000 的整数")
    if not isinstance(seed, int) or isinstance(seed, bool):
        return _err_env(f"{SERVER_NAME}.invalid_argument", "seed 必须为整数")
    try:
        run = _load_consistent_run(wsid, run_id)
        if run is None:
            return _err_env(
                f"{SERVER_NAME}.base_run_unavailable",
                "基准 FinanceRun 不存在或未通过勾稽",
                status="blocked",
            )
        from lvke_mcp.domains.finance import finance_model
        from lvke_mcp.domains.finance.advanced_analysis import run_monte_carlo

        finance_inputs = run.get("input_revision")
        if not isinstance(finance_inputs, dict) or not finance_inputs:
            return _ok_env(
                {"available": False, "missing_inputs": ["input_revision"]},
                source=f"{SERVER_NAME}.finance_run_monte_carlo",
                status="missing_inputs",
                blockers=["基准 run 缺少可重放 input_revision"],
                monte_carlo_id=None,
                run_id=run_id,
                sample_count=sample_count,
            )
        context = run.get("project_context") if isinstance(run.get("project_context"), dict) else {}
        spec = run.get("spec") if isinstance(run.get("spec"), dict) else None

        def rerun(scales: dict[str, float]) -> dict[str, Any] | None:
            result = finance_model.compute_financials(
                finance_inputs,
                invest_type=str(context.get("invest_type") or run.get("invest_type") or ""),
                build_period_months=(
                    int(context["build_period_months"])
                    if context.get("build_period_months") is not None else None
                ),
                industry=str(context.get("industry") or run.get("industry") or ""),
                spec=spec,
                _apply_custom=False,
                _with_analysis=False,
                _revenue_scale=scales.get("revenue_scale", 1.0),
                _op_cost_scale=scales.get("operating_cost_scale", 1.0),
                _construction_scale=scales.get("construction_scale", 1.0),
            )
            # Scenario economics can legitimately breach DSCR/ICR or the base
            # investment working-capital target. Those are scenario outcomes,
            # not a failure to recompute the deterministic model. The model's
            # own ``available`` flag remains authoritative for sample validity.
            return result

        summary = run_monte_carlo(
            distributions=distributions,
            sample_count=sample_count,
            seed=seed,
            rerun=rerun,
        )
        if summary.get("field_errors"):
            return _ok_env(
                summary,
                source=f"{SERVER_NAME}.finance_run_monte_carlo",
                status="blocked",
                blockers=["distribution_manifest_invalid"],
                next_actions=["仅使用允许字段和合法的 uniform/triangular/normal 边界"],
                monte_carlo_id=None,
                run_id=run_id,
                sample_count=sample_count,
                field_errors=summary["field_errors"],
            )
        manifest_hash = sha256_json({
            "run_id": run_id,
            "input_hash": run.get("input_hash"),
            "spec_hash": run.get("spec_hash"),
            "distributions": distributions,
            "sample_count": sample_count,
            "seed": seed,
        })
        payload = {
            **summary,
            "workspace_id": wsid,
            "run_id": run_id,
            "run_input_hash": run.get("input_hash"),
            "run_spec_hash": run.get("spec_hash"),
            "distribution_manifest": distributions,
            "distribution_manifest_hash": manifest_hash,
            "formal_ready": bool(summary.get("available")),
        }
        record = MONTE_CARLO_STORE.put(
            wsid,
            payload,
            producer=f"{SERVER_NAME}.finance_run_monte_carlo",
            status="ok" if summary.get("available") else "blocked",
            source_ids=[run_id],
            basis={"manifest_hash": manifest_hash},
        )
        status = "ok" if summary.get("available") else "blocked"
        return _ok_env(
            record,
            source=f"{SERVER_NAME}.finance_run_monte_carlo",
            status=status,
            resource_uris=[record["resource_uri"]],
            blockers=[] if status == "ok" else ["所有 Monte Carlo 样本均未产生有效 IRR/NPV"],
            monte_carlo_id=record["object_id"],
            run_id=run_id,
            sample_count=sample_count,
            field_errors=[],
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_run_monte_carlo failed",
            f"{SERVER_NAME}.monte_carlo_failed",
            "执行 Monte Carlo 分析失败",
        )


def _tool_get_monte_carlo(args: dict) -> dict:
    return _tool_get_analysis(
        args,
        store=MONTE_CARLO_STORE,
        id_field="monte_carlo_id",
        source=f"{SERVER_NAME}.finance_get_monte_carlo",
    )


def _tool_list_analyses(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    resource_type = str(args.get("resource_type") or "all")
    stores = []
    if resource_type in {"all", "balance_sheet"}:
        stores.append(("balance_sheet", BALANCE_SHEET_STORE))
    if resource_type in {"all", "monte_carlo"}:
        stores.append(("monte_carlo", MONTE_CARLO_STORE))
    if resource_type in {"all", "basis_of_estimate"}:
        stores.append(("basis_of_estimate", BASIS_OF_ESTIMATE_STORE))
    if resource_type in {"all", "fact_pack"}:
        stores.append(("fact_pack", FACT_PACK_STORE))
    entries = [
        {
            "uri": record["resource_uri"],
            "name": record["object_id"],
            "mimeType": "application/json",
            "resource_type": kind,
            "content_hash": record["content_hash"],
            "basis_hash": record["basis_hash"],
            "status": record["status"],
        }
        for kind, store in stores
        for record in store.list(wsid)
    ]
    try:
        page = paginate_resource_entries(
            entries,
            cursor=str(args.get("cursor") or ""),
            limit=int(args.get("limit") or 50),
        )
    except ValueError as exc:
        return _err_env(f"{SERVER_NAME}.{exc}", "Resource 分页游标无效", status="blocked")
    return _ok_env(
        page,
        source=f"{SERVER_NAME}.finance_list_analyses",
        status="ok",
        analysis_count=len(page["resources"]),
        next_cursor=page["next_cursor"],
    )


def _resolve_analysis_resource(uri: str) -> dict | None:
    return (
        BALANCE_SHEET_STORE.resolve_uri(uri)
        or MONTE_CARLO_STORE.resolve_uri(uri)
        or BASIS_OF_ESTIMATE_STORE.resolve_uri(uri)
        or FACT_PACK_STORE.resolve_uri(uri)
    )


def _tool_read_analysis_resource(args: dict) -> dict:
    wsid = _ws(args)
    uri = str(args.get("uri") or "").strip()
    if not wsid or not uri:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 与 uri 必填")
    record = _resolve_analysis_resource(uri)
    if record is None or record.get("workspace_id") != wsid:
        return _err_env(
            f"{SERVER_NAME}.resource_not_found",
            "Resource 不存在或不属于当前工作区",
            status="blocked",
        )
    return _ok_env(
        record,
        source=f"{SERVER_NAME}.finance_read_analysis_resource",
        status="ok" if record.get("status") == "ok" else "partial",
        resource_uris=[record["resource_uri"]],
        object_id=record["object_id"],
        content_hash=record["content_hash"],
        basis_hash=record["basis_hash"],
    )


_GET_ANALYSIS_BRANCHES = {
    "balance_sheet": ("finance_get_balance_sheet", "balance_sheet_id"),
    "monte_carlo": ("finance_get_monte_carlo", "monte_carlo_id"),
    "basis_of_estimate": ("finance_get_basis_of_estimate", "basis_of_estimate_id"),
    "fact_pack": ("finance_get_fact_pack", "fact_pack_id"),
}


def _install_get_analysis_aggregate(
    server: OfficialStdioServer,
    annotations: types.ToolAnnotations,
) -> None:
    legacy = {
        name: server._tools[name]  # noqa: SLF001
        for name, _id_field in _GET_ANALYSIS_BRANCHES.values()
    }
    server._round2_legacy_specs = legacy  # type: ignore[attr-defined]  # noqa: SLF001
    first = next(iter(legacy.values()))
    workspace_schema = first.input_schema["properties"]["workspace_id"]
    id_schema = first.input_schema["properties"][
        next(iter(_GET_ANALYSIS_BRANCHES.values()))[1]
    ]
    output_properties: dict = {}
    for spec in legacy.values():
        output_properties.update(spec.output_schema.get("properties", {}))

    def dispatch(args: dict) -> dict:
        legacy_name, id_field = _GET_ANALYSIS_BRANCHES[str(args["kind"])]
        return legacy[legacy_name].handler(
            {"workspace_id": args["workspace_id"], id_field: args["target_id"]}
        )

    server.register_tool(
        name="finance_get_analysis",
        description="按分析类型读取已固化财务分析对象，不重算、不改变正式资格。",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": workspace_schema,
                "kind": {"type": "string", "enum": list(_GET_ANALYSIS_BRANCHES)},
                "target_id": id_schema,
            },
            "required": ["workspace_id", "kind", "target_id"],
        },
        handler=dispatch,
        output_schema=_output_schema(output_properties),
        annotations=annotations,
    )
    for name in legacy:
        server._tools.pop(name)  # noqa: SLF001

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "BALANCE_SHEET_STORE",
    "BASIS_OF_ESTIMATE_STORE",
    "EVIDENCE_STORE",
    "FACT_PACK_STORE",
    "FORMAL_EVIDENCE",
    "FormalLineageError",
    "JSONArtifactStore",
    "MONTE_CARLO_STORE",
    "OfficialStdioServer",
    "SERVER_NAME",
    "SIM_A_FORMAL",
    "SPEC_STORE",
    "_GET_ANALYSIS_BRANCHES",
    "_err_env",
    "_exception_env",
    "_install_get_analysis_aggregate",
    "_latest_formal_boe",
    "_load_consistent_run",
    "_ok_env",
    "_output_schema",
    "_planning_record",
    "_required_boe_pointers",
    "_resolve_analysis_resource",
    "_str_list",
    "_tool_build_balance_sheet",
    "_tool_build_basis_of_estimate",
    "_tool_get_analysis",
    "_tool_get_balance_sheet",
    "_tool_get_basis_of_estimate",
    "_tool_get_monte_carlo",
    "_tool_list_analyses",
    "_tool_read_analysis_resource",
    "_tool_run_monte_carlo",
    "_unique_strings",
    "_ws",
    "hashlib",
    "ok",
    "paginate_resource_entries",
    "project_fact_may_be_certified",
    "reconstruction_errors",
    "sha256_json",
    "types",
    "validate_formal_record",
    "validate_same_formal_lineage",
]
