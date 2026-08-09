from __future__ import annotations

import os
import tempfile
import unittest

from lvke_mcp.adapters.data_acquisition_repository import SOURCE_STORE
from lvke_mcp.servers.lvke_data_acquisition import service as acquisition
from lvke_mcp.servers.lvke_data_analysis import service as analysis


class ExternalBrowserSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-browser-snapshot-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "browser-snapshot-test"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def test_codex_browser_body_is_retained_as_non_formal_candidate(self) -> None:
        body = "武汉市新洲区轨道交通研究正文，包含线路、站点与建设时序。"
        imported = acquisition.import_external_snapshot(
            self.workspace,
            url="https://www.wuhan.gov.cn/zwgk/example.html",
            title="公开页面",
            content=body,
            provider="codex-browser",
            provider_tool="browser_snapshot",
            retrieved_at="2026-08-09T12:00:00+08:00",
            content_kind="extracted_full_text",
        )
        self.assertTrue(imported["success"], imported)
        self.assertEqual(imported["content_origin"], "codex_browser_snapshot")
        self.assertFalse(imported["formal_use_allowed"])
        self.assertFalse(imported["project_fact_certified"])
        record = SOURCE_STORE.get(self.workspace, imported["source_snapshot_id"])
        payload = (record or {}).get("payload", {})
        self.assertEqual(payload.get("content"), body)
        self.assertEqual(payload.get("evidence_policy"), "candidate")

        ingested = analysis.ingest(
            self.workspace,
            [imported["source_snapshot_id"]],
            [],
        )
        queried = analysis.query(
            self.workspace,
            ingested["analysis_task_id"],
            "建设时序",
            5,
        )
        self.assertTrue(queried["hits"], queried)
        self.assertIn("建设时序", queried["hits"][0]["snippet"])
        self.assertFalse(queried["hits"][0]["formal_use_allowed"])
        evidence = analysis.build_evidence_pack(
            self.workspace,
            ingested["analysis_task_id"],
            [imported["source_snapshot_id"]],
            [{
                "field": "construction_schedule",
                "value": "2028-2032",
                "source_id": imported["source_snapshot_id"],
                "locator": queried["hits"][0]["locators"][0],
            }],
            [],
            evidence_track="real",
        )
        self.assertTrue(evidence["success"], evidence)
        self.assertFalse(evidence["formal_evidence_candidate"])
        self.assertFalse(evidence["project_fact_certified"])

    def test_browser_snapshot_keeps_url_safety_blocks(self) -> None:
        for index, url in enumerate((
            "http://127.0.0.1/secret",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://service.internal/source",
        )):
            with self.subTest(url=url):
                result = acquisition.import_external_snapshot(
                    self.workspace,
                    url=url,
                    title="blocked",
                    content=f"private body {index}",
                    provider="codex-browser",
                    provider_tool="browser_snapshot",
                    retrieved_at="2026-08-09T12:00:00+08:00",
                    content_kind="raw_content",
                )
                self.assertFalse(result["success"], result)
                self.assertEqual(result["status"], "blocked")
                self.assertIn("external_snapshot_url_blocked", result["blockers"])

    def test_search_or_research_output_kind_is_still_rejected(self) -> None:
        result = acquisition.import_external_snapshot(
            self.workspace,
            url="https://www.wuhan.gov.cn/zwgk/example.html",
            title="search answer",
            content="只有搜索摘要",
            provider="codex-browser",
            provider_tool="browser_snapshot",
            retrieved_at="2026-08-09T12:00:00+08:00",
            content_kind="search_summary",
        )
        self.assertFalse(result["success"], result)
        self.assertIn("external_content_kind_not_evidence_source", result["blockers"])


if __name__ == "__main__":
    unittest.main()
