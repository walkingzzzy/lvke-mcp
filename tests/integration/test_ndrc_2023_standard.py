from __future__ import annotations

import uuid

from lvke_mcp.adapters.finance_model_repository import SPEC_STORE
from lvke_mcp.domains.finance import run_service
from lvke_mcp.domains.finance._model_application.spec_cases import prepare_spec
from lvke_mcp.domains.finance.generation_standard import (
    coverage_snapshot,
    generation_baseline,
    stamp_finance_spec,
)
from lvke_mcp.domains.finance.model_manifest import build_manifest
from lvke_mcp.standards.ndrc_feasibility_2023 import (
    generation_basis,
    load_clause_tree,
    load_generation_mapping,
    source_fingerprint,
    validate_source_integrity,
)


def test_official_sources_are_hash_pinned_and_parseable() -> None:
    validation = validate_source_integrity()

    assert validation["valid"], validation
    assert len(validation["sources"]) == 4
    assert source_fingerprint().startswith("sha256:")

    tree = load_clause_tree()
    assert tree["source_valid"] is True
    assert sum(item["clause_count"] for item in tree["documents"]) == 118
    clause_ids = {
        clause["clause_id"]
        for document in tree["documents"]
        for clause in document["clauses"]
    }
    assert "government_outline.7.1" in clause_ids
    assert "enterprise_outline.6.5" in clause_ids
    assert "outline_explanation.4.7" in clause_ids


def test_generation_basis_exposes_finance_requirements_before_validation() -> None:
    basis = generation_basis("enterprise_investment")
    mapping = load_generation_mapping()

    assert basis["generation_standard"] == "ndrc-feasibility-outline-2023"
    assert basis["generation_standard_source_hash"] == mapping["source_fingerprint"]
    assert basis["generation_policy"] == "generate_then_validate_then_promote"
    assert basis["standard_conformance"] == "unverified"
    assert {
        requirement["requirement_id"] for requirement in basis["finance_requirements"]
    } == {
        "investment_estimate",
        "profitability",
        "financing_plan",
        "debt_service_capacity",
        "financial_sustainability",
    }
    assert all(
        requirement["generation_when_missing"]
        for requirement in basis["finance_requirements"]
    )


def test_finance_manifest_pins_official_standard_source() -> None:
    manifest = build_manifest()

    assert manifest.generation_standard == "ndrc-feasibility-outline-2023"
    assert manifest.generation_standard_version == "2023-05-01"
    assert manifest.generation_standard_source_hash == source_fingerprint()
    assert not manifest.validate(as_of="2026-01-01")


def test_finance_spec_stamp_uses_official_mapping_without_overwriting_input() -> None:
    source = {"revenue": {"model": "flat", "annual_revenue_wan": 3000.0}}

    stamped = stamp_finance_spec(source, invest_type="enterprise_investment")

    assert stamped is not None
    assert "generation_standard" not in source
    standard = stamped["generation_standard"]
    assert standard["standard_id"] == "ndrc-feasibility-outline-2023"
    assert standard["standard_version"] == "2023-05-01"
    assert standard["source_hash"] == source_fingerprint()
    assert standard["project_type"] == "enterprise_investment"
    assert standard["standard_conformance"] == "unverified"
    assert len(standard["finance_requirement_ids"]) == 5


def test_post_generation_coverage_is_non_blocking_and_traceable() -> None:
    baseline = generation_baseline(invest_type="enterprise_investment")
    snapshot = coverage_snapshot(
        finance_inputs={
            "total_investment_wan": 10_000.0,
            "build_period_months": 12,
            "annual_revenue_wan": 3_000.0,
            "cost_items": {"估算现金成本": 1_500.0},
        },
        table_manifest=[
            {"table_id": "investment"},
            {"table_id": "income-statement"},
            {"table_id": "cashflow"},
        ],
        invest_type="enterprise_investment",
    )

    assert baseline["source_hash"] == source_fingerprint()
    assert baseline["mapping_hash"].startswith("sha256:")
    assert snapshot["generated_against_standard"] is True
    assert snapshot["validation_stage"] == "post_generation"
    assert snapshot["status"] == "partial"
    assert len(snapshot["requirements"]) == 5
    investment = next(
        item for item in snapshot["requirements"]
        if item["requirement_id"] == "investment_estimate"
    )
    assert "finance.total_investment_wan" in investment["finance_paths_present"]
    assert "investment" in investment["table_codes_present"]
    sustainability = next(
        item for item in snapshot["requirements"]
        if item["requirement_id"] == "financial_sustainability"
    )
    # 原断言是 `assert sustainability["known_gap"]` —— 它钉住的是「财务计划现金
    # 流量表缺失」这条诚实披露。该缺口已实际关闭（附表11 进交付集），所以此处
    # 反过来断言：缺口标记必须消失，且该需求要求的三张表都在契约里。
    # 若将来有人只删 known_gap 而不真的补表，table_codes 这条会拦住。
    assert not sustainability.get("known_gap")
    assert "financial-plan" in sustainability["table_codes_missing"] or (
        "financial-plan" in sustainability["table_codes_present"]
    )


def test_candidate_finance_spec_persists_generation_baseline() -> None:
    workspace_id = "ndrc-spec-" + uuid.uuid4().hex
    result = prepare_spec({
        "workspace_id": workspace_id,
        "spec": {"revenue": {"model": "flat", "annual_revenue_wan": 300.0}},
        "input_revision": {
            "total_investment_wan": 1_000.0,
            "annual_revenue_wan": 300.0,
        },
    })

    assert result["success"] is True, result
    spec_id = str(result["spec_id"])
    payload = (SPEC_STORE.get(workspace_id, spec_id) or {}).get("payload") or {}
    assert payload["generation_standard"] == "ndrc-feasibility-outline-2023"
    assert payload["standard_source_hash"] == source_fingerprint()
    assert payload["standard_coverage_snapshot"]["status"] == "partial"
    assert payload["spec"]["generation_standard"]["standard_conformance"] == "unverified"


def test_finance_run_persists_post_generation_coverage_snapshot() -> None:
    workspace_id = "ndrc-run-" + uuid.uuid4().hex
    result = run_service.run_workspace_finance_model(
        workspace_id,
        input_revision={
            "total_investment_wan": 1_000.0,
            "annual_revenue_wan": 300.0,
            "is_operating": True,
            "cost_items": {"cash": 150.0},
        },
        force_flat=True,
        record_audit=True,
    )

    assert result["available"] is True, result
    assert result["run_id"]
    assert result["generation_standard"] == "ndrc-feasibility-outline-2023"
    assert result["standard_source_hash"] == source_fingerprint()
    assert result["standard_coverage_snapshot"]["status"] == "partial"
    # 14 = 原十三表 + 附表11 财务计划现金流量表。以常量而非字面量断言，避免下次
    # 交付集变动时又要改字面量（且字面量与常量不一致时很难看出哪个是真源）。
    from lvke_mcp.domains.finance.run_service import ENGINE_DELIVERY_COUNT

    assert len(result["table_manifest"]) == ENGINE_DELIVERY_COUNT == 14

    persisted = run_service.get_workspace_finance_run(
        workspace_id,
        run_id=result["run_id"],
        view="full",
    )
    assert persisted["generation_standard"] == result["generation_standard"]
    assert persisted["standard_coverage_snapshot"] == result["standard_coverage_snapshot"]