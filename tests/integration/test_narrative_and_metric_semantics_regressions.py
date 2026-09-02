from __future__ import annotations

import unittest
import unittest.mock as mk

import lvke_mcp.domains.finance.gate as gate
from lvke_mcp.domains.finance import run_service
from lvke_mcp.servers.lvke_deliverable_review._report_checks.claims import build_claim_graph
from lvke_mcp.servers.lvke_deliverable_review._report_checks.normalize import _semantic_near
from lvke_mcp.servers.lvke_deliverable_review._report_checks.structure import (
    _internal_consistency_findings,
)

_VIEW = {
    "indicators": {
        "project_irr_pct": 10.35, "npv_wan": 3113.54,
        "static_payback_years": 7.03, "dynamic_payback_years": 9.96,
        "bep_pct": 29.86, "capital_irr_pct": 22.14,
        "revenue": 6660, "net_profit": 2001.09,
    },
    "investment": {"total": 28000},
    "funding": {"capital": 11200, "loan": 16800},
}


def _verify(text: str) -> dict[str, object]:
    with mk.patch.object(run_service, "get_workspace_finance_run", return_value=_VIEW):
        return gate.verify_narrative_numbers("ws", text, run_id="run_x")


class NarrativeNumberExtractionTest(unittest.TestCase):
    """``verify_narrative_numbers`` 必须抓到正文里每一处造假数字。

    活体实测缺陷（两轮负例交叉定位出的真根因，与最初猜测的"按段抽取"不同）：

    1. ``re.search`` 每个指标**全篇只比第一处**——后文再次出现的造假数字既不进
       ``mismatches`` 也不进 ``unmapped``，``ok`` 仍可能为 true。
    2. 数字正则不接受 Markdown 强调符：正文写 ``总投资 **35000.00 万元**`` 时
       ``**`` 夹在标签与数字之间，整条规则失配 —— 这是当时总投资与 IRR 两个假
       数字逃检的**真正原因**。
    3. 不接受千分位与亿元：正式可研写 ``128,000.00 万元`` / ``12.80 亿元``。
    """

    def test_markdown_emphasis_does_not_hide_fake_numbers(self) -> None:
        result = _verify(
            "本项目总投资 **35000.00 万元**，项目投资财务内部收益率 **18.60%**，"
            "财务净现值 12000.00 万元，静态投资回收期 4.20 年。"
        )
        self.assertFalse(result["ok"])
        reported = {item["element"] for item in result["mismatches"]}
        self.assertEqual(
            reported,
            {"total_investment", "project_irr", "npv", "static_payback"},
            "四个假数字必须全部报出，一个都不能漏",
        )

    def test_thousands_separator_and_correct_values_all_match(self) -> None:
        result = _verify(
            "项目总投资 28,000.00 万元，其中资本金（自筹）11,200.00 万元、"
            "银行贷款 16,800.00 万元。达产年营业收入 6,660.00 万元、"
            "净利润 2,001.09 万元。项目投资财务内部收益率 10.35%，"
            "财务净现值（Ic=8.0%）3,113.54 万元，静态投资回收期 7.03 年，"
            "动态投资回收期 9.96 年，盈亏平衡点 29.86%。"
            "资本金财务内部收益率 22.14%。"
        )
        self.assertTrue(result["ok"], result["mismatches"])
        matched = {item["element"] for item in result["matches"]}
        self.assertIn("total_investment", matched)
        self.assertIn("capital_irr", matched)
        self.assertIn("project_irr", matched)

    def test_later_occurrence_is_also_compared(self) -> None:
        """开头写对、结论章改写成另一个数：全文扫才抓得到。"""
        result = _verify(
            "项目总投资 28,000.00 万元。\n"
            "……（中间大量正文）……\n"
            "综上，本项目总投资 66,666.00 万元。"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            [(m["element"], m["found"]) for m in result["mismatches"]],
            [("total_investment", 66666.0)],
        )

    def test_yi_yuan_normalized_to_wan(self) -> None:
        result = _verify("项目总投资 2.80 亿元。")
        self.assertTrue(result["ok"], result["mismatches"])
        self.assertEqual(result["matches"][0]["found"], 28000.0)

    def test_capital_irr_not_absorbed_by_project_irr(self) -> None:
        """两个 IRR 同句时必须分别映射，否则一真一假会互相掩盖。"""
        result = _verify(
            "项目投资财务内部收益率 10.35%，资本金财务内部收益率 88.88%。"
        )
        self.assertEqual(
            [(m["element"], m["found"]) for m in result["mismatches"]],
            [("capital_irr", 88.88)],
        )


