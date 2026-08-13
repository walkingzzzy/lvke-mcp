"""标识符拒绝守卫：非法标识符必须是业务阻断，不是系统故障。

`require_safe_id` 抛 `ValueError`，而工具入口的兜底 `except Exception` 会把它包成
`internal_error` + `system_success=False`。调用方于是看到"服务器坏了"，而真实情况是
"你传的 ID 格式不对" —— 既丢了可诊断性，也谎报了故障归属。

本组测试锁住两件事，缺一不可：

1. 已知标识符字段的拒绝**必须**转成业务信封（system_success=True）；
2. 形状相同但语义无关的 `ValueError`**必须**原样透传。

第 2 条是这个守卫最容易被改坏的地方：源码里存在 `invalid IRR scan range`、
`invalid delivery stage`、`invalid reference_table_schema: ...` 等同形状消息，
一旦匹配放宽成 `invalid (\\w+)`，真实的计算错误就会被贴上"标识符格式非法"的标签 ——
那恰好是本守卫要消灭的失败模式本身。
"""

from __future__ import annotations

import asyncio
import unittest

from lvke_mcp.runtime.input_guards import (
    _parse_id_field_from_error,
    guard_identifier_rejections,
)
from lvke_mcp.runtime.storage import require_safe_id

_SERVER = "lvke-data-analysis"
_guard = guard_identifier_rejections(_SERVER)

# 遍历形状之外，逐个都是 _SAFE_ID 会拒的真实形态。
_REJECTED_IDS = ("../etc/passwd", "bad/id", "", "   ", "-leading-dash", "a" * 200)


class IdentifierRejectionEnvelopeTest(unittest.TestCase):
    """非法标识符 → 业务阻断信封，且带可执行的修复指引。"""

    @staticmethod
    @_guard
    def _handler(args: dict) -> dict:
        require_safe_id(args["analysis_task_id"], "analysis_task_id")
        return {"success": True, "status": "ok"}

    def test_rejection_is_a_business_block_not_a_system_fault(self) -> None:
        for bad in _REJECTED_IDS:
            with self.subTest(identifier=bad):
                result = self._handler({"analysis_task_id": bad})
                # 归属：输入错误不得记为系统故障。
                self.assertTrue(result["system_success"])
                self.assertTrue(result["transport_success"])
                self.assertFalse(result["success"])
                self.assertFalse(result["business_success"])
                self.assertEqual("blocked", result["status"])

    def test_code_names_the_offending_field(self) -> None:
        """错误码要指名字段，否则多 ID 入口无法定位是哪个参数错了。"""

        result = self._handler({"analysis_task_id": "../etc/passwd"})
        self.assertEqual(f"{_SERVER}.invalid_analysis_task_id", result["code"])
        self.assertEqual(["invalid_analysis_task_id"], result["blockers"])
        self.assertTrue(result["next_actions"])

    def test_valid_identifier_passes_through_untouched(self) -> None:
        """守卫必须有判别力：合法 ID 不得被拦。"""

        self.assertEqual(
            {"success": True, "status": "ok"},
            self._handler({"analysis_task_id": "task_1"}),
        )


class NonIdentifierErrorsMustPropagateTest(unittest.TestCase):
    """形状相同、语义无关的 ValueError 不得被吞成"标识符非法"。"""

    def test_unrelated_value_errors_are_reraised(self) -> None:
        messages = (
            "invalid IRR scan range",            # domains/finance/calculations.py
            "invalid delivery stage",            # zero_material_delivery/_service/routing.py
            "invalid reference_table_schema: /x",  # domains/finance/reference_schema.py
            "invalid report generation task id",  # reports/_doc_service/gen_tasks.py
            "invalid task_support: bogus",        # runtime/transport.py
            "总投资必须为正数",
        )
        for message in messages:
            with self.subTest(message=message):

                @guard_identifier_rejections(_SERVER)
                def handler(_args: dict, _m: str = message) -> dict:
                    raise ValueError(_m)

                with self.assertRaises(ValueError):
                    handler({})

    def test_only_whitelisted_fields_convert(self) -> None:
        """匹配必须是"全串 + 白名单"，不是 substring 搜索。"""

        self.assertEqual(
            "analysis_task_id", _parse_id_field_from_error("invalid analysis_task_id")
        )
        # 未登记的字段名不转换（宁可透传，也不猜一个错误码）。
        self.assertIsNone(_parse_id_field_from_error("invalid unknown_field"))
        # 尾部还有别的话就不是纯标识符拒绝。
        self.assertIsNone(_parse_id_field_from_error("invalid task_id: extra context"))


class AsyncHandlerTest(unittest.TestCase):
    """异步 handler 走同一套判定（data_* 三个工具是 async）。"""

    def test_async_rejection_is_also_a_business_block(self) -> None:
        @guard_identifier_rejections(_SERVER)
        async def handler(args: dict) -> dict:
            require_safe_id(args["task_id"], "task_id")
            return {"success": True}

        result = asyncio.run(handler({"task_id": "bad/id"}))
        self.assertEqual(f"{_SERVER}.invalid_task_id", result["code"])
        self.assertTrue(result["system_success"])

    def test_async_valid_identifier_passes_through(self) -> None:
        @guard_identifier_rejections(_SERVER)
        async def handler(args: dict) -> dict:
            require_safe_id(args["task_id"], "task_id")
            return {"success": True, "status": "ok"}

        self.assertEqual(
            {"success": True, "status": "ok"}, asyncio.run(handler({"task_id": "t1"}))
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
