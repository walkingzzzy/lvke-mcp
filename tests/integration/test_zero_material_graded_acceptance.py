"""分级验收：technical 自动、internal 人工聚合、formal 只能被挣得。

这组测试守四条不变量：

1. 技术验收按领域出结果，硬阻断进 failed/blocked，只有质量限制时是
   passed_with_limitations。
2. 内部验收在技术验收未过时不可能通过；判据是**确认记录存在**，不是
   review 的 role_confirmed（后者在 quick profile 下退化成"有 Assessment 即已确认"）。
3. 限制项只能被"接受"，不能被内部确认清除。
4. formal 是资格：technical/internal 任一未过即 blocked，且 Promotion 失败时
   内部验收状态保留。
"""

from __future__ import annotations

import unittest

from lvke_mcp.servers.lvke_zero_material_delivery._service import acceptance


def _domain(name: str, status: str, **kwargs) -> dict:
    return {"domain": name, "status": status, **kwargs}


class TechnicalFoldTest(unittest.TestCase):
    def test_clean_run_is_passed(self) -> None:
        folded = acceptance.fold_technical([_domain("finance_tables", "passed")])
        self.assertEqual(folded["status"], "passed")
        self.assertEqual(folded["blockers"], [])
        self.assertEqual(folded["limitations"], [])

    def test_quality_only_is_passed_with_limitations(self) -> None:
        folded = acceptance.fold_technical(
            [
                _domain(
                    "research_evidence",
                    "passed_with_limitations",
                    limitations=["research_evidence_pending"],
                )
            ]
        )
        self.assertEqual(folded["status"], "passed_with_limitations")
        self.assertEqual(folded["blockers"], [])
        self.assertIn("research_evidence_pending", folded["limitations"])

    def test_hard_blocker_reaches_blocked(self) -> None:
        folded = acceptance.fold_technical(
            [_domain("finance_model", "failed", blockers=["finance_run_failed"])]
        )
        self.assertEqual(folded["status"], "blocked")
        self.assertIn("finance_run_failed", folded["blockers"])

    def test_severity_comes_from_the_shared_classifier(self) -> None:
        """阻断/质量的判定必须与 runtime/quality_severity 一致，不自己判一遍。"""

        folded = acceptance.fold_technical(
            [_domain("report_structure", "passed")],
            extra_limitations=["project_scale_inconsistent:route_length_km"],
        )
        # 规模对账不一致是口径非法，即便是通过 extra_limitations 传进来也必须阻断。
        self.assertIn("project_scale_inconsistent:route_length_km", folded["blockers"])
        self.assertNotEqual(folded["status"], "passed_with_limitations")


