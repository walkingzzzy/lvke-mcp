from __future__ import annotations

import uuid

from lvke_mcp.servers.lvke_zero_material_delivery import artifact_delivery
from lvke_mcp.servers.lvke_zero_material_delivery._service import lifecycle
from lvke_mcp.servers.lvke_zero_material_delivery._service.assumptions import (
    _build_assumption_package,
)
from lvke_mcp.servers.lvke_zero_material_delivery._service.intake import create_from_sentence
from lvke_mcp.servers.lvke_zero_material_delivery._service.orchestration import execute


def test_start_keeps_generated_run_successful_when_artifacts_have_quality_issues(
    monkeypatch,
) -> None:
    workspace_id = "zero-material-gateless-" + uuid.uuid4().hex
    created = create_from_sentence({
        "workspace_id": workspace_id,
        "sentence": "湖北咸安低空经济农旅融合项目可行性研究",
        "industry": "tourism_catering",
        "idempotency_key": "create-" + uuid.uuid4().hex,
    })
    delivery_run_id = created["delivery_run"]["delivery_run_id"]

    monkeypatch.setattr(
        lifecycle,
        "execute",
        lambda *args, **kwargs: {
            "status": "partial",
            "stage": "finance_ready",
            "warnings": ["研究证据不足"],
            "blockers": ["research_evidence_pending"],
            "quality_issues": ["finance_consistency_failed"],
            "object_refs": {"finance_run_id": "run_partial"},
            "resource_uris": ["lvke://finance-model/workspaces/test/runs/run_partial"],
        },
    )
    monkeypatch.setattr(
        artifact_delivery,
        "build_delivery_artifacts",
        lambda *args, **kwargs: {
            "object_refs": {},
            "resource_uris": [],
            "blockers": ["xlsx_export_failed"],
            "quality_issues": [],
            "manifest_uri": "",
        },
    )

    result = lifecycle.start({
        "workspace_id": workspace_id,
        "delivery_run_id": delivery_run_id,
        "idempotency_key": "start-" + uuid.uuid4().hex,
    })

    assert result["success"] is True, result
    assert result["business_success"] is True
    assert result["completed"] is True
    assert result["status"] == "partial"
    assert result["blockers"] == []
    assert result["delivery_run"]["blockers"] == []
    assert result["technical_preview_ready"] is False
    assert set(result["quality_issues"]) == {
        "research_evidence_pending",
        "finance_consistency_failed",
        "xlsx_export_failed",
    }


def test_generic_finance_route_preserves_scenario_invest_type() -> None:
    workspace_id = "zero-material-finance-contract-" + uuid.uuid4().hex
    created = create_from_sentence({
        "workspace_id": workspace_id,
        "sentence": "湖北咸安低空经济农旅融合项目可行性研究",
        "industry": "tourism_catering",
        "idempotency_key": "create-" + uuid.uuid4().hex,
    })
    intent = created["delivery_intent"]
    assumption_package = _build_assumption_package(intent)
    assumption_package["assumption_package_id"] = "assumption-" + uuid.uuid4().hex

    result = execute(
        workspace_id,
        intent,
        assumption_package,
        operation_key="execute-" + uuid.uuid4().hex,
    )

    assert result["stage"] == "tables_ready", result
    assert result["blockers"] == []
    assert result["finance_preparation"]["spec_id"]
    assert result["finance_run"]["run_id"]
    assert result["tables"]["finance_tables_package_id"]
    assert result["xlsx_export"]["xlsx_resource"]
    assert "candidate_input_invalid" not in result["quality_issues"]
