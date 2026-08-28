from __future__ import annotations

from lvke_mcp.runtime.outcomes import normalize_operation_outcome


def _normalize(payload: dict[str, object]) -> dict[str, object]:
    return normalize_operation_outcome(payload, server_name="lvke-contract-test")


def test_partial_result_is_completed_success_with_quality_status_preserved() -> None:
    result = _normalize({
        "success": False,
        "status": "partial",
        "artifact_id": "artifact-1",
        "quality_valid": False,
        "quality_issues": ["evidence_incomplete"],
        "blockers": [],
    })

    assert result["success"] is True
    assert result["business_success"] is True
    assert result["system_success"] is True
    assert result["transport_success"] is True
    assert result["completed"] is True
    assert result["result_available"] is True
    assert result["status"] == "partial"
    assert result["quality_valid"] is False
    assert result["quality_issues"] == ["evidence_incomplete"]
    assert "code" not in result


def test_empty_discovery_is_completed_without_fabricating_result() -> None:
    result = _normalize({
        "success": False,
        "status": "empty",
        "candidates": [],
        "warnings": ["no_candidates"],
    })

    assert result["success"] is True
    assert result["completed"] is True
    assert result["result_available"] is False
    assert result["candidates"] == []
    assert "code" not in result


def test_accepted_is_successful_but_not_completed() -> None:
    result = _normalize({
        "status": "running",
        "task_id": "task-1",
    })

    assert result["status"] == "accepted"
    assert result["success"] is True
    assert result["completed"] is False
    assert result["task_status"] == "running"


def test_unexecutable_statuses_remain_unsuccessful() -> None:
    for status in ("missing_inputs", "blocked", "incomplete", "failed", "upstream_failure"):
        result = _normalize({"status": status})

        assert result["success"] is False
        assert result["business_success"] is False
        assert result["completed"] is False
        assert result["code"] == f"lvke-contract-test.{status}"