class DomainResultsTest(unittest.TestCase):
    def _inputs(self, **overrides) -> dict:
        base = {
            "component_status": {"report_markdown": True, "report_docx": True},
            "unresolved_slots": [],
            "research": {"research_package_id": "rp_1", "fallback_used": False},
            "finance": {"run_id": "run_1", "consistency_ok": True},
            "tables": {
                "finance_tables_package_id": "ftp_1",
                "csv_ok": True,
                "xlsx_ok": True,
            },
            "lineage": {"object_refs": {"a": True}, "manifest_uri_present": True},
            "profile_selection": {"profile_content_hash": "sha256:x"},
        }
        base.update(overrides)
        return base

    def test_all_five_domains_are_always_reported(self) -> None:
        rows = acceptance.build_technical_domain_results(**self._inputs())
        self.assertEqual(
            [row["domain"] for row in rows], list(acceptance.TECHNICAL_DOMAINS)
        )
        self.assertTrue(all(row["status"] == "passed" for row in rows))

    def test_missing_component_blocks_not_merely_annotates(self) -> None:
        """必需交付组件缺失必须阻断。

        此前该码落到"未知码默认不阻断"，于是 DOCX 与 XLSX 都没产出的运行
        照样报 passed_with_limitations 并走到 formal=eligible。
        """

        rows = acceptance.build_technical_domain_results(
            **self._inputs(component_status={"report_docx": False})
        )
        report = next(row for row in rows if row["domain"] == "report_structure")
        self.assertEqual(report["status"], "failed")
        self.assertIn("required_component_missing:report_docx", report["blockers"])
        formal = acceptance.fold_formal(
            technical=acceptance.fold_technical(rows),
            internal={"status": "passed", "limitations": []},
        )
        self.assertEqual(formal["status"], "blocked")

    def test_absent_evidence_refs_are_not_counted_as_lineage_breaks(self) -> None:
        """公开检索无来源时证据引用为空，属"证据待补"，不是谱系断裂。

        把它算成谱系缺失会双重计数（已有 research_evidence_pending），
        并把一个正常的零材料运行判成失败。
        """

        rows = acceptance.build_technical_domain_results(
            **self._inputs(
                research={"research_package_id": "", "fallback_used": True},
                lineage={
                    "object_refs": {
                        "research_package_id": False,
                        "evidence_pack_id": False,
                        "finance_run_id": True,
                    },
                    "manifest_uri_present": True,
                },
            )
        )
        lineage = next(row for row in rows if row["domain"] == "delivery_lineage")
        self.assertEqual(lineage["status"], "passed")
        self.assertEqual(lineage["blockers"], [])
        # 证据待补仍必须如实披露，只是走限制项而不是阻断。
        research = next(row for row in rows if row["domain"] == "research_evidence")
        self.assertIn("research_evidence_pending", research["limitations"])

    def test_structural_lineage_break_still_blocks(self) -> None:
        rows = acceptance.build_technical_domain_results(
            **self._inputs(
                lineage={
                    "object_refs": {"finance_run_id": False},
                    "manifest_uri_present": False,
                }
            )
        )
        lineage = next(row for row in rows if row["domain"] == "delivery_lineage")
        self.assertEqual(lineage["status"], "failed")
        self.assertIn("delivery_lineage_missing:finance_run_id", lineage["blockers"])
        self.assertIn("delivery_manifest_missing", lineage["blockers"])

    def test_broken_consistency_blocks_finance_model(self) -> None:
        rows = acceptance.build_technical_domain_results(
            **self._inputs(finance={"run_id": "run_1", "consistency_ok": False})
        )
        finance = next(row for row in rows if row["domain"] == "finance_model")
        # 勾稽不通不是置信度问题：十三表与正文都建立在那份快照上。
        self.assertEqual(finance["status"], "failed")
        self.assertIn("finance_run_consistency_failed", finance["blockers"])

    def test_absent_finance_run_blocks(self) -> None:
        rows = acceptance.build_technical_domain_results(
            **self._inputs(finance={"run_id": "", "consistency_ok": None})
        )
        finance = next(row for row in rows if row["domain"] == "finance_model")
        self.assertEqual(finance["status"], "failed")
        self.assertIn("finance_run_failed", finance["blockers"])


