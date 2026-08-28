"""Lightweight outputSchema and release preflight regression tests."""

from __future__ import annotations

import json
from importlib import import_module

import jsonschema
import pytest

from lvke_mcp.runtime.release_preflight import run_release_preflight
from lvke_mcp.runtime.schemas import make_lightweight_output_schema
from lvke_mcp.testing.server_manifest import SERVER_SPECS


def test_lightweight_output_schema_has_required_envelope_fields() -> None:
    schema = make_lightweight_output_schema(
        schema_uri="lvke://schemas/lvke-finance-model/finance_run_model/output",
    )
    for field in (
        "success",
        "business_success",
        "system_success",
        "transport_success",
        "status",
        "resource_uris",
        "warnings",
        "blockers",
        "next_actions",
        "trace_id",
    ):
        assert field in schema["properties"]
    assert schema["x-lvke-output-schema-uri"].endswith("/output")


@pytest.mark.parametrize("spec", SERVER_SPECS, ids=lambda item: item.name)
def test_all_tools_publish_lightweight_output_schema(spec) -> None:
    module = import_module(spec.module)
    server = getattr(module, "SERVER", None) or module.build_server()
    for tool in server.tool_specs:
        public = server._public_output_schema(tool)  # noqa: SLF001
        jsonschema.Draft202012Validator.check_schema(public)
        assert public["x-lvke-output-schema-uri"] == (
            f"lvke://schemas/{spec.name}/{tool.name}/output"
        )


def test_release_preflight_splits_calculation_from_artifact_failure() -> None:
    report = run_release_preflight(
        calculation_checks=lambda: (["model ok"], []),
        required_artifacts=[],
        evd_distribution={"EVD-0": 20, "EVD-1": 4, "EVD-2": 0},
        sim_a_present=True,
        build_metadata_complete=True,
    )
    payload = report.to_dict()
    assert payload["calculation_gate"]["status"] == "pass"
    assert payload["artifact_gate"]["status"] == "pass"
    assert payload["evidence_gate"]["status"] == "fail"
    assert payload["release_gate"]["status"] == "fail"
    assert payload["release_ready"] is False


def test_release_preflight_formal_candidate_requires_evd2() -> None:
    report = run_release_preflight(
        calculation_checks=lambda: (["model ok"], []),
        evd_distribution={"EVD-0": 0, "EVD-1": 0, "EVD-2": 24},
        build_metadata_complete=True,
    )
    payload = report.to_dict()
    assert payload["evidence_gate"]["status"] == "pass"
    assert payload["release_gate"]["status"] == "pass"
    assert payload["release_ready"] is True