class MetricSemanticDisambiguationTest(unittest.TestCase):
    """审查域指标语义仲裁：长标签不得输给自己的子串。

    ``_semantic_near`` 排序键曾是 ``(距离, 方向, 表内序号)``。多条模式允许子串
    命中且互不互斥，于是「资本金财务内部收益率」与子串「财务内部收益率」
    ``match.end()`` 相同、距离并列，靠表内序号裁决 → 更宽的 ``project_irr``
    恒胜（它排在表首）。同理「工资及福利费合计」被后缀「福利费」抢走。
    修法是把**标签特异性**（命中跨度）排在表内序号之前。
    """

    def _metric(self, text: str, needle: str, unit: str) -> str:
        index = text.index(needle)
        return _semantic_near(text, index, index + len(needle), unit)

    def test_capital_irr_wins_over_project_irr_substring(self) -> None:
        self.assertEqual(
            self._metric("资本金财务内部收益率为22.14%", "22.14", "%"),
            "capital_irr",
        )

    def test_project_irr_still_recognized(self) -> None:
        self.assertEqual(
            self._metric("项目投资财务内部收益率 10.35%", "10.35", "%"),
            "project_irr",
        )

    def test_bare_and_after_tax_irr_recognized(self) -> None:
        """裸「内部收益率」「税后内部收益率」是国标可研常见写法，此前一条不认。"""
        for text in ("内部收益率10.35%", "税后内部收益率10.35%", "全部投资内部收益率10.35%"):
            with self.subTest(text=text):
                self.assertEqual(self._metric(text, "10.35", "%"), "project_irr")

    def test_wage_total_wins_over_welfare_suffix(self) -> None:
        self.assertEqual(
            self._metric("工资及福利费合计456.60万元", "456.60", "万元"),
            "wage_cost",
        )

    def test_standalone_welfare_still_welfare(self) -> None:
        self.assertEqual(
            self._metric("福利费315.00万元", "315.00", "万元"),
            "welfare_cost",
        )


class InternalConsistencyContextTest(unittest.TestCase):
    """一致性分组必须含情景/披露语境维度。

    只按 ``(metric, period, unit)`` 分桶时，敏感性三情景的 NPV 与差异披露句里的
    「A 与 B 差 C」会被判成同口径冲突 —— 与数字对错无关的假 P0。``period`` 只认
    时间期间（``_PERIOD_PATTERN``），表达不了情景，故单列 ``variance_context``。
    """

    def _findings(self, text: str) -> list[dict[str, object]]:
        return _internal_consistency_findings(
            build_claim_graph(text, target_id="t"), "t", [],
        )

    def test_sensitivity_scenarios_not_conflict(self) -> None:
        self.assertEqual(self._findings(
            "敏感性分析表明：营业收入下降 20% 时净现值转为 -2,996.48 万元；"
            "建设投资上升 20% 时净现值 -808.64 万元。悲观情景净现值 -300.64 万元。"
        ), [])

    def test_variance_disclosure_not_conflict(self) -> None:
        self.assertEqual(self._findings(
            "财务勾稽存在未收敛项：流动资金分项净额 747.18 万元与估算 "
            "1,500.00 万元差 752.82 万元。"
        ), [])

    def test_unexplained_conflict_still_reported(self) -> None:
        """反向：这条最容易被"消假阳性"顺手放过，必须钉住。"""
        findings = self._findings(
            "第一章 项目总投资 28,000.00 万元。\n"
            "第六章 项目总投资 35,000.00 万元。"
        )
        self.assertTrue(findings, "无解释的跨章节口径冲突仍必须是 finding")
        self.assertEqual(findings[0]["severity"], "P0")
        self.assertEqual(findings[0]["actual"]["values"], [28000.0, 35000.0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
