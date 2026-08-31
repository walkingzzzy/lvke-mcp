from __future__ import annotations

import os
import tempfile
import unittest

import lvke_mcp.servers.lvke_finance_model.service as finance_service
from lvke_mcp.adapters.finance_model_repository import SPEC_STORE
from lvke_mcp.domains.finance.run_service import compute_spec_hash


class FinanceSpecHashContractTest(unittest.TestCase):
    """spec_hash 的两条不变量。

    两处缺陷曾让未确认候选 spec 完全不可用，且都没有测试：

    1. ``prepare_spec`` 先算 hash 后盖 ``generation_standard`` 注记，存进
       SPEC_STORE 的 spec 是盖章后的 —— 记录自身不自洽，run 侧复算必然
       ``spec_hash_mismatch``，四条逃生路（改传 hash / 空串 / force_recompute /
       review_candidate）全被 ``payload.get(...) or args.get(...)`` 的优先级封死。
    2. ``run_model`` 把调用方 hash 拿去比**注入基线后**的 spec。收入驱动不完整时
       服务端会把 ``spec.revenue`` 整块换成 flat 估算基线，于是任何诚实的调用方
       hash 都必然被拒 —— 调用方不可能预知服务端要注入什么。

    既有测试用手工 ``SPEC_STORE.put`` 构造自洽 hash，绕过了 ``prepare_spec``，
    所以缺陷 1 长期没被测到。
    """

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-spec-hash-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.workspace = "spec-hash-contract"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _prepared(self) -> tuple[str, dict, str]:
        prepared = finance_service.prepare_spec({"workspace_id": self.workspace})
        spec_id = str(prepared.get("spec_id") or "")
        self.assertTrue(spec_id, prepared)
        record = SPEC_STORE.get(self.workspace, spec_id)
        payload = (record or {}).get("payload") or {}
        return spec_id, payload.get("spec") or {}, str(payload.get("spec_hash") or "")

    def test_prepared_candidate_record_is_self_consistent(self) -> None:
        _, spec, stored_hash = self._prepared()
        self.assertEqual(stored_hash, compute_spec_hash(spec))

    def test_unconfirmed_candidate_spec_id_can_run(self) -> None:
        """未确认候选 spec 可直接跑 run —— 这是产品自述的意图。

        ``run_cases.py`` 只在 review_candidate 下记一条不阻断的
        ``spec_confirmation_missing``，``next_actions`` 也明写"可确认候选 Spec，
        也可直接调用 finance_run_model"。被 spec_hash_mismatch 挡住违反该意图。
        """

        spec_id, _, _ = self._prepared()
        run = finance_service.run_model({
            "workspace_id": self.workspace,
            "spec_id": spec_id,
            "idempotency_key": "candidate-run-1",
        })
        self.assertNotIn("spec_hash_mismatch", run.get("blockers") or [], run)
        self.assertTrue(run.get("success"), run)

    def test_inline_spec_with_honest_hash_is_accepted(self) -> None:
        """默认值注入不得使诚实的调用方 hash 被拒。"""

        _, spec, stored_hash = self._prepared()
        run = finance_service.run_model({
            "workspace_id": self.workspace,
            "spec": spec,
            "spec_hash": stored_hash,
            "idempotency_key": "inline-honest-1",
        })
        self.assertNotIn("spec_hash_mismatch", run.get("blockers") or [], run)
        self.assertTrue(run.get("success"), run)

    def test_forged_spec_hash_is_still_rejected(self) -> None:
        """放开注入前比对不能顺带放过篡改（fail-closed 未被削弱）。"""

        _, spec, _ = self._prepared()
        run = finance_service.run_model({
            "workspace_id": self.workspace,
            "spec": spec,
            "spec_hash": "sha256:" + "0" * 64,
            "idempotency_key": "inline-forged-1",
        })
        self.assertFalse(run.get("success"), run)
        self.assertIn("spec_hash_mismatch", run.get("blockers") or [], run)


if __name__ == "__main__":
    unittest.main()
