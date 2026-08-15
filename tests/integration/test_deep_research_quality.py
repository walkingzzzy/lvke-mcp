from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from lvke_mcp.adapters.research_repository import PACKAGE_STORE, QUALITY_REVIEW_STORE
from lvke_mcp.domains.research import application
from lvke_mcp.domains.research._service.agent_lifecycle import (
    _citation_consistency_issues,
)


class DeepResearchQualityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-dr-quality-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "dr-quality-test"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_citation_audit_accepts_equivalent_sha256_wire_forms(self) -> None:
        digest = "a" * 64
        issues = _citation_consistency_issues(
            self.workspace,
            [{
                "source_id": "source-1",
                "locator": "document_text",
                "content_hash": f"sha256:{digest}",
            }],
            [{"sources": [{"source_id": "source-1", "content_hash": digest}]}],
            ["source-1"],
        )

        self.assertEqual(issues, [])
        mismatch = _citation_consistency_issues(
            self.workspace,
            [{
                "source_id": "source-1",
                "locator": "document_text",
                "content_hash": f"sha256:{'b' * 64}",
            }],
            [{"sources": [{"source_id": "source-1", "content_hash": digest}]}],
            ["source-1"],
        )
        self.assertEqual(
            mismatch,
            ["citation_content_hash_mismatch:0:source-1"],
        )

    def test_submit_persists_quality_summary_and_market_bindings(self) -> None:
        started = application.start_agent({
            "workspace_id": self.workspace,
            "topic": "区域产业项目市场规模",
            "industry": "制造业",
            "region": "湖北",
            "plan_items": [{"field": "market_size", "required": True}],
            "idempotency_key": "dr-start-quality",
        })
        self.assertTrue(started["success"], started)
        task_id = started["task_id"]
        submitted = application.submit_agent({
            "workspace_id": self.workspace,
            "task_id": task_id,
            "report_md": "市场规模见来源定位。",
            "citations": [{
                "source_id": "source-1",
                "resource_uri": "lvke://data-acquisition/workspaces/dr-quality-test/snapshots/source-1",
                "locator": "page:1",
                "content_hash": "sha256:" + "b" * 64,
            }],
            "source_snapshot_ids": ["source-1"],
            "quality_summary": {
                "query_rounds": 3,
                "usable_source_count": 1,
                "citation_coverage": 0.8,
                "missing_fields": ["target_share"],
                "conflicts": [{"field": "market_size", "sources": ["source-1", "source-2"]}],
            },
            "market_field_bindings": [{
                "field": "market_size",
                "value": 1200,
                "unit": "万元",
                "locator": "source-1#page:1",
                "source_snapshot_id": "source-1",
            }],
        })
        self.assertTrue(submitted["success"], submitted)
        self.assertEqual(submitted["status"], "partial")
        record = PACKAGE_STORE.get(self.workspace, submitted["research_package_id"])
        self.assertIsNotNone(record)
        artifacts = (record or {}).get("payload", {}).get("agent_artifacts", {})
        self.assertEqual(artifacts["quality_summary"]["query_rounds"], 3)
        self.assertEqual(artifacts["quality_summary"]["missing_fields"], ["target_share"])
        self.assertEqual(artifacts["market_field_bindings"][0]["field"], "market_size")

    def test_submit_without_quality_fields_keeps_legacy_package_shape(self) -> None:
        started = application.start_agent({
            "workspace_id": self.workspace,
            "topic": "政策资料",
            "idempotency_key": "dr-start-legacy",
        })
        submitted = application.submit_agent({
            "workspace_id": self.workspace,
            "task_id": started["task_id"],
            "report_md": "研究摘要。",
            "citations": [{"locator": "page:1"}],
            "source_snapshot_ids": ["source-1"],
        })
        self.assertTrue(submitted["success"], submitted)
        record = PACKAGE_STORE.get(self.workspace, submitted["research_package_id"])
        artifacts = (record or {}).get("payload", {}).get("agent_artifacts", {})
        self.assertNotIn("quality_summary", artifacts)
        self.assertNotIn("market_field_bindings", artifacts)

    def test_partial_package_requires_independent_quality_confirmation(self) -> None:
        started = application.start_agent({
            "workspace_id": self.workspace,
            "topic": "市场容量",
            "idempotency_key": "dr-start-confirm",
        })
        submitted = application.submit_agent({
            "workspace_id": self.workspace,
            "task_id": started["task_id"],
            "report_md": "市场容量结论。[1]",
            "citations": [{"locator": "page:1"}],
            "source_snapshot_ids": ["source-1"],
            "quality_summary": {
                "query_rounds": 2,
                "usable_source_count": 1,
                "citation_coverage": 1.0,
                "missing_fields": [],
                "conflicts": [],
            },
        })
        self.assertEqual(submitted["status"], "partial")
        confirmed = application.confirm_quality({
            "workspace_id": self.workspace,
            "research_package_id": submitted["research_package_id"],
        })
        self.assertTrue(confirmed["success"], confirmed)
        self.assertEqual(confirmed["status"], "completed")
        self.assertEqual(confirmed["quality_review_status"], "passed")
        record = PACKAGE_STORE.get(self.workspace, confirmed["research_package_id"])
        self.assertEqual((record or {}).get("status"), "completed")
        self.assertTrue((record or {}).get("payload", {}).get("quality_review_id"))

    def test_quality_confirmation_blocks_unaccepted_gaps(self) -> None:
        started = application.start_agent({
            "workspace_id": self.workspace,
            "topic": "供需缺口",
            "idempotency_key": "dr-start-gap",
        })
        submitted = application.submit_agent({
            "workspace_id": self.workspace,
            "task_id": started["task_id"],
            "report_md": "资料不完整。[1]",
            "citations": [{"locator": "page:1"}],
            "source_snapshot_ids": ["source-1"],
            "quality_summary": {
                "query_rounds": 1,
                "usable_source_count": 1,
                "citation_coverage": 0.5,
                "missing_fields": ["target_share"],
                "conflicts": [],
            },
        })
        blocked = application.confirm_quality({
            "workspace_id": self.workspace,
            "research_package_id": submitted["research_package_id"],
        })
        self.assertFalse(blocked["success"], blocked)
        self.assertEqual(blocked["code"], "research_quality_failed")

    def test_quality_output_prevalidation_failure_writes_nothing(self) -> None:
        started = application.start_agent({
            "workspace_id": self.workspace,
            "topic": "引用原子性",
            "idempotency_key": "dr-start-atomic",
        })
        submitted = application.submit_agent({
            "workspace_id": self.workspace,
            "task_id": started["task_id"],
            "report_md": "结论。[1]",
            "citations": [{"source_id": "source-atomic", "locator": "page:1"}],
            "source_snapshot_ids": ["source-atomic"],
            "quality_summary": {
                "usable_source_count": 1,
                "citation_coverage": 1.0,
                "missing_fields": [],
                "conflicts": [],
            },
        })
        package_count = len(PACKAGE_STORE.list(self.workspace))
        review_count = len(QUALITY_REVIEW_STORE.list(self.workspace))
        with patch(
            "lvke_mcp.domains.research._service.agent_lifecycle."
            "validate_quality_confirmation_output",
            side_effect=ValueError("forced schema failure"),
        ):
            result = application.confirm_quality({
                "workspace_id": self.workspace,
                "research_package_id": submitted["research_package_id"],
            })
        self.assertFalse(result["success"], result)
        self.assertEqual(result["code"], "quality_confirmation_output_invalid")
        self.assertEqual(len(PACKAGE_STORE.list(self.workspace)), package_count)
        self.assertEqual(len(QUALITY_REVIEW_STORE.list(self.workspace)), review_count)
        source = PACKAGE_STORE.get(self.workspace, submitted["research_package_id"])
        self.assertEqual((source or {}).get("status"), "partial")

    def test_citation_mismatch_blocks_before_quality_writes(self) -> None:
        started = application.start_agent({
            "workspace_id": self.workspace,
            "topic": "引用一致性",
            "idempotency_key": "dr-start-citation-audit",
        })
        submitted = application.submit_agent({
            "workspace_id": self.workspace,
            "task_id": started["task_id"],
            "report_md": "结论。[1]",
            "citations": [{"source_id": "not-in-basis", "locator": "page:1"}],
            "source_snapshot_ids": ["source-basis"],
            "quality_summary": {
                "usable_source_count": 1,
                "citation_coverage": 1.0,
                "missing_fields": [],
                "conflicts": [],
            },
        })
        review_count = len(QUALITY_REVIEW_STORE.list(self.workspace))
        result = application.confirm_quality({
            "workspace_id": self.workspace,
            "research_package_id": submitted["research_package_id"],
        })
        self.assertFalse(result["success"], result)
        self.assertEqual(result["code"], "research_citation_audit_failed")
        self.assertEqual(len(QUALITY_REVIEW_STORE.list(self.workspace)), review_count)


if __name__ == "__main__":
    unittest.main()
