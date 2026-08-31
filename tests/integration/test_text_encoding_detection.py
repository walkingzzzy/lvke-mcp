from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lvke_mcp.adapters.source_files_repository import _decode_text, _parse_bytes


class TextEncodingDetectionTests(unittest.TestCase):
    """纯文本 / Markdown 的编码检测。

    此处原本是裸 ``data.decode("utf-8", errors="replace")`` —— 同一文件里 CSV
    分支做了三级检测，文本分支一个都没有，且不设 degraded_reason 也不设 parser，
    ``status=succeeded``。GB18030 的中文标签会整片变成替换符，而**数字全部存活**：
    这正是本产品反复栽的「标签与数字错配」形态，且 locator 的 offset 建立在乱码
    文本上，下游 citation fragment 复核会照这些 offset 取到残骸。

    非 UTF-8 文本导入此前零测试覆盖。
    """

    def _analysis(self, content: bytes, name: str = "notes.md") -> dict:
        with tempfile.TemporaryDirectory(prefix="lvke-text-encoding-") as directory:
            path = Path(directory) / name
            path.write_bytes(content)
            return _parse_bytes(path, "text/markdown")

    def test_gb18030_chinese_is_decoded_not_mojibake(self) -> None:
        original = "# 江夏区项目\n\n总投资 46968 万元，水电能源 1600 万元。\n"
        analysis = self._analysis(original.encode("gb18030"))
        self.assertEqual(analysis["encoding"], "gb18030")
        self.assertNotIn("degraded_reason", analysis)
        self.assertIn("总投资 46968 万元", analysis["text_preview"])
        self.assertIn("水电能源 1600 万元", analysis["text_preview"])
        # 替换符一个都不该出现：标签丢失而数字存活是最危险的形态。
        self.assertNotIn("�", analysis["text_preview"])

    def test_utf8_and_bom_still_work(self) -> None:
        original = "总投资 46968 万元"
        for raw in (original.encode("utf-8"), original.encode("utf-8-sig")):
            analysis = self._analysis(raw)
            self.assertIn("总投资 46968 万元", analysis["text_preview"])
            self.assertNotIn("degraded_reason", analysis)

    def test_undecodable_bytes_are_disclosed_not_silent(self) -> None:
        """保留可读部分是刻意的（纯文本可能混二进制），但降级必须留痕。"""

        text, encoding, degraded = _decode_text(bytes([0x00, 0x81, 0xFF, 0xFE, 0x20]))
        self.assertEqual(degraded, "text_encoding_undecodable")
        self.assertEqual(encoding, "unknown")
        self.assertIsInstance(text, str)

    def test_text_branch_reports_its_own_parser(self) -> None:
        """原实现让文本分支沿用通用 parser 名，无法区分是哪条路径解析的。"""

        analysis = self._analysis("plain ascii 46968\n".encode("utf-8"))
        self.assertEqual(analysis["parser"], "mcp-text-parser.v1")

    def test_locator_offsets_match_decoded_text(self) -> None:
        """locator 必须建立在正确解码的文本上，否则下游按 offset 取到残骸。"""

        original = "总投资 46968 万元"
        analysis = self._analysis(original.encode("gb18030"))
        locators = [row for row in analysis["locators"] if row.get("kind") == "document_text"]
        self.assertTrue(locators, analysis)
        self.assertEqual(locators[0]["text"], original)


if __name__ == "__main__":
    unittest.main()
