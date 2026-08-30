from __future__ import annotations

import hashlib

from lvke_mcp.adapters.data_acquisition_repository import SOURCE_STORE
from lvke_mcp.servers.lvke_deliverable_review._service.target_resolve import _resolve_report_artifact
from lvke_mcp.servers.lvke_zero_material_delivery._service.base import REPORT_STORE
from lvke_mcp.domains.research import application as research
from lvke_mcp.servers.lvke_deliverable_review._service.suite_review import _dimension_results
from lvke_mcp.servers.lvke_deliverable_review.contracts import verdict_for
from lvke_mcp.servers.lvke_zero_material_delivery._service.routing import _resolve_route


def test_quick_dimension_without_assessment_is_incomplete() -> None:
    state = {"findings": [], "incomplete_reasons": []}
    result = _dimension_results(state, {}, {}, require_semantic=False)
    assert result
    assert all(item["status"] == "incomplete" for item in result)
    assert all(item["role_confirmed"] is False for item in result)
    assert all("quick_preview_without_semantic_assessment" in item["limitations"] for item in result)


def test_confirmed_blocking_finding_remains_active() -> None:
    finding = {"finding_id": "f1", "severity": "P1", "status": "confirmed"}
    assert verdict_for([finding], []) == "fail"


def test_solar_route_is_energy_route_and_environment_alias_is_normalized() -> None:
    route = _resolve_route("建设光伏电站")
    assert route["industry_code"] == "energy_utilities"
    assert route["asset_type"] == "solar_power"
    alias = _resolve_route("建设光伏电站", "environment_utilities")
    assert alias["industry_code"] == "energy_utilities"
    assert alias["compatibility_warnings"] == ["environment_utilities_deprecated_for_energy_project"]


def test_dr_submit_inherits_sources_from_latest_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LVKE_MCP_DATA_DIR", str(tmp_path))
    workspace = "research-plan-inheritance"
    content = "计划来源正文"
    digest = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    SOURCE_STORE.put(
        workspace,
        {"content": content, "external_content_hash": digest, "title": "source"},
        producer="test.review-regression",
        object_id="source-plan-1",
    )
    started = research.start_agent({
        "workspace_id": workspace,
        "topic": "计划继承测试",
        "industry": "制造业",
        "region": "湖北",
        "plan_items": [],
        "idempotency_key": "start-plan-inheritance",
    })
    plan = research.get_plan(workspace, started["task_id"])
    descriptor = {
        "source_type": "source_snapshot",
        "object_id": "source-plan-1",
        "resource_uri": f"lvke://data-acquisition/workspaces/{workspace}/sources/source-plan-1",
        "content_hash": digest,
        "locator": "web_snapshot",
        "evidence_track": "real",
        "allowed_uses": ["fact_extraction"],
    }
    added = research.add_sources({
        "workspace_id": workspace,
        "task_id": started["task_id"],
        "expected_basis_hash": plan["basis_hash"],
        "sources": [descriptor],
    })
    assert added["status"] == "ok"
    submitted = research.submit_agent({
        "workspace_id": workspace,
        "task_id": started["task_id"],
        "report_md": "研究提交",
        "citations": [{"source_id": "source-plan-1", "locator": "web_snapshot", "content_hash": digest}],
    })
    assert submitted["status"] == "partial"
    assert submitted["plan_revision_id"] == added["plan_revision_id"]


def test_zero_material_report_is_resolvable_as_preview(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LVKE_MCP_DATA_DIR", str(tmp_path))
    workspace = "zero-material-adapter"
    record = REPORT_STORE.put(
        workspace,
        {
            "object_type": "TechnicalReport",
            "assurance_level": "estimate_preview",
            "validation_complete": False,
            "input_evidence_complete": False,
            "finance_run_id": "run-preview",
        },
        producer="test.review-regression",
        status="partial",
    )
    snapshot, bindings, blockers = _resolve_report_artifact(
        workspace,
        record["object_id"],
        artifact_domain="zero_material_preview",
    )
    assert blockers == []
    assert snapshot["artifact_family"] == "zero_material_preview"
    assert snapshot["assurance_level"] == "estimate_preview"
    assert bindings["finance_run_id"] == "run-preview"
