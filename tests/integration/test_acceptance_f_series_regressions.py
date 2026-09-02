"""回归：2026-09-01 对话式验收仍开着的 F 系列缺陷。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid

from jsonschema.exceptions import ValidationError

from lvke_mcp.domains.finance.checks import run_checks
from lvke_mcp.domains.finance.revenue_models import expand
from lvke_mcp.domains.project_planning import application as planning
from lvke_mcp.domains.project_planning._service.factories import create_cost_driver_set
from lvke_mcp.runtime.quality_severity import is_blocking
from lvke_mcp.runtime.transport import OfficialStdioServer, _schema_validation_message
from lvke_mcp.servers.lvke_project_planning._lifecycle.cost import _calculated_cost_items


def _put(store, workspace_id: str, payload: dict, *, status: str = "confirmed") -> str:
    record = store.put(
        workspace_id,
        payload,
        producer="tests.acceptance-f-series",
        status=status,
        basis={"test_case": payload["object_type"], "nonce": uuid.uuid4().hex},
    )
    return str(record["object_id"])


def _planning_basis(workspace_id: str) -> tuple[str, str]:
    context_id = _put(
        planning.PROJECT_CONTEXT_STORE,
        workspace_id,
        {
            "object_type": "ProjectContext",
            "project_name": "江夏智慧农业产业园",
            "industry_code": "农业",
            "project_type": "new_build",
            "status": "confirmed",
            "evidence_track": "controlled_assumption",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
        },
    )
    scale_id = _put(
        planning.BUILD_SCALE_STORE,
        workspace_id,
        {
            "object_type": "BuildScaleCase",
            "project_context_id": context_id,
            "status": "confirmed",
            "evidence_track": "controlled_assumption",
            "evidence_policy": "controlled_assumption",
            "project_fact_certified": False,
        },
    )
    return context_id, scale_id


class CostTotalAndTraceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-f5-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_total_investment_does_not_take_other_wan(self) -> None:
        workspace_id = "f5-total-" + uuid.uuid4().hex
        context_id, scale_id = _planning_basis(workspace_id)
        created = create_cost_driver_set(
            workspace_id,
            context_id,
            scale_id,
            {
                "civil_wan": 1800,
                "equipment_wan": 7800,
                "installation_wan": 2000,
                "other_wan": 4200,
                "reserve_wan": 1200,
                "interest_wan": 900,
                "working_capital_wan": 1500,
            },
            [
                {"name": "工资", "annual_amount_wan": 100},
                {"name": "电费", "annual_amount_wan": 80},
                {"name": "修理费", "annual_amount_wan": 60},
            ],
            selection={"confirmation_reason": "按八项构成确认总投资口径"},
            idempotency_key="f5-total",
        )
        self.assertTrue(created["success"], created)
        payload = (created.get("cost_driver_set") or {}).get("payload") or created
        total = payload.get("project_total_investment_wan")
        if total is None:
            record = planning.COST_DRIVER_STORE.get(
                workspace_id, created["cost_driver_set_id"]
            )
            total = (record or {}).get("payload", {}).get("project_total_investment_wan")
        # 建设五项 1800+7800+2000+4200+1200=17000，再加利息 900、流资 1500
        self.assertEqual(total, 19400.0)
        self.assertNotEqual(total, 4200)

    def test_quantity_method_survives_amount_writeback(self) -> None:
        first, errors = _calculated_cost_items([{
            "name": "原料",
            "annual_quantity": 5400,
            "unit_consumption": 1,
            "unit_price_yuan": 1800,
            "conversion_to_wan": 0.0001,
            "loss_rate": 0.03,
        }])
        self.assertEqual(errors, [])
        self.assertEqual(first[0]["calculation_trace"]["method"], "quantity_consumption_price")
        second, _ = _calculated_cost_items(first)
        self.assertEqual(second[0]["calculation_trace"]["method"], "quantity_consumption_price")


class AgricultureRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-f8-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_chinese_agriculture_resolves_skill_route(self) -> None:
        workspace_id = "f8-agri-" + uuid.uuid4().hex
        context_id, _ = _planning_basis(workspace_id)
        resolved = planning.resolve_industry_skill(workspace_id, context_id)
        self.assertNotEqual(resolved.get("code"), "industry_skill_route_not_found", resolved)
        route_id = resolved.get("route_id") or (resolved.get("lineage") or {}).get("route_id")
        self.assertEqual(route_id, "agriculture")


class FlatRampStillConsumedTest(unittest.TestCase):
    def test_flat_ramp_is_not_dropped(self) -> None:
        out = expand(
            {"revenue": {"model": "flat", "annual_revenue_wan": 6660, "ramp": [0.6, 0.85, 1]}},
            10,
        )
        self.assertEqual(out["revenue_by_year"][:3], [3996.0, 5661.0, 6660.0])


class ConsistencyBlockingIssuesTest(unittest.TestCase):
    def test_cross_table_failure_is_blocking_code(self) -> None:
        self.assertTrue(is_blocking("finance_run_object_required"))
        self.assertTrue(is_blocking("report_revision_required"))
        self.assertTrue(is_blocking("review_run_required"))
        hits = [
            item
            for item in run_checks({
                "funding": {"capital": 11200, "loan": 16800, "subsidy": 0},
                "annual": {"financial_plan": [
                    {"phase": "建设期", "capital_own": 13100, "loan_draw": 0, "gov_subsidy": 0},
                    {"phase": "建设期", "capital_own": 13100, "loan_draw": 0, "gov_subsidy": 0},
                ]},
            })
            if item["rule"] == "附表11建设期融资结构=附表4资金筹措"
        ]
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0]["ok"])
        self.assertTrue(hits[0]["blocking"])


class NestedOneOfAcceptedKeysTest(unittest.TestCase):
    def test_wrong_branch_lists_accepted_keys(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "revenue_spec": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {
                                "model": {"const": "flat"},
                                "annual_revenue_wan": {"type": "number"},
                            },
                            "required": ["model", "annual_revenue_wan"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "model": {"const": "tourism"},
                                "annual_visitors": {"type": "number"},
                            },
                            "required": ["model", "annual_visitors"],
                            "additionalProperties": False,
                        },
                    ]
                }
            },
            "required": ["revenue_spec"],
        }
        try:
            OfficialStdioServer._validate(
                {"revenue_spec": {"model": "flat", "annual_visitors": 1}},
                schema,
            )
        except ValidationError as exc:
            message = _schema_validation_message(exc)
        else:
            self.fail("expected schema validation to fail")
        self.assertIn("Accepted keys", message)
        self.assertIn("annual_revenue_wan", message)


class FinancingPromotionTest(unittest.TestCase):
    def test_nested_financing_is_lifted(self) -> None:
        from lvke_mcp.domains.finance._model_application.spec_cases import (
            _canonical_candidate_inputs,
        )

        inputs, _adoption, rejected = _canonical_candidate_inputs(
            {
                "cost": {"cost_items": {"经营成本": 100}},
                "tax": {"income_tax_rate": 0.25},
                "financing": {"capital_own_wan": 4000, "loan_wan": 6000},
                "revenue": {"model": "flat", "annual_revenue_wan": 1000},
            },
            None,
            {},
        )
        self.assertEqual(rejected, [])
        self.assertEqual(inputs.get("capital_own_wan"), 4000)
        self.assertEqual(inputs.get("loan_wan"), 6000)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
