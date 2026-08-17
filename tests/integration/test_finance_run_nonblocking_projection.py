from __future__ import annotations

import ast
import uuid
from pathlib import Path

from lvke_mcp.domains.finance import run_service
from lvke_mcp.domains.finance._model_application.run_cases import run_model


def _degraded_run() -> dict:
    return {
        "available": True,
        "ok": False,
        "run_id": "run_nonblocking_projection",
        "consistency_ok": False,
        "calculation_status": "succeeded",
        "missing_inputs": ["wc_turnover.receivable", "wc_turnover.inventory"],
        "blocking_issues": [
            {"rule": "finance_consistency_failed", "detail": "测试勾稽差异"},
        ],
        "quality_issues": ["engine_quality_issue"],
        "warnings": [],
        "viability_status": "not_viable",
        "viability_issues": ["negative_npv"],
    }


def test_available_run_with_missing_turnover_and_consistency_issue_is_partial(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        run_service,
        "run_workspace_finance_model",
        lambda *args, **kwargs: _degraded_run(),
    )
    workspace_id = "finance-gateless-" + uuid.uuid4().hex

    result = run_model({
        "workspace_id": workspace_id,
        "idempotency_key": "missing-turnover-" + uuid.uuid4().hex,
        "mode": "review_candidate",
        "spec": {"revenue": {"model": "flat", "annual_revenue_wan": 100.0}},
        "input_revision": {
            "total_investment_wan": 1000.0,
            "annual_revenue_wan": 100.0,
            "is_operating": True,
            "invest_breakdown": {"working_capital_wan": 100.0},
        },
    })

    assert result["success"] is True, result
    assert result["status"] == "partial"
    assert result["run_id"] == "run_nonblocking_projection"
    assert result["blockers"] == []
    assert result["data"]["blocking_issues"] == []
    quality = result["data"]["quality_issues"]
    assert "missing_input:wc_turnover.receivable" in quality
    assert "missing_input:wc_turnover.inventory" in quality
    assert "engine_quality_issue" in quality


def test_legacy_run_entry_delegates_to_governed_application() -> None:
    path = Path("src/lvke_mcp/servers/lvke_finance_model/_server/run_tools.py")
    module = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_legacy_tool_run_model"
    )
    first_statement = function.body[0]

    assert isinstance(first_statement, ast.Return)
    assert isinstance(first_statement.value, ast.Call)
    assert isinstance(first_statement.value.func, ast.Name)
    assert first_statement.value.func.id == "_tool_run_model"