class InternalFoldTest(unittest.TestCase):
    def _confirmed_rows(self, **overrides) -> list[dict]:
        rows = []
        for dimension in acceptance.REQUIRED_DIMENSIONS:
            rows.append(
                {
                    "dimension": dimension,
                    "status": overrides.get(dimension, "passed"),
                    "confirmation_id": f"rvdim_{dimension}",
                    "role_declaration": f"{dimension} 负责人",
                    "review_statement": "已复核",
                    "limitations_accepted": [],
                    "incomplete_reasons": [],
                }
            )
        return rows

    def test_technical_failure_blocks_internal_regardless_of_confirmations(self) -> None:
        folded = acceptance.fold_internal(
            technical_status="blocked",
            dimension_results=self._confirmed_rows(),
        )
        self.assertEqual(folded["status"], "blocked")
        self.assertIn("technical_acceptance_not_passed:blocked", folded["blockers"])

    def test_missing_dimension_keeps_internal_pending(self) -> None:
        rows = self._confirmed_rows()[:-1]
        folded = acceptance.fold_internal(
            technical_status="passed", dimension_results=rows
        )
        self.assertEqual(folded["status"], "pending")
        self.assertEqual(folded["missing_dimensions"], ["feasibility"])

    def test_role_confirmed_alone_is_not_accepted_as_human_confirmation(self) -> None:
        """review 的 role_confirmed 在 quick profile 下只表示"有 Assessment"。

        内部验收必须要求真实的确认记录（confirmation_id），否则就是把系统自动
        检查当成人工签章。
        """

        rows = [
            {
                "dimension": dimension,
                "status": "passed",
                # 刻意给 role_confirmed=True 但不给 confirmation_id。
                "role_confirmed": True,
                "confirmation_id": "",
            }
            for dimension in acceptance.REQUIRED_DIMENSIONS
        ]
        folded = acceptance.fold_internal(
            technical_status="passed", dimension_results=rows
        )
        self.assertEqual(folded["status"], "pending")
        self.assertEqual(
            folded["missing_dimensions"], list(acceptance.REQUIRED_DIMENSIONS)
        )

    def test_all_confirmed_aggregates_to_passed(self) -> None:
        folded = acceptance.fold_internal(
            technical_status="passed", dimension_results=self._confirmed_rows()
        )
        self.assertEqual(folded["status"], "passed")
        self.assertEqual(folded["missing_dimensions"], [])
        self.assertEqual(len(folded["domain_confirmations"]), 7)
        self.assertTrue(all(
            row["identity_or_credential_verified"] is False
            for row in folded["domain_confirmations"]
        ))
        # 责任声明必须被带出来：只回 confirmation_id 会让调用方无法显示
        # "谁按什么责任确认的"，而这正是内部验收与自动检查的区别所在。
        self.assertEqual(len(folded["role_declarations"]), 7)

    def test_inherited_limitations_survive_confirmation(self) -> None:
        folded = acceptance.fold_internal(
            technical_status="passed_with_limitations",
            dimension_results=self._confirmed_rows(),
            inherited_limitations=["research_evidence_pending"],
        )
        self.assertEqual(folded["status"], "passed_with_limitations")
        # 限制项不能被内部确认清除。
        self.assertIn("research_evidence_pending", folded["limitations"])

    def test_failed_dimension_blocks(self) -> None:
        folded = acceptance.fold_internal(
            technical_status="passed",
            dimension_results=self._confirmed_rows(compliance="failed"),
        )
        self.assertEqual(folded["status"], "blocked")
        self.assertIn("review_dimension_failed:compliance", folded["blockers"])

    def test_structural_incomplete_is_disclosed_not_blocked(self) -> None:
        rows = self._confirmed_rows(compliance="incomplete")
        for row in rows:
            if row["dimension"] == "compliance":
                row["incomplete_reasons"] = ["review_package_role_missing:base_data"]
        folded = acceptance.fold_internal(
            technical_status="passed", dimension_results=rows
        )
        self.assertEqual(folded["status"], "passed_with_limitations")
        self.assertTrue(
            any("structurally_incomplete" in item for item in folded["limitations"])
        )

    def test_non_structural_incomplete_still_blocks(self) -> None:
        rows = self._confirmed_rows(compliance="incomplete")
        for row in rows:
            if row["dimension"] == "compliance":
                row["incomplete_reasons"] = ["standards_snapshot_unavailable"]
        folded = acceptance.fold_internal(
            technical_status="passed", dimension_results=rows
        )
        self.assertEqual(folded["status"], "blocked")
        self.assertIn("review_dimension_incomplete:compliance", folded["blockers"])

    def test_structural_reason_mixed_with_a_real_one_still_blocks(self) -> None:
        """一条真原因足以让整个维度继续阻断，不能被结构性缺项掩护。"""

        rows = self._confirmed_rows(compliance="incomplete")
        for row in rows:
            if row["dimension"] == "compliance":
                row["incomplete_reasons"] = [
                    "review_package_role_missing:base_data",
                    "standards_snapshot_unavailable",
                ]
        folded = acceptance.fold_internal(
            technical_status="passed", dimension_results=rows
        )
        self.assertEqual(folded["status"], "blocked")
        self.assertIn("review_dimension_incomplete:compliance", folded["blockers"])

    def test_incomplete_without_any_reason_blocks(self) -> None:
        """原因为空不等于"只有结构性缺项"，仍须阻断。"""

        folded = acceptance.fold_internal(
            technical_status="passed",
            dimension_results=self._confirmed_rows(compliance="incomplete"),
        )
        self.assertEqual(folded["status"], "blocked")
        self.assertIn("review_dimension_incomplete:compliance", folded["blockers"])


