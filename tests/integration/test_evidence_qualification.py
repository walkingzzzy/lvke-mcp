from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
