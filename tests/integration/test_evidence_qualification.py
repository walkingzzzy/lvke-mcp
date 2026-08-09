from __future__ import annotations

import inspect
import os
import tempfile
import unittest

from lvke_mcp.adapters.project_planning_repository import (
    MARKET_CASE_STORE,
    PROJECT_CONTEXT_STORE,
)
from lvke_mcp.domains.project_planning._service.factories import (
    create_revenue_driver_set,
)
from lvke_mcp.runtime.evidence_qualification import (
    combine_evidence_policies,
    project_fact_may_be_certified,
)


class EvidenceQualificationTest(unittest.TestCase):
    def test_only_explicit_formal_fully_certified_lineage_can_certify(self) -> None:
        formal_parent = {
            "evidence_policy": "formal_evidence",
            "project_fact_certified": True,
        }
        for policy in (
            "controlled_assumption",
            "source_reconstructed",
            "technical_fixture",
            "candidate",
            "real",
            "browser_snapshot",
        ):
            with self.subTest(policy=policy):
                self.assertFalse(project_fact_may_be_certified(
                    policy,
                    own_qualification_passed=True,
                    parents=[formal_parent],
                ))
        self.assertFalse(project_fact_may_be_certified(
            "formal_evidence",
            own_qualification_passed=True,
            parents=[{"evidence_policy": "controlled_assumption", "project_fact_certified": True}],
        ))
        self.assertFalse(project_fact_may_be_certified(
            "formal_evidence",
            own_qualification_passed=False,
            parents=[formal_parent],
        ))
        self.assertTrue(project_fact_may_be_certified(
            "formal_evidence",
            own_qualification_passed=True,
            parents=[formal_parent],
        ))
        self.assertEqual(
            combine_evidence_policies([
                formal_parent,
                {"evidence_policy": "controlled_assumption"},
            ]),
            "controlled_assumption",
        )

    def test_controlled_market_cannot_upgrade_revenue_driver(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lvke-planning-evidence-") as root:
            previous = os.environ.get("LVKE_MCP_DATA_DIR")
            os.environ["LVKE_MCP_DATA_DIR"] = root
            try:
                workspace = "planning-evidence-test"
                context = PROJECT_CONTEXT_STORE.put(
                    workspace,
                    {
                        "object_type": "ProjectContext",
                        "project_name": "test",
                        "evidence_track": "controlled_assumption",
                        "status": "confirmed",
                    },
                    producer="test",
                )
                market = MARKET_CASE_STORE.put(
                    workspace,
                    {
                        "object_type": "MarketSizingCase",
                        "project_context_id": context["object_id"],
                        "evidence_track": "controlled_assumption",
                        "evidence_policy": "controlled_assumption",
                        "project_fact_certified": False,
                        "status": "confirmed",
                        "selection": {"selected_candidate": {}},
                    },
                    producer="test",
                    status="confirmed",
                    source_ids=[context["object_id"]],
                )
                result = create_revenue_driver_set(
                    workspace,
                    context["object_id"],
                    market["object_id"],
                    {"model": "gov_payment", "annual_gov_payment_wan": 1000},
                    3,
                    idempotency_key="controlled-revenue-driver",
                )
                self.assertTrue(result["success"], result)
                self.assertEqual(result["evidence_policy"], "controlled_assumption")
                self.assertFalse(result["project_fact_certified"])
                self.assertFalse(result["revenue_driver_set"]["project_fact_certified"])
            finally:
                if previous is None:
                    os.environ.pop("LVKE_MCP_DATA_DIR", None)
                else:
                    os.environ["LVKE_MCP_DATA_DIR"] = previous


class DomainCertificationDefaultsTest(unittest.TestCase):
    """各域投影都不得把「非重建」或「调用方自报」当成已认证。

    这些点此前各自本地判断，缺省是 True：只要 evidence_policy 不是
    source_reconstructed 就认证项目事实，controlled_assumption 与
    technical_fixture 因此能拿到 project_fact_certified=true。
    """

    NON_FORMAL = (
        "controlled_assumption",
        "technical_fixture",
        "source_reconstructed",
        "candidate",
        "browser_snapshot",
        "real",
        "estimate_preview",
    )

    def test_fact_pack_snapshot_ignores_self_reported_certification(self) -> None:
        from lvke_mcp.domains.finance._fact_pack import snapshot as fact_pack_snapshot

        source = inspect.getsource(fact_pack_snapshot)
        self.assertIn("project_fact_may_be_certified", source)
        self.assertNotIn(
            'raw.get("project_fact_certified", evidence_policy != "source_reconstructed")',
            source,
        )

    def test_finance_and_acquisition_projections_use_the_shared_gate(self) -> None:
        from lvke_mcp.domains.asset_acquisition._backend import runs as acquisition_runs
        from lvke_mcp.domains.finance import evidence_binding
        from lvke_mcp.domains.finance._model_application import run_cases, spec_cases
        from lvke_mcp.servers.lvke_asset_acquisition import service as acquisition_service
        from lvke_mcp.servers.lvke_finance_model._server import analysis_tools

        for module in (
            evidence_binding,
            run_cases,
            spec_cases,
            analysis_tools,
            acquisition_runs,
            acquisition_service,
        ):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertIn("project_fact_may_be_certified", source)
                # 这些是被替换掉的缺省 True 写法，不应再出现。
                self.assertNotIn('"project_fact_certified": not reconstructed', source)
                self.assertNotIn('"project_fact_certified": not bool(reconstructed)', source)

    def test_no_non_formal_policy_can_certify_regardless_of_qualification(self) -> None:
        for policy in self.NON_FORMAL:
            for passed in (True, False):
                with self.subTest(policy=policy, own_qualification_passed=passed):
                    self.assertFalse(project_fact_may_be_certified(
                        policy, own_qualification_passed=passed
                    ))

    def test_formal_still_requires_its_own_gate_to_pass(self) -> None:
        self.assertTrue(project_fact_may_be_certified(
            "formal_evidence", own_qualification_passed=True
        ))
        self.assertFalse(project_fact_may_be_certified(
            "formal_evidence", own_qualification_passed=False
        ))


if __name__ == "__main__":
    unittest.main()