class FormalFoldTest(unittest.TestCase):
    def test_both_stages_must_pass(self) -> None:
        blocked = acceptance.fold_formal(
            technical={"status": "passed"}, internal={"status": "pending"}
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("internal_acceptance_not_passed:pending", blocked["blockers"])

    def test_eligible_only_when_both_passed(self) -> None:
        eligible = acceptance.fold_formal(
            technical={"status": "passed_with_limitations"},
            internal={"status": "passed"},
        )
        self.assertEqual(eligible["status"], "eligible")
        self.assertEqual(eligible["blockers"], [])

    def test_unanswered_required_field_blocks_formal_but_not_preview(self) -> None:
        """关键必填字段未答：技术预览可用，正式资格必须阻断。

        此前只把它记成限制项，于是五个关键字段全空的运行照样走到 eligible——
        与"不得因此直接获得正式资格"直接冲突。
        """

        folded = acceptance.fold_formal(
            technical={
                "status": "passed_with_limitations",
                "limitations": [
                    "required_field_unanswered:route_length_km",
                    "research_evidence_pending",
                ],
            },
            internal={"status": "passed_with_limitations", "limitations": []},
        )
        self.assertEqual(folded["status"], "blocked")
        self.assertIn("required_field_unanswered:route_length_km", folded["blockers"])
        # 非关键限制项不得被误升为阻断。
        self.assertNotIn("research_evidence_pending", folded["blockers"])

    def test_answered_required_fields_reach_eligible(self) -> None:
        """反面：字段答齐后必须真的可达 eligible，门禁不能变成死锁。"""

        folded = acceptance.fold_formal(
            technical={
                "status": "passed_with_limitations",
                "limitations": ["research_evidence_pending"],
            },
            internal={"status": "passed_with_limitations", "limitations": []},
        )
        self.assertEqual(folded["status"], "eligible")
        self.assertEqual(folded["blockers"], [])

    def test_skipped_non_critical_field_does_not_block_formal(self) -> None:
        """用户显式跳过的非关键字段只披露，不阻断正式资格。"""

        folded = acceptance.fold_formal(
            technical={
                "status": "passed_with_limitations",
                "limitations": ["required_field_skipped:loan_rate"],
            },
            internal={"status": "passed", "limitations": []},
        )
        self.assertEqual(folded["status"], "eligible")
        self.assertIn("required_field_skipped:loan_rate", folded["limitations"])

    def test_promotion_failure_blocks_without_erasing_internal(self) -> None:
        internal = {"status": "passed", "limitations": ["research_evidence_pending"]}
        folded = acceptance.fold_formal(
            technical={"status": "passed"},
            internal=internal,
            promotion_blockers=["formal_source_hash_mismatch"],
        )
        self.assertEqual(folded["status"], "blocked")
        self.assertIn("formal_source_hash_mismatch", folded["blockers"])
        # 内部验收状态保留，限制项也保留。
        self.assertEqual(internal["status"], "passed")
        self.assertIn("research_evidence_pending", folded["limitations"])

    def test_promoted_state_requires_a_promotion_id(self) -> None:
        promoted = acceptance.fold_formal(
            technical={"status": "passed"},
            internal={"status": "passed"},
            promotion_id="zmprom_abc",
        )
        self.assertEqual(promoted["status"], "promoted")
        self.assertEqual(promoted["promotion_id"], "zmprom_abc")

    def test_empty_acceptance_starts_not_started_and_blocked(self) -> None:
        empty = acceptance.empty_acceptance()
        self.assertEqual(empty["technical"]["status"], "not_started")
        self.assertEqual(empty["internal"]["status"], "not_started")
        # 正式资格默认阻断，不是"未知"。
        self.assertEqual(empty["formal"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
