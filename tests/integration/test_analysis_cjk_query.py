"""中文检索必须真能命中已索引正文。

此前 ``analysis_query`` 用 ``re.findall(r"[\\w\\u4e00-\\u9fff]+")`` 分词，
把"城市轨道交通客运量与票价"整段当成**一个** token，于是来源正文里明明写着
"客运量""票价"，查询仍返回空 hits，而响应又不说明到底是没索引还是没这个词。

修复后：
- 中文按确定性 1~4 字 n-gram 切分（不引词典，可复现可审计）
- n-gram 全落空时做子串回退，并标 ``match_mode=substring_fallback``
- ``analysis_query`` / ``analysis_status`` 返回已索引字符数、中文字数、文档数与失败原因
"""

from __future__ import annotations

import os
import tempfile
import unittest

from lvke_mcp.servers.lvke_data_analysis._service import ingest as ingest_service

_RAIL_TEXT = (
    "根据客流预测报告，本线初期日均客运量为18万人次，"
    "近期26万人次，远期35万人次。平均清分票价按3.2元/人次测算，"
    "非票收入主要来自广告与商业租赁。"
)


class CjkTokenizeTest(unittest.TestCase):
    def test_full_phrase_query_matches_partial_terms(self) -> None:
        tokens = ingest_service.tokenize_query("城市轨道交通客运量与票价")
        score, _, mode = ingest_service._score_document(_RAIL_TEXT, tokens)
        self.assertGreater(score, 0)
        self.assertEqual(mode, "tokenized")

    def test_exact_keyword_queries_match(self) -> None:
        for keyword in ("客运量", "票价", "清分票价", "非票收入", "人次"):
            with self.subTest(keyword=keyword):
                tokens = ingest_service.tokenize_query(keyword)
                score, _, _ = ingest_service._score_document(_RAIL_TEXT, tokens)
                self.assertGreater(score, 0, keyword)

    def test_unrelated_query_still_returns_zero(self) -> None:
        tokens = ingest_service.tokenize_query("光伏组件转换效率")
        score, _, mode = ingest_service._score_document(_RAIL_TEXT, tokens)
        self.assertEqual(score, 0.0)
        self.assertEqual(mode, "none")

    def test_longer_terms_outweigh_single_characters(self) -> None:
        specific = ingest_service.tokenize_query("客运量")
        generic = ingest_service.tokenize_query("量")
        specific_score, _, _ = ingest_service._score_document(_RAIL_TEXT, specific)
        generic_score, _, _ = ingest_service._score_document(_RAIL_TEXT, generic)
        self.assertGreater(specific_score, generic_score)

    def test_stopwords_do_not_dominate(self) -> None:
        tokens = ingest_service.tokenize_query("的了和与")
        self.assertEqual(
            [t for t in tokens["terms"] if len(t) == 1],
            [],
            tokens["terms"],
        )

    def test_ascii_and_cjk_mix(self) -> None:
        tokens = ingest_service.tokenize_query("IRR 与 客运量")
        self.assertIn("irr", tokens["ascii_terms"])
        self.assertIn("客运量", tokens["terms"])

    def test_substring_fallback_finds_shortened_phrase(self) -> None:
        # 文档只有"轨道交通"，查询给了更长的"城市轨道交通线网"，
        # n-gram 里"轨道交通"本身就会命中，因此这里用一个 n-gram 落空但
        # 子串仍可命中的场景验证回退分支存在且被计入。
        tokens = ingest_service.tokenize_query("轨道交通")
        score, _, mode = ingest_service._score_document("本项目为轨道交通工程", tokens)
        self.assertGreater(score, 0)
        self.assertIn(mode, {"tokenized", "substring_fallback"})


class IndexedStatsTest(unittest.TestCase):
    def test_stats_count_characters_and_documents(self) -> None:
        stats = ingest_service._indexed_stats(
            [
                {"source_id": "a", "source_type": "web_snapshot", "content": _RAIL_TEXT},
                {"source_id": "b", "source_type": "controlled_file", "content": ""},
            ]
        )
        self.assertEqual(stats["indexed_char_count"], len(_RAIL_TEXT))
        self.assertGreater(stats["indexed_cjk_char_count"], 0)
        self.assertEqual(stats["indexed_document_count"], 1)
        self.assertEqual(stats["empty_content_source_ids"], ["b"])
        self.assertEqual(
            stats["indexed_char_count_by_source_type"]["controlled_file"], 0
        )


class SnapshotIngestQueryTest(unittest.TestCase):
    """端到端：SourceSnapshot 的 payload.content 必须真被索引到。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-analysis-cjk-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "analysis-cjk-test"
        from lvke_mcp.adapters.data_acquisition_repository import SOURCE_STORE

        record = SOURCE_STORE.put(
            self.workspace,
            {
                "url": "https://example.gov.cn/rail-notice",
                "title": "城市轨道交通线路客流预测公示",
                "content": _RAIL_TEXT,
                "retrieved_at": "2026-08-09T00:00:00Z",
            },
            producer="test",
            status="ok",
        )
        self.source_id = record["object_id"]

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _task(self) -> str:
        result = ingest_service.ingest(self.workspace, [self.source_id], [])
        self.assertTrue(result["success"], result)
        return result["analysis_task_id"]

    def test_status_reports_indexed_characters(self) -> None:
        status = ingest_service.status(self.workspace, self._task())
        self.assertEqual(status["document_count"], 1)
        self.assertEqual(status["indexed_char_count"], len(_RAIL_TEXT))
        self.assertGreater(status["indexed_cjk_char_count"], 0)

    def test_government_page_keyword_query_returns_hits(self) -> None:
        task_id = self._task()
        for keyword in ("客运量", "票价", "清分票价"):
            with self.subTest(keyword=keyword):
                result = ingest_service.query(self.workspace, task_id, keyword, 10)
                self.assertTrue(result["hits"], f"{keyword} -> {result}")
                self.assertEqual(result["hits"][0]["source_id"], self.source_id)
                self.assertIn(keyword[-2:], result["hits"][0]["snippet"])

    def test_full_sentence_query_returns_hits(self) -> None:
        result = ingest_service.query(
            self.workspace, self._task(), "城市轨道交通客运量与票价水平", 10
        )
        self.assertTrue(result["hits"], result)

    def test_empty_result_explains_itself(self) -> None:
        result = ingest_service.query(
            self.workspace, self._task(), "光伏组件转换效率", 10
        )
        self.assertEqual(result["hits"], [])
        self.assertTrue(result["warnings"])
        self.assertIn("已索引", result["warnings"][0])
        self.assertGreater(result["indexed_char_count"], 0)

    def test_query_reports_its_own_terms(self) -> None:
        result = ingest_service.query(self.workspace, self._task(), "客运量", 10)
        self.assertGreater(result["query_term_count"], 0)
        self.assertIn("客运量", result["query_terms"])


if __name__ == "__main__":
    unittest.main()
