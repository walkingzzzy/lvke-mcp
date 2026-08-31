from __future__ import annotations

import unittest

from lvke_mcp.servers.lvke_data_analysis._service.trends import financial_trends


def _obs(period: str, value: float) -> dict:
    return {
        "source_id": "src-1",
        "metric": "revenue",
        "value": value,
        "unit": "万元",
        "period": period,
    }


class FinancialTrendsCagrSpanTests(unittest.TestCase):
    """CAGR 的跨度门禁。

    唯一护栏原本只有 ``years <= 0``，于是 2024Q1→Q2（跨度 0.25 年）会把季度波动
    年化成 **217.6%**，且 ``issues=[]``、``status=ok``、零 warning —— 一个荒谬的
    年增长率被当成正常结果交出去。年化的前提是"该增速可代表全年"，短跨度不成立，
    属口径非法而非置信度不足，所以拒绝出数。

    该工具此前没有任何测试文件。
    """

    def test_sub_year_span_refuses_to_annualize(self) -> None:
        result = financial_trends("ws", [_obs("2024Q1", 100), _obs("2024Q2", 133.5)], methods=["cagr"])
        self.assertEqual(result["results"], [])
        self.assertEqual(result["status"], "partial")
        reasons = [item["reason"] for item in result["issues"]]
        self.assertIn("cagr_span_below_one_year", reasons)
        issue = next(item for item in result["issues"] if item["reason"] == "cagr_span_below_one_year")
        self.assertEqual(issue["elapsed_years"], 0.25)

    def test_exactly_one_year_span_is_allowed(self) -> None:
        """边界不能误杀：整一年跨度是合法的年化基础。"""

        result = financial_trends("ws", [_obs("2023Q1", 100), _obs("2024Q1", 120)], methods=["cagr"])
        self.assertEqual(len(result["results"]), 1)
        self.assertAlmostEqual(result["results"][0]["result"], 0.2, places=6)
        self.assertEqual(
            [item for item in result["issues"] if item["reason"] == "cagr_span_below_one_year"],
            [],
        )

    def test_multi_year_span_still_computes(self) -> None:
        result = financial_trends("ws", [_obs("2022", 100), _obs("2024", 144)], methods=["cagr"])
        self.assertEqual(len(result["results"]), 1)
        self.assertAlmostEqual(result["results"][0]["result"], 0.2, places=6)

    def test_zero_or_negative_base_still_reported_separately(self) -> None:
        """既有的 invalid_cagr_base_or_span 判据未被新分支吞掉。"""

        result = financial_trends("ws", [_obs("2022", 0), _obs("2024", 144)], methods=["cagr"])
        reasons = [item["reason"] for item in result["issues"]]
        self.assertIn("invalid_cagr_base_or_span", reasons)
        self.assertNotIn("cagr_span_below_one_year", reasons)


if __name__ == "__main__":
    unittest.main()
