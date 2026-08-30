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
            # 技术报告对象必须存在：它是审查目标，缺失即"审查没有可审对象"，
            # 那是独立的 fail-closed 判据，不属于本用例要守的语义。
            "object_refs": {"technical_report_id": "zmrep_stub"},
            "resource_uris": [],
            "blockers": ["xlsx_export_failed"],
            "quality_issues": [],
            # manifest / 配置 hash / 组件齐备是交付完整性判据，缺失即阻断。
            # 本用例要守的是"证据待补、勾稽提示、导出失败按质量项放行"，
            # 因此桩必须提供完整的交付产物，否则测的就变成了完整性缺失。
            "manifest_uri": "lvke://zero-material-delivery/workspaces/test/manifests/m1",
            "report_profile": {"profile_content_hash": "sha256:" + "a" * 64},
            "component_status": {},
            "unresolved_slots": [],
            "finance_summary": {"consistency_ok": True},
        },
    )
    # 技术审查链在本用例里不是被测对象。桩成"审查通过、无附加问题"，
    # 让断言聚焦于真正要守的语义：研究证据待补、勾稽提示、导出失败这三类
    # 必须按质量项放行，而不是被审查侧的 fail-closed 判据盖住。
    #
    # 审查本身的 fail-open/fail-closed 行为由 test_delivery_status_honesty.py
    # 的 ReviewStartFailOpenTest 与 StartResponseHonestyTest 专门覆盖。
    monkeypatch.setattr(
        lifecycle,
        "run_technical_acceptance",
        lambda *args, **kwargs: {
            "status": "passed",
            "review_preparation_id": "rvprep_stub",
            "review_id": "review_stub",
            "review_package_id": "rvpkg_stub",
            "feasibility_validation_id": "",
            "domain_results": [],
            "blockers": [],
            "limitations": [],
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
